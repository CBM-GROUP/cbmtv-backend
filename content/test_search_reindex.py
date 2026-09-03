"""
Search / reindex separation.

`GET /api/content/search/` did not search. It called `index.delete()` on the
whole Meilisearch `content` index and rebuilt it from the database, on an
unauthenticated GET -- so any crawler, link prefetch or accidental navigation
wiped search for every client. The dashboard's "Sync Search Data" button was
its only intentional caller.

The rebuild now lives at `POST /api/content/reindex/` behind IsAdminUser, and
`GET /api/content/search/?q=` is a read-only proxy.

Every test here patches `content.views.get_search_index`. The repo's .env points
MEILISEARCH_URL/MASTER_KEY at the live production instance, so a test that
reached a real client could delete the production index.
"""
from unittest.mock import MagicMock, patch

from django.urls import reverse
from rest_framework import status
from rest_framework.test import APITestCase, APIClient

from accounts.models import User
from channel.models import Channel
from .models import Content


class SearchReindexTestBase(APITestCase):
    def setUp(self):
        self.client = APIClient()
        self.search_url = reverse('search')
        self.reindex_url = reverse('reindex')

        self.channel = Channel.objects.create(name="Search Fixture")
        # Bound to names: search_view now hydrates Meilisearch hits against real
        # rows, so a test must feed the index ids the database actually holds.
        self.dija = Content.objects.create(
            title="Dear Dija", content_type="movie", genre="Short Movie",
            description="Two teenagers bond over romance films.",
            channel=self.channel,
        )
        self.series = Content.objects.create(
            title="A Series", content_type="series", genre="Drama",
            channel=self.channel,
        )

        self.admin = User.objects.create_user(
            email="admin@example.com", name="Admin", password="pass1234",
            role="admin",
        )
        self.admin.is_staff = True
        self.admin.save()

        self.normal_user = User.objects.create_user(
            email="user@example.com", name="User", password="pass1234",
        )

    def make_index(self, hits=None):
        """A stand-in Meilisearch index handle that records what was called."""
        index = MagicMock()
        index.search.return_value = {'hits': hits or [], 'estimatedTotalHits': len(hits or [])}
        return index


class SearchViewTests(SearchReindexTestBase):
    def test_search_returns_hits_without_mutating_the_index(self):
        index = self.make_index(hits=[{'id': self.dija.id, 'title': 'Dear Dija'}])

        with patch('content.views.get_search_index', return_value=index):
            response = self.client.get(self.search_url, {'q': 'dija'})

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data['query'], 'dija')
        self.assertEqual(response.data['count'], 1)
        self.assertEqual(response.data['hits'][0]['title'], 'Dear Dija')

        # The whole point of the split.
        index.search.assert_called_once()
        index.delete.assert_not_called()
        index.add_documents.assert_not_called()
        index.delete_all_documents.assert_not_called()

    def test_search_is_anonymously_readable(self):
        index = self.make_index()
        with patch('content.views.get_search_index', return_value=index):
            response = self.client.get(self.search_url, {'q': 'anything'})
        self.assertEqual(response.status_code, status.HTTP_200_OK)

    def test_search_without_query_returns_empty_and_never_touches_backend(self):
        with patch('content.views.get_search_index') as get_index:
            response = self.client.get(self.search_url)

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data['count'], 0)
        self.assertEqual(response.data['hits'], [])
        get_index.assert_not_called()

    def test_blank_query_is_treated_as_no_query(self):
        with patch('content.views.get_search_index') as get_index:
            response = self.client.get(self.search_url, {'q': '   '})
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data['count'], 0)
        get_index.assert_not_called()

    def test_search_limit_is_passed_through_and_capped(self):
        index = self.make_index()
        with patch('content.views.get_search_index', return_value=index):
            self.client.get(self.search_url, {'q': 'x', 'limit': '5'})
        self.assertEqual(index.search.call_args[0][1]['limit'], 5)

        index = self.make_index()
        with patch('content.views.get_search_index', return_value=index):
            self.client.get(self.search_url, {'q': 'x', 'limit': '100000'})
        self.assertEqual(index.search.call_args[0][1]['limit'], 100)

        index = self.make_index()
        with patch('content.views.get_search_index', return_value=index):
            self.client.get(self.search_url, {'q': 'x', 'limit': 'abc'})
        self.assertEqual(index.search.call_args[0][1]['limit'], 20)

    def test_search_post_is_not_allowed(self):
        """Searching is a read. Writes belong to the reindex route."""
        response = self.client.post(self.search_url, {'q': 'x'}, format='json')
        self.assertEqual(response.status_code, status.HTTP_405_METHOD_NOT_ALLOWED)

    def test_unconfigured_meilisearch_falls_back_to_the_database(self):
        """
        This used to 503. A deployment with no Meilisearch is a normal state --
        it should still be able to find a title, so search degrades to the
        database rather than reporting itself broken.
        """
        from content.views import SearchUnavailable

        with patch('content.views.get_search_index',
                   side_effect=SearchUnavailable('MEILISEARCH_URL is unset.')):
            response = self.client.get(self.search_url, {'q': 'dija'})

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data['source'], 'database')
        self.assertEqual([h['title'] for h in response.data['hits']], ['Dear Dija'])

    def test_unreachable_meilisearch_falls_back_to_the_database(self):
        """Was a 502. Same reasoning: an outage must not take search down."""
        index = self.make_index()
        index.search.side_effect = RuntimeError("connection refused")

        with patch('content.views.get_search_index', return_value=index):
            response = self.client.get(self.search_url, {'q': 'dija'})

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data['source'], 'database')
        self.assertEqual(response.data['count'], 1)


class SearchHydrationTests(SearchReindexTestBase):
    """
    Meilisearch ranks; the database decides what comes back.

    The production index held six documents from a dataset that no longer
    existed. Two of its ids had since been reassigned to different programmes,
    so the one query that did return a hit sent the viewer to the wrong title,
    and two others resolved to nothing at all. Treating index documents as the
    payload is what made that possible.
    """

    def test_hits_are_serialized_from_the_database_not_the_index(self):
        """The index's copy of a title can be stale; the row cannot."""
        index = self.make_index(hits=[{'id': self.dija.id, 'title': 'STALE TITLE'}])

        with patch('content.views.get_search_index', return_value=index):
            response = self.client.get(self.search_url, {'q': 'dija'})

        hit = response.data['hits'][0]
        self.assertEqual(hit['title'], 'Dear Dija')
        self.assertEqual(hit['id'], self.dija.id)
        # Full serializer output, so the client can render a real result row.
        self.assertIn('thumbnail', hit)
        self.assertIn('content_type', hit)
        self.assertEqual(response.data['source'], 'meilisearch')

    def test_ids_missing_from_the_database_are_dropped(self):
        missing = self.series.id + 4242
        index = self.make_index(hits=[
            {'id': missing, 'title': 'Deleted Long Ago'},
            {'id': self.dija.id, 'title': 'Dear Dija'},
        ])

        with patch('content.views.get_search_index', return_value=index):
            response = self.client.get(self.search_url, {'q': 'x'})

        self.assertEqual(response.data['count'], 1)
        self.assertEqual([h['id'] for h in response.data['hits']], [self.dija.id])

    def test_meilisearch_ranking_order_is_preserved(self):
        """
        in_bulk() returns rows in whatever order the database likes, so the view
        has to reimpose the index ranking or relevance ordering is lost.
        """
        index = self.make_index(hits=[{'id': self.series.id}, {'id': self.dija.id}])
        with patch('content.views.get_search_index', return_value=index):
            forward = self.client.get(self.search_url, {'q': 'x'})
        self.assertEqual(
            [h['id'] for h in forward.data['hits']],
            [self.series.id, self.dija.id],
        )

        index = self.make_index(hits=[{'id': self.dija.id}, {'id': self.series.id}])
        with patch('content.views.get_search_index', return_value=index):
            reverse = self.client.get(self.search_url, {'q': 'x'})
        self.assertEqual(
            [h['id'] for h in reverse.data['hits']],
            [self.dija.id, self.series.id],
        )

    def test_a_wholly_stale_index_falls_back_to_the_database(self):
        """
        Every hit dropped during hydration is indistinguishable from no hits at
        all, and is exactly the production failure. Fall through to the database
        rather than reporting "no results" for a title that does exist.
        """
        index = self.make_index(hits=[
            {'id': 90001, 'title': 'The Great Show'},
            {'id': 90002, 'title': 'House of Kings'},
        ])

        with patch('content.views.get_search_index', return_value=index):
            response = self.client.get(self.search_url, {'q': 'dija'})

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data['source'], 'database')
        self.assertEqual([h['title'] for h in response.data['hits']], ['Dear Dija'])

    def test_empty_index_falls_back_to_the_database(self):
        index = self.make_index(hits=[])
        with patch('content.views.get_search_index', return_value=index):
            response = self.client.get(self.search_url, {'q': 'series'})

        self.assertEqual(response.data['source'], 'database')
        self.assertEqual([h['title'] for h in response.data['hits']], ['A Series'])

    def test_hits_with_unusable_ids_are_skipped_not_fatal(self):
        """The index is external state and may hold an older document schema."""
        index = self.make_index(hits=[
            {'title': 'no id at all'},
            {'id': None},
            {'id': 'not-a-number'},
            {'id': str(self.dija.id)},
        ])

        with patch('content.views.get_search_index', return_value=index):
            response = self.client.get(self.search_url, {'q': 'x'})

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual([h['id'] for h in response.data['hits']], [self.dija.id])

    def test_no_match_anywhere_is_an_empty_result_not_an_error(self):
        index = self.make_index(hits=[])
        with patch('content.views.get_search_index', return_value=index):
            response = self.client.get(self.search_url, {'q': 'zzzznotathing'})

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data['count'], 0)
        self.assertEqual(response.data['hits'], [])


class DatabaseFallbackMatchingTests(SearchReindexTestBase):
    """The fallback has to be usable on its own -- it is what answers today."""

    def search(self, term, **params):
        """Run a query with Meilisearch forced offline."""
        from content.views import SearchUnavailable

        with patch('content.views.get_search_index',
                   side_effect=SearchUnavailable('off')):
            return self.client.get(self.search_url, {'q': term, **params})

    def test_matching_is_case_insensitive(self):
        for term in ('dear dija', 'DEAR DIJA', 'DeAr DiJa'):
            with self.subTest(term=term):
                titles = [h['title'] for h in self.search(term).data['hits']]
                self.assertEqual(titles, ['Dear Dija'])

    def test_partial_matches_work(self):
        for term in ('dij', 'ija', 'Dear'):
            with self.subTest(term=term):
                self.assertEqual(self.search(term).data['count'], 1)

    def test_genre_is_searchable(self):
        titles = [h['title'] for h in self.search('drama').data['hits']]
        self.assertEqual(titles, ['A Series'])

    def test_description_is_searchable(self):
        titles = [h['title'] for h in self.search('romance films').data['hits']]
        self.assertEqual(titles, ['Dear Dija'])

    def test_fallback_respects_the_limit(self):
        for n in range(12):
            Content.objects.create(
                title=f'Padding {n}', content_type='movie', channel=self.channel,
            )
        self.assertEqual(self.search('Padding', limit=5).data['count'], 5)


class ReindexViewTests(SearchReindexTestBase):
    def test_anonymous_cannot_reindex(self):
        with patch('content.views.get_search_index') as get_index:
            response = self.client.post(self.reindex_url)

        self.assertIn(
            response.status_code,
            (status.HTTP_401_UNAUTHORIZED, status.HTTP_403_FORBIDDEN),
        )
        get_index.assert_not_called()

    def test_authenticated_non_admin_cannot_reindex(self):
        self.client.force_authenticate(user=self.normal_user)
        with patch('content.views.get_search_index') as get_index:
            response = self.client.post(self.reindex_url)

        self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)
        get_index.assert_not_called()

    def test_anonymous_get_on_reindex_is_not_a_backdoor(self):
        """The old destructive behaviour was reachable by GET. It must not be."""
        with patch('content.views.get_search_index') as get_index:
            response = self.client.get(self.reindex_url)

        self.assertIn(
            response.status_code,
            (status.HTTP_401_UNAUTHORIZED,
             status.HTTP_403_FORBIDDEN,
             status.HTTP_405_METHOD_NOT_ALLOWED),
        )
        get_index.assert_not_called()

    def test_admin_can_reindex(self):
        index = self.make_index()
        self.client.force_authenticate(user=self.admin)

        with patch('content.views.get_search_index', return_value=index):
            response = self.client.post(self.reindex_url)

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data['status'], 'success')
        self.assertEqual(response.data['indexed'], 2)
        # The dashboard alerts response.data.message; keep that key populated.
        self.assertIn('message', response.data)
        self.assertIn('2', response.data['message'])

        index.delete.assert_called_once()
        index.add_documents.assert_called_once()

    def test_reindex_documents_carry_the_real_genre_field(self):
        """
        The old builder read `content.genres.all()` behind a
        `hasattr(content, 'genres')` guard. Content has no such relation, so the
        guard was always False and every document shipped `genres: []`.
        """
        index = self.make_index()
        self.client.force_authenticate(user=self.admin)

        with patch('content.views.get_search_index', return_value=index):
            self.client.post(self.reindex_url)

        documents = index.add_documents.call_args[0][0]
        by_title = {doc['title']: doc for doc in documents}
        self.assertEqual(by_title['Dear Dija']['genre'], 'Short Movie')
        self.assertEqual(by_title['A Series']['genre'], 'Drama')
        self.assertEqual(by_title['A Series']['content_type'], 'series')

    def test_reindex_survives_a_missing_index_on_delete(self):
        index = self.make_index()
        index.delete.side_effect = RuntimeError("index_not_found")
        self.client.force_authenticate(user=self.admin)

        with patch('content.views.get_search_index', return_value=index):
            response = self.client.post(self.reindex_url)

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        index.add_documents.assert_called_once()

    def test_reindex_reports_502_when_add_documents_fails(self):
        index = self.make_index()
        index.add_documents.side_effect = RuntimeError("connection refused")
        self.client.force_authenticate(user=self.admin)

        with patch('content.views.get_search_index', return_value=index):
            response = self.client.post(self.reindex_url)

        self.assertEqual(response.status_code, status.HTTP_502_BAD_GATEWAY)
        self.assertEqual(response.data['status'], 'error')

    def test_reindex_returns_503_when_meilisearch_is_unconfigured(self):
        from content.views import SearchUnavailable

        self.client.force_authenticate(user=self.admin)
        with patch('content.views.get_search_index',
                   side_effect=SearchUnavailable('MEILISEARCH_URL is unset.')):
            response = self.client.post(self.reindex_url)

        self.assertEqual(response.status_code, status.HTTP_503_SERVICE_UNAVAILABLE)


class SearchConfigurationTests(APITestCase):
    """get_search_index must not build a client without configuration."""

    def test_missing_url_raises_search_unavailable(self):
        from django.test import override_settings
        from content.views import SearchUnavailable, get_search_index

        with override_settings(MEILISEARCH_URL=''):
            with self.assertRaises(SearchUnavailable):
                get_search_index()

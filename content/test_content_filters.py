"""
Query-parameter filtering and pagination limits for the content list endpoint.

`?content_type=` was accepted and silently ignored: ContentListCreateView read
only `?channel=`, so `/api/content/?content_type=series` returned the whole
catalogue. Both clients depend on it -- the stream app's
API_Routes.listSeries is literally "/api/content/?content_type=series", and the
mobile home screen needs it for its per-category rails.
"""
from django.urls import reverse
from rest_framework import status
from rest_framework.test import APITestCase, APIClient

from channel.models import Channel
from .models import Content


class ContentFilterTests(APITestCase):
    def setUp(self):
        self.client = APIClient()
        self.url = reverse('content-list-create')

        # Deliberately not the seeded names. channel/signals.py creates five
        # default channels ("CBM Movies", "CBM Music", ...) on post_migrate, so
        # every test database already has them and `create` would collide on
        # Channel.name's unique constraint.
        self.movies_channel = Channel.objects.create(name="Filter Fixture A")
        self.music_channel = Channel.objects.create(name="Filter Fixture B")

        self.movie_a = Content.objects.create(
            title="Dear Dija", content_type="movie", genre="Short Movie",
            channel=self.movies_channel,
        )
        self.movie_b = Content.objects.create(
            title="Watu Wote", content_type="movie", genre="Drama",
            channel=self.music_channel,
        )
        self.series = Content.objects.create(
            title="A Series", content_type="series", genre="Drama",
            channel=self.movies_channel,
        )
        self.animation = Content.objects.create(
            title="Escape From School", content_type="animations", genre="Family",
            channel=self.movies_channel,
        )
        self.documentary = Content.objects.create(
            title="A Documentary", content_type="documentary", genre="Nature",
            channel=self.music_channel,
        )

    def _titles(self, response):
        return sorted(item['title'] for item in response.data['results'])

    # --- content_type ------------------------------------------------------

    def test_filter_by_content_type_movie(self):
        response = self.client.get(self.url, {'content_type': 'movie'})
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(self._titles(response), ["Dear Dija", "Watu Wote"])
        self.assertTrue(
            all(item['content_type'] == 'movie' for item in response.data['results'])
        )

    def test_filter_by_content_type_series(self):
        """The exact query the stream app's listSeries route sends."""
        response = self.client.get(self.url, {'content_type': 'series'})
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data['count'], 1)
        self.assertEqual(self._titles(response), ["A Series"])

    def test_filter_by_each_valid_content_type(self):
        expected = {
            'movie': 2, 'series': 1, 'animations': 1, 'documentary': 1,
            'music': 0, 'miniseries': 0, 'original': 0,
        }
        for content_type, count in expected.items():
            with self.subTest(content_type=content_type):
                response = self.client.get(self.url, {'content_type': content_type})
                self.assertEqual(response.status_code, status.HTTP_200_OK)
                self.assertEqual(response.data['count'], count)

    def test_invalid_content_type_returns_400(self):
        response = self.client.get(self.url, {'content_type': 'not-a-type'})
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn('content_type', response.data)

    def test_content_type_tolerates_trailing_slash(self):
        response = self.client.get(self.url, {'content_type': 'movie/'})
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data['count'], 2)

    # --- genre -------------------------------------------------------------

    def test_filter_by_genre(self):
        response = self.client.get(self.url, {'genre': 'Drama'})
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(self._titles(response), ["A Series", "Watu Wote"])

    def test_genre_match_is_case_insensitive_and_partial(self):
        """Genre is free text like "Short Movie", so match loosely."""
        response = self.client.get(self.url, {'genre': 'short'})
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(self._titles(response), ["Dear Dija"])

    def test_unknown_genre_returns_empty_not_error(self):
        response = self.client.get(self.url, {'genre': 'Nonexistent'})
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data['count'], 0)

    # --- combined ----------------------------------------------------------

    def test_content_type_and_genre_combine(self):
        response = self.client.get(
            self.url, {'content_type': 'movie', 'genre': 'Drama'}
        )
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(self._titles(response), ["Watu Wote"])

    def test_channel_and_content_type_combine(self):
        response = self.client.get(
            self.url,
            {'channel': self.movies_channel.id, 'content_type': 'movie'},
        )
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(self._titles(response), ["Dear Dija"])

    def test_all_three_filters_combine(self):
        response = self.client.get(
            self.url,
            {
                'channel': self.movies_channel.id,
                'content_type': 'series',
                'genre': 'drama',
            },
        )
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(self._titles(response), ["A Series"])

    # --- backwards compatibility -------------------------------------------

    def test_unfiltered_list_still_returns_everything(self):
        response = self.client.get(self.url)
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data['count'], 5)

    def test_channel_filter_still_works(self):
        response = self.client.get(self.url, {'channel': self.music_channel.id})
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(self._titles(response), ["A Documentary", "Watu Wote"])

    def test_channel_filter_tolerates_trailing_slash(self):
        response = self.client.get(
            self.url, {'channel': f'{self.music_channel.id}/'}
        )
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data['count'], 2)

    def test_non_numeric_channel_returns_empty(self):
        response = self.client.get(self.url, {'channel': 'abc'})
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data['count'], 0)

    def test_movies_endpoint_still_works(self):
        response = self.client.get(reverse('movie-list'))
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data['count'], 2)

    def test_detail_endpoint_still_works(self):
        response = self.client.get(
            reverse('content-detail', args=[self.series.id])
        )
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data['title'], "A Series")

    def test_list_is_anonymously_readable(self):
        self.client.force_authenticate(user=None)
        self.assertEqual(self.client.get(self.url).status_code, status.HTTP_200_OK)


class ContentPaginationTests(APITestCase):
    """StandardPagination previously had no max_page_size."""

    @classmethod
    def setUpTestData(cls):
        channel = Channel.objects.create(name="Pagination Fixture")
        Content.objects.bulk_create([
            Content(title=f"Item {i}", content_type="movie", channel=channel)
            for i in range(105)
        ])

    def setUp(self):
        self.client = APIClient()
        self.url = reverse('content-list-create')

    def test_default_page_size_is_ten(self):
        response = self.client.get(self.url)
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(len(response.data['results']), 10)
        self.assertEqual(response.data['count'], 105)
        self.assertIsNotNone(response.data['next'])

    def test_page_size_below_the_cap_is_honoured(self):
        response = self.client.get(self.url, {'page_size': 25})
        self.assertEqual(len(response.data['results']), 25)

    def test_page_size_is_capped_at_max_page_size(self):
        """?page_size=1000000 must not serialize the whole table."""
        response = self.client.get(self.url, {'page_size': 1000000})
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(len(response.data['results']), 100)
        self.assertIsNotNone(response.data['next'])

    def test_pages_do_not_repeat_rows(self):
        """
        Ordering guard. PageNumberPagination slices the queryset, and slicing an
        unordered one lets the database return a different order per page, so
        rows repeat or vanish between pages.
        """
        seen = []
        url = self.url
        params = {'page_size': 50}
        for _ in range(3):
            response = self.client.get(url, params)
            if response.status_code != status.HTTP_200_OK:
                break
            seen.extend(item['id'] for item in response.data['results'])
            if not response.data.get('next'):
                break
            params = {'page_size': 50, 'page': len(seen) // 50 + 1}

        self.assertEqual(len(seen), len(set(seen)), "a row appeared on two pages")
        self.assertEqual(len(seen), 105)

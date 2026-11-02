from unittest.mock import MagicMock, patch

from rest_framework.test import APITestCase

from channel.models import Channel
from content.models import Content, Genre
from content.search_sync import SearchUnavailable, content_document


class FieldSearchTests(APITestCase):
    url = '/api/content/search/'

    def setUp(self):
        self.channel = Channel.objects.create(name='CBM Nature')
        self.other_channel = Channel.objects.create(name='CBM Drama')
        self.genre = Genre.objects.create(name='Wildlife', for_documentaries=True)
        self.title = Content.objects.create(
            title='African Wildlife', description='Directed by Amina',
            director='Nuru Kato', cast=['Daniel Ouma'], genre='Nature',
            category='documentaries', format='standalone', content_type='documentary',
            channel=self.channel, status='approved',
        )
        self.title.genres.add(self.genre)
        self.other = Content.objects.create(
            title='Amina Returns', description='African Wildlife adventure',
            director='Amina Kato', cast=['Wildlife Presenter'],
            category='movies', format='standalone', content_type='movie',
            channel=self.other_channel, status='approved',
        )
        self.draft = Content.objects.create(
            title='Secret Wildlife', category='documentaries', format='standalone',
            content_type='documentary', channel=self.channel, status='preview',
            cast=['Daniel Ouma'],
        )

    def search(self, query, **params):
        with patch('content.views.get_search_index', side_effect=SearchUnavailable('offline')):
            return self.client.get(self.url, {'q': query, **params})

    def ids(self, response):
        self.assertEqual(response.status_code, 200)
        return {hit['id'] for hit in response.data['hits']}

    def test_omitted_mode_is_title_and_cross_field_mentions_do_not_match(self):
        implicit = self.search('African Wildlife')
        explicit = self.search('African Wildlife', search_by='title')
        self.assertEqual(implicit.data, explicit.data)
        self.assertEqual(implicit.data['search_by'], 'title')
        self.assertEqual(self.ids(implicit), {self.title.id})
        self.assertEqual(self.ids(self.search('Amina')), {self.other.id})

    def test_every_field_has_its_own_matches(self):
        cases = [
            ('everything', 'Amina', {self.title.id, self.other.id}),
            ('director', 'Amina', {self.other.id}),
            ('genre', 'Wildlife', {self.title.id}),
            ('cast', 'Daniel', {self.title.id}),
            ('channel', 'CBM Nature', {self.title.id}),
        ]
        for mode, query, wanted in cases:
            with self.subTest(mode=mode):
                self.assertEqual(self.ids(self.search(query, search_by=mode)), wanted)

    def test_filters_pagination_invalid_mode_and_private_titles(self):
        self.assertEqual(self.ids(self.search('Wildlife', search_by='everything', category='movies')), {self.other.id})
        self.assertEqual(self.ids(self.search('Wildlife', search_by='everything', channel=str(self.channel.id))), {self.title.id})
        self.assertEqual(self.ids(self.search('Secret', search_by='title')), set())
        self.assertEqual(self.search('x', search_by='description').status_code, 400)
        page = self.search('Wildlife', search_by='everything', limit=1)
        self.assertEqual(page.data['total'], 2)
        self.assertEqual(page.data['next_offset'], 1)
        next_page = self.search('Wildlife', search_by='everything', limit=1, offset=1)
        self.assertEqual(next_page.data['count'], 1)
        self.assertIsNone(next_page.data['next_offset'])

    def test_index_query_is_scoped_and_stale_cross_field_hits_are_rejected(self):
        index = MagicMock()
        index.search.return_value = {'hits': [{'id': self.other.id}, {'id': self.title.id}]}
        with patch('content.views.get_search_index', return_value=index):
            response = self.client.get(self.url, {'q': 'African Wildlife'})
        self.assertEqual(self.ids(response), {self.title.id})
        self.assertEqual(index.search.call_args.args[1]['attributesToSearchOn'], ['title'])

    def test_index_document_includes_structured_metadata_and_parent_genre(self):
        parent = Genre.objects.create(name='Nature', for_documentaries=True)
        self.genre.parent = parent
        self.genre.save()
        document = content_document(self.title)
        self.assertEqual(document['cast'], ['Daniel Ouma'])
        self.assertEqual(document['director'], 'Nuru Kato')
        self.assertEqual(document['channel_id'], self.channel.id)
        self.assertIn('Nature', document['genres'])

    def test_inactive_managed_genre_is_removed_from_searchable_metadata(self):
        self.genre.is_active = False
        self.genre.save()
        self.assertEqual(self.ids(self.search('Wildlife', search_by='genre')), set())
        self.assertEqual(content_document(self.title)['genres'], [])

    def test_cast_payload_requires_credited_names(self):
        from content.serializers import ContentSerializer
        bad = ContentSerializer(self.title, data={'cast': 'Daniel Ouma'}, partial=True)
        self.assertFalse(bad.is_valid())
        good = ContentSerializer(self.title, data={'cast': ['Daniel Ouma', 'Amina Kato']}, partial=True)
        self.assertTrue(good.is_valid(), good.errors)

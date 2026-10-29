"""Complete public matching and continuation, without any live index access."""
from unittest.mock import MagicMock, patch

from rest_framework.test import APITestCase

from channel.models import Channel
from content.models import Content
from content.search_sync import SearchUnavailable


class ExhaustiveSearchTests(APITestCase):
    url = '/api/content/search/'

    def setUp(self):
        self.channel, _ = Channel.objects.get_or_create(name='CBM Originals')

    def content(self, title, **values):
        return Content.objects.create(title=title, channel=self.channel,
                                      **{'status': 'approved', 'content_type': 'movie', **values})

    def test_partial_index_cannot_hide_the_bigger_dream(self):
        wanted = self.content('The Bigger Dream', content_type='miniseries')
        other = self.content('The Chicken Hunt', content_type='animations')
        index = MagicMock()
        index.search.return_value = {'hits': [{'id': other.pk}]}
        with patch('content.views.get_search_index', return_value=index):
            response = self.client.get(self.url, {'q': 'The Bigger Dream', 'limit': 8})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data['hits'][0]['id'], wanted.pk)
        self.assertEqual(response.data['hits'][0]['content_type'], 'miniseries')
        index.delete.assert_not_called()
        index.add_documents.assert_not_called()

    def test_partial_genre_results_are_supplemented_without_duplicates(self):
        first = self.content('First', genre='Drama')
        second = self.content('Second', genre='Drama')
        index = MagicMock()
        index.search.return_value = {'hits': [{'id': first.pk}, {'id': first.pk}]}
        with patch('content.views.get_search_index', return_value=index):
            response = self.client.get(self.url, {'q': 'Drama'})
        self.assertEqual([item['id'] for item in response.data['hits']], [first.pk, second.pk])
        self.assertEqual(response.data['total'], 2)

    def test_more_than_one_hundred_matches_are_reachable(self):
        Content.objects.bulk_create([Content(title=f'Complete drama {i}', genre='Drama',
                                            channel=self.channel, status='approved', content_type='movie')
                                     for i in range(137)])
        offset, found = 0, []
        with patch('content.views.get_search_index', side_effect=SearchUnavailable('fixture offline')):
            for _ in range(30):
                response = self.client.get(self.url, {'q': 'drama', 'limit': 8, 'offset': offset})
                self.assertEqual(response.status_code, 200)
                self.assertEqual(response.data['total'], 137)
                self.assertLessEqual(response.data['count'], 8)
                found.extend(row['id'] for row in response.data['hits'])
                next_offset = response.data['next_offset']
                if next_offset is None:
                    break
                self.assertGreater(next_offset, offset)
                offset = next_offset
        self.assertEqual(len(found), 137)
        self.assertEqual(len(set(found)), 137)

    def test_offset_can_cross_from_index_results_into_database_supplement(self):
        rows = [self.content(f'Drama {i}', genre='Drama') for i in range(5)]
        index = MagicMock()
        index.search.return_value = {'hits': [{'id': rows[1].pk}, {'id': rows[0].pk}]}
        with patch('content.views.get_search_index', return_value=index):
            response = self.client.get(self.url, {'q': 'Drama', 'offset': 1, 'limit': 3})
        self.assertEqual([row['id'] for row in response.data['hits']], [rows[0].pk, rows[4].pk, rows[3].pk])
        self.assertEqual(response.data['next_offset'], 4)

    def test_index_only_matches_beyond_one_hundred_are_paginated(self):
        Content.objects.bulk_create([Content(title=f'Fuzzy result {i}', channel=self.channel,
                                            status='approved', content_type='movie') for i in range(137)])
        ids = list(Content.objects.order_by('id').values_list('id', flat=True))
        index = MagicMock()
        index.search.side_effect = lambda _query, options: {'hits': [{'id': pk} for pk in ids[:options['limit']]]}
        with patch('content.views.get_search_index', return_value=index):
            response = self.client.get(self.url, {'q': 'typoonlytoken', 'limit': 8, 'offset': 128})
            capped_page = self.client.get(self.url, {'q': 'typoonlytoken', 'limit': 100000})
        self.assertEqual(response.data['total'], 137)
        self.assertEqual([row['id'] for row in response.data['hits']], ids[128:136])
        self.assertEqual(response.data['next_offset'], 136)
        self.assertEqual(capped_page.data['count'], 100)
        self.assertEqual(capped_page.data['next_offset'], 100)

    def test_unpublished_and_deleted_ids_never_consume_a_public_result_slot(self):
        private = self.content('Secret drama', status='preview')
        public = self.content('Visible drama')
        index = MagicMock()
        index.search.return_value = {'hits': [{'id': private.pk}, {'id': 9999999}, {'id': public.pk}]}
        with patch('content.views.get_search_index', return_value=index):
            response = self.client.get(self.url, {'q': 'drama', 'limit': 1})
        self.assertEqual([row['id'] for row in response.data['hits']], [public.pk])
        self.assertEqual(response.data['total'], 1)
        self.assertIsNone(response.data['next_offset'])

    def test_public_metadata_and_words_across_fields_are_searchable(self):
        row = self.content('A new journey', genre='Adventure', description='A story in Kampala',
                           director='Amina Example', writer='Writer Example', country='Uganda')
        with patch('content.views.get_search_index', side_effect=SearchUnavailable('fixture offline')):
            for term in ['CBM Originals', 'Kampala', 'Amina', 'Uganda', 'Writer Example', 'journey Uganda', 'Originals Adventure']:
                with self.subTest(term=term):
                    response = self.client.get(self.url, {'q': term})
                    self.assertEqual([item['id'] for item in response.data['hits']], [row.pk])

    def test_category_words_do_not_change_documentary_or_animation_classification(self):
        documentary = self.content('True story', content_type='documentary')
        animation = self.content('Animated story', content_type='animations')
        with patch('content.views.get_search_index', side_effect=SearchUnavailable('fixture offline')):
            for term, row in [('Documentaries', documentary), ('Animation', animation)]:
                response = self.client.get(self.url, {'q': term})
                self.assertEqual(response.data['hits'][0]['content_type'], row.content_type)

    def test_invalid_offsets_are_rejected_and_large_offsets_end_cleanly(self):
        with patch('content.views.get_search_index', side_effect=SearchUnavailable('fixture offline')):
            for value in ['-1', 'not-a-number', '1.5']:
                self.assertEqual(self.client.get(self.url, {'q': 'drama', 'offset': value}).status_code, 400)
            response = self.client.get(self.url, {'q': 'drama', 'offset': '999999999999999999999'})
        self.assertEqual(response.data['hits'], [])
        self.assertIsNone(response.data['next_offset'])

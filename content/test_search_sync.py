from unittest.mock import MagicMock, patch
from types import SimpleNamespace

from django.test import TestCase
from rest_framework.test import APIClient

from accounts.models import User
from channel.models import Channel
from .models import Content
from .search_sync import content_document, upsert_content


class ContentSearchSyncTests(TestCase):
    def setUp(self):
        self.client = APIClient()
        user = User.objects.create_user(
            email='search-admin@example.com', name='Search Admin', password='pass1234', role='admin'
        )
        user.is_staff = True
        user.save()
        self.client.force_authenticate(user=user)
        self.channel = Channel.objects.create(name='Search Sync Channel')

    def test_create_update_delete_schedule_incremental_index_work(self):
        with patch('content.views.upsert_content') as upsert, patch('content.views.remove_content') as remove:
            with self.captureOnCommitCallbacks(execute=True):
                created = self.client.post('/api/content/', {
                    'title': 'First title', 'content_type': 'movie', 'channel': self.channel.id,
                }, format='json')
            self.assertEqual(created.status_code, 201)
            content_id = created.data['id']
            upsert.assert_called_once_with(content_id)

            with self.captureOnCommitCallbacks(execute=True):
                updated = self.client.patch(f'/api/content/{content_id}/', {
                    'title': 'Renamed title',
                }, format='json')
            self.assertEqual(updated.status_code, 200)
            self.assertEqual(Content.objects.get(pk=content_id).title, 'Renamed title')
            self.assertEqual(upsert.call_count, 2)

            with self.captureOnCommitCallbacks(execute=True):
                deleted = self.client.delete(f'/api/content/{content_id}/')
            self.assertEqual(deleted.status_code, 204)
            remove.assert_called_once_with(content_id)

    def test_index_document_uses_current_content_fields(self):
        content = Content.objects.create(
            title='Renamed title', description='New description', genre='Drama',
            content_type='movie', channel=self.channel,
        )
        index = MagicMock()
        index.add_documents.return_value.task_uid = 42
        index.wait_for_task.return_value.status = 'succeeded'
        with patch('content.search_sync.get_search_index', return_value=index):
            upsert_content(content.id)
        index.add_documents.assert_called_once_with([content_document(content)])
        index.wait_for_task.assert_called_once_with(42, timeout_in_ms=3000)

    def test_index_failure_does_not_undo_database_save(self):
        content = Content.objects.create(title='Saved title', content_type='movie', channel=self.channel)
        with patch('content.search_sync.get_search_index', side_effect=RuntimeError('offline')):
            with self.assertLogs('content.search_sync', level='ERROR') as logs:
                upsert_content(content.id)
        self.assertTrue(Content.objects.filter(pk=content.id).exists())
        self.assertIn('Search indexing failed', logs.output[0])

    def test_committed_crud_is_visible_through_the_mobile_search_endpoint(self):
        documents = {}
        index = MagicMock()

        def add_documents(rows):
            for row in rows:
                documents[row['id']] = row
            return SimpleNamespace(task_uid=1)

        def delete_document(content_id):
            documents.pop(content_id, None)
            return SimpleNamespace(task_uid=2)

        def search(query, options):
            return {'hits': [row for row in documents.values()
                             if query.lower() in row['title'].lower()][:options['limit']]}

        index.add_documents.side_effect = add_documents
        index.delete_document.side_effect = delete_document
        index.search.side_effect = search
        index.wait_for_task.return_value.status = 'succeeded'

        with patch('content.search_sync.get_search_index', return_value=index), \
             patch('content.views.get_search_index', return_value=index):
            with self.captureOnCommitCallbacks(execute=True):
                created = self.client.post('/api/content/', {
                    'title': 'Mobile search fixture', 'content_type': 'movie',
                    'channel': self.channel.id,
                }, format='json')
            self.assertEqual(created.status_code, 201)
            content_id = created.data['id']
            first = self.client.get('/api/content/search/', {'q': 'Mobile search fixture'})
            self.assertEqual(first.data['source'], 'meilisearch')
            self.assertEqual(first.data['hits'][0]['id'], content_id)

            with self.captureOnCommitCallbacks(execute=True):
                updated = self.client.patch(f'/api/content/{content_id}/', {
                    'title': 'Renamed mobile fixture',
                }, format='json')
            self.assertEqual(updated.status_code, 200)
            renamed = self.client.get('/api/content/search/', {'q': 'Renamed mobile fixture'})
            self.assertEqual(renamed.data['source'], 'meilisearch')
            self.assertEqual(renamed.data['hits'][0]['id'], content_id)

            with self.captureOnCommitCallbacks(execute=True):
                deleted = self.client.delete(f'/api/content/{content_id}/')
            self.assertEqual(deleted.status_code, 204)
            missing = self.client.get('/api/content/search/', {'q': 'Renamed mobile fixture'})
            self.assertEqual(missing.data['hits'], [])
            self.assertNotIn(content_id, documents)

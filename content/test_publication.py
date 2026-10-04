"""Approval is the boundary between dashboard records and public content."""

from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from django.test import TestCase
from rest_framework.test import APIClient

from accounts.models import User
from channel.models import Channel
from library.models import SavedItem, WatchProgress

from .models import Content, Episode, MiniSeries, Season
from .views import build_search_documents


class PublicationTests(TestCase):
    def setUp(self):
        self.client = APIClient()
        self.channel = Channel.objects.create(name='Publication Fixture')
        self.admin = User.objects.create_user(
            email='publication-admin@example.com', name='Admin', password='pass1234', role='admin'
        )
        self.admin.is_staff = True
        self.admin.save()
        self.viewer = User.objects.create_user(
            email='publication-viewer@example.com', name='Viewer', password='pass1234'
        )
        self.approved = Content.objects.create(
            title='Public movie', content_type='movie', channel=self.channel,
            status='approved', is_featured=True, is_trending=True,
        )
        self.preview = Content.objects.create(
            title='Private series', content_type='series', channel=self.channel,
            status='preview', is_featured=True, is_trending=True,
        )
        self.preview_mini = Content.objects.create(
            title='Private miniseries', content_type='miniseries', channel=self.channel,
            status='preview',
        )
        self.rejected = Content.objects.create(
            title='Rejected movie', content_type='movie', channel=self.channel, status='rejected'
        )
        self.comingsoon = Content.objects.create(
            title='Reserved promotion', content_type='movie', channel=self.channel, status='comingsoon'
        )
        self.blank = Content.objects.create(
            title='Unset status', content_type='movie', channel=self.channel
        )
        self.season = Season.objects.create(content=self.preview, title='S1', season_number=1)
        self.episode = Episode.objects.create(season=self.season, title='E1', episode_number=1)
        self.part = MiniSeries.objects.create(content=self.preview_mini, title='Part', miniseries_no=1)

    def test_public_catalogue_details_flags_channel_and_ids_are_approved_only(self):
        for path in ('/api/content/', '/api/content/?featured=true',
                     '/api/content/?trending=true', '/api/content/movies/',
                     f'/api/channels/{self.channel.id}/contents/'):
            response = self.client.get(path)
            self.assertEqual(response.status_code, 200, path)
            self.assertEqual([row['id'] for row in response.data['results']], [self.approved.id], path)
        self.assertEqual(self.client.get(f'/api/content/{self.preview.id}/').status_code, 404)
        self.assertEqual(self.client.get(f'/api/content/{self.approved.id}/').status_code, 200)
        self.assertEqual(self.client.get('/api/content/ids/').data['total']['ids'], [self.approved.id])

    def test_signed_in_viewer_cannot_request_dashboard_scope(self):
        self.client.force_authenticate(user=self.viewer)
        for path in ('/api/content/?scope=dashboard',
                     f'/api/content/{self.preview.id}/?scope=dashboard',
                     f'/api/channels/{self.channel.id}/contents/?scope=dashboard',
                     '/api/content/seasons/?scope=dashboard'):
            self.assertEqual(self.client.get(path).status_code, 403, path)
        self.assertEqual(self.client.get(f'/api/content/{self.preview.id}/').status_code, 404)

    def test_staff_scope_reads_all_statuses_and_unapproved_children(self):
        self.client.force_authenticate(user=self.admin)
        self.assertEqual(self.client.get('/api/content/').data['count'], 1)
        rows = self.client.get('/api/content/?scope=dashboard')
        self.assertEqual(rows.data['count'], 6)
        self.assertEqual(self.client.get(f'/api/content/{self.preview.id}/?scope=dashboard').status_code, 200)
        self.assertEqual(self.client.get('/api/content/ids/?scope=dashboard').data['total']['count'], 6)
        self.assertEqual(self.client.get(
            f'/api/channels/{self.channel.id}/contents/?scope=dashboard'
        ).data['count'], 6)
        self.assertEqual(self.client.get(f'/api/content/{self.preview.id}/').status_code, 404)

        for plural, child in (('seasons', self.season), ('episodes', self.episode),
                              ('miniseries', self.part)):
            self.assertEqual(self.client.get(f'/api/content/{plural}/').data['count'] if plural != 'miniseries'
                             else len(self.client.get(f'/api/content/{plural}/').data), 0)
            self.assertEqual(self.client.get(f'/api/content/{plural}/{child.id}/').status_code, 404)
            self.assertEqual(self.client.get(f'/api/content/{plural}/{child.id}/?scope=dashboard').status_code, 200)
        self.assertEqual(self.client.get('/api/content/seasons/?scope=dashboard').data['count'], 1)
        self.assertEqual(self.client.get('/api/content/episodes/?scope=dashboard').data['count'], 1)
        self.assertEqual(len(self.client.get('/api/content/miniseries/?scope=dashboard').data), 1)

    def test_library_hides_old_unapproved_items_and_rejects_new_writes(self):
        SavedItem.objects.create(user=self.viewer, content=self.approved)
        SavedItem.objects.create(user=self.viewer, content=self.preview)
        WatchProgress.objects.create(user=self.viewer, content=self.preview, position_seconds=5)
        self.client.force_authenticate(user=self.viewer)
        self.assertEqual(self.client.get('/api/library/saved/').data['count'], 1)
        self.assertEqual(self.client.get('/api/library/saved/ids/').data, [self.approved.id])
        self.assertEqual(self.client.get('/api/library/progress/').data['count'], 0)
        self.assertEqual(self.client.post('/api/library/saved/', {'content': self.preview.id}).status_code, 400)
        self.assertEqual(self.client.post('/api/library/progress/', {
            'content': self.preview.id, 'position_seconds': 5,
        }).status_code, 400)

    def test_stale_search_hit_and_fallback_never_expose_unapproved(self):
        index = MagicMock()
        index.search.return_value = {'hits': [{'id': self.preview.id}]}
        with patch('content.views.get_search_index', return_value=index):
            response = self.client.get('/api/content/search/?q=Private')
        self.assertEqual(response.data['hits'], [])
        self.assertEqual(response.data['source'], 'database')
        self.assertEqual([doc['id'] for doc in build_search_documents()], [self.approved.id])

    def test_status_transitions_update_public_search_index(self):
        documents = {}
        index = MagicMock()

        def add_documents(rows):
            for row in rows:
                documents[row['id']] = row
            return SimpleNamespace(task_uid=1)

        def delete_document(content_id):
            documents.pop(content_id, None)
            return SimpleNamespace(task_uid=2)

        index.add_documents.side_effect = add_documents
        index.delete_document.side_effect = delete_document
        index.wait_for_task.return_value.status = 'succeeded'
        index.search.side_effect = lambda query, options: {
            'hits': [row for row in documents.values() if query.lower() in row['title'].lower()]
        }

        self.client.force_authenticate(user=self.admin)
        with patch('content.search_sync.get_search_index', return_value=index), \
             patch('content.views.get_search_index', return_value=index):
            with self.captureOnCommitCallbacks(execute=True):
                created = self.client.post('/api/content/', {
                    'title': 'Lifecycle title', 'content_type': 'movie',
                    'channel': self.channel.id, 'status': 'preview',
                })
            self.assertEqual(created.status_code, 201)
            content_id = created.data['id']
            self.assertNotIn(content_id, documents)
            self.assertEqual(self.client.get(f'/api/content/{content_id}/').status_code, 404)
            self.assertEqual(self.client.get('/api/content/?scope=dashboard').data['count'], 7)

            for new_status, indexed in (('approved', True), ('preview', False),
                                        ('approved', True), ('rejected', False),
                                        ('approved', True), ('comingsoon', False)):
                with self.captureOnCommitCallbacks(execute=True):
                    response = self.client.patch(f'/api/content/{content_id}/', {
                        'status': new_status,
                    })
                self.assertEqual(response.status_code, 200)
                self.assertEqual(content_id in documents, indexed)
                self.assertEqual(self.client.get(f'/api/content/{content_id}/').status_code,
                                 200 if indexed else 404)
                hits = self.client.get('/api/content/search/?q=Lifecycle')
                self.assertEqual(bool(hits.data['hits']), indexed)

            with self.captureOnCommitCallbacks(execute=True):
                self.client.patch(f'/api/content/{content_id}/', {'status': 'approved'})
            with self.captureOnCommitCallbacks(execute=True):
                self.assertEqual(self.client.delete(f'/api/content/{content_id}/').status_code, 204)
            self.assertNotIn(content_id, documents)

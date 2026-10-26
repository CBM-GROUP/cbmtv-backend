from unittest.mock import patch
from datetime import timedelta

from django.test import override_settings
from rest_framework.test import APITestCase

from accounts.models import DashboardRole, User
from channel.models import Channel
from .models import Content, Season


@override_settings(PASSWORD_HASHERS=['django.contrib.auth.hashers.MD5PasswordHasher'])
class ContentLifecycleTests(APITestCase):
    def setUp(self):
        role = DashboardRole.objects.create(name='Editor', permissions=['content'])
        self.editor = User.objects.create_user(email='editor@example.com', name='Editor', password='local-only')
        self.editor.dashboard_role = role
        self.editor.save()
        self.viewer = User.objects.create_user(email='viewer@example.com', name='Viewer', password='local-only')
        self.channel = Channel.objects.create(name='Lifecycle fixture')
        self.row = Content.objects.create(title='Existing', channel=self.channel, content_type='movie',
                                          status='approved', size='12345', duration=timedelta(seconds=90))
        self.client.force_authenticate(self.editor)

    def test_all_creation_statuses_are_forced_to_preview_with_media_metadata_preserved(self):
        for status in (None, 'approved', 'preview', 'comingsoon', 'rejected'):
            payload = {'title': 'New', 'channel': self.channel.pk, 'content_type': 'movie',
                       'streaming_link': 'https://cdn.example/movie.mp4', 'trailer_link': 'https://cdn.example/trailer.mp4',
                       'thumbnail': 'https://cdn.example/poster.png', 'size': '123456789', 'duration': '90.25'}
            if status is not None:
                payload['status'] = status
            result = self.client.post('/api/content/', payload, format='json')
            self.assertEqual(result.status_code, 201)
            self.assertEqual(result.data['status'], 'preview')
            self.assertEqual(result.data['size'], '123456789')
            self.assertEqual(result.data['duration'], '00:01:30.250000')
            self.assertEqual(result.data['streaming_link'], payload['streaming_link'])
            self.assertEqual(result.data['trailer_link'], payload['trailer_link'])

    def test_metadata_and_flags_preserve_status_and_do_not_publish_preview(self):
        result = self.client.patch(f'/api/content/{self.row.pk}/', {'title': 'Updated'}, format='json')
        self.assertEqual(result.data['status'], 'approved')
        self.assertEqual(result.data['size'], '12345')
        for state in ('preview', 'rejected', 'comingsoon'):
            self.row.status = state
            self.row.save()
            result = self.client.patch(f'/api/content/{self.row.pk}/', {'is_featured': True, 'is_trending': True}, format='json')
            self.assertEqual(result.data['status'], state)
            self.assertTrue(result.data['is_featured'])
            self.client.force_authenticate(None)
            for path in ('/api/content/?featured=true', '/api/content/?trending=true',
                         f'/api/channels/{self.channel.pk}/contents/', '/api/content/search/?q=Updated'):
                data = self.client.get(path).data
                self.assertFalse(data.get('results', data.get('hits', [])))
            self.client.force_authenticate(self.editor)

    @patch('content.views.upsert_content')
    def test_editor_unpublish_approve_actions_logged_after_commit(self, upsert):
        for target in ('preview', 'approved'):
            with self.assertLogs('content.publication', level='INFO') as logs:
                with self.captureOnCommitCallbacks(execute=True):
                    result = self.client.patch(f'/api/content/{self.row.pk}/', {'status': target}, format='json')
            self.assertEqual(result.status_code, 200)
            self.assertEqual(result.data['status'], target)
            self.assertIn(f'actor_id={self.editor.pk}', logs.output[0])
            self.assertIn(f'to={target}', logs.output[0])
            self.assertEqual(self.client.get(f'/api/content/{self.row.pk}/?scope=dashboard').status_code, 200)
            self.client.force_authenticate(None)
            self.assertEqual(self.client.get(f'/api/content/{self.row.pk}/').status_code, 200 if target == 'approved' else 404)
            self.client.force_authenticate(self.editor)

    def test_viewer_and_anonymous_cannot_change_publication_or_flags(self):
        for user in (self.viewer, None):
            self.client.force_authenticate(user)
            result = self.client.patch(f'/api/content/{self.row.pk}/', {'status': 'preview', 'is_featured': True}, format='json')
            self.assertIn(result.status_code, (401, 403))
        self.row.refresh_from_db()
        self.assertEqual(self.row.status, 'approved')
        self.assertFalse(self.row.is_featured)

    def test_public_size_key_retained_as_null_and_dashboard_reads_bytes(self):
        self.assertEqual(self.client.get(f'/api/content/{self.row.pk}/?scope=dashboard').data['size'], '12345')
        self.client.force_authenticate(None)
        self.assertIsNone(self.client.get(f'/api/content/{self.row.pk}/').data['size'])
        self.assertIsNone(self.client.get(f'/api/channels/{self.channel.pk}/contents/').data['results'][0]['size'])

    def test_episode_and_miniseries_duration_accepts_seconds_and_unknown(self):
        for kind, parent_field, number_field in [('series', 'season', 'episode_number'), ('miniseries', 'content', 'miniseries_no')]:
            parent = Content.objects.create(title=kind, channel=self.channel, content_type=kind, status='preview')
            parent_id = Season.objects.create(content=parent, title='S1', season_number=1).pk if kind == 'series' else parent.pk
            endpoint = '/api/content/episodes/' if kind == 'series' else '/api/content/miniseries/'
            for duration in ('90.25', None):
                result = self.client.post(endpoint, {parent_field: parent_id, number_field: 1,
                    'title': 'Part', 'duration': duration, 'streaming_link': 'https://cdn.example/part.mp4'}, format='json')
                self.assertEqual(result.status_code, 201)
                self.assertEqual(result.data['duration'], '00:01:30.250000' if duration else None)

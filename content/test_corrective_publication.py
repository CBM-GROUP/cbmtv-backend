from unittest.mock import patch

from django.test import TestCase
from rest_framework.test import APIClient

from accounts.models import DashboardRole, User
from channel.models import Channel
from .models import Content
from .serializers import ContentSerializer


class CorrectivePublicationTests(TestCase):
    def setUp(self):
        role = DashboardRole.objects.create(name='Concurrency editor', permissions=['content'])
        self.user = User.objects.create_user(email='race@example.com', name='Editor', password='test')
        self.user.dashboard_role = role
        self.user.save()
        channel = Channel.objects.create(name='Race fixture')
        self.row = Content.objects.create(title='Original', channel=channel, content_type='movie', status='approved')
        self.client = APIClient()
        self.client.force_authenticate(self.user)

    def test_exact_stale_instance_metadata_race_cannot_restore_approved(self):
        unpublish = ContentSerializer(Content.objects.get(pk=self.row.pk), data={'status': 'preview'}, partial=True)
        metadata = ContentSerializer(Content.objects.get(pk=self.row.pk), data={'title': 'Edited'}, partial=True)
        unpublish.is_valid(raise_exception=True)
        metadata.is_valid(raise_exception=True)
        unpublish.save()
        metadata.save()
        self.row.refresh_from_db()
        self.assertEqual(self.row.status, 'preview')
        self.assertEqual(self.row.title, 'Edited')

    @patch('content.views.upsert_content')
    def test_stale_explicit_transition_conflicts_and_logs_only_after_commit(self, upsert):
        endpoint = f'/api/content/{self.row.pk}/'
        with self.assertLogs('content.publication', level='INFO') as logs:
            with self.captureOnCommitCallbacks(execute=True) as callbacks:
                response = self.client.patch(endpoint, {'status': 'preview'}, format='json', HTTP_IF_PUBLICATION_STATUS='approved')
                self.assertEqual(response.status_code, 200)
                self.assertEqual(logs.output, [])
            self.assertTrue(callbacks)
        self.assertIn('from=approved to=preview', logs.output[0])
        conflict = self.client.patch(endpoint, {'status': 'approved'}, format='json', HTTP_IF_PUBLICATION_STATUS='approved')
        self.assertEqual(conflict.status_code, 409)
        self.row.refresh_from_db()
        self.assertEqual(self.row.status, 'preview')

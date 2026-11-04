from datetime import timedelta
from io import StringIO
from unittest.mock import patch
from botocore.exceptions import ClientError
from django.core.management import call_command
from django.core.management.base import CommandError
from django.test import TestCase, override_settings
from django.utils import timezone
from accounts.models import User
from content.models import MediaAsset
from content.test_media_uploads import TEST_STORAGE


@override_settings(MEDIA_STORAGE=TEST_STORAGE)
class CleanupMediaTests(TestCase):
    def setUp(self):
        owner = User.objects.create_superuser('cleanup@example.com', 'Cleanup', password='test')
        self.asset = MediaAsset.objects.create(owner=owner, source_key='cbmvideo/private/fixture.mp4',
            reference_url='https://cdn.example/fixture.mp4', content_type='video/mp4', size_bytes=12, upload_id='fixture-upload')
        MediaAsset.objects.filter(pk=self.asset.pk).update(created_at=timezone.now() - timedelta(hours=50))
        self.patch = patch('content.management.commands.cleanup_media_uploads._client')
        self.storage = self.patch.start().return_value
        self.addCleanup(self.patch.stop)

    def test_dry_run_and_minimum_age_preserve_active_uploads(self):
        call_command('cleanup_media_uploads', stdout=StringIO())
        self.storage.abort_multipart_upload.assert_not_called()
        with self.assertRaises(CommandError):
            call_command('cleanup_media_uploads', older_than_hours=24, apply=True, stdout=StringIO())
        MediaAsset.objects.filter(pk=self.asset.pk).update(created_at=timezone.now())
        call_command('cleanup_media_uploads', apply=True, stdout=StringIO())
        self.storage.abort_multipart_upload.assert_not_called()

    def test_abandoned_parts_are_aborted_and_marked_canceled(self):
        call_command('cleanup_media_uploads', apply=True, stdout=StringIO())
        self.asset.refresh_from_db()
        self.assertEqual(self.asset.state, 'canceled')
        self.storage.abort_multipart_upload.assert_called_once()

    def test_lost_completion_is_recovered_without_deleting_source(self):
        self.storage.abort_multipart_upload.side_effect = ClientError({'Error': {'Code': 'NoSuchUpload'}}, 'AbortMultipartUpload')
        self.storage.head_object.return_value = {'ContentLength': 12, 'ContentType': 'video/mp4'}
        call_command('cleanup_media_uploads', apply=True, stdout=StringIO())
        self.asset.refresh_from_db()
        self.assertEqual(self.asset.state, 'uploaded')
        self.storage.delete_object.assert_not_called()

    def test_transient_storage_failure_retains_retryable_session(self):
        self.storage.abort_multipart_upload.side_effect = RuntimeError('sensitive-storage-url')
        with self.assertRaises(CommandError) as raised:
            call_command('cleanup_media_uploads', apply=True, stdout=StringIO())
        self.assertNotIn('sensitive-storage-url', str(raised.exception))
        self.asset.refresh_from_db()
        self.assertEqual(self.asset.state, 'uploading')

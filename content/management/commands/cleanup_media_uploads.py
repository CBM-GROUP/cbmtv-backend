"""Explicit cleanup for durable abandoned sessions; S3 lifecycle remains a backstop."""
from datetime import timedelta
from botocore.exceptions import ClientError
from django.conf import settings
from django.core.management.base import BaseCommand, CommandError
from django.utils import timezone
from common.multipart_upload import _client
from content.models import MediaAsset


class Command(BaseCommand):
    help = 'Abort abandoned multipart sessions older than their 24-hour authorization. Dry run unless --apply.'

    def add_arguments(self, parser):
        parser.add_argument('--older-than-hours', type=int, default=48)
        parser.add_argument('--apply', action='store_true')

    def handle(self, *args, **options):
        if options['older_than_hours'] < 25:
            raise CommandError('Keep at least 25 hours for session and in-flight part authorization expiry.')
        assets = MediaAsset.objects.filter(state='uploading', created_at__lt=timezone.now() - timedelta(hours=options['older_than_hours']))
        if not options['apply']:
            self.stdout.write(f'{assets.count()} abandoned upload sessions eligible; use --apply to abort them.')
            return
        client = _client(settings.MEDIA_STORAGE)
        failed = 0
        for asset in assets.iterator():
            try:
                try:
                    client.abort_multipart_upload(Bucket=settings.MEDIA_STORAGE['S3_BUCKET_NAME'], Key=asset.source_key, UploadId=asset.upload_id)
                except ClientError as exc:
                    if exc.response.get('Error', {}).get('Code') != 'NoSuchUpload':
                        raise
                    # Preserve completed sources whose HTTP/DB completion was
                    # interrupted, rather than categorizing them as canceled.
                    try:
                        head = client.head_object(Bucket=settings.MEDIA_STORAGE['S3_BUCKET_NAME'], Key=asset.source_key)
                    except ClientError as missing:
                        if missing.response.get('Error', {}).get('Code') not in ('NoSuchKey', '404', 'NotFound'):
                            raise
                    else:
                        if head['ContentLength'] == asset.size_bytes and head['ContentType'] == asset.content_type:
                            MediaAsset.objects.filter(pk=asset.pk, state='uploading').update(state='uploaded')
                            continue
                MediaAsset.objects.filter(pk=asset.pk, state='uploading').update(state='canceled')
            except Exception:
                failed += 1
        if failed:
            raise CommandError(f'{failed} sessions could not be cleaned up; retry later. No storage credentials were logged.')
        self.stdout.write('Abandoned multipart sessions cleaned up.')

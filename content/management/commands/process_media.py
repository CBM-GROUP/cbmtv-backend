"""Run the existing database queue in an explicitly started worker process."""
import time
import uuid
from datetime import timedelta

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError
from django.db import close_old_connections, connection, transaction
from django.db.models import Q
from django.utils import timezone
from django.utils.module_loading import import_string

from common.transcoding import TranscodingError
from content.models import MediaAsset


def claim_media():
    now = timezone.now()
    maximum = max(1, int(getattr(settings, 'MEDIA_TRANSCODING_MAX_ATTEMPTS', 3)))
    expired = Q(state='processing') & (Q(lease_until__lte=now) | Q(lease_until__isnull=True))
    due = Q(next_attempt_at__isnull=True) | Q(next_attempt_at__lte=now)
    with transaction.atomic():
        MediaAsset.objects.filter(expired, purpose='protected', attempts__gte=maximum).update(
            state='failed', error='Transcoding worker lease expired after the final attempt.',
            lease_id=None, lease_until=None, updated_at=now,
        )
        queryset = MediaAsset.objects.filter(
            (Q(state='queued') & due) | expired, purpose='protected', attempts__lt=maximum,
        ).order_by('created_at', 'pk')
        queryset = queryset.select_for_update(skip_locked=True) if connection.features.has_select_for_update_skip_locked else queryset.select_for_update()
        asset = queryset.first()
        if not asset:
            return None
        asset.state = 'processing'
        asset.attempts += 1
        asset.error = ''
        asset.lease_id = uuid.uuid4()
        timeout = max(1, int(getattr(settings, 'MEDIA_PROCESSING_TIMEOUT', 14400)))
        asset.lease_until = now + timedelta(seconds=timeout + 600)
        asset.next_attempt_at = None
        asset.save(update_fields=['state', 'attempts', 'error', 'lease_id', 'lease_until', 'next_attempt_at', 'updated_at'])
        return asset


def process_one(provider):
    asset = claim_media()
    if asset is None:
        return False
    fenced = MediaAsset.objects.filter(pk=asset.pk, state='processing', lease_id=asset.lease_id)
    try:
        prefix = f'protected/hls/{asset.pk}/{asset.lease_id}/'
        master_key, renditions = provider.transcode(asset, prefix)
        if master_key != prefix + 'master.m3u8' or not isinstance(renditions, list) or not renditions:
            raise TranscodingError('Transcoding provider returned incomplete output.')
        fenced.filter(lease_until__gt=timezone.now()).update(
            state='ready', master_key=master_key, renditions=renditions, error='',
            lease_id=None, lease_until=None, next_attempt_at=None, updated_at=timezone.now(),
        )
    except Exception as exc:
        # Provider/tool/storage exceptions can contain credentials or filenames.
        # Persist only fixed categories controlled by this implementation.
        safe_errors = {
            'Transcoding timed out.', 'Unsupported video dimensions.', 'Invalid output prefix.',
            'Media could not be decoded or encoded.', 'Source size does not match the completed upload.',
            'Source download was incomplete.', 'Source could not be downloaded.',
            'Source does not contain a supported video stream.', 'Encoded media could not be uploaded.',
            'Transcoding provider returned incomplete output.',
        }
        message = str(exc) if isinstance(exc, TranscodingError) and str(exc) in safe_errors else 'Transcoding failed. Retry or contact an administrator.'
        maximum = max(1, int(getattr(settings, 'MEDIA_TRANSCODING_MAX_ATTEMPTS', 3)))
        retry = asset.attempts < maximum
        fenced.update(
            state='queued' if retry else 'failed', error=message,
            lease_id=None, lease_until=None,
            next_attempt_at=timezone.now() + timedelta(seconds=min(3600, 60 * 2 ** (asset.attempts - 1))) if retry else None,
            updated_at=timezone.now(),
        )
    return True


class Command(BaseCommand):
    help = 'Process protected media asynchronously using the configured provider (no AWS services are provisioned).'

    def add_arguments(self, parser):
        parser.add_argument('--once', action='store_true', help='Process at most one available job and exit.')
        parser.add_argument('--poll-seconds', type=float, default=5)

    def handle(self, *args, **options):
        if options['poll_seconds'] < 0.1:
            raise CommandError('--poll-seconds must be at least 0.1.')
        try:
            provider = import_string(getattr(settings, 'MEDIA_TRANSCODING_PROVIDER', 'common.transcoding.FFmpegProvider'))()
        except (ImportError, AttributeError, TypeError) as exc:
            raise CommandError('Configured transcoding provider could not be loaded.') from exc
        try:
            while True:
                close_old_connections()
                worked = process_one(provider)
                if options['once']:
                    self.stdout.write('Processed one media job.' if worked else 'No media jobs available.')
                    return
                if not worked:
                    time.sleep(options['poll_seconds'])
        except KeyboardInterrupt:
            self.stdout.write('Media worker stopped; active leases can be recovered by another worker.')

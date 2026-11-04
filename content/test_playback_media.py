"""Security and lifecycle checks for protected media; AWS integrations are mocked."""
import base64
import json
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import patch

from botocore.exceptions import ClientError
from django.test import override_settings
from rest_framework.test import APITestCase

from accounts.models import DashboardRole, User
from channel.models import Channel
from common.media_storage import MediaStorageError
from common.multipart_upload import (
    PART_SIZE, abort_video_upload, finish_video_upload, sign_part, start_video_upload, upload_status,
)
from content.models import Content, Episode, MediaAsset, MiniSeries, Season, contentadverts
from content.playback import _signer
from content.test_media_uploads import TEST_STORAGE
from library.models import SavedItem, WatchProgress


DELIVERY = {
    'ENABLED': True, 'KEY_PAIR_ID': 'test-key', 'PRIVATE_KEY_FILE': 'mocked',
    'COOKIE_DOMAIN': 'example.com', 'API_ORIGIN': 'https://api.example.com',
    'TTL': 900, 'LEGACY_PREFIXES': ['/cbmvideo/'],
}


def decode_cookie(value):
    return base64.b64decode(value.replace('-', '+').replace('_', '=').replace('~', '/'))


@override_settings(MEDIA_STORAGE=TEST_STORAGE, PLAYBACK_DELIVERY=DELIVERY)
class PlaybackAuthorizationTests(APITestCase):
    def setUp(self):
        self.owner = User.objects.create_superuser('media-owner@example.com', 'Owner', password='test')
        self.viewer = User.objects.create_user('media-viewer@example.com', 'Viewer', password='test')
        self.other = User.objects.create_superuser('media-other@example.com', 'Other', password='test')
        self.channel = Channel.objects.create(name='Protected playback fixture')
        self.movie = Content.objects.create(
            channel=self.channel, title='Protected movie', content_type='movie', category='movies',
            format='standalone', status='approved', streaming_link='https://cdn.example.com/cbmvideo/movie.mp4',
            trailer_link='https://cdn.example.com/cbmvideo/trailer.mp4',
        )
        self.series = Content.objects.create(
            channel=self.channel, title='Protected series', content_type='series',
            category='tv_shows', format='series', status='approved',
        )
        self.season = Season.objects.create(content=self.series, title='Season', season_number=1)
        self.episode = Episode.objects.create(
            season=self.season, title='Episode', episode_number=1,
            streaming_link='https://cdn.example.com/cbmvideo/episode.mp4',
        )
        self.mini = Content.objects.create(channel=self.channel, title='Parts', content_type='miniseries', status='approved')
        self.part = MiniSeries.objects.create(content=self.mini, title='Part', miniseries_no=1,
            streaming_link='https://cdn.example.com/cbmvideo/part.mp4')
        self.sign = patch('content.playback._signer', return_value=lambda message: b'test-signature')
        self.sign.start()
        self.addCleanup(self.sign.stop)

    def playback(self, content=None, **selection):
        return self.client.post(f'/api/content/{(content or self.movie).pk}/playback/', selection, format='json')

    def asset(self, state='ready', **extra):
        asset = MediaAsset.objects.create(
            owner=self.owner, source_key='cbmvideo/private/source.mp4', reference_url=self.movie.streaming_link,
            content_type='video/mp4', size_bytes=1024, state=state, **extra,
        )
        if state == 'ready':
            asset.master_key = f'protected/hls/{asset.pk}/attempt/master.m3u8'
            asset.save(update_fields=['master_key'])
        return asset

    def test_anonymous_cannot_obtain_playback_grant(self):
        response = self.playback()
        self.assertEqual(response.status_code, 401)
        self.assertEqual(response['Cache-Control'], 'private, no-store')
        self.assertFalse(response.cookies)

    @override_settings(PLAYBACK_LEGACY_PUBLIC_ORIGINS=['https://cdn.example.com'])
    def test_existing_public_video_keeps_mobile_link_and_website_playback(self):
        catalogue = self.client.get(f'/api/content/{self.movie.pk}/')
        self.assertEqual(catalogue.data['streaming_link'], self.movie.streaming_link)
        self.assertTrue(catalogue.data['playback_available'])
        self.assertEqual(self.playback().status_code, 401)
        self.client.force_authenticate(self.viewer)
        with override_settings(PLAYBACK_DELIVERY={**DELIVERY, 'ENABLED': False}):
            response = self.playback()
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data['url'], self.movie.streaming_link)
        self.assertEqual(response.data['type'], 'progressive')
        self.assertFalse(response.data['with_credentials'])
        self.assertEqual(response['Cache-Control'], 'private, no-store')
        self.assertFalse(response.cookies)

    @override_settings(PLAYBACK_LEGACY_PUBLIC_ORIGINS=['https://cdn.example.com'])
    def test_protected_upload_never_uses_public_legacy_compatibility(self):
        self.asset('uploaded')
        self.assertIsNone(self.client.get(f'/api/content/{self.movie.pk}/').data['streaming_link'])
        self.client.force_authenticate(self.viewer)
        response = self.playback()
        self.assertEqual(response.status_code, 409)
        self.assertNotIn('url', response.data)

    @override_settings(PLAYBACK_LEGACY_PUBLIC_ORIGINS=['https://cdn.example.com'])
    def test_public_legacy_compatibility_rejects_private_and_other_origins(self):
        self.client.force_authenticate(self.viewer)
        for reference in ('https://cdn.example.com/cbmvideo/private/source.mp4',
                          'https://cdn.example.com/cbmvideo/protected/source.mp4',
                          'https://evil.example.com/cbmvideo/movie.mp4',
                          'https://cdn.example.com/cbmvideo/movie.mp4?token=x',
                          'https://cdn.example.com:444/cbmvideo/movie.mp4'):
            self.movie.streaming_link = reference
            self.movie.save(update_fields=['streaming_link'])
            self.assertIsNone(self.client.get(f'/api/content/{self.movie.pk}/').data['streaming_link'])
            self.assertEqual(self.playback().status_code, 409, reference)

    def test_progressive_fallback_is_signed_and_expires(self):
        self.client.force_authenticate(self.viewer)
        response = self.playback()
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data['type'], 'progressive')
        self.assertIn('Signature=', response.data['url'])
        self.assertIn('Key-Pair-Id=test-key', response.data['url'])
        remaining = (datetime.fromisoformat(response.data['expires_at']) - datetime.now(timezone.utc)).total_seconds()
        self.assertTrue(895 <= remaining <= 900)
        self.assertFalse(response.cookies)
        self.assertEqual(response['Cache-Control'], 'private, no-store')

    def test_hls_cookie_policy_covers_children_and_only_this_attempt(self):
        asset = self.asset()
        self.client.force_authenticate(self.viewer)
        response = self.playback()
        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.data['with_credentials'])
        self.assertEqual(response.data['type'], 'hls')
        self.assertNotIn('Signature=', response.data['url'])
        policy = json.loads(decode_cookie(response.cookies['CloudFront-Policy'].value))
        expected = f'https://cdn.example.com/protected/hls/{asset.pk}/attempt/*'
        self.assertEqual(policy['Statement'][0]['Resource'], expected)
        expiry = policy['Statement'][0]['Condition']['DateLessThan']['AWS:EpochTime']
        self.assertEqual(expiry, int(datetime.fromisoformat(response.data['expires_at']).timestamp()))
        self.assertEqual(set(response.cookies), {'CloudFront-Policy', 'CloudFront-Signature', 'CloudFront-Key-Pair-Id'})
        for cookie in response.cookies.values():
            self.assertTrue(cookie['secure'])
            self.assertTrue(cookie['httponly'])
            self.assertEqual(cookie['samesite'], 'None')
            self.assertEqual(cookie['domain'], 'example.com')
            self.assertEqual(cookie['path'], f'/protected/hls/{asset.pk}/attempt/')

    def test_processing_or_failed_source_never_becomes_legacy_fallback(self):
        asset = self.asset('uploaded')
        self.client.force_authenticate(self.viewer)
        for state in ('uploading', 'uploaded', 'queued', 'processing', 'failed', 'canceled'):
            asset.state = state
            asset.save(update_fields=['state'])
            response = self.playback()
            self.assertEqual(response.status_code, 409, state)
            self.assertEqual(response.data['state'], state)
            self.assertNotIn('url', response.data)
            self.assertFalse(response.cookies)

    def test_invalid_ready_output_cannot_grant_broad_cookie_policy(self):
        asset = self.asset()
        self.client.force_authenticate(self.viewer)
        for key in ('', 'master.m3u8', 'protected/hls/another/attempt/master.m3u8',
                    f'protected/hls/{asset.pk}/../master.m3u8'):
            asset.master_key = key
            asset.save(update_fields=['master_key'])
            response = self.playback()
            self.assertIn(response.status_code, (409, 503), key)
            self.assertFalse(response.cookies)
            self.assertNotIn('url', response.data)

    def test_inactive_channel_and_unpublished_content_deny_even_administrators(self):
        self.client.force_authenticate(self.owner)
        for state in ('preview', 'rejected', 'comingsoon'):
            self.movie.status = state
            self.movie.save(update_fields=['status'])
            self.assertEqual(self.playback().status_code, 404)
        self.movie.status = 'approved'
        self.movie.save(update_fields=['status'])
        self.channel.is_active = False
        self.channel.save(update_fields=['is_active'])
        self.assertEqual(self.playback().status_code, 404)

    def test_legacy_hls_untrusted_hosts_paths_and_queries_are_rejected(self):
        self.client.force_authenticate(self.viewer)
        for reference in (
            'https://cdn.example.com/cbmvideo/master.m3u8',
            'https://cdn.example.com.evil.test/cbmvideo/movie.mp4',
            'https://evil.test/cbmvideo/movie.mp4', 'http://cdn.example.com/cbmvideo/movie.mp4',
            'https://cdn.example.com/other/movie.mp4',
            'https://cdn.example.com/cbmvideo/movie.mp4?token=untrusted',
        ):
            self.movie.streaming_link = reference
            self.movie.save(update_fields=['streaming_link'])
            response = self.playback()
            self.assertEqual(response.status_code, 409, reference)
            self.assertNotIn('url', response.data)

    def test_signing_not_configured_fails_closed(self):
        self.client.force_authenticate(self.viewer)
        self.sign.stop()
        with override_settings(PLAYBACK_DELIVERY={**DELIVERY, 'ENABLED': False}):
            self.assertEqual(self.playback().status_code, 503)

    def test_hls_requires_cookie_domain_shared_by_api_and_cdn(self):
        self.asset()
        self.client.force_authenticate(self.viewer)
        for config in ({'COOKIE_DOMAIN': ''}, {'API_ORIGIN': 'https://api.unrelated.test'}, {'COOKIE_DOMAIN': 'evil.test'}):
            with override_settings(PLAYBACK_DELIVERY={**DELIVERY, **config}):
                response = self.playback()
                self.assertEqual(response.status_code, 503)
                self.assertFalse(response.cookies)

    def test_expiry_is_bounded_by_server(self):
        self.client.force_authenticate(self.viewer)
        for configured, expected in ((1, 60), (999999, 3600)):
            with override_settings(PLAYBACK_DELIVERY={**DELIVERY, 'TTL': configured}):
                response = self.playback()
                delta = (datetime.fromisoformat(response.data['expires_at']) - datetime.now(timezone.utc)).total_seconds()
                self.assertTrue(expected - 5 <= delta <= expected)

    def test_episode_and_part_must_belong_to_requested_title(self):
        self.client.force_authenticate(self.viewer)
        self.assertEqual(self.playback(self.series).status_code, 400)
        self.assertEqual(self.playback(self.movie, episode=self.episode.pk).status_code, 404)
        self.assertEqual(self.playback(self.series, episode=self.episode.pk).status_code, 200)
        self.assertEqual(self.playback(self.mini, miniseries=self.part.pk).status_code, 200)
        self.assertEqual(self.playback(self.series, episode=self.episode.pk, miniseries=self.part.pk).status_code, 400)
        for value in (True, 0, -1, 'not-an-id', '\u00b2', '9' * 100):
            self.assertEqual(self.playback(self.series, episode=value).status_code, 400)

    def test_public_catalogue_and_nested_library_redact_main_sources_but_keep_trailers(self):
        response = self.client.get(f'/api/content/{self.movie.pk}/')
        self.assertEqual(response.status_code, 200)
        self.assertIsNone(response.data['streaming_link'])
        self.assertTrue(response.data['playback_available'])
        self.assertEqual(response.data['trailer_link'], self.movie.trailer_link)
        for path in ('/api/content/episodes/', '/api/content/miniseries/'):
            rows = self.client.get(path).data
            if isinstance(rows, dict):
                rows = rows['results']
            self.assertTrue(rows)
            self.assertTrue(all(row['streaming_link'] is None for row in rows))
        SavedItem.objects.create(user=self.viewer, content=self.movie)
        WatchProgress.objects.create(user=self.viewer, content=self.movie, position_seconds=30, duration_seconds=100)
        self.client.force_authenticate(self.viewer)
        for path in ('/api/library/saved/', '/api/library/progress/'):
            rows = self.client.get(path).data['results']
            self.assertEqual(len(rows), 1)
            self.assertIsNone(rows[0]['content']['streaming_link'])
        self.client.force_authenticate(self.owner)
        self.assertIsNone(self.client.get(f'/api/content/{self.movie.pk}/').data['streaming_link'])
        self.assertEqual(self.client.get(f'/api/content/{self.movie.pk}/?scope=dashboard').data['streaming_link'], self.movie.streaming_link)

    def test_saved_source_queues_after_save_and_rejects_other_upload_owners(self):
        asset = self.asset('uploaded')
        self.client.force_authenticate(self.owner)
        response = self.client.patch(f'/api/content/{self.movie.pk}/', {'streaming_link': asset.reference_url}, format='json')
        self.assertEqual(response.status_code, 200)
        asset.refresh_from_db()
        self.assertEqual(asset.state, 'queued')
        different = Content.objects.create(channel=self.channel, title='Other movie', content_type='movie', status='preview')
        self.client.force_authenticate(self.other)
        self.assertEqual(self.client.patch(f'/api/content/{different.pk}/', {'streaming_link': asset.reference_url}, format='json').status_code, 400)

    def test_incomplete_or_public_upload_cannot_be_attached_as_protected(self):
        asset = self.asset('uploading')
        self.client.force_authenticate(self.owner)
        for state, purpose in (('uploading', 'protected'), ('canceled', 'protected'), ('uploaded', 'public')):
            asset.state, asset.purpose = state, purpose
            asset.save(update_fields=['state', 'purpose'])
            self.assertEqual(self.client.patch(f'/api/content/{self.movie.pk}/', {'streaming_link': asset.reference_url}, format='json').status_code, 400)

    def test_media_status_retry_owner_and_permissions(self):
        asset = self.asset('failed')
        retry = f'/api/content/media/{asset.pk}/retry/'
        self.client.force_authenticate(self.viewer)
        self.assertEqual(self.client.get('/api/content/media/status/', {'reference_url': asset.reference_url}).status_code, 403)
        self.assertEqual(self.client.post(retry).status_code, 403)
        self.client.force_authenticate(self.other)
        self.assertEqual(self.client.post(retry).status_code, 404)
        self.client.force_authenticate(self.owner)
        status = self.client.get('/api/content/media/status/', {'reference_url': asset.reference_url})
        self.assertEqual(status.data['state'], 'failed')
        self.assertFalse(status.data['ready'])
        self.assertEqual(self.client.post(retry).status_code, 202)
        self.assertEqual(self.client.post(retry).status_code, 409)
        asset.refresh_from_db()
        self.assertEqual(asset.state, 'queued')

    def test_protected_sources_cannot_be_saved_in_public_trailer_or_advert_fields(self):
        asset = self.asset('uploaded')
        advert = contentadverts.objects.create(advert_type='hello', advert_name='Public advert')
        self.client.force_authenticate(self.owner)
        for path, field in (
            (f'/api/content/{self.movie.pk}/', 'trailer_link'),
            (f'/api/content/seasons/{self.season.pk}/', 'trailer_link'),
            (f'/api/content/adverts/{advert.pk}/', 'stream_link'),
            (f'/api/content/adverts/{advert.pk}/', 'advert_link'),
        ):
            with self.subTest(path=path, field=field):
                response = self.client.patch(path, {field: asset.reference_url}, format='json')
                self.assertEqual(response.status_code, 400)
                self.assertIn(field, response.data)

    def test_legacy_protected_references_are_redacted_from_public_trailers_and_adverts(self):
        asset = self.asset('uploaded')
        # Existing records may predate the serializer guard or have been written
        # outside REST. Public reads must still hide a known protected source.
        Content.objects.filter(pk=self.movie.pk).update(trailer_link=asset.reference_url)
        Season.objects.filter(pk=self.season.pk).update(trailer_link=asset.reference_url)
        advert = contentadverts.objects.create(advert_type='hello', advert_name='Old advert',
            stream_link=asset.reference_url, advert_link=asset.reference_url)
        self.assertIsNone(self.client.get(f'/api/content/{self.movie.pk}/').data['trailer_link'])
        self.assertIsNone(self.client.get(f'/api/content/seasons/{self.season.pk}/').data['trailer_link'])
        result = self.client.get(f'/api/content/adverts/{advert.pk}/')
        self.assertIsNone(result.data['stream_link'])
        self.assertIsNone(result.data['advert_link'])

    def test_public_upload_requires_completion_before_trailer_attachment(self):
        asset = self.asset('uploading', purpose='public')
        self.client.force_authenticate(self.owner)
        path = f'/api/content/{self.movie.pk}/'
        self.assertEqual(self.client.patch(path, {'trailer_link': asset.reference_url}, format='json').status_code, 400)
        asset.state = 'uploaded'
        asset.save(update_fields=['state'])
        self.assertEqual(self.client.patch(path, {'trailer_link': asset.reference_url}, format='json').status_code, 200)
        self.client.force_authenticate(None)
        self.assertEqual(self.client.get(path).data['trailer_link'], asset.reference_url)

    @patch('content.media_views._client')
    def test_staff_source_preview_is_object_specific_and_requires_completed_source(self, factory):
        asset = self.asset('uploading')
        path, payload = '/api/content/media/preview/', {'reference_url': asset.reference_url}
        self.assertEqual(self.client.post(path, payload).status_code, 401)
        self.client.force_authenticate(self.viewer)
        self.assertEqual(self.client.post(path, payload).status_code, 403)
        role = DashboardRole.objects.create(name='Channels only preview fixture', permissions=['channels'])
        self.viewer.dashboard_role = role
        self.viewer.save(update_fields=['dashboard_role'])
        self.assertEqual(self.client.post(path, payload).status_code, 403)
        self.client.force_authenticate(self.owner)
        self.assertEqual(self.client.post(path, payload).status_code, 409)
        factory.assert_not_called()
        asset.state = 'queued'
        asset.save(update_fields=['state'])
        factory.return_value.generate_presigned_url.return_value = 'https://s3.example.com/short-object-grant'
        response = self.client.post(path, payload)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data['type'], 'source')
        self.assertEqual(response.data['expires_in'], 300)
        self.assertEqual(response['Cache-Control'], 'private, no-store')
        factory.return_value.generate_presigned_url.assert_called_once_with('get_object',
            Params={'Bucket': TEST_STORAGE['S3_BUCKET_NAME'], 'Key': asset.source_key}, ExpiresIn=300)

    @patch('content.media_views._client')
    def test_other_editors_can_preview_attached_sources_but_not_unattached_private_uploads(self, factory):
        asset = self.asset('uploaded')
        path, payload = '/api/content/media/preview/', {'reference_url': asset.reference_url}
        Content.objects.filter(pk=self.movie.pk).update(streaming_link=None)
        self.client.force_authenticate(self.other)
        self.assertEqual(self.client.post(path, payload).status_code, 403)
        factory.assert_not_called()
        Content.objects.filter(pk=self.movie.pk).update(streaming_link=asset.reference_url)
        factory.return_value.generate_presigned_url.return_value = 'https://s3.example.com/short-object-grant'
        self.assertEqual(self.client.post(path, payload).status_code, 200)


@override_settings(MEDIA_STORAGE=TEST_STORAGE)
class DurableMultipartTests(APITestCase):
    def setUp(self):
        self.owner = User.objects.create_superuser('upload-owner@example.com', 'Owner', password='test')
        self.other = User.objects.create_user('upload-other@example.com', 'Other', password='test')
        self.patch = patch('common.multipart_upload._client')
        self.client_factory = self.patch.start()
        self.addCleanup(self.patch.stop)
        self.s3 = self.client_factory.return_value
        self.s3.create_multipart_upload.return_value = {'UploadId': 's3-upload'}
        self.s3.generate_presigned_url.return_value = 'https://s3.example.com/part'

    def started(self, size=PART_SIZE + 1):
        self.size = size
        self.session = start_video_upload('movie.mp4', 'video/mp4', size, self.owner.pk)
        self.asset = MediaAsset.objects.get(pk=self.session['media_id'])
        self.s3.list_parts.return_value = {'Parts': [
            {'PartNumber': n, 'ETag': f'part-{n}', 'Size': min(self.asset.part_size, size - (n - 1) * self.asset.part_size)}
            for n in range(1, self.session['part_count'] + 1)
        ]}
        self.s3.head_object.return_value = {'ContentLength': size, 'ContentType': 'video/mp4'}
        return self.session['token']

    def test_owner_bound_resume_and_part_signatures(self):
        token = self.started()
        status = upload_status(token, self.owner.pk)
        self.assertEqual([part['Size'] for part in status['parts']], [PART_SIZE, 1])
        self.assertEqual(sign_part(token, self.owner.pk, 2), 'https://s3.example.com/part')
        self.assertEqual(self.s3.generate_presigned_url.call_args.kwargs['ExpiresIn'], 900)
        for operation in (lambda: upload_status(token, self.other.pk), lambda: sign_part(token, self.other.pk, 1),
                          lambda: finish_video_upload(token, self.other.pk), lambda: abort_video_upload(token, self.other.pk)):
            with self.assertRaises(MediaStorageError):
                operation()

    def test_complete_is_idempotent_and_late_cancel_cannot_remove_source(self):
        token = self.started()
        self.assertEqual(finish_video_upload(token, self.owner.pk), self.session['delivery_url'])
        self.assertEqual(finish_video_upload(token, self.owner.pk), self.session['delivery_url'])
        self.s3.complete_multipart_upload.assert_called_once()
        self.asset.refresh_from_db()
        self.assertEqual(self.asset.state, 'uploaded')
        self.assertTrue(upload_status(token, self.owner.pk)['completed'])
        abort_video_upload(token, self.owner.pk)
        self.s3.abort_multipart_upload.assert_not_called()
        with self.assertRaises(MediaStorageError):
            sign_part(token, self.owner.pk, 1)

    def test_missing_or_wrong_sized_parts_never_complete(self):
        token = self.started()
        correct = self.s3.list_parts.return_value['Parts']
        for invalid in (correct[:1], [{**correct[0], 'Size': PART_SIZE - 1}, correct[1]], [correct[0], {**correct[1], 'Size': 2}]):
            self.s3.list_parts.return_value = {'Parts': invalid}
            with self.assertRaises(MediaStorageError):
                finish_video_upload(token, self.owner.pk)
        self.s3.complete_multipart_upload.assert_not_called()
        self.asset.refresh_from_db()
        self.assertEqual(self.asset.state, 'uploading')

    def test_lost_s3_completion_response_recovers_using_verified_head(self):
        token = self.started()
        self.s3.list_parts.side_effect = ClientError({'Error': {'Code': 'NoSuchUpload'}}, 'ListParts')
        self.assertEqual(finish_video_upload(token, self.owner.pk), self.session['delivery_url'])
        self.s3.head_object.assert_called_once()
        self.asset.refresh_from_db()
        self.assertEqual(self.asset.state, 'uploaded')
        self.assertTrue(upload_status(token, self.owner.pk)['completed'])

    def test_transient_status_outage_returns_retryable_503_and_preserves_session(self):
        token = self.started()
        original_parts = self.s3.list_parts.return_value
        self.client.force_authenticate(self.owner)
        self.s3.list_parts.side_effect = ClientError({'Error': {'Code': 'ServiceUnavailable'}}, 'ListParts')
        path = '/api/content/media/multipart/status/'
        response = self.client.post(path, {'token': token}, format='json')
        self.assertEqual(response.status_code, 503)
        self.asset.refresh_from_db()
        self.assertEqual(self.asset.state, 'uploading')
        self.assertEqual(MediaAsset.objects.count(), 1)
        self.s3.list_parts.side_effect = None
        self.s3.list_parts.return_value = original_parts
        response = self.client.post(path, {'token': token}, format='json')
        self.assertEqual(response.status_code, 200)
        self.assertEqual([part['PartNumber'] for part in response.data['parts']], [1, 2])
        self.s3.create_multipart_upload.assert_called_once()

    def test_status_recovers_lost_complete_without_creating_or_completing_again(self):
        token = self.started()
        self.s3.list_parts.side_effect = ClientError({'Error': {'Code': 'NoSuchUpload'}}, 'ListParts')
        self.client.force_authenticate(self.owner)
        response = self.client.post('/api/content/media/multipart/status/', {'token': token}, format='json')
        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.data['completed'])
        self.assertEqual(response.data['delivery_url'], self.session['delivery_url'])
        self.s3.head_object.assert_called_once()
        self.s3.complete_multipart_upload.assert_not_called()
        self.s3.create_multipart_upload.assert_called_once()
        self.asset.refresh_from_db()
        self.assertEqual(self.asset.state, 'uploaded')

    def test_lost_complete_head_outage_is_retryable_but_absent_source_requires_new_upload(self):
        token = self.started()
        self.s3.list_parts.side_effect = ClientError({'Error': {'Code': 'NoSuchUpload'}}, 'ListParts')
        self.client.force_authenticate(self.owner)
        path = '/api/content/media/multipart/status/'
        for code, expected in (('ServiceUnavailable', 503), ('NoSuchKey', 400)):
            self.s3.head_object.side_effect = ClientError({'Error': {'Code': code}}, 'HeadObject')
            self.assertEqual(self.client.post(path, {'token': token}, format='json').status_code, expected)
            self.asset.refresh_from_db()
            self.assertEqual(self.asset.state, 'uploading')

    def test_head_mismatch_does_not_advance_state(self):
        token = self.started()
        self.s3.list_parts.side_effect = ClientError({'Error': {'Code': 'NoSuchUpload'}}, 'ListParts')
        for head in ({'ContentLength': 1, 'ContentType': 'video/mp4'}, {'ContentLength': self.size, 'ContentType': 'text/plain'}):
            self.s3.head_object.return_value = head
            with self.assertRaises(MediaStorageError):
                finish_video_upload(token, self.owner.pk)
        self.asset.refresh_from_db()
        self.assertEqual(self.asset.state, 'uploading')

    def test_cancellation_cleans_s3_and_prevents_future_upload_operations(self):
        token = self.started()
        abort_video_upload(token, self.owner.pk)
        self.s3.abort_multipart_upload.assert_called_once()
        self.asset.refresh_from_db()
        self.assertEqual(self.asset.state, 'canceled')
        with self.assertRaises(MediaStorageError):
            finish_video_upload(token, self.owner.pk)

    def test_bad_file_types_sizes_and_viewers_are_rejected_before_s3(self):
        for filename, mime, size in (('movie.exe', 'video/mp4', 10), ('movie.mp4', 'text/plain', 10),
                                    ('movie.mp4', 'video/mp4', 0), ('movie.mp4', 'video/mp4', True),
                                    ('movie.mp4', 'video/mp4', 1.5), ('movie.mp4', 'video/mp4', 101 * 1024**3)):
            with self.assertRaises(MediaStorageError):
                start_video_upload(filename, mime, size, self.owner.pk)
        self.s3.create_multipart_upload.assert_not_called()
        path = '/api/content/media/multipart/start/'
        self.assertEqual(self.client.post(path, {}).status_code, 401)
        self.client.force_authenticate(self.other)
        self.assertEqual(self.client.post(path, {}).status_code, 403)

    def test_invalid_mime_containers_and_overflowing_numeric_size_return_400(self):
        self.client.force_authenticate(self.owner)
        path = '/api/content/media/multipart/start/'
        for mime in ([], {}, None):
            response = self.client.post(path, {'filename': 'movie.mp4', 'content_type': mime, 'size_bytes': 100}, format='json')
            self.assertEqual(response.status_code, 400)
        # JSON exponent notation can overflow Python's float even without the
        # non-standard Infinity literal, which DRF's parser already rejects.
        response = self.client.generic('POST', path,
            '{"filename":"movie.mp4","content_type":"video/mp4","size_bytes":1e309}', content_type='application/json')
        self.assertEqual(response.status_code, 400)
        self.s3.create_multipart_upload.assert_not_called()

    def test_configurable_part_size_parallelism_and_s3_part_count_bounds(self):
        with override_settings(MEDIA_STORAGE={**TEST_STORAGE, 'MULTIPART_PART_SIZE': 1, 'MULTIPART_PARALLELISM': 99}):
            session = start_video_upload('movie.mp4', 'video/mp4', 50 * 1024**3, self.owner.pk)
        self.assertGreaterEqual(session['part_size'], 5 * 1024**2)
        self.assertLessEqual(session['part_count'], 10000)
        self.assertLessEqual(session['parallelism'], 8)


class RealCloudFrontSignatureTests(APITestCase):
    def test_signer_uses_server_private_key_and_verifiable_signature(self):
        from cryptography.hazmat.primitives import hashes, serialization
        from cryptography.hazmat.primitives.asymmetric import padding, rsa
        key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / 'test-private.pem'
            path.write_bytes(key.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8, serialization.NoEncryption()))
            with override_settings(PLAYBACK_DELIVERY={**DELIVERY, 'PRIVATE_KEY_FILE': str(path)}):
                signature = _signer()(b'policy to verify')
            key.public_key().verify(signature, b'policy to verify', padding.PKCS1v15(), hashes.SHA1())

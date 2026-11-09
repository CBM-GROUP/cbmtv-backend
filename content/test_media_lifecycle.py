"""Exercise the upload-to-ready path with real FFmpeg and emulated S3."""
import io
import shutil
import subprocess
import tempfile
from pathlib import Path
from types import SimpleNamespace
from unittest import skipUnless
from unittest.mock import Mock, patch

from django.test import SimpleTestCase, override_settings
from rest_framework.test import APITestCase

from accounts.models import User
from channel.models import Channel
from content.management.commands.process_media import process_one
from content.models import Content, MediaAsset
from content.test_media_uploads import TEST_STORAGE


@override_settings(MEDIA_STORAGE=TEST_STORAGE, MEDIA_PROCESSING_TIMEOUT=120)
@skipUnless(shutil.which('ffmpeg') and shutil.which('ffprobe'), 'FFmpeg tools are required')
class MediaLifecycleTests(APITestCase):
    def test_multipart_attachment_real_hls_and_ready_grant_boundary(self):
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory) / 'source.mp4'
            subprocess.run([
                'ffmpeg', '-nostdin', '-hide_banner', '-loglevel', 'error', '-y',
                '-f', 'lavfi', '-i', 'testsrc2=size=640x480:rate=15',
                '-f', 'lavfi', '-i', 'sine=frequency=440:sample_rate=48000',
                '-t', '1.2', '-c:v', 'libx264', '-pix_fmt', 'yuv420p',
                '-c:a', 'aac', str(source),
            ], check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            original = source.read_bytes()
            owner = User.objects.create_superuser('phase1-owner@example.com', 'Owner', password='test')
            viewer = User.objects.create_user('phase1-viewer@example.com', 'Viewer', password='test')
            channel = Channel.objects.create(name='Phase 1 media')
            movie = Content.objects.create(channel=channel, title='Phase 1 movie', content_type='movie',
                                           category='movies', format='standalone', status='approved')
            s3 = Mock()
            s3.create_multipart_upload.return_value = {'UploadId': 'isolated-upload'}
            s3.generate_presigned_url.return_value = 'https://s3.example.invalid/part'
            s3.list_parts.return_value = {'Parts': [{'PartNumber': 1, 'ETag': 'fixture-etag',
                                                     'Size': len(original)}], 'IsTruncated': False}
            s3.head_object.return_value = {'ContentLength': len(original), 'ContentType': 'video/mp4'}
            s3.get_object.return_value = {'ContentLength': len(original), 'Body': io.BytesIO(original)}
            uploaded = {}
            order = []

            def store(path, bucket, key, ExtraArgs):
                uploaded[key] = (Path(path).read_bytes(), ExtraArgs['ContentType'])
                order.append(key)

            s3.upload_file.side_effect = store
            with patch('common.multipart_upload._client', return_value=s3), \
                 patch('common.transcoding._client', return_value=s3):
                self.client.force_authenticate(owner)
                base = '/api/content/media/multipart/'
                started = self.client.post(base + 'start/', {'filename': 'phase1.mp4',
                    'content_type': 'video/mp4', 'size_bytes': len(original)}, format='json')
                self.assertEqual(started.status_code, 200)
                self.assertEqual(started.data['part_count'], 1)
                token = started.data['token']
                asset = MediaAsset.objects.get(pk=started.data['media_id'])
                self.assertEqual(asset.state, 'uploading')
                self.assertTrue(asset.source_key.startswith('cbmvideo/private/'))
                self.assertEqual(self.client.post(base + 'part/', {'token': token,
                    'part_number': 1}, format='json').status_code, 200)
                self.assertEqual(self.client.post(base + 'status/', {'token': token},
                    format='json').data['parts'][0]['Size'], len(original))
                completed = self.client.post(base + 'complete/', {'token': token}, format='json')
                self.assertEqual(completed.status_code, 200)
                self.assertEqual(completed.data['delivery_url'], asset.reference_url)
                asset.refresh_from_db()
                self.assertEqual(asset.state, 'uploaded')
                self.assertEqual(self.client.patch(f'/api/content/{movie.pk}/',
                    {'streaming_link': asset.reference_url}, format='json').status_code, 200)
                asset.refresh_from_db()
                self.assertEqual(asset.state, 'queued')
                self.client.force_authenticate(viewer)
                self.assertEqual(self.client.post(f'/api/content/{movie.pk}/playback/').status_code, 409)
                from common.transcoding import FFmpegProvider
                self.assertTrue(process_one(FFmpegProvider()))

            asset.refresh_from_db()
            self.assertEqual(asset.state, 'ready', asset.error)
            self.client.force_authenticate(owner)
            status = self.client.get('/api/content/media/status/', {'reference_url': asset.reference_url})
            self.assertEqual(status.status_code, 200)
            self.assertTrue(status.data['ready'])
            self.client.force_authenticate(None)
            self.assertEqual(self.client.post(f'/api/content/{movie.pk}/playback/').status_code, 401)
            self.client.force_authenticate(viewer)
            delivery = {'ENABLED': True, 'KEY_PAIR_ID': 'fixture-key', 'PRIVATE_KEY_FILE': 'mocked',
                'COOKIE_DOMAIN': 'example.com', 'API_ORIGIN': 'https://api.example.com',
                'TTL': 900, 'LEGACY_PREFIXES': []}
            with override_settings(PLAYBACK_DELIVERY=delivery), \
                 patch('content.playback._signer', return_value=lambda message: b'fixture-signature'):
                grant = self.client.post(f'/api/content/{movie.pk}/playback/')
            self.assertEqual(grant.status_code, 200)
            self.assertEqual(grant.data['type'], 'hls')
            self.assertEqual(grant.data['url'], f'https://cdn.example.com/{asset.master_key}')
            self.assertEqual(set(grant.cookies), {'CloudFront-Policy', 'CloudFront-Signature',
                                                  'CloudFront-Key-Pair-Id'})
            self.assertEqual([row['height'] for row in asset.renditions], [360, 480])
            self.assertEqual(order[-1], asset.master_key)
            master = uploaded[asset.master_key][0].decode()
            for row in asset.renditions:
                self.assertIn(f'RESOLUTION={row["width"]}x{row["height"]}', master)
                self.assertIn(f'BANDWIDTH={row["bandwidth"]}', master)
                self.assertIn(row['playlist'], master)
                playlist_key = asset.master_key.rsplit('/', 1)[0] + '/' + row['playlist']
                playlist = uploaded[playlist_key][0].decode()
                self.assertIn('#EXT-X-ENDLIST', playlist)
                segments = [line for line in playlist.splitlines() if line.endswith('.ts')]
                self.assertTrue(segments)
                for segment in segments:
                    segment_key = playlist_key.rsplit('/', 1)[0] + '/' + segment
                    self.assertEqual(uploaded[segment_key][1], 'video/mp2t')
                    segment_file = Path(directory) / f'{row["height"]}-{segment}'
                    segment_file.write_bytes(uploaded[segment_key][0])
                    probe = subprocess.run(['ffprobe', '-v', 'error', '-show_entries',
                        'stream=codec_type,codec_name', '-of', 'csv=p=0', str(segment_file)],
                        check=True, capture_output=True, text=True).stdout
                    self.assertIn('h264,video', probe)
                    self.assertIn('aac,audio', probe)


@override_settings(MEDIA_STORAGE=TEST_STORAGE, MEDIA_PROCESSING_TIMEOUT=120)
@skipUnless(shutil.which('ffmpeg') and shutil.which('ffprobe'), 'FFmpeg tools are required')
class FullHdRenditionTests(SimpleTestCase):
    def test_full_hd_source_generates_all_four_actual_variants(self):
        import uuid
        from common.transcoding import FFmpegProvider

        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory) / 'full-hd.mp4'
            subprocess.run(['ffmpeg', '-nostdin', '-hide_banner', '-loglevel', 'error', '-y',
                '-f', 'lavfi', '-i', 'testsrc2=size=1920x1080:rate=15', '-t', '1',
                '-c:v', 'libx264', '-preset', 'ultrafast', '-pix_fmt', 'yuv420p', str(source)],
                check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            raw = source.read_bytes()
            asset = SimpleNamespace(pk=uuid.uuid4(), source_key='cbmvideo/private/full-hd.mp4',
                                    size_bytes=len(raw))
            s3 = Mock()
            s3.get_object.return_value = {'ContentLength': len(raw), 'Body': io.BytesIO(raw)}
            uploads = {}
            s3.upload_file.side_effect = lambda path, bucket, key, ExtraArgs: uploads.update({key: Path(path).read_bytes()})
            with patch('common.transcoding._client', return_value=s3):
                master_key, renditions = FFmpegProvider().transcode(asset,
                    f'protected/hls/{asset.pk}/full-hd-fixture/')
            self.assertEqual([row['height'] for row in renditions], [360, 480, 720, 1080])
            master = uploads[master_key].decode()
            for row in renditions:
                self.assertLessEqual(row['width'], 1920)
                self.assertIn(f'RESOLUTION={row["width"]}x{row["height"]}', master)
                self.assertIn(f'BANDWIDTH={row["bandwidth"]}', master)
                self.assertIn(row['playlist'], master)

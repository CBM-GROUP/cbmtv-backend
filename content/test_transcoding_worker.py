"""Durable queue recovery and provider boundaries; no live AWS calls."""
import json
import uuid
from datetime import timedelta
from io import BytesIO
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

from django.test import TestCase, SimpleTestCase, override_settings
from django.utils import timezone

from accounts.models import User
from common.transcoding import FFmpegProvider, TranscodingError, rendition_ladder
from content.management.commands.process_media import claim_media, process_one
from content.models import MediaAsset
from content.test_media_uploads import TEST_STORAGE


@override_settings(MEDIA_TRANSCODING_MAX_ATTEMPTS=3, MEDIA_PROCESSING_TIMEOUT=60)
class MediaWorkerTests(TestCase):
    def setUp(self):
        self.owner = User.objects.create_user('worker@example.com', 'Worker fixture', password='test')
        self.asset = MediaAsset.objects.create(
            owner=self.owner, source_key='cbmvideo/private/worker.mp4',
            reference_url='https://cdn.example.com/cbmvideo/private/worker.mp4',
            content_type='video/mp4', size_bytes=100, state='queued',
        )

    def provider(self):
        provider = Mock()
        provider.transcode.side_effect = lambda asset, prefix: (prefix + 'master.m3u8', [{'height': 360}])
        return provider

    def test_successful_job_is_ready_only_after_provider_upload_completion(self):
        provider = self.provider()
        def transcode(asset, prefix):
            persisted = MediaAsset.objects.get(pk=asset.pk)
            self.assertEqual(persisted.state, 'processing')
            self.assertEqual(persisted.master_key, '')
            self.assertIsNotNone(persisted.lease_id)
            return prefix + 'master.m3u8', [{'height': 360}]
        provider.transcode.side_effect = transcode
        self.assertTrue(process_one(provider))
        self.asset.refresh_from_db()
        self.assertEqual(self.asset.state, 'ready')
        self.assertEqual(self.asset.attempts, 1)
        self.assertIsNone(self.asset.lease_id)
        self.assertIsNone(self.asset.lease_until)
        provider.transcode.assert_called_once()
        self.assertFalse(process_one(provider))

    def test_uploaded_and_public_sources_are_not_claimed(self):
        for state, purpose in (('uploading', 'protected'), ('uploaded', 'protected'), ('queued', 'public')):
            MediaAsset.objects.filter(pk=self.asset.pk).update(state=state, purpose=purpose)
            self.assertIsNone(claim_media())

    def test_transient_failure_is_delayed_then_can_retry(self):
        provider = Mock()
        provider.transcode.side_effect = RuntimeError('https://signed.example/?secret=must-not-be-stored')
        self.assertTrue(process_one(provider))
        self.asset.refresh_from_db()
        self.assertEqual(self.asset.state, 'queued')
        self.assertGreater(self.asset.next_attempt_at, timezone.now())
        self.assertNotIn('secret', self.asset.error)
        self.assertIsNone(claim_media())
        MediaAsset.objects.filter(pk=self.asset.pk).update(next_attempt_at=timezone.now() - timedelta(seconds=1))
        self.assertTrue(process_one(self.provider()))
        self.asset.refresh_from_db()
        self.assertEqual(self.asset.state, 'ready')
        self.assertEqual(self.asset.attempts, 2)

    def test_final_failure_stops_automatic_retries(self):
        MediaAsset.objects.filter(pk=self.asset.pk).update(attempts=2)
        provider = Mock()
        provider.transcode.side_effect = TranscodingError('Transcoding timed out.')
        process_one(provider)
        self.asset.refresh_from_db()
        self.assertEqual(self.asset.state, 'failed')
        self.assertEqual(self.asset.error, 'Transcoding timed out.')
        self.assertIsNone(self.asset.next_attempt_at)
        self.assertIsNone(claim_media())

    def test_active_lease_is_exclusive_and_expired_lease_is_recoverable(self):
        first = claim_media()
        self.assertIsNotNone(first)
        self.assertIsNone(claim_media())
        MediaAsset.objects.filter(pk=self.asset.pk).update(lease_until=timezone.now() - timedelta(seconds=1))
        recovered = claim_media()
        self.assertNotEqual(first.lease_id, recovered.lease_id)
        self.assertEqual(recovered.attempts, 2)

    def test_expired_final_lease_becomes_failed_without_another_encode(self):
        MediaAsset.objects.filter(pk=self.asset.pk).update(state='processing', attempts=3,
            lease_id=uuid.uuid4(), lease_until=timezone.now() - timedelta(seconds=1))
        self.assertIsNone(claim_media())
        self.asset.refresh_from_db()
        self.assertEqual(self.asset.state, 'failed')
        self.assertIn('lease expired', self.asset.error)

    def test_stale_worker_cannot_publish_after_lease_replacement(self):
        replacement = uuid.uuid4()
        provider = self.provider()
        def transcode(asset, prefix):
            MediaAsset.objects.filter(pk=asset.pk).update(lease_id=replacement)
            return prefix + 'master.m3u8', [{'height': 360}]
        provider.transcode.side_effect = transcode
        process_one(provider)
        self.asset.refresh_from_db()
        self.assertEqual(self.asset.state, 'processing')
        self.assertEqual(self.asset.lease_id, replacement)
        self.assertEqual(self.asset.master_key, '')

    def test_expired_worker_cannot_publish_even_before_replacement(self):
        provider = self.provider()
        def transcode(asset, prefix):
            MediaAsset.objects.filter(pk=asset.pk).update(lease_until=timezone.now() - timedelta(seconds=1))
            return prefix + 'master.m3u8', [{'height': 360}]
        provider.transcode.side_effect = transcode
        process_one(provider)
        self.asset.refresh_from_db()
        self.assertNotEqual(self.asset.state, 'ready')
        self.assertEqual(self.asset.master_key, '')

    def test_incomplete_provider_result_does_not_mark_ready(self):
        provider = Mock()
        provider.transcode.return_value = ('protected/wrong/master.m3u8', [])
        process_one(provider)
        self.asset.refresh_from_db()
        self.assertEqual(self.asset.state, 'queued')
        self.assertIn('incomplete output', self.asset.error)


@override_settings(MEDIA_STORAGE=TEST_STORAGE, MEDIA_PROCESSING_TIMEOUT=60)
class FFmpegProviderTests(SimpleTestCase):
    def test_ladder_preserves_bounds_and_adds_supported_resolutions(self):
        self.assertEqual([row['height'] for row in rendition_ladder(1920, 1080)], [360, 480, 720, 1080])
        self.assertEqual([row['height'] for row in rendition_ladder(640, 480)], [360, 480])
        self.assertEqual(rendition_ladder(321, 241)[0]['height'], 240)
        for width, height in ((321, 241), (1080, 1920), (3840, 2160), (640, 360)):
            for row in rendition_ladder(width, height):
                self.assertLessEqual(row['width'], width)
                self.assertLessEqual(row['height'], height)
                self.assertEqual(row['width'] % 2, 0)
                self.assertEqual(row['height'] % 2, 0)
        with self.assertRaises(TranscodingError):
            rendition_ladder(0, 0)

    def test_anamorphic_display_aspect_becomes_square_pixels_without_upscaling(self):
        # PAL stored at 720x576 with 16:15 pixels displays at 4:3. The output
        # must preserve that display ratio instead of squeezing it to 5:4.
        ladder = rendition_ladder(720, 576, '16:15')
        self.assertEqual([(row['width'], row['height']) for row in ladder], [(480, 360), (640, 480)])
        for row in ladder:
            self.assertAlmostEqual(row['width'] / row['height'], 4 / 3)
            self.assertLessEqual(row['width'], 720)
            self.assertLessEqual(row['height'], 576)
        # Pixels narrower than square also need display-aspect correction.
        ladder = rendition_ladder(720, 480, '8:9')
        self.assertEqual((ladder[-1]['width'], ladder[-1]['height']), (640, 480))

    def test_anamorphic_quarter_turn_inverts_display_aspect_and_pixel_bounds(self):
        for rotation in (90, -90, 270):
            ladder = rendition_ladder(720, 576, '16:15', rotation)
            self.assertEqual([(row['width'], row['height']) for row in ladder], [(270, 360), (360, 480), (540, 720)])
            for row in ladder:
                self.assertAlmostEqual(row['width'] / row['height'], 3 / 4)
                self.assertLessEqual(row['width'], 576)
                self.assertLessEqual(row['height'], 720)

    def test_ultrawide_anamorphic_outputs_respect_encoder_resolution_cap(self):
        for row in rendition_ladder(3840, 1080, '1:1'):
            self.assertLessEqual(row['width'], 1920)
            self.assertLessEqual(row['height'], 1080)
            self.assertAlmostEqual(row['width'] / row['height'], 32 / 9, delta=0.01)

    def test_unspecified_pixel_aspect_falls_back_to_square_pixels(self):
        for aspect in ('N/A', '0:1', None):
            self.assertEqual(rendition_ladder(640, 480, aspect), rendition_ladder(640, 480))

    def asset(self):
        return SimpleNamespace(pk=uuid.uuid4(), source_key='cbmvideo/private/input.mp4', size_bytes=10)

    @patch('common.transcoding._client')
    def test_provider_uses_probed_pixel_aspect_and_rotation_in_encoder_dimensions(self, factory):
        for rotation, expected_scale in ((0, 'scale=480:360,setsar=1'), (90, 'scale=270:360,setsar=1')):
            with self.subTest(rotation=rotation):
                asset = self.asset()
                factory.return_value.get_object.return_value = {'ContentLength': 10, 'Body': BytesIO(b'x' * 10)}
                probe = {'streams': [{'index': 0, 'codec_type': 'video', 'width': 720, 'height': 576,
                    'avg_frame_rate': '24/1', 'sample_aspect_ratio': '16:15',
                    'side_data_list': [{'rotation': rotation}]}]}
                # Stop before real encoding; inspect the concrete command at the
                # provider boundary, where probe metadata could otherwise be lost.
                with patch.object(FFmpegProvider, '_run', side_effect=[json.dumps(probe).encode(), TranscodingError('test stop')]) as run:
                    with self.assertRaises(TranscodingError):
                        FFmpegProvider().transcode(asset, f'protected/hls/{asset.pk}/attempt/')
                command = run.call_args_list[1].args[0]
                self.assertEqual(command[command.index('-vf') + 1], expected_scale)

    @patch('common.transcoding._client')
    def test_size_mismatch_stops_before_decoding_or_publishing(self, factory):
        asset = self.asset()
        factory.return_value.get_object.return_value = {'ContentLength': 11, 'Body': BytesIO(b'x' * 11)}
        with patch.object(FFmpegProvider, '_run') as run:
            with self.assertRaises(TranscodingError):
                FFmpegProvider().transcode(asset, f'protected/hls/{asset.pk}/attempt/')
        run.assert_not_called()
        factory.return_value.upload_file.assert_not_called()

    @patch('common.transcoding._client')
    def test_no_audio_is_preserved_and_master_uploaded_after_every_child(self, factory):
        asset = self.asset()
        client = factory.return_value
        client.get_object.return_value = {'ContentLength': 10, 'Body': BytesIO(b'x' * 10)}
        commands = []
        probe = {'streams': [{'index': 0, 'codec_type': 'video', 'width': 320, 'height': 240, 'avg_frame_rate': '24/1'}]}
        def run(command, deadline):
            commands.append(command)
            if '-show_streams' in command:
                return json.dumps(probe).encode()
            playlist = Path(command[-1])
            playlist.write_text('#EXTM3U\n#EXTINF:6,\nsegment_000000.ts\n#EXT-X-ENDLIST\n')
            (playlist.parent / 'segment_000000.ts').write_bytes(b'fixture')
            return b''
        with patch.object(FFmpegProvider, '_run', side_effect=run):
            master, renditions = FFmpegProvider().transcode(asset, f'protected/hls/{asset.pk}/attempt/')
        self.assertEqual([row['height'] for row in renditions], [240])
        self.assertIn('-an', commands[-1])
        self.assertNotIn('-c:a', commands[-1])
        self.assertNotIn('mp4a', renditions[0]['codecs'])
        self.assertIn('file,pipe,crypto', commands[-1])
        self.assertIn('mov,matroska,webm', commands[-1])
        calls = client.upload_file.call_args_list
        self.assertEqual(calls[-1].args[2], master)
        self.assertEqual(calls[0].kwargs['ExtraArgs']['ContentType'], 'video/mp2t')
        self.assertEqual(calls[-1].kwargs['ExtraArgs']['ContentType'], 'application/vnd.apple.mpegurl')

    def test_output_prefix_cannot_escape_asset_namespace(self):
        asset = self.asset()
        with self.assertRaises(TranscodingError):
            FFmpegProvider().transcode(asset, 'public/other/')

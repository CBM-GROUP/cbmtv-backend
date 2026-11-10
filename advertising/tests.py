import io
import uuid
import threading
from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta
from unittest import skipUnless
from unittest.mock import patch, MagicMock
from PIL import Image
from django.core.cache import cache
from django.core.files.uploadedfile import SimpleUploadedFile
from django.core.management import call_command
from django.db import connection, connections, transaction
from django.test import override_settings, TransactionTestCase
from django.utils import timezone
from rest_framework.test import APITestCase
from rest_framework.response import Response
from rest_framework.exceptions import ValidationError
from accounts.models import User, DashboardRole
from channel.models import Channel
from content.models import Content, MediaAsset, Genre
from content.test_media_uploads import TEST_STORAGE
from discovery.models import PlaybackSession, DiscoveryEvent
from homepage.models import HomepageConfiguration
from homepage.defaults import default_sections
from .models import Advertiser, Campaign, Creative, ImageAsset, Placement, Decision, Event, CampaignDay, AdRequest, Audit
from .delivery import context_session, decide, ingest
from .serializers import safe_destination, targeting_rules

BASE = '/api/advertising/'


def fixtures(user):
    advertiser = Advertiser.objects.create(name='Fixture sponsor', email='private@example.com', billing_reference='PRIVATE')
    campaign = Campaign.objects.create(name='Fixture campaign', advertiser=advertiser,
        starts_at=timezone.now() - timedelta(days=1), ends_at=timezone.now() + timedelta(days=2),
        approval='approved', status='active', placements=['content_sponsor', 'channel_sponsor', 'homepage_banner'])
    image = ImageAsset.objects.create(owner=user, desktop_url='https://cdn.example.com/banner.webp',
        mobile_url='https://cdn.example.com/small.webp', width=1000, height=300, size_bytes=1000)
    creative = Creative.objects.create(name='Fixture creative', campaign=campaign, kind='image', image=image,
        approval='approved', placements=campaign.placements, destination_url='https://sponsor.example.com/offer')
    for code in campaign.placements:
        Placement.objects.update_or_create(code=code, defaults={'enabled': True})
    channel = Channel.objects.create(name='Advertising channel')
    content = Content.objects.create(title='Published fixture', content_type='movie', status='approved', channel=channel, category='movies')
    return advertiser, campaign, creative, content


@override_settings(ADVERTISING_ENABLED=True)
class AdvertisingTests(APITestCase):
    def setUp(self):
        cache.clear()
        self.staff = User.objects.create_superuser('ad-staff@example.com', 'Staff', password='test')
        self.viewer = User.objects.create_user('ad-viewer@example.com', 'Viewer', password='test')
        self.other = User.objects.create_user('ad-other@example.com', 'Other', password='test')
        self.advertiser, self.campaign, self.creative, self.content = fixtures(self.staff)

    def session(self, context='content', **fields):
        return self.client.post(BASE + 'session/', {'context': context, **({'content': self.content.pk} if context == 'content' else {}), **fields}, format='json')

    def decision(self, token=None, placement='content_sponsor', **fields):
        token = token or self.session().data['session']
        return self.client.post(BASE + 'decision/', {'session': token, 'placement': placement,
            'request_id': str(uuid.uuid4()), **fields}, format='json')

    def event(self, token, decision, kind='impression', **fields):
        return self.client.post(BASE + 'events/', {'session': token, 'decision_id': str(decision.pk), 'kind': kind, **fields}, format='json')

    def served(self):
        token = self.session().data['session']
        result = self.decision(token)
        self.assertEqual(result.status_code, 200, result.data)
        self.assertIsNotNone(result.data['ad'])
        decision = Decision.objects.get(pk=result.data['decision_id'])
        Decision.objects.filter(pk=decision.pk).update(created_at=timezone.now() - timedelta(seconds=5))
        return token, decision

    def test_private_records_and_permissions(self):
        for user, status in [(None, 401), (self.viewer, 403)]:
            self.client.force_authenticate(user)
            for route in ['advertisers/', 'campaigns/', 'creatives/', 'placements/', 'reports/', 'targets/']:
                self.assertEqual(self.client.get(BASE + route).status_code, status)
        self.client.force_authenticate(self.staff)
        result = self.client.get(BASE + 'advertisers/')
        self.assertEqual(result.data['results'][0]['billing_reference'], 'PRIVATE')
        self.assertEqual(result['Cache-Control'], 'private, no-store')

    def test_public_payload_never_exposes_contact_source_or_budget(self):
        result = self.decision()
        self.assertIsNotNone(result.data['ad'])
        for private in ['PRIVATE', 'private@example.com', 'source_key', 'impression_limit', 'billing_reference']:
            self.assertNotIn(private, str(result.data))

    def test_featured_insertion_uses_existing_campaign_eligibility(self):
        code = 'homepage_featured_sponsor'
        Placement.objects.update_or_create(code=code, defaults={'enabled': True})
        self.campaign.placements.append(code); self.campaign.save(update_fields=['placements'])
        self.creative.placements.append(code); self.creative.save(update_fields=['placements'])
        config, _ = HomepageConfiguration.objects.get_or_create(pk=1)
        config.advert_insertion_enabled = True
        for interval in (1, 2, 3):
            config.advert_interval = interval; config.save()
            token = self.session('homepage').data['session']
            self.assertEqual(self.decision(token, code, break_key=f'featured-{interval}').data['ad']['name'], self.creative.name)
            self.assertEqual(self.decision(token, code, break_key=f'featured-{interval + 1 if interval > 1 else 21}').status_code, 400)
        self.assertEqual(Event.objects.count(), 0)
        config.advert_insertion_enabled = False; config.save()
        self.assertEqual(self.decision(self.session('homepage').data['session'], code, break_key='featured-3').status_code, 400)
        config.advert_insertion_enabled = True; config.save()
        self.creative.approval = 'draft'; self.creative.save(update_fields=['approval'])
        self.assertIsNone(self.decision(self.session('homepage').data['session'], code, break_key='featured-3').data['ad'])

    def test_flag_and_placements_fail_open(self):
        with override_settings(ADVERTISING_ENABLED=False):
            self.assertFalse(self.session().data['enabled'])
            self.assertIsNone(self.decision(token='ignored').data['ad'])
            self.assertEqual(Decision.objects.count(), 0)
        Placement.objects.filter(pk='content_sponsor').update(enabled=False)
        self.assertIsNone(self.decision().data['ad'])

    def test_decision_and_request_idempotency_are_distinct_from_impressions(self):
        token = self.session().data['session']
        request_id = str(uuid.uuid4())
        first = self.decision(token, request_id=request_id)
        second = self.decision(token, request_id=request_id)
        third = self.decision(token)
        self.assertEqual(first.data['decision_id'], second.data['decision_id'])
        self.assertEqual(first.data['decision_id'], third.data['decision_id'])
        self.assertEqual((Decision.objects.count(), AdRequest.objects.count(), Event.objects.count()), (1, 2, 0))
        conflict = self.decision(self.session().data['session'], request_id=request_id)
        self.assertEqual(conflict.status_code, 400)

    def test_impression_threshold_dedup_and_actual_reporting(self):
        token, decision = self.served()
        self.assertEqual(self.event(token, decision).status_code, 200)
        self.assertTrue(self.event(token, decision).data['duplicate'])
        self.campaign.refresh_from_db()
        self.assertEqual(self.campaign.impressions, 1)
        self.assertEqual(CampaignDay.objects.get(campaign=self.campaign).impressions, 1)
        self.assertEqual(DiscoveryEvent.objects.count(), 0)
        self.client.force_authenticate(self.staff)
        report = self.client.get(BASE + 'reports/').data
        self.assertEqual(report['summary']['impression'], 1)
        self.assertEqual(report['summary']['requests'], 1)
        self.assertIsNone(report['summary']['completion_rate'])
        self.assertEqual(report['by_placement'][0]['decision__placement_id'], 'content_sponsor')
        self.assertEqual(report['by_creative'][0]['decision__creative_id'], self.creative.pk)
        filtered = self.client.get(BASE + 'reports/', {'creative': self.creative.pk}).data
        self.assertEqual(filtered['summary']['impression'], 1)
        self.assertEqual(filtered['by_campaign'][0]['impression'], filtered['by_creative'][0]['impression'])
        self.assertEqual(self.client.get(BASE + 'reports/', {'creative': 'invalid'}).status_code, 400)

    def test_campaign_totals_equal_individual_advert_events(self):
        first_token, first = self.served()
        self.assertEqual(self.event(first_token, first).status_code, 200)
        second = Creative.objects.create(name='Second advert', campaign=self.campaign, kind='image',
            image=self.creative.image, approval='approved', placements=self.creative.placements)
        self.creative.approval = 'draft'; self.creative.save(update_fields=['approval'])
        second_token, decision = self.served()
        self.assertEqual(decision.creative_id, second.pk)
        self.assertEqual(self.event(second_token, decision).status_code, 200)
        self.client.force_authenticate(self.staff)
        data = self.client.get(BASE + 'reports/', {'campaign': self.campaign.pk}).data
        self.assertEqual(data['summary']['impression'], 2)
        self.assertEqual(sum(row['impression'] for row in data['by_creative']), 2)
        self.assertEqual(data['by_campaign'][0]['impression'], 2)
        filtered = self.client.get(BASE + 'reports/', {'creative': second.pk}).data
        self.assertEqual(filtered['summary']['impression'], 1)
        self.assertEqual(filtered['by_date'][0]['impression'], 1)

    def test_early_forged_cross_session_and_replayed_events(self):
        token = self.session().data['session']
        response = self.decision(token)
        decision = Decision.objects.get(pk=response.data['decision_id'])
        self.assertEqual(self.event(token, decision).status_code, 400)
        self.assertEqual(self.event(token, decision, 'click').status_code, 200)
        self.assertFalse(Event.objects.filter(decision=decision, kind='impression').exists())
        other = self.session().data['session']
        self.assertEqual(self.event(other, decision).status_code, 403)
        self.assertEqual(self.event('forged', decision).status_code, 403)
        self.assertEqual(self.event(token, decision, 'complete').status_code, 400)
        Decision.objects.filter(pk=decision.pk).update(expires_at=timezone.now() - timedelta(seconds=1))
        self.assertEqual(self.event(token, decision).status_code, 400)

    def test_signed_in_session_is_bound_to_owner_and_context(self):
        self.client.force_authenticate(self.viewer)
        token = self.session().data['session']
        self.client.force_authenticate(self.other)
        self.assertEqual(self.decision(token).status_code, 403)
        self.client.force_authenticate(self.viewer)
        self.content.status = 'preview'; self.content.save()
        self.assertEqual(self.decision(token).status_code, 403)

    def test_schedule_approval_advertiser_pause_and_readiness_gates(self):
        for field, value in [('approval', 'review'), ('status', 'paused'), ('starts_at', timezone.now() + timedelta(days=1)), ('ends_at', timezone.now() - timedelta(seconds=1))]:
            original = getattr(self.campaign, field)
            # For the expiry case keep a valid database schedule.
            if field == 'starts_at': value = timezone.now() + timedelta(hours=1)
            setattr(self.campaign, field, value); self.campaign.save()
            self.assertIsNone(self.decision().data['ad'], field)
            setattr(self.campaign, field, original); self.campaign.save()
        self.advertiser.active = False; self.advertiser.save()
        self.assertIsNone(self.decision().data['ad'])
        self.advertiser.active = True; self.advertiser.save()
        self.creative.approval = 'draft'; self.creative.save()
        self.assertIsNone(self.decision().data['ad'])

    def test_total_daily_caps_reserve_and_release_without_inflating_impressions(self):
        self.campaign.impression_limit = 1; self.campaign.save()
        token, first = self.served()
        self.assertIsNone(self.decision().data['ad'])
        self.campaign.refresh_from_db(); self.assertEqual(self.campaign.impressions, 0)
        self.assertEqual(self.event(token, first, 'failure').status_code, 200)
        self.assertIsNotNone(self.decision().data['ad'])
        Decision.objects.filter(impression_at__isnull=True).update(expires_at=timezone.now() - timedelta(seconds=1))
        token, second = self.served()
        self.assertEqual(self.event(token, second).status_code, 200)
        self.assertIsNone(self.decision().data['ad'])

    def test_daily_cap_survives_expired_reservations_and_midnight(self):
        self.campaign.daily_limit = 1; self.campaign.save()
        token, decision = self.served()
        Decision.objects.filter(pk=decision.pk).update(created_at=timezone.now() - timedelta(days=1))
        self.assertIsNone(self.decision().data['ad'])
        self.event(token, decision)
        self.assertIsNone(self.decision().data['ad'])

    def test_target_groups_and_untrusted_geo(self):
        genre = Genre.objects.create(name='Drama', for_movies=True)
        self.content.genres.add(genre)
        self.campaign.targeting = {'categories': ['movies'], 'genres': [genre.pk], 'channels': [self.content.channel_id], 'contents': [self.content.pk]}
        self.campaign.save()
        self.assertIsNotNone(self.decision().data['ad'])
        self.campaign.targeting['categories'] = ['animations']; self.campaign.save()
        self.assertIsNone(self.decision().data['ad'])
        self.campaign.targeting = {'regions': ['UG']}; self.campaign.save()
        result = self.decision(region='UG', HTTP_X_CBM_COUNTRY='UG')
        self.assertIsNone(result.data['ad'])

    def test_priority_and_rotation_with_frequency_caps(self):
        another = Campaign.objects.create(name='Second', advertiser=self.advertiser, starts_at=self.campaign.starts_at,
            ends_at=self.campaign.ends_at, approval='approved', status='active', placements=['content_sponsor'], priority=2)
        creative = Creative.objects.create(name='Priority', campaign=another, kind='image', image=self.creative.image, approval='approved', placements=['content_sponsor'])
        self.assertEqual(self.decision().data['ad']['name'], 'Priority')
        another.status = 'paused'; another.save()
        self.campaign.session_limit = 1; self.campaign.placements += ['homepage_featured_sponsor']; self.campaign.save()
        self.creative.placements = self.campaign.placements; self.creative.save()
        HomepageConfiguration.objects.create(sections=default_sections() + [
            {'id': 'homepage_banner', 'enabled': True}, {'id': 'homepage_featured_sponsor', 'enabled': True}])
        Placement.objects.create(code='homepage_featured_sponsor', enabled=True) if not Placement.objects.filter(pk='homepage_featured_sponsor').exists() else Placement.objects.filter(pk='homepage_featured_sponsor').update(enabled=True)
        session = context_session({'context': 'homepage'}, self.viewer)
        placement = Placement.objects.get(pk='homepage_banner')
        first = decide(session, placement, 'once', uuid.uuid4(), Response())
        # A second eligible placement in the same session shares its frequency cap.
        second = decide(session, Placement.objects.get(pk='homepage_featured_sponsor'), 'once', uuid.uuid4(), Response())
        self.assertIsNotNone(first['ad']); self.assertIsNone(second['ad'])

    def test_equal_priority_campaigns_rotate_before_repeating(self):
        second = Campaign.objects.create(name='Rotation second', advertiser=self.advertiser, starts_at=self.campaign.starts_at,
            ends_at=self.campaign.ends_at, approval='approved', status='active', placements=['content_sponsor'], priority=1)
        Creative.objects.create(name='Rotation creative', campaign=second, kind='image', image=self.creative.image,
            approval='approved', placements=['content_sponsor'])
        choices = [self.decision().data['ad']['name'] for _ in range(3)]
        self.assertEqual(choices, ['Fixture creative', 'Rotation creative', 'Fixture creative'])

    def test_homepage_requires_explicit_configuration_and_keeps_hero_separate(self):
        token = self.session(context='homepage').data['session']
        self.assertEqual(self.decision(token, 'homepage_banner').status_code, 400)
        HomepageConfiguration.objects.create(sections=default_sections() + [{'id': 'homepage_banner', 'enabled': True}])
        token = self.session(context='homepage').data['session']
        self.assertIsNotNone(self.decision(token, 'homepage_banner').data['ad'])
        self.assertEqual(self.decision(token, 'featured_hero').status_code, 400)

    def test_promoted_unpublished_content_is_never_served(self):
        self.creative.promoted_content = self.content; self.creative.save()
        other_content = Content.objects.create(title='Context', channel=self.content.channel, status='approved', content_type='movie')
        self.content.status = 'preview'; self.content.save()
        token = self.session(content=other_content.pk).data['session']
        self.assertIsNone(self.decision(token).data['ad'])

    def test_sponsored_collection_preserves_order_and_hides_unpublished_items(self):
        second = Content.objects.create(title='Second sponsored title', channel=self.content.channel, status='approved', content_type='movie')
        self.creative.promoted_contents = [second.pk, self.content.pk]; self.creative.save()
        result = self.decision().data['ad']
        self.assertEqual([row['id'] for row in result['sponsored_items']], [second.pk, self.content.pk])
        second.status = 'preview'; second.save()
        self.assertIsNone(self.decision().data['ad'])

    def test_campaign_validation_approval_workflow_and_edits_reset_review(self):
        self.client.force_authenticate(self.staff)
        endpoint = BASE + f'campaigns/{self.campaign.pk}/'
        bad = self.client.patch(endpoint, {'targeting': {'sensitive_profile': ['x']}}, format='json')
        self.assertEqual(bad.status_code, 400)
        self.assertEqual(self.client.patch(endpoint, {'name': 'Revised'}, format='json').status_code, 200)
        self.campaign.refresh_from_db(); self.assertEqual((self.campaign.approval, self.campaign.status), ('draft', 'paused'))
        flow = endpoint + 'workflow/'
        self.assertEqual(self.client.post(flow, {'action': 'resume'}).status_code, 400)
        self.assertEqual(self.client.post(flow, {'action': 'submit'}).data['approval'], 'review')
        self.assertEqual(self.client.post(flow, {'action': 'approve'}).data['approval'], 'approved')
        self.assertEqual(self.client.post(flow, {'action': 'resume'}).data['status'], 'active')
        self.assertEqual(self.client.post(flow, {'action': 'pause'}).data['status'], 'paused')
        self.assertTrue(Audit.objects.filter(action='approve').exists())

    def test_role_cannot_approve_but_can_manage_and_preview(self):
        role = DashboardRole.objects.create(name='Ad operator', permissions=['adverts'])
        operator = User.objects.create_user('operator@example.com', 'Operator', password='test')
        operator.dashboard_role = role; operator.save()
        self.client.force_authenticate(operator)
        self.creative.approval = 'review'; self.creative.save()
        flow = BASE + f'creatives/{self.creative.pk}/workflow/'
        self.assertEqual(self.client.post(flow, {'action': 'approve'}).status_code, 403)
        self.assertEqual(self.client.post(BASE + f'creatives/{self.creative.pk}/preview/').status_code, 200)
        self.client.force_authenticate(self.staff)
        self.assertEqual(self.client.post(flow, {'action': 'approve'}).data['approval'], 'approved')

    def test_creative_edits_cannot_keep_approval_or_attach_another_owners_asset(self):
        token, old_decision = self.served()
        self.client.force_authenticate(self.staff)
        endpoint = BASE + f'creatives/{self.creative.pk}/'
        response = self.client.patch(endpoint, {'destination_url': 'javascript:alert(1)'}, format='json')
        self.assertEqual(response.status_code, 400)
        self.client.patch(endpoint, {'cta': 'Explore'}, format='json')
        self.creative.refresh_from_db(); self.assertEqual(self.creative.approval, 'draft')
        old_decision.refresh_from_db(); self.assertIsNotNone(old_decision.terminal_at)
        self.client.force_authenticate(self.other)
        role = DashboardRole.objects.create(name='Other operator', permissions=['adverts'])
        self.other.dashboard_role = role; self.other.save()
        payload = {'campaign': self.campaign.pk, 'kind': 'image', 'image': str(self.creative.image_id), 'name': 'Stolen', 'placements': ['content_sponsor']}
        self.assertEqual(self.client.post(BASE + 'creatives/', payload, format='json').status_code, 400)

    def test_destination_target_and_date_validation_are_strict(self):
        for url in ['http://example.com', '//example.com', 'https://127.0.0.1/a', 'https://user:pass@example.com', 'https://localhost/a', 'https://example.local/a', 'https://example.com:444/a']:
            with self.assertRaises(Exception): safe_destination(url)
        self.assertEqual(safe_destination('https://brand.example.com/offer?x=1'), 'https://brand.example.com/offer?x=1')
        for value in [{'channels': [True]}, {'genres': [999999]}, {'regions': ['uganda']}, {'categories': 'movies'}, {'categories': [[]]}]:
            with self.assertRaises(Exception): targeting_rules(value)
        self.client.force_authenticate(self.staff)
        for query in ['?from=2026-02-30', '?from=2000-01-01', '?to=9999-12-31']:
            self.assertEqual(self.client.get(BASE + 'reports/' + query).status_code, 400)

    def test_malformed_requests_are_validation_errors_and_internal_promotions_track_clicks(self):
        for route in ('session/', 'decision/', 'events/'):
            self.assertEqual(self.client.post(BASE + route, [], format='json').status_code, 400)
        token, decision = self.served()
        self.event(token, decision)
        self.creative.destination_url = ''; self.creative.promoted_content = self.content; self.creative.save()
        self.assertEqual(self.event(token, decision, 'click').status_code, 200)

    @override_settings(MEDIA_STORAGE=TEST_STORAGE)
    @patch('advertising.media._client')
    def test_image_decode_dimensions_mime_ownership_and_optimized_variants(self, client):
        self.client.force_authenticate(self.staff)
        raw = io.BytesIO(); Image.new('RGB', (1200, 300)).save(raw, format='PNG')
        file = SimpleUploadedFile('banner.png', raw.getvalue(), content_type='image/png')
        result = self.client.post(BASE + 'images/', {'file': file}, format='multipart')
        self.assertEqual(result.status_code, 201, result.data)
        asset = ImageAsset.objects.get(pk=result.data['id'])
        self.assertEqual((asset.owner, asset.width, asset.height), (self.staff, 1200, 300))
        self.assertEqual(client.return_value.put_object.call_count, 2)
        body = client.return_value.put_object.call_args.kwargs['Body']
        with Image.open(io.BytesIO(body)) as decoded:
            self.assertEqual(decoded.format, 'WEBP'); self.assertLessEqual(decoded.width, 640)
        forged = SimpleUploadedFile('fake.png', b'not an image', content_type='image/png')
        self.assertEqual(self.client.post(BASE + 'images/', {'file': forged}, format='multipart').status_code, 400)
        mismatch = SimpleUploadedFile('wrong.jpg', raw.getvalue(), content_type='image/jpeg')
        self.assertEqual(self.client.post(BASE + 'images/', {'file': mismatch}, format='multipart').status_code, 400)

    def make_video(self):
        self.campaign.placements = ['preroll', 'midroll', 'postroll']; self.campaign.skippable_after = 5; self.campaign.save()
        media = MediaAsset.objects.create(owner=self.staff, source_key='cbmvideo/private/ad.mp4',
            reference_url='https://cdn.example.com/private/ad.mp4', content_type='video/mp4', size_bytes=1000,
            purpose='protected', state='ready', renditions=[{'duration_seconds': 20}])
        media.master_key = f'protected/hls/{media.pk}/attempt/master.m3u8'; media.save()
        self.creative.kind = 'video'; self.creative.image = None; self.creative.media = media; self.creative.placements = self.campaign.placements; self.creative.save()
        for code in self.campaign.placements:
            Placement.objects.update_or_create(code=code, defaults={'enabled': True, 'breakpoints': [60] if code == 'midroll' else []})
        return media

    @patch('advertising.delivery.grant', return_value='https://cdn.example.com/protected/master.m3u8')
    @override_settings(MEDIA_STORAGE={'CLOUDFRONT_BASE_URL': 'https://cdn.example.com'})
    def test_video_sessions_milestones_skip_and_replay(self, grant):
        self.make_video()
        playback = PlaybackSession.objects.create(user=self.viewer, content=self.content)
        self.client.force_authenticate(self.other)
        self.assertEqual(self.session('playback', playback_session=str(playback.pk)).status_code, 400)
        self.client.force_authenticate(self.viewer)
        token = self.session('playback', playback_session=str(playback.pk)).data['session']
        response = self.decision(token, 'preroll')
        self.assertEqual(response.data['ad']['duration_seconds'], 20)
        decision = Decision.objects.get(pk=response.data['decision_id'])
        self.assertEqual(self.event(token, decision, 'video_start').status_code, 200)
        self.assertEqual(self.event(token, decision, 'skip', position_seconds=10).status_code, 400)
        Decision.objects.filter(pk=decision.pk).update(created_at=timezone.now() - timedelta(seconds=30), started_at=timezone.now() - timedelta(seconds=25))
        self.assertEqual(self.event(token, decision, 'impression', position_seconds=3).status_code, 200)
        self.assertEqual(self.event(token, decision, 'complete', position_seconds=20).status_code, 400)
        for kind, position in [('first_quartile', 5), ('midpoint', 10), ('third_quartile', 15), ('complete', 20)]:
            self.assertEqual(self.event(token, decision, kind, position_seconds=position).status_code, 200, kind)
        self.assertIsNone(self.decision(token, 'preroll').data['ad'])
        self.assertEqual(self.event(token, decision, 'failure').status_code, 400)
        self.assertEqual(self.decision(token, 'midroll', break_key='61').status_code, 400)

    def test_unready_video_and_delivery_configuration_fail_open(self):
        media = self.make_video(); media.state = 'processing'; media.save()
        self.client.force_authenticate(self.viewer)
        playback = PlaybackSession.objects.create(user=self.viewer, content=self.content)
        token = self.session('playback', playback_session=str(playback.pk)).data['session']
        self.assertIsNone(self.decision(token, 'preroll').data['ad'])

    def test_pause_invalidates_outstanding_decision_before_impression(self):
        token, decision = self.served()
        self.campaign.status = 'paused'; self.campaign.save()
        self.assertEqual(self.event(token, decision).status_code, 400)
        self.assertIsNone(self.decision(token).data['ad'])

    def test_cleanup_dry_run_retains_lifetime_counter(self):
        token, decision = self.served(); self.event(token, decision)
        Decision.objects.filter(pk=decision.pk).update(created_at=timezone.now() - timedelta(days=91))
        call_command('cleanup_advertising', stdout=io.StringIO())
        self.assertTrue(Decision.objects.filter(pk=decision.pk).exists())
        call_command('cleanup_advertising', '--apply', stdout=io.StringIO())
        self.assertFalse(Decision.objects.filter(pk=decision.pk).exists())
        self.campaign.refresh_from_db(); self.assertEqual(self.campaign.impressions, 1)


@skipUnless(connection.vendor == 'postgresql', 'Hard-cap row locking requires PostgreSQL.')
@override_settings(ADVERTISING_ENABLED=True)
class AdvertisingConcurrencyTests(TransactionTestCase):
    def setUp(self):
        self.user = User.objects.create_user('concurrent-ad@example.com', 'Viewer', password='test')
        _, self.campaign, _, self.content = fixtures(self.user)
        self.campaign.impression_limit = 1; self.campaign.save()
        self.sessions = [context_session({'context': 'content', 'content': self.content.pk}, self.user) for _ in range(2)]

    def test_distinct_sessions_cannot_overreserve_last_impression(self):
        def reserve(session):
            try:
                return decide(session, Placement.objects.get(pk='content_sponsor'), 'once', uuid.uuid4(), Response())['ad'] is not None
            finally:
                connections.close_all()
        with ThreadPoolExecutor(max_workers=2) as pool:
            results = list(pool.map(reserve, self.sessions))
        self.assertEqual(sum(results), 1)

    def test_concurrent_duplicate_impressions_count_once(self):
        session = self.sessions[0]
        result = decide(session, Placement.objects.get(pk='content_sponsor'), 'once', uuid.uuid4(), Response())
        decision_id = uuid.UUID(result['decision_id'])
        Decision.objects.filter(pk=decision_id).update(created_at=timezone.now() - timedelta(seconds=5))
        def event(_):
            try:
                return ingest(session, decision_id, 'impression', 0)
            finally:
                connections.close_all()
        with ThreadPoolExecutor(max_workers=2) as pool:
            results = list(pool.map(event, range(2)))
        self.assertEqual(sum(not r['duplicate'] for r in results), 1)
        self.campaign.refresh_from_db(); self.assertEqual(self.campaign.impressions, 1)

    def test_distinct_sessions_reusing_request_uuid_get_validation_not_server_error(self):
        request_id = uuid.uuid4()
        def reserve(session):
            try:
                decide(session, Placement.objects.get(pk='content_sponsor'), 'once', request_id, Response())
                return 'served'
            except ValidationError:
                return 'conflict'
            finally:
                connections.close_all()
        with ThreadPoolExecutor(max_workers=2) as pool:
            results = list(pool.map(reserve, self.sessions))
        self.assertEqual(sorted(results), ['conflict', 'served'])
        self.assertEqual(AdRequest.objects.count(), 1)
        self.assertEqual(Decision.objects.count(), 1)

    def test_daily_only_cap_is_serialized_across_sessions(self):
        self.campaign.impression_limit = None; self.campaign.daily_limit = 1; self.campaign.save()
        self.test_distinct_sessions_cannot_overreserve_last_impression()

    def test_busy_candidate_uses_no_ad_instead_of_waiting_on_mutable_lock_order(self):
        acquired, release = threading.Event(), threading.Event()
        def lock_campaign():
            try:
                with transaction.atomic():
                    Campaign.objects.select_for_update().get(pk=self.campaign.pk)
                    acquired.set()
                    release.wait(5)
            finally:
                connections.close_all()
        with ThreadPoolExecutor(max_workers=1) as pool:
            held = pool.submit(lock_campaign)
            self.assertTrue(acquired.wait(3))
            try:
                result = decide(self.sessions[0], Placement.objects.get(pk='content_sponsor'), 'once', uuid.uuid4(), Response())
                self.assertIsNone(result['ad'])
                self.assertEqual(result['reason'], 'capacity_busy')
            finally:
                release.set()
            held.result()

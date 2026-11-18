import uuid
from datetime import timedelta
from unittest import skipUnless
from concurrent.futures import ThreadPoolExecutor
from unittest.mock import patch

from django.db import connection, connections
from django.test import TransactionTestCase, override_settings
from django.utils import timezone
from rest_framework.response import Response
from rest_framework.test import APITestCase

from accounts.models import User
from channel.models import Channel
from content.models import Content, MediaAsset
from discovery.models import PlaybackSession
from .delivery import context_session, decide
from .models import (Advertiser, AdvertisingOperation, AllocationDay, Campaign, Creative,
    Decision, DeliveryAllocation, Event, ImageAsset, Placement, Audit)
from .reports import rates


BASE = '/api/advertising/'


@override_settings(ADVERTISING_ENABLED=True, ADVERTISING_ALLOCATION_DELIVERY_ENABLED=True)
class AllocationDeliveryTests(APITestCase):
    def setUp(self):
        self.staff = User.objects.create_superuser('phase2b-staff@example.com', 'Staff', password='test')
        self.viewer = User.objects.create_user('phase2b-viewer@example.com', 'Viewer', password='test')
        self.client.force_authenticate(self.viewer)
        self.advertiser = Advertiser.objects.create(name='Direct advertiser')
        self.image = ImageAsset.objects.create(owner=self.staff, desktop_url='https://cdn.example.com/ad.webp',
            mobile_url='https://cdn.example.com/ad-small.webp', width=640, height=120, size_bytes=1000)
        self.creative = Creative.objects.create(name='Direct creative', advertiser=self.advertiser, kind='image',
            image=self.image, approval='approved')
        self.placement = Placement.objects.get(code='content_sponsor')
        self.placement.enabled = True
        self.placement.save(update_fields=['enabled'])
        self.allocation = DeliveryAllocation.objects.create(creative=self.creative, placement=self.placement,
            starts_at=timezone.now() - timedelta(minutes=1), status='active')
        channel = Channel.objects.create(name='Phase 2B channel')
        self.content = Content.objects.create(title='Phase 2B title', channel=channel, content_type='movie', status='approved')

    def session(self):
        result = self.client.post(BASE + 'session/', {'context': 'content', 'content': self.content.pk}, format='json')
        self.assertEqual(result.status_code, 200, result.data)
        return result.data['session']

    def decision(self, token=None):
        result = self.client.post(BASE + 'decision/', {'session': token or self.session(),
            'placement': self.placement.pk, 'request_id': str(uuid.uuid4())}, format='json')
        self.assertEqual(result.status_code, 200, result.data)
        return result

    def test_direct_creative_requires_approved_active_current_allocation(self):
        served = self.decision().data
        self.assertEqual(served['ad']['name'], self.creative.name)
        record = Decision.objects.get(pk=served['decision_id'])
        self.assertEqual(record.allocation_id, self.allocation.pk)
        self.assertIsNone(record.campaign_id)
        for model, field, value in [(self.creative, 'approval', 'draft'),
                                    (self.allocation, 'status', 'paused'),
                                    (self.allocation, 'starts_at', timezone.now() + timedelta(minutes=1)),
                                    (self.allocation, 'ends_at', timezone.now() - timedelta(seconds=1)),
                                    (self.advertiser, 'active', False),
                                    (self.placement, 'enabled', False)]:
            old = getattr(model, field)
            setattr(model, field, value)
            model.save(update_fields=[field])
            self.assertIsNone(self.decision().data['ad'], field)
            setattr(model, field, old)
            model.save(update_fields=[field])
        self.allocation.status = 'active'
        self.allocation.starts_at = timezone.now() - timedelta(minutes=1)
        self.allocation.ends_at = None
        self.allocation.save()

    def test_aware_schedule_start_is_inclusive_and_end_is_exclusive(self):
        start = timezone.now() + timedelta(minutes=5)
        end = start + timedelta(hours=1)
        self.allocation.starts_at = start
        self.allocation.ends_at = end
        self.allocation.save(update_fields=['starts_at', 'ends_at'])
        with patch('advertising.delivery.timezone.now', return_value=start - timedelta(microseconds=1)):
            self.assertIsNone(self.decision().data['ad'])
        with patch('advertising.delivery.timezone.now', return_value=start):
            self.assertIsNotNone(self.decision().data['ad'])
        with patch('advertising.delivery.timezone.now', return_value=end):
            self.assertIsNone(self.decision().data['ad'])

    def test_campaign_controls_combine_with_allocation_and_legacy_rolls_back(self):
        campaign = Campaign.objects.create(name='Campaign', advertiser=self.advertiser, approval='approved',
            status='active', starts_at=timezone.now() - timedelta(hours=1),
            ends_at=timezone.now() + timedelta(hours=1), placements=['content_sponsor'],
            targeting={'categories': ['animations']}, priority=10)
        self.creative.campaign = campaign
        self.creative.placements = ['content_sponsor']
        self.creative.save()
        self.assertIsNone(self.decision().data['ad'])
        campaign.targeting = {}
        campaign.save(update_fields=['targeting'])
        self.assertIsNotNone(self.decision().data['ad'])
        for field, value in [('approval', 'review'), ('status', 'paused'),
                             ('starts_at', timezone.now() + timedelta(minutes=1)),
                             ('placements', [])]:
            old = getattr(campaign, field)
            setattr(campaign, field, value)
            campaign.save(update_fields=[field])
            self.assertIsNone(self.decision().data['ad'], field)
            setattr(campaign, field, old)
            campaign.save(update_fields=[field])
        with override_settings(ADVERTISING_ALLOCATION_DELIVERY_ENABLED=False):
            self.assertIsNotNone(self.decision().data['ad'])
        self.creative.campaign = None
        self.creative.save(update_fields=['campaign'])
        with override_settings(ADVERTISING_ALLOCATION_DELIVERY_ENABLED=False):
            self.assertIsNone(self.decision().data['ad'])

    def test_operation_and_emergency_switches_override_allocation(self):
        AdvertisingOperation.objects.create(pk=1, enabled=False)
        self.assertFalse(self.client.post(BASE + 'session/', {'context': 'content', 'content': self.content.pk}, format='json').data['enabled'])
        AdvertisingOperation.objects.filter(pk=1).update(enabled=True)
        with override_settings(ADVERTISING_ENABLED=False):
            self.assertIsNone(self.decision().data['ad'])
        self.assertIsNotNone(self.decision().data['ad'])

    def test_distinct_advertisers_rotate_and_priority_does_not_starve(self):
        other = Advertiser.objects.create(name='Other advertiser')
        other_creative = Creative.objects.create(name='Other creative', advertiser=other, kind='image',
            image=self.image, approval='approved')
        DeliveryAllocation.objects.create(creative=other_creative, placement=self.placement,
            starts_at=self.allocation.starts_at, status='active')
        choices = [self.decision().data['ad']['advertiser'] for _ in range(4)]
        self.assertEqual(choices, ['Direct advertiser', 'Other advertiser', 'Direct advertiser', 'Other advertiser'])
        DeliveryAllocation.objects.filter(creative=other_creative).update(status='paused')
        second = Creative.objects.create(name='Low priority', advertiser=self.advertiser, kind='image',
            image=self.image, approval='approved')
        DeliveryAllocation.objects.create(creative=second, placement=self.placement,
            starts_at=self.allocation.starts_at, status='active', priority=1)
        self.allocation.priority = 2
        self.allocation.selection_score = 0
        self.allocation.save(update_fields=['priority', 'selection_score'])
        names = [self.decision().data['ad']['name'] for _ in range(9)]
        self.assertIn('Low priority', names)
        self.assertGreater(names.count('Direct creative'), names.count('Low priority'))

    def test_impression_attribution_duplicate_event_and_reports(self):
        token = self.session()
        served = self.decision(token).data
        record = Decision.objects.get(pk=served['decision_id'])
        Decision.objects.filter(pk=record.pk).update(created_at=timezone.now() - timedelta(seconds=3))
        body = {'session': token, 'decision_id': str(record.pk), 'kind': 'impression'}
        first = self.client.post(BASE + 'events/', body, format='json')
        self.assertEqual(first.status_code, 200, first.data)
        self.assertFalse(first.data['duplicate'])
        second = self.client.post(BASE + 'events/', body, format='json')
        self.assertEqual(second.status_code, 200, second.data)
        self.assertTrue(second.data['duplicate'])
        self.assertEqual(Event.objects.filter(decision=record, kind='impression').count(), 1)
        self.allocation.refresh_from_db()
        self.assertEqual(self.allocation.impressions, 1)
        self.assertEqual(AllocationDay.objects.get(allocation=self.allocation, day=timezone.now().date()).impressions, 1)
        self.client.force_authenticate(self.staff)
        report = self.client.get(BASE + 'reports/', {'allocation': self.allocation.pk})
        self.assertEqual(report.status_code, 200, report.data)
        self.assertEqual(report.data['by_allocation'][0]['decision__allocation_id'], self.allocation.pk)

    @override_settings(ADVERTISING_GEO_TRUSTED_PROXIES=['127.0.0.1'])
    def test_trusted_geo_target_is_bound_at_decision_and_events_still_count(self):
        campaign = Campaign.objects.create(name='Geo campaign', advertiser=self.advertiser,
            approval='approved', status='active', starts_at=timezone.now() - timedelta(hours=1),
            ends_at=timezone.now() + timedelta(hours=1), placements=['content_sponsor'],
            targeting={'regions': ['UG']})
        self.creative.campaign = campaign
        self.creative.placements = ['content_sponsor']
        self.creative.save(update_fields=['campaign', 'placements'])
        token = self.session()
        blocked = self.decision().data
        self.assertIsNone(blocked['ad'])
        selected = self.client.post(BASE + 'decision/', {'session': token, 'placement': 'content_sponsor',
            'request_id': str(uuid.uuid4())}, format='json', HTTP_X_CBM_COUNTRY='UG', REMOTE_ADDR='127.0.0.1')
        self.assertIsNotNone(selected.data['ad'], selected.data)
        decision = Decision.objects.get(pk=selected.data['decision_id'])
        Decision.objects.filter(pk=decision.pk).update(created_at=timezone.now() - timedelta(seconds=3))
        event = self.client.post(BASE + 'events/', {'session': token, 'decision_id': str(decision.pk),
            'kind': 'impression'}, format='json')
        self.assertEqual(event.status_code, 200, event.data)

    def test_direct_caps_reserve_before_impressions(self):
        self.allocation.impression_limit = 1
        self.allocation.save(update_fields=['impression_limit'])
        self.assertIsNotNone(self.decision().data['ad'])
        self.assertIsNone(self.decision().data['ad'])

    def test_allocation_caps_are_configurable_and_unlimited_does_not_remove_viewer_limit(self):
        self.allocation.daily_limit = 1
        self.allocation.impression_limit = 1
        self.allocation.save(update_fields=['daily_limit', 'impression_limit'])
        token = self.session()
        self.assertIsNotNone(self.decision(token).data['ad'])
        self.assertIsNone(self.decision(self.session()).data['ad'])
        self.allocation.daily_limit = None
        self.allocation.impression_limit = None
        self.allocation.save(update_fields=['daily_limit', 'impression_limit'])
        self.assertIsNotNone(self.decision(self.session()).data['ad'])

    def test_placement_settings_permissions_audit_and_midroll_limit(self):
        self.client.force_authenticate(self.staff)
        endpoint = BASE + 'placements/midroll/'
        response = self.client.patch(endpoint, {'enabled': True, 'breakpoints': [60, 120],
            'max_midroll_interruptions': 1, 'skippable_after': None, 'max_video_duration': 10}, format='json')
        self.assertEqual(response.status_code, 200, response.data)
        self.assertTrue(Audit.objects.filter(resource='placement', resource_id='midroll', action='updated').exists())
        self.assertEqual(self.client.patch(endpoint, {'max_midroll_interruptions': 3}, format='json').status_code, 400)
        self.assertEqual(self.client.patch(endpoint, {'skippable_after': 11}, format='json').status_code, 400)
        self.client.force_authenticate(self.viewer)
        self.assertEqual(self.client.patch(endpoint, {'enabled': False}, format='json').status_code, 403)

    @override_settings(MEDIA_STORAGE={'CLOUDFRONT_BASE_URL': 'https://cdn.example.com'})
    @patch('advertising.delivery.grant', return_value='https://cdn.example.com/protected/master.m3u8')
    def test_direct_skip_delay_non_skippable_duration_and_midroll_cues(self, grant):
        media = MediaAsset.objects.create(owner=self.staff, source_key='cbmvideo/private/phase2b1.mp4',
            reference_url='https://cdn.example.com/private/phase2b1.mp4', content_type='video/mp4',
            size_bytes=1000, purpose='protected', state='ready', renditions=[{'duration_seconds': 10}])
        media.master_key = f'protected/hls/{media.pk}/attempt/master.m3u8'
        media.save(update_fields=['master_key'])
        self.creative.kind = 'video'; self.creative.image = None; self.creative.media = media
        self.creative.save(update_fields=['kind', 'image', 'media'])
        pre = Placement.objects.get(pk='preroll')
        pre.enabled = True; pre.skippable_after = 5; pre.max_video_duration = 9
        pre.save(update_fields=['enabled', 'skippable_after', 'max_video_duration'])
        DeliveryAllocation.objects.create(creative=self.creative, placement=pre,
            starts_at=self.allocation.starts_at, status='active')
        playback = PlaybackSession.objects.create(user=self.viewer, content=self.content)
        token = self.client.post(BASE + 'session/', {'context': 'playback',
            'playback_session': str(playback.pk)}, format='json').data['session']
        def choose(placement='preroll', key='once'):
            return self.client.post(BASE + 'decision/', {'session': token, 'placement': placement,
                'break_key': key, 'request_id': str(uuid.uuid4())}, format='json')
        self.assertIsNone(choose().data['ad'])
        pre.max_video_duration = 10; pre.save(update_fields=['max_video_duration'])
        # A new playback session avoids replaying the previous no-ad decision.
        playback = PlaybackSession.objects.create(user=self.viewer, content=self.content)
        token = self.client.post(BASE + 'session/', {'context': 'playback',
            'playback_session': str(playback.pk)}, format='json').data['session']
        selected = choose().data
        self.assertEqual(selected['ad']['skippable_after'], 5)
        pre.skippable_after = None; pre.save(update_fields=['skippable_after'])
        self.assertEqual(choose().data['ad']['skippable_after'], 5, 'replay keeps the issued skip rule')
        body = {'session': token, 'decision_id': selected['decision_id']}
        self.assertEqual(self.client.post(BASE + 'events/', {**body, 'kind': 'video_start', 'position_seconds': 0}, format='json').status_code, 200)
        decision = Decision.objects.get(pk=selected['decision_id'])
        Decision.objects.filter(pk=decision.pk).update(started_at=timezone.now() - timedelta(seconds=4))
        self.assertEqual(self.client.post(BASE + 'events/', {**body, 'kind': 'skip', 'position_seconds': 5}, format='json').status_code, 400)
        Decision.objects.filter(pk=decision.pk).update(started_at=timezone.now() - timedelta(seconds=6))
        self.assertEqual(self.client.post(BASE + 'events/', {**body, 'kind': 'skip', 'position_seconds': 5}, format='json').status_code, 200)
        playback = PlaybackSession.objects.create(user=self.viewer, content=self.content)
        token = self.client.post(BASE + 'session/', {'context': 'playback',
            'playback_session': str(playback.pk)}, format='json').data['session']
        selected = choose().data
        self.assertIsNone(selected['ad']['skippable_after'])
        mid = Placement.objects.get(pk='midroll')
        mid.enabled = True; mid.breakpoints = [60, 120, 180]; mid.max_midroll_interruptions = 1
        mid.save(update_fields=['enabled', 'breakpoints', 'max_midroll_interruptions'])
        mid_allocation = DeliveryAllocation.objects.create(creative=self.creative, placement=mid,
            starts_at=self.allocation.starts_at, status='paused')
        opened = self.client.post(BASE + 'session/', {'context': 'playback',
            'playback_session': str(playback.pk)}, format='json')
        self.assertEqual(next(row for row in opened.data['placements'] if row['code'] == 'midroll')['breakpoints'], [60, 120, 180])
        self.assertIsNone(choose('midroll', '60').data['ad'])
        mid_allocation.status = 'active'; mid_allocation.save(update_fields=['status'])
        self.assertIsNotNone(choose('midroll', '120').data['ad'])
        self.assertEqual(choose('midroll', '180').data['reason'], 'midroll_limit')

    def test_report_rates_do_not_assume_abandoned_video_completes(self):
        row = rates({'video_start': 2, 'complete': 1, 'skip': 0, 'click': 1, 'impression': 2})
        self.assertEqual(row['completion_rate'], .5)
        self.assertEqual(row['skip_rate'], 0)
        self.assertEqual(row['click_through_rate'], .5)
        self.assertIsNone(rates({'video_start': 0, 'complete': 0, 'skip': 0, 'click': 0, 'impression': 0})['skip_rate'])

    def test_dashboard_eligibility_reasons_follow_live_gates(self):
        self.client.force_authenticate(self.staff)
        endpoint = BASE + f'allocations/{self.allocation.pk}/'
        self.assertTrue(self.client.get(endpoint).data['eligibility']['eligible'])
        self.assertEqual(self.client.get(BASE + 'operation/').data['strategy'], 'allocation')
        with override_settings(ADVERTISING_ALLOCATION_DELIVERY_ENABLED=False):
            self.assertIn('allocation_strategy_disabled', self.client.get(endpoint).data['eligibility']['reasons'])
            self.assertEqual(self.client.get(BASE + 'operation/').data['strategy'], 'campaign')
        self.allocation.starts_at = timezone.now() + timedelta(minutes=5)
        self.allocation.save(update_fields=['starts_at'])
        self.assertIn('scheduled', self.client.get(endpoint).data['eligibility']['reasons'])
        self.placement.enabled = False
        self.placement.save(update_fields=['enabled'])
        self.assertIn('placement_disabled', self.client.get(endpoint).data['eligibility']['reasons'])
        self.client.force_authenticate(self.viewer)
        self.assertEqual(self.client.get(endpoint).status_code, 403)

    @override_settings(MEDIA_STORAGE={'CLOUDFRONT_BASE_URL': 'https://cdn.example.com'})
    @patch('advertising.delivery.grant', return_value='https://cdn.example.com/protected/master.m3u8')
    def test_video_preroll_midroll_postroll_and_protected_media(self, grant):
        media = MediaAsset.objects.create(owner=self.staff, source_key='cbmvideo/private/phase2b.mp4',
            reference_url='https://cdn.example.com/private/phase2b.mp4', content_type='video/mp4',
            size_bytes=1000, purpose='protected', state='ready', renditions=[{'duration_seconds': 20}])
        media.master_key = f'protected/hls/{media.pk}/attempt/master.m3u8'
        media.save(update_fields=['master_key'])
        self.creative.kind = 'video'
        self.creative.image = None
        self.creative.media = media
        self.creative.save(update_fields=['kind', 'image', 'media'])
        for code in ('preroll', 'midroll', 'postroll'):
            placement = Placement.objects.get(code=code)
            placement.enabled = True
            placement.breakpoints = [60] if code == 'midroll' else []
            placement.save(update_fields=['enabled', 'breakpoints'])
            DeliveryAllocation.objects.create(creative=self.creative, placement=placement,
                starts_at=self.allocation.starts_at, status='active')
        playback = PlaybackSession.objects.create(user=self.viewer, content=self.content)
        opened = self.client.post(BASE + 'session/', {'context': 'playback',
            'playback_session': str(playback.pk)}, format='json')
        self.assertEqual(opened.status_code, 200, opened.data)
        token = opened.data['session']

        def video_decision(code, break_key='once'):
            return self.client.post(BASE + 'decision/', {'session': token, 'placement': code,
                'break_key': break_key, 'request_id': str(uuid.uuid4())}, format='json')

        pre = video_decision('preroll')
        self.assertEqual(pre.status_code, 200, pre.data)
        self.assertEqual(pre.data['ad']['source_type'], 'hls')
        self.assertNotIn(media.source_key, str(pre.data))
        self.assertEqual(video_decision('preroll').data['decision_id'], pre.data['decision_id'])
        mid = video_decision('midroll', '60')
        self.assertIsNotNone(mid.data['ad'])
        self.assertIsNone(video_decision('postroll').data['ad'], 'two video interruptions per playback session')
        self.assertGreaterEqual(grant.call_count, 2)
        DeliveryAllocation.objects.filter(creative=self.creative, placement_id='preroll').update(status='paused')
        self.assertIsNone(video_decision('preroll').data['ad'])
        media.state = 'failed'
        media.save(update_fields=['state'])
        self.assertIsNone(video_decision('midroll', '60').data['ad'])

    def test_session_reservations_limit_direct_advertiser_across_placements(self):
        from homepage.models import HomepageConfiguration
        from homepage.defaults import default_sections
        codes = ['homepage_banner', 'homepage_sponsored_section', 'homepage_featured_sponsor']
        HomepageConfiguration.objects.create(sections=default_sections() + [
            {'id': code, 'enabled': True} for code in codes], advert_insertion_enabled=True, advert_interval=3)
        for code in codes:
            placement = Placement.objects.get(code=code)
            placement.enabled = True
            placement.save(update_fields=['enabled'])
            DeliveryAllocation.objects.create(creative=self.creative, placement=placement,
                starts_at=self.allocation.starts_at, status='active')
        opened = self.client.post(BASE + 'session/', {'context': 'homepage'}, format='json')
        self.assertEqual(opened.status_code, 200, opened.data)
        token = opened.data['session']
        for code, key in [(codes[0], 'once'), (codes[1], 'once')]:
            result = self.client.post(BASE + 'decision/', {'session': token, 'placement': code,
                'break_key': key, 'request_id': str(uuid.uuid4())}, format='json')
            self.assertIsNotNone(result.data['ad'], (code, result.data))
        third = self.client.post(BASE + 'decision/', {'session': token, 'placement': codes[2],
            'break_key': 'featured-3', 'request_id': str(uuid.uuid4())}, format='json')
        self.assertIsNone(third.data['ad'])


@skipUnless(connection.vendor == 'postgresql', 'Concurrent allocation reservation requires PostgreSQL.')
@override_settings(ADVERTISING_ENABLED=True, ADVERTISING_ALLOCATION_DELIVERY_ENABLED=True)
class AllocationConcurrencyTests(TransactionTestCase):
    def test_direct_cap_cannot_be_overreserved(self):
        user = User.objects.create_user('phase2b-concurrent@example.com', 'Viewer', password='test')
        advertiser = Advertiser.objects.create(name='Concurrent client')
        image = ImageAsset.objects.create(desktop_url='https://cdn.example.com/a.webp',
            mobile_url='https://cdn.example.com/b.webp', width=640, height=120, size_bytes=100)
        creative = Creative.objects.create(name='Concurrent', advertiser=advertiser, kind='image', image=image, approval='approved')
        placement, _ = Placement.objects.update_or_create(code='content_sponsor', defaults={'enabled': True})
        allocation = DeliveryAllocation.objects.create(creative=creative, placement=placement,
            starts_at=timezone.now() - timedelta(minutes=1), status='active', impression_limit=1)
        channel = Channel.objects.create(name='Concurrent channel')
        content = Content.objects.create(title='Concurrent content', channel=channel, content_type='movie', status='approved')
        sessions = [context_session({'context': 'content', 'content': content.pk}, user) for _ in range(2)]

        def choose(session):
            try:
                return decide(session, placement, 'once', uuid.uuid4(), Response())['ad'] is not None
            finally:
                connections.close_all()

        with ThreadPoolExecutor(max_workers=2) as pool:
            results = list(pool.map(choose, sessions))
        self.assertEqual(sum(results), 1)
        self.assertEqual(Decision.objects.filter(allocation=allocation, creative__isnull=False).count(), 1)

import uuid
from datetime import timedelta

from django.test import override_settings
from django.utils import timezone
from rest_framework.test import APITestCase

from accounts.models import User
from channel.models import Channel
from content.models import Content
from .models import Advertiser, Campaign, Creative, ImageAsset, Placement, Decision, Event


@override_settings(ADVERTISING_ENABLED=True)
class AdvertisingDeliveryStageTests(APITestCase):
    def test_decision_reserves_and_one_impression_consumes_the_cap(self):
        owner = User.objects.create_superuser('stage-owner@example.com', 'Owner', password='test')
        advertiser = Advertiser.objects.create(name='Stage sponsor')
        campaign = Campaign.objects.create(name='Stage campaign', advertiser=advertiser,
            starts_at=timezone.now() - timedelta(hours=1), ends_at=timezone.now() + timedelta(days=1),
            approval='approved', status='active', placements=['content_sponsor'], impression_limit=1)
        image = ImageAsset.objects.create(owner=owner, desktop_url='https://cdn.example.com/ad.webp',
            mobile_url='https://cdn.example.com/ad-small.webp', width=1200, height=300, size_bytes=100)
        Creative.objects.create(name='Stage creative', campaign=campaign, kind='image', image=image,
            approval='approved', placements=['content_sponsor'], destination_url='https://sponsor.example.com/')
        Placement.objects.update_or_create(code='content_sponsor', defaults={'enabled': True})
        channel = Channel.objects.create(name='Stage channel')
        content = Content.objects.create(title='Stage title', content_type='movie', status='approved', channel=channel, category='movies')

        session = self.client.post('/api/advertising/session/', {'context': 'content', 'content': content.pk}, format='json')
        self.assertEqual(session.status_code, 200, session.data)
        token = session.data['session']
        request = {'session': token, 'placement': 'content_sponsor', 'request_id': str(uuid.uuid4())}
        decision = self.client.post('/api/advertising/decision/', request, format='json')
        self.assertEqual(decision.status_code, 200, decision.data)
        self.assertIsNotNone(decision.data['ad'])
        self.assertEqual(campaign.impressions, 0)
        self.assertEqual(self.client.post('/api/advertising/decision/', request, format='json').data['decision_id'], decision.data['decision_id'])
        issued = Decision.objects.get(pk=decision.data['decision_id'])
        Decision.objects.filter(pk=issued.pk).update(created_at=timezone.now() - timedelta(seconds=5))
        event = {'session': token, 'decision_id': str(issued.pk), 'kind': 'impression'}
        self.assertEqual(self.client.post('/api/advertising/events/', event, format='json').status_code, 200)
        self.assertTrue(self.client.post('/api/advertising/events/', event, format='json').data['duplicate'])
        campaign.refresh_from_db()
        self.assertEqual(campaign.impressions, 1)
        self.assertEqual(Event.objects.filter(kind='impression').count(), 1)

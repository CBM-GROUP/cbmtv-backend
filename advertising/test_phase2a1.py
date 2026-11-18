import uuid
from concurrent.futures import ThreadPoolExecutor
from unittest import skipUnless

from django.db import connection, connections
from django.test import TransactionTestCase
from rest_framework.test import APIClient, APITestCase

from accounts.models import User
from .models import Advertiser, Audit, Campaign, Creative, CreativeCreateRequest, DeliveryAllocation, ImageAsset, Placement


BASE = '/api/advertising/'


def image_for(user):
    return ImageAsset.objects.create(owner=user, desktop_url='https://cdn.example.com/ad.webp',
        mobile_url='https://cdn.example.com/ad-small.webp', width=1000, height=300, size_bytes=1000)


class AdvertisingManagementApiTests(APITestCase):
    def setUp(self):
        self.staff = User.objects.create_superuser('phase2a1@example.com', 'Staff', password='test')
        self.client.force_authenticate(self.staff)
        self.advertiser = Advertiser.objects.create(name='Alpine Foods', email='alpine@example.com')
        self.campaign = Campaign.objects.create(name='Winter launch', advertiser=self.advertiser)
        self.image = image_for(self.staff)

    def payload(self, **changes):
        return {'name': 'Winter banner', 'advertiser': self.advertiser.pk,
                'kind': 'image', 'image': str(self.image.pk), **changes}

    def test_creative_create_retry_is_scoped_and_payload_mismatch_conflicts(self):
        key = str(uuid.uuid4())
        first = self.client.post(BASE + 'creatives/', self.payload(), format='json', HTTP_IDEMPOTENCY_KEY=key)
        self.assertEqual(first.status_code, 201, first.data)
        replay = self.client.post(BASE + 'creatives/', self.payload(), format='json', HTTP_IDEMPOTENCY_KEY=key)
        self.assertEqual(replay.status_code, 201, replay.data)
        self.assertEqual(replay['Idempotent-Replay'], 'true')
        self.assertEqual(first.data, replay.data)
        self.assertEqual(Creative.objects.count(), 1)
        self.assertEqual(CreativeCreateRequest.objects.count(), 1)
        self.assertEqual(Audit.objects.filter(resource='creative', action='created').count(), 1)
        conflict = self.client.post(BASE + 'creatives/', self.payload(name='Different'), format='json', HTTP_IDEMPOTENCY_KEY=key)
        self.assertEqual(conflict.status_code, 409)
        self.assertEqual(Creative.objects.count(), 1)

        other = User.objects.create_superuser('phase2a1-other@example.com', 'Other', password='test')
        other_image = image_for(other)
        self.client.force_authenticate(other)
        separate = self.client.post(BASE + 'creatives/', self.payload(name='Other staff', image=str(other_image.pk)),
                                    format='json', HTTP_IDEMPOTENCY_KEY=key)
        self.assertEqual(separate.status_code, 201, separate.data)
        self.assertNotEqual(first.data['id'], separate.data['id'])

    def test_failed_create_does_not_reserve_key_and_old_clients_still_work(self):
        key = str(uuid.uuid4())
        invalid = self.client.post(BASE + 'creatives/', self.payload(image=None), format='json', HTTP_IDEMPOTENCY_KEY=key)
        self.assertEqual(invalid.status_code, 400)
        self.assertFalse(CreativeCreateRequest.objects.exists())
        valid = self.client.post(BASE + 'creatives/', self.payload(), format='json', HTTP_IDEMPOTENCY_KEY=key)
        self.assertEqual(valid.status_code, 201, valid.data)
        old = self.client.post(BASE + 'creatives/', self.payload(name='Legacy caller'), format='json')
        self.assertEqual(old.status_code, 201, old.data)
        self.assertEqual(Creative.objects.count(), 2)
        self.assertEqual(self.client.post(BASE + 'creatives/', self.payload(), format='json', HTTP_IDEMPOTENCY_KEY='bad').status_code, 400)

    def test_paginated_filters_and_allocation_summary(self):
        other = Advertiser.objects.create(name='Bright Labs')
        direct = Creative.objects.create(name='Direct banner', advertiser=self.advertiser, kind='image', image=self.image)
        campaign_creative = Creative.objects.create(name='Winter video', advertiser=self.advertiser,
            campaign=self.campaign, kind='image', image=self.image)
        Creative.objects.create(name='Other banner', advertiser=other, kind='image', image=self.image)
        first_placement = Placement.objects.get(code='content_sponsor')
        second_placement = Placement.objects.get(code='channel_sponsor')
        from django.utils import timezone
        DeliveryAllocation.objects.create(creative=direct, placement=first_placement, starts_at=timezone.now(), status='paused')
        DeliveryAllocation.objects.create(creative=direct, placement=second_placement, starts_at=timezone.now(), status='active')

        def names(path):
            result = self.client.get(BASE + path)
            self.assertEqual(result.status_code, 200, result.data)
            return result.data

        self.assertEqual(names('advertisers/?q=alp')['results'][0]['id'], self.advertiser.pk)
        self.assertEqual(names(f'campaigns/?advertiser={self.advertiser.pk}&q=winter')['count'], 1)
        creatives = names(f'creatives/?advertiser={self.advertiser.pk}&direct=true&q=direct&kind=image&approval=draft')
        self.assertEqual(creatives['count'], 1)
        self.assertEqual(creatives['results'][0]['allocation_count'], 2)
        self.assertEqual(creatives['results'][0]['allocation_statuses'], {'paused': 1, 'active': 1, 'completed': 0})
        self.assertEqual(names(f'creatives/?campaign={self.campaign.pk}&direct=false')['results'][0]['id'], campaign_creative.pk)
        allocations = names(f'allocations/?advertiser={self.advertiser.pk}&creative={direct.pk}&status=active&placement=channel_sponsor')
        self.assertEqual(allocations['count'], 1)
        self.assertEqual(allocations['results'][0]['creative'], direct.pk)
        self.assertEqual(self.client.get(BASE + 'creatives/?kind=unknown').status_code, 400)
        self.assertEqual(self.client.get(BASE + 'allocations/?creative=bad').status_code, 400)


@skipUnless(connection.vendor == 'postgresql', 'Concurrent idempotency verification requires PostgreSQL.')
class CreativeCreateConcurrencyTests(TransactionTestCase):
    def test_same_key_concurrent_requests_create_once(self):
        staff = User.objects.create_superuser('phase2a1-concurrent@example.com', 'Staff', password='test')
        advertiser = Advertiser.objects.create(name='Concurrent client')
        image = image_for(staff)
        payload = {'name': 'Concurrent creative', 'advertiser': advertiser.pk, 'kind': 'image', 'image': str(image.pk)}
        key = str(uuid.uuid4())

        def create(_):
            client = APIClient()
            client.force_authenticate(staff)
            try:
                return client.post(BASE + 'creatives/', payload, format='json', HTTP_IDEMPOTENCY_KEY=key)
            finally:
                connections.close_all()

        with ThreadPoolExecutor(max_workers=2) as pool:
            results = list(pool.map(create, range(2)))
        self.assertEqual([response.status_code for response in results], [201, 201])
        self.assertEqual(results[0].data['id'], results[1].data['id'])
        self.assertEqual(Creative.objects.count(), 1)
        self.assertEqual(Audit.objects.filter(resource='creative', action='created').count(), 1)

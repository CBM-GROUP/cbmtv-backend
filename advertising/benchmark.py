"""Opt-in benchmark in the isolated test database, never a production command."""
import json
import statistics
import time
import uuid
from datetime import timedelta
from django.db import connection
from django.test import TransactionTestCase, override_settings
from django.test.utils import CaptureQueriesContext
from django.utils import timezone
from rest_framework.test import APIClient
from accounts.models import User
from .models import Campaign, Creative, Placement, Decision, Event
from .delivery import context_session, receipt
from .tests import fixtures


@override_settings(ADVERTISING_ENABLED=True)
class AdvertisingBenchmark(TransactionTestCase):
    def test_local_measurements(self):
        user = User.objects.create_superuser('benchmark@example.com', 'Fixture', password='test')
        client = APIClient(); client.force_authenticate(user)
        advertiser, first, creative, content = fixtures(user)
        for index in range(19):
            campaign = Campaign.objects.create(name=f'Fixture campaign {index}', advertiser=advertiser,
                starts_at=first.starts_at, ends_at=first.ends_at, approval='approved', status='active', placements=['content_sponsor'])
            Creative.objects.create(name='Fixture banner', campaign=campaign, kind='image', image=creative.image,
                                    approval='approved', placements=['content_sponsor'])
        placement = Placement.objects.get(pk='content_sponsor')
        measurements = {}
        def measure(kind, operation):
            started = time.perf_counter()
            with CaptureQueriesContext(connection) as queries:
                result = operation()
                self.assertEqual(result.status_code, 200, result.data)
            measurements.setdefault(kind, []).append(((time.perf_counter() - started) * 1000, len(queries)))
            return result.data
        for _ in range(30):
            session = context_session({'context': 'content', 'content': content.pk}, user)
            token = receipt(session)
            result = measure('decision', lambda: client.post('/api/advertising/decision/', {'session': token,
                'placement': placement.pk, 'request_id': str(uuid.uuid4())}, format='json'))
            decision_id = result['decision_id']
            Decision.objects.filter(pk=decision_id).update(created_at=timezone.now() - timedelta(seconds=5))
            measure('ingestion', lambda: client.post('/api/advertising/events/', {'session': token,
                'decision_id': decision_id, 'kind': 'impression', 'position_seconds': 0}, format='json'))
        # Retained history increases report volume without fabricating a public
        # metric. These records exist only in Django's disposable test database.
        decisions = list(Decision.objects.all())
        Event.objects.bulk_create([Event(decision=decisions[i % len(decisions)], kind='click') for i in range(len(decisions))])
        for _ in range(10):
            measure('report', lambda: client.get('/api/advertising/reports/'))
        summary = {}
        for kind, samples in measurements.items():
            values = sorted(s[0] for s in samples)
            summary[kind] = {'median_ms': round(statistics.median(values), 2), 'p95_ms': round(values[min(len(values)-1, int(.95*len(values)))], 2),
                             'median_queries': statistics.median(s[1] for s in samples), 'samples': len(samples)}
        print('ADVERTISING_BENCHMARK ' + json.dumps({'database': connection.vendor, 'transport': 'Django in-process APIClient, forced local test authentication', 'campaigns': 20, 'decisions': 30, 'events': 60, **summary}))

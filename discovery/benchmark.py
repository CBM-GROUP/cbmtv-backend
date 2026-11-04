"""Local synthetic discovery profile. Run only with cbmtv.test_settings."""
import json
import os
import statistics
import time
import uuid
from django.core.management import call_command
from django.db import connection
from django.test.utils import CaptureQueriesContext
from rest_framework.test import APIClient
from accounts.models import User
from channel.models import Channel
from content.models import Content
from .aggregation import aggregate_batch
from .models import DiscoveryEvent


def _profile(action, runs=1):
    samples = []
    queries = []
    for _ in range(runs):
        start = time.perf_counter()
        with CaptureQueriesContext(connection) as captured:
            action()
        samples.append((time.perf_counter() - start) * 1000)
        queries.append(len(captured))
    return {'median_ms': round(statistics.median(samples), 2),
            'p95_ms': round(sorted(samples)[max(0, int(0.95 * len(samples)) - 1)], 2),
            'median_queries': statistics.median(queries)}


def run():
    if os.environ.get('DJANGO_SETTINGS_MODULE') != 'cbmtv.test_settings' or connection.vendor != 'sqlite':
        raise RuntimeError('Run only under isolated SQLite cbmtv.test_settings.')
    call_command('migrate', verbosity=0, interactive=False)
    user = User.objects.create_user('benchmark@cbmtv.test', 'Benchmark', password='test')
    channel = Channel.objects.create(name='Benchmark channel')
    content = [Content(channel=channel, title=f'Benchmark film {index}', content_type='movie',
        category='movies', format='standalone', status='approved') for index in range(40)]
    Content.objects.bulk_create(content)
    client = APIClient()
    client.force_authenticate(user)
    target = content[0]
    ingestion = _profile(lambda: client.post('/api/discovery/events/',
        {'event_id': str(uuid.uuid4()), 'type': 'detail_opened', 'content': target.pk}, format='json'), 20)
    aggregation = _profile(lambda: aggregate_batch(500))
    ranking = _profile(lambda: client.get('/api/discovery/rankings/most-watched/'), 10)
    similar = _profile(lambda: client.get(f'/api/discovery/similar/{target.pk}/'), 10)
    recommended = _profile(lambda: client.get('/api/discovery/recommended/'), 10)
    event_bytes = sum(len(json.dumps({'id': str(row.id), 'type': row.kind, 'content': row.content_id}))
        for row in DiscoveryEvent.objects.all())
    with connection.cursor() as cursor:
        cursor.execute('PRAGMA page_count')
        pages_before = cursor.fetchone()[0]
        cursor.execute('PRAGMA page_size')
        page_size = cursor.fetchone()[0]
    DiscoveryEvent.objects.bulk_create([DiscoveryEvent(kind='detail_opened', content=target, user=user)
        for _ in range(1000)])
    with connection.cursor() as cursor:
        cursor.execute('PRAGMA page_count')
        pages_after = cursor.fetchone()[0]
    print(json.dumps({'fixture': {'titles': 40, 'events': 20, 'database': 'SQLite in-memory',
        'event_payload_bytes': event_bytes, 'database_growth_bytes_for_1000_extra_events': (pages_after - pages_before) * page_size},
        'ingestion': ingestion, 'aggregation_20_events': aggregation,
        'ranking': ranking, 'similar': similar, 'cold_start_recommendation': recommended}, indent=2))

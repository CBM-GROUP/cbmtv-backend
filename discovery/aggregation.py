"""Idempotent, transactional rollup of retained source events into hourly counters."""
from collections import defaultdict
from datetime import timezone
from django.db import connection, transaction
from django.db.models import F
from django.utils import timezone as django_timezone
from .models import AggregationState, ContentHourlyMetric, DiscoveryEvent, QueryHourlyMetric


def _hour(value):
    return value.astimezone(timezone.utc).replace(minute=0, second=0, microsecond=0)


def aggregate_batch(limit=500):
    with transaction.atomic():
        if connection.vendor == 'postgresql':
            with connection.cursor() as cursor:
                cursor.execute('SELECT pg_try_advisory_xact_lock(%s)', [0x43424D445343])
                if not cursor.fetchone()[0]:
                    return 0
        events = list(DiscoveryEvent.objects.filter(aggregated_at__isnull=True).order_by('occurred_at', 'id').select_for_update(skip_locked=True)[:limit])
        submissions = DiscoveryEvent.objects.in_bulk({event.search_id for event in events
            if event.kind == DiscoveryEvent.SEARCH_SELECTED and event.search_id})
        now = django_timezone.now()
        content_counts = defaultdict(lambda: defaultdict(int))
        query_counts = defaultdict(lambda: defaultdict(int))
        for event in events:
            hour = _hour(event.occurred_at)
            if event.content_id:
                field = {
                    DiscoveryEvent.QUALIFIED_VIEW: 'qualified_views',
                    DiscoveryEvent.SEARCH_SELECTED: 'search_clicks',
                    DiscoveryEvent.DETAIL_OPENED: 'detail_opens',
                    DiscoveryEvent.PLAYBACK_STARTED: 'playback_starts',
                    DiscoveryEvent.PLAYBACK_COMPLETED: 'completions',
                }.get(event.kind)
                if field:
                    content_counts[(hour, event.content_id)][field] += 1
            if event.term:
                field = {DiscoveryEvent.SEARCH_SUBMITTED: 'submissions',
                         DiscoveryEvent.SEARCH_SELECTED: 'conversions'}.get(event.kind)
                if field:
                    query_hour = _hour(submissions[event.search_id].occurred_at) if event.kind == DiscoveryEvent.SEARCH_SELECTED and event.search_id in submissions else hour
                    query_counts[(query_hour, event.term, event.mode)][field] += 1
                if event.kind == DiscoveryEvent.SEARCH_SUBMITTED and event.result_count == 0:
                    query_counts[(hour, event.term, event.mode)]['zero_results'] += 1
            event.aggregated_at = now
        for (hour, content_id), counts in content_counts.items():
            row, _ = ContentHourlyMetric.objects.get_or_create(hour=hour, content_id=content_id)
            ContentHourlyMetric.objects.filter(pk=row.pk).update(**{field: F(field) + amount for field, amount in counts.items()})
        for (hour, term, mode), counts in query_counts.items():
            row, _ = QueryHourlyMetric.objects.get_or_create(hour=hour, term=term, mode=mode)
            QueryHourlyMetric.objects.filter(pk=row.pk).update(**{field: F(field) + amount for field, amount in counts.items()})
        if events:
            DiscoveryEvent.objects.bulk_update(events, ['aggregated_at'])
        state, _ = AggregationState.objects.get_or_create(pk=1)
        state.last_success_at = now
        if events:
            state.last_event_at = events[-1].occurred_at
            state.processed_events += len(events)
        state.save(update_fields=['last_success_at', 'last_event_at', 'processed_events'])
        return len(events)


def rebuild():
    """Reset both rollups and marks together; subsequent batches can resume safely."""
    with transaction.atomic():
        if connection.vendor == 'postgresql':
            with connection.cursor() as cursor:
                cursor.execute('SELECT pg_advisory_xact_lock(%s)', [0x43424D445343])
        ContentHourlyMetric.objects.all().delete()
        QueryHourlyMetric.objects.all().delete()
        DiscoveryEvent.objects.filter(aggregated_at__isnull=False).update(aggregated_at=None)
        AggregationState.objects.update_or_create(pk=1, defaults={'last_success_at': None, 'last_event_at': None, 'processed_events': 0})

"""Rank from hourly aggregates; publication is checked at read time."""
from datetime import timedelta
from django.db.models import Count, F, FloatField, Q, Sum, Value
from django.db.models.functions import Coalesce, Greatest
from django.utils import timezone
from content.models import Content
from content.publication import public_eligibility
from content.serializers import ContentSerializer
from .models import AggregationState, ContentHourlyMetric

PERIODS = {'24h': timedelta(hours=24), '7d': timedelta(days=7), '30d': timedelta(days=30)}


def freshness():
    state = AggregationState.objects.filter(pk=1).first()
    at = state.last_success_at if state else None
    return {'available': at is not None, 'aggregate_updated_at': at.isoformat() if at else None,
            'stale': at is None or timezone.now() - at > timedelta(hours=2)}


def content_metrics(period, kind, now=None):
    now = now or timezone.now()
    cutoff = now - PERIODS[period]
    base = ContentHourlyMetric.objects.filter(hour__gte=cutoff, hour__lte=now,
        **public_eligibility('content__'))
    if kind in ('most_watched', 'most_searched'):
        field = 'qualified_views' if kind == 'most_watched' else 'search_clicks'
        return base.values('content_id').annotate(score=Coalesce(Sum(field), 0)).filter(score__gt=0).order_by('-score', 'content_id')
    return trending_metrics(now - timedelta(days=7), now, cutoff)


def trending_metrics(start, end, recent_start):
    rows = ContentHourlyMetric.objects.filter(hour__gte=start, hour__lt=end,
        **public_eligibility('content__')).values('content_id').annotate(
        recent_views=Coalesce(Sum('qualified_views', filter=Q(hour__gte=recent_start)), 0),
        previous_views=Coalesce(Sum('qualified_views', filter=Q(hour__lt=recent_start)), 0),
        recent_clicks=Coalesce(Sum('search_clicks', filter=Q(hour__gte=recent_start)), 0),
    ).filter(Q(recent_views__gte=2) | Q(recent_views__gte=1, recent_clicks__gte=2))
    return rows.annotate(score=F('recent_views') * Value(4.0, output_field=FloatField()) +
        F('recent_clicks') + Greatest(Value(0.0, output_field=FloatField()),
        F('recent_views') * Value(3.0, output_field=FloatField()) -
        F('previous_views') * Value(0.5, output_field=FloatField()))).order_by('-score', 'content_id')


def ranked_payload(kind, period, limit, offset):
    rows = content_metrics(period, kind)
    ids = list(rows.values_list('content_id', flat=True)[offset:offset + limit])
    titles = Content.objects.filter(pk__in=ids, **public_eligibility()).prefetch_related('genres')
    by_id = {item.pk: item for item in titles}
    return {**freshness(), 'period': period, 'basis': 'qualified_views' if kind == 'most_watched' else
        'search_result_selections' if kind == 'most_searched' else 'recent_growth',
        'total': rows.count(), 'offset': offset, 'next_offset': offset + len(ids) if offset + len(ids) < rows.count() else None,
        'items': ContentSerializer([by_id[pk] for pk in ids if pk in by_id], many=True).data}

"""Public discovery, private recommendations and staff-only aggregate analytics."""
from datetime import datetime, time, timedelta, timezone
from django.db.models import Count, Q, Sum
from django.db.models.functions import Coalesce, TruncDate
from django.utils import timezone as django_timezone
from django.utils.dateparse import parse_date
from rest_framework import permissions, status
from rest_framework.exceptions import ValidationError
from rest_framework.response import Response
from rest_framework.throttling import AnonRateThrottle, UserRateThrottle
from rest_framework.views import APIView
from accounts.permissions import HasDashboardSection
from common.http import PrivateResponseMixin
from content.models import Content
from content.publication import public_eligibility
from content.serializers import ContentSerializer
from .events import ingest
from .models import AggregationState, ContentHourlyMetric, QueryHourlyMetric
from .rankings import PERIODS, freshness, ranked_payload, trending_metrics
from .recommendations import similar, recommended


class DiscoveryAnonThrottle(AnonRateThrottle):
    rate = '30/min'


class DiscoveryUserThrottle(UserRateThrottle):
    rate = '120/min'


def _bounds(request):
    try:
        limit = int(request.query_params.get('limit', 12))
        offset = int(request.query_params.get('offset', 0))
    except (TypeError, ValueError):
        raise ValidationError('Use integer limit and offset.') from None
    if not 1 <= limit <= 30 or not 0 <= offset <= 1000:
        raise ValidationError('Limit must be 1–30 and offset 0–1000.')
    return limit, offset


def _candidate_bounds(limit, offset):
    # The metadata recommender deliberately examines at most 500 candidates.
    # Expose a bounded 100-item result window rather than silently returning
    # an empty page after item 30.
    if limit + offset > 100:
        raise ValidationError('Similar and personalized results support the first 100 items.')
    return limit + offset


class DiscoveryEventView(PrivateResponseMixin, APIView):
    permission_classes = [permissions.AllowAny]
    throttle_classes = [DiscoveryAnonThrottle, DiscoveryUserThrottle]

    def post(self, request):
        result = ingest(request.data, request.user)
        return Response(result, status=status.HTTP_200_OK if result.get('duplicate') else status.HTTP_201_CREATED)


class RankingView(APIView):
    permission_classes = [permissions.AllowAny]

    def get(self, request, kind):
        if kind not in ('most-watched', 'most-searched', 'trending'):
            return Response({'detail': 'Unknown ranking.'}, status=404)
        period = request.query_params.get('period', '24h' if kind == 'trending' else '7d')
        if period not in PERIODS or (kind == 'trending' and period != '24h'):
            raise ValidationError({'period': 'Choose 24h, 7d or 30d; trending uses 24h.'})
        limit, offset = _bounds(request)
        key = kind.replace('-', '_')
        return Response(ranked_payload(key, period, limit, offset))


class SimilarView(APIView):
    permission_classes = [permissions.AllowAny]

    def get(self, request, pk):
        content = Content.objects.filter(pk=pk, **public_eligibility()).prefetch_related('genres').first()
        if not content:
            return Response({'detail': 'Content not found.'}, status=404)
        limit, offset = _bounds(request)
        items = similar(content, _candidate_bounds(limit, offset))
        return Response({'content': pk, 'basis': 'shared_metadata', 'total': len(items), 'offset': offset,
            'items': ContentSerializer(items[offset:offset + limit], many=True).data})


class RecommendedView(PrivateResponseMixin, APIView):
    permission_classes = [permissions.IsAuthenticated]

    def get(self, request):
        limit, offset = _bounds(request)
        result = recommended(request.user, _candidate_bounds(limit, offset))
        items = result.pop('items')
        return Response({**result, 'total': len(items), 'offset': offset, 'items': items[offset:offset + limit]})


class DiscoveryAnalyticsView(PrivateResponseMixin, APIView):
    permission_classes = [HasDashboardSection]
    required_section = 'analytics'

    def get(self, request):
        today = django_timezone.now().date()
        raw_start, raw_end = request.query_params.get('from'), request.query_params.get('to')
        start = parse_date(raw_start) if raw_start else today - timedelta(days=6)
        end = parse_date(raw_end) if raw_end else today
        if start is None or end is None:
            raise ValidationError({'range': 'Use YYYY-MM-DD dates.'})
        if end < start or (end - start).days > 89 or end > today:
            raise ValidationError({'range': 'Choose an inclusive range within the past 90 days.'})
        if start < today - timedelta(days=89):
            raise ValidationError({'range': 'Retained analytics cover the past 90 days.'})
        after = datetime.combine(start, time.min, tzinfo=timezone.utc)
        before = datetime.combine(end + timedelta(days=1), time.min, tzinfo=timezone.utc)
        titles = ContentHourlyMetric.objects.filter(hour__gte=after, hour__lt=before,
            **public_eligibility('content__'))
        queries = QueryHourlyMetric.objects.filter(hour__gte=after, hour__lt=before)
        sums = titles.aggregate(qualified_views=Coalesce(Sum('qualified_views'), 0),
            search_clicks=Coalesce(Sum('search_clicks'), 0), detail_opens=Coalesce(Sum('detail_opens'), 0),
            playback_starts=Coalesce(Sum('playback_starts'), 0), completions=Coalesce(Sum('completions'), 0))
        search = queries.aggregate(submissions=Coalesce(Sum('submissions'), 0),
            zero_results=Coalesce(Sum('zero_results'), 0), conversions=Coalesce(Sum('conversions'), 0))
        def title_rows(field):
            return [{'content_id': row['content_id'], 'title': row['content__title'], 'count': row['count']}
                for row in titles.values('content_id', 'content__title').annotate(count=Sum(field)).filter(count__gt=0)
                .order_by('-count', 'content_id')[:10]]
        terms = list(queries.values('term', 'mode').annotate(count=Sum('submissions'), zero=Sum('zero_results'),
            clicks=Sum('conversions')).order_by('-count', 'term')[:20])
        zero = list(queries.values('term', 'mode').annotate(count=Sum('zero_results')).filter(count__gt=0)
            .order_by('-count', 'term')[:20])
        views_time = list(titles.annotate(day=TruncDate('hour')).values('day').annotate(count=Sum('qualified_views')).order_by('day'))
        search_time = list(queries.annotate(day=TruncDate('hour')).values('day').annotate(count=Sum('submissions'),
            zero=Sum('zero_results')).order_by('day'))
        categories = list(titles.values('content__category').annotate(count=Sum('qualified_views')).order_by('-count', 'content__category'))
        channels = list(titles.values('content__channel_id', 'content__channel__name').annotate(count=Sum('qualified_views'))
            .order_by('-count', 'content__channel_id')[:20])
        recent_start = max(after, before - timedelta(days=1))
        trend_ids = list(trending_metrics(max(after, before - timedelta(days=7)), before, recent_start)
            .values_list('content_id', flat=True)[:10])
        trend_titles = dict(Content.objects.filter(pk__in=trend_ids).values_list('pk', 'title'))
        state = freshness()
        return Response({**state, 'from': start.isoformat(), 'to': end.isoformat(),
            'summary': {**sums, **search, 'conversion_rate': round(search['conversions'] / search['submissions'], 4)
                if search['submissions'] else 0},
            'most_watched': title_rows('qualified_views'), 'most_searched': title_rows('search_clicks'),
            'trending': [{'content_id': pk, 'title': trend_titles[pk]} for pk in trend_ids if pk in trend_titles],
            'popular_queries': terms, 'zero_result_queries': zero,
            'views_over_time': views_time, 'searches_over_time': search_time,
            'categories': categories, 'channels': channels})

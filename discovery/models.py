"""Bounded source events and hourly rollups for discovery, never public identities."""
import uuid
from django.conf import settings
from django.db import models


class PlaybackSession(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE)
    content = models.ForeignKey('content.Content', on_delete=models.CASCADE)
    episode = models.ForeignKey('content.Episode', null=True, on_delete=models.CASCADE)
    miniseries = models.ForeignKey('content.MiniSeries', null=True, on_delete=models.CASCADE)
    created_at = models.DateTimeField(auto_now_add=True, db_index=True)
    started_at = models.DateTimeField(null=True)
    first_position = models.FloatField(null=True)
    max_position = models.FloatField(default=0)
    qualified_at = models.DateTimeField(null=True)
    completed_at = models.DateTimeField(null=True)


class DiscoveryEvent(models.Model):
    SEARCH_SUBMITTED = 'search_submitted'
    SEARCH_SELECTED = 'search_selected'
    DETAIL_OPENED = 'detail_opened'
    PLAYBACK_STARTED = 'playback_started'
    PLAYBACK_PROGRESS = 'playback_progress'
    PLAYBACK_COMPLETED = 'playback_completed'
    QUALIFIED_VIEW = 'qualified_view'
    RECOMMENDATION_SELECTED = 'recommendation_selected'
    KINDS = tuple((value, value) for value in (
        SEARCH_SUBMITTED, SEARCH_SELECTED, DETAIL_OPENED, PLAYBACK_STARTED,
        PLAYBACK_PROGRESS, PLAYBACK_COMPLETED, QUALIFIED_VIEW, RECOMMENDATION_SELECTED,
    ))
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    kind = models.CharField(max_length=32, choices=KINDS)
    user = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, on_delete=models.CASCADE)
    content = models.ForeignKey('content.Content', null=True, on_delete=models.CASCADE)
    session = models.ForeignKey(PlaybackSession, null=True, on_delete=models.CASCADE)
    search_id = models.UUIDField(null=True)
    term = models.CharField(max_length=120, blank=True)
    mode = models.CharField(max_length=16, blank=True)
    result_count = models.PositiveIntegerField(null=True)
    position_seconds = models.PositiveIntegerField(null=True)
    duration_seconds = models.PositiveIntegerField(null=True)
    source = models.CharField(max_length=24, blank=True)
    qualified_day = models.DateField(null=True)
    occurred_at = models.DateTimeField(auto_now_add=True, db_index=True)
    aggregated_at = models.DateTimeField(null=True, db_index=True)

    class Meta:
        indexes = [
            models.Index(fields=['aggregated_at', 'occurred_at'], name='discovery_event_pending'),
            models.Index(fields=['kind', 'occurred_at'], name='discovery_event_kind_time'),
            models.Index(fields=['search_id', 'kind'], name='discovery_event_search'),
        ]
        constraints = [
            models.UniqueConstraint(fields=['search_id'], condition=models.Q(kind='search_selected'), name='discovery_one_click_per_search'),
            models.UniqueConstraint(fields=['user', 'content', 'qualified_day'], condition=models.Q(kind='qualified_view'), name='discovery_one_view_per_day'),
        ]


class ContentHourlyMetric(models.Model):
    hour = models.DateTimeField()
    content = models.ForeignKey('content.Content', on_delete=models.CASCADE)
    qualified_views = models.PositiveIntegerField(default=0)
    search_clicks = models.PositiveIntegerField(default=0)
    detail_opens = models.PositiveIntegerField(default=0)
    playback_starts = models.PositiveIntegerField(default=0)
    completions = models.PositiveIntegerField(default=0)

    class Meta:
        constraints = [models.UniqueConstraint(fields=['hour', 'content'], name='discovery_content_hour')]
        indexes = [models.Index(fields=['hour', 'content'], name='discovery_content_period')]


class QueryHourlyMetric(models.Model):
    hour = models.DateTimeField()
    term = models.CharField(max_length=120)
    mode = models.CharField(max_length=16)
    submissions = models.PositiveIntegerField(default=0)
    zero_results = models.PositiveIntegerField(default=0)
    conversions = models.PositiveIntegerField(default=0)

    class Meta:
        constraints = [models.UniqueConstraint(fields=['hour', 'term', 'mode'], name='discovery_query_hour')]
        indexes = [models.Index(fields=['hour', 'term'], name='discovery_query_period')]


class AggregationState(models.Model):
    """Singleton heartbeat so zero activity differs from an unrun pipeline."""
    id = models.PositiveSmallIntegerField(primary_key=True, default=1, editable=False)
    last_success_at = models.DateTimeField(null=True)
    last_event_at = models.DateTimeField(null=True)
    processed_events = models.PositiveBigIntegerField(default=0)

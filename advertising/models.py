import uuid
from django.conf import settings
from django.db import models
from django.db.models import Q, F
from .constants import APPROVALS, EVENTS, PLACEMENTS


class Timestamped(models.Model):
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        abstract = True


class Advertiser(Timestamped):
    name = models.CharField(max_length=200)
    contact_name = models.CharField(max_length=200, blank=True)
    email = models.EmailField(blank=True)
    phone = models.CharField(max_length=80, blank=True)
    billing_reference = models.CharField(max_length=200, blank=True)
    active = models.BooleanField(default=True)


class Placement(Timestamped):
    code = models.CharField(primary_key=True, max_length=48, choices=tuple(PLACEMENTS.items()))
    enabled = models.BooleanField(default=False)
    # Mid-rolls are elapsed main-content seconds, shared by eligible titles.
    breakpoints = models.JSONField(default=list, blank=True)
    failure_policy = models.CharField(max_length=16, default='continue', choices=[('continue', 'Continue content')])


class AdvertisingSettings(models.Model):
    """Administrator activation; the environment kill switch still takes precedence."""
    id = models.PositiveSmallIntegerField(primary_key=True, default=1, editable=False)
    enabled = models.BooleanField(default=False)
    homepage_enabled = models.BooleanField(default=False)
    video_enabled = models.BooleanField(default=False)
    updated_at = models.DateTimeField(auto_now=True)


class Campaign(Timestamped):
    name = models.CharField(max_length=200)
    advertiser = models.ForeignKey(Advertiser, on_delete=models.PROTECT, related_name='campaigns')
    description = models.TextField(blank=True)
    starts_at = models.DateTimeField()
    ends_at = models.DateTimeField()
    approval = models.CharField(max_length=16, choices=APPROVALS, default='draft')
    status = models.CharField(max_length=16, default='paused', choices=[(s, s) for s in ('paused', 'active', 'completed')])
    priority = models.PositiveSmallIntegerField(default=1)
    placements = models.JSONField(default=list)
    targeting = models.JSONField(default=dict, blank=True)
    impression_limit = models.PositiveBigIntegerField(null=True, blank=True)
    daily_limit = models.PositiveIntegerField(null=True, blank=True)
    session_limit = models.PositiveSmallIntegerField(default=3)
    skippable_after = models.PositiveSmallIntegerField(null=True, blank=True)
    impressions = models.PositiveBigIntegerField(default=0, editable=False)
    last_served_at = models.DateTimeField(null=True, editable=False)

    class Meta:
        indexes = [models.Index(fields=['approval', 'status', 'starts_at', 'ends_at'], name='ad_campaign_eligible')]
        constraints = [models.CheckConstraint(condition=Q(ends_at__gt=F('starts_at')), name='ad_campaign_schedule')]


class ImageAsset(Timestamped):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    owner = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True)
    desktop_url = models.URLField(max_length=1000)
    mobile_url = models.URLField(max_length=1000)
    width = models.PositiveIntegerField()
    height = models.PositiveIntegerField()
    size_bytes = models.PositiveIntegerField()


class Creative(Timestamped):
    name = models.CharField(max_length=200)
    campaign = models.ForeignKey(Campaign, on_delete=models.PROTECT, related_name='creatives')
    kind = models.CharField(max_length=10, choices=[('image', 'Image'), ('video', 'Video')])
    placements = models.JSONField(default=list)
    approval = models.CharField(max_length=16, choices=APPROVALS, default='draft')
    destination_url = models.URLField(max_length=1000, blank=True)
    cta = models.CharField(max_length=60, blank=True)
    image = models.ForeignKey(ImageAsset, null=True, blank=True, on_delete=models.PROTECT)
    media = models.ForeignKey('content.MediaAsset', null=True, blank=True, on_delete=models.PROTECT)
    duration_seconds = models.PositiveSmallIntegerField(null=True, blank=True)
    promoted_content = models.ForeignKey('content.Content', null=True, blank=True, on_delete=models.SET_NULL)
    promoted_channel = models.ForeignKey('channel.Channel', null=True, blank=True, on_delete=models.SET_NULL)
    promoted_contents = models.JSONField(default=list, blank=True)


class AdSession(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    user = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, on_delete=models.CASCADE)
    playback = models.OneToOneField('discovery.PlaybackSession', null=True, on_delete=models.CASCADE)
    context = models.CharField(max_length=16, choices=[(s, s) for s in ('homepage', 'channel', 'content', 'playback')])
    content = models.ForeignKey('content.Content', null=True, on_delete=models.CASCADE)
    channel = models.ForeignKey('channel.Channel', null=True, on_delete=models.CASCADE)
    created_at = models.DateTimeField(auto_now_add=True)
    expires_at = models.DateTimeField(db_index=True)


class Decision(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    session = models.ForeignKey(AdSession, on_delete=models.CASCADE)
    placement = models.ForeignKey(Placement, on_delete=models.PROTECT)
    break_key = models.CharField(max_length=32)
    campaign = models.ForeignKey(Campaign, null=True, on_delete=models.PROTECT)
    creative = models.ForeignKey(Creative, null=True, on_delete=models.PROTECT)
    reason = models.CharField(max_length=32, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    expires_at = models.DateTimeField(db_index=True)
    impression_at = models.DateTimeField(null=True)
    started_at = models.DateTimeField(null=True)
    terminal_at = models.DateTimeField(null=True)

    class Meta:
        constraints = [models.UniqueConstraint(fields=['session', 'placement', 'break_key'], name='ad_break_once')]
        indexes = [models.Index(fields=['campaign', 'impression_at', 'expires_at'], name='ad_reservations')]


class CampaignDay(models.Model):
    campaign = models.ForeignKey(Campaign, on_delete=models.CASCADE)
    day = models.DateField()
    impressions = models.PositiveBigIntegerField(default=0)

    class Meta:
        constraints = [models.UniqueConstraint(fields=['campaign', 'day'], name='ad_campaign_day')]


class AdRequest(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    decision = models.ForeignKey(Decision, on_delete=models.CASCADE)
    created_at = models.DateTimeField(auto_now_add=True, db_index=True)


class Event(models.Model):
    decision = models.ForeignKey(Decision, on_delete=models.CASCADE, related_name='events')
    kind = models.CharField(max_length=24, choices=tuple((s, s) for s in EVENTS))
    position_seconds = models.PositiveIntegerField(default=0)
    created_at = models.DateTimeField(auto_now_add=True, db_index=True)

    class Meta:
        constraints = [models.UniqueConstraint(fields=['decision', 'kind'], name='ad_event_once')]
        indexes = [models.Index(fields=['kind', 'created_at'], name='ad_report_events')]


class Audit(models.Model):
    actor = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, on_delete=models.SET_NULL)
    resource = models.CharField(max_length=32)
    resource_id = models.CharField(max_length=48)
    action = models.CharField(max_length=32)
    created_at = models.DateTimeField(auto_now_add=True, db_index=True)

from django.db import models, transaction
from django.utils import timezone
from channel.models import Channel
from .taxonomy import CATEGORY_CHOICES, FORMAT_CHOICES
import uuid
from django.conf import settings


class MediaAsset(models.Model):
    """Durable source/upload and leased transcoding job; never a public URL grant."""
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    owner = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT)
    source_key = models.CharField(max_length=512, unique=True)
    reference_url = models.URLField(max_length=1000, unique=True)
    content_type = models.CharField(max_length=100)
    purpose = models.CharField(max_length=16, default='protected')
    size_bytes = models.PositiveBigIntegerField()
    part_size = models.PositiveBigIntegerField(default=8388608)
    upload_id = models.TextField(blank=True)
    state = models.CharField(max_length=16, default='uploading', choices=[(s, s) for s in (
        'uploading', 'uploaded', 'queued', 'processing', 'ready', 'failed', 'canceled',
    )], db_index=True)
    master_key = models.CharField(max_length=512, blank=True)
    renditions = models.JSONField(default=list)
    error = models.CharField(max_length=240, blank=True)
    attempts = models.PositiveIntegerField(default=0)
    lease_id = models.UUIDField(null=True, editable=False)
    lease_until = models.DateTimeField(null=True)
    next_attempt_at = models.DateTimeField(null=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)


class Genre(models.Model):
    name = models.CharField(max_length=200)
    name_key = models.CharField(max_length=400, unique=True, editable=False)
    description = models.TextField(blank=True)
    is_active = models.BooleanField(default=True)
    parent = models.ForeignKey('self', null=True, blank=True, on_delete=models.PROTECT, related_name='children')
    for_movies = models.BooleanField(default=False)
    for_tv_shows = models.BooleanField(default=False)
    for_documentaries = models.BooleanField(default=False)
    for_animations = models.BooleanField(default=False)

    @transaction.atomic
    def save(self, *args, **kwargs):
        self.name = self.name.strip()
        self.name_key = self.name.casefold()
        if kwargs.get('update_fields') is not None and 'name' in kwargs['update_fields']:
            kwargs['update_fields'] = set(kwargs['update_fields']) | {'name_key'}
        return super().save(*args, **kwargs)

    def __str__(self):
        return self.name

# Create your models here.
class Content(models.Model):
    CONTENT_TYPES = [
        ('movie', 'Movie'),
        ('music', 'Music'),
        ('series', 'Series'),
        ('miniseries', 'Miniseries'),
        ('original', 'Original'),
        ('documentary', 'Documentary'),
        ('animations', 'Animations'),
        ('tv_show', 'TV Show'),
    ]
    STATUS_TYPES = [
        ('preview', 'Preview'),
        ('approved', 'Approved'),
        ('rejected', 'Rejected'),
        ('comingsoon', 'Comingsoon')
    ]

    title = models.CharField(max_length=200)
    description = models.TextField(blank=True)
    content_type = models.CharField(max_length=20, choices=CONTENT_TYPES)
    category = models.CharField(max_length=20, choices=CATEGORY_CHOICES, null=True, blank=True, db_index=True)
    format = models.CharField(max_length=20, choices=FORMAT_CHOICES, null=True, blank=True, db_index=True)
    genres = models.ManyToManyField(Genre, blank=True, related_name='titles')
    trailer_link = models.URLField(blank=True, null=True)
    streaming_link = models.URLField(blank=True, null=True) #only for non-episodic content
    thumbnail = models.URLField(blank=True, null=True)
    size = models.CharField(max_length=20, blank=True, null=True)
    duration = models.DurationField(blank=True, null=True)
    director = models.CharField(max_length=200, blank=True, null=True)
    cast = models.JSONField(default=list, blank=True)
    writer = models.CharField(max_length=200, blank=True, null=True)
    genre = models.CharField(max_length=100, blank=True, null=True)
    country = models.CharField(max_length=100, blank=True, null=True)
    status = models.CharField(max_length=100, blank=True, null=True, choices=STATUS_TYPES)
    channel = models.ForeignKey(Channel, on_delete=models.CASCADE, related_name="content")
    # Editorial flags, set by an admin. Clients use them for the hero and the
    # trending rail instead of guessing from recency.
    is_featured = models.BooleanField(default=False, db_index=True)
    is_trending = models.BooleanField(default=False, db_index=True)

    created_at = models.DateTimeField(auto_now_add=True)
    first_published_at = models.DateTimeField(null=True, blank=True, editable=False, db_index=True)
    publication_history_unknown = models.BooleanField(default=False, editable=False)

    @transaction.atomic
    def save(self, *args, **kwargs):
        # Unknown legacy history stays unknown, including after republishing.
        # Protect every writer, including admin/scripts outside the REST API.
        fields = kwargs.get('update_fields')
        channel_id = self.channel_id
        if not self._state.adding and fields is not None and not {'channel', 'channel_id'} & set(fields):
            channel_id = type(self).objects.values_list('channel_id', flat=True).get(pk=self.pk)
        channel = Channel.objects.select_for_update().get(pk=channel_id)
        previous = None if self._state.adding else type(self).objects.select_for_update().filter(pk=self.pk).values(
            'status', 'first_published_at', 'publication_history_unknown',
        ).first()
        self.first_published_at = previous['first_published_at'] if previous else None
        self.publication_history_unknown = previous['publication_history_unknown'] if previous else False
        effective_status = self.status if fields is None or 'status' in fields or previous is None else previous['status']
        if channel.is_active and not self.publication_history_unknown and effective_status == 'approved' and not self.first_published_at:
            self.first_published_at = timezone.now()
            if fields is not None:
                kwargs['update_fields'] = set(fields) | {'first_published_at'}
        return super().save(*args, **kwargs)

    def __str__(self):
        return self.title

class Season(models.Model):
    content = models.ForeignKey(Content, on_delete=models.CASCADE, related_name="seasons")
    title = models.CharField(max_length=200)
    season_number = models.PositiveIntegerField()
    trailer_link = models.URLField(blank=True, null=True)
    description=models.TextField(blank=True)
    thumbnail = models.URLField(blank=True, null=True)
    

    def __str__(self):
        return f"{self.content.title} - Season {self.season_number}"

class Episode(models.Model):
    season = models.ForeignKey(Season, on_delete=models.CASCADE, related_name="episodes")
    title = models.CharField(max_length=200)
    episode_number = models.PositiveIntegerField()
    streaming_link = models.URLField(blank=True, null=True)
    duration = models.DurationField(blank=True, null=True)
    thumbnail = models.URLField(blank=True, null=True)

    def __str__(self):
        return f"{self.season.content.title} - Season {self.season.season_number} - Episode {self.episode_number}"
    

class contentadverts(models.Model):
    ADVERT_TYPES = [
        ('hello', 'Hello'),
        ('stream', 'Stream'),
        ('middle', 'Middle'),
        ('end', 'End'),
    ]
    advert_type = models.CharField(max_length=20, choices=ADVERT_TYPES)
    advert_name = models.CharField(max_length=200, blank=True, null=True)
    advert_description = models.TextField(blank=True, null=True)
    advert_link = models.URLField(blank=True, null=True)
    stream_link = models.URLField(blank=True, null=True)
    advert_thumbnail = models.URLField(blank=True, null=True)
    show_in_hero = models.BooleanField(default=True)
    hero_order = models.PositiveIntegerField(default=0)

    def __str__(self):
        return f"{self.advert_type}"


class HeroSettings(models.Model):
    image_duration_seconds = models.PositiveSmallIntegerField(default=5)
    display_mode = models.CharField(max_length=20, default='thumbnail_trailer', choices=[
        ('thumbnails_only', 'Thumbnails only'), ('thumbnail_trailer', 'Thumbnail + trailer'),
    ])
    trailer_preview_seconds = models.PositiveSmallIntegerField(default=20)


class MiniSeries(models.Model):
    content = models.ForeignKey(Content, on_delete=models.CASCADE, related_name="miniseries")
    title = models.CharField(max_length=200)
    miniseries_no = models.PositiveIntegerField()
    streaming_link = models.URLField(blank=True, null=True)
    duration = models.DurationField(blank=True, null=True)
    thumbnail = models.URLField(blank=True, null=True)

    def __str__(self):
        return f"{self.content.title} - Miniseries {self.miniseries_no}: {self.title}"

from django.db import models
from channel.models import Channel
from .taxonomy import CATEGORY_CHOICES, FORMAT_CHOICES


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
    image_duration_seconds = models.PositiveSmallIntegerField(default=20)


class MiniSeries(models.Model):
    content = models.ForeignKey(Content, on_delete=models.CASCADE, related_name="miniseries")
    title = models.CharField(max_length=200)
    miniseries_no = models.PositiveIntegerField()
    streaming_link = models.URLField(blank=True, null=True)
    duration = models.DurationField(blank=True, null=True)
    thumbnail = models.URLField(blank=True, null=True)

    def __str__(self):
        return f"{self.content.title} - Miniseries {self.miniseries_no}: {self.title}"

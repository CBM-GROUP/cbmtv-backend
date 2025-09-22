from django.db import models
from channel.models import Channel

# Create your models here.
class Content(models.Model):
    CONTENT_TYPES = [
        ('movie', 'Movie'),
        ('music', 'Music'),
        ('series', 'Series'),
        ('original', 'Original'),
        ('documentary', 'Documentary'),
        ('animations', 'Animations'),
    ]

    title = models.CharField(max_length=200)
    description = models.TextField(blank=True)
    content_type = models.CharField(max_length=20, choices=CONTENT_TYPES)
    trailer_link = models.URLField(blank=True, null=True)
    streaming_link = models.URLField(blank=True, null=True) #only for non-episodic content
    thumbnail = models.URLField(blank=True, null=True)
    size = models.CharField(max_length=20, blank=True, null=True)
    duration = models.DurationField(blank=True, null=True)
    director = models.CharField(max_length=200, blank=True, null=True)
    writer = models.CharField(max_length=200, blank=True, null=True)
    genre = models.CharField(max_length=100, blank=True, null=True)

    channel = models.ForeignKey(Channel, on_delete=models.CASCADE, related_name="content")

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

    def __str__(self):
        return f"{self.advert_type}"


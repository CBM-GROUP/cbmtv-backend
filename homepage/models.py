from django.db import models
from django.db.models import F, Q
from .defaults import default_sections


class HomepageConfiguration(models.Model):
    # One configuration, enforced in the database as well as the API.
    id = models.PositiveSmallIntegerField(primary_key=True, default=1, editable=False)
    sections = models.JSONField(default=default_sections)
    release_duration = models.PositiveIntegerField(default=30)
    release_unit = models.CharField(max_length=6, default='days', choices=[
        (unit, unit) for unit in ('hours', 'days', 'months', 'years')
    ])

    class Meta:
        constraints = [models.CheckConstraint(condition=Q(id=1), name='homepage_singleton')]


class FeaturedTitle(models.Model):
    content = models.OneToOneField('content.Content', on_delete=models.CASCADE)
    position = models.PositiveIntegerField()
    start_at = models.DateTimeField(null=True, blank=True)
    end_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ('position', 'pk')
        constraints = [models.CheckConstraint(
            condition=Q(start_at__isnull=True) | Q(end_at__isnull=True) | Q(end_at__gt=F('start_at')),
            name='homepage_valid_title_schedule',
        )]


class FeaturedChannel(models.Model):
    channel = models.OneToOneField('channel.Channel', on_delete=models.CASCADE)
    position = models.PositiveIntegerField()

    class Meta:
        ordering = ('position', 'pk')

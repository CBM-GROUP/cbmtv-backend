"""
Per-user library state: what a viewer saved, and where they stopped watching.

Both are keyed on (user, content). A series or miniseries has one progress row,
not one per episode -- it records the episode the viewer was last on, which is
what "Continue watching" resumes.
"""
from django.conf import settings
from django.db import models

from content.models import Content, Episode, MiniSeries


class SavedItem(models.Model):
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="saved_items")
    content = models.ForeignKey(Content, on_delete=models.CASCADE, related_name="saved_by")
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=['user', 'content'], name='unique_saved_item'),
        ]
        ordering = ['-created_at', '-id']

    def __str__(self):
        return f"{self.user} saved {self.content}"


class WatchProgress(models.Model):
    # A viewer who has reached this share of the runtime has finished it; the
    # credits are not worth resuming.
    COMPLETION_THRESHOLD = 0.95

    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="watch_progress")
    content = models.ForeignKey(Content, on_delete=models.CASCADE, related_name="watch_progress")
    episode = models.ForeignKey(Episode, on_delete=models.CASCADE, null=True, blank=True, related_name="watch_progress")
    miniseries = models.ForeignKey(MiniSeries, on_delete=models.CASCADE, null=True, blank=True, related_name="watch_progress")
    position_seconds = models.PositiveIntegerField()
    duration_seconds = models.PositiveIntegerField(default=0)
    completed = models.BooleanField(default=False)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=['user', 'content'], name='unique_watch_progress'),
        ]
        ordering = ['-updated_at', '-id']
        verbose_name_plural = 'watch progress'

    def __str__(self):
        return f"{self.user} at {self.position_seconds}s of {self.content}"

    @classmethod
    def is_complete(cls, position_seconds, duration_seconds):
        return duration_seconds > 0 and position_seconds >= cls.COMPLETION_THRESHOLD * duration_seconds

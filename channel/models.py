from django.db import models, transaction
from django.utils import timezone

# Create your models here.
class Channel(models.Model):
    name = models.CharField(max_length=100, unique=True)
    description = models.TextField(blank=True)
    logo_url = models.URLField(null=True, blank=True)
    cover_image_url = models.URLField(null=True, blank=True)
    is_active = models.BooleanField(default=True)

    @transaction.atomic
    def save(self, *args, **kwargs):
        previous = None if self._state.adding else type(self).objects.select_for_update().filter(pk=self.pk).values('is_active').first()
        result = super().save(*args, **kwargs)
        fields = kwargs.get('update_fields')
        if previous and not previous['is_active'] and self.is_active and (fields is None or 'is_active' in fields):
            # Activation can be a title's first public appearance. Preserve
            # known dates and every legacy record with unknown history.
            self.content.filter(status='approved', first_published_at__isnull=True,
                                publication_history_unknown=False).update(first_published_at=timezone.now())
        return result

    def __str__(self):
        return self.name

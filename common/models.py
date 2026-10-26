from django.conf import settings
from django.db import models


class CreationOperation(models.Model):
    """Durable deduplication; never expire keys while their actor can retry."""
    actor = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE)
    key = models.UUIDField()
    endpoint = models.CharField(max_length=160)
    payload_digest = models.CharField(max_length=64)
    record_id = models.PositiveBigIntegerField(null=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        constraints = [models.UniqueConstraint(fields=['actor', 'key'], name='creation_actor_key_unique')]

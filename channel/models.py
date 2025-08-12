from django.db import models

# Create your models here.
class Channel(models.Model):
    name = models.CharField(max_length=100, unique=True)
    cover_image_url = models.URLField(null=True, blank=True)

    def __str__(self):
        return self.name
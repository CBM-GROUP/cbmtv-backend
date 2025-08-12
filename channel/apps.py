from django.apps import AppConfig
from django.db.models.signals import post_migrate


class ChannelsConfig(AppConfig):
    default_auto_field = 'django.db.models.BigAutoField'
    name = 'channel'

    def ready(self):
        from .signals import create_default_channels
        post_migrate.connect(create_default_channels, sender=self)

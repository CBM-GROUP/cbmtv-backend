from .models import Channel

def create_default_channels(sender, **kwargs):
    default_channels = [
    {"name": "CBM Movies"},
    {"name": "CBM Music"},
    {"name": "CBM Documentaries"},
    {"name": "CBM Originals"},
    {"name": "CBM Series"},
    ]
    for channel in default_channels:
        Channel.objects.get_or_create(name=channel["name"])

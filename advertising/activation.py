from django.conf import settings

from .constants import HOMEPAGE_PLACEMENTS, VIDEO_PLACEMENTS
from .models import AdvertisingSettings


def activation_state():
    row = AdvertisingSettings.objects.filter(pk=1).first()
    return {
        'environment_enabled': settings.ADVERTISING_ENABLED,
        'enabled': bool(row and row.enabled),
        'homepage_enabled': bool(row and row.homepage_enabled),
        'video_enabled': bool(row and row.video_enabled),
    }


def advertising_active(placement=None):
    # The deployment kill switch must keep legacy reads independent of this
    # new table while migrations and application instances roll forward.
    if not settings.ADVERTISING_ENABLED:
        return False
    state = activation_state()
    if not (state['environment_enabled'] and state['enabled']):
        return False
    if placement in HOMEPAGE_PLACEMENTS:
        return state['homepage_enabled']
    if placement in VIDEO_PLACEMENTS:
        return state['video_enabled']
    return True

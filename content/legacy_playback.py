"""Temporary compatibility for the already-public production video catalogue."""
from urllib.parse import urlsplit

from django.conf import settings


def public_legacy_video(reference):
    if not reference:
        return False
    try:
        actual = urlsplit(reference)
    except ValueError:
        return False
    if (actual.scheme != 'https' or not actual.hostname or actual.username or actual.password
            or actual.query or actual.fragment
            or not actual.path.startswith('/cbmvideo/')
            or any(part in actual.path for part in ('%', '\\', '..', '//'))
            or actual.path.startswith(('/cbmvideo/private/', '/cbmvideo/protected/'))
            or not actual.path.lower().endswith(('.mp4', '.m4v', '.webm', '.mov'))):
        return False
    return any(actual.netloc == urlsplit(origin).netloc
               and urlsplit(origin).scheme == 'https'
               and urlsplit(origin).path in ('', '/')
               for origin in settings.PLAYBACK_LEGACY_PUBLIC_ORIGINS)

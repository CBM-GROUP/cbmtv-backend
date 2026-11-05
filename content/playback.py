"""Expiring authorization for the complete HLS tree, separate from catalogue reads."""
import base64
import json
from datetime import datetime, timedelta, timezone
from urllib.parse import urlsplit
from pathlib import Path
import uuid
from django.utils import timezone as django_timezone
from django.conf import settings
from django.shortcuts import get_object_or_404
from rest_framework.views import APIView
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.exceptions import APIException, ValidationError
from common.http import PrivateResponseMixin
from common.media_storage import build_delivery_url
from .models import Content, Episode, MiniSeries, MediaAsset
from .legacy_playback import public_legacy_video
from .publication import public_eligibility
from discovery.models import PlaybackSession


class PlaybackUnavailable(APIException):
    status_code = 503
    default_detail = 'Protected delivery is not configured. Please try again later.'


def _signer():
    # The feature flag is an operations gate, not a substitute for CDN policy.
    config = settings.PLAYBACK_DELIVERY
    if not config.get('ENABLED') or not config.get('KEY_PAIR_ID') or not config.get('PRIVATE_KEY_FILE'):
        raise PlaybackUnavailable()
    try:
        from cryptography.hazmat.primitives import serialization, hashes
        from cryptography.hazmat.primitives.asymmetric import padding
        key = serialization.load_pem_private_key(Path(config['PRIVATE_KEY_FILE']).read_bytes(), password=None)
        def sign(message):
            return key.sign(message, padding.PKCS1v15(), hashes.SHA1())
        return sign
    except Exception as exc:
        raise PlaybackUnavailable() from exc


def _encoded(value):
    return base64.b64encode(value).decode().replace('+', '-').replace('=', '_').replace('/', '~')


def grant(response, resource, expires, hls=False):
    from botocore.signers import CloudFrontSigner
    config = settings.PLAYBACK_DELIVERY
    sign = _signer()
    signer = CloudFrontSigner(config['KEY_PAIR_ID'], sign)
    if not hls:
        return signer.generate_presigned_url(resource, date_less_than=expires)
    # A master URL signature alone would leave child requests unauthorized.
    domain = config.get('COOKIE_DOMAIN', '').lstrip('.')
    cdn_host = urlsplit(resource).hostname or ''
    api_host = urlsplit(config.get('API_ORIGIN', '')).hostname or ''
    if not domain or any(host != domain and not host.endswith('.' + domain) for host in (cdn_host, api_host)):
        raise PlaybackUnavailable()
    policy = json.dumps({'Statement': [{'Resource': resource.rsplit('/', 1)[0] + '/*',
        'Condition': {'DateLessThan': {'AWS:EpochTime': int(expires.timestamp())}}}]}, separators=(',', ':')).encode()
    values = {'CloudFront-Policy': _encoded(policy), 'CloudFront-Signature': _encoded(sign(policy)),
              'CloudFront-Key-Pair-Id': config['KEY_PAIR_ID']}
    for name, value in values.items():
        response.set_cookie(name, value, max_age=max(1, int((expires - datetime.now(timezone.utc)).total_seconds())),
                            domain=domain, secure=True, httponly=True, samesite='None', path=urlsplit(resource).path.rsplit('/', 1)[0] + '/')
    return resource


def selected_media(content, episode=None, miniseries=None):
    if episode and miniseries:
        raise ValidationError('Choose an episode or part, not both.')
    if episode:
        return get_object_or_404(Episode, pk=episode, season__content=content)
    if miniseries:
        return get_object_or_404(MiniSeries, pk=miniseries, content=content)
    if content.format == 'series' or content.content_type in ('series', 'miniseries'):
        raise ValidationError('Select an episode or part.')
    return content


def playback_session(request, content, selection):
    if request.data.get('session_id'):
        try:
            session_pk = uuid.UUID(str(request.data['session_id']))
        except (TypeError, ValueError, AttributeError):
            raise ValidationError({'session_id': 'Use a valid playback session.'}) from None
        session = PlaybackSession.objects.filter(pk=session_pk, user=request.user, content=content,
            episode_id=selection.get('episode'), miniseries_id=selection.get('miniseries'),
            created_at__gte=django_timezone.now() - timedelta(hours=24)).first()
        if not session:
            raise ValidationError({'session_id': 'Playback session is unavailable.'})
        return session
    return PlaybackSession.objects.create(user=request.user, content=content,
        episode_id=selection.get('episode'), miniseries_id=selection.get('miniseries'))


class PlaybackView(PrivateResponseMixin, APIView):
    permission_classes = [IsAuthenticated]

    def finalize_response(self, request, response, *args, **kwargs):
        if isinstance(getattr(response, 'data', None), dict) and response.data.get('state') == 'ready':
            response.data['advertising_enabled'] = settings.ADVERTISING_ENABLED
        return super().finalize_response(request, response, *args, **kwargs)

    def post(self, request, pk):
        content = get_object_or_404(Content, pk=pk, **public_eligibility())
        selection = {}
        for key in ('episode', 'miniseries'):
            value = request.data.get(key)
            if value is not None:
                raw = str(value)
                if isinstance(value, bool) or not raw.isascii() or not raw.isdigit() or len(raw) > 18 or int(raw) < 1:
                    raise ValidationError({key: 'Select a valid identifier.'})
                selection[key] = int(value)
        media = selected_media(content, **selection)
        reference = media.streaming_link
        if not reference:
            return Response({'state': 'unavailable', 'detail': 'No playable media is available.'}, status=409)
        asset = MediaAsset.objects.filter(reference_url=reference).first()
        if asset and (asset.state != 'ready' or asset.purpose != 'protected'):
            return Response({'state': asset.state, 'detail': 'This video is not ready for playback.'}, status=409)
        if not asset and public_legacy_video(reference):
            # This exact URL is already public in the existing catalogue. Keep
            # the mobile contract and allow the website's authenticated player.
            refresh_at = datetime.now(timezone.utc) + timedelta(days=1)
            session = playback_session(request, content, selection)
            return Response({'state': 'ready', 'type': 'progressive', 'url': reference,
                             'content': content.pk, **selection, 'with_credentials': False,
                             'expires_at': refresh_at.isoformat(), 'session_id': str(session.pk)})
        config = settings.PLAYBACK_DELIVERY
        cdn = settings.MEDIA_STORAGE.get('CLOUDFRONT_BASE_URL') or settings.MEDIA_STORAGE.get('CDN_DOMAIN', '')
        if not cdn or urlsplit(cdn).scheme != 'https':
            raise PlaybackUnavailable()
        if asset:
            if (not asset.master_key.startswith(f'protected/hls/{asset.pk}/')
                    or not asset.master_key.endswith('/master.m3u8')
                    or any(part in asset.master_key for part in ('..', '?', '#', '\\'))):
                raise PlaybackUnavailable()
            source = build_delivery_url(cdn, asset.master_key)
            source_type = 'hls'
        else:
            # Legacy fallback is restricted to exact CDN origin and progressive
            # media under an operations-approved protected cache behavior.
            expected, actual = urlsplit(cdn), urlsplit(reference)
            prefixes = config.get('LEGACY_PREFIXES', [])
            if (actual.scheme != 'https' or actual.netloc != expected.netloc or actual.query or actual.fragment
                    or actual.path.startswith(('/cbmvideo/private/', '/cbmvideo/protected/', '/protected/'))
                    or not any(actual.path.startswith(p) for p in prefixes)
                    or not actual.path.lower().endswith(('.mp4', '.m4v', '.webm', '.mov'))):
                return Response({'state': 'unavailable', 'detail': 'This legacy video requires protected media migration.'}, status=409)
            source, source_type = reference, 'progressive'
        expires = datetime.now(timezone.utc) + timedelta(seconds=max(60, min(3600, config.get('TTL', 900))))
        response = Response({'state': 'ready', 'type': source_type, 'expires_at': expires.isoformat(),
            'content': content.pk, **selection, 'with_credentials': source_type == 'hls'})
        response.data['url'] = grant(response, source, expires, hls=source_type == 'hls')
        session = playback_session(request, content, selection)
        response.data['session_id'] = str(session.pk)
        return response

"""Validate sparse, idempotent discovery events at the server trust boundary."""
import uuid
from datetime import timedelta
from django.db import IntegrityError, transaction
from django.utils import timezone
from rest_framework.exceptions import ValidationError, PermissionDenied
from content.models import Content
from content.publication import public_eligibility
from .models import DiscoveryEvent, PlaybackSession
from .receipts import read_receipt


def _uuid(value, name):
    try:
        return uuid.UUID(str(value))
    except (ValueError, TypeError, AttributeError):
        raise ValidationError({name: 'Use a UUID.'}) from None


def _content(value):
    raw = str(value)
    if isinstance(value, bool) or not raw.isascii() or not raw.isdigit() or len(raw) > 18 or int(raw) < 1:
        raise ValidationError({'content': 'Use a positive content ID.'})
    content = Content.objects.filter(pk=int(raw), **public_eligibility()).first()
    if not content:
        raise ValidationError({'content': 'Content is not available.'})
    return content


def _seconds(data):
    try:
        position, duration = float(data['position_seconds']), float(data['duration_seconds'])
    except (KeyError, TypeError, ValueError):
        raise ValidationError({'position_seconds': 'Provide finite position and duration.'}) from None
    import math
    if not math.isfinite(position) or not math.isfinite(duration) or duration <= 0 or duration > 28800 or position < 0 or position > duration + 2:
        raise ValidationError({'position_seconds': 'Provide a valid position within an eight-hour video.'})
    return position, duration


def _create(event_id, **values):
    try:
        with transaction.atomic():
            return DiscoveryEvent.objects.create(id=event_id, **values), True
    except IntegrityError:
        return DiscoveryEvent.objects.filter(pk=event_id).first(), False


def ingest(data, user):
    if not isinstance(data, dict):
        raise ValidationError('Provide one event object.')
    event_id = _uuid(data.get('event_id'), 'event_id')
    viewer = user if user and user.is_authenticated else None
    existing = DiscoveryEvent.objects.filter(pk=event_id).first()
    if existing:
        if existing.user_id != (viewer.pk if viewer else None):
            raise PermissionDenied('This event belongs to another viewer.')
        return {'accepted': True, 'duplicate': True}
    kind = data.get('type')
    allowed = {value for value, _ in DiscoveryEvent.KINDS} - {DiscoveryEvent.QUALIFIED_VIEW}
    if kind not in allowed:
        raise ValidationError({'type': 'Unsupported discovery event.'})
    # Staff and named fixture accounts must not become public popularity signals.
    if viewer and (viewer.is_staff or viewer.is_superuser or (viewer.email or '').lower().endswith(('@example.com', '@example.invalid'))):
        return {'accepted': False, 'reason': 'excluded_account'}
    if kind in (DiscoveryEvent.SEARCH_SUBMITTED, DiscoveryEvent.SEARCH_SELECTED):
        try:
            receipt = read_receipt(data.get('receipt'))
        except ValueError as exc:
            raise ValidationError({'receipt': str(exc)}) from exc
        if kind == DiscoveryEvent.SEARCH_SUBMITTED:
            event, made = _create(event_id, kind=kind, user=viewer, search_id=event_id,
                term=receipt['q'], mode=receipt['m'], result_count=receipt['n'])
            return {'accepted': bool(event), 'duplicate': not made, 'search_id': str(event_id)}
        search_id = _uuid(data.get('search_id'), 'search_id')
        submitted = DiscoveryEvent.objects.filter(pk=search_id, kind=DiscoveryEvent.SEARCH_SUBMITTED).first()
        if not submitted or submitted.user_id != (viewer.pk if viewer else None) or submitted.term != receipt['q'] or submitted.mode != receipt['m']:
            raise ValidationError({'search_id': 'Submit this search first.'})
        if DiscoveryEvent.objects.filter(search_id=search_id, kind=DiscoveryEvent.SEARCH_SELECTED).exists():
            raise ValidationError({'search_id': 'Only the first selected result counts for this search.'})
        content = _content(data.get('content'))
        if content.pk not in receipt['ids']:
            raise ValidationError({'content': 'The title was not in the signed result page.'})
        event, made = _create(event_id, kind=kind, user=viewer, content=content, search_id=search_id,
            term=receipt['q'], mode=receipt['m'])
        return {'accepted': bool(event), 'duplicate': not made}
    if kind in (DiscoveryEvent.DETAIL_OPENED, DiscoveryEvent.RECOMMENDATION_SELECTED):
        content = _content(data.get('content'))
        source = ''
        if kind == DiscoveryEvent.RECOMMENDATION_SELECTED:
            source = data.get('source')
            if source not in ('similar', 'personalized', 'trending', 'most_watched'):
                raise ValidationError({'source': 'Choose a discovery source.'})
        event, made = _create(event_id, kind=kind, user=viewer, content=content, source=source)
        return {'accepted': bool(event), 'duplicate': not made}
    if not viewer:
        raise PermissionDenied('Sign in before recording playback activity.')
    session_id = _uuid(data.get('session_id'), 'session_id')
    now = timezone.now()
    with transaction.atomic():
        session = PlaybackSession.objects.select_for_update().filter(pk=session_id, user=viewer,
            content__status='approved', content__channel__is_active=True,
            created_at__gte=now - timedelta(hours=24)).first()
        if not session:
            raise ValidationError({'session_id': 'Playback session is unavailable.'})
        if kind == DiscoveryEvent.PLAYBACK_STARTED:
            if session.started_at:
                return {'accepted': True, 'duplicate': True}
            position, duration = _seconds(data)
            session.started_at = now
            session.first_position = position
            session.max_position = position
            session.save(update_fields=['started_at', 'first_position', 'max_position'])
            _create(event_id, kind=kind, user=viewer, content=session.content, session=session,
                position_seconds=int(position), duration_seconds=int(duration))
            return {'accepted': True, 'duplicate': False}
        if not session.started_at:
            raise ValidationError({'session_id': 'Start playback before reporting progress.'})
        position, duration = _seconds(data)
        if position > session.max_position:
            session.max_position = position
            session.save(update_fields=['max_position'])
        threshold = min(30, max(10, duration * 0.1))
        enough_play = position - (session.first_position or 0) >= threshold and (now - session.started_at).total_seconds() >= min(15, threshold)
        if kind == DiscoveryEvent.PLAYBACK_PROGRESS:
            if not enough_play or session.qualified_at:
                return {'accepted': False, 'reason': 'threshold_or_duplicate'}
            session.qualified_at = now
            session.save(update_fields=['qualified_at'])
            _create(event_id, kind=kind, user=viewer, content=session.content, session=session,
                position_seconds=int(position), duration_seconds=int(duration))
            # One viewer/title/UTC day, including all episodes and renewed grants.
            _create(uuid.uuid4(), kind=DiscoveryEvent.QUALIFIED_VIEW, user=viewer,
                content=session.content, session=session, qualified_day=now.date())
            return {'accepted': True, 'qualified': True}
        if session.completed_at:
            return {'accepted': True, 'duplicate': True}
        if position < duration * 0.95 or not (enough_play or session.qualified_at):
            raise ValidationError({'position_seconds': 'Completion requires meaningful playback near the end.'})
        session.completed_at = now
        session.save(update_fields=['completed_at'])
        _create(event_id, kind=kind, user=viewer, content=session.content, session=session,
            position_seconds=int(position), duration_seconds=int(duration))
        if not session.qualified_at:
            session.qualified_at = now
            session.save(update_fields=['qualified_at'])
            _create(uuid.uuid4(), kind=DiscoveryEvent.QUALIFIED_VIEW, user=viewer,
                content=session.content, session=session, qualified_day=now.date())
        return {'accepted': True, 'completed': True}

"""Server-authorized context, serialized cap reservations, and replay-safe events."""
import uuid
from datetime import timedelta
from urllib.parse import urlsplit
from django.conf import settings
from django.core import signing
from django.db import transaction, connection
from django.db.models import Q, F
from django.utils import timezone
from rest_framework.exceptions import ValidationError, PermissionDenied
from content.models import Content
from content.publication import public_eligibility
from content.playback import grant, PlaybackUnavailable
from common.media_storage import build_delivery_url
from channel.models import Channel
from discovery.models import PlaybackSession
from homepage.models import HomepageConfiguration
from .constants import HOMEPAGE_PLACEMENTS, VIDEO_PLACEMENTS, EVENTS
from .models import AdSession, Decision, Campaign, CampaignDay, Creative, Placement, Event, AdRequest
from .serializers import creative_ready, media_duration


def identifier(value):
    try:
        return uuid.UUID(str(value))
    except (ValueError, TypeError, AttributeError):
        raise ValidationError('Use a valid session or request identifier.') from None


def context_session(data, user):
    now = timezone.now()
    context = data.get('context')
    content = channel = playback = None
    owner = user if user.is_authenticated else None
    if context == 'playback':
        if owner is None:
            raise PermissionDenied('Sign in for video advertising.')
        playback = PlaybackSession.objects.filter(pk=identifier(data.get('playback_session')), user=user,
            created_at__gte=now - timedelta(hours=24), content__in=Content.objects.filter(**public_eligibility())).first()
        if not playback:
            raise ValidationError('A current authorized playback session is required.')
        content = playback.content
        existing = AdSession.objects.filter(playback=playback).first()
        if existing:
            return existing
    elif context == 'content':
        content = Content.objects.filter(pk=positive_id(data.get('content')), **public_eligibility()).first()
        if not content:
            raise ValidationError('Content is unavailable.')
    elif context == 'channel':
        channel = Channel.objects.filter(pk=positive_id(data.get('channel')), is_active=True).first()
        if not channel:
            raise ValidationError('Channel is unavailable.')
    elif context != 'homepage':
        raise ValidationError('Choose homepage, channel, content or playback context.')
    fields = dict(user=owner, context=context, content=content, channel=channel,
                  expires_at=playback.created_at + timedelta(hours=24) if playback else now + timedelta(hours=2))
    if playback:
        return AdSession.objects.get_or_create(playback=playback, defaults=fields)[0]
    return AdSession.objects.create(**fields)


def positive_id(value):
    if type(value) is not int or not 1 <= value <= 2**63 - 1:
        raise ValidationError('Select a valid context identifier.')
    return value


def receipt(session):
    return signing.dumps({'session': str(session.pk)}, salt='advertising.session', compress=True)


def authorized_session(token, user):
    try:
        value = signing.loads(token, salt='advertising.session', max_age=86400)
        session = AdSession.objects.get(pk=identifier(value['session']), expires_at__gt=timezone.now())
    except (signing.BadSignature, AdSession.DoesNotExist, KeyError, TypeError):
        raise PermissionDenied('Advertising session expired or invalid.') from None
    owner = user.pk if user.is_authenticated else None
    if session.user_id != owner:
        raise PermissionDenied('Advertising session belongs to another viewer.')
    if session.content_id and not Content.objects.filter(pk=session.content_id, **public_eligibility()).exists():
        raise PermissionDenied('Content is unavailable.')
    if session.channel_id and not Channel.objects.filter(pk=session.channel_id, is_active=True).exists():
        raise PermissionDenied('Channel is unavailable.')
    return session


def placement_allowed(session, placement, break_key):
    code = placement.code
    if session.context == 'playback':
        if code not in VIDEO_PLACEMENTS:
            return False
        return break_key in [str(n) for n in placement.breakpoints] if code == 'midroll' else break_key == 'once'
    if session.context == 'homepage':
        config = HomepageConfiguration.objects.filter(pk=1).first() or HomepageConfiguration()
        if code == 'homepage_featured_sponsor' and break_key.startswith('featured-'):
            slot = break_key.removeprefix('featured-')
            return (config.advert_insertion_enabled and slot.isdecimal() and
                    1 <= int(slot) <= 20 and int(slot) % config.advert_interval == 0)
        if break_key != 'once':
            return False
        return code in HOMEPAGE_PLACEMENTS and any(s['id'] == code and s['enabled'] for s in config.sections)
    if break_key != 'once':
        return False
    return code == {'channel': 'channel_sponsor', 'content': 'content_sponsor'}.get(session.context)


def matches(campaign, session, region):
    rules = campaign.targeting
    content = session.content
    if rules.get('regions') and region not in rules['regions']:
        return False
    channel_id = content.channel_id if content else session.channel_id
    for key, actual in [('contents', content.pk if content else None), ('channels', channel_id),
                        ('categories', content.category if content else None)]:
        if rules.get(key) and actual not in rules[key]:
            return False
    if rules.get('genres') and (not content or not content.genres.filter(pk__in=rules['genres'], is_active=True).exists()):
        return False
    return True


def active(campaign, now):
    return (campaign.approval == 'approved' and campaign.status == 'active' and campaign.advertiser.active
            and campaign.starts_at <= now < campaign.ends_at)


def promoted_eligible(creative):
    if creative.promoted_contents and Content.objects.filter(pk__in=creative.promoted_contents, **public_eligibility()).count() != len(creative.promoted_contents):
        return False
    if creative.promoted_content_id and not Content.objects.filter(pk=creative.promoted_content_id, **public_eligibility()).exists():
        return False
    if creative.promoted_channel_id and not Channel.objects.filter(pk=creative.promoted_channel_id, is_active=True).exists():
        return False
    # A deleted promoted object cannot silently become an unrelated banner.
    if 'homepage_content_promotion' in creative.placements and not creative.promoted_content_id:
        return False
    if 'homepage_channel_promotion' in creative.placements and not creative.promoted_channel_id:
        return False
    return True


def payload(decision, response, region=''):
    now = timezone.now()
    if (not settings.ADVERTISING_ENABLED or not decision.creative_id or decision.expires_at <= now
            or decision.terminal_at or not decision.placement.enabled or not active(decision.campaign, now)
            or decision.creative.approval != 'approved' or not promoted_eligible(decision.creative)
            or decision.placement_id not in decision.campaign.placements or decision.placement_id not in decision.creative.placements
            or not matches(decision.campaign, decision.session, region)
            or not creative_ready(decision.creative)):
        return {'ad': None, 'decision_id': str(decision.pk), 'reason': decision.reason or 'ineligible'}
    creative = decision.creative
    ad = {'decision_id': str(decision.pk), 'kind': creative.kind, 'name': creative.name,
          'advertiser': decision.campaign.advertiser.name, 'destination_url': creative.destination_url,
          'cta': creative.cta, 'skippable_after': decision.campaign.skippable_after,
          'placement': decision.placement_id, 'expires_at': decision.expires_at.isoformat(),
          'promoted_content': creative.promoted_content_id, 'promoted_channel': creative.promoted_channel_id}
    if creative.promoted_contents:
        contents = {row['id']: row for row in Content.objects.filter(pk__in=creative.promoted_contents,
            **public_eligibility()).values('id', 'title', 'thumbnail')}
        ad['sponsored_items'] = [contents[pk] for pk in creative.promoted_contents if pk in contents]
    if creative.kind == 'image':
        ad.update(url=creative.image.desktop_url, mobile_url=creative.image.mobile_url,
                  width=creative.image.width, height=creative.image.height)
    else:
        media = creative.media
        if not media.master_key.startswith(f'protected/hls/{media.pk}/') or not media.master_key.endswith('/master.m3u8') or any(c in media.master_key for c in ('..', '?', '#', '\\')):
            return {'ad': None, 'decision_id': str(decision.pk), 'reason': 'media_unavailable'}
        cdn = settings.MEDIA_STORAGE.get('CLOUDFRONT_BASE_URL') or settings.MEDIA_STORAGE.get('CDN_DOMAIN', '')
        if urlsplit(cdn).scheme != 'https':
            return {'ad': None, 'decision_id': str(decision.pk), 'reason': 'media_unavailable'}
        try:
            ad.update(url=grant(response, build_delivery_url(cdn, media.master_key), decision.expires_at, hls=True),
                      source_type='hls', with_credentials=True, duration_seconds=media_duration(media))
        except PlaybackUnavailable:
            return {'ad': None, 'decision_id': str(decision.pk), 'reason': 'media_unavailable'}
    return {'ad': ad, 'decision_id': str(decision.pk)}


@transaction.atomic
def decide(session, placement, break_key, request_id, response, region=''):
    # All delivery/event paths take session then campaign locks in that order.
    session = AdSession.objects.select_for_update(of=('self',)).select_related('content').get(pk=session.pk)
    existing_request = AdRequest.objects.filter(pk=request_id).select_related('decision').first()
    if existing_request and (existing_request.decision.session_id != session.pk or existing_request.decision.placement_id != placement.code
                             or existing_request.decision.break_key != break_key):
        raise ValidationError('Request identifier already belongs to another break.')
    existing = Decision.objects.filter(session=session, placement=placement, break_key=break_key).first()
    if existing:
        record_request(request_id, existing)
        return payload(existing, response, region)
    now = timezone.now()
    expires = min(now + timedelta(minutes=15), session.expires_at)
    choice = None
    campaign_choice = None
    reason = 'no_eligible_campaign'
    if not settings.ADVERTISING_ENABLED:
        reason = 'disabled'
    elif not placement.enabled or not placement_allowed(session, placement, break_key):
        reason = 'placement_disabled'
    else:
        # Streamed queue: priority, least recently served, then stable ID. Rotation
        # operates within equal priority. Each candidate is rechecked under lock.
        candidates = Campaign.objects.filter(approval='approved', status='active', advertiser__active=True,
            starts_at__lte=now, ends_at__gt=now).order_by('-priority', F('last_served_at').asc(nulls_first=True), 'pk').values_list('pk', flat=True).iterator(chunk_size=100)
        for pk in candidates:
            queryset = Campaign.objects.select_related('advertiser').filter(pk=pk)
            locks = {'of': ('self',)}
            if connection.features.has_select_for_update_skip_locked:
                locks['skip_locked'] = True
            campaign = queryset.select_for_update(**locks).first()
            if campaign is None:
                reason = 'capacity_busy'
                continue
            if not active(campaign, now) or placement.code not in campaign.placements or not matches(campaign, session, region):
                continue
            reserved = Decision.objects.filter(campaign=campaign, impression_at__isnull=True,
                terminal_at__isnull=True, expires_at__gt=now)
            day = now.date()
            delivered_today = CampaignDay.objects.filter(campaign=campaign, day=day).values_list('impressions', flat=True).first() or 0
            # Include reservations from the previous UTC day: they may report
            # their first impression today. This is deliberately conservative.
            if campaign.impression_limit is not None and campaign.impressions + reserved.count() >= campaign.impression_limit:
                continue
            if campaign.daily_limit is not None and delivered_today + reserved.count() >= campaign.daily_limit:
                continue
            frequency = Decision.objects.filter(session=session, campaign=campaign).filter(
                Q(impression_at__isnull=False) | Q(terminal_at__isnull=True, expires_at__gt=now)).count()
            if frequency >= campaign.session_limit:
                continue
            creatives = Creative.objects.filter(campaign=campaign, approval='approved').select_related('media', 'image').order_by('pk')
            previous = set(Decision.objects.filter(session=session, campaign=campaign).values_list('creative_id', flat=True))
            eligible = [c for c in creatives if placement.code in c.placements and creative_ready(c) and promoted_eligible(c)]
            eligible.sort(key=lambda c: (c.pk in previous, c.pk))
            for creative in eligible:
                draft = Decision(id=uuid.uuid4(), session=session, placement=placement, break_key=break_key,
                                 campaign=campaign, creative=creative, expires_at=expires)
                if payload(draft, response, region)['ad'] is not None:
                    choice, campaign_choice = creative, campaign
                    break
                reason = 'media_unavailable'
            if choice:
                campaign.last_served_at = now
                campaign.save(update_fields=['last_served_at'])
                break
    decision = Decision.objects.create(session=session, placement=placement, break_key=break_key,
        creative=choice, campaign=campaign_choice, reason='' if choice else reason, expires_at=expires)
    record_request(request_id, decision)
    return payload(decision, response, region)


def record_request(request_id, decision):
    # get_or_create handles a simultaneous UUID insert in a savepoint. A reused
    # identifier for a different session/break is rejected and the outer
    # transaction rolls back its new decision/reservation.
    row, _ = AdRequest.objects.get_or_create(pk=request_id, defaults={'decision': decision})
    if row.decision_id != decision.pk:
        raise ValidationError('Request identifier already belongs to another break.')


@transaction.atomic
def ingest(session, decision_id, kind, position):
    if kind not in EVENTS or type(position) is not int or not 0 <= position <= 300:
        raise ValidationError('Use a supported event and position in seconds.')
    AdSession.objects.select_for_update(of=('self',)).get(pk=session.pk)
    decision = Decision.objects.filter(pk=decision_id, session=session).first()
    if not decision or not decision.creative_id:
        raise PermissionDenied('A served decision in this viewer session is required.')
    campaign = Campaign.objects.select_for_update(of=('self',)).select_related('advertiser').get(pk=decision.campaign_id)
    decision = Decision.objects.select_for_update(of=('self',)).select_related('creative__media', 'placement').get(pk=decision.pk)
    if Event.objects.filter(decision=decision, kind=kind).exists():
        return {'accepted': True, 'duplicate': True}
    now = timezone.now()
    if not settings.ADVERTISING_ENABLED or decision.expires_at <= now or decision.terminal_at:
        raise ValidationError('Decision is expired, stopped or already finished.')
    video = decision.creative.kind == 'video'
    elapsed = (now - decision.started_at).total_seconds() if decision.started_at else 0
    duration = media_duration(decision.creative.media) if video else None
    if kind in ('video_start', 'first_quartile', 'midpoint', 'third_quartile', 'complete', 'skip') and not video:
        raise ValidationError('This event requires video media.')
    if kind in ('impression', 'video_start') and (not active(campaign, now) or not decision.placement.enabled
            or decision.creative.approval != 'approved' or not promoted_eligible(decision.creative)):
        raise ValidationError('Delivery has been stopped.')
    if kind == 'impression':
        minimum = 2 if video else 1
        if (now - decision.created_at).total_seconds() < minimum or video and (elapsed < 2 or position < 2):
            raise ValidationError('Impression requires qualifying visible playback or display time.')
        day, _ = CampaignDay.objects.get_or_create(campaign=campaign, day=now.date())
        if campaign.impression_limit is not None and campaign.impressions >= campaign.impression_limit or campaign.daily_limit is not None and day.impressions >= campaign.daily_limit:
            raise ValidationError('Campaign cap reached.')
        campaign.impressions += 1
        campaign.save(update_fields=['impressions'])
        day.impressions += 1
        day.save(update_fields=['impressions'])
        decision.impression_at = now
    elif kind == 'video_start':
        decision.started_at = now
    elif kind in ('first_quartile', 'midpoint', 'third_quartile', 'complete'):
        ratios = {'first_quartile': .25, 'midpoint': .5, 'third_quartile': .75, 'complete': .95}
        predecessors = {'first_quartile': 'video_start', 'midpoint': 'first_quartile',
                        'third_quartile': 'midpoint', 'complete': 'third_quartile'}
        threshold = duration * ratios[kind] if duration else 301
        if not decision.impression_at or position < threshold or elapsed < max(2, threshold - 1) or not Event.objects.filter(decision=decision, kind=predecessors[kind]).exists():
            raise ValidationError('Video milestones require ordered, elapsed playback.')
        if kind == 'complete':
            decision.terminal_at = now
    elif kind == 'skip':
        if campaign.skippable_after is None or not decision.started_at or elapsed < campaign.skippable_after or position < campaign.skippable_after:
            raise ValidationError('This advertisement is not skippable yet.')
        decision.terminal_at = now
    elif kind == 'click':
        if not (decision.creative.destination_url or decision.creative.promoted_content_id or decision.creative.promoted_channel_id or decision.creative.promoted_contents):
            raise ValidationError('An approved click destination is required.')
    elif kind == 'failure':
        decision.terminal_at = now
    decision.save(update_fields=['impression_at', 'started_at', 'terminal_at'])
    Event.objects.create(decision=decision, kind=kind, position_seconds=position)
    return {'accepted': True, 'duplicate': False}

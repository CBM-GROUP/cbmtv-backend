import ipaddress
import re
from urllib.parse import urlsplit
from django.utils import timezone
from django.db.models import Q
from rest_framework import serializers
from content.models import Content, Genre
from content.publication import public_eligibility
from content.taxonomy import CATEGORY_FLAGS
from channel.models import Channel
from homepage.serializers import AwareDateTimeField
from .constants import PLACEMENTS, VIDEO_PLACEMENTS
from .models import Advertiser, Campaign, Creative, Placement, DeliveryAllocation


def placement_codes(value):
    if not isinstance(value, list) or len(value) != len(set(str(v) for v in value)) or any(not isinstance(v, str) or v not in PLACEMENTS for v in value):
        raise serializers.ValidationError('Choose unique supported placements.')
    return value


def safe_destination(value):
    if not value:
        return value
    try:
        parsed = urlsplit(value)
        host = parsed.hostname or ''
        if (parsed.scheme != 'https' or parsed.username or parsed.password or parsed.port not in (None, 443)
                or '.' not in host or not re.search(r'[A-Za-z]', host.rsplit('.', 1)[-1])
                or host.endswith(('.local', '.internal', '.localhost', '.test'))
                or any(ord(c) < 33 or c in '\\' for c in value)):
            raise ValueError()
        try:
            ipaddress.ip_address(host)
        except ValueError:
            pass
        else:
            raise ValueError()
    except (ValueError, TypeError):
        raise serializers.ValidationError('Use a public HTTPS destination without credentials or a custom port.') from None
    return value


def targeting_rules(value):
    if not isinstance(value, dict) or set(value) - {'categories', 'genres', 'channels', 'contents', 'regions'}:
        raise serializers.ValidationError('Supported targeting: categories, genres, channels, contents, regions.')
    for key, values in value.items():
        if not isinstance(values, list) or len(values) > 100 or len(values) != len(set(str(v) for v in values)):
            raise serializers.ValidationError('Each target must be a unique list of at most 100 values.')
        if key == 'categories' and any(not isinstance(v, str) or v not in CATEGORY_FLAGS for v in values):
            raise serializers.ValidationError('Select valid categories.')
        if key == 'regions' and any(not isinstance(v, str) or len(v) != 2 or not v.isascii() or not v.isalpha() or v != v.upper() for v in values):
            raise serializers.ValidationError('Regions use uppercase two-letter codes.')
        models = {'genres': (Genre, {'is_active': True}), 'channels': (Channel, {'is_active': True}),
                  'contents': (Content, public_eligibility())}
        if key in models:
            model, filters = models[key]
            if any(type(v) is not int or v < 1 for v in values) or model.objects.filter(pk__in=values, **filters).count() != len(values):
                raise serializers.ValidationError('Select existing eligible targets.')
    return value


class AdvertiserSerializer(serializers.ModelSerializer):
    validate_default_destination_url = staticmethod(safe_destination)
    class Meta:
        model = Advertiser
        fields = ('id', 'name', 'contact_name', 'email', 'phone', 'billing_reference', 'active', 'default_destination_url', 'created_at', 'updated_at')
        read_only_fields = ('created_at', 'updated_at')


class CampaignSerializer(serializers.ModelSerializer):
    starts_at = AwareDateTimeField(required=False, allow_null=True)
    ends_at = AwareDateTimeField(required=False, allow_null=True)
    validate_targeting = staticmethod(targeting_rules)
    validate_default_destination_url = staticmethod(safe_destination)

    class Meta:
        model = Campaign
        fields = ('id', 'name', 'advertiser', 'description', 'default_destination_url', 'starts_at', 'ends_at', 'approval', 'status',
                  'priority', 'placements', 'targeting', 'impression_limit', 'daily_limit', 'session_limit',
                  'skippable_after', 'impressions', 'created_at', 'updated_at')
        read_only_fields = ('approval', 'status', 'impressions', 'created_at', 'updated_at')
        extra_kwargs = {'priority': {'min_value': 1, 'max_value': 100}, 'session_limit': {'min_value': 1, 'max_value': 20},
                        'impression_limit': {'min_value': 1}, 'daily_limit': {'min_value': 1},
                        'skippable_after': {'max_value': 300}}

    def validate(self, attrs):
        start = attrs.get('starts_at', getattr(self.instance, 'starts_at', None))
        end = attrs.get('ends_at', getattr(self.instance, 'ends_at', None))
        if self.instance and 'advertiser' in attrs and attrs['advertiser'] != self.instance.advertiser and self.instance.creatives.exists():
            raise serializers.ValidationError({'advertiser': 'Move or archive existing creatives before changing the client.'})
        if bool(start) != bool(end):
            raise serializers.ValidationError('Set both campaign dates or leave both unset.')
        if start and end and end <= start:
            raise serializers.ValidationError({'ends_at': 'End must be after start.'})
        return attrs

    def validate_placements(self, value):
        return placement_codes(value)


def media_duration(media):
    try:
        duration = float(media.renditions[0]['duration_seconds'])
        return duration if 0 < duration <= 300 else None
    except (TypeError, KeyError, IndexError, ValueError, AttributeError):
        return None


def creative_ready(creative):
    if creative.kind == 'image':
        return bool(creative.image_id)
    return bool(creative.media_id and creative.media.purpose == 'protected' and creative.media.state == 'ready'
                and media_duration(creative.media))


class CreativeSerializer(serializers.ModelSerializer):
    advertiser_name = serializers.CharField(source='advertiser.name', read_only=True)
    campaign_name = serializers.CharField(source='campaign.name', read_only=True, allow_null=True)
    ready = serializers.SerializerMethodField()
    media_reference = serializers.CharField(source='media.reference_url', read_only=True, allow_null=True)
    media_state = serializers.CharField(source='media.state', read_only=True, allow_null=True)
    effective_destination_url = serializers.SerializerMethodField()
    destination_source = serializers.SerializerMethodField()
    allocation_count = serializers.IntegerField(read_only=True, default=0)
    allocation_statuses = serializers.SerializerMethodField()
    validate_placements = staticmethod(placement_codes)
    validate_destination_url = staticmethod(safe_destination)

    class Meta:
        model = Creative
        fields = ('id', 'name', 'campaign', 'campaign_name', 'advertiser', 'advertiser_name', 'kind', 'placements', 'approval', 'destination_url',
                  'effective_destination_url', 'destination_source', 'allocation_count', 'allocation_statuses',
                  'cta', 'image', 'media', 'duration_seconds', 'promoted_content', 'promoted_channel', 'promoted_contents', 'ready',
                  'media_reference', 'media_state', 'created_at', 'updated_at')
        read_only_fields = ('approval', 'duration_seconds', 'created_at', 'updated_at')
        extra_kwargs = {'advertiser': {'required': False}, 'campaign': {'required': False, 'allow_null': True},
                        'placements': {'required': False}}

    def get_effective_destination_url(self, obj):
        from .destinations import effective_destination
        return effective_destination(obj)[0]

    def get_destination_source(self, obj):
        from .destinations import effective_destination
        return effective_destination(obj)[1]

    def get_allocation_statuses(self, obj):
        return {state: getattr(obj, f'allocation_{state}_count', 0) for state in ('paused', 'active', 'completed')}

    def get_ready(self, obj):
        return creative_ready(obj)

    def validate_promoted_contents(self, value):
        if (not isinstance(value, list) or len(value) > 6 or any(type(pk) is not int or pk < 1 for pk in value)
                or len(value) != len(set(value)) or Content.objects.filter(pk__in=value, **public_eligibility()).count() != len(value)):
            raise serializers.ValidationError('Choose up to six distinct published titles for the sponsored collection.')
        return value

    def validate(self, attrs):
        def field(key):
            return attrs.get(key, getattr(self.instance, key, None))
        campaign, kind, image, media = (field(key) for key in ('campaign', 'kind', 'image', 'media'))
        advertiser = field('advertiser')
        if not advertiser and campaign:
            advertiser = campaign.advertiser
            attrs['advertiser'] = advertiser
        if not advertiser:
            raise serializers.ValidationError({'advertiser': 'Select a client.'})
        if campaign and campaign.advertiser_id != advertiser.pk:
            raise serializers.ValidationError({'campaign': 'Campaign belongs to another client.'})
        if self.instance and advertiser != self.instance.advertiser:
            raise serializers.ValidationError('A creative cannot be moved to another client.')
        if self.instance and campaign != self.instance.campaign:
            raise serializers.ValidationError('A creative cannot be moved to another campaign.')
        if kind == 'image' and (not image or media) or kind == 'video' and (not media or image):
            raise serializers.ValidationError('Attach exactly one asset matching the creative type.')
        asset = image if kind == 'image' else media
        previous_id = getattr(self.instance, 'image_id' if kind == 'image' else 'media_id', None)
        if asset and asset.pk != previous_id and asset.owner_id != self.context['request'].user.pk:
            raise serializers.ValidationError('This upload belongs to another account.')
        if media and (media.purpose != 'protected' or media.state in ('uploading', 'canceled')):
            raise serializers.ValidationError('Complete a protected video upload first.')
        if media and media.size_bytes > 1024 ** 3:
            raise serializers.ValidationError('Advertising videos must be no larger than 1 GiB.')
        placements = field('placements') or []
        if campaign and set(placements) - set(campaign.placements):
            raise serializers.ValidationError('Creative placements must be eligible for its campaign.')
        if any((code in VIDEO_PLACEMENTS) != (kind == 'video') for code in placements):
            raise serializers.ValidationError('Video placements require video; page placements require images.')
        promoted_content, promoted_channel = field('promoted_content'), field('promoted_channel')
        if promoted_content and not Content.objects.filter(pk=promoted_content.pk, **public_eligibility()).exists():
            raise serializers.ValidationError('Promoted content must be published on an active channel.')
        if promoted_channel and not promoted_channel.is_active:
            raise serializers.ValidationError('Promoted channels must be active.')
        if 'homepage_content_promotion' in placements and not promoted_content:
            raise serializers.ValidationError('A sponsored title needs published promoted content.')
        if 'homepage_channel_promotion' in placements and not promoted_channel:
            raise serializers.ValidationError('A sponsored channel needs an active promoted channel.')
        return attrs


class DeliveryAllocationSerializer(serializers.ModelSerializer):
    starts_at = AwareDateTimeField()
    ends_at = AwareDateTimeField(required=False, allow_null=True)
    eligibility = serializers.SerializerMethodField()

    def get_eligibility(self, allocation):
        from .delivery import allocation_blockers
        reasons = allocation_blockers(allocation)
        return {'eligible': not reasons, 'reasons': reasons}

    class Meta:
        model = DeliveryAllocation
        fields = ('id', 'creative', 'placement', 'starts_at', 'ends_at', 'status', 'priority', 'daily_limit', 'impression_limit', 'impressions', 'eligibility', 'created_at', 'updated_at')
        read_only_fields = ('status', 'impressions', 'eligibility', 'created_at', 'updated_at')
        extra_kwargs = {'daily_limit': {'min_value': 1}, 'impression_limit': {'min_value': 1}}

    def validate(self, attrs):
        creative = attrs.get('creative', getattr(self.instance, 'creative', None))
        placement = attrs.get('placement', getattr(self.instance, 'placement', None))
        start = attrs.get('starts_at', getattr(self.instance, 'starts_at', None))
        end = attrs.get('ends_at', getattr(self.instance, 'ends_at', None))
        if self.instance and creative != self.instance.creative:
            raise serializers.ValidationError('An allocation cannot move to another creative.')
        if end and start and end <= start:
            raise serializers.ValidationError({'ends_at': 'End must be after start.'})
        if creative and placement:
            if (placement.code in VIDEO_PLACEMENTS) != (creative.kind == 'video'):
                raise serializers.ValidationError('Placement does not support this creative format.')
            overlaps = DeliveryAllocation.objects.filter(creative=creative, placement=placement).filter(
                Q(ends_at__isnull=True) | Q(ends_at__gt=start))
            if end:
                overlaps = overlaps.filter(starts_at__lt=end)
            if self.instance:
                overlaps = overlaps.exclude(pk=self.instance.pk)
            if overlaps.exists():
                raise serializers.ValidationError('This creative already has an overlapping allocation for the placement.')
        return attrs


class PlacementSerializer(serializers.ModelSerializer):
    class Meta:
        model = Placement
        fields = ('code', 'enabled', 'breakpoints', 'skippable_after', 'max_video_duration', 'max_midroll_interruptions', 'failure_policy')
        read_only_fields = ('code',)
        extra_kwargs = {'skippable_after': {'min_value': 0, 'max_value': 300},
                        'max_video_duration': {'min_value': 1, 'max_value': 300},
                        'max_midroll_interruptions': {'min_value': 0, 'max_value': 2}}

    def validate(self, attrs):
        code = self.instance.code
        if code not in VIDEO_PLACEMENTS and set(attrs) & {'skippable_after', 'max_video_duration', 'max_midroll_interruptions'}:
            raise serializers.ValidationError('Video playback settings apply only to video placements.')
        if code != 'midroll' and 'max_midroll_interruptions' in attrs:
            raise serializers.ValidationError('Mid-roll interruption limits apply only to mid-roll.')
        delay = attrs.get('skippable_after', self.instance.skippable_after)
        maximum = attrs.get('max_video_duration', self.instance.max_video_duration)
        if delay is not None and delay > maximum:
            raise serializers.ValidationError('Skip delay cannot exceed maximum video duration.')
        return attrs

    def validate_breakpoints(self, value):
        if not isinstance(value, list) or len(value) > 3 or any(type(n) is not int or n < 60 or n > 86400 for n in value) or value != sorted(set(value)):
            raise serializers.ValidationError('Use up to three increasing main-video breakpoints, 60–86400 seconds.')
        if self.instance.code != 'midroll' and value:
            raise serializers.ValidationError('Only mid-roll placements have breakpoints.')
        return value

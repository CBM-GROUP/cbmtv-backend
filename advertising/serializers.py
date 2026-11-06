import ipaddress
import re
from urllib.parse import urlsplit
from django.utils import timezone
from rest_framework import serializers
from content.models import Content, Genre
from content.publication import public_eligibility
from content.taxonomy import CATEGORY_FLAGS
from channel.models import Channel
from homepage.serializers import AwareDateTimeField
from .constants import PLACEMENTS, VIDEO_PLACEMENTS
from .models import Advertiser, Campaign, Creative, Placement


def placement_codes(value):
    if not isinstance(value, list) or not value or len(value) != len(set(str(v) for v in value)) or any(not isinstance(v, str) or v not in PLACEMENTS for v in value):
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
    class Meta:
        model = Advertiser
        fields = ('id', 'name', 'contact_name', 'email', 'phone', 'billing_reference', 'active', 'created_at', 'updated_at')
        read_only_fields = ('created_at', 'updated_at')


class CampaignSerializer(serializers.ModelSerializer):
    starts_at = AwareDateTimeField()
    ends_at = AwareDateTimeField()
    validate_placements = staticmethod(placement_codes)
    validate_targeting = staticmethod(targeting_rules)

    class Meta:
        model = Campaign
        fields = ('id', 'name', 'advertiser', 'description', 'starts_at', 'ends_at', 'approval', 'status',
                  'priority', 'placements', 'targeting', 'impression_limit', 'daily_limit', 'session_limit',
                  'skippable_after', 'impressions', 'created_at', 'updated_at')
        read_only_fields = ('approval', 'status', 'impressions', 'created_at', 'updated_at')
        extra_kwargs = {'priority': {'min_value': 1, 'max_value': 100}, 'session_limit': {'min_value': 1, 'max_value': 20},
                        'impression_limit': {'min_value': 1}, 'daily_limit': {'min_value': 1},
                        'skippable_after': {'max_value': 300}}

    def validate(self, attrs):
        start = attrs.get('starts_at', getattr(self.instance, 'starts_at', None))
        end = attrs.get('ends_at', getattr(self.instance, 'ends_at', None))
        if start and end and end <= start:
            raise serializers.ValidationError({'ends_at': 'End must be after start.'})
        return attrs


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
    ready = serializers.SerializerMethodField()
    media_reference = serializers.CharField(source='media.reference_url', read_only=True, allow_null=True)
    media_state = serializers.CharField(source='media.state', read_only=True, allow_null=True)
    advertiser = serializers.IntegerField(source='campaign.advertiser_id', read_only=True)
    validate_placements = staticmethod(placement_codes)
    validate_destination_url = staticmethod(safe_destination)

    class Meta:
        model = Creative
        fields = ('id', 'name', 'campaign', 'advertiser', 'kind', 'placements', 'approval', 'destination_url',
                  'cta', 'image', 'media', 'duration_seconds', 'promoted_content', 'promoted_channel', 'promoted_contents', 'ready',
                  'media_reference', 'media_state', 'created_at', 'updated_at')
        read_only_fields = ('approval', 'duration_seconds', 'created_at', 'updated_at')

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


class PlacementSerializer(serializers.ModelSerializer):
    class Meta:
        model = Placement
        fields = ('code', 'enabled', 'breakpoints', 'failure_policy')
        read_only_fields = ('code',)

    def validate_breakpoints(self, value):
        if not isinstance(value, list) or len(value) > 3 or any(type(n) is not int or n < 60 or n > 86400 for n in value) or value != sorted(set(value)):
            raise serializers.ValidationError('Use up to three increasing main-video breakpoints, 60–86400 seconds.')
        if self.instance.code != 'midroll' and value:
            raise serializers.ValidationError('Only mid-roll placements have breakpoints.')
        return value

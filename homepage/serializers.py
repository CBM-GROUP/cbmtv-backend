from rest_framework import serializers
from django.utils import timezone
from django.utils.dateparse import parse_datetime
from channel.models import Channel
from channel.serializers import ChannelSerializer
from content.models import Content
from content.publication import PUBLIC_STATUS, public_eligibility
from content.serializers import ContentSerializer
from .defaults import REQUIRED_SECTIONS, SECTION_LABELS
from advertising.constants import HOMEPAGE_PLACEMENTS


class SectionSerializer(serializers.Serializer):
    id = serializers.ChoiceField(choices=list(SECTION_LABELS) + list(HOMEPAGE_PLACEMENTS))
    enabled = serializers.BooleanField()


class AwareDateTimeField(serializers.DateTimeField):
    def to_internal_value(self, value):
        try:
            parsed = parse_datetime(value) if isinstance(value, str) else value
        except ValueError:
            parsed = None
        if parsed is not None and hasattr(parsed, 'utcoffset') and timezone.is_naive(parsed):
            raise serializers.ValidationError('Include a timezone offset or Z in schedule timestamps.')
        return super().to_internal_value(value)


class TitlePlacementSerializer(serializers.Serializer):
    content = serializers.PrimaryKeyRelatedField(queryset=Content.objects.filter(
        **public_eligibility(),
    ))
    start_at = AwareDateTimeField(required=False, allow_null=True, default=None)
    end_at = AwareDateTimeField(required=False, allow_null=True, default=None)
    detail = ContentSerializer(source='content', read_only=True)

    def validate(self, attrs):
        if attrs['start_at'] and attrs['end_at'] and attrs['start_at'] >= attrs['end_at']:
            raise serializers.ValidationError({'end_at': 'End must be after start.'})
        return attrs


class ChannelPlacementSerializer(serializers.Serializer):
    channel = serializers.PrimaryKeyRelatedField(queryset=Channel.objects.filter(is_active=True))
    detail = ChannelSerializer(source='channel', read_only=True)


class NewReleasesSerializer(serializers.Serializer):
    duration = serializers.IntegerField(min_value=1, max_value=1000)
    unit = serializers.ChoiceField(choices=('hours', 'days', 'months', 'years'))


class ConfigurationSerializer(serializers.Serializer):
    sections = SectionSerializer(many=True)
    featured_titles = TitlePlacementSerializer(many=True, max_length=20)
    featured_channels = ChannelPlacementSerializer(many=True, max_length=30)
    new_releases = NewReleasesSerializer()

    def validate_sections(self, value):
        ids = [row['id'] for row in value]
        if len(ids) != len(set(ids)) or not set(SECTION_LABELS).issubset(ids):
            raise serializers.ValidationError('Include every section exactly once.')
        if ids[0] != 'featured_hero':
            raise serializers.ValidationError('Featured Hero must stay first.')
        if any(not row['enabled'] for row in value if row['id'] in REQUIRED_SECTIONS):
            raise serializers.ValidationError('Featured Hero and Continue Watching are required.')
        enabled_ads = [row for row in value if row['id'] in HOMEPAGE_PLACEMENTS and row['enabled']]
        if len(enabled_ads) > 2:
            raise serializers.ValidationError('Enable at most two homepage advertising sections.')
        if any(row['id'] in HOMEPAGE_PLACEMENTS and row['enabled'] for row in value[:3]):
            raise serializers.ValidationError('Keep the first three homepage sections for browsing.')
        return value

    def validate_featured_titles(self, value):
        ids = [row['content'].pk for row in value]
        if len(ids) != len(set(ids)):
            raise serializers.ValidationError('A title may only be featured once.')
        return value

    def validate_featured_channels(self, value):
        ids = [row['channel'].pk for row in value]
        if len(ids) != len(set(ids)):
            raise serializers.ValidationError('A channel may only be featured once.')
        return value

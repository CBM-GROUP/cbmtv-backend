from rest_framework import serializers
from django.db import transaction
import logging
from common.idempotency import OperationConflict
from .models import Content, Season, Episode, contentadverts, MiniSeries, HeroSettings
from .publication import can_read_dashboard_content

class ContentSerializer(serializers.ModelSerializer):
    def update(self, instance, validated_data):
        # Reload under the lock: DRF may have fetched this instance before an
        # overlapping editorial transition committed. Never save stale status.
        with transaction.atomic():
            current = Content.objects.select_for_update().get(pk=instance.pk)
            previous_status = current.status
            request = self.context.get('request')
            expected = request.headers.get('If-Publication-Status') if request else None
            if 'status' in validated_data and expected is not None and expected != previous_status:
                raise OperationConflict('Publication status changed. Refresh before trying again.')
            content = super().update(current, validated_data)
            if content.status != previous_status:
                actor_id = request.user.pk if request else None
                content_id, new_status = content.pk, content.status
                transaction.on_commit(lambda: logging.getLogger('content.publication').info(
                    'Publication changed actor_id=%s content_id=%s from=%s to=%s',
                    actor_id, content_id, previous_status, new_status,
                ))
            return content

    def create(self, validated_data):
        # Status changes belong to an explicit edit after creation. This also
        # protects non-dashboard API clients that submit an approved status.
        return super().create({**validated_data, 'status': 'preview'})

    def to_representation(self, instance):
        data = super().to_representation(instance)
        request = self.context.get('request')
        if not (request and can_read_dashboard_content(request.user) and (
            request.method not in ('GET', 'HEAD', 'OPTIONS') or request.query_params.get('scope') == 'dashboard'
        )):
            # Keep the existing nullable key for old clients; technical byte
            # counts are only useful in the authorized management experience.
            data['size'] = None
        return data

    class Meta:
        model = Content
        fields = '__all__'

class SeasonSerializer(serializers.ModelSerializer):
    class Meta:
        model = Season
        fields = '__all__'

    def validate(self, attrs):
        content = attrs.get('content') or getattr(self.instance, 'content', None)
        if content and content.content_type != 'series':
            raise serializers.ValidationError('Seasons can only be created for content of type "series".')
        return attrs

class EpisodeSerializer(serializers.ModelSerializer):
    class Meta:
        model = Episode
        fields = '__all__'

    def validate(self, attrs):
        season = attrs.get('season') or getattr(self.instance, 'season', None)
        if season and season.content.content_type != 'series':
            raise serializers.ValidationError('Episodes can only be created for seasons under series content.')
        return attrs


class ContentAdvertSerializer(serializers.ModelSerializer):
    class Meta:
        model = contentadverts
        fields = '__all__'


class HeroSettingsSerializer(serializers.ModelSerializer):
    class Meta:
        model = HeroSettings
        fields = ('image_duration_seconds',)

    def validate_image_duration_seconds(self, value):
        if not 5 <= value <= 120:
            raise serializers.ValidationError('Choose a duration between 5 and 120 seconds.')
        return value


class MiniSeriesSerializer(serializers.ModelSerializer):
    class Meta:
        model = MiniSeries
        fields = '__all__'

    def validate(self, attrs):
        content = attrs.get('content') or getattr(self.instance, 'content', None)
        if content and content.content_type != 'miniseries':
            raise serializers.ValidationError('MiniSeries can only be created for content of type "miniseries".')
        return attrs

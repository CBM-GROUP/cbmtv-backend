from django.db import transaction
from rest_framework import serializers
from .models import MediaAsset
from .legacy_playback import public_legacy_video
from .publication import can_read_dashboard_content


class PublicMediaMixin:
    def _public_reference(self, value):
        if value:
            asset = MediaAsset.objects.filter(reference_url=value).first()
            if asset and (asset.purpose != 'public' or asset.state in ('uploading', 'canceled')):
                raise serializers.ValidationError('Public trailers and adverts require a completed public upload.')
        return value

    def validate_trailer_link(self, value):
        return self._public_reference(value)

    def validate_stream_link(self, value):
        return self._public_reference(value)

    def validate_advert_link(self, value):
        return self._public_reference(value)

    def to_representation(self, instance):
        data = super().to_representation(instance)
        for field in ('trailer_link', 'stream_link', 'advert_link'):
            value = data.get(field)
            if value and MediaAsset.objects.filter(reference_url=value, purpose='protected').exists():
                data[field] = None
        return data


class PlaybackMediaMixin(PublicMediaMixin):
    def validate_streaming_link(self, value):
        if not value:
            return value
        asset = MediaAsset.objects.filter(reference_url=value).first()
        if asset:
            request = self.context.get('request')
            existing = self.instance and self.instance.streaming_link == value
            if not request or (asset.owner_id != request.user.pk and not existing):
                raise serializers.ValidationError('This upload belongs to another account.')
            if asset.state in ('uploading', 'canceled') or asset.purpose != 'protected':
                raise serializers.ValidationError('Complete a protected source upload before saving it.')
        return value

    def _queue(self, instance):
        MediaAsset.objects.filter(reference_url=instance.streaming_link, purpose='protected', state='uploaded').update(state='queued')
        return instance

    @transaction.atomic
    def create(self, validated_data):
        return self._queue(super().create(validated_data))

    @transaction.atomic
    def update(self, instance, validated_data):
        return self._queue(super().update(instance, validated_data))

    def to_representation(self, instance):
        data = super().to_representation(instance)
        request = self.context.get('request')
        management = request and can_read_dashboard_content(request.user) and (
            request.method not in ('GET', 'HEAD', 'OPTIONS') or request.query_params.get('scope') == 'dashboard')
        data['playback_available'] = bool(instance.streaming_link)
        if not management and not (public_legacy_video(instance.streaming_link)
                                   and not MediaAsset.objects.filter(reference_url=instance.streaming_link).exists()):
            data['streaming_link'] = None
        return data

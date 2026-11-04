from rest_framework import serializers

from content.models import Content, Episode, MiniSeries
from content.serializers import ContentSerializer
from content.publication import PUBLIC_STATUS, public_eligibility
from .models import SavedItem, WatchProgress


class SavedItemSerializer(serializers.ModelSerializer):
    # Nested with the exact shape of GET /api/content/<id>/, so a client can
    # render a saved item with the same card it uses everywhere else.
    content = ContentSerializer(read_only=True)

    class Meta:
        model = SavedItem
        fields = ['id', 'content', 'created_at']


class SaveContentSerializer(serializers.Serializer):
    content = serializers.PrimaryKeyRelatedField(queryset=Content.objects.filter(**public_eligibility()))


class WatchProgressSerializer(serializers.ModelSerializer):
    content = ContentSerializer(read_only=True)

    class Meta:
        model = WatchProgress
        fields = [
            'id', 'content', 'episode', 'miniseries',
            'position_seconds', 'duration_seconds', 'completed', 'updated_at',
        ]
        read_only_fields = fields


class RecordProgressSerializer(serializers.Serializer):
    content = serializers.PrimaryKeyRelatedField(queryset=Content.objects.filter(**public_eligibility()))
    episode = serializers.PrimaryKeyRelatedField(
        queryset=Episode.objects.filter(**public_eligibility('season__content__')).select_related('season'), required=False, allow_null=True
    )
    miniseries = serializers.PrimaryKeyRelatedField(
        queryset=MiniSeries.objects.filter(**public_eligibility('content__')), required=False, allow_null=True
    )
    position_seconds = serializers.IntegerField(min_value=0)
    duration_seconds = serializers.IntegerField(min_value=0, required=False, default=0)

    def validate(self, attrs):
        content = attrs['content']
        episode = attrs.get('episode')
        miniseries = attrs.get('miniseries')

        if episode and miniseries:
            raise serializers.ValidationError('Select an episode or part, not both.')
        episodic = content.format == 'series' or content.content_type in ('series', 'miniseries')
        if episodic and not (episode or miniseries):
            raise serializers.ValidationError('Episode or part is required for episodic progress.')
        if episode is not None and episode.season.content_id != content.id:
            raise serializers.ValidationError({'episode': ['Episode does not belong to this content.']})
        if miniseries is not None and miniseries.content_id != content.id:
            raise serializers.ValidationError({'miniseries': ['Miniseries part does not belong to this content.']})
        if not episodic and (episode or miniseries):
            raise serializers.ValidationError('Standalone progress cannot select an episode or part.')
        return attrs

from rest_framework import serializers
from .models import Content, Season, Episode, contentadverts

class ContentSerializer(serializers.ModelSerializer):
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
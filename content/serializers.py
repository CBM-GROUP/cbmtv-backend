from rest_framework import serializers
from django.db import transaction
from django.db import IntegrityError
import logging
from common.idempotency import OperationConflict
from .models import Content, Genre, Season, Episode, contentadverts, MiniSeries, HeroSettings
from .publication import can_read_dashboard_content
from .publication import PUBLIC_STATUS
from .media_serializers import PlaybackMediaMixin, PublicMediaMixin
from .taxonomy import CATEGORY_CHOICES, CATEGORY_FLAGS, legacy_classification, legacy_type_for, genre_categories


class GenreSerializer(serializers.ModelSerializer):
    categories = serializers.ListField(
        child=serializers.ChoiceField(choices=CATEGORY_CHOICES), required=False, write_only=True,
    )
    used_count = serializers.SerializerMethodField()

    class Meta:
        model = Genre
        fields = ('id', 'name', 'description', 'is_active', 'parent', 'categories', 'used_count')
        read_only_fields = ('id', 'used_count')

    def get_used_count(self, instance):
        request = self.context.get('request')
        if request and can_read_dashboard_content(request.user) and (
            request.method not in ('GET', 'HEAD', 'OPTIONS') or request.query_params.get('scope') == 'dashboard'
        ):
            return instance.titles.count()
        return instance.titles.filter(status=PUBLIC_STATUS).count()

    def to_representation(self, instance):
        data = super().to_representation(instance)
        data['categories'] = genre_categories(instance)
        return data

    def validate_name(self, value):
        value = value.strip()
        if not value:
            raise serializers.ValidationError('Enter a genre name.')
        duplicate = Genre.objects.filter(name_key=value.casefold())
        if self.instance:
            duplicate = duplicate.exclude(pk=self.instance.pk)
        if duplicate.exists():
            raise serializers.ValidationError('A genre with this name already exists.')
        return value

    def validate(self, attrs):
        categories = attrs.get('categories', genre_categories(self.instance) if self.instance else [])
        if not categories or len(categories) != len(set(categories)):
            raise serializers.ValidationError({'categories': 'Choose one or more distinct categories.'})
        parent = attrs.get('parent', self.instance.parent if self.instance else None)
        if parent:
            if (self.instance and (parent.pk == self.instance.pk or self.instance.children.exists())) or parent.parent_id:
                raise serializers.ValidationError({'parent': 'Only a top-level genre may be a parent; two levels are supported.'})
            if not set(categories).issubset(genre_categories(parent)):
                raise serializers.ValidationError({'categories': 'Subgenre categories must belong to its parent.'})
        if self.instance and self.instance.children.exists():
            for child in self.instance.children.all():
                if not set(genre_categories(child)).issubset(categories):
                    raise serializers.ValidationError({'categories': 'Existing subgenres use a category being removed.'})
        if self.instance and 'categories' in attrs:
            if self.instance.titles.exclude(category__in=categories).exists():
                raise serializers.ValidationError({'categories': 'Existing titles use a category being removed.'})
        return attrs

    def _save_categories(self, genre, categories):
        if categories is not None:
            for category, field in CATEGORY_FLAGS.items():
                setattr(genre, field, category in categories)
        try:
            genre.save()
        except IntegrityError as exc:
            raise serializers.ValidationError({'name': 'A genre with this name already exists.'}) from exc
        return genre

    def create(self, validated_data):
        categories = validated_data.pop('categories')
        return self._save_categories(Genre(**validated_data), categories)

    def update(self, instance, validated_data):
        categories = validated_data.pop('categories', None)
        for field, value in validated_data.items():
            setattr(instance, field, value)
        return self._save_categories(instance, categories)

class ContentSerializer(PlaybackMediaMixin, serializers.ModelSerializer):
    genres = serializers.PrimaryKeyRelatedField(many=True, queryset=Genre.objects.all(), required=False)
    cast = serializers.ListField(
        child=serializers.CharField(max_length=200, allow_blank=False, trim_whitespace=True),
        required=False,
        max_length=100,
    )

    def validate(self, attrs):
        legacy_type = attrs.get('content_type', self.instance.content_type if self.instance else None)
        category = attrs.get('category', self.instance.category if self.instance else None)
        content_format = attrs.get('format', self.instance.format if self.instance else None)
        if self.instance and attrs.get('status') == 'approved' and self.instance.status != 'approved' and not category:
            # Old clients may approve an unambiguously typed legacy title.
            inferred_category, inferred_format = legacy_classification(legacy_type)
            if inferred_category:
                category = inferred_category
                content_format = content_format or inferred_format
                attrs.setdefault('category', category)
                attrs.setdefault('format', content_format)
        if not self.instance and legacy_type:
            inferred_category, inferred_format = legacy_classification(legacy_type)
            category = category or inferred_category
            content_format = content_format or inferred_format
            attrs.setdefault('category', category)
            attrs.setdefault('format', content_format)
            expected_type = legacy_type_for(category, content_format) if category and content_format else None
            if expected_type and legacy_type != expected_type and not (legacy_type == 'miniseries' and content_format == 'series'):
                raise serializers.ValidationError({'content_type': 'Legacy type conflicts with category and format.'})
        if not self.instance and not legacy_type:
            if not category or not content_format:
                raise serializers.ValidationError({'category': 'Select category and format for a new title.'})
            attrs['content_type'] = legacy_type_for(category, content_format)
        if category == 'movies' and content_format not in (None, 'standalone'):
            raise serializers.ValidationError({'format': 'Movies must be standalone.'})
        if self.instance and content_format == 'standalone' and self.instance.format != 'standalone':
            if self.instance.seasons.exists() or self.instance.miniseries.exists():
                raise serializers.ValidationError({'format': 'Remove or retain existing episodes before changing to standalone.'})
        if self.instance and attrs.get('status') == 'approved' and self.instance.status != 'approved':
            if not category or not content_format:
                raise serializers.ValidationError({'category': 'Classify this title before approval.'})
        if self.instance and self.instance.status == 'approved' and ('category' in attrs or 'format' in attrs):
            if not category or not content_format:
                raise serializers.ValidationError({'category': 'Published titles must retain category and format.'})
        if self.instance and self.instance.content_type != 'miniseries' and ('category' in attrs or 'format' in attrs):
            if category and content_format and 'content_type' not in attrs:
                attrs['content_type'] = legacy_type_for(category, content_format)
        if self.instance and category and content_format and 'content_type' in attrs:
            expected_type = legacy_type_for(category, content_format)
            if attrs['content_type'] != expected_type and not (self.instance.content_type == attrs['content_type'] == 'miniseries' and content_format == 'series'):
                raise serializers.ValidationError({'content_type': 'Legacy type conflicts with category and format.'})
        assigned = attrs.get('genres')
        if assigned is not None or (self.instance and 'category' in attrs):
            assigned = assigned if assigned is not None else list(self.instance.genres.all())
            if assigned and not category:
                raise serializers.ValidationError({'category': 'Select a category before assigning genres.'})
            existing_ids = set(self.instance.genres.values_list('pk', flat=True)) if self.instance else set()
            for genre in assigned:
                if category not in genre_categories(genre):
                    raise serializers.ValidationError({'genres': f'{genre.name} does not apply to this category.'})
                if not genre.is_active and genre.pk not in existing_ids:
                    raise serializers.ValidationError({'genres': f'{genre.name} is inactive.'})
        return attrs

    def update(self, instance, validated_data):
        # Reload under the lock: DRF may have fetched this instance before an
        # overlapping editorial transition committed. Never save stale status.
        from channel.models import Channel

        class ParentOwnershipChanged(Exception):
            pass

        for _ in range(3):
            try:
                with transaction.atomic():
                    # A reassignment can commit while this writer waits on the
                    # old channel. Reject that row before acquiring another
                    # parent lock after the child lock (activation locks parent
                    # first). PostgreSQL rechecks this predicate after waiting.
                    channel_ids = set(Content.objects.filter(pk=instance.pk).values_list('channel_id', flat=True))
                    if 'channel' in validated_data:
                        channel_ids.add(validated_data['channel'].pk)
                    list(Channel.objects.select_for_update().filter(pk__in=channel_ids).order_by('pk'))
                    current = Content.objects.select_for_update().filter(pk=instance.pk, channel_id__in=channel_ids).first()
                    if current is None:
                        if not Content.objects.filter(pk=instance.pk).exists():
                            raise Content.DoesNotExist()
                        # Roll back the attempt's savepoint to release its locks.
                        raise ParentOwnershipChanged()
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
            except ParentOwnershipChanged:
                continue
        raise OperationConflict('Content channel changed repeatedly. Refresh before trying again.')

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
        extra_kwargs = {'content_type': {'required': False}}

class SeasonSerializer(PublicMediaMixin, serializers.ModelSerializer):
    class Meta:
        model = Season
        fields = '__all__'

    def validate(self, attrs):
        content = attrs.get('content') or getattr(self.instance, 'content', None)
        if content and content.format != 'series' and content.content_type != 'series':
            raise serializers.ValidationError('Seasons require series format.')
        if content and Season.objects.filter(content=content, season_number=attrs.get('season_number', getattr(self.instance, 'season_number', None))).exclude(pk=getattr(self.instance, 'pk', None)).exists():
            raise serializers.ValidationError({'season_number': 'This season number already exists for the title.'})
        return attrs

    def _save_unique(self, instance, values):
        with transaction.atomic():
            content = values.get('content', instance.content if instance else None)
            Content.objects.select_for_update().get(pk=content.pk)
            number = values.get('season_number', instance.season_number if instance else None)
            if Season.objects.filter(content=content, season_number=number).exclude(pk=getattr(instance, 'pk', None)).exists():
                raise serializers.ValidationError({'season_number': 'This season number already exists for the title.'})
            return super().update(instance, values) if instance else super().create(values)

    def create(self, validated_data):
        return self._save_unique(None, validated_data)

    def update(self, instance, validated_data):
        return self._save_unique(instance, validated_data)

class EpisodeSerializer(PlaybackMediaMixin, serializers.ModelSerializer):
    class Meta:
        model = Episode
        fields = '__all__'

    def validate(self, attrs):
        season = attrs.get('season') or getattr(self.instance, 'season', None)
        if season and season.content.format != 'series' and season.content.content_type != 'series':
            raise serializers.ValidationError('Episodes require a title with series format.')
        if season and Episode.objects.filter(season=season, episode_number=attrs.get('episode_number', getattr(self.instance, 'episode_number', None))).exclude(pk=getattr(self.instance, 'pk', None)).exists():
            raise serializers.ValidationError({'episode_number': 'This episode number already exists in the season.'})
        return attrs

    def _save_unique(self, instance, values):
        with transaction.atomic():
            season = values.get('season', instance.season if instance else None)
            Season.objects.select_for_update().get(pk=season.pk)
            number = values.get('episode_number', instance.episode_number if instance else None)
            if Episode.objects.filter(season=season, episode_number=number).exclude(pk=getattr(instance, 'pk', None)).exists():
                raise serializers.ValidationError({'episode_number': 'This episode number already exists in the season.'})
            return super().update(instance, values) if instance else super().create(values)

    def create(self, validated_data):
        return self._save_unique(None, validated_data)

    def update(self, instance, validated_data):
        return self._save_unique(instance, validated_data)


class ContentAdvertSerializer(PublicMediaMixin, serializers.ModelSerializer):
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


class MiniSeriesSerializer(PlaybackMediaMixin, serializers.ModelSerializer):
    class Meta:
        model = MiniSeries
        fields = '__all__'

    def validate(self, attrs):
        content = attrs.get('content') or getattr(self.instance, 'content', None)
        if content and content.content_type != 'miniseries':
            raise serializers.ValidationError('MiniSeries can only be created for content of type "miniseries".')
        return attrs

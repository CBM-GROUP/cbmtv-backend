"""Effective homepage data. Preview and public reads share eligibility rules."""
from calendar import monthrange
from datetime import timedelta
from django.db.models import OuterRef, Q, Subquery
from django.utils import timezone
from channel.models import Channel
from channel.serializers import ChannelSerializer
from content.models import Content, Episode, MiniSeries
from content.publication import public_eligibility
from content.serializers import ContentSerializer
from content.taxonomy import CATEGORY_FLAGS
from library.models import WatchProgress
from library.serializers import WatchProgressSerializer
from .defaults import SECTION_LABELS, default_sections
from .models import FeaturedChannel, FeaturedTitle, HomepageConfiguration


def configuration():
    config = HomepageConfiguration.objects.filter(pk=1).first() or HomepageConfiguration()
    return {
        'sections': config.sections,
        'featured_titles': list(FeaturedTitle.objects.select_related('content', 'content__channel').prefetch_related('content__genres')),
        'featured_channels': list(FeaturedChannel.objects.select_related('channel')),
        'new_releases': {'duration': config.release_duration, 'unit': config.release_unit},
    }


def release_cutoff(now, duration, unit):
    if unit in ('hours', 'days'):
        return now - timedelta(**{unit: duration})
    months = duration * (12 if unit == 'years' else 1)
    absolute_month = now.year * 12 + now.month - 1 - months
    year, month = divmod(absolute_month, 12)
    month += 1
    return now.replace(year=year, month=month, day=min(now.day, monthrange(year, month)[1]))


def build_homepage(config, user=None, now=None):
    now = now or timezone.now()
    public = Content.objects.filter(**public_eligibility()).prefetch_related('genres')
    sections = []
    for section in config['sections'] or default_sections():
        key = section['id']
        source = 'editorial' if key == 'featured_hero' else (
            'personalized' if key == 'continue_watching' else 'automatic'
        )
        row = {**section, 'label': SECTION_LABELS[key], 'source': source, 'items': []}
        sections.append(row)
        if not section['enabled']:
            continue
        if key == 'featured_hero':
            placements = config['featured_titles']
            def field(item, name):
                return item.get(name) if isinstance(item, dict) else getattr(item, name)
            ids = [field(p, 'content').pk for p in placements if
                   (not field(p, 'start_at') or field(p, 'start_at') <= now) and
                   (not field(p, 'end_at') or field(p, 'end_at') > now)]
            eligible = {title.pk: title for title in public.filter(pk__in=ids)}
            titles = [eligible[pk] for pk in ids if pk in eligible]
            # Configured schedules must not be bypassed by a fallback selection.
            if not placements:
                titles = list(public.filter(is_featured=True).order_by('-id')[:6])
            row['items'] = ContentSerializer(titles, many=True).data
        elif key == 'featured_channels':
            channels = Channel.objects.filter(is_active=True).order_by('name', 'pk')
            row['items'] = ChannelSerializer(channels, many=True).data
        elif key == 'continue_watching':
            if user and user.is_authenticated:
                next_episode = Episode.objects.filter(
                    season__content_id=OuterRef('content_id'),
                ).filter(
                    Q(season__season_number__gt=OuterRef('episode__season__season_number')) |
                    Q(season__season_number=OuterRef('episode__season__season_number'), episode_number__gt=OuterRef('episode__episode_number'))
                ).exclude(streaming_link__isnull=True).exclude(streaming_link='').order_by('season__season_number', 'episode_number', 'pk')
                next_part = MiniSeries.objects.filter(
                    content_id=OuterRef('content_id'), miniseries_no__gt=OuterRef('miniseries__miniseries_no'),
                ).exclude(streaming_link__isnull=True).exclude(streaming_link='').order_by('miniseries_no', 'pk')
                progress = WatchProgress.objects.filter(
                    user=user, position_seconds__gt=0,
                    **public_eligibility('content__'),
                ).annotate(
                    next_episode_id=Subquery(next_episode.values('pk')[:1]),
                    next_part_id=Subquery(next_part.values('pk')[:1]),
                ).filter(
                    Q(completed=False) | Q(next_episode_id__isnull=False) | Q(next_part_id__isnull=False),
                ).select_related('content').prefetch_related('content__genres')[:12]
                # A completed episode is not a completed series. Project the next
                # playable part without rewriting the existing progress records.
                for item in progress:
                    data = WatchProgressSerializer(item).data
                    if item.completed:
                        data['episode' if item.episode_id else 'miniseries'] = item.next_episode_id or item.next_part_id
                        data.update(position_seconds=0, duration_seconds=0, completed=False)
                    row['items'].append(data)
        elif key == 'new_releases':
            cutoff = release_cutoff(now, **config['new_releases'])
            titles = public.filter(first_published_at__gte=cutoff, first_published_at__lte=now).order_by('-first_published_at', '-id')[:6]
            row['items'] = ContentSerializer(titles, many=True).data
        elif key in CATEGORY_FLAGS:
            row['items'] = ContentSerializer(public.filter(category=key).order_by('-id')[:12], many=True).data
    return {'sections': sections}

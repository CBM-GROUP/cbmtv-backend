"""Small deterministic metadata recommender using existing viewer/library facts."""
from collections import Counter
from django.db.models import Q
from content.models import Content
from content.publication import public_eligibility
from content.serializers import ContentSerializer
from library.models import SavedItem, WatchProgress, PartProgress
from .rankings import content_metrics, freshness


def _genre_ids(content):
    return {genre.pk for genre in content.genres.all() if genre.is_active}


def _cast(content):
    return {str(name).casefold() for name in (content.cast or []) if isinstance(name, str)} if isinstance(content.cast, list) else set()


def _choose(scored, limit, channel_cap=3, category_cap=5):
    chosen, channels, categories = [], Counter(), Counter()
    for _, item in sorted(scored, key=lambda row: (-row[0], -row[1].created_at.timestamp(), row[1].pk)):
        category = item.category or item.content_type
        if channels[item.channel_id] >= channel_cap or categories[category] >= category_cap:
            continue
        chosen.append(item)
        channels[item.channel_id] += 1
        categories[category] += 1
        if len(chosen) >= limit:
            break
    return chosen


def similar(content, limit=12):
    public = Content.objects.filter(**public_eligibility())
    genre_ids = _genre_ids(content)
    predicate = Q(category=content.category) | Q(channel_id=content.channel_id)
    if genre_ids:
        predicate |= Q(genres__in=genre_ids)
    if content.director:
        predicate |= Q(director__iexact=content.director)
    candidates = public.exclude(pk=content.pk).filter(predicate).distinct().prefetch_related('genres').order_by('-created_at', '-pk')[:500]
    cast = _cast(content)
    scored = []
    for item in candidates:
        overlap = len(genre_ids & _genre_ids(item))
        score = overlap * 5 + (3 if item.category == content.category else 0)
        score += 3 if content.director and item.director and item.director.casefold() == content.director.casefold() else 0
        score += min(3, len(cast & _cast(item)))
        score += 1 if item.channel_id == content.channel_id else 0
        score += 1 if item.format == content.format else 0
        if score > 0:
            scored.append((score, item))
    return _choose(scored, limit, channel_cap=4, category_cap=limit)


def recommended(user, limit=12):
    watched_ids = set(WatchProgress.objects.filter(user=user).values_list('content_id', flat=True))
    watched_ids.update(PartProgress.objects.filter(user=user).values_list('content_id', flat=True))
    saved_ids = set(SavedItem.objects.filter(user=user).values_list('content_id', flat=True))
    seed_ids = watched_ids | saved_ids
    public = Content.objects.filter(**public_eligibility())
    seeds = list(public.filter(pk__in=seed_ids).prefetch_related('genres')[:100])
    if not seeds:
        popular_ids = list(content_metrics('30d', 'most_watched').values_list('content_id', flat=True)[:limit])
        by_id = {item.pk: item for item in public.filter(pk__in=popular_ids).prefetch_related('genres')}
        fallback = [by_id[pk] for pk in popular_ids if pk in by_id]
        if not fallback:
            fallback = list(public.order_by('-first_published_at', '-pk').prefetch_related('genres')[:limit])
        return {'personalized': False, 'reason': 'cold_start', 'items': ContentSerializer(fallback, many=True).data, **freshness()}
    category, genre, channel = Counter(), Counter(), Counter()
    for seed in seeds:
        weight = 2 if seed.pk in watched_ids else 1
        category[seed.category or seed.content_type] += weight
        channel[seed.channel_id] += weight
        for pk in _genre_ids(seed):
            genre[pk] += weight
    candidates = public.exclude(pk__in=seed_ids).prefetch_related('genres').order_by('-created_at', '-pk')[:500]
    scored = []
    for item in candidates:
        score = category[item.category or item.content_type] * 2 + channel[item.channel_id]
        score += sum(genre[pk] * 3 for pk in _genre_ids(item))
        if score > 0:
            scored.append((score, item))
    return {'personalized': True, 'reason': 'watch_and_saved_history',
        'items': ContentSerializer(_choose(scored, limit), many=True).data, **freshness()}

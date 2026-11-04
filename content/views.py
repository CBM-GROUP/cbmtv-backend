from rest_framework import viewsets
from .models import Content, Genre, Season, Episode, contentadverts, MiniSeries, HeroSettings
from .serializers import ContentSerializer, GenreSerializer, SeasonSerializer, EpisodeSerializer, ContentAdvertSerializer, MiniSeriesSerializer, HeroSettingsSerializer
from rest_framework import generics
from rest_framework import permissions
from rest_framework.response import Response
from rest_framework.decorators import api_view, permission_classes
from rest_framework.views import APIView
from rest_framework.exceptions import ValidationError
from django.conf import settings
from django.http import JsonResponse
from django.db.models import Q
from django.db import transaction
import os
import logging
from common.media_storage import MediaStorageError, create_upload_target
from common.pagination import StandardPagination
from accounts.permissions import HasDashboardSection
from common.idempotency import IdempotentCreateMixin
from .publication import PUBLIC_STATUS, public_eligibility, HasDashboardMediaAccess, dashboard_scope, visible_queryset
from .search_sync import SearchUnavailable, get_search_index, content_document, upsert_content, remove_content
from .search_reindex import rebuild_search_index as swap_search_index
from .taxonomy import CATEGORY_CHOICES, FORMAT_CHOICES, CATEGORY_FLAGS
from discovery.receipts import make_receipt


class MediaUploadTargetView(APIView):
    permission_classes = [HasDashboardMediaAccess]

    def post(self, request):
        try:
            target = create_upload_target(
                filename=request.data.get('filename'),
                content_type=request.data.get('content_type'),
                media_type=request.data.get('media_type'),
                # Lets the backend size the URL's validity window to the file
                # instead of applying one flat TTL to every upload.
                size_bytes=request.data.get('size_bytes'),
            )
        except MediaStorageError as exc:
            return Response({'error': str(exc)}, status=400)
        return Response(target, status=200)


def build_search_documents():
    """
    The rows pushed into the Meilisearch index.

    The previous version emitted `genres` from `content.genres.all()` guarded by
    `hasattr(content, 'genres')`. Content has no such relation -- it has a
    `genre` CharField -- so the guard was always False and every document was
    indexed with an empty `genres: []`. Indexing the real field makes genre
    searchable for the first time.

    `description` is indexed too, so a plot word can find a title. Only fields
    worth *matching* on belong here: everything the client renders is read back
    from the database in `search_view`, never from the index.
    """
    return [content_document(content) for content in Content.objects.filter(**public_eligibility()).select_related('channel').prefetch_related('genres__parent').order_by('id')]


def rebuild_search_index():
    """Rebuild with a verified shadow index; retain the old index for rollback."""
    return swap_search_index(build_search_documents())


def _hit_ids(raw_hits):
    """
    The content ids Meilisearch ranked, in rank order.

    Anything without a coercible integer id is skipped rather than raising --
    the index is external state and may hold documents from an older schema.
    """
    ids = []
    seen = set()
    for hit in raw_hits:
        if not isinstance(hit, dict):
            continue
        try:
            content_id = int(hit.get('id'))
        except (TypeError, ValueError):
            continue
        if content_id > 0 and content_id not in seen:
            seen.add(content_id)
            ids.append(content_id)
    return ids


def _rank_ordered(ids):
    """
    Fetch Content rows for `ids`, preserving the order Meilisearch ranked them.

    One query plus a dict lookup, not a query per hit. Ids the database no
    longer holds simply fall out, which is what turns a stale index into fewer
    results instead of links to the wrong programme.
    """
    rows = Content.objects.filter(**public_eligibility()).in_bulk(ids)
    return [rows[i] for i in ids if i in rows]


SEARCH_FIELDS = {
    'title': ('title',),
    'director': ('director',),
    'genre': ('genre', 'genres__name', 'genres__parent__name'),
    'cast': ('cast',),
    'channel': ('channel__name',),
    'everything': ('title', 'director', 'cast', 'genre', 'genres__name',
                   'genres__parent__name', 'channel__name', 'description',
                   'writer', 'country', 'content_type', 'category', 'format'),
}

INDEX_FIELDS = {
    'title': ['title'], 'director': ['director'], 'genre': ['genre', 'genres'],
    'cast': ['cast'], 'channel': ['channel'],
    'everything': ['title', 'director', 'cast', 'genres', 'genre', 'channel',
                   'description', 'writer', 'country', 'content_type',
                   'category', 'format'],
}


def _database_matches(query, search_by='title'):
    """Authoritative public matches, including records absent from the index.

    Each query word may match a different field within the selected search mode.
    Keep this as a queryset so continuation pages do not materialize the catalogue.
    """
    fields = SEARCH_FIELDS[search_by]
    aliases = {
        'movies': 'movies', 'movie': 'movies',
        'tv shows': 'tv_shows', 'tv show': 'tv_shows',
        'documentaries': 'documentaries', 'documentary': 'documentaries',
        'animations': 'animations', 'animation': 'animations',
    }
    old_types = {'movies': 'movie', 'documentaries': 'documentary', 'animations': 'animations'}
    def category_match(key):
        match = Q(category=key)
        if key in old_types:
            match |= Q(category__isnull=True, content_type=old_types[key])
        return match
    predicate = Q()
    for term in query.split():
        term_match = Q()
        for field in fields:
            match = Q(**{f'{field}__icontains': term})
            if field.startswith('genres__'):
                match &= Q(genres__is_active=True)
                if field.startswith('genres__parent__'):
                    match &= Q(genres__parent__is_active=True)
            term_match |= match
        if search_by == 'everything' and term.lower() in aliases:
            term_match |= category_match(aliases[term.lower()])
        predicate &= term_match
    category = aliases.get(query.lower())
    if search_by == 'everything' and category:
        predicate |= category_match(category)
    elif search_by == 'everything' and query.lower() == 'series':
        predicate |= Q(format='series') | Q(content_type__in=('series', 'miniseries'))
    return Content.objects.filter(**public_eligibility()).filter(predicate).distinct().order_by('-created_at', '-id')


@api_view(['GET'])
@permission_classes([permissions.AllowAny])
def categories_view(request):
    return Response([{'id': key, 'label': label} for key, label in CATEGORY_CHOICES])


class GenreListCreateView(generics.ListCreateAPIView):
    required_section = 'content'
    serializer_class = GenreSerializer
    pagination_class = StandardPagination

    def get_queryset(self):
        queryset = Genre.objects.all().order_by('name_key', 'pk')
        if not dashboard_scope(self.request):
            queryset = queryset.filter(is_active=True)
        category = self.request.query_params.get('category')
        if category:
            if category not in CATEGORY_FLAGS:
                raise ValidationError({'category': 'Choose one of the four primary categories.'})
            queryset = queryset.filter(**{CATEGORY_FLAGS[category]: True})
        return queryset

    def get_permissions(self):
        return [permissions.AllowAny()] if self.request.method in ('GET', 'HEAD', 'OPTIONS') else [HasDashboardSection()]


class GenreDetailView(generics.RetrieveUpdateAPIView):
    required_section = 'content'
    serializer_class = GenreSerializer

    def get_queryset(self):
        queryset = Genre.objects.all()
        if self.request.method not in ('GET', 'HEAD', 'OPTIONS') or dashboard_scope(self.request):
            return queryset
        return queryset.filter(is_active=True)

    def perform_update(self, serializer):
        genre = serializer.save()
        ids = list(Content.objects.filter(Q(genres=genre) | Q(genres__parent=genre)).distinct().values_list('pk', flat=True))
        transaction.on_commit(lambda: [upsert_content(pk) for pk in ids])

    def get_permissions(self):
        return [permissions.AllowAny()] if self.request.method in ('GET', 'HEAD', 'OPTIONS') else [HasDashboardSection()]


@api_view(['GET'])
@permission_classes([permissions.AllowAny])
def search_view(request):
    """
    Read-only search over the content catalogue.

    GET /api/content/search/?q=<term>&limit=<page size>&offset=<continuation>

    Meilisearch ranks; the database decides what actually comes back. Hits are
    hydrated by id through `_rank_ordered`, so an index holding rows that no
    longer exist yields fewer results rather than results pointing at whatever
    record now owns that id. The production index did exactly that -- six
    documents from an unrelated dataset, one of which resolved to a different
    programme entirely.

    Current database matches always supplement the ranked index IDs. Exact titles
    come first, then Meilisearch's order, then additional public metadata matches.
    Index outages still leave the complete database matching path available.
    `count` remains the returned page size for existing callers; `total` and
    `next_offset` allow the existing popup to reach further pages. No index writes
    or new publication/access rules are performed by this read endpoint.

    This endpoint previously DELETED the entire index and rebuilt it from the
    database, on an unauthenticated GET, despite being named `search`. Any
    crawler, prefetch or accidental navigation wiped search for everyone. The
    rebuild now lives behind POST /api/content/reindex/ (admin only); this route
    does what its name says and never writes.
    """
    query = (request.query_params.get('q') or '').strip()
    search_by = (request.query_params.get('search_by') or 'title').strip().lower()
    if search_by not in SEARCH_FIELDS:
        return Response({'search_by': f'Choose one of: {", ".join(SEARCH_FIELDS)}.'}, status=400)
    if len(query) > 200:
        return Response({'q': 'Use at most 200 characters.'}, status=400)
    if not query:
        return Response({'query': '', 'count': 0, 'hits': [], 'source': 'none',
                         'total': 0, 'offset': 0, 'next_offset': None, 'search_by': search_by})

    try:
        limit = int(request.query_params.get('limit') or 20)
    except (TypeError, ValueError):
        limit = 20
    limit = max(1, min(limit, 100))
    try:
        offset = int(request.query_params.get('offset') or 0)
        if offset < 0:
            raise ValueError
    except (TypeError, ValueError):
        return Response({'offset': 'Use a non-negative integer.'}, status=400)

    published = Content.objects.filter(**public_eligibility())
    category = request.query_params.get('category')
    if category:
        if category not in dict(CATEGORY_CHOICES):
            return Response({'category': 'Choose one of the four primary categories.'}, status=400)
        published = published.filter(category=category)
    channel = request.query_params.get('channel')
    if channel:
        if not channel.isdigit() or int(channel) < 1:
            return Response({'channel': 'Use a positive channel ID.'}, status=400)
        published = published.filter(channel_id=int(channel))
    ranked_ids = []

    try:
        index = get_search_index()
        # Request ranked IDs across this catalogue, not just the visible page.
        # Missing/stale index records cannot cap the database match set below.
        result = index.search(query, {
            'limit': max(limit, min(offset + limit, 1000)),
            'attributesToRetrieve': ['id'],
            'attributesToSearchOn': INDEX_FIELDS[search_by],
        })
        raw_hits = result.get('hits', []) if isinstance(result, dict) else []
        ids = _hit_ids(raw_hits)
        if search_by == 'everything':
            # Keep Meilisearch typo tolerance for broad discovery. Publication
            # still comes from the current database, never the index document.
            eligible = set(published.filter(pk__in=ids).values_list('pk', flat=True))
        else:
            # A stale index must not turn a director/cast/genre/channel match
            # into a hit whose current metadata only mentions the query elsewhere.
            matching = _database_matches(query, search_by).filter(pk__in=ids)
            eligible = set(published.filter(pk__in=matching.values('pk')).values_list('pk', flat=True))
        ranked_ids = [content_id for content_id in ids if content_id in eligible]
    except SearchUnavailable:
        pass
    except Exception:
        # Unreachable or erroring Meilisearch. Search degrades; it does not 502.
        pass

    # An exact title must remain findable even when the index returns weaker
    # matches. Supplement partial index responses, not only completely empty ones.
    exact_ids = list(published.filter(title__iexact=query).order_by('-created_at', '-id').values_list('pk', flat=True)) if search_by in ('title', 'everything') else []
    head_ids = list(dict.fromkeys(exact_ids + ranked_ids))
    remaining = _database_matches(query, search_by).filter(pk__in=published.values('pk')).exclude(pk__in=head_ids)
    total = len(head_ids) + remaining.count()
    rows = []
    if offset < total:
        rows = _rank_ordered(head_ids[offset:offset + limit])
        if len(rows) < limit:
            tail_offset = max(0, offset - len(head_ids))
            rows.extend(remaining[tail_offset:tail_offset + limit - len(rows)])

    hits = ContentSerializer(rows, many=True).data
    return Response({
        'query': query,
        'search_by': search_by,
        'count': len(hits),
        'hits': hits,
        'source': 'meilisearch' if ranked_ids else 'database',
        'total': total,
        'offset': offset,
        'next_offset': offset + len(hits) if offset + len(hits) < total else None,
        'receipt': make_receipt(query, search_by, total, [row.pk for row in rows]),
    })


class ReindexView(APIView):
    required_section = 'content'
    """
    Rebuild the Meilisearch index from the database.

    POST /api/content/reindex/ -- admin only. This is the administrative half of
    the old `search_view`, now behind a write method and a permission check.
    """
    permission_classes = [HasDashboardSection]

    def post(self, request):
        try:
            result = rebuild_search_index()
        except SearchUnavailable as exc:
            return Response({'status': 'error', 'message': str(exc)}, status=503)
        except Exception:
            logging.getLogger(__name__).exception('Search reindex failed')
            return Response(
                {'status': 'error', 'message': 'Reindex failed. Check the server logs.'}, status=502
            )

        return Response({
            'status': 'success',
            'indexed': result['indexed'],
            'backup_index': result['backup_index'],
            # The dashboard's "Sync Search Data" button alerts response.data.message.
            'message': f'Indexed {result["indexed"]} documents to Meilisearch',
        })

@api_view(['GET'])
def content_ids(request):
    """Return content IDs with optional filtering by content type"""
    content_type = request.GET.get('type', None)
    queryset = Content.objects.all() if dashboard_scope(request) else Content.objects.filter(**public_eligibility())
    
    # Get all available content types from the model choices
    available_types = [choice[0] for choice in Content.CONTENT_TYPES]
    
    if content_type:
        # Validate content type
        if content_type not in available_types:
            return Response({
                'error': f'Invalid content type. Available types: {available_types}'
            }, status=400)
        
        # Filter by specific content type
        content_ids = list(queryset.filter(content_type=content_type).values_list('id', flat=True))
        return Response({
            'content_type': content_type,
            'content_ids': content_ids,
            'count': len(content_ids)
        })
    else:
        # Return all content IDs grouped by content type
        result = {}
        for content_type in available_types:
            ids = list(queryset.filter(content_type=content_type).values_list('id', flat=True))
            result[content_type] = {
                'ids': ids,
                'count': len(ids)
            }
        
        # Also include total count
        total_ids = list(queryset.values_list('id', flat=True))
        result['total'] = {
            'ids': total_ids,
            'count': len(total_ids)
        }
        
        return Response(result)

TRUE_VALUES = ('true', '1', 'yes')
FALSE_VALUES = ('false', '0', 'no')


def parse_bool_param(name, value):
    """Parse a boolean query parameter, rejecting anything ambiguous with a 400."""
    normalized = str(value).strip('/').strip().lower()
    if normalized in TRUE_VALUES:
        return True
    if normalized in FALSE_VALUES:
        return False
    raise ValidationError({
        name: f"Invalid value '{value}'. Use true or false."
    })


#list all content and create new content
class ContentListCreateView(IdempotentCreateMixin, generics.ListCreateAPIView):
    required_section = 'content'
    queryset = Content.objects.all()
    serializer_class = ContentSerializer
    pagination_class = StandardPagination

    def perform_create(self, serializer):
        content = serializer.save()
        transaction.on_commit(lambda content_id=content.id: upsert_content(content_id))
    
    def get_queryset(self):
        """
        Supports ?channel=, ?content_type=, ?genre=, ?featured= and ?trending=,
        composably.

        Only `channel` used to be read here. `?content_type=series` -- which the
        stream app's API_Routes.listSeries relies on, and which the mobile home
        screen needs for its category rails -- was silently ignored and returned
        the entire catalogue.
        """
        # Ordered explicitly: PageNumberPagination slices this queryset, and
        # slicing an unordered one lets the database return a different order per
        # page, so rows repeat or vanish between pages. Django warns about this
        # (UnorderedObjectListWarning) rather than failing.
        queryset = visible_queryset(self.request, Content.objects.all()).order_by('-id')

        channel_id = self.request.query_params.get('channel')
        if channel_id:
            # Be tolerant to trailing slashes like ?channel=5/
            try:
                queryset = queryset.filter(channel_id=int(str(channel_id).strip('/')))
            except (TypeError, ValueError):
                return Content.objects.none()

        content_type = self.request.query_params.get('content_type')
        if content_type:
            content_type = str(content_type).strip('/')
            valid_types = [choice[0] for choice in Content.CONTENT_TYPES]
            if content_type not in valid_types:
                raise ValidationError({
                    'content_type': (
                        f"Invalid content type '{content_type}'. "
                        f"Valid types: {', '.join(valid_types)}."
                    )
                })
            queryset = queryset.filter(content_type=content_type)

        category = self.request.query_params.get('category')
        if category:
            if category not in dict(CATEGORY_CHOICES):
                raise ValidationError({'category': 'Choose one of the four primary categories.'})
            queryset = queryset.filter(category=category)

        # DRF reserves ?format= for renderer negotiation, so use content_format.
        content_format = self.request.query_params.get('content_format')
        if content_format:
            if content_format not in dict(FORMAT_CHOICES):
                raise ValidationError({'content_format': 'Choose standalone or series.'})
            queryset = queryset.filter(format=content_format)

        genre_id = self.request.query_params.get('genre_id')
        if genre_id:
            if not str(genre_id).isdigit():
                raise ValidationError({'genre_id': 'Use a positive genre ID.'})
            queryset = queryset.filter(genres__id=int(genre_id))

        genre = self.request.query_params.get('genre')
        if genre:
            # Genre is a free-text CharField holding values like "Short Movie",
            # so match loosely rather than requiring an exact string.
            term = str(genre).strip('/')
            queryset = queryset.filter(Q(genre__icontains=term) | Q(genres__name__icontains=term))

        for param, field in (('featured', 'is_featured'), ('trending', 'is_trending')):
            value = self.request.query_params.get(param)
            if value:
                queryset = queryset.filter(**{field: parse_bool_param(param, value)})

        return queryset.distinct()

    def get_permissions(self):
        if self.request.method in ('GET', 'HEAD', 'OPTIONS'):
            return [permissions.AllowAny()]
        return [HasDashboardSection()]

#get, update, delete content
class ContentDetailView(generics.RetrieveUpdateDestroyAPIView):
    required_section = 'content'
    queryset = Content.objects.all()
    serializer_class = ContentSerializer

    def get_queryset(self):
        return visible_queryset(self.request, Content.objects.all())

    def perform_update(self, serializer):
        content = serializer.save()
        transaction.on_commit(lambda content_id=content.id: upsert_content(content_id))

    def perform_destroy(self, instance):
        content_id = instance.id
        instance.delete()
        transaction.on_commit(lambda: remove_content(content_id))
    
    def get_permissions(self):
        if self.request.method in ('GET', 'HEAD', 'OPTIONS'):
            return [permissions.AllowAny()]
        return [HasDashboardSection()]
    

#movies: creat only
class MovieListView(generics.ListAPIView):
    queryset = Content.objects.filter(content_type='movie')
    serializer_class= ContentSerializer
    pagination_class = StandardPagination

    def get_queryset(self):
        # Ordered for the same reason as ContentListCreateView: this view is
        # paginated, and slicing an unordered queryset lets rows repeat or
        # vanish between pages.
        return visible_queryset(self.request, Content.objects.filter(content_type='movie')).order_by('-id')

    def get_permissions(self):
        return [permissions.AllowAny()]

"""Updates for content are handled universally via ContentDetailView at /<id>/ (PUT/PATCH)."""

#create & list seasons
class SeasonListCreateView(generics.ListCreateAPIView):
    required_section = 'content'
    queryset = Season.objects.all()
    serializer_class = SeasonSerializer
    permission_classes = [permissions.IsAuthenticated]
    pagination_class = StandardPagination

    def get_queryset(self):
        queryset = visible_queryset(self.request, Season.objects.all(), 'content__status')
        content_id = self.request.query_params.get('content')
        if content_id:
            # Be tolerant to trailing slashes like ?content=12/
            try:
                cleaned_id = int(str(content_id).strip('/'))
                return queryset.filter(content_id=cleaned_id).order_by('season_number', 'id')
            except (TypeError, ValueError):
                return queryset.none()
        return queryset.order_by('season_number', 'id')
    
    def get_permissions(self):
        if self.request.method in ('GET', 'HEAD', 'OPTIONS'):
            return [permissions.AllowAny()]
        return [HasDashboardSection()]
#retrieve.update,delete single season
class SeasonDetailView(generics.RetrieveUpdateDestroyAPIView):
    required_section = 'content'
    queryset = Season.objects.all()
    serializer_class=SeasonSerializer
    permission_classes = [permissions.IsAuthenticated]

    def get_queryset(self):
        return visible_queryset(self.request, Season.objects.all(), 'content__status')

    def get_permissions(self):
        if self.request.method in ('GET', 'HEAD', 'OPTIONS'):
            return [permissions.AllowAny()]
        return [HasDashboardSection()]

class EpisodeListCreateView(IdempotentCreateMixin, generics.ListCreateAPIView):
    required_section = 'content'
    serializer_class = EpisodeSerializer
    permission_classes = [permissions.IsAuthenticated]
    pagination_class = StandardPagination
    def get_queryset(self):
        queryset = visible_queryset(self.request, Episode.objects.all(), 'season__content__status')
        season_id = self.request.query_params.get('season')
        if season_id:
            # Be tolerant to trailing slashes like ?season=5/
            try:
                cleaned_id = int(str(season_id).strip('/'))
                return queryset.filter(season_id=cleaned_id).order_by('episode_number', 'id')
            except (TypeError, ValueError):
                return queryset.none()
        return queryset.order_by('season_id', 'episode_number', 'id')

    def get_permissions(self):
        if self.request.method in ('GET', 'HEAD', 'OPTIONS'):
            return [permissions.AllowAny()]
        return [HasDashboardSection()]


class EpisodeDetailView(generics.RetrieveUpdateDestroyAPIView):
    required_section = 'content'
    queryset = Episode.objects.all()
    serializer_class = EpisodeSerializer
    permission_classes = [permissions.IsAuthenticated]

    def get_queryset(self):
        return visible_queryset(self.request, Episode.objects.all(), 'season__content__status')

    def get_permissions(self):
        if self.request.method in ('GET', 'HEAD', 'OPTIONS'):
            return [permissions.AllowAny()]
        return [HasDashboardSection()]


# adverts
class HeroSettingsView(APIView):
    required_section = 'settings'
    def get_permissions(self):
        if self.request.method in ('GET', 'HEAD', 'OPTIONS'):
            return [permissions.AllowAny()]
        return [HasDashboardSection()]

    def get(self, request):
        instance = HeroSettings.objects.filter(pk=1).first() or HeroSettings()
        return Response(HeroSettingsSerializer(instance).data)

    def patch(self, request):
        instance, _ = HeroSettings.objects.get_or_create(pk=1)
        serializer = HeroSettingsSerializer(instance, data=request.data, partial=True)
        serializer.is_valid(raise_exception=True)
        serializer.save()
        return Response(serializer.data)


class ContentAdvertListCreateView(IdempotentCreateMixin, generics.ListCreateAPIView):
    required_section = 'adverts'
    queryset = contentadverts.objects.all()
    serializer_class = ContentAdvertSerializer

    def get_permissions(self):
        if self.request.method in ('GET', 'HEAD', 'OPTIONS'):
            return [permissions.AllowAny()]
        return [HasDashboardSection()]


class ContentAdvertDetailView(generics.RetrieveUpdateDestroyAPIView):
    required_section = 'adverts'
    queryset = contentadverts.objects.all()
    serializer_class = ContentAdvertSerializer

    def get_permissions(self):
        if self.request.method in ('GET', 'HEAD', 'OPTIONS'):
            return [permissions.AllowAny()]
        return [HasDashboardSection()]


class MiniSeriesListCreateView(IdempotentCreateMixin, generics.ListCreateAPIView):
    required_section = 'content'
    queryset = MiniSeries.objects.all()
    serializer_class = MiniSeriesSerializer
    permission_classes = [permissions.IsAuthenticated]
    

    def get_queryset(self):
        queryset = visible_queryset(self.request, MiniSeries.objects.all(), 'content__status')
        content_id = self.request.query_params.get('content')
        if content_id:
            try:
                cleaned_id = int(str(content_id).strip('/'))
                return queryset.filter(content_id=cleaned_id)
            except (TypeError, ValueError):
                return queryset.none()
        return queryset

    def get_permissions(self):
        if self.request.method in ('GET', 'HEAD', 'OPTIONS'):
            return [permissions.AllowAny()]
        return [HasDashboardSection()]


class MiniSeriesDetailView(generics.RetrieveUpdateDestroyAPIView):
    required_section = 'content'
    queryset = MiniSeries.objects.all()
    serializer_class = MiniSeriesSerializer
    permission_classes = [permissions.IsAuthenticated]

    def get_queryset(self):
        return visible_queryset(self.request, MiniSeries.objects.all(), 'content__status')

    def get_permissions(self):
        if self.request.method in ('GET', 'HEAD', 'OPTIONS'):
            return [permissions.AllowAny()]
        return [HasDashboardSection()]


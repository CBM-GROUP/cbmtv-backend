from rest_framework import viewsets
from .models import Content, Season, Episode, contentadverts, MiniSeries
from .serializers import ContentSerializer, SeasonSerializer, EpisodeSerializer, ContentAdvertSerializer, MiniSeriesSerializer
from rest_framework import generics
from rest_framework import permissions
from rest_framework.response import Response
from rest_framework.decorators import api_view, permission_classes
from rest_framework.views import APIView
from rest_framework.exceptions import ValidationError
from django.conf import settings
from django.http import JsonResponse
from django.db.models import Q
import os
import meilisearch
from common.media_storage import MediaStorageError, create_upload_target
from common.pagination import StandardPagination


class SearchUnavailable(RuntimeError):
    """Meilisearch is not configured for this deployment."""


def get_search_index():
    """
    Build the Meilisearch index handle on demand.

    This used to be module-level state (`client = meilisearch.Client(...)`,
    `index = client.index('content')`) built from os.getenv at import time, with
    the variable names absent from .env.example. Resolving it per call means a
    deployment without Meilisearch configured fails as a clean 503 from the one
    endpoint that needs it, instead of carrying a misconfigured client around.
    """
    if not settings.MEILISEARCH_URL:
        raise SearchUnavailable(
            'Search is not configured: MEILISEARCH_URL is unset.'
        )
    client = meilisearch.Client(
        settings.MEILISEARCH_URL, settings.MEILISEARCH_MASTER_KEY
    )
    return client.index(settings.MEILISEARCH_INDEX)


class MediaUploadTargetView(APIView):
    permission_classes = [permissions.IsAuthenticated]

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
    return [
        {
            'id': content.id,
            'title': content.title,
            'genre': content.genre or '',
            'content_type': content.content_type or '',
            'description': content.description or '',
        }
        for content in Content.objects.all().order_by('id')
    ]


def rebuild_search_index():
    """
    Drop and repopulate the Meilisearch content index from the database.

    Shared by `POST /api/content/reindex/` and `manage.py reindex_search`, so the
    HTTP route and the CLI cannot drift apart. Raises SearchUnavailable when
    Meilisearch is unconfigured; any other exception is a backend failure and is
    left for the caller to translate.
    """
    index = get_search_index()
    documents = build_search_documents()

    try:
        index.delete()
    except Exception:
        # A missing index is not an error: add_documents recreates it.
        pass

    # Re-resolve after the delete so the handle refers to the new index.
    get_search_index().add_documents(documents)
    return len(documents)


def _hit_ids(raw_hits):
    """
    The content ids Meilisearch ranked, in rank order.

    Anything without a coercible integer id is skipped rather than raising --
    the index is external state and may hold documents from an older schema.
    """
    ids = []
    for hit in raw_hits:
        if not isinstance(hit, dict):
            continue
        try:
            ids.append(int(hit.get('id')))
        except (TypeError, ValueError):
            continue
    return ids


def _rank_ordered(ids):
    """
    Fetch Content rows for `ids`, preserving the order Meilisearch ranked them.

    One query plus a dict lookup, not a query per hit. Ids the database no
    longer holds simply fall out, which is what turns a stale index into fewer
    results instead of links to the wrong programme.
    """
    rows = Content.objects.in_bulk(ids)
    return [rows[i] for i in ids if i in rows]


def _database_search(query, limit):
    """
    Search the database directly.

    The fallback for whenever Meilisearch cannot answer -- unconfigured,
    erroring, or holding an index so stale that nothing it ranked still exists.
    `icontains` is case-insensitive and matches substrings, so partial titles
    work with no index at all.
    """
    return list(
        Content.objects.filter(
            Q(title__icontains=query)
            | Q(genre__icontains=query)
            | Q(description__icontains=query)
        ).order_by('-created_at', '-id')[:limit]
    )


@api_view(['GET'])
@permission_classes([permissions.AllowAny])
def search_view(request):
    """
    Read-only search over the content catalogue.

    GET /api/content/search/?q=<term>&limit=<n>

    Meilisearch ranks; the database decides what actually comes back. Hits are
    hydrated by id through `_rank_ordered`, so an index holding rows that no
    longer exist yields fewer results rather than results pointing at whatever
    record now owns that id. The production index did exactly that -- six
    documents from an unrelated dataset, one of which resolved to a different
    programme entirely.

    When Meilisearch cannot answer -- unconfigured, unreachable, or stale enough
    that nothing it ranked survived hydration -- this falls back to a database
    `icontains` search instead of failing. `source` reports which path answered.

    This endpoint previously DELETED the entire index and rebuilt it from the
    database, on an unauthenticated GET, despite being named `search`. Any
    crawler, prefetch or accidental navigation wiped search for everyone. The
    rebuild now lives behind POST /api/content/reindex/ (admin only); this route
    does what its name says and never writes.
    """
    query = (request.query_params.get('q') or '').strip()
    if not query:
        return Response({'query': '', 'count': 0, 'hits': [], 'source': 'none'})

    try:
        limit = int(request.query_params.get('limit') or 20)
    except (TypeError, ValueError):
        limit = 20
    limit = max(1, min(limit, 100))

    source = 'meilisearch'
    rows = []

    try:
        index = get_search_index()
        result = index.search(query, {'limit': limit})
        raw_hits = result.get('hits', []) if isinstance(result, dict) else []
        rows = _rank_ordered(_hit_ids(raw_hits))
    except SearchUnavailable:
        source = 'database'
    except Exception:
        # Unreachable or erroring Meilisearch. Search degrades; it does not 502.
        source = 'database'

    if not rows:
        # Covers an empty index, an index stale enough that hydration dropped
        # every hit, and a genuinely unmatched query. The database is cheap
        # here and settles all three.
        rows = _database_search(query, limit)
        source = 'database'

    hits = ContentSerializer(rows, many=True).data
    return Response({
        'query': query,
        'count': len(hits),
        'hits': hits,
        'source': source,
    })


class ReindexView(APIView):
    """
    Rebuild the Meilisearch index from the database.

    POST /api/content/reindex/ -- admin only. This is the administrative half of
    the old `search_view`, now behind a write method and a permission check.
    """
    permission_classes = [permissions.IsAdminUser]

    def post(self, request):
        try:
            indexed = rebuild_search_index()
        except SearchUnavailable as exc:
            return Response({'status': 'error', 'message': str(exc)}, status=503)
        except Exception as exc:
            return Response(
                {'status': 'error', 'message': f'Reindex failed: {exc}'}, status=502
            )

        return Response({
            'status': 'success',
            'indexed': indexed,
            # The dashboard's "Sync Search Data" button alerts response.data.message.
            'message': f'Indexed {indexed} documents to Meilisearch',
        })

@api_view(['GET'])
def content_ids(request):
    """Return content IDs with optional filtering by content type"""
    content_type = request.GET.get('type', None)
    
    # Get all available content types from the model choices
    available_types = [choice[0] for choice in Content.CONTENT_TYPES]
    
    if content_type:
        # Validate content type
        if content_type not in available_types:
            return Response({
                'error': f'Invalid content type. Available types: {available_types}'
            }, status=400)
        
        # Filter by specific content type
        content_ids = list(Content.objects.filter(content_type=content_type).values_list('id', flat=True))
        return Response({
            'content_type': content_type,
            'content_ids': content_ids,
            'count': len(content_ids)
        })
    else:
        # Return all content IDs grouped by content type
        result = {}
        for content_type in available_types:
            ids = list(Content.objects.filter(content_type=content_type).values_list('id', flat=True))
            result[content_type] = {
                'ids': ids,
                'count': len(ids)
            }
        
        # Also include total count
        total_ids = list(Content.objects.values_list('id', flat=True))
        result['total'] = {
            'ids': total_ids,
            'count': len(total_ids)
        }
        
        return Response(result)

#list all content and create new content
class ContentListCreateView(generics.ListCreateAPIView):
    queryset = Content.objects.all()
    serializer_class = ContentSerializer
    pagination_class = StandardPagination
    
    def get_queryset(self):
        """
        Supports ?channel=, ?content_type= and ?genre=, composably.

        Only `channel` used to be read here. `?content_type=series` -- which the
        stream app's API_Routes.listSeries relies on, and which the mobile home
        screen needs for its category rails -- was silently ignored and returned
        the entire catalogue.
        """
        # Ordered explicitly: PageNumberPagination slices this queryset, and
        # slicing an unordered one lets the database return a different order per
        # page, so rows repeat or vanish between pages. Django warns about this
        # (UnorderedObjectListWarning) rather than failing.
        queryset = Content.objects.all().order_by('-id')

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

        genre = self.request.query_params.get('genre')
        if genre:
            # Genre is a free-text CharField holding values like "Short Movie",
            # so match loosely rather than requiring an exact string.
            queryset = queryset.filter(genre__icontains=str(genre).strip('/'))

        return queryset

    def get_permissions(self):
        if self.request.method in ('GET', 'HEAD', 'OPTIONS'):
            return [permissions.AllowAny()]
        return [permissions.IsAdminUser()]

#get, update, delete content
class ContentDetailView(generics.RetrieveUpdateDestroyAPIView):
    queryset = Content.objects.all()
    serializer_class = ContentSerializer
    
    def get_permissions(self):
        if self.request.method in ('GET', 'HEAD', 'OPTIONS'):
            return [permissions.AllowAny()]
        return [permissions.IsAdminUser()]
    

#movies: creat only
class MovieListView(generics.ListAPIView):
    queryset = Content.objects.filter(content_type='movie')
    serializer_class= ContentSerializer
    pagination_class = StandardPagination

    def get_queryset(self):
        # Ordered for the same reason as ContentListCreateView: this view is
        # paginated, and slicing an unordered queryset lets rows repeat or
        # vanish between pages.
        return Content.objects.filter(content_type='movie').order_by('-id')

    def get_permissions(self):
        return [permissions.AllowAny()]

"""Updates for content are handled universally via ContentDetailView at /<id>/ (PUT/PATCH)."""

#create & list seasons
class SeasonListCreateView(generics.ListCreateAPIView):
    queryset = Season.objects.all()
    serializer_class = SeasonSerializer
    permission_classes = [permissions.IsAuthenticated]
    pagination_class = StandardPagination

    def get_queryset(self):
        content_id = self.request.query_params.get('content')
        if content_id:
            # Be tolerant to trailing slashes like ?content=12/
            try:
                cleaned_id = int(str(content_id).strip('/'))
                return Season.objects.filter(content_id=cleaned_id).order_by('season_number', 'id')
            except (TypeError, ValueError):
                return Season.objects.none()
        return Season.objects.all().order_by('season_number', 'id')
    
    def get_permissions(self):
        if self.request.method in ('GET', 'HEAD', 'OPTIONS'):
            return [permissions.AllowAny()]
        return [permissions.IsAdminUser()]
#retrieve.update,delete single season
class SeasonDetailView(generics.RetrieveUpdateDestroyAPIView):
    queryset = Season.objects.all()
    serializer_class=SeasonSerializer
    permission_classes = [permissions.IsAuthenticated]

    def get_permissions(self):
        if self.request.method in ('GET', 'HEAD', 'OPTIONS'):
            return [permissions.AllowAny()]
        return [permissions.IsAdminUser()]

class EpisodeListCreateView(generics.ListCreateAPIView):
    serializer_class = EpisodeSerializer
    permission_classes = [permissions.IsAuthenticated]
    pagination_class = StandardPagination
    def get_queryset(self):
        season_id = self.request.query_params.get('season')
        if season_id:
            # Be tolerant to trailing slashes like ?season=5/
            try:
                cleaned_id = int(str(season_id).strip('/'))
                return Episode.objects.filter(season_id=cleaned_id).order_by('episode_number', 'id')
            except (TypeError, ValueError):
                return Episode.objects.none()
        return Episode.objects.all().order_by('season_id', 'episode_number', 'id')

    def get_permissions(self):
        if self.request.method in ('GET', 'HEAD', 'OPTIONS'):
            return [permissions.AllowAny()]
        return [permissions.IsAdminUser()]


class EpisodeDetailView(generics.RetrieveUpdateDestroyAPIView):
    queryset = Episode.objects.all()
    serializer_class = EpisodeSerializer
    permission_classes = [permissions.IsAuthenticated]

    def get_permissions(self):
        if self.request.method in ('GET', 'HEAD', 'OPTIONS'):
            return [permissions.AllowAny()]
        return [permissions.IsAdminUser()]


# adverts
class ContentAdvertListCreateView(generics.ListCreateAPIView):
    queryset = contentadverts.objects.all()
    serializer_class = ContentAdvertSerializer

    def get_permissions(self):
        if self.request.method in ('GET', 'HEAD', 'OPTIONS'):
            return [permissions.AllowAny()]
        return [permissions.IsAdminUser()]


class ContentAdvertDetailView(generics.RetrieveUpdateDestroyAPIView):
    queryset = contentadverts.objects.all()
    serializer_class = ContentAdvertSerializer

    def get_permissions(self):
        if self.request.method in ('GET', 'HEAD', 'OPTIONS'):
            return [permissions.AllowAny()]
        return [permissions.IsAdminUser()]


class MiniSeriesListCreateView(generics.ListCreateAPIView):
    queryset = MiniSeries.objects.all()
    serializer_class = MiniSeriesSerializer
    permission_classes = [permissions.IsAuthenticated]
    

    def get_queryset(self):
        content_id = self.request.query_params.get('content')
        if content_id:
            try:
                cleaned_id = int(str(content_id).strip('/'))
                return MiniSeries.objects.filter(content_id=cleaned_id)
            except (TypeError, ValueError):
                return MiniSeries.objects.none()
        return MiniSeries.objects.all()

    def get_permissions(self):
        if self.request.method in ('GET', 'HEAD', 'OPTIONS'):
            return [permissions.AllowAny()]
        return [permissions.IsAdminUser()]


class MiniSeriesDetailView(generics.RetrieveUpdateDestroyAPIView):
    queryset = MiniSeries.objects.all()
    serializer_class = MiniSeriesSerializer
    permission_classes = [permissions.IsAuthenticated]

    def get_permissions(self):
        if self.request.method in ('GET', 'HEAD', 'OPTIONS'):
            return [permissions.AllowAny()]
        return [permissions.IsAdminUser()]


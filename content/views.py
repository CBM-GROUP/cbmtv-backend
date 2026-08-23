from rest_framework import viewsets
from .models import Content, Season, Episode, contentadverts, MiniSeries
from .serializers import ContentSerializer, SeasonSerializer, EpisodeSerializer, ContentAdvertSerializer, MiniSeriesSerializer
from rest_framework import generics
from rest_framework import permissions
from rest_framework.response import Response
from rest_framework.decorators import api_view
from rest_framework.views import APIView
from django.http import JsonResponse
from django.db.models import Q
import os
import meilisearch
from common.media_storage import MediaStorageError, create_upload_target
from common.pagination import StandardPagination
#  Initialize Meilisearch client
MEILISEARCH_URL = os.getenv('MEILISEARCH_URL')
MASTER_KEY = os.getenv('MASTER_KEY')
client = meilisearch.Client(MEILISEARCH_URL, MASTER_KEY)
index = client.index('content')


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


@api_view(['GET'])
def search_view(request):
    """
    API to fetch all Content objects from the database and index them to Meilisearch.
    """
    try:
        try:
            # Delete existing index (optional)
            client.index('content').delete()
            return search_content(request)
        except Exception:
            # If deletion fails, still index content
            return search_content(request)
    except Exception as e:
        return JsonResponse({
            'status': 'error', 
            'message': str(e)
        }, status=500)


def search_content(request):
    # Fetch all content from database
    contents = Content.objects.all()

    # Prepare documents for Meilisearch
    documents = []
    for content in contents:
        documents.append({
            'id': content.id,
            'title': content.title,
            # If you store genres as a ManyToManyField
            'genres': [genre.name for genre in content.genres.all()] if hasattr(content, 'genres') else []
        })

    # Add documents to Meilisearch
    index.add_documents(documents)

    return JsonResponse({
        'status': 'success',
        'message': f'Indexed {len(documents)} documents to Meilisearch'
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
        channel_id = self.request.query_params.get('channel')
        if channel_id:
            # Be tolerant to trailing slashes like ?channel=5/
            try:
                cleaned_id = int(str(channel_id).strip('/'))
                return Content.objects.filter(channel_id=cleaned_id)
            except (TypeError, ValueError):
                return Content.objects.none()
        return Content.objects.all()
    
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
        return Content.objects.filter(content_type='movie')

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
                return Season.objects.filter(content_id=cleaned_id)
            except (TypeError, ValueError):
                return Season.objects.none()
        return Season.objects.all()
    
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
                return Episode.objects.filter(season_id=cleaned_id)
            except (TypeError, ValueError):
                return Episode.objects.none()
        return Episode.objects.all()

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


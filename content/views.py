from rest_framework import viewsets
from .models import Content, Season, Episode
from .serializers import ContentSerializer, SeasonSerializer, EpisodeSerializer
from rest_framework import generics
from rest_framework.response import Response
from rest_framework.decorators import api_view
from django.http import JsonResponse
from django.db.models import Q
# Create your views here.

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
    
#get, update, delete content
class ContentDetailView(generics.RetrieveUpdateDestroyAPIView):
    queryset = Content.objects.all()
    serializer_class = ContentSerializer
    

#movies: creat only
class MovieListView(generics.ListAPIView):
    queryset = Content.objects.filter(content_type='movie')
    serializer_class= ContentSerializer

    def get_queryset(self):
        return Content.objects.filter(content_type='movie')

#movies, update only
class MovieUpdateView(generics.UpdateAPIView):
    queryset = Content.objects.filter(content_type='movie')
    serializer_class= ContentSerializer
    lookup_field ='pk'

#create & list seasons
class SeasonListCreateView(generics.ListCreateAPIView):
    queryset = Season.objects.all()
    serializer_class = SeasonSerializer

#retrieve.update,delete single season
class SeasonDetailView(generics.RetrieveUpdateDestroyAPIView):
    queryset = Season.objects.all()
    serializer_class=SeasonSerializer

class EpisodeListCreateView(generics.ListCreateAPIView):
    serializer_class = EpisodeSerializer

    def get_queryset(self):
        season_id = self.request.query_params.get('season')
        if season_id:
            return Episode.objects.filter(season_id=season_id)
        return Episode.objects.all()



class EpisodeDetailView(generics.RetrieveUpdateDestroyAPIView):
    queryset = Episode.objects.all()
    serializer_class = EpisodeSerializer
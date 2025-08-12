from django.urls import path
from .views import (
    ContentListCreateView, ContentDetailView, EpisodeDetailView, EpisodeListCreateView,MovieListView, MovieUpdateView, SeasonDetailView,SeasonListCreateView, content_ids
    
    )

urlpatterns = [
    path('', ContentListCreateView.as_view(), name='content-list-create'),
    path('<int:pk>/', ContentDetailView.as_view(), name='content-detail'),
    path('ids/', content_ids, name='content-ids'),

    #movie-specific routes
    path('movies/', MovieListView.as_view(),name='movie-list'),
    path('movies/<int:pk>/update/', MovieUpdateView.as_view(), name='update-movie'),    

    path('seasons/', SeasonListCreateView.as_view(), name='season-list-create'),
    path('seasons/<int:pk>/', SeasonDetailView.as_view(),name='season-detail'),

    path('episodes/',EpisodeListCreateView.as_view(), name='episode-list-create'),
    path('episodes/<int:pk>/', EpisodeDetailView.as_view(), name='episode-detail'),

]
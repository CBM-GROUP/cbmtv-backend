from django.urls import path
from .views import (
    ContentListCreateView, ContentDetailView, EpisodeDetailView, EpisodeListCreateView, MovieListView, SeasonDetailView, SeasonListCreateView, content_ids,
    ContentAdvertListCreateView, ContentAdvertDetailView
    )

urlpatterns = [
    path('', ContentListCreateView.as_view(), name='content-list-create'),
    path('<int:pk>/', ContentDetailView.as_view(), name='content-detail'),
    path('ids/', content_ids, name='content-ids'),

    # Movie-specific listing (updates are handled universally at /<id>/)
    path('movies/', MovieListView.as_view(),name='movie-list'),

    path('seasons/', SeasonListCreateView.as_view(), name='season-list-create'),
    path('seasons/<int:pk>/', SeasonDetailView.as_view(),name='season-detail'),

    path('episodes/',EpisodeListCreateView.as_view(), name='episode-list-create'),
    path('episodes/<int:pk>/', EpisodeDetailView.as_view(), name='episode-detail'),

    # Adverts
    path('adverts/', ContentAdvertListCreateView.as_view(), name='advert-list-create'),
    path('adverts/<int:pk>/', ContentAdvertDetailView.as_view(), name='advert-detail'),

]
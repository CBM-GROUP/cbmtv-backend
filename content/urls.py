from django.urls import path
from .views import (
    ContentListCreateView, ContentDetailView, EpisodeDetailView, EpisodeListCreateView, MovieListView, SeasonDetailView, SeasonListCreateView, content_ids,
    ContentAdvertListCreateView, ContentAdvertDetailView, MiniSeriesListCreateView, MiniSeriesDetailView,
    MediaUploadTargetView, search_view, ReindexView, HeroSettingsView,
    categories_view, GenreListCreateView, GenreDetailView
    )
from .multipart_views import MultipartVideoUploadView
from .playback import PlaybackView
from .media_views import MediaStatusView, MediaRetryView, MediaPreviewView

urlpatterns = [
    path('<int:pk>/playback/', PlaybackView.as_view(), name='content-playback'),
    path('media/status/', MediaStatusView.as_view(), name='media-status'),
    path('media/preview/', MediaPreviewView.as_view(), name='media-preview'),
    path('media/<uuid:pk>/retry/', MediaRetryView.as_view(), name='media-retry'),
    path('media/upload-target/', MediaUploadTargetView.as_view(), name='media-upload-target'),
    path('media/multipart/<str:action>/', MultipartVideoUploadView.as_view(), name='media-multipart-upload'),
    path('categories/', categories_view, name='content-categories'),
    path('genres/', GenreListCreateView.as_view(), name='genre-list-create'),
    path('genres/<int:pk>/', GenreDetailView.as_view(), name='genre-detail'),
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
    path('hero-settings/', HeroSettingsView.as_view(), name='hero-settings'),
    path('adverts/', ContentAdvertListCreateView.as_view(), name='advert-list-create'),
    path('adverts/<int:pk>/', ContentAdvertDetailView.as_view(), name='advert-detail'),

    # MiniSeries
    path('miniseries/', MiniSeriesListCreateView.as_view(), name='miniseries-list-create'),
    path('miniseries/<int:pk>/', MiniSeriesDetailView.as_view(), name='miniseries-detail'),

    # SEARCH -- read-only. Was a destructive full reindex on an unauthenticated
    # GET; the rebuild now lives at reindex/ below.
    path('search/', search_view, name='search'),

    # REINDEX -- administrative, POST only, IsAdminUser.
    path('reindex/', ReindexView.as_view(), name='reindex'),

]

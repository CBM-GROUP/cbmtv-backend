from django.urls import path

from .views import (
    ProgressDeleteView,
    ProgressListCreateView,
    SavedDeleteView,
    SavedIdsView,
    SavedListCreateView,
)

urlpatterns = [
    path('saved/', SavedListCreateView.as_view(), name='library-saved'),
    path('saved/ids/', SavedIdsView.as_view(), name='library-saved-ids'),
    path('saved/<int:content_id>/', SavedDeleteView.as_view(), name='library-saved-delete'),
    path('progress/', ProgressListCreateView.as_view(), name='library-progress'),
    path('progress/<int:content_id>/', ProgressDeleteView.as_view(), name='library-progress-delete'),
]

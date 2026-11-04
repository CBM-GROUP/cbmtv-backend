from django.urls import path
from .views import DiscoveryEventView, RankingView, SimilarView, RecommendedView, DiscoveryAnalyticsView

urlpatterns = [
    path('events/', DiscoveryEventView.as_view(), name='discovery-events'),
    path('rankings/<str:kind>/', RankingView.as_view(), name='discovery-ranking'),
    path('similar/<int:pk>/', SimilarView.as_view(), name='discovery-similar'),
    path('recommended/', RecommendedView.as_view(), name='discovery-recommended'),
    path('analytics/', DiscoveryAnalyticsView.as_view(), name='discovery-analytics'),
]

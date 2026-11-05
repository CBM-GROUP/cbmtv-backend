from django.urls import include, path
from rest_framework.routers import DefaultRouter

from .views import (AdvertiserViewSet, CampaignViewSet, CreativeViewSet,
                    ImageUploadView, PlacementViewSet, TargetOptionsView,
                    SessionView, DecisionView, EventView)

router = DefaultRouter()
router.register('advertisers', AdvertiserViewSet)
router.register('campaigns', CampaignViewSet)
router.register('creatives', CreativeViewSet)
router.register('placements', PlacementViewSet)

urlpatterns = [
    path('session/', SessionView.as_view()),
    path('decision/', DecisionView.as_view()),
    path('events/', EventView.as_view()),
    path('targets/', TargetOptionsView.as_view()),
    path('images/', ImageUploadView.as_view()),
    path('', include(router.urls)),
]

from django.urls import include, path
from rest_framework.routers import DefaultRouter

from .views import (AdvertiserViewSet, CampaignViewSet, CreativeViewSet,
                    ImageUploadView, PlacementViewSet, TargetOptionsView)

router = DefaultRouter()
router.register('advertisers', AdvertiserViewSet)
router.register('campaigns', CampaignViewSet)
router.register('creatives', CreativeViewSet)
router.register('placements', PlacementViewSet)

urlpatterns = [
    path('targets/', TargetOptionsView.as_view()),
    path('images/', ImageUploadView.as_view()),
    path('', include(router.urls)),
]

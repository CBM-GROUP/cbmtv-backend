from django.db import transaction
from rest_framework.permissions import AllowAny
from rest_framework.response import Response
from rest_framework.views import APIView
from accounts.permissions import HasDashboardSection
from common.http import PrivateResponseMixin
from .models import FeaturedChannel, FeaturedTitle, HomepageConfiguration
from .serializers import ConfigurationSerializer
from .service import build_homepage, configuration


class HomepageView(PrivateResponseMixin, APIView):
    permission_classes = [AllowAny]

    def get(self, request):
        return Response(build_homepage(configuration(), request.user))


class ManagementView(PrivateResponseMixin, APIView):
    permission_classes = [HasDashboardSection]
    required_section = 'content'

    def get(self, request):
        return Response(ConfigurationSerializer(configuration()).data)

    @transaction.atomic
    def put(self, request):
        serializer = ConfigurationSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        data = serializer.validated_data
        config, _ = HomepageConfiguration.objects.get_or_create(pk=1)
        config = HomepageConfiguration.objects.select_for_update().get(pk=config.pk)
        config.sections = data['sections']
        config.release_duration = data['new_releases']['duration']
        config.release_unit = data['new_releases']['unit']
        if 'advert_insertion' in data:
            config.advert_insertion_enabled = data['advert_insertion']['enabled']
            config.advert_interval = data['advert_insertion']['interval']
        config.save()
        FeaturedTitle.objects.all().delete()
        FeaturedChannel.objects.all().delete()
        FeaturedTitle.objects.bulk_create([
            FeaturedTitle(position=i, **placement) for i, placement in enumerate(data['featured_titles'])
        ])
        FeaturedChannel.objects.bulk_create([
            FeaturedChannel(position=i, **placement) for i, placement in enumerate(data['featured_channels'])
        ])
        return Response(ConfigurationSerializer(configuration()).data)


class PreviewView(PrivateResponseMixin, APIView):
    permission_classes = [HasDashboardSection]
    required_section = 'content'

    def post(self, request):
        serializer = ConfigurationSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        # Staff preview never loads a viewer's personal history.
        return Response(build_homepage(serializer.validated_data))

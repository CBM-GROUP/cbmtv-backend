from rest_framework import viewsets, permissions
from rest_framework.decorators import action
from rest_framework.response import Response
from common.pagination import StandardPagination
from .models import Channel
from .serializers import ChannelSerializer
from content.models import Content
from content.serializers import ContentSerializer
from content.publication import visible_queryset
from content.search_sync import upsert_content
from django.db import transaction
from accounts.permissions import HasDashboardSection, dashboard_permissions
from rest_framework.exceptions import PermissionDenied, ValidationError
from common.idempotency import IdempotentCreateMixin



class ChannelViewSet(IdempotentCreateMixin, viewsets.ModelViewSet):
    required_section = 'channels'
    """
    ViewSet for Channels.
    - Lists, retrieves, creates, updates, deletes channels.
    - Provides a custom paginated endpoint for content under a channel.
    """
    # Ordered explicitly: PageNumberPagination slices an unordered queryset,
    # which Postgres is free to return in a different order per query — rows
    # then repeat or vanish across pages. Django warns about this
    # (UnorderedObjectListWarning) rather than failing.
    queryset = Channel.objects.all().order_by("name")
    serializer_class = ChannelSerializer
    permission_classes = [HasDashboardSection]  # Default permissions
    pagination_class = StandardPagination

    def get_queryset(self):
        queryset = super().get_queryset()
        if self.request.method not in ('GET', 'HEAD', 'OPTIONS'):
            return queryset
        scope = self.request.query_params.get('scope')
        if scope is not None:
            if scope != 'dashboard':
                raise ValidationError({'scope': 'Use scope=dashboard for management reads.'})
            if not set(dashboard_permissions(self.request.user)) & {'channels', 'content'}:
                raise PermissionDenied('Channel or Content section access is required.')
            return queryset
        return queryset.filter(is_active=True)

    def perform_update(self, serializer):
        channel = serializer.save()
        ids = list(channel.content.values_list('pk', flat=True))
        transaction.on_commit(lambda: [upsert_content(pk) for pk in ids])

    def get_permissions(self):
        """
        Allow read-only access to anyone, restrict modifications to admins.
        """
        if self.request.method in ('GET', 'HEAD', 'OPTIONS'):
            return [permissions.AllowAny()]
        return [HasDashboardSection()]

    @action(
        detail=True,
        methods=['get'],
        url_path='contents',
        pagination_class=StandardPagination
    )
    def contents(self, request, pk=None):
        """
        Returns paginated content for this channel.
        Endpoint: /api/channels/<id>/contents/
        """
        channel = self.get_object()
        contents_qs = visible_queryset(request, Content.objects.filter(channel=channel)).order_by('-id')

        # Apply pagination
        page = self.paginate_queryset(contents_qs)
        if page is not None:
            serializer = ContentSerializer(page, many=True)
            return self.get_paginated_response(serializer.data)

        serializer = ContentSerializer(contents_qs, many=True)
        return Response(serializer.data)

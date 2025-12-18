from rest_framework import viewsets, permissions
from rest_framework.decorators import action
from rest_framework.response import Response
from common.pagination import StandardPagination
from .models import Channel
from .serializers import ChannelSerializer
from content.models import Content
from content.serializers import ContentSerializer



class ChannelViewSet(viewsets.ModelViewSet):
    """
    ViewSet for Channels.
    - Lists, retrieves, creates, updates, deletes channels.
    - Provides a custom paginated endpoint for content under a channel.
    """
    queryset = Channel.objects.all()
    serializer_class = ChannelSerializer
    permission_classes = [permissions.IsAdminUser]  # Default permissions
    pagination_class = StandardPagination

    def get_permissions(self):
        """
        Allow read-only access to anyone, restrict modifications to admins.
        """
        if self.request.method in ('GET', 'HEAD', 'OPTIONS'):
            return [permissions.AllowAny()]
        return [permissions.IsAdminUser()]

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
        contents_qs = Content.objects.filter(channel=channel).order_by('-id')  # optional ordering

        # Apply pagination
        page = self.paginate_queryset(contents_qs)
        if page is not None:
            serializer = ContentSerializer(page, many=True)
            return self.get_paginated_response(serializer.data)

        serializer = ContentSerializer(contents_qs, many=True)
        return Response(serializer.data)

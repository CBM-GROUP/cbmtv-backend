"""
The signed-in viewer's library.

  GET    /api/library/saved/                  paginated saved items
  POST   /api/library/saved/                  {content} -- idempotent
  GET    /api/library/saved/ids/              bare array of saved content ids
  DELETE /api/library/saved/<content_id>/     idempotent
  GET    /api/library/progress/               paginated, unfinished only
                                              (?include_completed=true for all)
  POST   /api/library/progress/               upsert on (user, content)
  DELETE /api/library/progress/<content_id>/  idempotent

Every query is scoped to request.user; there is no way to address another
user's rows. Deletes answer 204 whether or not the row existed, so a client
retrying after a dropped response does not see a spurious error.
"""
from rest_framework import generics, permissions, status
from rest_framework.response import Response
from rest_framework.views import APIView

from common.pagination import StandardPagination
from .models import SavedItem, WatchProgress
from .serializers import (
    RecordProgressSerializer,
    SaveContentSerializer,
    SavedItemSerializer,
    WatchProgressSerializer,
)

TRUE_VALUES = ('true', '1', 'yes')


class SavedListCreateView(generics.ListAPIView):
    permission_classes = [permissions.IsAuthenticated]
    serializer_class = SavedItemSerializer
    pagination_class = StandardPagination

    def get_queryset(self):
        return (
            SavedItem.objects.filter(user=self.request.user)
            .select_related('content')
            .order_by('-created_at', '-id')
        )

    def post(self, request):
        serializer = SaveContentSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        item, created = SavedItem.objects.get_or_create(
            user=request.user, content=serializer.validated_data['content']
        )
        return Response(
            SavedItemSerializer(item).data,
            status=status.HTTP_201_CREATED if created else status.HTTP_200_OK,
        )


class SavedIdsView(APIView):
    permission_classes = [permissions.IsAuthenticated]

    def get(self, request):
        ids = (
            SavedItem.objects.filter(user=request.user)
            .order_by('-created_at', '-id')
            .values_list('content_id', flat=True)
        )
        return Response(list(ids))


class SavedDeleteView(APIView):
    permission_classes = [permissions.IsAuthenticated]

    def delete(self, request, content_id):
        SavedItem.objects.filter(user=request.user, content_id=content_id).delete()
        return Response(status=status.HTTP_204_NO_CONTENT)


class ProgressListCreateView(generics.ListAPIView):
    permission_classes = [permissions.IsAuthenticated]
    serializer_class = WatchProgressSerializer
    pagination_class = StandardPagination

    def get_queryset(self):
        queryset = (
            WatchProgress.objects.filter(user=self.request.user)
            .select_related('content')
            .order_by('-updated_at', '-id')
        )
        include_completed = self.request.query_params.get('include_completed', '')
        if str(include_completed).strip('/').lower() not in TRUE_VALUES:
            queryset = queryset.filter(completed=False)
        return queryset

    def post(self, request):
        serializer = RecordProgressSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        data = serializer.validated_data

        position = data['position_seconds']
        duration = data.get('duration_seconds') or 0
        progress, _ = WatchProgress.objects.update_or_create(
            user=request.user,
            content=data['content'],
            defaults={
                'episode': data.get('episode'),
                'miniseries': data.get('miniseries'),
                'position_seconds': position,
                'duration_seconds': duration,
                'completed': WatchProgress.is_complete(position, duration),
            },
        )
        return Response(WatchProgressSerializer(progress).data, status=status.HTTP_200_OK)


class ProgressDeleteView(APIView):
    permission_classes = [permissions.IsAuthenticated]

    def delete(self, request, content_id):
        WatchProgress.objects.filter(user=request.user, content_id=content_id).delete()
        return Response(status=status.HTTP_204_NO_CONTENT)

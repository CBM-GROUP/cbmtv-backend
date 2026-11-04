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
from common.http import PrivateResponseMixin
from content.publication import PUBLIC_STATUS, public_eligibility
from .models import SavedItem, WatchProgress
from .models import PartProgress
from django.db import transaction
from .serializers import (
    RecordProgressSerializer,
    SaveContentSerializer,
    SavedItemSerializer,
    WatchProgressSerializer,
)

TRUE_VALUES = ('true', '1', 'yes')


class SavedListCreateView(PrivateResponseMixin, generics.ListAPIView):
    permission_classes = [permissions.IsAuthenticated]
    serializer_class = SavedItemSerializer
    pagination_class = StandardPagination

    def get_queryset(self):
        return (
            SavedItem.objects.filter(user=self.request.user, **public_eligibility('content__'))
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


class SavedIdsView(PrivateResponseMixin, APIView):
    permission_classes = [permissions.IsAuthenticated]

    def get(self, request):
        ids = (
            SavedItem.objects.filter(user=request.user, **public_eligibility('content__'))
            .order_by('-created_at', '-id')
            .values_list('content_id', flat=True)
        )
        return Response(list(ids))


class SavedDeleteView(PrivateResponseMixin, APIView):
    permission_classes = [permissions.IsAuthenticated]

    def delete(self, request, content_id):
        SavedItem.objects.filter(user=request.user, content_id=content_id).delete()
        return Response(status=status.HTTP_204_NO_CONTENT)


class ProgressListCreateView(PrivateResponseMixin, generics.ListAPIView):
    permission_classes = [permissions.IsAuthenticated]
    serializer_class = WatchProgressSerializer
    pagination_class = StandardPagination

    def get_queryset(self):
        queryset = (
            WatchProgress.objects.filter(user=self.request.user, **public_eligibility('content__'))
            .select_related('content')
            .order_by('-updated_at', '-id')
        )
        include_completed = self.request.query_params.get('include_completed', '')
        if str(include_completed).strip('/').lower() not in TRUE_VALUES:
            queryset = queryset.filter(completed=False)
        return queryset

    @transaction.atomic
    def post(self, request):
        serializer = RecordProgressSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        data = serializer.validated_data

        position = data['position_seconds']
        duration = data.get('duration_seconds') or 0
        # Serialize concurrent writes per viewer/title without changing the
        # Branch 3 unique snapshot key or overwriting history for other parts.
        from accounts.models import User
        User.objects.select_for_update().get(pk=request.user.pk)
        if data.get('episode') or data.get('miniseries'):
            PartProgress.objects.update_or_create(user=request.user, content=data['content'],
                episode=data.get('episode'), miniseries=data.get('miniseries'), defaults={
                    'position_seconds': position, 'duration_seconds': duration,
                    'completed': WatchProgress.is_complete(position, duration),
                })
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


class ProgressDeleteView(PrivateResponseMixin, APIView):
    permission_classes = [permissions.IsAuthenticated]

    def get(self, request, content_id):
        from django.shortcuts import get_object_or_404
        from rest_framework.exceptions import ValidationError
        selected = {key: request.query_params[key] for key in ('episode', 'miniseries') if key in request.query_params}
        if selected:
            if len(selected) != 1 or any(not value.isascii() or not value.isdigit() or len(value) > 18 or int(value) < 1 for value in selected.values()):
                raise ValidationError('Select one valid episode or part.')
            filters = dict(user=request.user, content_id=content_id, **selected, **public_eligibility('content__'))
            progress = PartProgress.objects.select_related('content').filter(**filters).first()
            snapshot = WatchProgress.objects.select_related('content').filter(**filters).first()
            if snapshot and (not progress or snapshot.updated_at > progress.updated_at):
                # A rolled-back older backend may have updated the Branch 3
                # snapshot without knowing the additive PartProgress table.
                progress = snapshot
            if not progress:
                from django.http import Http404
                raise Http404
            return Response(WatchProgressSerializer(progress).data)
        progress = get_object_or_404(
            WatchProgress.objects.select_related('content').prefetch_related('content__genres'),
            user=request.user, content_id=content_id, **public_eligibility('content__'),
        )
        return Response(WatchProgressSerializer(progress).data)

    def delete(self, request, content_id):
        WatchProgress.objects.filter(user=request.user, content_id=content_id).delete()
        PartProgress.objects.filter(user=request.user, content_id=content_id).delete()
        return Response(status=status.HTTP_204_NO_CONTENT)

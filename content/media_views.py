from django.shortcuts import get_object_or_404
from rest_framework.views import APIView
from rest_framework.response import Response
from common.http import PrivateResponseMixin
from .models import MediaAsset, Content, Episode, MiniSeries
from .views import MediaUploadTargetView
from .publication import can_read_dashboard_content
from accounts.permissions import HasDashboardSection
from common.multipart_upload import _client
from django.conf import settings


class MediaStatusView(PrivateResponseMixin, APIView):
    permission_classes = MediaUploadTargetView.permission_classes

    def get(self, request):
        reference = request.query_params.get('reference_url', '')
        asset = MediaAsset.objects.filter(reference_url=reference).first()
        attached = can_read_dashboard_content(request.user) and any(model.objects.filter(streaming_link=reference).exists() for model in (Content, Episode, MiniSeries))
        if asset and asset.owner_id != request.user.pk and not attached:
            asset = None
        if not asset:
            return Response({'state': 'legacy', 'ready': False, 'error': ''})
        return Response({'media_id': str(asset.pk), 'state': asset.state,
            'source_uploaded': asset.state not in ('uploading', 'canceled'),
            'ready': asset.state == 'ready' or asset.purpose == 'public' and asset.state == 'uploaded',
            'renditions': asset.renditions, 'error': asset.error})


class MediaRetryView(PrivateResponseMixin, APIView):
    permission_classes = MediaUploadTargetView.permission_classes

    def post(self, request, pk):
        asset = get_object_or_404(MediaAsset, pk=pk, owner=request.user)
        changed = MediaAsset.objects.filter(pk=asset.pk, state='failed', purpose='protected').update(
            state='queued', error='', attempts=0, next_attempt_at=None, lease_id=None, lease_until=None)
        return Response({'queued': bool(changed)}, status=202 if changed else 409)


class MediaPreviewView(PrivateResponseMixin, APIView):
    permission_classes = [HasDashboardSection]
    required_section = 'content'

    def post(self, request):
        asset = get_object_or_404(MediaAsset, reference_url=request.data.get('reference_url', ''))
        attached = any(model.objects.filter(streaming_link=asset.reference_url).exists() for model in (Content, Episode, MiniSeries))
        if asset.owner_id != request.user.pk and not attached:
            return Response({'detail': 'This source belongs to another account.'}, status=403)
        if asset.state in ('uploading', 'canceled'):
            return Response({'detail': 'Source upload is incomplete.'}, status=409)
        # Staff may inspect the uploaded source while HLS is processing. This
        # short, object-specific grant does not make its S3 origin public.
        try:
            url = _client(settings.MEDIA_STORAGE).generate_presigned_url('get_object',
                Params={'Bucket': settings.MEDIA_STORAGE['S3_BUCKET_NAME'], 'Key': asset.source_key}, ExpiresIn=300)
        except Exception:
            return Response({'detail': 'Source preview is unavailable.'}, status=503)
        return Response({'url': url, 'type': 'source', 'expires_in': 300, 'state': asset.state})

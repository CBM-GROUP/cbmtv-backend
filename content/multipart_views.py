from rest_framework import permissions
from rest_framework.response import Response
from rest_framework.views import APIView

from common.media_storage import MediaStorageError
from common.multipart_upload import (
    abort_video_upload,
    finish_video_upload,
    sign_part,
    start_video_upload,
)


class MultipartVideoUploadView(APIView):
    # Matches the committed single-PUT upload endpoint's access rule.
    permission_classes = [permissions.IsAuthenticated]

    def post(self, request, action):
        try:
            if action == 'start':
                result = start_video_upload(
                    request.data.get('filename'), request.data.get('content_type'),
                    request.data.get('size_bytes'), request.user.pk,
                )
            elif action == 'part':
                result = {'upload_url': sign_part(
                    request.data.get('token'), request.user.pk, request.data.get('part_number')
                )}
            elif action == 'complete':
                result = {'delivery_url': finish_video_upload(request.data.get('token'), request.user.pk)}
            elif action == 'abort':
                abort_video_upload(request.data.get('token'), request.user.pk)
                result = {'canceled': True}
            else:
                return Response({'error': 'Unknown upload action.'}, status=404)
        except MediaStorageError as exc:
            return Response({'error': str(exc)}, status=400)
        return Response(result, status=200)

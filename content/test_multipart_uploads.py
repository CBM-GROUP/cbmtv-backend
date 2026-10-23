from unittest.mock import patch

from django.test import override_settings
from rest_framework.test import APITestCase

from accounts.models import User
from common.media_storage import MediaStorageError
from common.multipart_upload import PART_SIZE, abort_video_upload, finish_video_upload, sign_part, start_video_upload
from content.test_media_uploads import TEST_STORAGE
from content.multipart_views import MultipartVideoUploadView
from content.views import MediaUploadTargetView


@override_settings(MEDIA_STORAGE=TEST_STORAGE)
class MultipartUploadTests(APITestCase):
    def test_permissions_match_single_put_upload(self):
        self.assertEqual(MultipartVideoUploadView.permission_classes, MediaUploadTargetView.permission_classes)
        self.assertEqual(
            MultipartVideoUploadView.required_section,
            getattr(MediaUploadTargetView, "required_section", None),
        )

    def setUp(self):
        self.user = User.objects.create_user(
            email="uploader@example.com", password="pw", name="Uploader",
            phone="", location="", country="", role="admin",
        )
        self.user.is_staff = True
        self.user.save(update_fields=["is_staff"])

    @patch("common.multipart_upload.boto3.client")
    def test_start_sign_complete_and_abort_bind_to_user_and_key(self, client_factory):
        client = client_factory.return_value
        client.create_multipart_upload.return_value = {"UploadId": "upload-1"}
        client.generate_presigned_url.return_value = "https://s3.example/part"
        client.list_parts.return_value = {
            "Parts": [{"PartNumber": n, "ETag": f'"etag-{n}"'} for n in range(1, 4)],
            "IsTruncated": False,
        }
        started = start_video_upload("film.mp4", "video/mp4", PART_SIZE * 2 + 1, self.user.pk)
        self.assertEqual(started["part_count"], 3)
        self.assertIn("/cbmvideo/", started["delivery_url"])
        self.assertEqual(sign_part(started["token"], self.user.pk, 2), "https://s3.example/part")
        with self.assertRaises(MediaStorageError):
            sign_part(started["token"], self.user.pk + 1, 2)
        with self.assertRaises(MediaStorageError):
            sign_part(started["token"], self.user.pk, 4)
        self.assertEqual(finish_video_upload(started["token"], self.user.pk), started["delivery_url"])
        self.assertEqual(client.complete_multipart_upload.call_args.kwargs["MultipartUpload"]["Parts"][0]["ETag"], '"etag-1"')
        abort_video_upload(started["token"], self.user.pk)
        self.assertEqual(client.abort_multipart_upload.call_args.kwargs["UploadId"], "upload-1")

    @patch("common.multipart_upload.boto3.client")
    def test_incomplete_upload_cannot_be_published(self, client_factory):
        client = client_factory.return_value
        client.create_multipart_upload.return_value = {"UploadId": "upload-1"}
        client.list_parts.return_value = {"Parts": [{"PartNumber": 1, "ETag": '"one"'}], "IsTruncated": False}
        started = start_video_upload("film.mp4", "video/mp4", PART_SIZE * 2, self.user.pk)
        with self.assertRaises(MediaStorageError):
            finish_video_upload(started["token"], self.user.pk)
        client.complete_multipart_upload.assert_not_called()

    @patch("common.multipart_upload.boto3.client")
    def test_route_requires_authentication_and_starts_upload(self, client_factory):
        client_factory.return_value.create_multipart_upload.return_value = {"UploadId": "upload-1"}
        path = "/api/content/media/multipart/start/"
        payload = {"filename": "film.mp4", "content_type": "video/mp4", "size_bytes": PART_SIZE * 2}
        self.assertIn(self.client.post(path, payload, format="json").status_code, (401, 403))
        self.client.force_authenticate(self.user)
        response = self.client.post(path, payload, format="json")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data["part_count"], 2)

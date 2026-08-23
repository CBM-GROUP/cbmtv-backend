from unittest.mock import patch

from django.test import override_settings
from django.urls import resolve
from rest_framework.test import APITestCase

from accounts.models import User
from common.media_storage import (
    MediaStorageError,
    build_delivery_url,
    create_upload_target,
    resolve_presign_ttl,
)
from content.views import MediaUploadTargetView


TEST_STORAGE = {
    "PROVIDER": "s3",
    "CREDENTIALS_MODE": "access_keys",
    "AWS_ACCESS_KEY_ID": "test-access-key",
    "AWS_SECRET_ACCESS_KEY": "test-secret-key",
    "S3_BUCKET_NAME": "test-bucket",
    "AWS_REGION": "eu-west-1",
    "CLOUDFRONT_BASE_URL": "https://cdn.example.com",
    "CDN_DOMAIN": "https://cdn.example.com",
    "IMAGE_BASE_URL": "",
    "PRESIGNED_URL_TTL": 3600,
    "PRESIGNED_URL_MAX_TTL": 43200,
    "MIN_UPLOAD_BYTES_PER_SEC": 100 * 1024,
    "PRESIGNED_URL_OVERHEAD": 300,
    "MAX_UPLOAD_BYTES": 5 * 1024**3,
}


@override_settings(MEDIA_STORAGE=TEST_STORAGE)
class MediaStorageTests(APITestCase):
    url = "/api/content/media/upload-target/"

    def setUp(self):
        self.user = User.objects.create_user(
            email="admin@example.com",
            name="Admin",
            phone="",
            location="",
            country="",
            password="password",
            role="admin",
        )

    def test_route_resolves_to_media_upload_view(self):
        self.assertIs(resolve(self.url).func.view_class, MediaUploadTargetView)

    def test_endpoint_requires_authentication_instead_of_returning_404(self):
        response = self.client.post(self.url, {}, format="json")
        self.assertIn(response.status_code, (401, 403))

    @patch("common.media_storage.boto3.client")
    def test_image_target_is_served_from_s3_not_cloudfront(self, client):
        """Images bypass CloudFront entirely: image delivery URLs point
        straight at the bucket, independent of the CDN."""
        client.return_value.generate_presigned_url.return_value = "https://s3.example/upload"
        result = create_upload_target("poster.jpg", "image/jpeg", "image")
        self.assertTrue(result["object_key"].startswith("cbm-images/"))
        self.assertEqual(
            result["delivery_url"],
            f'https://test-bucket.s3.eu-west-1.amazonaws.com/{result["object_key"]}',
        )
        self.assertNotIn("cdn.example.com", result["delivery_url"])

    @patch("common.media_storage.boto3.client")
    def test_image_target_honours_image_base_url_override(self, client):
        client.return_value.generate_presigned_url.return_value = "https://s3.example/upload"
        with override_settings(
            MEDIA_STORAGE={**TEST_STORAGE, "IMAGE_BASE_URL": "https://img.example.com"}
        ):
            result = create_upload_target("poster.jpg", "image/jpeg", "image")
        self.assertEqual(
            result["delivery_url"],
            f'https://img.example.com/{result["object_key"]}',
        )

    @patch("common.media_storage.boto3.client")
    def test_image_target_does_not_require_cloudfront(self, client):
        """Only video needs the CDN, so an image upload must still work with
        CLOUDFRONT_BASE_URL unset."""
        client.return_value.generate_presigned_url.return_value = "https://s3.example/upload"
        with override_settings(
            MEDIA_STORAGE={**TEST_STORAGE, "CLOUDFRONT_BASE_URL": "", "CDN_DOMAIN": ""}
        ):
            result = create_upload_target("poster.jpg", "image/jpeg", "image")
        self.assertTrue(
            result["delivery_url"].startswith("https://test-bucket.s3.eu-west-1.amazonaws.com/")
        )

    @patch("common.media_storage.boto3.client")
    def test_video_target_keeps_prefix_in_cloudfront_url(self, client):
        """The distribution's origin has no origin path, so the delivery URL is
        the full S3 key including cbmvideo/. Confirmed live:
        <cdn>/cbmvideo/<f>.mp4 -> 200, <cdn>/<f>.mp4 -> 403.
        This inverted when the /cbmvideo origin path was removed."""
        client.return_value.generate_presigned_url.return_value = "https://s3.example/upload"
        result = create_upload_target("movie.mp4", "video/mp4", "video")
        self.assertTrue(result["object_key"].startswith("cbmvideo/"))
        self.assertEqual(
            result["delivery_url"], f'https://cdn.example.com/{result["object_key"]}'
        )
        self.assertIn("/cbmvideo/", result["delivery_url"])

    @patch("common.media_storage.boto3.client")
    def test_presigned_url_uses_sigv4(self, client):
        """SigV2 is deprecated and only works in pre-2014 regions; boto3 falls
        back to it against the global s3.amazonaws.com endpoint unless pinned."""
        create_upload_target("poster.jpg", "image/jpeg", "image")
        _, kwargs = client.call_args
        self.assertEqual(kwargs["config"].signature_version, "s3v4")

    def test_rejects_mime_extension_mismatch(self):
        with self.assertRaises(MediaStorageError):
            create_upload_target("movie.exe", "video/mp4", "video")

    def test_url_join_avoids_duplicate_slashes(self):
        self.assertEqual(
            build_delivery_url("https://cdn.example.com/", "/cbm-images/poster.jpg"),
            "https://cdn.example.com/cbm-images/poster.jpg",
        )

    @patch("common.media_storage.boto3.client")
    def test_endpoint_never_returns_credentials(self, client):
        client.return_value.generate_presigned_url.return_value = "https://s3.example/upload"
        self.client.force_authenticate(self.user)
        response = self.client.post(
            self.url,
            {"filename": "poster.jpg", "content_type": "image/jpeg", "media_type": "image"},
            format="json",
        )
        self.assertEqual(response.status_code, 200)
        self.assertNotIn("test-secret-key", str(response.data))


@override_settings(MEDIA_STORAGE=TEST_STORAGE)
class PresignExpiryTests(APITestCase):
    """The presigned window is sized to the file.

    A flat one-hour TTL failed large uploads at the very end, after every byte
    had already been sent -- the worst possible moment to find out.
    """

    def setUp(self):
        self.url = "/api/content/media/upload-target/"
        self.user = User.objects.create_user(
            email="uploader@example.com",
            password="pw",
            name="Uploader",
            phone="0700000000",
            location="Kampala",
            country="UG",
        )

    def test_small_file_keeps_the_floor(self):
        self.assertEqual(resolve_presign_ttl(200 * 1024, TEST_STORAGE), 3600)

    def test_missing_size_keeps_the_floor(self):
        """Older clients omit size_bytes; they must not regress."""
        self.assertEqual(resolve_presign_ttl(None, TEST_STORAGE), 3600)

    def test_large_file_gets_a_proportional_window(self):
        # 2 GiB at the 100 KB/s floor rate is ~5.8 hours, well past one hour.
        ttl = resolve_presign_ttl(2 * 1024**3, TEST_STORAGE)
        self.assertGreater(ttl, 3600)
        self.assertEqual(ttl, int(2 * 1024**3 / (100 * 1024)) + 300)

    def test_window_is_capped(self):
        self.assertEqual(resolve_presign_ttl(500 * 1024**3, TEST_STORAGE), 43200)

    @patch("common.media_storage.boto3.client")
    def test_expiry_reaches_boto_and_the_response(self, client):
        client.return_value.generate_presigned_url.return_value = "https://s3.example/upload"
        result = create_upload_target("movie.mp4", "video/mp4", "video", size_bytes=2 * 1024**3)
        expected = int(2 * 1024**3 / (100 * 1024)) + 300
        _, kwargs = client.return_value.generate_presigned_url.call_args
        self.assertEqual(kwargs["ExpiresIn"], expected)
        self.assertEqual(result["expires_in"], expected)

    def test_rejects_a_file_too_large_for_a_single_put(self):
        """S3 caps a single-part PUT at 5 GiB. Fail before the upload, not after."""
        with self.assertRaises(MediaStorageError):
            create_upload_target("huge.mp4", "video/mp4", "video", size_bytes=6 * 1024**3)

    def test_rejects_a_non_numeric_size(self):
        with self.assertRaises(MediaStorageError):
            create_upload_target("movie.mp4", "video/mp4", "video", size_bytes="big")

    @patch("common.media_storage.boto3.client")
    def test_endpoint_passes_size_through(self, client):
        client.return_value.generate_presigned_url.return_value = "https://s3.example/upload"
        self.client.force_authenticate(self.user)
        response = self.client.post(
            self.url,
            {
                "filename": "movie.mp4",
                "content_type": "video/mp4",
                "media_type": "video",
                "size_bytes": 2 * 1024**3,
            },
            format="json",
        )
        self.assertEqual(response.status_code, 200)
        self.assertGreater(response.data["expires_in"], 3600)

    def test_endpoint_reports_an_oversized_file(self):
        self.client.force_authenticate(self.user)
        response = self.client.post(
            self.url,
            {
                "filename": "huge.mp4",
                "content_type": "video/mp4",
                "media_type": "video",
                "size_bytes": 6 * 1024**3,
            },
            format="json",
        )
        self.assertEqual(response.status_code, 400)
        self.assertIn("too large", response.data["error"])

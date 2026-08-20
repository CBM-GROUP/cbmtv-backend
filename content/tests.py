from datetime import timedelta

from django.urls import reverse
from rest_framework.test import APITestCase, APIClient
from rest_framework import status
from channel.models import Channel
from .models import Content, MiniSeries
from accounts.models import User


class ContentUpdatePermissionsTests(APITestCase):
    def setUp(self):
        self.client = APIClient()

        # Create users
        self.admin_user = User.objects.create_user(
            email="admin@example.com",
            name="Admin",
            phone="0000000000",
            location="HQ",
            country="ZZ",
            password="pass1234",
            role="admin",
        )
        self.admin_user.is_staff = True
        self.admin_user.save()

        self.normal_user = User.objects.create_user(
            email="user@example.com",
            name="User",
            phone="1111111111",
            location="Town",
            country="YY",
            password="pass1234",
            role="user",
        )

        # Create a channel
        self.channel, _ = Channel.objects.get_or_create(name="CBM Movies")

        # Create contents
        self.movie = Content.objects.create(
            title="Sample Movie",
            description="Desc",
            content_type="movie",
            channel=self.channel,
        )

        self.series = Content.objects.create(
            title="Sample Series",
            description="Desc",
            content_type="series",
            channel=self.channel,
        )

        self.miniseries_parent = Content.objects.create(
            title="Sample MiniSeries Container",
            description="Desc",
            content_type="miniseries",
            channel=self.channel,
        )

        # Base URLs (content app likely included under /api/content/ by project urls)
        self.base_url = "/api/content/"
        self.miniseries_url = f"{self.base_url}miniseries/"

    def test_admin_can_put_movie(self):
        self.client.force_authenticate(user=self.admin_user)
        url = f"{self.base_url}{self.movie.id}/"
        payload = {
            "title": "Updated Movie",
            "description": "New Desc",
            "content_type": "movie",
            "channel": self.channel.id,
            "director": "John Doe",
            "writer": "Jane Roe",
            "genre": "Action",
        }
        response = self.client.put(url, payload, format="json")
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.movie.refresh_from_db()
        self.assertEqual(self.movie.title, "Updated Movie")
        self.assertEqual(self.movie.director, "John Doe")
        self.assertEqual(self.movie.writer, "Jane Roe")
        self.assertEqual(self.movie.genre, "Action")

    def test_admin_can_create_content_with_cloudfront_media_urls(self):
        self.client.force_authenticate(user=self.admin_user)
        payload = {
            "title": "CloudFront Movie",
            "description": "Uploaded directly to S3",
            "content_type": "movie",
            "channel": self.channel.id,
            "status": "comingsoon",
            "thumbnail": "https://cdn.example.com/cbm-images/poster.jpg",
            "trailer_link": "https://cdn.example.com/trailer.mp4",
            "streaming_link": "https://cdn.example.com/movie.mp4",
            "duration": "00:20:00",
        }

        response = self.client.post(self.base_url, payload, format="json")

        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        created = Content.objects.get(title="CloudFront Movie")
        self.assertEqual(created.status, "comingsoon")
        self.assertEqual(created.thumbnail, payload["thumbnail"])
        self.assertEqual(created.streaming_link, payload["streaming_link"])
        self.assertEqual(created.trailer_link, payload["trailer_link"])

    def test_create_rejects_channel_zero(self):
        self.client.force_authenticate(user=self.admin_user)
        response = self.client.post(
            self.base_url,
            {
                "title": "Invalid Channel",
                "content_type": "movie",
                "channel": 0,
            },
            format="json",
        )

        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn("channel", response.data)

    def test_admin_can_patch_series(self):
        self.client.force_authenticate(user=self.admin_user)
        url = f"{self.base_url}{self.series.id}/"
        payload = {
            "writer": "Show Writer",
            "genre": "Drama",
        }
        response = self.client.patch(url, payload, format="json")
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.series.refresh_from_db()
        self.assertEqual(self.series.writer, "Show Writer")
        self.assertEqual(self.series.genre, "Drama")

    def test_normal_user_cannot_update(self):
        self.client.force_authenticate(user=self.normal_user)
        url = f"{self.base_url}{self.movie.id}/"
        payload = {"title": "Blocked"}
        response = self.client.patch(url, payload, format="json")
        self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)

    def test_get_returns_new_fields(self):
        # First set via admin
        self.client.force_authenticate(user=self.admin_user)
        url = f"{self.base_url}{self.movie.id}/"
        payload = {"director": "Visible Director", "writer": "Visible Writer", "genre": "Sci-Fi"}
        self.client.patch(url, payload, format="json")

        # Fetch as authenticated normal user
        self.client.force_authenticate(user=self.normal_user)
        response = self.client.get(url)
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertIn("director", response.data)
        self.assertIn("writer", response.data)
        self.assertIn("genre", response.data)
        self.assertEqual(response.data["director"], "Visible Director")
        self.assertEqual(response.data["writer"], "Visible Writer")
        self.assertEqual(response.data["genre"], "Sci-Fi")

    def test_miniseries_create_requires_correct_content_type(self):
        self.client.force_authenticate(user=self.admin_user)
        # Attempt to create under a non-miniseries content (movie) should fail
        payload = {
            "content": self.movie.id,
            "title": "Mini Ep 1",
            "miniseries_no": 1,
            "streaming_link": "https://example.com/m1.m3u8",
            "duration": "00:20:00",
            "thumbnail": "https://example.com/t1.jpg"
        }
        response = self.client.post(self.miniseries_url, payload, format="json")
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)

        # Create under correct content type should pass
        payload["content"] = self.miniseries_parent.id
        response = self.client.post(self.miniseries_url, payload, format="json")
        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        self.assertEqual(MiniSeries.objects.count(), 1)

    def test_miniseries_update_and_delete(self):
        self.client.force_authenticate(user=self.admin_user)
        # Create valid miniseries
        ms = MiniSeries.objects.create(
            content=self.miniseries_parent,
            title="Mini Ep 1",
            miniseries_no=1,
            streaming_link="https://example.com/m1.m3u8",
            duration=timedelta(minutes=20),
            thumbnail="https://example.com/t1.jpg",
        )

        # Update (PATCH)
        detail_url = f"{self.miniseries_url}{ms.id}/"
        patch_payload = {"title": "Mini Ep 1 - Updated"}
        response = self.client.patch(detail_url, patch_payload, format="json")
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        ms.refresh_from_db()
        self.assertEqual(ms.title, "Mini Ep 1 - Updated")

        # Delete
        response = self.client.delete(detail_url)
        self.assertEqual(response.status_code, status.HTTP_204_NO_CONTENT)
        self.assertEqual(MiniSeries.objects.count(), 0)

    def test_miniseries_filter_by_content(self):
        self.client.force_authenticate(user=self.admin_user)
        # Create two miniseries under same content
        MiniSeries.objects.create(content=self.miniseries_parent, title="A", miniseries_no=1)
        MiniSeries.objects.create(content=self.miniseries_parent, title="B", miniseries_no=2)

        # And one under a different content container
        other_parent = Content.objects.create(
            title="Other Mini Container",
            content_type="miniseries",
            channel=self.channel,
        )
        MiniSeries.objects.create(content=other_parent, title="C", miniseries_no=1)

        # Filter
        response = self.client.get(f"{self.miniseries_url}?content={self.miniseries_parent.id}")
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(len(response.data), 2)

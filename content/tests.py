from django.urls import reverse
from rest_framework.test import APITestCase, APIClient
from rest_framework import status
from channel.models import Channel
from .models import Content
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
        self.channel = Channel.objects.create(name="CBM Movies")

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

        # Base URLs (content app likely included under /api/content/ by project urls)
        self.base_url = "/api/content/"

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

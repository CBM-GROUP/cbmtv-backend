from rest_framework import status
from rest_framework.test import APITestCase, APIClient

from accounts.models import User
from .models import Channel


class ChannelApiTests(APITestCase):
    """
    Covers the endpoints the dashboard's channel screen calls.

    These exist because a production deploy shipped the `description` /
    `logo_url` model fields without running `migrate`, so every read of
    `/api/channels/` 500'd on a missing column while the create path returned a
    bare 400 the UI swallowed. A test run against a migrated DB catches the
    first half of that; `railway.json`'s pre-deploy migrate prevents it.
    """

    def setUp(self):
        self.client = APIClient()

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

        Channel.objects.get_or_create(name="CBM Movies")

    def test_list_is_public_and_returns_every_declared_field(self):
        response = self.client.get("/api/channels/")

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        first = response.data["results"][0]
        self.assertEqual(
            set(first.keys()),
            {"id", "name", "description", "logo_url", "cover_image_url"},
        )

    def test_list_is_ordered_by_name(self):
        Channel.objects.create(name="AAA First")
        Channel.objects.create(name="ZZZ Last")

        names = [c["name"] for c in self.client.get("/api/channels/").data["results"]]

        self.assertEqual(names, sorted(names))

    def test_create_requires_admin(self):
        response = self.client.post("/api/channels/", {"name": "Anonymous"}, format="json")

        self.assertEqual(response.status_code, status.HTTP_401_UNAUTHORIZED)

    def test_create_accepts_blank_optional_fields(self):
        """The dashboard form posts '' for every image it has no upload for."""
        self.client.force_authenticate(user=self.admin_user)

        response = self.client.post(
            "/api/channels/",
            {"name": "CBM Kids", "description": "", "logo_url": "", "cover_image_url": ""},
            format="json",
        )

        self.assertEqual(response.status_code, status.HTTP_201_CREATED, response.data)

    def test_create_with_duplicate_name_returns_a_readable_400(self):
        self.client.force_authenticate(user=self.admin_user)

        response = self.client.post("/api/channels/", {"name": "CBM Movies"}, format="json")

        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn("name", response.data)

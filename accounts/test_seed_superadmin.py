import os
from io import StringIO
from unittest.mock import patch
from django.core.management import call_command
from django.core.management.base import CommandError
from django.test import TestCase
from django.contrib.auth import get_user_model

User = get_user_model()


class SeedSuperadminCommandTests(TestCase):
    @patch.dict(os.environ, {"DJANGO_SUPERUSER_EMAIL": "", "DJANGO_SUPERUSER_PASSWORD": ""})
    def test_seed_superadmin_requires_explicit_credentials(self):
        with self.assertRaises(CommandError):
            call_command("seed_superadmin")
        self.assertFalse(User.objects.exists())

    @patch.dict(os.environ, {"DJANGO_SUPERUSER_EMAIL": "admin@example.com", "DJANGO_SUPERUSER_PASSWORD": "UniqueTestPassword123!"})
    def test_seed_superadmin_from_environment(self):
        out = StringIO()
        call_command("seed_superadmin", stdout=out)

        user = User.objects.get(email="admin@example.com")
        self.assertTrue(user.is_superuser)
        self.assertTrue(user.is_staff)
        self.assertTrue(user.is_active)
        self.assertEqual(user.role, "admin")
        self.assertTrue(user.check_password("UniqueTestPassword123!"))
        self.assertIn("successfully created", out.getvalue())

    def test_seed_superadmin_idempotent(self):
        # Run first time
        call_command("seed_superadmin", email="admin@example.com", password="UniqueTestPassword123!")
        self.assertEqual(User.objects.filter(email="admin@example.com").count(), 1)

        # Run second time without changes
        out = StringIO()
        call_command("seed_superadmin", email="admin@example.com", password="DifferentTestPassword123!", stdout=out)
        self.assertEqual(User.objects.filter(email="admin@example.com").count(), 1)
        self.assertIn("already exists", out.getvalue())
        self.assertTrue(User.objects.get(email="admin@example.com").check_password("UniqueTestPassword123!"))

    def test_seed_superadmin_custom_credentials(self):
        out = StringIO()
        call_command(
            "seed_superadmin",
            email="customadmin@cbmtv.online",
            password="MySecretPassword999!",
            name="Custom Admin",
            phone="+256711111111",
            location="Entebbe",
            country="Uganda",
            stdout=out,
        )

        user = User.objects.filter(email="customadmin@cbmtv.online").first()
        self.assertIsNotNone(user)
        self.assertEqual(user.name, "Custom Admin")
        self.assertEqual(user.phone, "+256711111111")
        self.assertEqual(user.location, "Entebbe")
        self.assertTrue(user.check_password("MySecretPassword999!"))
        self.assertTrue(user.is_superuser)
        self.assertTrue(user.is_staff)

    def test_seed_superadmin_reset_password(self):
        # Create user with initial password
        call_command("seed_superadmin", email="admin@example.com", password="OldPassword123!")
        user = User.objects.get(email="admin@example.com")
        self.assertTrue(user.check_password("OldPassword123!"))

        # Run with reset_password
        out = StringIO()
        call_command(
            "seed_superadmin",
            email="admin@example.com",
            password="NewPassword123!",
            reset_password=True,
            stdout=out,
        )
        user.refresh_from_db()
        self.assertTrue(user.check_password("NewPassword123!"))
        self.assertIn("Updated attributes: password", out.getvalue())

    def test_seed_superadmin_elevates_existing_standard_user(self):
        # Existing user without staff/superuser
        User.objects.create_user(
            email="promoted@cbmtv.online",
            name="Normal User",
            password="NormalPassword123!",
            role="user",
        )
        promoted = User.objects.get(email="promoted@cbmtv.online")
        self.assertFalse(promoted.is_superuser)
        self.assertFalse(promoted.is_staff)
        self.assertEqual(promoted.role, "user")

        # Run command targeting this user
        out = StringIO()
        call_command("seed_superadmin", email="promoted@cbmtv.online", password="UniqueTestPassword123!", stdout=out)
        promoted.refresh_from_db()
        self.assertTrue(promoted.is_superuser)
        self.assertTrue(promoted.is_staff)
        self.assertEqual(promoted.role, "admin")

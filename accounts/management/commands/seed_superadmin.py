"""
Seed or update a superadmin account.

Usage:
    python manage.py seed_superadmin
    python manage.py seed_superadmin --email admin@example.com --password SecretPassword123!
    python manage.py seed_superadmin --reset-password

Supports environment variables:
    DJANGO_SUPERUSER_EMAIL
    DJANGO_SUPERUSER_PASSWORD
    DJANGO_SUPERUSER_NAME
    DJANGO_SUPERUSER_PHONE
    DJANGO_SUPERUSER_LOCATION
    DJANGO_SUPERUSER_COUNTRY
"""
import os
from django.core.management.base import BaseCommand
from django.contrib.auth import get_user_model


class Command(BaseCommand):
    help = "Seeds a superadmin account from arguments or environment variables with safe defaults."

    def add_arguments(self, parser):
        parser.add_argument(
            "--email",
            type=str,
            default=None,
            help="Email address for the superadmin account (default: env DJANGO_SUPERUSER_EMAIL or admin@cbmtv.online)",
        )
        parser.add_argument(
            "--password",
            type=str,
            default=None,
            help="Password for the superadmin account (default: env DJANGO_SUPERUSER_PASSWORD or AdminPass123!)",
        )
        parser.add_argument(
            "--name",
            type=str,
            default=None,
            help="Full name for the superadmin account (default: env DJANGO_SUPERUSER_NAME or CBM TV Super Admin)",
        )
        parser.add_argument(
            "--phone",
            type=str,
            default=None,
            help="Phone number for the superadmin account (default: env DJANGO_SUPERUSER_PHONE or +256700000000)",
        )
        parser.add_argument(
            "--location",
            type=str,
            default=None,
            help="Location for the superadmin account (default: env DJANGO_SUPERUSER_LOCATION or Kampala)",
        )
        parser.add_argument(
            "--country",
            type=str,
            default=None,
            help="Country for the superadmin account (default: env DJANGO_SUPERUSER_COUNTRY or Uganda)",
        )
        parser.add_argument(
            "--reset-password",
            action="store_true",
            help="Update the password if the user already exists",
        )

    def handle(self, *args, **options):
        email = (options.get("email") or os.environ.get("DJANGO_SUPERUSER_EMAIL") or "admin@cbmtv.online").strip()
        password = options.get("password") or os.environ.get("DJANGO_SUPERUSER_PASSWORD") or "AdminPass123!"
        name = (options.get("name") or os.environ.get("DJANGO_SUPERUSER_NAME") or "CBM TV Super Admin").strip()
        phone = (options.get("phone") or os.environ.get("DJANGO_SUPERUSER_PHONE") or "+256700000000").strip()
        location = (options.get("location") or os.environ.get("DJANGO_SUPERUSER_LOCATION") or "Kampala").strip()
        country = (options.get("country") or os.environ.get("DJANGO_SUPERUSER_COUNTRY") or "Uganda").strip()
        reset_password = options.get("reset_password", False)

        if not email:
            self.stderr.write(self.style.ERROR("Error: An email address is required."))
            return

        if not password:
            self.stderr.write(self.style.ERROR("Error: A password is required."))
            return

        User = get_user_model()
        user = User.objects.filter(email=email).first()

        if user:
            # User already exists
            updated_fields = []
            if not user.is_superuser:
                user.is_superuser = True
                updated_fields.append("is_superuser")
            if not user.is_staff:
                user.is_staff = True
                updated_fields.append("is_staff")
            if not user.is_active:
                user.is_active = True
                updated_fields.append("is_active")
            if user.role != "admin":
                user.role = "admin"
                updated_fields.append("role")

            if reset_password:
                user.set_password(password)
                updated_fields.append("password")

            if updated_fields:
                user.save()
                self.stdout.write(
                    self.style.SUCCESS(
                        f"Superadmin user '{email}' already exists. Updated attributes: {', '.join(updated_fields)}."
                    )
                )
            else:
                self.stdout.write(
                    self.style.WARNING(
                        f"Superadmin user '{email}' already exists with full superuser privileges. No changes made. (Use --reset-password to update password)."
                    )
                )
        else:
            User.objects.create_superuser(
                email=email,
                name=name,
                phone=phone,
                location=location,
                country=country,
                password=password,
                role="admin",
            )
            self.stdout.write(
                self.style.SUCCESS(
                    f"Superadmin '{email}' successfully created with role=admin, staff=True, superuser=True."
                )
            )

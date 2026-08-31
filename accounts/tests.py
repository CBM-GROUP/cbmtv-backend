"""
Registration and login tests.

This module was empty. It exists now because POST /api/accounts/register/
returned a 500 for the most ordinary payload there is: email, name and password
with no optional profile fields. RegisterSerializer marks phone/location/country
optional (the model has blank=True, null=True), but UserManager.create_user
required them positionally, so `create_user(**validated_data)` raised TypeError.

The stream app hit this on every sign-up. The dashboard did not, because it
happens to send all five fields.
"""
from django.urls import reverse
from rest_framework import status
from rest_framework.test import APITestCase, APIClient

from .models import User


class RegistrationTests(APITestCase):
    def setUp(self):
        self.client = APIClient()
        self.register_url = reverse('register')
        self.login_url = reverse('login')

    def test_register_with_only_required_fields(self):
        """email + name + password alone must create a user, not raise."""
        response = self.client.post(
            self.register_url,
            {
                'email': 'minimal@example.com',
                'name': 'Minimal User',
                'password': 'pass1234',
            },
            format='json',
        )

        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        user = User.objects.get(email='minimal@example.com')
        self.assertEqual(user.name, 'Minimal User')
        self.assertEqual(user.role, 'user')
        # Optional fields stay empty rather than blocking the registration.
        self.assertIn(user.phone, (None, ''))
        self.assertIn(user.location, (None, ''))
        self.assertIn(user.country, (None, ''))

    def test_register_with_all_fields(self):
        response = self.client.post(
            self.register_url,
            {
                'email': 'full@example.com',
                'name': 'Full User',
                'password': 'pass1234',
                'phone': '+256700000000',
                'location': 'Kampala',
                'country': 'UG',
                'image': 'https://example.com/avatar.png',
            },
            format='json',
        )

        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        user = User.objects.get(email='full@example.com')
        self.assertEqual(user.phone, '+256700000000')
        self.assertEqual(user.location, 'Kampala')
        self.assertEqual(user.country, 'UG')
        self.assertEqual(user.image, 'https://example.com/avatar.png')

    def test_register_stores_a_usable_password_hash(self):
        """The submitted password must authenticate, i.e. it was hashed once."""
        self.client.post(
            self.register_url,
            {'email': 'hash@example.com', 'name': 'Hash', 'password': 'pass1234'},
            format='json',
        )
        user = User.objects.get(email='hash@example.com')
        self.assertNotEqual(user.password, 'pass1234')
        self.assertTrue(user.check_password('pass1234'))

    def test_duplicate_registration_is_rejected(self):
        payload = {
            'email': 'dupe@example.com',
            'name': 'First',
            'password': 'pass1234',
        }
        first = self.client.post(self.register_url, payload, format='json')
        self.assertEqual(first.status_code, status.HTTP_201_CREATED)

        second = self.client.post(
            self.register_url,
            {**payload, 'name': 'Second'},
            format='json',
        )
        self.assertEqual(second.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn('email', second.data)
        self.assertEqual(User.objects.filter(email='dupe@example.com').count(), 1)

    def test_register_without_email_is_rejected(self):
        response = self.client.post(
            self.register_url,
            {'name': 'No Email', 'password': 'pass1234'},
            format='json',
        )
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)

    def test_login_after_minimal_registration(self):
        """End to end: the account created above can obtain a token pair."""
        self.client.post(
            self.register_url,
            {'email': 'login@example.com', 'name': 'Login User', 'password': 'pass1234'},
            format='json',
        )

        response = self.client.post(
            self.login_url,
            {'email': 'login@example.com', 'password': 'pass1234'},
            format='json',
        )

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertIn('access', response.data)
        self.assertIn('refresh', response.data)
        # CustomTokenObtainPairSerializer attaches a user block the clients read.
        self.assertEqual(response.data['user']['email'], 'login@example.com')
        self.assertEqual(response.data['user']['name'], 'Login User')

    def test_login_with_wrong_password_is_rejected(self):
        self.client.post(
            self.register_url,
            {'email': 'wrong@example.com', 'name': 'Wrong', 'password': 'pass1234'},
            format='json',
        )
        response = self.client.post(
            self.login_url,
            {'email': 'wrong@example.com', 'password': 'not-the-password'},
            format='json',
        )
        self.assertEqual(response.status_code, status.HTTP_401_UNAUTHORIZED)


class UserManagerTests(APITestCase):
    """Direct manager coverage, independent of the serializer layer."""

    def test_create_user_without_optional_fields(self):
        user = User.objects.create_user(
            email='manager@example.com',
            name='Manager',
            password='pass1234',
        )
        self.assertEqual(user.email, 'manager@example.com')
        self.assertFalse(user.is_staff)
        self.assertTrue(user.check_password('pass1234'))

    def test_create_user_requires_email(self):
        with self.assertRaises(ValueError):
            User.objects.create_user(email='', name='No Email', password='x')

    def test_create_superuser_without_optional_fields(self):
        admin = User.objects.create_superuser(
            email='root@example.com',
            name='Root',
            password='pass1234',
        )
        self.assertTrue(admin.is_staff)
        self.assertTrue(admin.is_superuser)
        self.assertEqual(admin.role, 'admin')

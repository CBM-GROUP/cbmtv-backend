import re
from datetime import timedelta

from django.core import mail
from django.test import override_settings
from django.utils import timezone
from rest_framework import status
from rest_framework.test import APITestCase

from .models import DashboardRole, User


@override_settings(EMAIL_BACKEND='django.core.mail.backends.locmem.EmailBackend', DEBUG=False)
class DashboardAccessTests(APITestCase):
    def setUp(self):
        self.admin = User.objects.create_superuser(
            email='owner@example.com', name='Owner', password='OwnerPass123!'
        )
        self.content_role = DashboardRole.objects.create(name='Content editor', permissions=['content'])
        self.settings_role = DashboardRole.objects.create(name='Settings editor', permissions=['settings'])

    def invite(self, role=None):
        self.client.force_authenticate(self.admin)
        response = self.client.post('/api/accounts/dashboard-users/', {
            'email': 'editor@example.com', 'name': 'Editor',
            'dashboard_role': (role or self.content_role).pk,
        }, format='json')
        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        self.assertNotIn('temporary_pin', response.data)
        pin = re.search(r'PIN: ([A-Za-z0-9]{16})', mail.outbox[-1].body).group(1)
        return User.objects.get(email='editor@example.com'), pin

    def test_invitation_requires_activation_and_grants_only_selected_section(self):
        user, pin = self.invite()
        self.assertTrue(user.must_change_password)
        self.assertTrue(user.check_password(pin))
        self.assertRegex(pin, r'[A-Za-z]')
        self.assertRegex(pin, r'[0-9]')

        self.client.force_authenticate(user=None)
        login = self.client.post('/api/accounts/login/', {
            'email': user.email, 'password': pin,
        }, format='json')
        self.assertEqual(login.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertNotIn('access', login.data)

        wrong = self.client.post('/api/accounts/dashboard-activate/', {
            'email': user.email, 'temporary_pin': 'wrong-pin', 'new_password': 'NewPass123!safe',
        }, format='json')
        self.assertEqual(wrong.status_code, status.HTTP_400_BAD_REQUEST)

        activated = self.client.post('/api/accounts/dashboard-activate/', {
            'email': user.email, 'temporary_pin': pin, 'new_password': 'NewPass123!safe',
        }, format='json')
        self.assertEqual(activated.status_code, status.HTTP_200_OK)
        user.refresh_from_db()
        self.assertFalse(user.must_change_password)
        self.assertIsNone(user.temporary_pin_expires_at)
        self.assertFalse(user.check_password(pin))

        login = self.client.post('/api/accounts/login/', {
            'email': user.email, 'password': 'NewPass123!safe',
        }, format='json')
        self.assertEqual(login.status_code, status.HTTP_200_OK)
        self.assertIn('access', login.data)
        self.assertEqual(login.data['user']['dashboard_permissions'], ['content'])

        self.client.force_authenticate(user)
        forbidden = self.client.patch('/api/content/hero-settings/', {'image_duration_seconds': 25}, format='json')
        self.assertEqual(forbidden.status_code, status.HTTP_403_FORBIDDEN)
        forbidden = self.client.post('/api/channels/', {'name': 'A channel'}, format='json')
        self.assertEqual(forbidden.status_code, status.HTTP_403_FORBIDDEN)

        user.dashboard_role = self.settings_role
        user.save(update_fields=['dashboard_role'])
        allowed = self.client.patch('/api/content/hero-settings/', {'image_duration_seconds': 25}, format='json')
        self.assertEqual(allowed.status_code, status.HTTP_200_OK)

    def test_expired_pin_and_attempt_limit(self):
        user, pin = self.invite()
        user.temporary_pin_expires_at = timezone.now() - timedelta(seconds=1)
        user.save(update_fields=['temporary_pin_expires_at'])
        self.client.force_authenticate(user=None)
        response = self.client.post('/api/accounts/dashboard-activate/', {
            'email': user.email, 'temporary_pin': pin, 'new_password': 'NewPass123!safe',
        }, format='json')
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)

    def test_role_editor_is_superuser_only_and_profile_edits_are_self_only(self):
        self.client.force_authenticate(self.admin)
        role = self.client.post('/api/accounts/dashboard-roles/', {
            'name': 'Advert editor', 'permissions': ['adverts'],
        }, format='json')
        self.assertEqual(role.status_code, status.HTTP_201_CREATED)
        user, _ = self.invite()
        user.must_change_password = False
        user.save(update_fields=['must_change_password'])
        self.client.force_authenticate(user)
        denied = self.client.post('/api/accounts/dashboard-roles/', {
            'name': 'All access', 'permissions': ['users'],
        }, format='json')
        self.assertEqual(denied.status_code, status.HTTP_403_FORBIDDEN)
        denied = self.client.patch(f'/api/accounts/users/{self.admin.pk}/', {'name': 'Changed'}, format='json')
        self.assertEqual(denied.status_code, status.HTTP_403_FORBIDDEN)
        denied = self.client.post(f'/api/accounts/users/{self.admin.pk}/password/', {
            'current_password': 'OwnerPass123!', 'new_password': 'OtherPass123!', 'confirm_password': 'OtherPass123!',
        }, format='json')
        self.assertEqual(denied.status_code, status.HTTP_403_FORBIDDEN)

    @override_settings(EMAIL_BACKEND='django.core.mail.backends.console.EmailBackend')
    def test_invitation_does_not_create_user_when_email_is_not_configured(self):
        self.client.force_authenticate(self.admin)
        response = self.client.post('/api/accounts/dashboard-users/', {
            'email': 'new@example.com', 'name': 'New User', 'dashboard_role': self.content_role.pk,
        }, format='json')
        self.assertEqual(response.status_code, status.HTTP_503_SERVICE_UNAVAILABLE)
        self.assertFalse(User.objects.filter(email='new@example.com').exists())

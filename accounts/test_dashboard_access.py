from datetime import timedelta

from django.core.cache import cache
from django.core import mail
from django.test import override_settings
from django.utils import timezone
from rest_framework import status
from rest_framework.test import APITestCase

from .models import DashboardRole, User


@override_settings(EMAIL_BACKEND='django.core.mail.backends.locmem.EmailBackend', DEBUG=False)
class DashboardAccessTests(APITestCase):
    def setUp(self):
        cache.clear()
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
        pin = response.data['temporary_pin']
        self.assertEqual(len(pin), 16)
        listed = self.client.get('/api/accounts/dashboard-users/')
        self.assertNotIn('temporary_pin', listed.data[0])
        detail = self.client.get(f"/api/accounts/dashboard-users/{response.data['id']}/")
        self.assertNotIn('temporary_pin', detail.data)
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

    def test_five_bad_pins_lock_activation_until_reissue(self):
        user, pin = self.invite()
        self.client.force_authenticate(user=None)
        for _ in range(5):
            response = self.client.post('/api/accounts/dashboard-activate/', {
                'email': user.email, 'temporary_pin': 'wrong', 'new_password': 'NewPass123!safe',
            }, format='json')
            self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        blocked = self.client.post('/api/accounts/dashboard-activate/', {
            'email': user.email, 'temporary_pin': pin, 'new_password': 'NewPass123!safe',
        }, format='json')
        self.assertEqual(blocked.status_code, status.HTTP_400_BAD_REQUEST)
        self.client.force_authenticate(self.admin)
        reissued = self.client.post(f'/api/accounts/dashboard-users/{user.pk}/resend-invite/')
        self.assertEqual(reissued.status_code, status.HTTP_200_OK)
        self.client.force_authenticate(user=None)
        activated = self.client.post('/api/accounts/dashboard-activate/', {
            'email': user.email, 'temporary_pin': reissued.data['temporary_pin'],
            'new_password': 'NewPass123!safe',
        }, format='json')
        self.assertEqual(activated.status_code, status.HTTP_200_OK)

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
    def test_invitation_works_without_email_configuration(self):
        self.client.force_authenticate(self.admin)
        response = self.client.post('/api/accounts/dashboard-users/', {
            'email': 'new@example.com', 'name': 'New User', 'dashboard_role': self.content_role.pk,
        }, format='json')
        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        self.assertIn('temporary_pin', response.data)
        self.assertTrue(User.objects.filter(email='new@example.com').exists())

    def test_reissue_invalidates_old_pin_and_exposes_new_pin_once(self):
        user, first = self.invite()
        response = self.client.post(f'/api/accounts/dashboard-users/{user.pk}/resend-invite/')
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        second = response.data['temporary_pin']
        self.assertNotEqual(first, second)
        user.refresh_from_db()
        self.assertFalse(user.check_password(first))
        self.assertTrue(user.check_password(second))
        detail = self.client.get(f'/api/accounts/dashboard-users/{user.pk}/')
        self.assertNotIn('temporary_pin', detail.data)

    def test_invited_user_cannot_skip_activation_with_password_reset(self):
        user, _ = self.invite()
        self.client.force_authenticate(user=None)
        response = self.client.post('/api/accounts/password-reset/', {'email': user.email}, format='json')
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(len(mail.outbox), 0)

    def test_search_edit_deactivate_reactivate_and_refresh(self):
        user, pin = self.invite()
        self.client.force_authenticate(user=None)
        activated = self.client.post('/api/accounts/dashboard-activate/', {
            'email': user.email, 'temporary_pin': pin, 'new_password': 'NewPass123!safe',
        }, format='json')
        self.assertEqual(activated.status_code, status.HTTP_200_OK)
        login = self.client.post('/api/accounts/login/', {
            'email': user.email, 'password': 'NewPass123!safe',
        }, format='json')
        refresh = login.data['refresh']
        access = login.data['access']
        self.client.force_authenticate(self.admin)
        found = self.client.get('/api/accounts/dashboard-users/?search=editor@')
        self.assertEqual(len(found.data), 1)
        edited = self.client.patch(f'/api/accounts/dashboard-users/{user.pk}/', {
            'name': 'Editor Updated', 'phone': '12345', 'is_active': False,
        }, format='json')
        self.assertEqual(edited.status_code, status.HTTP_200_OK)
        self.assertEqual(edited.data['dashboard_status'], 'inactive')
        self.client.force_authenticate(user=None)
        denied_login = self.client.post('/api/accounts/login/', {
            'email': user.email, 'password': 'NewPass123!safe',
        }, format='json')
        self.assertNotIn('access', denied_login.data)
        denied_refresh = self.client.post('/api/accounts/token/refresh/', {'refresh': refresh}, format='json')
        self.assertNotIn('access', denied_refresh.data)
        self.client.credentials(HTTP_AUTHORIZATION=f'Bearer {access}')
        denied_profile = self.client.get('/api/accounts/profile/')
        self.assertEqual(denied_profile.status_code, status.HTTP_401_UNAUTHORIZED)
        self.client.credentials()
        self.client.force_authenticate(self.admin)
        restored = self.client.patch(f'/api/accounts/dashboard-users/{user.pk}/', {'is_active': True}, format='json')
        self.assertEqual(restored.status_code, status.HTTP_200_OK)
        self.assertEqual(restored.data['dashboard_status'], 'active')

    def test_viewer_and_inactive_user_cannot_manage_dashboard(self):
        viewer = User.objects.create_user(email='viewer@example.com', name='Viewer', password='ViewerPass123!')
        self.client.force_authenticate(viewer)
        self.assertEqual(self.client.get('/api/accounts/dashboard-users/').status_code, status.HTTP_403_FORBIDDEN)
        self.assertEqual(self.client.post('/api/accounts/dashboard-users/', {}).status_code, status.HTTP_403_FORBIDDEN)
        viewer.is_active = False
        viewer.save(update_fields=['is_active'])
        self.assertEqual(self.client.get('/api/accounts/dashboard-users/').status_code, status.HTTP_403_FORBIDDEN)

    def test_role_holder_can_manage_only_roles_within_own_permissions(self):
        manager_role = DashboardRole.objects.create(name='User manager', permissions=['users'])
        manager = User.objects.create_user(email='manager@example.com', name='Manager', password='ManagerPass123!')
        manager.dashboard_role = manager_role
        manager.save(update_fields=['dashboard_role'])
        self.client.force_authenticate(manager)
        self.assertEqual(self.client.get('/api/accounts/dashboard-users/').status_code, status.HTTP_200_OK)
        forbidden = self.client.post('/api/accounts/dashboard-users/', {
            'email': 'outside@example.com', 'name': 'Outside', 'dashboard_role': self.content_role.pk,
        }, format='json')
        self.assertEqual(forbidden.status_code, status.HTTP_403_FORBIDDEN)
        allowed = self.client.post('/api/accounts/dashboard-users/', {
            'email': 'inside@example.com', 'name': 'Inside', 'dashboard_role': manager_role.pk,
        }, format='json')
        self.assertEqual(allowed.status_code, status.HTTP_201_CREATED)
        self.assertEqual(self.client.patch(f'/api/accounts/dashboard-users/{manager.pk}/', {
            'dashboard_role': self.settings_role.pk,
        }, format='json').status_code, status.HTTP_409_CONFLICT)

    def test_self_deactivation_and_last_administrator_are_blocked(self):
        self.client.force_authenticate(self.admin)
        response = self.client.patch(f'/api/accounts/dashboard-users/{self.admin.pk}/', {'is_active': False}, format='json')
        self.assertEqual(response.status_code, status.HTTP_409_CONFLICT)
        other = User.objects.create_superuser(email='other@example.com', name='Other', password='OtherPass123!')
        self.client.force_authenticate(other)
        response = self.client.patch(f'/api/accounts/dashboard-users/{self.admin.pk}/', {'is_active': False}, format='json')
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        response = self.client.patch(f'/api/accounts/dashboard-users/{other.pk}/', {'is_active': False}, format='json')
        self.assertEqual(response.status_code, status.HTTP_409_CONFLICT)

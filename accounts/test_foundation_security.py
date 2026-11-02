import base64
from io import StringIO
from unittest.mock import patch

from django.contrib.auth.hashers import check_password
from django.core.cache import cache
from django.core.management import call_command
from django.core.management.base import CommandError
from rest_framework.test import APITestCase
from rest_framework_simplejwt.tokens import RefreshToken

from .forms import AccountCreationForm, AccountPasswordChangeForm
from .models import DashboardRole, User
from .password_reset import issue_reset_code
from .sessions import SessionRefreshToken, revoke_sessions
from .throttles import TrustedProxyRateThrottle
from .views import GoogleDirectLoginView, GoogleLinkView
from .password_reset import PasswordResetConfirmView, PasswordResetRequestView
from .dashboard_access import DashboardActivateView


class FoundationSecurityTests(APITestCase):
    def setUp(self):
        cache.clear()
        self.password = 'Strong-Initial-Password-123!'
        self.user = User.objects.create_user(email='viewer@example.com', name='Viewer', password=self.password)

    def authenticate(self, user=None, token=None):
        self.client.credentials(HTTP_AUTHORIZATION='Bearer ' + (token or str(SessionRefreshToken.for_user(user or self.user).access_token)))

    def test_registration_applies_validators_before_creating_an_account(self):
        for password in ('short', 'password', '123456789', 'registrant@example.com'):
            with self.subTest(password=password):
                result = self.client.post('/api/accounts/register/', {
                    'email': 'registrant@example.com', 'name': 'Registrant', 'password': password,
                }, format='json')
                self.assertEqual(result.status_code, 400)
                self.assertIn('password', result.data)
        self.assertFalse(User.objects.filter(email='registrant@example.com').exists())

    def test_password_change_rejects_weak_and_non_string_values_without_changing_password(self):
        self.authenticate()
        for password in ('short', 'password', '123456789', 'viewer@example.com', 12345):
            result = self.client.post(f'/api/accounts/users/{self.user.pk}/password/', {
                'current_password': self.password, 'new_password': password, 'confirm_password': password,
            }, format='json')
            self.assertEqual(result.status_code, 400)
        self.user.refresh_from_db()
        self.assertTrue(self.user.check_password(self.password))
        self.assertEqual(self.user.session_version, 0)

    def test_basic_authentication_is_not_an_api_authentication_path(self):
        basic = base64.b64encode(f'{self.user.email}:{self.password}'.encode()).decode()
        self.client.credentials(HTTP_AUTHORIZATION='Basic ' + basic)
        self.assertEqual(self.client.get('/api/accounts/profile/').status_code, 401)
        self.authenticate()
        self.assertEqual(self.client.get('/api/accounts/profile/').status_code, 200)

    def test_logout_requires_authentication(self):
        self.assertEqual(self.client.post('/api/accounts/logout/').status_code, 401)
        self.user.refresh_from_db()
        self.assertEqual(self.user.session_version, 0)

    def test_logout_revokes_access_and_refresh_on_all_devices_only_for_the_caller(self):
        first = SessionRefreshToken.for_user(self.user)
        second = SessionRefreshToken.for_user(self.user)
        other = User.objects.create_user(email='other@example.com', name='Other', password=self.password)
        other_token = SessionRefreshToken.for_user(other)
        self.authenticate(token=str(first.access_token))
        self.assertEqual(self.client.post('/api/accounts/logout/', {'user_id': other.pk}, format='json').status_code, 204)
        for refresh in (first, second):
            self.authenticate(token=str(refresh.access_token))
            self.assertEqual(self.client.get('/api/accounts/profile/').status_code, 401)
            self.client.credentials()
            self.assertEqual(self.client.post('/api/accounts/token/refresh/', {'refresh': str(refresh)}).status_code, 401)
        self.authenticate(token=str(other_token.access_token))
        self.assertEqual(self.client.get('/api/accounts/profile/').status_code, 200)
        self.client.credentials()
        login = self.client.post('/api/accounts/login/', {'email': self.user.email, 'password': self.password})
        self.assertEqual(login.status_code, 200)
        self.authenticate(token=login.data['access'])
        self.assertEqual(self.client.get('/api/accounts/profile/').status_code, 200)

    def test_legacy_tokens_work_until_explicit_revocation(self):
        legacy = RefreshToken.for_user(self.user)
        self.assertNotIn('session_version', legacy)
        self.authenticate(token=str(legacy.access_token))
        self.assertEqual(self.client.get('/api/accounts/profile/').status_code, 200)
        self.client.credentials()
        self.assertEqual(self.client.post('/api/accounts/token/refresh/', {'refresh': str(legacy)}).status_code, 200)
        self.authenticate(token=str(legacy.access_token))
        self.assertEqual(self.client.post('/api/accounts/logout/').status_code, 204)
        self.client.credentials()
        self.assertEqual(self.client.post('/api/accounts/token/refresh/', {'refresh': str(legacy)}).status_code, 401)

    def test_a_stale_profile_save_cannot_restore_revoked_sessions(self):
        stale = User.objects.get(pk=self.user.pk)
        revoke_sessions(self.user)
        revoke_sessions(stale)
        stale.name = 'Edited profile'
        stale.save()
        self.user.refresh_from_db()
        self.assertEqual(self.user.session_version, 2)
        self.assertEqual(self.user.name, 'Edited profile')

    def test_password_change_revokes_tokens_and_preserves_success_contract(self):
        old = SessionRefreshToken.for_user(self.user)
        self.authenticate(token=str(old.access_token))
        result = self.client.patch(f'/api/accounts/users/{self.user.pk}/password/', {
            'current_password': self.password, 'new_password': 'Another-Secure-Password-456!',
            'confirm_password': 'Another-Secure-Password-456!',
        }, format='json')
        self.assertEqual(result.status_code, 200)
        self.assertEqual(result.data, {'detail': 'Password updated successfully'})
        self.assertEqual(self.client.get('/api/accounts/profile/').status_code, 401)
        self.client.credentials()
        self.assertEqual(self.client.post('/api/accounts/token/refresh/', {'refresh': str(old)}).status_code, 401)
        login = self.client.post('/api/accounts/login/', {'email': self.user.email, 'password': 'Another-Secure-Password-456!'})
        self.assertEqual(login.status_code, 200)

    def test_password_reset_revokes_existing_tokens(self):
        old = SessionRefreshToken.for_user(self.user)
        code = issue_reset_code(self.user)
        result = self.client.post('/api/accounts/password-reset/confirm/', {
            'email': self.user.email, 'code': code, 'new_password': 'Another-Secure-Password-456!',
        }, format='json')
        self.assertEqual(result.status_code, 200)
        self.authenticate(token=str(old.access_token))
        self.assertEqual(self.client.get('/api/accounts/profile/').status_code, 401)
        self.client.credentials()
        self.assertEqual(self.client.post('/api/accounts/token/refresh/', {'refresh': str(old)}).status_code, 401)

    def test_deactivation_then_reactivation_does_not_restore_old_tokens(self):
        role = DashboardRole.objects.create(name='Editor', permissions=['content'])
        self.user.dashboard_role = role
        self.user.save(update_fields=['dashboard_role'])
        old = SessionRefreshToken.for_user(self.user)
        owner = User.objects.create_superuser(email='owner@example.com', name='Owner', password=self.password)
        self.authenticate(owner)
        for active in (False, True):
            result = self.client.patch(f'/api/accounts/dashboard-users/{self.user.pk}/', {'is_active': active}, format='json')
            self.assertEqual(result.status_code, 200)
        self.authenticate(token=str(old.access_token))
        self.assertEqual(self.client.get('/api/accounts/profile/').status_code, 401)

    def test_google_issuance_carries_current_session_version(self):
        self.user.session_version = 3
        self.user.google_subject = 'test-google-subject'
        self.user.save(update_fields=['session_version', 'google_subject'])
        with patch('accounts.views.verified_google_identity', return_value=('test-google-subject', self.user.email, self.user.name)):
            result = self.client.post('/api/accounts/login/google/direct/', {'id_token': 'test-token'}, format='json')
        self.assertEqual(result.status_code, 200)
        self.assertEqual(RefreshToken(result.data['refresh'])['session_version'], 3)
        self.authenticate(token=result.data['access'])
        self.assertEqual(self.client.get('/api/accounts/profile/').status_code, 200)

    def test_malformed_or_stale_session_version_is_rejected(self):
        for version in (True, '0', -1, 1):
            token = SessionRefreshToken.for_user(self.user)
            token['session_version'] = version
            self.authenticate(token=str(token.access_token))
            self.assertEqual(self.client.get('/api/accounts/profile/').status_code, 401)

    def test_reindex_errors_do_not_expose_provider_details(self):
        self.user.dashboard_role = DashboardRole.objects.create(name='Search editor', permissions=['content'])
        self.user.save(update_fields=['dashboard_role'])
        self.authenticate()
        with patch('content.views.rebuild_search_index', side_effect=RuntimeError('private-host secret-token')):
            with self.assertLogs('content.views', level='ERROR'):
                result = self.client.post('/api/content/reindex/')
        self.assertEqual(result.status_code, 502)
        self.assertEqual(result.data['message'], 'Reindex failed. Check the server logs.')
        self.assertNotIn(b'secret-token', result.content)

    def test_all_public_credential_throttles_use_the_trusted_proxy_policy(self):
        for view in (GoogleDirectLoginView, GoogleLinkView, PasswordResetRequestView,
                     PasswordResetConfirmView, DashboardActivateView):
            self.assertTrue(issubclass(view.throttle_classes[0], TrustedProxyRateThrottle))

    def test_seed_password_policy_rejects_weak_creation_and_reset_atomically(self):
        with self.assertRaises(CommandError):
            call_command('seed_superadmin', email='weak@example.com', password='short', stdout=StringIO())
        self.assertFalse(User.objects.filter(email='weak@example.com').exists())
        with self.assertRaises(CommandError):
            call_command('seed_superadmin', email=self.user.email, password='short', reset_password=True, stdout=StringIO())
        self.user.refresh_from_db()
        self.assertTrue(self.user.check_password(self.password))
        self.assertFalse(self.user.is_superuser)
        self.assertEqual(self.user.session_version, 0)

    def test_admin_creation_validates_and_hashes_password(self):
        data = {'email': 'newadmin@example.com', 'name': 'New Account', 'password1': 'short', 'password2': 'short'}
        weak = AccountCreationForm(data=data)
        self.assertFalse(weak.is_valid())
        data.update(password1=self.password, password2=self.password)
        valid = AccountCreationForm(data=data)
        self.assertTrue(valid.is_valid(), valid.errors)
        created = valid.save()
        self.assertTrue(check_password(self.password, created.password))
        self.assertNotEqual(created.password, self.password)

    def test_admin_password_change_validates_and_revokes_sessions(self):
        weak = AccountPasswordChangeForm(self.user, data={'password1': 'short', 'password2': 'short'})
        self.assertFalse(weak.is_valid())
        valid = AccountPasswordChangeForm(self.user, data={
            'password1': 'Another-Secure-Password-456!', 'password2': 'Another-Secure-Password-456!',
        })
        self.assertTrue(valid.is_valid(), valid.errors)
        valid.save()
        self.user.refresh_from_db()
        self.assertEqual(self.user.session_version, 1)
        self.assertTrue(self.user.check_password('Another-Secure-Password-456!'))


class UploadAuthorizationTests(APITestCase):
    def setUp(self):
        cache.clear()
        self.viewer = User.objects.create_user(email='upload-viewer@example.com', name='Viewer', password='test')

    def test_anonymous_and_viewer_requests_never_reach_upload_providers(self):
        with patch('content.views.create_upload_target') as single, patch('content.multipart_views.start_video_upload') as multipart:
            for user in (None, self.viewer):
                self.client.credentials(**({'HTTP_AUTHORIZATION': 'Bearer ' + str(SessionRefreshToken.for_user(user).access_token)} if user else {}))
                for path in ('upload-target/', 'multipart/start/', 'multipart/part/', 'multipart/complete/', 'multipart/abort/'):
                    result = self.client.post('/api/content/media/' + path, {}, format='json')
                    self.assertIn(result.status_code, (401, 403))
            single.assert_not_called()
            multipart.assert_not_called()

    def test_media_access_preserves_section_level_permissions(self):
        with patch('content.views.create_upload_target', return_value={'delivery_url': 'https://media.example/image.png'}) as provider:
            for section in ('channels', 'content', 'adverts', 'settings', 'users', 'analytics'):
                role = DashboardRole.objects.create(name=section, permissions=[section])
                self.viewer.dashboard_role = role
                self.viewer.save(update_fields=['dashboard_role'])
                self.client.credentials(HTTP_AUTHORIZATION='Bearer ' + str(SessionRefreshToken.for_user(self.viewer).access_token))
                provider.reset_mock()
                result = self.client.post('/api/content/media/upload-target/', {}, format='json')
                self.assertEqual(result.status_code, 200 if section in ('channels', 'content', 'adverts') else 403)
                self.assertEqual(provider.called, section in ('channels', 'content', 'adverts'))

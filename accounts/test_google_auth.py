import time
from unittest.mock import patch

from django.test import override_settings
from django.core.cache import cache
from google.auth import exceptions as google_exceptions
from rest_framework.test import APITestCase
from rest_framework_simplejwt.tokens import AccessToken, RefreshToken

from .models import DashboardRole, User


@override_settings(GOOGLE_OAUTH_CLIENT_IDS=['web-client', 'mobile-client'])
class GoogleAuthenticationTests(APITestCase):
    login_url = '/api/accounts/login/google/direct/'

    def setUp(self):
        cache.clear()

    def claims(self, **changes):
        result = {
            'sub': 'google-subject-1', 'email': 'viewer@example.com',
            'email_verified': True, 'name': 'Viewer', 'aud': 'web-client',
            'iss': 'https://accounts.google.com', 'exp': time.time() + 3600,
        }
        result.update(changes)
        return result

    def google_login(self, claims=None, payload=None):
        with patch('accounts.google_auth.google_id_token.verify_oauth2_token', return_value=claims or self.claims()) as verify:
            response = self.client.post(self.login_url, payload or {'id_token': 'google-signed-token'}, format='json')
        return response, verify

    def test_verified_token_creates_and_reuses_subject(self):
        response, verify = self.google_login()
        self.assertEqual(response.status_code, 201)
        self.assertIn('access', response.data)
        self.assertEqual(verify.call_args.args[0], 'google-signed-token')
        self.assertIsNone(verify.call_args.kwargs['audience'])
        user = User.objects.get(email='viewer@example.com')
        self.assertEqual(str(AccessToken(response.data['access'])['user_id']), str(user.pk))
        self.assertEqual(str(RefreshToken(response.data['refresh'])['user_id']), str(user.pk))
        self.client.credentials(HTTP_AUTHORIZATION=f"Bearer {response.data['access']}")
        self.assertEqual(self.client.get('/api/accounts/profile/').status_code, 200)
        self.client.credentials()
        self.assertEqual(user.google_subject, 'google-subject-1')
        self.assertFalse(user.has_usable_password())
        second, _ = self.google_login(self.claims(email='changed@example.com'))
        self.assertEqual(second.status_code, 200)
        self.assertEqual(second.data['user']['id'], user.pk)
        self.assertEqual(User.objects.count(), 1)

    def test_legacy_payload_cannot_issue_tokens(self):
        response = self.client.post(self.login_url, {
            'email': 'victim@example.com', 'google_id': 'forged', 'name': 'Victim',
        }, format='json')
        self.assertEqual(response.status_code, 400)
        self.assertNotIn('access', response.data)

    def test_invalid_signature_is_rejected(self):
        with patch('accounts.google_auth.google_id_token.verify_oauth2_token', side_effect=ValueError('bad signature')):
            response = self.client.post(self.login_url, {'id_token': 'forged'}, format='json')
        self.assertEqual(response.status_code, 401)
        self.assertNotIn('access', response.data)
        self.assertEqual(User.objects.count(), 0)

    def test_google_verifier_rejects_wrong_issuer_and_reports_transport_failure(self):
        with patch('accounts.google_auth.google_id_token.verify_oauth2_token', side_effect=google_exceptions.GoogleAuthError('wrong issuer')):
            rejected = self.client.post(self.login_url, {'id_token': 'wrong-issuer'}, format='json')
        self.assertEqual(rejected.status_code, 401)
        with patch('accounts.google_auth.google_id_token.verify_oauth2_token', side_effect=google_exceptions.TransportError('certificate fetch failed')):
            unavailable = self.client.post(self.login_url, {'id_token': 'unverifiable'}, format='json')
        self.assertEqual(unavailable.status_code, 503)
        self.assertEqual(User.objects.count(), 0)

    def test_expired_wrong_audience_wrong_issuer_unverified_and_missing_subject(self):
        invalid_claims = [
            self.claims(exp=time.time() - 1),
            self.claims(aud='attacker-client'),
            self.claims(iss='https://attacker.example'),
            self.claims(email_verified=False),
            self.claims(sub=''),
            self.claims(email=''),
            self.claims(email_verified=None),
            self.claims(exp=None),
        ]
        for claims in invalid_claims:
            with self.subTest(claims=claims):
                response, _ = self.google_login(claims)
                self.assertEqual(response.status_code, 401)
                self.assertNotIn('access', response.data)
        self.assertEqual(User.objects.count(), 0)

    def test_missing_configuration_fails_closed(self):
        with override_settings(GOOGLE_OAUTH_CLIENT_IDS=[]):
            response = self.client.post(self.login_url, {'id_token': 'token'}, format='json')
        self.assertEqual(response.status_code, 503)

    def test_existing_email_does_not_auto_link_even_if_google_provider(self):
        user = User.objects.create_user(email='viewer@example.com', name='Viewer', password='SafePassword123!')
        for provider in ('local', 'google'):
            user.auth_provider = provider
            user.save(update_fields=['auth_provider'])
            response, _ = self.google_login()
            self.assertEqual(response.status_code, 409)
            self.assertNotIn('access', response.data)
            user.refresh_from_db()
            self.assertIsNone(user.google_subject)

    def test_explicit_password_confirmed_link(self):
        user = User.objects.create_user(email='viewer@example.com', name='Viewer', password='SafePassword123!')
        self.client.force_authenticate(user)
        with patch('accounts.google_auth.google_id_token.verify_oauth2_token', return_value=self.claims()):
            wrong = self.client.post('/api/accounts/google/link/', {
                'id_token': 'token', 'current_password': 'wrong',
            }, format='json')
            self.assertEqual(wrong.status_code, 400)
            linked = self.client.post('/api/accounts/google/link/', {
                'id_token': 'token', 'current_password': 'SafePassword123!',
            }, format='json')
        self.assertEqual(linked.status_code, 200)
        user.refresh_from_db()
        self.assertEqual(user.google_subject, 'google-subject-1')
        self.client.force_authenticate(user=None)
        response, _ = self.google_login()
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data['user']['id'], user.pk)

    def test_link_rejects_mismatched_email_and_subject(self):
        user = User.objects.create_user(email='viewer@example.com', name='Viewer', password='SafePassword123!')
        self.client.force_authenticate(user)
        for claims in (self.claims(email='attacker@example.com'), self.claims(sub='other-sub')):
            if claims['sub'] == 'other-sub':
                user.google_subject = 'google-subject-1'
                user.save(update_fields=['google_subject'])
            with patch('accounts.google_auth.google_id_token.verify_oauth2_token', return_value=claims):
                response = self.client.post('/api/accounts/google/link/', {
                    'id_token': 'token', 'current_password': 'SafePassword123!',
                }, format='json')
            self.assertIn(response.status_code, (400, 409))

    def test_inactive_unactivated_and_staff_accounts_cannot_google_login(self):
        user = User.objects.create_user(email='viewer@example.com', name='Viewer', password=None)
        user.google_subject = 'google-subject-1'
        for updates in ({'is_active': False}, {'is_active': True, 'must_change_password': True},
                        {'must_change_password': False, 'is_staff': True},
                        {'is_staff': False, 'role': 'admin'}):
            for key, value in updates.items():
                setattr(user, key, value)
            user.save()
            response, _ = self.google_login()
            self.assertEqual(response.status_code, 403)
            self.assertNotIn('access', response.data)

    def test_dashboard_role_holder_cannot_google_login_or_link(self):
        role = DashboardRole.objects.create(name='Content editor', permissions=['content'])
        user = User.objects.create_user(
            email='viewer@example.com', name='Editor', password='SafePassword123!',
        )
        user.dashboard_role = role
        user.google_subject = 'google-subject-1'
        user.save(update_fields=['dashboard_role', 'google_subject'])
        response, _ = self.google_login()
        self.assertEqual(response.status_code, 403)
        self.assertNotIn('access', response.data)
        self.client.force_authenticate(user)
        with patch('accounts.google_auth.google_id_token.verify_oauth2_token', return_value=self.claims()):
            link = self.client.post('/api/accounts/google/link/', {
                'id_token': 'google-signed-token', 'current_password': 'SafePassword123!',
            }, format='json')
        self.assertEqual(link.status_code, 403)

    def test_password_login_and_refresh_respect_account_state(self):
        user = User.objects.create_user(email='local@example.com', name='Local', password='SafePassword123!')
        login = self.client.post('/api/accounts/login/', {
            'email': user.email, 'password': 'SafePassword123!',
        }, format='json')
        self.assertEqual(login.status_code, 200)
        refreshed = self.client.post('/api/accounts/token/refresh/', {'refresh': login.data['refresh']}, format='json')
        self.assertEqual(refreshed.status_code, 200)
        user.is_active = False
        user.save(update_fields=['is_active'])
        denied = self.client.post('/api/accounts/token/refresh/', {'refresh': login.data['refresh']}, format='json')
        self.assertEqual(denied.status_code, 400)

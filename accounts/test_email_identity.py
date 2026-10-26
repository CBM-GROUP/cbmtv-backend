from io import StringIO
from unittest.mock import patch

from django.core import mail
from django.core.cache import cache
from django.core.management import call_command
from django.test import override_settings
from rest_framework.test import APITestCase
from rest_framework.throttling import ScopedRateThrottle

from .models import User
from .models import DashboardRole
from .dashboard_access import set_temporary_pin
from .throttles import PasswordLoginThrottle
from types import SimpleNamespace
from .password_reset import issue_reset_code


@override_settings(PASSWORD_HASHERS=['django.contrib.auth.hashers.MD5PasswordHasher'],
                   EMAIL_BACKEND='django.core.mail.backends.locmem.EmailBackend')
class EmailIdentityTests(APITestCase):
    @override_settings(PASSWORD_LOGIN_NUM_PROXIES=1, PASSWORD_LOGIN_TRUSTED_PROXY_IPS=['192.0.2.10'])
    def test_forwarded_login_address_requires_explicitly_trusted_peer_and_valid_chain(self):
        throttle = PasswordLoginThrottle()
        request = SimpleNamespace(META={'REMOTE_ADDR': '192.0.2.11', 'HTTP_X_FORWARDED_FOR': '198.51.100.1'})
        self.assertEqual(throttle.get_ident(request), '192.0.2.11')
        request.META['REMOTE_ADDR'] = '192.0.2.10'
        self.assertEqual(throttle.get_ident(request), '198.51.100.1')
        request.META['HTTP_X_FORWARDED_FOR'] = 'spoofed-invalid-address'
        self.assertEqual(throttle.get_ident(request), '192.0.2.10')
        with override_settings(PASSWORD_LOGIN_NUM_PROXIES=2):
            request.META['HTTP_X_FORWARDED_FOR'] = '198.51.100.1'
            self.assertEqual(throttle.get_ident(request), '192.0.2.10')

    def test_ambiguous_invitation_activation_does_not_select_or_mutate_either_account(self):
        role = DashboardRole.objects.create(name='Invited editor', permissions=['content'])
        other = self.collision()
        for user in (self.user, other):
            user.dashboard_role = role
            user.save()
            pin = set_temporary_pin(user)
        response = self.client.post('/api/accounts/dashboard-activate/', {
            'email': other.email, 'temporary_pin': pin, 'new_password': 'A-New-Strong-2026-Pass!',
        })
        self.assertEqual(response.status_code, 400)
        for user in (self.user, other):
            user.refresh_from_db()
            self.assertTrue(user.must_change_password)
            self.assertEqual(user.temporary_pin_attempts, 0)

    def setUp(self):
        cache.clear()
        self.user = User.objects.create_user(email='MixedCase@example.com', name='Viewer', password='original-password')

    def collision(self):
        return User.objects.create_user(email='mixedcase@example.com', name='Other', password='other-password')

    def test_case_insensitive_login_preserves_identity_and_password(self):
        result = self.client.post('/api/accounts/login/', {
            'email': '  MIXEDCASE@EXAMPLE.COM  ', 'password': 'original-password',
        }, format='json')
        self.assertEqual(result.status_code, 200)
        self.assertEqual(result.data['user']['id'], self.user.pk)
        self.assertIn('access', result.data)
        self.assertIn('refresh', result.data)
        self.user.refresh_from_db()
        self.assertEqual(self.user.email, 'MixedCase@example.com')
        self.assertTrue(self.user.check_password('original-password'))

    def test_ambiguous_login_fails_closed_even_for_exact_casing(self):
        self.collision()
        for email, password in [('MixedCase@example.com', 'original-password'),
                                ('mixedcase@example.com', 'other-password'), ('absent@example.com', 'other-password')]:
            result = self.client.post('/api/accounts/login/', {'email': email, 'password': password})
            self.assertEqual(result.status_code, 401)
            self.assertEqual(str(result.data['detail']), 'No active account found with the given credentials')
        self.assertEqual(User.objects.count(), 2)

    def test_case_variant_registration_is_rejected_and_not_merged(self):
        result = self.client.post('/api/accounts/register/', {
            'email': 'MIXEDCASE@EXAMPLE.COM', 'name': 'Other', 'password': 'other-password',
        })
        self.assertEqual(result.status_code, 400)
        self.assertEqual(User.objects.count(), 1)
        self.user.refresh_from_db()
        self.assertTrue(self.user.check_password('original-password'))

    def test_case_insensitive_reset_targets_only_the_unique_account(self):
        code = issue_reset_code(self.user)
        response = self.client.post('/api/accounts/password-reset/confirm/', {
            'email': 'MIXEDCASE@EXAMPLE.COM', 'code': code, 'new_password': 'a-Much-Stronger-2026-pass',
        })
        self.assertEqual(response.status_code, 200)
        self.user.refresh_from_db()
        self.assertTrue(self.user.check_password('a-Much-Stronger-2026-pass'))

    def test_ambiguous_reset_does_not_send_mail_or_change_either_account(self):
        code = issue_reset_code(self.user)
        self.collision()
        before = len(mail.outbox)
        requested = self.client.post('/api/accounts/password-reset/', {'email': 'MIXEDCASE@example.com'})
        absent = self.client.post('/api/accounts/password-reset/', {'email': 'absent@example.com'})
        self.assertEqual(requested.data, absent.data)
        self.assertEqual(len(mail.outbox), before)
        result = self.client.post('/api/accounts/password-reset/confirm/', {
            'email': self.user.email, 'code': code, 'new_password': 'a-Much-Stronger-2026-pass',
        })
        self.assertEqual(result.status_code, 400)
        self.user.refresh_from_db()
        self.assertTrue(self.user.check_password('original-password'))

    def test_google_collision_never_auto_links_and_ambiguous_explicit_link_is_rejected(self):
        identity = ('unique-subject', 'MIXEDCASE@example.com', 'Viewer')
        with patch('accounts.views.verified_google_identity', return_value=identity):
            response = self.client.post('/api/accounts/login/google/direct/', {'id_token': 'test-only'})
            self.assertEqual(response.status_code, 409)
            self.client.force_authenticate(self.user)
            linked = self.client.post('/api/accounts/google/link/', {
                'id_token': 'test-only', 'current_password': 'original-password',
            })
            self.assertEqual(linked.status_code, 200)
            self.user.google_subject = None
            self.user.save(update_fields=['google_subject'])
            self.collision()
            rejected = self.client.post('/api/accounts/google/link/', {
                'id_token': 'test-only', 'current_password': 'original-password',
            })
            self.assertEqual(rejected.status_code, 400)
            self.user.refresh_from_db()
            self.assertIsNone(self.user.google_subject)

    def test_password_login_is_throttled_without_disclosing_account_state(self):
        with patch.dict(ScopedRateThrottle.THROTTLE_RATES, {'password_login': '2/minute'}):
            for attempt in range(2):
                self.assertEqual(self.client.post('/api/accounts/login/', {
                    'email': 'absent@example.com', 'password': 'wrong-password',
                }, HTTP_X_FORWARDED_FOR=f'192.0.2.{attempt}').status_code, 401)
            self.assertEqual(self.client.post('/api/accounts/login/', {
                'email': self.user.email, 'password': 'original-password',
            }, HTTP_X_FORWARDED_FOR='192.0.2.99').status_code, 429)

    def test_collision_command_is_read_only_and_does_not_print_emails(self):
        self.collision()
        output = StringIO()
        call_command('audit_email_collisions', stdout=output)
        self.assertIn('collision groups: 1; affected accounts: 2', output.getvalue())
        self.assertNotIn('@', output.getvalue())
        self.assertEqual(User.objects.count(), 2)

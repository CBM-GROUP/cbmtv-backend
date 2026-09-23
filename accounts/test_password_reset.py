"""
Emailed-code password reset.

The mobile app's "Forgot password" flow had a screen and no endpoint. These
cover the two routes it now calls, and the properties that make a six-digit
code safe to use: hashed at rest, single use, short lived, capped attempts, and
answers that do not reveal whether an email has an account.
"""
from datetime import timedelta
from unittest import mock

from django.core import mail
from django.core.cache import cache
from django.urls import reverse
from django.utils import timezone
from rest_framework import status
from rest_framework.test import APITestCase, APIClient

from . import password_reset
from .models import PasswordResetCode, User

NEW_PASSWORD = 'a-Much-Stronger-2026-pass'


class PasswordResetTests(APITestCase):
    def setUp(self):
        # Throttle counters live in the cache, which outlives a single test.
        cache.clear()
        self.client = APIClient()
        self.request_url = reverse('password_reset')
        self.confirm_url = reverse('password_reset_confirm')
        self.user = User.objects.create_user(
            email='forgetful@example.com', name='Forgetful', password='old-pass-1234'
        )

    def request_code(self, email='forgetful@example.com', code='123456'):
        with mock.patch.object(password_reset, 'generate_code', return_value=code):
            return self.client.post(self.request_url, {'email': email}, format='json')

    def confirm(self, code='123456', password=NEW_PASSWORD, email='forgetful@example.com'):
        return self.client.post(
            self.confirm_url,
            {'email': email, 'code': code, 'new_password': password},
            format='json',
        )

    def test_request_emails_a_code_and_stores_only_its_hash(self):
        response = self.request_code()

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(len(mail.outbox), 1)
        self.assertEqual(mail.outbox[0].to, ['forgetful@example.com'])
        self.assertIn('123456', mail.outbox[0].body)

        reset = PasswordResetCode.objects.get(user=self.user)
        self.assertNotIn('123456', reset.code_hash)
        self.assertGreater(reset.expires_at, timezone.now())

    def test_request_does_not_reveal_unknown_emails(self):
        known = self.request_code()
        unknown = self.request_code(email='nobody@example.com')

        self.assertEqual(unknown.status_code, status.HTTP_200_OK)
        self.assertEqual(unknown.data, known.data)
        self.assertEqual(len(mail.outbox), 1)

    def test_request_rejects_a_malformed_email(self):
        response = self.client.post(self.request_url, {'email': 'not-an-email'}, format='json')
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn('email', response.data)

    def test_confirm_resets_the_password_once(self):
        self.request_code()

        response = self.confirm()
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.user.refresh_from_db()
        self.assertTrue(self.user.check_password(NEW_PASSWORD))

        reused = self.confirm(password='another-Strong-2026-pass')
        self.assertEqual(reused.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn('code', reused.data)

    def test_new_password_can_log_in(self):
        self.request_code()
        self.confirm()

        login = self.client.post(
            reverse('login'),
            {'email': 'forgetful@example.com', 'password': NEW_PASSWORD},
            format='json',
        )
        self.assertEqual(login.status_code, status.HTTP_200_OK)

    def test_wrong_code_and_unknown_email_share_one_error(self):
        self.request_code()

        wrong = self.confirm(code='000000')
        unknown = self.confirm(email='nobody@example.com')

        self.assertEqual(wrong.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertEqual(wrong.data, {'code': ['Invalid or expired code.']})
        self.assertEqual(unknown.data, wrong.data)

    def test_code_dies_after_five_wrong_attempts(self):
        self.request_code()
        for _ in range(PasswordResetCode.MAX_ATTEMPTS):
            self.assertEqual(self.confirm(code='000000').status_code, status.HTTP_400_BAD_REQUEST)

        # Even the right code is refused now.
        self.assertEqual(self.confirm().status_code, status.HTTP_400_BAD_REQUEST)
        self.user.refresh_from_db()
        self.assertTrue(self.user.check_password('old-pass-1234'))

    def test_expired_code_is_refused(self):
        self.request_code()
        PasswordResetCode.objects.update(expires_at=timezone.now() - timedelta(seconds=1))

        self.assertEqual(self.confirm().status_code, status.HTTP_400_BAD_REQUEST)

    def test_new_request_invalidates_the_previous_code(self):
        self.request_code(code='111111')
        self.request_code(code='222222')

        self.assertEqual(self.confirm(code='111111').status_code, status.HTTP_400_BAD_REQUEST)
        self.assertEqual(self.confirm(code='222222').status_code, status.HTTP_200_OK)

    def test_weak_password_is_rejected_without_burning_the_code(self):
        self.request_code()

        weak = self.confirm(password='123')
        self.assertEqual(weak.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn('new_password', weak.data)

        self.assertEqual(self.confirm().status_code, status.HTTP_200_OK)

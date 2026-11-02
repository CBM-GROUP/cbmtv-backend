import json
import os
from pathlib import Path
import subprocess
import sys
from io import StringIO
from unittest.mock import patch

from django.core.management import call_command
from django.core.management.base import CommandError
from django.test import SimpleTestCase, override_settings


class HealthTests(SimpleTestCase):
    def test_liveness_does_not_touch_database(self):
        with patch('cbmtv.views.connection') as connection:
            connection.cursor.side_effect = RuntimeError('database down')
            response = self.client.get('/health/live/')
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json(), {'status': 'ok'})
        self.assertEqual(response['Cache-Control'], 'no-store')
        connection.cursor.assert_not_called()

    def test_readiness_executes_a_query_and_is_not_cached(self):
        with patch('cbmtv.views.connection') as connection:
            response = self.client.get('/health/')
        self.assertEqual(response.status_code, 200)
        connection.cursor.return_value.__enter__.return_value.execute.assert_called_once_with('SELECT 1')
        self.assertEqual(response.json(), {'status': 'ok'})
        self.assertEqual(response['Cache-Control'], 'no-store')

    def test_readiness_failure_does_not_disclose_exception_details(self):
        with patch('cbmtv.views.connection') as connection:
            connection.cursor.side_effect = RuntimeError('password=do-not-disclose host=private')
            response = self.client.get('/health/')
        self.assertEqual(response.status_code, 503)
        self.assertEqual(response.json(), {'status': 'unhealthy'})
        self.assertEqual(response['Cache-Control'], 'no-store')
        self.assertNotIn(b'do-not-disclose', response.content)

    @override_settings(MEILISEARCH_URL='')
    def test_operator_check_does_not_contact_unconfigured_search(self):
        out = StringIO()
        with patch('common.management.commands.check_dependencies.connection'), patch('common.management.commands.check_dependencies.get_search_index') as search:
            call_command('check_dependencies', stdout=out)
        self.assertEqual(json.loads(out.getvalue()), {'database': 'ok', 'search': 'not_configured'})
        search.assert_not_called()

    @override_settings(MEILISEARCH_URL='https://private.example')
    def test_operator_check_reports_search_failure_without_credentials(self):
        out = StringIO()
        with patch('common.management.commands.check_dependencies.connection'), patch('common.management.commands.check_dependencies.get_search_index', side_effect=RuntimeError('secret-token')):
            with self.assertRaises(CommandError):
                call_command('check_dependencies', stdout=out)
        self.assertEqual(json.loads(out.getvalue()), {'database': 'ok', 'search': 'unavailable'})
        self.assertNotIn('secret-token', out.getvalue())


class SettingsSecurityTests(SimpleTestCase):
    def load_settings(self, **values):
        env = {**os.environ, 'PYTHON_DOTENV_DISABLED': '1', 'DEBUG': 'false', **values}
        if 'SECRET_KEY' not in values:
            env.pop('SECRET_KEY', None)
        return subprocess.run([sys.executable, '-B', '-c', "import runpy; runpy.run_path('cbmtv/settings.py')"],
                              cwd=Path(__file__).resolve().parent.parent, env=env, text=True, capture_output=True)

    def test_production_requires_a_strong_explicit_secret(self):
        for key in (None, '', 'short', 'x' * 60, 'django-insecure-' + 'example' * 10):
            with self.subTest(key=key):
                result = self.load_settings(**({'SECRET_KEY': key} if key is not None else {}))
                self.assertNotEqual(result.returncode, 0)
                self.assertIn('Configure a strong SECRET_KEY', result.stderr)

    def test_explicit_strong_secret_and_debug_development_start(self):
        strong = self.load_settings(SECRET_KEY='a-strong-test-only-key-0123456789abcdef-0123456789abcdef')
        self.assertEqual(strong.returncode, 0, strong.stderr)
        development = self.load_settings(DEBUG='true')
        self.assertEqual(development.returncode, 0, development.stderr)

    def test_test_settings_reject_a_shared_or_production_database(self):
        env = {**os.environ, 'CBMTV_TEST_DATABASE_URL': 'postgresql://test:unused@production.example/production'}
        result = subprocess.run([sys.executable, '-B', '-c', 'import cbmtv.test_settings'],
                                cwd=Path(__file__).resolve().parent.parent, env=env, text=True, capture_output=True)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn('isolated cbmtv_foundation_test database', result.stderr)

"""Isolated verification: never loads .env or uses a shared database."""
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
os.environ['PYTHON_DOTENV_DISABLED'] = '1'
os.environ['DJANGO_SETTINGS_MODULE'] = 'cbmtv.settings'
from django.conf import settings

settings.DATABASES = {'default': {'ENGINE': 'django.db.backends.sqlite3', 'NAME': ':memory:'}}
if '--postgres' in sys.argv:
    import dj_database_url
    from urllib.parse import urlparse
    from uuid import uuid4
    url = os.environ.get('PHASE1_TEST_DATABASE_URL', '')
    target = urlparse(url)
    if target.scheme not in ('postgres', 'postgresql') or target.hostname not in ('localhost', '127.0.0.1', '::1') or target.path != '/cbmtv_phase1_test':
        raise SystemExit('PostgreSQL verification requires an explicit loopback PHASE1_TEST_DATABASE_URL ending /cbmtv_phase1_test.')
    database = dj_database_url.parse(url)
    database['TEST'] = {'NAME': 'test_cbmtv_phase1_' + uuid4().hex[:12]}
    settings.DATABASES = {'default': database}
    sys.argv.remove('--postgres')
settings.SECRET_KEY = 'phase1-disposable-verification-only-secret'
settings.MEILISEARCH_URL = ''
settings.EMAIL_BACKEND = 'django.core.mail.backends.locmem.EmailBackend'
settings.PASSWORD_HASHERS = ['django.contrib.auth.hashers.MD5PasswordHasher']
import django
django.setup()
from django.core.management import call_command
from django.test.utils import get_runner

if sys.argv[1:2] == ['--checks']:
    call_command('check')
    call_command('makemigrations', check=True, dry_run=True)
else:
    labels = sys.argv[1:] or ['accounts', 'content', 'channel', 'library', 'common']
    sys.exit(bool(get_runner(settings)(verbosity=1, interactive=False).run_tests(labels)))

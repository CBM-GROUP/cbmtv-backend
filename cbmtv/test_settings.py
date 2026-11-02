"""Isolated CI settings. Never load .env, production credentials, or shared databases."""
import os
from urllib.parse import urlparse

os.environ['PYTHON_DOTENV_DISABLED'] = '1'
os.environ['DEBUG'] = 'false'
os.environ['SECRET_KEY'] = 'foundation-tests-only-secret-0123456789abcdef-0123456789abcdef'

from .settings import *  # noqa: F403,E402

test_url = os.environ.get('CBMTV_TEST_DATABASE_URL', '')
if test_url:
    target = urlparse(test_url)
    if (target.scheme not in ('postgres', 'postgresql')
            or target.hostname not in ('localhost', '127.0.0.1', '::1', 'postgres')
            or target.path != '/cbmtv_foundation_test'):
        raise ImproperlyConfigured('Tests require an isolated cbmtv_foundation_test database on loopback or the postgres test service.')
    DATABASES = {'default': dj_database_url.parse(test_url, conn_max_age=0)}
else:
    DATABASES = {'default': {'ENGINE': 'django.db.backends.sqlite3', 'NAME': ':memory:'}}

ALLOWED_HOSTS = ['testserver', 'localhost', '127.0.0.1']
EMAIL_BACKEND = 'django.core.mail.backends.locmem.EmailBackend'
PASSWORD_HASHERS = ['django.contrib.auth.hashers.MD5PasswordHasher']
PASSWORD_LOGIN_NUM_PROXIES = 0
PASSWORD_LOGIN_TRUSTED_PROXY_IPS = []
MEILISEARCH_URL = ''
MEILISEARCH_MASTER_KEY = ''
GOOGLE_OAUTH_CLIENT_IDS = []
MEDIA_STORAGE = {'PROVIDER': 's3', 'CREDENTIALS_MODE': 'access_keys'}
CACHES = {'default': {'BACKEND': 'django.core.cache.backends.locmem.LocMemCache', 'LOCATION': 'foundation-tests'}}

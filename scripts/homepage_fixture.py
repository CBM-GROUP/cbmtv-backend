"""Disposable loopback fixture for the homepage browser acceptance test.

Run from the backend root: python scripts/homepage_fixture.py
Uses in-memory SQLite, never .env or an existing database. Do not deploy.
"""
import os
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
os.environ['DJANGO_SETTINGS_MODULE'] = 'cbmtv.test_settings'
os.environ['PYTHON_DOTENV_DISABLED'] = '1'
os.environ['PYTHONDONTWRITEBYTECODE'] = '1'
os.environ.pop('CBMTV_TEST_DATABASE_URL', None)

import django
django.setup()

from django.conf import settings
from django.core.management import call_command
from accounts.models import User
from channel.models import Channel
from content.models import Content, Episode, Season
from homepage.models import FeaturedChannel, FeaturedTitle, HomepageConfiguration
from library.models import WatchProgress

settings.CORS_ALLOWED_ORIGINS = ['http://127.0.0.1:3017', 'http://127.0.0.1:3018']
call_command('migrate', verbosity=0)
editor = User.objects.create_user(email='editor@homepage.test', name='Homepage editor', password='fixture-pass')
editor.is_staff = True
editor.save()
viewer = User.objects.create_user(email='viewer@homepage.test', name='Viewer', password='fixture-pass')
other = User.objects.create_user(email='other@homepage.test', name='Other viewer', password='fixture-pass')
channels = [Channel.objects.create(name=f'Homepage Channel {i + 1}') for i in range(8)]
titles = [Content.objects.create(
    title=f'Homepage Title {i + 1}', channel=channels[i % 8], status='approved',
    category=category, format='standalone', content_type=legacy,
    description='Local acceptance fixture for CBM TV homepage editorial management.',
    streaming_link='https://example.invalid/fixture.mp4',
) for i, (category, legacy) in enumerate([
    ('movies', 'movie'), ('tv_shows', 'tv_show'), ('documentaries', 'documentary'), ('animations', 'animations'),
] * 3)]
draft = Content.objects.create(title='Private fixture title', channel=channels[0], status='preview', content_type='movie')
inactive = Channel.objects.create(name='Inactive fixture channel', is_active=False)
Content.objects.create(title='Inactive fixture title', channel=inactive, status='approved', content_type='movie', category='movies', format='standalone')
series = Content.objects.create(title='Homepage Series', channel=channels[0], status='approved', content_type='series', category='tv_shows', format='series')
season = Season.objects.create(content=series, title='First season', season_number=1)
first = Episode.objects.create(season=season, title='First episode', episode_number=1, streaming_link='https://example.invalid/1.mp4')
second = Episode.objects.create(season=season, title='Next episode', episode_number=2, streaming_link='https://example.invalid/2.mp4')
HomepageConfiguration.objects.create()
FeaturedTitle.objects.bulk_create([FeaturedTitle(content=title, position=i) for i, title in enumerate(titles[:2])])
FeaturedChannel.objects.bulk_create([FeaturedChannel(channel=channel, position=i) for i, channel in enumerate(channels)])
WatchProgress.objects.create(user=viewer, content=titles[0], position_seconds=42, duration_seconds=100)
WatchProgress.objects.create(user=viewer, content=series, episode=first, position_seconds=100, duration_seconds=100, completed=True)
WatchProgress.objects.create(user=other, content=titles[1], position_seconds=60, duration_seconds=100)
print('Disposable homepage fixture ready on 127.0.0.1:8097', flush=True)
call_command('runserver', '127.0.0.1:8097', use_reloader=False, use_threading=False, verbosity=0)

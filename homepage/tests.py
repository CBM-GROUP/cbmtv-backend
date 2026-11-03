from copy import deepcopy
from datetime import datetime, timedelta, timezone as dt_timezone
from django.apps import apps
from django.db import connection
from django.test import TestCase, TransactionTestCase
from django.test.utils import CaptureQueriesContext
from django.utils import timezone
from rest_framework.test import APIClient
from accounts.models import DashboardRole, User
from channel.models import Channel
from content.models import Content, Episode, MiniSeries, Season, contentadverts
from content.serializers import ContentSerializer
from library.models import WatchProgress
from .defaults import default_sections
from .models import FeaturedTitle, HomepageConfiguration
from .service import build_homepage, configuration, release_cutoff


class HomepageTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.channel = Channel.objects.create(name='Editorial')
        cls.admin = User.objects.create_user(email='editor@example.com', name='Editor', password='pass1234')
        cls.admin.is_staff = True
        cls.admin.save()
        cls.viewer = User.objects.create_user(email='viewer@example.com', name='Viewer', password='pass1234')
        cls.other = User.objects.create_user(email='other@example.com', name='Other', password='pass1234')
        cls.a = Content.objects.create(title='First', channel=cls.channel, status='approved', content_type='movie', category='movies', format='standalone')
        cls.b = Content.objects.create(title='Second', channel=cls.channel, status='approved', content_type='movie', category='movies', format='standalone')
        cls.draft = Content.objects.create(title='Private', channel=cls.channel, status='preview', content_type='movie')

    def setUp(self):
        self.client = APIClient()

    def payload(self):
        return {'sections': default_sections(), 'featured_titles': [
            {'content': self.b.pk, 'start_at': None, 'end_at': None},
            {'content': self.a.pk, 'start_at': None, 'end_at': None},
        ], 'featured_channels': [{'channel': self.channel.pk}], 'new_releases': {'duration': 30, 'unit': 'days'}}

    def save(self, payload=None):
        self.client.force_authenticate(self.admin)
        return self.client.put('/api/homepage/manage/', payload or self.payload(), format='json')

    def section(self, key, response=None):
        response = response or self.client.get('/api/homepage/')
        self.assertEqual(response.status_code, 200)
        return next(row for row in response.data['sections'] if row['id'] == key)

    def test_anonymous_defaults_and_no_database_write(self):
        response = self.client.get('/api/homepage/')
        self.assertEqual([row['id'] for row in response.data['sections']], [row['id'] for row in default_sections()])
        self.assertEqual(self.section('continue_watching', response)['items'], [])
        self.assertFalse(HomepageConfiguration.objects.exists())
        self.assertEqual(response['Cache-Control'], 'private, no-store')
        self.assertIn('Authorization', response['Vary'])

    def test_featured_order_publication_and_no_adverts(self):
        contentadverts.objects.create(advert_name='Never a hero', show_in_hero=True)
        self.assertEqual(self.save().status_code, 200)
        self.client.force_authenticate(None)
        self.assertEqual([r['id'] for r in self.section('featured_hero')['items']], [self.b.pk, self.a.pk])
        Content.objects.filter(pk=self.b.pk).update(status='rejected')
        self.assertEqual([r['id'] for r in self.section('featured_hero')['items']], [self.a.pk])

    def test_fallback_and_scheduled_fallback_not_bypassed(self):
        Content.objects.filter(pk=self.a.pk).update(is_featured=True)
        self.assertEqual([r['id'] for r in self.section('featured_hero')['items']], [self.a.pk])
        FeaturedTitle.objects.create(content=self.a, position=0, start_at=timezone.now() + timedelta(days=1))
        self.assertEqual(self.section('featured_hero')['items'], [])

    def test_scheduling_start_inclusive_end_exclusive(self):
        now = timezone.now()
        FeaturedTitle.objects.create(content=self.a, position=0, start_at=now, end_at=now + timedelta(hours=1))
        config = configuration()
        def ids(at):
            return build_homepage(config, now=at)['sections'][0]['items']
        self.assertEqual(ids(now - timedelta(microseconds=1)), [])
        self.assertEqual([r['id'] for r in ids(now)], [self.a.pk])
        self.assertEqual(ids(now + timedelta(hours=1)), [])

    def test_schedule_requires_timezone_and_normalizes_offsets(self):
        data = self.payload()
        data['featured_titles'][0]['start_at'] = '2026-11-03T12:00:00'
        self.assertEqual(self.save(data).status_code, 400)
        data['featured_titles'][0].update(start_at='2026-11-03T12:00:00+03:00', end_at='2026-11-03T10:00:00Z')
        self.assertEqual(self.save(data).status_code, 200)
        self.assertEqual(FeaturedTitle.objects.first().start_at.hour, 9)

    def test_invalid_selection_duplicate_schedule_atomic_save(self):
        self.assertEqual(self.save().status_code, 200)
        invalid_payloads = []
        data = self.payload(); data['featured_titles'][0]['content'] = self.draft.pk; invalid_payloads.append(data)
        data = self.payload(); data['featured_titles'].append(data['featured_titles'][0]); invalid_payloads.append(data)
        data = self.payload(); data['featured_titles'][0].update(start_at='2026-11-02T12:00:00Z', end_at='2026-11-02T11:00:00Z'); invalid_payloads.append(data)
        data = self.payload(); data['featured_channels'] *= 2; invalid_payloads.append(data)
        for data in invalid_payloads:
            self.assertEqual(self.save(data).status_code, 400)
            self.assertEqual(list(FeaturedTitle.objects.values_list('content_id', flat=True)), [self.b.pk, self.a.pk])

    def test_channel_order_inactive_empty_and_deleted(self):
        second = Channel.objects.create(name='Second channel')
        empty = Channel.objects.create(name='No public titles')
        Content.objects.create(title='Channel title', content_type='movie', channel=second, status='approved')
        data = self.payload(); data['featured_channels'] = [{'channel': second.pk}, {'channel': empty.pk}, {'channel': self.channel.pk}]
        self.assertEqual(self.save(data).status_code, 200)
        self.assertEqual([row['id'] for row in self.section('featured_channels')['items']], [second.pk, self.channel.pk])
        second.is_active = False; second.save()
        self.assertEqual([row['id'] for row in self.section('featured_channels')['items']], [self.channel.pk])
        self.assertEqual(self.save(data).status_code, 400)
        second.delete()
        self.assertEqual(len(self.section('featured_channels')['items']), 1)

    def test_layout_order_optional_visibility_and_required_sections(self):
        data = self.payload()
        data['sections'][3], data['sections'][4] = data['sections'][4], data['sections'][3]
        data['sections'][-1]['enabled'] = False
        self.assertEqual(self.save(data).status_code, 200)
        self.assertEqual(self.client.get('/api/homepage/').data['sections'][3]['id'], 'tv_shows')
        self.assertFalse(self.section('animations')['enabled'])
        for key in ('featured_hero', 'continue_watching'):
            invalid = deepcopy(data)
            next(row for row in invalid['sections'] if row['id'] == key)['enabled'] = False
            self.assertEqual(self.save(invalid).status_code, 400)
        invalid = deepcopy(data); invalid['sections'].pop(2)
        self.assertEqual(self.save(invalid).status_code, 400)
        invalid = deepcopy(data); invalid['sections'][0], invalid['sections'][1] = invalid['sections'][1], invalid['sections'][0]
        self.assertEqual(self.save(invalid).status_code, 400)

    def test_progress_privacy_and_saved_position(self):
        WatchProgress.objects.create(user=self.viewer, content=self.a, position_seconds=42, duration_seconds=100)
        WatchProgress.objects.create(user=self.other, content=self.b, position_seconds=60, duration_seconds=100)
        self.assertEqual(self.section('continue_watching')['items'], [])
        self.client.force_authenticate(self.viewer)
        items = self.section('continue_watching')['items']
        self.assertEqual([row['content']['id'] for row in items], [self.a.pk])
        self.assertEqual(items[0]['position_seconds'], 42)
        self.assertEqual(self.client.get(f'/api/library/progress/{self.b.pk}/').status_code, 404)
        self.assertEqual(self.client.get(f'/api/library/progress/{self.a.pk}/').data['position_seconds'], 42)
        self.client.force_authenticate(self.other)
        self.assertEqual([r['content']['id'] for r in self.section('continue_watching')['items']], [self.b.pk])

    def test_episode_completion_projects_next_without_changing_progress(self):
        series = Content.objects.create(title='Series', channel=self.channel, status='approved', category='tv_shows', format='series', content_type='series')
        season = Season.objects.create(content=series, title='S1', season_number=1)
        first = Episode.objects.create(season=season, title='E1', episode_number=1, streaming_link='https://example.com/1.mp4')
        second = Episode.objects.create(season=season, title='E2', episode_number=2, streaming_link='https://example.com/2.mp4')
        progress = WatchProgress.objects.create(user=self.viewer, content=series, episode=first, position_seconds=100, duration_seconds=100, completed=True)
        self.client.force_authenticate(self.viewer)
        item = self.section('continue_watching')['items'][0]
        self.assertEqual(item['episode'], second.pk)
        self.assertEqual(item['position_seconds'], 0)
        progress.refresh_from_db(); self.assertEqual(progress.episode_id, first.pk)
        progress.episode = second; progress.save()
        self.assertEqual(self.section('continue_watching')['items'], [])

    def test_completed_miniseries_part_projects_next_playable_part(self):
        series = Content.objects.create(title='Parts', channel=self.channel, status='approved', category='documentaries', format='series', content_type='miniseries')
        first = MiniSeries.objects.create(content=series, title='Part 1', miniseries_no=1, streaming_link='https://example.com/1.mp4')
        MiniSeries.objects.create(content=series, title='Missing media', miniseries_no=2)
        third = MiniSeries.objects.create(content=series, title='Part 3', miniseries_no=3, streaming_link='https://example.com/3.mp4')
        WatchProgress.objects.create(user=self.viewer, content=series, miniseries=first, position_seconds=100, duration_seconds=100, completed=True)
        self.client.force_authenticate(self.viewer)
        item = self.section('continue_watching')['items'][0]
        self.assertEqual(item['miniseries'], third.pk)
        self.assertIsNone(item['episode'])
        self.assertFalse(item['completed'])

    def test_inactive_channel_has_consistent_public_boundary(self):
        from unittest.mock import patch
        from content.views import build_search_documents
        self.channel.is_active = False
        self.channel.save()
        for path in ('/api/content/', '/api/content/movies/'):
            response = self.client.get(path)
            self.assertEqual(response.status_code, 200)
            self.assertEqual(response.data['results'], [])
        self.assertNotIn(self.channel.pk, [row['id'] for row in self.client.get('/api/channels/').data['results']])
        self.assertEqual(self.client.get(f'/api/content/{self.a.pk}/').status_code, 404)
        self.assertEqual(self.client.get(f'/api/channels/{self.channel.pk}/').status_code, 404)
        with patch('content.views.get_search_index', side_effect=RuntimeError('offline')):
            self.assertEqual(self.client.get('/api/content/search/', {'q': 'First'}).data['hits'], [])
        self.assertEqual(build_search_documents(), [])
        self.client.force_authenticate(self.admin)
        self.assertIn(self.channel.pk, [row['id'] for row in self.client.get('/api/channels/', {'scope': 'dashboard'}).data['results']])
        self.assertEqual(self.client.get('/api/content/', {'scope': 'dashboard'}).data['count'], 3)

    def test_personalized_errors_and_library_responses_are_private(self):
        for user in (None, self.viewer):
            self.client.force_authenticate(user)
            for path in ('/api/homepage/', '/api/homepage/manage/', '/api/library/progress/', f'/api/library/progress/{self.a.pk}/', '/api/library/saved/'):
                response = self.client.get(path)
                self.assertEqual(response['Cache-Control'], 'private, no-store')
                self.assertIn('Authorization', response['Vary'])

    def test_new_releases_first_publication_cutoff_order_cap(self):
        now = timezone.now()
        Content.objects.all().update(first_published_at=None)
        for i in range(8):
            title = Content.objects.create(title=f'Release {i}', channel=self.channel, content_type='movie', status='approved')
            Content.objects.filter(pk=title.pk).update(first_published_at=now - timedelta(days=i))
        Content.objects.filter(pk=self.a.pk).update(first_published_at=now - timedelta(days=31))
        Content.objects.filter(pk=self.draft.pk).update(first_published_at=now)
        Content.objects.filter(pk=self.b.pk).update(first_published_at=now + timedelta(days=1))
        rows = self.section('new_releases')['items']
        self.assertEqual([r['title'] for r in rows], [f'Release {i}' for i in range(6)])
        data = self.payload(); data['new_releases'] = {'duration': 1, 'unit': 'hours'}
        self.assertEqual(self.save(data).status_code, 200)
        self.assertEqual(len(self.section('new_releases')['items']), 1)

    def test_calendar_months_years_and_exact_cutoff(self):
        march = datetime(2024, 3, 31, 12, tzinfo=dt_timezone.utc)
        self.assertEqual(release_cutoff(march, 1, 'months').day, 29)
        self.assertEqual(release_cutoff(march.replace(month=2, day=29), 1, 'years').day, 28)
        Content.objects.all().update(first_published_at=None)
        Content.objects.filter(pk=self.a.pk).update(first_published_at=march - timedelta(days=30))
        rows = next(r for r in build_homepage(configuration(), now=march)['sections'] if r['id'] == 'new_releases')['items']
        self.assertEqual([r['id'] for r in rows], [self.a.pk])

    def test_first_publication_immutable_and_api_readonly(self):
        first = self.a.first_published_at
        self.a.status = 'preview'; self.a.save()
        self.a.first_published_at = timezone.now(); self.a.status = 'approved'; self.a.save()
        self.assertEqual(self.a.first_published_at, first)
        serializer = ContentSerializer(self.a, data={'first_published_at': timezone.now().isoformat()}, partial=True)
        self.assertTrue(serializer.is_valid()); serializer.save()
        self.a.refresh_from_db(); self.assertEqual(self.a.first_published_at, first)
        self.draft.status = 'approved'; self.draft.save(update_fields=['status'])
        self.draft.refresh_from_db(); self.assertIsNotNone(self.draft.first_published_at)

    def test_unknown_legacy_history_preserved_across_republishing(self):
        from importlib import import_module
        migrate = import_module('content.migrations.0018_preserve_unknown_publication_history').preserve_unknown
        Content.objects.filter(pk=self.a.pk).update(first_published_at=None)
        from types import SimpleNamespace
        migrate(apps, SimpleNamespace(connection=connection))
        self.a.refresh_from_db()
        self.a.status = 'preview'; self.a.save()
        self.a.status = 'approved'; self.a.save()
        self.assertIsNone(self.a.first_published_at)
        self.assertTrue(self.a.publication_history_unknown)

    def test_first_publication_waits_for_channel_activation(self):
        channel = Channel.objects.create(name='Not launched', is_active=False)
        title = Content.objects.create(title='Waiting for launch', channel=channel, content_type='movie', status='approved')
        self.assertIsNone(title.first_published_at)
        unknown = Content.objects.create(title='Unknown history', channel=channel, content_type='movie', status='approved')
        Content.objects.filter(pk=unknown.pk).update(publication_history_unknown=True)
        channel.is_active = True
        channel.save(update_fields=['is_active'])
        title.refresh_from_db(); unknown.refresh_from_db()
        self.assertIsNotNone(title.first_published_at)
        self.assertIsNone(unknown.first_published_at)
        published = title.first_published_at
        channel.is_active = False; channel.save()
        channel.is_active = True; channel.save()
        title.refresh_from_db()
        self.assertEqual(title.first_published_at, published)

    def test_partial_save_uses_persisted_channel_eligibility(self):
        inactive = Channel.objects.create(name='Inactive parent', is_active=False)
        title = Content.objects.create(title='Not yet public', channel=inactive, content_type='movie', status='approved')
        title.channel = self.channel
        title.title = 'Metadata edit'
        title.save(update_fields=['title'])
        title.refresh_from_db()
        self.assertEqual(title.channel_id, inactive.pk)
        self.assertIsNone(title.first_published_at)

    def test_permissions_preview_no_write_or_progress(self):
        for user in (None, self.viewer):
            self.client.force_authenticate(user)
            for method, path in [('get', '/api/homepage/manage/'), ('put', '/api/homepage/manage/'), ('post', '/api/homepage/preview/')]:
                response = getattr(self.client, method)(path, self.payload(), format='json')
                self.assertIn(response.status_code, (401, 403))
        role = DashboardRole.objects.create(name='Other section', permissions=['channels'])
        self.viewer.dashboard_role = role; self.viewer.save()
        self.client.force_authenticate(self.viewer)
        self.assertEqual(self.client.get('/api/homepage/manage/').status_code, 403)
        role.permissions = ['content']; role.save()
        self.viewer.refresh_from_db(); self.client.force_authenticate(self.viewer)
        WatchProgress.objects.create(user=self.viewer, content=self.a, position_seconds=42)
        preview = self.client.post('/api/homepage/preview/', self.payload(), format='json')
        self.assertEqual(preview.status_code, 200)
        self.assertEqual(self.section('continue_watching', preview)['items'], [])
        self.assertFalse(HomepageConfiguration.objects.exists())
        self.assertFalse(FeaturedTitle.objects.exists())

    def test_categories_use_taxonomy_and_exclude_private_titles(self):
        self.a.category = 'documentaries'; self.a.format = 'series'; self.a.save()
        self.draft.category = 'documentaries'; self.draft.save()
        self.assertEqual([r['id'] for r in self.section('documentaries')['items']], [self.a.pk])
        self.assertEqual([r['id'] for r in self.section('movies')['items']], [self.b.pk])

    def test_legacy_apis_keep_shapes_and_ignore_management(self):
        self.assertEqual(self.save().status_code, 200)
        self.client.force_authenticate(None)
        self.assertIn('results', self.client.get('/api/content/').data)
        self.assertIn('results', self.client.get('/api/channels/').data)
        self.assertIsInstance(self.client.get('/api/content/adverts/').data, list)
        self.assertIn('image_duration_seconds', self.client.get('/api/content/hero-settings/').data)
        self.assertEqual(self.client.get(f'/api/content/{self.draft.pk}/').status_code, 404)

    def test_homepage_queries_are_bounded(self):
        self.assertEqual(self.save().status_code, 200)
        with CaptureQueriesContext(connection) as queries:
            build_homepage(configuration())
        self.assertLessEqual(len(queries), 24)


class PublicationTransactionTests(TransactionTestCase):
    def test_model_writer_owns_its_transaction_and_preserves_first_publication(self):
        # Unlike TestCase, no ambient transaction hides missing row-lock scope.
        channel = Channel.objects.create(name='Transaction test')
        title = Content.objects.create(title='Direct writer', channel=channel, content_type='movie', status='preview')
        title.status = 'approved'
        title.save(update_fields=['status'])
        title.refresh_from_db()
        published = title.first_published_at
        self.assertIsNotNone(published)
        title.status = 'preview'
        title.save()
        title.status = 'approved'
        title.save()
        title.refresh_from_db()
        self.assertEqual(title.first_published_at, published)

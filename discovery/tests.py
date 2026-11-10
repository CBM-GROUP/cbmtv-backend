import uuid
from datetime import timedelta
from django.core.management import call_command
from django.utils import timezone
from rest_framework.test import APITestCase
from accounts.models import DashboardRole, User
from channel.models import Channel
from content.models import Content, Genre
from library.models import SavedItem, WatchProgress
from .aggregation import aggregate_batch, rebuild
from .models import AggregationState, ContentHourlyMetric, DiscoveryEvent, PlaybackSession, QueryHourlyMetric


class DiscoveryTests(APITestCase):
    def setUp(self):
        self.viewer = User.objects.create_user('viewer@cbmtv.test', 'Viewer', password='test')
        self.other = User.objects.create_user('other@cbmtv.test', 'Other', password='test')
        self.channel = Channel.objects.create(name='Discovery channel')
        self.movie = Content.objects.create(channel=self.channel, title='Ocean film', content_type='movie',
            category='movies', format='standalone', status='approved', director='A Director')
        self.related = Content.objects.create(channel=self.channel, title='Ocean sequel', content_type='movie',
            category='movies', format='standalone', status='approved', director='A Director')
        self.hidden = Content.objects.create(channel=self.channel, title='Ocean draft', content_type='movie',
            category='movies', format='standalone', status='preview')
        self.genre = Genre.objects.create(name='Adventure', is_active=True, for_movies=True)
        self.movie.genres.add(self.genre)
        self.related.genres.add(self.genre)

    def event(self, kind, **fields):
        return self.client.post('/api/discovery/events/', {'event_id': str(uuid.uuid4()), 'type': kind, **fields}, format='json')

    def test_search_receipt_counts_and_click_conversion_are_real_and_idempotent(self):
        response = self.client.get('/api/content/search/', {'q': 'Ocean', 'search_by': 'title'})
        self.assertEqual(response.status_code, 200)
        receipt = response.data['receipt']
        self.assertEqual(response.data['total'], 2)
        search_id = str(uuid.uuid4())
        payload = {'event_id': search_id, 'type': 'search_submitted', 'receipt': receipt}
        self.assertEqual(self.client.post('/api/discovery/events/', payload, format='json').status_code, 201)
        self.assertEqual(self.client.post('/api/discovery/events/', payload, format='json').data['duplicate'], True)
        self.assertEqual(self.event('search_selected', receipt=receipt, search_id=search_id, content=self.movie.pk).status_code, 201)
        self.assertEqual(self.event('search_selected', receipt=receipt, search_id=search_id, content=self.related.pk).status_code, 400)
        aggregate_batch()
        metric = QueryHourlyMetric.objects.get(term='ocean', mode='title')
        self.assertEqual((metric.submissions, metric.conversions), (1, 1))
        self.assertEqual(ContentHourlyMetric.objects.get(content=self.movie).search_clicks, 1)
        self.assertEqual(aggregate_batch(), 0)
        self.assertEqual(self.client.get('/api/discovery/rankings/most-searched/').data['items'][0]['id'], self.movie.pk)

    def test_no_results_sensitive_terms_and_forged_click_are_rejected(self):
        receipt = self.client.get('/api/content/search/', {'q': 'nothingmatches'}).data['receipt']
        self.assertEqual(self.event('search_submitted', receipt=receipt).status_code, 201)
        sensitive = self.client.get('/api/content/search/', {'q': 'password=secret'}).data['receipt']
        self.assertIsNone(sensitive)
        self.assertEqual(self.event('search_submitted', receipt='fake').status_code, 400)
        search_id = str(DiscoveryEvent.objects.get(kind='search_submitted').pk)
        self.assertEqual(self.event('search_selected', receipt=receipt, search_id=search_id, content=self.movie.pk).status_code, 400)
        aggregate_batch()
        self.assertEqual(QueryHourlyMetric.objects.get(term='nothingmatches').zero_results, 1)

    def test_playback_requires_own_session_and_qualified_watch_dedupes_by_day(self):
        session = PlaybackSession.objects.create(user=self.viewer, content=self.movie)
        self.assertEqual(self.event('playback_started', session_id=str(session.pk), position_seconds=0,
            duration_seconds=120).status_code, 403)
        self.client.force_authenticate(self.other)
        self.assertEqual(self.event('playback_started', session_id=str(session.pk), position_seconds=0,
            duration_seconds=120).status_code, 400)
        self.client.force_authenticate(self.viewer)
        self.assertEqual(self.event('playback_started', session_id=str(session.pk), position_seconds=0,
            duration_seconds=120).status_code, 201)
        self.assertEqual(self.event('playback_progress', session_id=str(session.pk), position_seconds=30,
            duration_seconds=120).data['accepted'], False)
        PlaybackSession.objects.filter(pk=session.pk).update(started_at=timezone.now() - timedelta(seconds=35))
        self.assertEqual(self.event('playback_progress', session_id=str(session.pk), position_seconds=30,
            duration_seconds=120).data['qualified'], True)
        self.assertEqual(self.event('playback_progress', session_id=str(session.pk), position_seconds=60,
            duration_seconds=120).data['accepted'], False)
        second = PlaybackSession.objects.create(user=self.viewer, content=self.movie, started_at=timezone.now()-timedelta(seconds=35), first_position=0)
        self.assertEqual(self.event('playback_progress', session_id=str(second.pk), position_seconds=30,
            duration_seconds=120).data['qualified'], True)
        aggregate_batch()
        self.assertEqual(ContentHourlyMetric.objects.get(content=self.movie).qualified_views, 1)
        self.assertEqual(self.client.get('/api/discovery/rankings/most-watched/').data['items'][0]['id'], self.movie.pk)

    def test_completion_does_not_count_fast_seek_and_aggregate_rebuild_is_stable(self):
        self.client.force_authenticate(self.viewer)
        session = PlaybackSession.objects.create(user=self.viewer, content=self.movie)
        self.event('playback_started', session_id=str(session.pk), position_seconds=0, duration_seconds=120)
        self.assertEqual(self.event('playback_completed', session_id=str(session.pk), position_seconds=120,
            duration_seconds=120).status_code, 400)
        PlaybackSession.objects.filter(pk=session.pk).update(started_at=timezone.now()-timedelta(seconds=35))
        self.assertEqual(self.event('playback_completed', session_id=str(session.pk), position_seconds=120,
            duration_seconds=120).status_code, 201)
        aggregate_batch()
        before = list(ContentHourlyMetric.objects.values_list('qualified_views', 'completions'))
        rebuild()
        aggregate_batch()
        self.assertEqual(before, list(ContentHourlyMetric.objects.values_list('qualified_views', 'completions')))

    def test_related_and_recommended_respect_publication_and_account(self):
        related = self.client.get(f'/api/discovery/similar/{self.movie.pk}/')
        self.assertEqual([row['id'] for row in related.data['items']], [self.related.pk])
        self.assertEqual(self.client.get(f'/api/discovery/similar/{self.hidden.pk}/').status_code, 404)
        self.assertEqual(self.client.get('/api/discovery/recommended/').status_code, 401)
        self.client.force_authenticate(self.viewer)
        cold = self.client.get('/api/discovery/recommended/').data
        self.assertFalse(cold['personalized'])
        SavedItem.objects.create(user=self.viewer, content=self.movie)
        personalized = self.client.get('/api/discovery/recommended/').data
        self.assertTrue(personalized['personalized'])
        self.assertEqual([item['id'] for item in personalized['items']], [self.related.pk])
        self.client.force_authenticate(self.other)
        self.assertFalse(self.client.get('/api/discovery/recommended/').data['personalized'])

    def test_trending_uses_recent_growth_and_excludes_unpublished_titles(self):
        now = timezone.now().replace(minute=0, second=0, microsecond=0)
        ContentHourlyMetric.objects.create(hour=now - timedelta(days=3), content=self.movie, qualified_views=100)
        ContentHourlyMetric.objects.create(hour=now - timedelta(hours=2), content=self.movie, qualified_views=2)
        ContentHourlyMetric.objects.create(hour=now - timedelta(hours=2), content=self.related,
            qualified_views=3, search_clicks=2)
        ContentHourlyMetric.objects.create(hour=now - timedelta(hours=2), content=self.hidden,
            qualified_views=1000, search_clicks=1000)
        ids = [row['id'] for row in self.client.get('/api/discovery/rankings/trending/').data['items']]
        self.assertEqual(ids, [self.related.pk, self.movie.pk])
        self.assertEqual(self.client.get('/api/discovery/rankings/trending/', {'period': '30d'}).status_code, 400)

    def test_channel_top_content_uses_qualified_views_in_range(self):
        hour = timezone.now().replace(minute=0, second=0, microsecond=0)
        ContentHourlyMetric.objects.create(hour=hour, content=self.movie, qualified_views=4)
        ContentHourlyMetric.objects.create(hour=hour, content=self.related, qualified_views=2)
        ContentHourlyMetric.objects.create(hour=hour, content=self.hidden, qualified_views=99)
        analyst = User.objects.create_superuser('channel-analyst@cbmtv.test', 'Analyst', password='test')
        self.client.force_authenticate(analyst)
        data = self.client.get('/api/discovery/analytics/').data
        self.assertEqual(data['channel_top_content'][self.channel.pk], [
            {'content_id': self.movie.pk, 'title': self.movie.title, 'count': 4},
            {'content_id': self.related.pk, 'title': self.related.title, 'count': 2},
        ])

    def test_anonymous_event_rate_limit(self):
        self.client.defaults['REMOTE_ADDR'] = '198.51.100.71'
        for _ in range(30):
            self.assertEqual(self.event('detail_opened', content=self.movie.pk).status_code, 201)
        self.assertEqual(self.event('detail_opened', content=self.movie.pk).status_code, 429)

    def test_staff_analytics_permission_empty_and_retention_dry_run(self):
        self.assertEqual(self.client.get('/api/discovery/analytics/').status_code, 401)
        self.client.force_authenticate(self.viewer)
        self.assertEqual(self.client.get('/api/discovery/analytics/').status_code, 403)
        role = DashboardRole.objects.create(name='Analyst', permissions=['analytics'])
        analyst = User.objects.create_user('analyst@cbmtv.test', 'Analyst', password='test')
        analyst.dashboard_role = role
        analyst.save(update_fields=['dashboard_role'])
        self.client.force_authenticate(analyst)
        data = self.client.get('/api/discovery/analytics/').data
        self.assertFalse(data['available'])
        aggregate_batch()
        self.assertTrue(self.client.get('/api/discovery/analytics/').data['available'])
        old = DiscoveryEvent.objects.create(kind='detail_opened', content=self.movie)
        DiscoveryEvent.objects.filter(pk=old.pk).update(occurred_at=timezone.now()-timedelta(days=91))
        call_command('cleanup_discovery')
        self.assertTrue(DiscoveryEvent.objects.filter(pk=old.pk).exists())
        call_command('cleanup_discovery', apply=True)
        self.assertFalse(DiscoveryEvent.objects.filter(pk=old.pk).exists())

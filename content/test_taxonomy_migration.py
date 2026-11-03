"""Upgrade proof against a disposable test database containing legacy relationships."""

from django.db import connection
from django.db.migrations.executor import MigrationExecutor
from django.test import TransactionTestCase
from accounts.models import User
from channel.models import Channel
from library.models import SavedItem, WatchProgress


class LegacyTaxonomyUpgradeTests(TransactionTestCase):
    def test_legacy_rows_and_relationships_survive_additive_classification(self):
        before = [('content', '0014_hero_settings_and_advert_placement')]
        after = [('content', '0015_content_taxonomy')]
        executor = MigrationExecutor(connection)
        latest = executor.loader.graph.leaf_nodes()
        self.addCleanup(lambda: MigrationExecutor(connection).migrate(latest))
        executor.migrate(before)
        old = executor.loader.project_state(before).apps
        ids = {}
        try:
            Content = old.get_model('content', 'Content')
            Season = old.get_model('content', 'Season')
            Episode = old.get_model('content', 'Episode')
            MiniSeries = old.get_model('content', 'MiniSeries')

            channel = Channel.objects.create(name='Legacy Nature')
            viewer = User.objects.create(email='legacy-taxonomy@example.com', name='Legacy', password='unusable')
            movie = Content.objects.create(title='Film', content_type='movie', channel_id=channel.pk,
                status='approved', genre='Comedy', streaming_link='https://cdn.example/film.mp4')
            animated = Content.objects.create(title='Animated', content_type='animations', channel_id=channel.pk,
                status='approved', genre='Comedy')
            documentary = Content.objects.create(title='Nature Film', content_type='documentary',
                channel_id=channel.pk, status='approved', genre='Nature')
            series = Content.objects.create(title='Unknown Series', content_type='series',
                channel_id=channel.pk, status='approved', genre='Drama')
            season = Season.objects.create(content=series, title='First', season_number=1)
            episode = Episode.objects.create(season=season, title='Pilot', episode_number=1,
                streaming_link='https://cdn.example/pilot.mp4')
            mini = Content.objects.create(title='Legacy Mini', content_type='miniseries', channel_id=channel.pk,
                status='preview', genre='Drama')
            mini_part = MiniSeries.objects.create(content=mini, title='Part', miniseries_no=1)
            original = Content.objects.create(title='Original', content_type='original', channel_id=channel.pk,
                status='approved', genre='Comedy')
            music = Content.objects.create(title='Music', content_type='music', channel_id=channel.pk,
                status='approved', genre='Comedy')
            saved = SavedItem.objects.create(user_id=viewer.pk, content_id=movie.pk)
            progress = WatchProgress.objects.create(user_id=viewer.pk, content_id=series.pk, episode_id=episode.pk,
                position_seconds=30, duration_seconds=90)
            ids = {name: row.pk for name, row in locals().items() if name in (
                'channel', 'movie', 'animated', 'documentary', 'series', 'season', 'episode',
                'mini', 'mini_part', 'original', 'music', 'saved', 'progress')}
        finally:
            MigrationExecutor(connection).migrate(after)

        new = MigrationExecutor(connection).loader.project_state(after).apps
        Content = new.get_model('content', 'Content')
        Genre = new.get_model('content', 'Genre')
        Season = new.get_model('content', 'Season')
        Episode = new.get_model('content', 'Episode')
        MiniSeries = new.get_model('content', 'MiniSeries')

        movie = Content.objects.get(pk=ids['movie'])
        self.assertEqual((movie.category, movie.format, movie.content_type), ('movies', 'standalone', 'movie'))
        self.assertEqual((movie.channel_id, movie.status, movie.streaming_link),
                         (ids['channel'], 'approved', 'https://cdn.example/film.mp4'))
        self.assertEqual(Content.objects.get(pk=ids['animated']).category, 'animations')
        self.assertEqual(Content.objects.get(pk=ids['documentary']).category, 'documentaries')
        self.assertEqual((Content.objects.get(pk=ids['series']).category, Content.objects.get(pk=ids['series']).format),
                         (None, 'series'))
        self.assertEqual((Content.objects.get(pk=ids['mini']).category, Content.objects.get(pk=ids['mini']).format),
                         (None, 'series'))
        for key in ('original', 'music'):
            row = Content.objects.get(pk=ids[key])
            self.assertIsNone(row.category)
            self.assertIsNone(row.format)
            self.assertEqual(row.genre, 'Comedy')
        comedy = Genre.objects.get(name_key='comedy')
        self.assertTrue(comedy.for_movies and comedy.for_animations)
        self.assertEqual(set(comedy.titles.values_list('pk', flat=True)), {ids['movie'], ids['animated']})
        self.assertTrue(Genre.objects.filter(name_key='nature', for_documentaries=True).exists())
        self.assertEqual(Content.objects.get(pk=ids['series']).genre, 'Drama')
        self.assertEqual(Season.objects.get(pk=ids['season']).content_id, ids['series'])
        self.assertEqual(Episode.objects.get(pk=ids['episode']).season_id, ids['season'])
        self.assertEqual(MiniSeries.objects.get(pk=ids['mini_part']).content_id, ids['mini'])
        self.assertEqual(SavedItem.objects.get(pk=ids['saved']).content_id, ids['movie'])
        self.assertEqual(WatchProgress.objects.get(pk=ids['progress']).episode_id, ids['episode'])

"""Upgrade proof for legacy publication history and homepage's additive schema."""
from django.db import connection
from django.db.migrations.executor import MigrationExecutor
from django.test import TransactionTestCase

from accounts.models import User
from library.models import WatchProgress


class HomepageMigrationTests(TransactionTestCase):
    def test_legacy_upgrade_preserves_history_ownership_and_progress(self):
        executor = MigrationExecutor(connection)
        latest = executor.loader.graph.leaf_nodes()
        self.addCleanup(lambda: MigrationExecutor(connection).migrate(latest))
        before = [
            ('homepage', None),
            ('content', '0016_content_cast'),
            ('channel', '0003_channel_description_channel_logo_url'),
        ]
        executor.migrate(before)
        old = executor.loader.project_state([target for target in before if target[1]]).apps
        Channel = old.get_model('channel', 'Channel')
        Content = old.get_model('content', 'Content')
        Season = old.get_model('content', 'Season')
        Episode = old.get_model('content', 'Episode')
        channel = Channel.objects.create(name='Legacy migration channel')
        approved = Content.objects.create(title='Previously published', status='approved',
            channel=channel, content_type='series', category='tv_shows', format='series')
        draft = Content.objects.create(title='Unknown draft history', status='preview',
            channel=channel, content_type='movie', category='movies', format='standalone')
        season = Season.objects.create(content=approved, title='Season', season_number=1)
        episode = Episode.objects.create(season=season, title='Episode', episode_number=1)
        viewer = User.objects.create_user(email='migration@homepage.test', name='Viewer', password='test-pass')
        progress = WatchProgress.objects.create(user_id=viewer.pk, content_id=approved.pk,
            episode_id=episode.pk, position_seconds=42, duration_seconds=100)

        executor = MigrationExecutor(connection)
        executor.migrate(latest)
        new = executor.loader.project_state(latest).apps
        NewContent = new.get_model('content', 'Content')
        NewChannel = new.get_model('channel', 'Channel')
        self.assertTrue(NewChannel.objects.get(pk=channel.pk).is_active)
        for pk in (approved.pk, draft.pk):
            row = NewContent.objects.get(pk=pk)
            self.assertIsNone(row.first_published_at)
            self.assertTrue(row.publication_history_unknown)
            self.assertEqual(row.channel_id, channel.pk)
        progress.refresh_from_db()
        self.assertEqual((progress.content_id, progress.episode_id, progress.position_seconds),
                         (approved.pk, episode.pk, 42))
        self.assertFalse(new.get_model('homepage', 'HomepageConfiguration').objects.exists())
        self.assertFalse(new.get_model('homepage', 'FeaturedTitle').objects.exists())
        # Reapplying the already migrated graph changes neither legacy data nor history.
        MigrationExecutor(connection).migrate(latest)
        self.assertEqual(NewContent.objects.filter(publication_history_unknown=True,
                                                   first_published_at__isnull=True).count(), 2)

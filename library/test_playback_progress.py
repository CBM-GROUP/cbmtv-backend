from django.urls import reverse
from content.models import Episode, MiniSeries
from .tests import LibraryTestCase
from .models import WatchProgress, PartProgress


class PartProgressTests(LibraryTestCase):
    def test_episode_completion_retains_other_episode_history_and_snapshot(self):
        second = Episode.objects.create(season=self.season, title='E2', episode_number=2)
        self.login()
        for episode, position in ((self.episode, 100), (second, 35)):
            result = self.client.post(reverse('library-progress'), {'content': self.series.pk, 'episode': episode.pk,
                'position_seconds': position, 'duration_seconds': 100}, format='json')
            self.assertEqual(result.status_code, 200)
        self.assertEqual(WatchProgress.objects.filter(user=self.user, content=self.series).count(), 1)
        self.assertEqual(PartProgress.objects.filter(user=self.user, content=self.series).count(), 2)
        self.assertTrue(PartProgress.objects.get(user=self.user, episode=self.episode).completed)
        self.assertFalse(PartProgress.objects.get(user=self.user, episode=second).completed)
        base = reverse('library-progress-delete', args=[self.series.pk])
        self.assertEqual(self.client.get(base).data['episode'], second.pk)
        self.assertEqual(self.client.get(base, {'episode': self.episode.pk}).data['position_seconds'], 100)
        self.login(self.other)
        self.assertEqual(self.client.get(base, {'episode': self.episode.pk}).status_code, 404)

    def test_legacy_snapshot_stays_resumable_without_fabricated_history(self):
        legacy = WatchProgress.objects.create(user=self.user, content=self.series, episode=self.episode,
            position_seconds=57, duration_seconds=100)
        self.login()
        response = self.client.get(reverse('library-progress-delete', args=[self.series.pk]), {'episode': self.episode.pk})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data['position_seconds'], 57)
        legacy.refresh_from_db()
        self.assertEqual(legacy.position_seconds, 57)
        self.assertEqual(PartProgress.objects.count(), 0)

    def test_older_backend_snapshot_update_after_rollback_wins_for_same_episode(self):
        self.login()
        endpoint = reverse('library-progress')
        self.client.post(endpoint, {'content': self.series.pk, 'episode': self.episode.pk,
            'position_seconds': 15, 'duration_seconds': 100}, format='json')
        snapshot = WatchProgress.objects.get(user=self.user, content=self.series)
        snapshot.position_seconds = 47
        snapshot.save(update_fields=['position_seconds', 'updated_at'])
        result = self.client.get(reverse('library-progress-delete', args=[self.series.pk]), {'episode': self.episode.pk})
        self.assertEqual(result.data['position_seconds'], 47)
        self.assertEqual(PartProgress.objects.get(user=self.user, episode=self.episode).position_seconds, 15)

    def test_invalid_selectors_return_validation_errors(self):
        self.login()
        base = reverse('library-progress-delete', args=[self.series.pk])
        for selector in ('²', '0', '999999999999999999999999999999999999', 'abc'):
            self.assertEqual(self.client.get(base, {'episode': selector}).status_code, 400)
        self.assertEqual(self.client.post(reverse('library-progress'), {'content': self.series.pk,
            'position_seconds': 12}, format='json').status_code, 400)

    def test_legacy_fallback_requires_matching_selector_and_viewer(self):
        legacy = WatchProgress.objects.create(user=self.user, content=self.series, episode=self.episode,
            position_seconds=57, duration_seconds=100)
        second = Episode.objects.create(season=self.season, title='Other episode', episode_number=2)
        self.login()
        base = reverse('library-progress-delete', args=[self.series.pk])
        self.assertEqual(self.client.get(base, {'episode': second.pk}).status_code, 404)
        self.login(self.other)
        self.assertEqual(self.client.get(base, {'episode': self.episode.pk}).status_code, 404)
        legacy.refresh_from_db()
        self.assertEqual(legacy.position_seconds, 57)
        self.assertEqual(PartProgress.objects.count(), 0)

    def test_miniseries_parts_retain_history_and_delete_clears_only_current_account(self):
        second = MiniSeries.objects.create(content=self.mini, title='Part two', miniseries_no=2)
        self.login()
        for part, position in ((self.part, 100), (second, 45)):
            response = self.client.post(reverse('library-progress'), {'content': self.mini.pk,
                'miniseries': part.pk, 'position_seconds': position, 'duration_seconds': 100}, format='json')
            self.assertEqual(response.status_code, 200)
        self.assertEqual(PartProgress.objects.filter(user=self.user).count(), 2)
        self.assertTrue(PartProgress.objects.get(user=self.user, miniseries=self.part).completed)
        self.assertFalse(WatchProgress.objects.get(user=self.user, content=self.mini).completed)
        PartProgress.objects.create(user=self.other, content=self.mini, miniseries=self.part,
            position_seconds=20, duration_seconds=100)
        base = reverse('library-progress-delete', args=[self.mini.pk])
        self.assertEqual(self.client.get(base, {'miniseries': self.part.pk}).data['position_seconds'], 100)
        self.assertEqual(self.client.get(base).data['miniseries'], second.pk)
        self.assertEqual(self.client.delete(base).status_code, 204)
        self.assertFalse(PartProgress.objects.filter(user=self.user).exists())
        self.assertTrue(PartProgress.objects.filter(user=self.other).exists())

    def test_legacy_completed_episode_snapshot_does_not_invent_series_history(self):
        legacy = WatchProgress.objects.create(user=self.user, content=self.series, episode=self.episode,
            position_seconds=100, duration_seconds=100, completed=True)
        self.login()
        base = reverse('library-progress-delete', args=[self.series.pk])
        response = self.client.get(base, {'episode': self.episode.pk})
        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.data['completed'])
        legacy.refresh_from_db()
        self.assertEqual(legacy.episode_id, self.episode.pk)
        self.assertEqual(PartProgress.objects.count(), 0)

    def test_progress_selectors_must_be_ascii_and_belong_to_title(self):
        self.login()
        base = reverse('library-progress-delete', args=[self.series.pk])
        for value in ('\u00b2', '\u0661', '9' * 100):
            self.assertEqual(self.client.get(base, {'episode': value}).status_code, 400)
        response = self.client.post(reverse('library-progress'), {'content': self.movie.pk,
            'episode': self.episode.pk, 'position_seconds': 20, 'duration_seconds': 100}, format='json')
        self.assertEqual(response.status_code, 400)
        self.assertFalse(PartProgress.objects.exists())

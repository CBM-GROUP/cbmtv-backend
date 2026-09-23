"""
Saved items and watch progress: the per-user library behind the mobile app's
Saved tab, hero heart button and "Continue watching" rail.
"""
from django.urls import reverse
from rest_framework import status
from rest_framework.test import APITestCase, APIClient

from accounts.models import User
from channel.models import Channel
from content.models import Content, Episode, MiniSeries, Season
from .models import SavedItem, WatchProgress


class LibraryTestCase(APITestCase):
    def setUp(self):
        self.client = APIClient()
        self.user = User.objects.create_user(
            email='viewer@example.com', name='Viewer', password='pass1234'
        )
        self.other = User.objects.create_user(
            email='other@example.com', name='Other', password='pass1234'
        )
        # Not a seeded channel name -- see channel/signals.py.
        self.channel = Channel.objects.create(name="Library Fixture")
        self.movie = Content.objects.create(
            title="Dear Dija", content_type="movie", channel=self.channel,
        )
        self.movie_b = Content.objects.create(
            title="Watu Wote", content_type="movie", channel=self.channel,
        )
        self.series = Content.objects.create(
            title="A Series", content_type="series", channel=self.channel,
        )
        self.season = Season.objects.create(content=self.series, title="S1", season_number=1)
        self.episode = Episode.objects.create(season=self.season, title="E1", episode_number=1)
        self.mini = Content.objects.create(
            title="A Miniseries", content_type="miniseries", channel=self.channel,
        )
        self.part = MiniSeries.objects.create(content=self.mini, title="Part 1", miniseries_no=1)

    def login(self, user=None):
        self.client.force_authenticate(user or self.user)


class AuthRequiredTests(LibraryTestCase):
    def test_every_endpoint_requires_authentication(self):
        for method, url in (
            ('get', reverse('library-saved')),
            ('post', reverse('library-saved')),
            ('get', reverse('library-saved-ids')),
            ('delete', reverse('library-saved-delete', args=[self.movie.id])),
            ('get', reverse('library-progress')),
            ('post', reverse('library-progress')),
            ('delete', reverse('library-progress-delete', args=[self.movie.id])),
        ):
            response = getattr(self.client, method)(url, {}, format='json')
            self.assertEqual(response.status_code, status.HTTP_401_UNAUTHORIZED, f"{method} {url}")


class SavedItemTests(LibraryTestCase):
    def test_save_returns_201_then_200_when_already_saved(self):
        self.login()
        url = reverse('library-saved')

        first = self.client.post(url, {'content': self.movie.id}, format='json')
        self.assertEqual(first.status_code, status.HTTP_201_CREATED)
        self.assertEqual(first.data['content']['id'], self.movie.id)
        self.assertEqual(first.data['content']['title'], "Dear Dija")

        again = self.client.post(url, {'content': self.movie.id}, format='json')
        self.assertEqual(again.status_code, status.HTTP_200_OK)
        self.assertEqual(again.data['id'], first.data['id'])
        self.assertEqual(SavedItem.objects.filter(user=self.user).count(), 1)

    def test_unknown_content_is_a_field_error(self):
        self.login()
        response = self.client.post(reverse('library-saved'), {'content': 999999}, format='json')
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn('content', response.data)

    def test_list_is_paginated_newest_first_and_scoped_to_user(self):
        SavedItem.objects.create(user=self.user, content=self.movie)
        SavedItem.objects.create(user=self.user, content=self.movie_b)
        SavedItem.objects.create(user=self.other, content=self.series)
        self.login()

        response = self.client.get(reverse('library-saved'))

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data['count'], 2)
        self.assertEqual(
            [item['content']['id'] for item in response.data['results']],
            [self.movie_b.id, self.movie.id],
        )
        self.assertIn('is_featured', response.data['results'][0]['content'])

    def test_ids_is_a_bare_array(self):
        SavedItem.objects.create(user=self.user, content=self.movie)
        SavedItem.objects.create(user=self.other, content=self.series)
        self.login()

        response = self.client.get(reverse('library-saved-ids'))

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data, [self.movie.id])

    def test_delete_is_idempotent_and_only_touches_own_row(self):
        SavedItem.objects.create(user=self.user, content=self.movie)
        SavedItem.objects.create(user=self.other, content=self.movie)
        self.login()
        url = reverse('library-saved-delete', args=[self.movie.id])

        self.assertEqual(self.client.delete(url).status_code, status.HTTP_204_NO_CONTENT)
        self.assertEqual(self.client.delete(url).status_code, status.HTTP_204_NO_CONTENT)
        self.assertFalse(SavedItem.objects.filter(user=self.user).exists())
        self.assertTrue(SavedItem.objects.filter(user=self.other).exists())


class WatchProgressTests(LibraryTestCase):
    def test_record_upserts_one_row_per_content(self):
        self.login()
        url = reverse('library-progress')

        first = self.client.post(
            url, {'content': self.movie.id, 'position_seconds': 60, 'duration_seconds': 3600}, format='json'
        )
        self.assertEqual(first.status_code, status.HTTP_200_OK)
        self.assertFalse(first.data['completed'])

        second = self.client.post(
            url, {'content': self.movie.id, 'position_seconds': 120, 'duration_seconds': 3600}, format='json'
        )
        self.assertEqual(second.status_code, status.HTTP_200_OK)
        self.assertEqual(second.data['id'], first.data['id'])
        self.assertEqual(second.data['position_seconds'], 120)
        self.assertEqual(second.data['content']['id'], self.movie.id)
        self.assertEqual(WatchProgress.objects.filter(user=self.user).count(), 1)

    def test_reaching_95_percent_marks_completed(self):
        self.login()
        response = self.client.post(
            reverse('library-progress'),
            {'content': self.movie.id, 'position_seconds': 3420, 'duration_seconds': 3600},
            format='json',
        )
        self.assertTrue(response.data['completed'])

    def test_unknown_duration_never_completes(self):
        self.login()
        response = self.client.post(
            reverse('library-progress'), {'content': self.movie.id, 'position_seconds': 9999}, format='json'
        )
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data['duration_seconds'], 0)
        self.assertFalse(response.data['completed'])

    def test_episode_and_miniseries_must_belong_to_content(self):
        self.login()
        url = reverse('library-progress')

        ok = self.client.post(
            url, {'content': self.series.id, 'episode': self.episode.id, 'position_seconds': 5}, format='json'
        )
        self.assertEqual(ok.status_code, status.HTTP_200_OK)
        self.assertEqual(ok.data['episode'], self.episode.id)

        wrong_episode = self.client.post(
            url, {'content': self.movie.id, 'episode': self.episode.id, 'position_seconds': 5}, format='json'
        )
        self.assertEqual(wrong_episode.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn('episode', wrong_episode.data)

        ok_part = self.client.post(
            url, {'content': self.mini.id, 'miniseries': self.part.id, 'position_seconds': 5}, format='json'
        )
        self.assertEqual(ok_part.status_code, status.HTTP_200_OK)
        self.assertEqual(ok_part.data['miniseries'], self.part.id)

        wrong_part = self.client.post(
            url, {'content': self.series.id, 'miniseries': self.part.id, 'position_seconds': 5}, format='json'
        )
        self.assertEqual(wrong_part.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn('miniseries', wrong_part.data)

    def test_list_hides_completed_unless_asked(self):
        WatchProgress.objects.create(
            user=self.user, content=self.movie, position_seconds=10, duration_seconds=100
        )
        WatchProgress.objects.create(
            user=self.user, content=self.movie_b, position_seconds=100, duration_seconds=100, completed=True
        )
        WatchProgress.objects.create(
            user=self.other, content=self.series, position_seconds=10, duration_seconds=100
        )
        self.login()

        unfinished = self.client.get(reverse('library-progress'))
        self.assertEqual(unfinished.status_code, status.HTTP_200_OK)
        self.assertEqual(unfinished.data['count'], 1)
        self.assertEqual(unfinished.data['results'][0]['content']['id'], self.movie.id)

        everything = self.client.get(reverse('library-progress'), {'include_completed': 'true'})
        self.assertEqual(everything.data['count'], 2)

    def test_delete_is_idempotent(self):
        WatchProgress.objects.create(user=self.user, content=self.movie, position_seconds=10)
        self.login()
        url = reverse('library-progress-delete', args=[self.movie.id])

        self.assertEqual(self.client.delete(url).status_code, status.HTTP_204_NO_CONTENT)
        self.assertEqual(self.client.delete(url).status_code, status.HTTP_204_NO_CONTENT)
        self.assertFalse(WatchProgress.objects.exists())

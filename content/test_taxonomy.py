from unittest.mock import patch

from django.test import override_settings
from rest_framework.test import APITestCase

from accounts.models import DashboardRole, User
from channel.models import Channel
from .models import Content, Genre, Season, Episode, MiniSeries
from .search_sync import content_document


@override_settings(PASSWORD_HASHERS=['django.contrib.auth.hashers.MD5PasswordHasher'])
class TaxonomyAPITests(APITestCase):
    def setUp(self):
        role = DashboardRole.objects.create(name='Taxonomy editor', permissions=['content'])
        self.editor = User.objects.create_user(email='taxonomy@example.com', name='Taxonomy', password='local-password')
        self.editor.dashboard_role = role
        self.editor.save()
        self.viewer = User.objects.create_user(email='viewer-taxonomy@example.com', name='Viewer', password='local-password')
        self.channel = Channel.objects.create(name='Nature')
        self.other_channel = Channel.objects.create(name='Culture')
        self.client.force_authenticate(self.editor)

    def genre(self, name='Comedy', categories=None, **extra):
        response = self.client.post('/api/content/genres/', {
            'name': name, 'description': '', 'categories': categories or ['movies'], **extra,
        }, format='json')
        self.assertEqual(response.status_code, 201, response.data)
        return response.data

    def title(self, category, content_format='standalone', genres=None, channel=None):
        response = self.client.post('/api/content/', {
            'title': f'{category} {content_format}', 'category': category, 'format': content_format,
            'genres': genres or [], 'channel': channel or self.channel.pk, 'status': 'approved',
        }, format='json')
        self.assertEqual(response.status_code, 201, response.data)
        return response.data

    def test_four_fixed_categories_and_public_genre_visibility(self):
        categories = self.client.get('/api/content/categories/').data
        self.assertEqual([row['id'] for row in categories], ['movies', 'tv_shows', 'documentaries', 'animations'])
        self.assertEqual(self.client.post('/api/content/categories/', {}, format='json').status_code, 405)
        active = self.genre(categories=['movies', 'tv_shows', 'animations'])
        inactive = self.genre('Dormant', ['documentaries'], is_active=False)
        self.client.force_authenticate(None)
        self.assertEqual([row['id'] for row in self.client.get('/api/content/genres/').data['results']], [active['id']])
        self.assertEqual(self.client.get(f"/api/content/genres/{inactive['id']}/").status_code, 404)
        self.assertEqual(self.client.get('/api/content/genres/?scope=dashboard').status_code, 403)
        self.client.force_authenticate(self.editor)
        ids = [row['id'] for row in self.client.get('/api/content/genres/?scope=dashboard').data['results']]
        self.assertIn(inactive['id'], ids)

    def test_genre_edit_casefold_uniqueness_subgenres_and_safe_deactivation(self):
        parent = self.genre('Nature', ['documentaries', 'animations'])
        child = self.genre('Wildlife', ['documentaries'], parent=parent['id'])
        self.assertEqual(self.client.post('/api/content/genres/', {'name': ' nature ', 'categories': ['movies']}, format='json').status_code, 400)
        self.assertEqual(self.client.patch(f"/api/content/genres/{parent['id']}/", {'parent': child['id']}, format='json').status_code, 400)
        self.assertEqual(self.client.patch(f"/api/content/genres/{parent['id']}/", {'categories': ['animations']}, format='json').status_code, 400)
        self.assertEqual(self.client.patch(f"/api/content/genres/{child['id']}/", {'parent': child['id']}, format='json').status_code, 400)
        changed = self.client.patch(f"/api/content/genres/{child['id']}/", {'name': 'Wild Animals', 'description': 'Fauna'}, format='json')
        self.assertEqual(changed.status_code, 200, changed.data)
        self.assertEqual(changed.data['description'], 'Fauna')
        self.assertEqual(self.client.delete(f"/api/content/genres/{child['id']}/").status_code, 405)
        self.assertEqual(self.client.patch(f"/api/content/genres/{child['id']}/", {'is_active': False}, format='json').status_code, 200)
        self.assertFalse(Genre.objects.get(pk=child['id']).is_active)

    def test_parent_genre_rename_refreshes_titles_assigned_to_children(self):
        parent = self.genre('Nature', ['documentaries'])
        child = self.genre('Wildlife', ['documentaries'], parent=parent['id'])
        title = Content.objects.create(title='Savanna', content_type='documentary',
                                       category='documentaries', format='standalone',
                                       channel=self.channel, status='approved')
        title.genres.add(child['id'])
        with patch('content.views.upsert_content') as upsert:
            with self.captureOnCommitCallbacks(execute=True):
                response = self.client.patch(f"/api/content/genres/{parent['id']}/",
                                             {'name': 'Natural World'}, format='json')
        self.assertEqual(response.status_code, 200)
        upsert.assert_called_once_with(title.pk)

    def test_content_assignment_filters_publication_and_channel(self):
        comedy = self.genre('Comedy', ['movies', 'tv_shows', 'animations'])
        action = self.genre('Action', ['movies'])
        movie = self.title('movies', genres=[comedy['id'], action['id']])
        self.assertEqual(movie['status'], 'preview')
        self.assertEqual(movie['content_type'], 'movie')
        self.assertEqual(movie['genres'], [comedy['id'], action['id']])
        self.assertEqual(movie['channel'], self.channel.pk)
        self.assertEqual(self.client.post('/api/content/', {'title': 'Two owners', 'category': 'movies',
            'format': 'standalone', 'channel': [self.channel.pk, self.other_channel.pk]}, format='json').status_code, 400)
        self.assertEqual(self.client.get(f"/api/content/genres/{comedy['id']}/?scope=dashboard").data['used_count'], 1)
        self.client.force_authenticate(None)
        self.assertEqual(self.client.get(f"/api/content/genres/{comedy['id']}/").data['used_count'], 0)
        self.assertEqual(self.client.get('/api/content/?category=movies').data['count'], 0)
        self.assertEqual(self.client.get('/api/content/search/?q=Comedy').data['hits'], [])
        self.client.force_authenticate(self.editor)
        with patch('content.views.upsert_content') as upsert:
            with self.captureOnCommitCallbacks(execute=True):
                approved = self.client.patch(f"/api/content/{movie['id']}/", {'status': 'approved'}, format='json')
            upsert.assert_called_once_with(movie['id'])
        self.assertEqual(approved.status_code, 200, approved.data)
        self.client.force_authenticate(None)
        for params in ({'category': 'movies'}, {'genre_id': comedy['id']}, {'genre': 'Comedy'},
                       {'content_format': 'standalone'}, {'channel': self.channel.pk}):
            self.assertEqual(self.client.get('/api/content/', params).data['count'], 1)
        self.assertEqual(self.client.get('/api/content/search/', {'q': 'Comedy', 'search_by': 'genre'}).data['hits'][0]['id'], movie['id'])
        self.assertEqual(self.client.get('/api/content/', {'channel': self.other_channel.pk}).data['count'], 0)
        self.assertEqual(self.client.get('/api/content/', {'category': 'documentaries'}).data['count'], 0)
        self.client.force_authenticate(self.editor)
        self.assertEqual(self.client.patch(f"/api/content/{movie['id']}/", {'category': 'documentaries'}, format='json').status_code, 400)
        self.assertEqual(self.client.patch(f"/api/content/genres/{comedy['id']}/", {'categories': ['tv_shows']}, format='json').status_code, 400)
        self.assertEqual(self.client.patch(f"/api/content/{movie['id']}/", {'category': None}, format='json').status_code, 400)
        changed = self.client.patch(f"/api/content/{movie['id']}/", {'category': 'documentaries', 'genres': []}, format='json')
        self.assertEqual(changed.status_code, 200, changed.data)
        self.assertEqual(changed.data['content_type'], 'documentary')
        self.assertEqual(self.client.post('/api/content/', {'title': 'Conflicting', 'category': 'animations', 'format': 'standalone',
            'content_type': 'movie', 'channel': self.channel.pk}, format='json').status_code, 400)

    def test_series_in_three_categories_and_standalone_has_no_seasons(self):
        ids = {}
        for category in ('tv_shows', 'documentaries', 'animations'):
            title = self.title(category, 'series')
            ids[category] = title['id']
            self.assertEqual(title['content_type'], 'series')
            season = self.client.post('/api/content/seasons/', {'content': title['id'], 'title': 'S1', 'season_number': 1}, format='json')
            self.assertEqual(season.status_code, 201, season.data)
            episode = self.client.post('/api/content/episodes/', {'season': season.data['id'], 'title': 'Pilot', 'episode_number': 1}, format='json')
            self.assertEqual(episode.status_code, 201, episode.data)
            self.assertEqual(self.client.post('/api/content/episodes/', {'season': season.data['id'], 'title': 'Duplicate', 'episode_number': 1}, format='json').status_code, 400)
            self.assertEqual(self.client.post('/api/content/seasons/', {'content': title['id'], 'title': 'Duplicate', 'season_number': 1}, format='json').status_code, 400)
            self.assertEqual(self.client.patch(f"/api/content/{title['id']}/", {'format': 'standalone'}, format='json').status_code, 400)
            self.assertEqual(Season.objects.get(pk=season.data['id']).content_id, title['id'])
            self.assertEqual(Episode.objects.get(pk=episode.data['id']).season_id, season.data['id'])
        standalone = self.title('documentaries')
        self.assertEqual(self.client.post('/api/content/seasons/', {'content': standalone['id'], 'title': 'Wrong', 'season_number': 1}, format='json').status_code, 400)
        self.assertEqual(self.client.post('/api/content/', {'title': 'Invalid', 'category': 'movies', 'format': 'series', 'channel': self.channel.pk}, format='json').status_code, 400)
        for pk in (*ids.values(), standalone['id']):
            self.assertEqual(self.client.patch(f'/api/content/{pk}/', {'status': 'approved'}, format='json').status_code, 200)
        self.client.force_authenticate(None)
        documentaries = self.client.get('/api/content/', {'category': 'documentaries'}).data['results']
        self.assertEqual({row['format'] for row in documentaries}, {'standalone', 'series'})
        self.assertEqual(self.client.get('/api/content/', {'category': 'animations'}).data['count'], 1)

    def test_legacy_miniseries_and_ambiguous_approval(self):
        mini = Content.objects.create(title='Legacy mini', channel=self.channel, content_type='miniseries', status='preview')
        child = MiniSeries.objects.create(content=mini, title='Part 1', miniseries_no=1)
        self.assertEqual(self.client.get(f'/api/content/miniseries/{child.pk}/?scope=dashboard').status_code, 200)
        self.assertEqual(self.client.patch(f'/api/content/{mini.pk}/', {'status': 'approved'}, format='json').status_code, 400)
        self.assertTrue(MiniSeries.objects.filter(pk=child.pk, content_id=mini.pk).exists())
        old_movie = self.client.post('/api/content/', {'title': 'Old client', 'content_type': 'movie', 'channel': self.channel.pk}, format='json')
        self.assertEqual(old_movie.status_code, 201, old_movie.data)
        self.assertEqual(old_movie.data['category'], 'movies')
        self.assertEqual(old_movie.data['format'], 'standalone')
        legacy = Content.objects.create(title='Older original', channel=self.channel, content_type='original', status='approved')
        self.client.force_authenticate(None)
        self.assertEqual(self.client.get(f'/api/content/{legacy.pk}/').status_code, 200)
        self.assertEqual(self.client.get('/api/content/', {'category': 'movies'}).data['count'], 0)

    def test_permissions_and_search_document(self):
        nature = self.genre('Nature', ['documentaries'])
        title = self.title('documentaries', 'series', [nature['id']])
        row = Content.objects.get(pk=title['id'])
        document = content_document(row)
        self.assertEqual(document['category'], 'documentaries')
        self.assertEqual(document['format'], 'series')
        self.assertEqual(document['genres'], ['Nature'])
        self.assertEqual(document['channel'], 'Nature')
        self.client.force_authenticate(self.viewer)
        self.assertIn(self.client.post('/api/content/genres/', {'name': 'Forbidden', 'categories': ['movies']}, format='json').status_code, (401, 403))
        self.assertIn(self.client.patch(f"/api/content/{title['id']}/", {'category': 'movies'}, format='json').status_code, (401, 403))
        self.assertEqual(self.client.get('/api/content/genres/?scope=dashboard').status_code, 403)

    @patch('content.views.upsert_content')
    def test_genre_rename_schedules_search_refresh_for_assigned_title(self, upsert):
        genre = self.genre('Drama', ['tv_shows'])
        title = self.title('tv_shows', 'series', [genre['id']])
        with self.captureOnCommitCallbacks(execute=True):
            response = self.client.patch(f"/api/content/genres/{genre['id']}/", {'name': 'New Drama'}, format='json')
        self.assertEqual(response.status_code, 200, response.data)
        upsert.assert_called_once_with(title['id'])
        self.assertEqual(content_document(Content.objects.get(pk=title['id']))['genres'], ['New Drama'])

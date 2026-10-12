from django.test import TestCase, override_settings
from rest_framework.test import APIClient

from accounts.models import DashboardRole, User
from channel.models import Channel
from content.models import Content, contentadverts
from library.models import SavedItem, WatchProgress


URL = '/api/dashboard/analytics/'


@override_settings(PASSWORD_HASHERS=['django.contrib.auth.hashers.MD5PasswordHasher'])
class DashboardAnalyticsTests(TestCase):
    def setUp(self):
        self.client = APIClient()
        self.admin = User.objects.create_superuser(email='admin@example.com', name='Admin', password='StrongPass123!')
        self.analytics_role = DashboardRole.objects.create(name='Analyst', permissions=['analytics'])
        self.analyst = User.objects.create_user(email='analyst@example.com', name='Analyst', password='StrongPass123!')
        self.analyst.dashboard_role = self.analytics_role
        self.analyst.save(update_fields=['dashboard_role'])
        self.viewer = User.objects.create_user(email='viewer@example.com', name='Viewer', password='StrongPass123!')

    def get_as(self, user):
        self.client.force_authenticate(user=user)
        return self.client.get(URL)

    def test_empty_metrics_and_explicit_authorization(self):
        self.assertEqual(self.client.get(URL).status_code, 401)
        self.assertEqual(self.get_as(self.viewer).status_code, 403)
        self.assertEqual(self.get_as(self.analyst).status_code, 200)
        data = self.get_as(self.admin).data
        self.assertEqual(data['content']['total_records'], 0)
        self.assertEqual(data['saved']['total_records'], 0)
        self.assertEqual(data['progress']['unfinished_records'], 0)
        self.assertEqual(data['users'], {'total': 3, 'active': 3, 'inactive': 0, 'pending_activation': 0})

    def test_inactive_unactivated_and_other_roles_are_denied(self):
        legacy_staff = User.objects.create_user(email='staff@example.com', name='Staff', password='StrongPass123!')
        legacy_staff.is_staff = True
        legacy_staff.save(update_fields=['is_staff'])
        self.assertEqual(self.get_as(legacy_staff).status_code, 403)
        content_role = DashboardRole.objects.create(name='Editor', permissions=['content'])
        self.analyst.dashboard_role = content_role
        self.analyst.save(update_fields=['dashboard_role'])
        self.assertEqual(self.get_as(self.analyst).status_code, 403)
        self.analyst.dashboard_role = self.analytics_role
        self.analyst.must_change_password = True
        self.analyst.save(update_fields=['dashboard_role', 'must_change_password'])
        self.assertEqual(self.get_as(self.analyst).status_code, 403)
        self.analyst.must_change_password = False
        self.analyst.is_active = False
        self.analyst.save(update_fields=['must_change_password', 'is_active'])
        self.assertEqual(self.get_as(self.analyst).status_code, 403)

    def test_aggregates_all_pages_and_counts_distinct_users(self):
        initial_channels = Channel.objects.count()
        initial_adverts = contentadverts.objects.count()
        channel = Channel.objects.create(name='Films')
        content = [Content.objects.create(
            title=f'Title {index}', channel=channel,
            content_type='movie' if index % 2 else 'series',
            status='approved' if index < 11 else 'preview',
        ) for index in range(13)]
        contentadverts.objects.create(advert_type='hello')
        second = User.objects.create_user(email='second@example.com', name='Second', password='StrongPass123!')
        inactive = User.objects.create_user(email='inactive@example.com', name='Inactive', password='StrongPass123!')
        inactive.is_active = False
        inactive.save(update_fields=['is_active'])
        pending = User.objects.create_user(email='pending@example.com', name='Pending', password='StrongPass123!')
        pending.must_change_password = True
        pending.save(update_fields=['must_change_password'])
        for title in content[:12]:
            SavedItem.objects.create(user=self.viewer, content=title)
        SavedItem.objects.create(user=second, content=content[0])
        WatchProgress.objects.create(user=self.viewer, content=content[0], position_seconds=20)
        WatchProgress.objects.create(user=self.viewer, content=content[1], position_seconds=50, completed=True)
        WatchProgress.objects.create(user=second, content=content[0], position_seconds=30)
        data = self.get_as(self.admin).data
        self.assertEqual(data['content']['total_records'], 13)
        self.assertEqual(data['content']['channel_count'], initial_channels + 1)
        self.assertEqual(data['content']['advert_count'], initial_adverts + 1)
        self.assertEqual({row['status']: row['count'] for row in data['content']['by_status']}, {'approved': 11, 'preview': 2})
        self.assertEqual({row['type']: row['count'] for row in data['content']['by_type']}, {'movie': 6, 'series': 7})
        self.assertEqual(data['users'], {'total': 6, 'active': 4, 'inactive': 1, 'pending_activation': 1})
        self.assertEqual(data['saved']['total_records'], 13)
        self.assertEqual(data['saved']['distinct_users'], 2)
        self.assertEqual(data['saved']['most_saved_titles'][0]['saved_records'], 2)
        self.assertEqual(len(data['saved']['most_saved_titles']), 10)
        self.assertEqual(data['progress']['users_with_records'], 2)
        self.assertEqual(data['progress']['unfinished_records'], 2)
        self.assertEqual(data['progress']['unfinished_titles'][0]['users_with_unfinished_progress'], 2)

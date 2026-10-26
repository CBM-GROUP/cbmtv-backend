from concurrent.futures import ThreadPoolExecutor
from threading import Barrier, Event
from unittest import skipUnless
from unittest.mock import patch
from uuid import uuid4

from django.db import close_old_connections, connection, transaction
from django.test import TestCase, TransactionTestCase
from rest_framework.test import APIClient

from accounts.models import DashboardRole, User
from channel.models import Channel
from channel.serializers import ChannelSerializer
from content.models import Content, Episode, MiniSeries, Season, contentadverts
from content.serializers import ContentSerializer, EpisodeSerializer, MiniSeriesSerializer, ContentAdvertSerializer
from .models import CreationOperation


class CreationFixtures:
    def setUp(self):
        role = DashboardRole.objects.create(name='Create editor', permissions=['content', 'adverts', 'channels'])
        self.user = User.objects.create_user(email='create@example.com', name='Editor', password='test')
        self.user.dashboard_role = role
        self.user.save()
        self.client = APIClient()
        self.client.force_authenticate(self.user)
        self.channel = Channel.objects.create(name='Fixture')
        self.series = Content.objects.create(title='Series', content_type='series', channel=self.channel, status='preview')
        self.mini = Content.objects.create(title='Mini', content_type='miniseries', channel=self.channel, status='preview')
        self.season = Season.objects.create(content=self.series, title='S1', season_number=1)
        self.cases = [
            ('/api/content/', Content, {'title': 'Created', 'channel': self.channel.pk, 'content_type': 'movie'}),
            ('/api/content/episodes/', Episode, {'title': 'Episode', 'season': self.season.pk, 'episode_number': 1}),
            ('/api/content/miniseries/', MiniSeries, {'title': 'Part', 'content': self.mini.pk, 'miniseries_no': 1}),
            ('/api/content/adverts/', contentadverts, {'advert_type': 'middle', 'advert_name': 'Advert'}),
            ('/api/channels/', Channel, {'name': 'Created channel'}),
        ]

    def post(self, endpoint, payload, key):
        return self.client.post(endpoint, payload, format='json', HTTP_IDEMPOTENCY_KEY=str(key))


class IdempotencyTests(CreationFixtures, TestCase):
    def test_lost_success_response_retries_same_record_independently_for_all_five_paths(self):
        for endpoint, model, payload in self.cases:
            with self.subTest(endpoint=endpoint):
                count, key = model.objects.count(), uuid4()
                original = self.post(endpoint, payload, key)
                self.assertEqual(original.status_code, 201, original.data)
                # Simulate a new browser/client after the successful response was lost.
                self.client = APIClient()
                self.client.force_authenticate(self.user)
                replay = self.post(endpoint, dict(reversed(list(payload.items()))), key)
                self.assertEqual(replay.status_code, 201, replay.data)
                self.assertEqual(replay.data['id'], original.data['id'])
                self.assertEqual(replay['Idempotency-Replayed'], 'true')
                self.assertEqual(model.objects.count(), count + 1)

    def test_serialization_failure_after_persist_retries_without_second_record(self):
        serializers = [ContentSerializer, EpisodeSerializer, MiniSeriesSerializer, ContentAdvertSerializer, ChannelSerializer]
        for (endpoint, model, payload), serializer in zip(self.cases, serializers):
            with self.subTest(endpoint=endpoint):
                count, key = model.objects.count(), uuid4()
                with patch.object(serializer, 'to_representation', side_effect=RuntimeError('response failed')):
                    with self.assertRaises(RuntimeError):
                        self.post(endpoint, payload, key)
                self.assertEqual(model.objects.count(), count + 1)
                self.assertEqual(CreationOperation.objects.get(key=key).record_id, model.objects.latest('pk').pk)
                replay = self.post(endpoint, payload, key)
                self.assertEqual(replay.status_code, 201)
                self.assertEqual(model.objects.count(), count + 1)

    def test_actor_scope_and_old_operation_do_not_expire_into_new_creation(self):
        from datetime import timedelta
        from django.utils import timezone
        endpoint, model, payload = self.cases[0]
        key = uuid4()
        first = self.post(endpoint, payload, key)
        CreationOperation.objects.filter(key=key).update(created_at=timezone.now() - timedelta(days=400))
        self.assertEqual(self.post(endpoint, payload, key).data['id'], first.data['id'])
        other = User.objects.create_user(email='other-editor@example.com', name='Other', password='test')
        other.dashboard_role = self.user.dashboard_role
        other.save()
        self.client.force_authenticate(other)
        second = self.post(endpoint, payload, key)
        self.assertEqual(second.status_code, 201)
        self.assertNotEqual(second.data['id'], first.data['id'])

    def test_payload_endpoint_mismatch_and_deleted_record_never_create(self):
        endpoint, model, payload = self.cases[0]
        key = uuid4()
        first = self.post(endpoint, payload, key)
        self.assertEqual(self.post(endpoint, {**payload, 'title': 'Different'}, key).status_code, 409)
        self.assertEqual(self.post('/api/channels/', {'name': 'Other'}, key).status_code, 409)
        model.objects.get(pk=first.data['id']).delete()
        before = model.objects.count()
        self.assertEqual(self.post(endpoint, payload, key).status_code, 410)
        self.assertEqual(model.objects.count(), before)

    def test_validation_rollback_fresh_operation_permission_and_legacy_compatibility(self):
        endpoint, model, payload = self.cases[0]
        key = uuid4()
        self.assertEqual(self.post(endpoint, {}, key).status_code, 400)
        self.assertFalse(CreationOperation.objects.filter(key=key).exists())
        first = self.post(endpoint, payload, key)
        second = self.post(endpoint, payload, uuid4())
        self.assertNotEqual(first.data['id'], second.data['id'])
        self.user.dashboard_role = None
        self.user.save()
        self.assertEqual(self.post(endpoint, payload, key).status_code, 403)
        self.user.dashboard_role = DashboardRole.objects.first()
        self.user.save()
        self.assertEqual(self.client.post(endpoint, payload, format='json').status_code, 201)
        self.assertEqual(self.client.options(endpoint)['Idempotency-Supported'], 'v1')
        self.assertEqual(self.post(endpoint, payload, 'not-a-uuid').status_code, 400)


@skipUnless(connection.vendor == 'postgresql', 'Requires disposable PostgreSQL and independent sessions')
class PostgreSQLCorrectiveTests(CreationFixtures, TransactionTestCase):
    def test_concurrent_retries_all_five_workflows(self):
        for endpoint, model, payload in self.cases:
            barrier, key, count = Barrier(2), uuid4(), model.objects.count()
            def request():
                close_old_connections()
                try:
                    client = APIClient()
                    client.force_authenticate(User.objects.get(pk=self.user.pk))
                    barrier.wait(timeout=10)
                    response = client.post(endpoint, payload, format='json', HTTP_IDEMPOTENCY_KEY=str(key))
                    return response.status_code, response.data.get('id')
                finally:
                    close_old_connections()
            with ThreadPoolExecutor(max_workers=2) as pool:
                futures = [pool.submit(request) for _ in range(2)]
                results = [future.result(timeout=20) for future in futures]
            self.assertEqual([r[0] for r in results], [201, 201])
            self.assertEqual(results[0][1], results[1][1])
            self.assertEqual(model.objects.count(), count + 1)

    def test_stale_metadata_waits_for_publication_commit(self):
        row = Content.objects.create(title='Race', channel=self.channel, content_type='movie', status='approved')
        stale = Content.objects.get(pk=row.pk)
        entered, finished = Event(), Event()
        def metadata():
            close_old_connections()
            try:
                serializer = ContentSerializer(stale, data={'title': 'Edited'}, partial=True)
                serializer.is_valid(raise_exception=True)
                entered.set()
                serializer.save()
                finished.set()
            finally:
                close_old_connections()
        with ThreadPoolExecutor(max_workers=1) as pool:
            with transaction.atomic():
                locked = Content.objects.select_for_update().get(pk=row.pk)
                locked.status = 'preview'
                locked.save(update_fields=['status'])
                future = pool.submit(metadata)
                self.assertTrue(entered.wait(timeout=5))
                self.assertFalse(finished.wait(timeout=0.2))
            future.result(timeout=10)
        row.refresh_from_db()
        self.assertEqual(row.status, 'preview')
        self.assertEqual(row.title, 'Edited')


class CommittedCreationTests(CreationFixtures, TransactionTestCase):
    def test_post_commit_callback_failure_keeps_durable_association(self):
        endpoint, model, payload = self.cases[0]
        key, count = uuid4(), model.objects.count()
        with patch('content.views.upsert_content', side_effect=RuntimeError('callback failed after commit')):
            with self.assertRaises(RuntimeError):
                self.post(endpoint, payload, key)
        self.assertEqual(self.post(endpoint, payload, key).status_code, 201)
        self.assertEqual(model.objects.count(), count + 1)

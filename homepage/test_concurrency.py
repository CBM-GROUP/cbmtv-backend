"""Publication lock ordering proof requiring independent PostgreSQL sessions."""
from concurrent.futures import ThreadPoolExecutor
from threading import Event
from time import monotonic
from unittest import skipUnless

from django.db import close_old_connections, connection, transaction
from django.test import TransactionTestCase

from channel.models import Channel
from content.models import Content
from content.serializers import ContentSerializer


@skipUnless(connection.vendor == 'postgresql', 'Requires disposable PostgreSQL and independent sessions')
class HomepagePublicationConcurrencyTests(TransactionTestCase):
    def test_channel_reassignment_and_activation_preserve_parent_lock_order(self):
        old_channel = Channel.objects.create(name='Old inactive parent', is_active=False)
        new_channel = Channel.objects.create(name='New inactive parent', is_active=False)
        title = Content.objects.create(title='Before edit', content_type='movie',
                                       channel=old_channel, status='approved')
        self.assertIsNone(title.first_published_at)
        stale = Content.objects.get(pk=title.pk)
        metadata_waiting = Event()
        old_parent_acquired = Event()
        activation_parent_acquired = Event()
        metadata_child_checked = Event()
        metadata_pid = []

        def wait(event, description):
            if not event.wait(timeout=8):
                raise AssertionError(f'Timed out waiting for {description}')

        def configure_session():
            with connection.cursor() as cursor:
                cursor.execute("SET lock_timeout = '8s'")
                cursor.execute("SET statement_timeout = '12s'")
                cursor.execute('SELECT pg_backend_pid()')
                return cursor.fetchone()[0]

        def edit_metadata():
            close_old_connections()
            try:
                metadata_pid.append(configure_session())
                first_parent = True
                first_child = True

                def synchronize(execute, sql, params, many, context):
                    nonlocal first_parent, first_child
                    if first_parent and 'FOR UPDATE' in sql and 'FROM "channel_channel"' in sql:
                        first_parent = False
                        metadata_waiting.set()
                        result = execute(sql, params, many, context)
                        old_parent_acquired.set()
                        wait(activation_parent_acquired, 'activation to own the new parent')
                        return result
                    if first_child and 'FOR UPDATE' in sql and 'FROM "content_content"' in sql:
                        first_child = False
                        result = execute(sql, params, many, context)
                        metadata_child_checked.set()
                        return result
                    return execute(sql, params, many, context)

                with connection.execute_wrapper(synchronize):
                    serializer = ContentSerializer(stale, data={'title': 'Edited concurrently'}, partial=True)
                    serializer.is_valid(raise_exception=True)
                    serializer.save()
            finally:
                connection.close()

        def activate_parent():
            close_old_connections()
            try:
                configure_session()
                wait(old_parent_acquired, 'metadata to acquire the old parent')
                with transaction.atomic():
                    channel = Channel.objects.select_for_update().get(pk=new_channel.pk)
                    activation_parent_acquired.set()
                    wait(metadata_child_checked, 'metadata to check child ownership')
                    channel.is_active = True
                    channel.save(update_fields=['is_active'])
                    return Content.objects.values_list('first_published_at', flat=True).get(pk=title.pk)
            finally:
                connection.close()

        with ThreadPoolExecutor(max_workers=2) as pool:
            with transaction.atomic():
                Channel.objects.select_for_update().get(pk=old_channel.pk)
                metadata = pool.submit(edit_metadata)
                activation = pool.submit(activate_parent)
                wait(metadata_waiting, 'metadata to request the old parent lock')
                # Observe the database blocker, rather than relying on a sleep
                # to assume the worker has reached its SELECT FOR UPDATE.
                deadline = monotonic() + 5
                while True:
                    with connection.cursor() as cursor:
                        cursor.execute('SELECT pg_backend_pid() = ANY(pg_blocking_pids(%s))', [metadata_pid[0]])
                        if cursor.fetchone()[0]:
                            break
                    if monotonic() >= deadline:
                        self.fail('Metadata did not block on the original parent')
                    Event().wait(timeout=0.01)
                moving = Content.objects.get(pk=title.pk)
                moving.channel = new_channel
                moving.save(update_fields=['channel'])
            metadata.result(timeout=15)
            published_at = activation.result(timeout=15)

        title.refresh_from_db()
        new_channel.refresh_from_db()
        self.assertTrue(new_channel.is_active)
        self.assertEqual(title.channel_id, new_channel.pk)
        self.assertEqual(title.title, 'Edited concurrently')
        self.assertEqual(title.status, 'approved')
        self.assertIsNotNone(published_at)
        self.assertEqual(title.first_published_at, published_at)

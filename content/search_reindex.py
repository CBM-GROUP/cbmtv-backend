"""Build a verified shadow index and atomically replace the active search index."""

import time
import uuid
from urllib.parse import quote

import requests
from django.conf import settings

from .search_sync import SearchUnavailable


SEARCHABLE_ATTRIBUTES = [
    'title', 'director', 'cast', 'genres', 'genre', 'channel', 'description',
    'writer', 'country', 'category', 'format', 'content_type',
]
FILTERABLE_ATTRIBUTES = ['category', 'format', 'channel_id']


def rebuild_search_index(documents):
    """Leave the old index under a backup UID for a deliberate rollback."""
    base = (settings.MEILISEARCH_URL or '').rstrip('/')
    uid = settings.MEILISEARCH_INDEX
    if not base or not uid or not settings.MEILISEARCH_MASTER_KEY:
        raise SearchUnavailable('Search is not fully configured.')
    backup = f'{uid}_backup_{uuid.uuid4().hex[:12]}'
    headers = {'Authorization': f'Bearer {settings.MEILISEARCH_MASTER_KEY}'}

    def call(method, path, payload=None):
        response = requests.request(method, base + path, headers=headers, json=payload, timeout=30)
        response.raise_for_status()
        return response.json()

    def wait(task):
        task_uid = task.get('taskUid') or task.get('uid')
        if task_uid is None:
            raise RuntimeError('Meilisearch did not return a task ID.')
        for _ in range(120):
            result = call('GET', f'/tasks/{task_uid}')
            if result['status'] == 'succeeded':
                return
            if result['status'] == 'failed':
                raise RuntimeError(f'Meilisearch task {task_uid} failed.')
            time.sleep(1)
        raise RuntimeError(f'Meilisearch task {task_uid} timed out.')

    def indexed_ids(index):
        ids = []
        while True:
            page = call('GET', f'/indexes/{quote(index, safe="")}/documents?offset={len(ids)}&limit=1000')
            rows = page['results']
            ids.extend(row['id'] for row in rows)
            if len(rows) < 1000:
                return ids

    expected_ids = {document['id'] for document in documents}
    if len(expected_ids) != len(documents):
        raise RuntimeError('Duplicate content IDs in the reindex source.')
    wait(call('POST', '/indexes', {'uid': backup, 'primaryKey': 'id'}))
    shadow = f'/indexes/{quote(backup, safe="")}'
    wait(call('PATCH', shadow + '/settings', {
        'searchableAttributes': SEARCHABLE_ATTRIBUTES,
        'filterableAttributes': FILTERABLE_ATTRIBUTES,
    }))
    if documents:
        wait(call('POST', shadow + '/documents?primaryKey=id', documents))
    staged_ids = indexed_ids(backup)
    if len(staged_ids) != len(documents) or set(staged_ids) != expected_ids:
        raise RuntimeError(f'Shadow index verification failed; live index unchanged. Shadow: {backup}')
    active_settings = call('GET', shadow + '/settings')
    if (active_settings.get('searchableAttributes') != SEARCHABLE_ATTRIBUTES or
            not set(FILTERABLE_ATTRIBUTES).issubset(active_settings.get('filterableAttributes', []))):
        raise RuntimeError(f'Shadow index settings verification failed; live index unchanged. Shadow: {backup}')
    wait(call('POST', '/swap-indexes', [{'indexes': [uid, backup]}]))
    final_ids = indexed_ids(uid)
    if len(final_ids) != len(documents) or set(final_ids) != expected_ids:
        wait(call('POST', '/swap-indexes', [{'indexes': [uid, backup]}]))
        raise RuntimeError('Active index verification failed; previous index restored.')
    return {'indexed': len(documents), 'backup_index': backup}

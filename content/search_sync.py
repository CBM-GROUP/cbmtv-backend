"""Incremental Meilisearch maintenance for committed Content changes."""

import logging

import meilisearch
from django.conf import settings

from .models import Content


logger = logging.getLogger(__name__)


class SearchUnavailable(RuntimeError):
    """Meilisearch is not configured for this deployment."""


def get_search_index():
    if not settings.MEILISEARCH_URL:
        raise SearchUnavailable('Search is not configured: MEILISEARCH_URL is unset.')
    return meilisearch.Client(
        settings.MEILISEARCH_URL, settings.MEILISEARCH_MASTER_KEY, timeout=3
    ).index(settings.MEILISEARCH_INDEX)


def content_document(content):
    return {
        'id': content.id,
        'title': content.title,
        'genre': content.genre or '',
        'content_type': content.content_type or '',
        'description': content.description or '',
    }


def _wait_for_success(index, task):
    result = index.wait_for_task(task.task_uid, timeout_in_ms=3000)
    if result.status != 'succeeded':
        raise RuntimeError(f'Meilisearch task {task.task_uid} {result.status}: {result.error}')


def upsert_content(content_id):
    """Read committed state so an edit in a transaction indexes its final values."""
    try:
        content = Content.objects.filter(pk=content_id).first()
        if content is None:
            return
        index = get_search_index()
        _wait_for_success(index, index.add_documents([content_document(content)]))
    except Exception:
        logger.exception('Search indexing failed for Content id=%s; database save succeeded', content_id)


def remove_content(content_id):
    try:
        index = get_search_index()
        _wait_for_success(index, index.delete_document(content_id))
    except Exception:
        logger.exception('Search deletion failed for Content id=%s; database delete succeeded', content_id)

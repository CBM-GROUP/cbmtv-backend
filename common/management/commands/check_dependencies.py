"""Read-only operator diagnostics; never called by the public health endpoint."""
import json

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError
from django.db import connection

from content.search_sync import get_search_index


class Command(BaseCommand):
    help = 'Check database and configured search connectivity without printing credentials or changing either service.'

    def handle(self, *args, **options):
        results = {'database': 'unavailable', 'search': 'not_configured'}
        try:
            with connection.cursor() as cursor:
                cursor.execute('SELECT 1')
                cursor.fetchone()
            results['database'] = 'ok'
        except Exception:
            pass
        if settings.MEILISEARCH_URL:
            try:
                get_search_index().get_stats()
                results['search'] = 'ok'
            except Exception:
                results['search'] = 'unavailable'
        self.stdout.write(json.dumps(results, sort_keys=True))
        if 'unavailable' in results.values():
            raise CommandError('A configured dependency is unavailable.')

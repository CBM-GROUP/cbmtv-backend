"""
Rebuild the Meilisearch content index from the database.

The reindex was reachable only by pressing "Sync Search Data" in the admin
dashboard, which needs a browser session and a staff account. That is a poor fit
for a deploy step or a one-off repair, so the same rebuild is exposed here.

    python manage.py reindex_search
    python manage.py reindex_search --dry-run

Both paths call `content.views.rebuild_search_index`, so the CLI and the HTTP
route cannot drift apart.
"""
from django.core.management.base import BaseCommand, CommandError

from content.views import (
    SearchUnavailable,
    build_search_documents,
    rebuild_search_index,
)


class Command(BaseCommand):
    help = 'Rebuild the Meilisearch content index from the database.'

    def add_arguments(self, parser):
        parser.add_argument(
            '--dry-run',
            action='store_true',
            help=(
                'Report the target index and the document count without writing '
                'anything. Worth running first: MEILISEARCH_URL is read from the '
                'environment, and a local checkout pointed at a remote instance '
                'will rebuild that remote index from local data.'
            ),
        )

    def handle(self, *args, **options):
        from django.conf import settings

        target = settings.MEILISEARCH_URL or '(unset)'
        index_name = settings.MEILISEARCH_INDEX

        if options['dry_run']:
            documents = build_search_documents()
            self.stdout.write(f'target index : {index_name} on {target}')
            self.stdout.write(f'would index  : {len(documents)} document(s)')
            for document in documents[:10]:
                self.stdout.write(f'  {document["id"]}  {document["title"]}')
            if len(documents) > 10:
                self.stdout.write(f'  ... and {len(documents) - 10} more')
            self.stdout.write(self.style.WARNING('dry run -- nothing was written'))
            return

        self.stdout.write(f'Rebuilding "{index_name}" on {target} ...')

        try:
            indexed = rebuild_search_index()
        except SearchUnavailable as exc:
            raise CommandError(str(exc))
        except Exception as exc:
            raise CommandError(f'Reindex failed: {exc}')

        self.stdout.write(
            self.style.SUCCESS(f'Indexed {indexed} document(s) to Meilisearch.')
        )

# Catalogue search and index recovery

`GET /api/content/search/?q=African%20Wildlife` searches **Title**. It is identical to
`GET /api/content/search/?q=African%20Wildlife&search_by=title`.

The accepted `search_by` values are `title`, `everything`, `director`, `genre`,
`cast`, and `channel`. A different value returns HTTP 400. The `category` filter
accepts the four primary category IDs; `channel` accepts a positive channel ID.
`limit` (1–100) and zero-based `offset` paginate the results. The response keeps
`query`, `hits`, `count`, `total`, `offset`, `next_offset`, and `source`, and adds
`search_by`. Only approved Content rows are returned. Meilisearch ranks matches;
the database confirms each field-specific result still matches the requested
metadata field. Everything retains Meilisearch's typo-tolerant broad ranking.
If Meilisearch is unavailable, the database provides field-scoped matching.

Examples:

- `/api/content/search/?q=Kato&search_by=director`
- `/api/content/search/?q=Wildlife&search_by=genre&category=documentaries`
- `/api/content/search/?q=Daniel&search_by=cast&channel=4`

`Content.cast` is a JSON array of credited actor/performer names. Existing rows
receive an empty array in migration `content/0016_content_cast`; no cast names
are inferred from descriptions. Alternate titles are not modeled.

## Release and recovery

1. Apply `content/0016_content_cast` before serving the new API code.
2. Deploy the backend. Verify field-scoped API responses and approved-only
   results with representative titles.
3. Run `python manage.py reindex_search --dry-run` from the production API
   environment and confirm the approved document count and index name.
4. Run `python manage.py reindex_search` once. It creates a shadow index, waits
   for settings and document tasks, compares approved IDs, and swaps indexes
   atomically. The previous index stays under the printed backup UID.
5. Verify the active index count, field coverage, representative queries, and
   publication exclusions before releasing the dashboard and website.

If verification after release finds an index problem, swap the active index and
the printed backup UID through Meilisearch `POST /swap-indexes` using a secure
administrative connection. This restores the previous index without deleting
documents. Keep the backup until the release is accepted. The application
rechecks database publication status even if the index contains stale records.

Incremental indexing runs after committed content, genre, and channel edits.
Failures are logged without rolling back the editorial save. Run the safe
reindex command to reconcile an index after an outage or missed callback.

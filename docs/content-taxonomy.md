# Branch 2: content classification and genre contract

Implementation base: backend `d72876f0736d29597de8c6c6fc1bd77efc9b220c`
(`master`, Branch 1 merged); dashboard `ebae21b79761d17ce378965c5ddb4ef59c72face`
(`main`, Branch 1 merged). Work is in isolated `feat/content-taxonomy` worktrees.

## Data model and legacy upgrade

`content/0015_content_taxonomy` adds nullable `Content.category` and `Content.format`,
a many-to-many `Content.genres` relation, and the `Genre` table. It retains the
single `Content.channel` foreign key, legacy `content_type` and free-text `genre`,
all content IDs, publication/media fields, Season/Episode and MiniSeries tables,
and library foreign keys. The nullable additions let the older API coexist
briefly with the upgraded schema. Apply the migration before starting the new API.

The four category IDs are fixed: `movies`, `tv_shows`, `documentaries`,
`animations`. Formats are `standalone` and `series`. Categories have no staff
create/delete API. Genres have stable numeric IDs, a unique Unicode-casefolded
name key, an optional top-level parent, description, active state, and one or
more applicable categories. No genre DELETE endpoint exists; deactivate a used
genre to keep its assignments.

| Legacy `content_type` | Migration category | Migration format | Notes |
| --- | --- | --- | --- |
| `movie` | `movies` | `standalone` | Clear legacy meaning. |
| `documentary` | `documentaries` | `standalone` or `series` | Existing seasons imply `series`. |
| `animations` | `animations` | `standalone` or `series` | Existing seasons imply `series`. |
| `series` | unset | `series` | Could be TV, documentary or animation; staff classify. |
| `miniseries` | unset | `series` | Existing MiniSeries children stay in their table; staff classify. |
| `music`, `original`, unknown | unset | unset | Do not guess a category or format. |

Historical uppercase values are recognized for the clear mappings; the stored
legacy value itself is not rewritten. `tv_show` is a new compatibility type for
new standalone TV programming. Newly created series use legacy `series` while
their category distinguishes TV, documentary and animation series.

The migration links a single unambiguous legacy `genre` label on a classified
title into the managed Genre relation. One shared genre record can gain multiple
category flags. Compound text containing commas, semicolons or slashes, and
genre text on unclassified titles, remain untouched for review. The original
free-text field is never cleared. Review `Content.objects.filter(category__isnull=True)`
and legacy `genre` strings that have no managed relation after rollout; assign
categories and genres through the dashboard. Existing approved ambiguous titles
remain publicly visible under their legacy paths, but do not appear in a new
category filter until classified. New approval transitions require category and
format; metadata edits to already-approved legacy titles remain possible.

The local development database read-only inspection found three titles (`movie`,
`series`, `animations`), one season, two episodes and no duplicate episode-number
groups. It is not evidence of production catalogue composition. A migration test
creates realistic pre-0015 records and proves content/channel/status/media,
seasons, episodes, miniseries, saved items and watch progress survive upgrade.

## API

`GET /api/content/categories/` is public and returns exactly:

```json
[{"id":"movies","label":"Movies"},{"id":"tv_shows","label":"TV Shows"},{"id":"documentaries","label":"Documentaries"},{"id":"animations","label":"Animations"}]
```

`GET /api/content/genres/` and `GET /api/content/genres/{id}/` are public for
active genres. `?category=documentaries` filters applicable genres. Public
`used_count` counts approved titles only. Content editors can use
`?scope=dashboard` to see inactive genres and counts across all titles.
`POST /api/content/genres/` and `PATCH /api/content/genres/{id}/` require the
existing Content dashboard section permission. Example:

```json
{"name":"Nature","description":"Natural world","categories":["documentaries","animations"],"parent":null,"is_active":true}
```

Responses include `id`, the same editable fields, and `used_count`.
`parent` is a Genre ID or null. The API rejects duplicate names ignoring case,
empty/duplicate category sets, a parent with a parent, cycles, categories not
shared with a parent, and category edits that would invalidate children or
assigned titles. Deactivation leaves existing title links intact. Staff can
reactivate a genre through PATCH; viewers cannot write these endpoints.

`POST /api/content/` accepts one `category`, one `format`, one numeric `channel`,
and `genres` as an array of Genre IDs, alongside existing metadata and media
fields. Creation always returns `status: "preview"` even if the client submits
approved. The server derives the retained `content_type` when omitted. Example:

```json
{"title":"Wildlife of Africa","description":"A nature series","category":"documentaries","format":"series","channel":7,"genres":[12,18]}
```

The response keeps existing fields and adds `category`, `format` and `genres`:

```json
{"id":42,"title":"Wildlife of Africa","category":"documentaries","format":"series","content_type":"series","channel":7,"genres":[12,18],"status":"preview"}
```

The example response is abbreviated; current metadata fields remain. `PATCH`
supports editing classification and assignments. Movies must be standalone.
Genres must apply to the chosen category; inactive genres cannot be newly
assigned. An existing inactive assignment may remain during unrelated edits.
A title with seasons or MiniSeries children cannot be changed to standalone.
The `channel` field remains one foreign key; no multi-channel assignment exists.

`GET /api/content/` accepts composable `category`, `content_format`, `genre_id`
and existing `channel`, `content_type`, free-text `genre`, featured and trending
filters. `content_format` is the query parameter because DRF reserves `format`
for response renderer selection; the JSON field remains `format`. For example:

```text
GET /api/content/?category=documentaries&content_format=series&genre_id=12&channel=7
```

Without `scope=dashboard`, lists/details, channel pages, child endpoints and
search still expose only `status=approved` titles. Existing clients can keep
using `content_type`, `genre`, `/movies/`, seasons and miniseries paths. Season
and episode creation now accepts any title with series format and rejects
duplicate season or episode numbers within their parent. Parent-row locks
serialize conflicting API writes on PostgreSQL; direct database writes are not
protected by a new uniqueness constraint because existing production duplicate
data has not been audited.

## Search and rollout

Approved Meilisearch documents now carry `category`, `format`, managed `genres`
names, `channel` name, and the retained title, description, free-text genre and
legacy content type. Content changes and genre edits schedule incremental index
work after commit; unpublished content is removed rather than indexed. Public
search hydrates IDs from approved database rows and also matches managed genre
names/category/channel in its database fallback. Rebuild the existing index
after migration with the administrative `POST /api/content/reindex/` or
`python manage.py reindex_search`. The repository's existing search sync is
synchronous and non-durable; a failed callback is logged and requires reindex.
No analytics ranking is introduced.

The dashboard reads category labels from the API, offers category/format and
multi-genre controls, preserves the old free-text field for review, keeps the
single-channel selector, shows season management for any series format, and
adds searchable Genre Management under the existing Content permission. New
standalone titles use the existing principal-video upload; series use existing
season/episode media management. Save and explicit approval remain separate.
The public website's full category pages are a later workstream; consumers
using only legacy `content_type` may need to adopt `category` and `format` to
distinguish documentary/animated series from TV series.

Deploy order: migration, updated backend, search reindex, then dashboard. On
application rollback, keep the additive 0015 schema so older code can run and
new classification data survives. Reversing the database migration drops new
taxonomy data and needs an explicit backup/restore plan. Production legacy
composition and mixed-version consumer behavior must be reviewed before release.

## Verification

The full backend suite passes on disposable PostgreSQL 16 (242 tests) and
in-memory SQLite (242 tests, two PostgreSQL-only skips). Fresh PostgreSQL
migrations through content/0015 and the pre-0015 legacy-upgrade fixture pass.
Dashboard taxonomy tests, existing foundation tests, lint, TypeScript checks
and Next production build pass locally. This branch is left uncommitted in
isolated worktrees for review; no push, PR, merge or deployment is included.

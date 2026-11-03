# Homepage management — Branch 3

Prepared for separate PR review on `feat/homepage-management`. No merge,
migration against a shared database, index maintenance or deployment is included.
Branch 4 is not included.

## Source and deployment baseline

Verified on 3 November 2026 through local Git and read-only GitHub API calls:

| Repository / target | Worktree | Base |
| --- | --- | --- |
| Backend / `origin/master` | `D:/kalinzi/projects/cbmtv/.worktrees/backend-homepage` | `f126fc4cbbecb8965cc621b960c528e465ce8377` |
| Dashboard / `origin/main` | `D:/kalinzi/projects/cbmtv/.worktrees/dashboard-homepage` | `587b16ec200204d81db982301660a2f66d2baf3f` |
| Website / `origin/main` | `D:/kalinzi/projects/cbmtv/.worktrees/website-homepage` | `65b2094da85378fe0e05a6ae0112e8f1d352783c` |

These three isolated worktrees already existed with partial Branch 3 changes;
they were preserved, reviewed and completed. Their bases match the current remote
target tips and contain the field-specific search release. The original backend
and website checkouts contain unrelated uncommitted work and were left alone.

GitHub reports successful Backend CI and a successful deployment run for
`f126fc4`. Website and dashboard base revisions have successful Vercel statuses.
Later backend deployment workflow runs reference `ops/search-index-audit`, so
workflow metadata alone must not be treated as proof of the live API revision.
Public API health/category/homepage probes were unreachable from this environment;
the running production revision, database migration state and real media are
unverified. No production write was attempted.

## Models and migrations

- `channel/0004_channel_is_active`: adds `Channel.is_active`, default true,
  preserving existing channel availability.
- `content/0017_content_first_published_at_and_more`: adds nullable, indexed,
  API-read-only `first_published_at` and read-only `publication_history_unknown`.
- `content/0018_preserve_unknown_publication_history`: marks every existing row
  without a known publication date as unknown. Neither creation dates nor current
  approval status can prove prior publication. This includes legacy drafts, which
  may previously have been published. No dates are invented.
- `homepage/0001_initial`: singleton `HomepageConfiguration`, unique
  `FeaturedTitle` placements with optional start/end dates, and unique
  `FeaturedChannel` placements. Position plus primary key gives deterministic order.
  Database constraints enforce singleton identity and end after start.

New titles receive their immutable first-publication date when approved in an
active channel. Approving a title in an inactive channel leaves the date null;
activating that channel stamps eligible titles without known/unknown historical
dates. Republishing and reactivating preserve existing timestamps. Content writes
and channel activation use transactions and parent-before-content lock ordering.
Existing API approval behavior (create draft, then approve) remains in place.
Publishing integrations must use the model/API lifecycle: raw SQL, bulk updates
and bulk creation bypass Django save hooks.

Legacy unknown dates remain null even after republishing and stay excluded from
New Releases. They remain eligible for the hero, categories and channels. A later
audited backfill may use independently verified publication evidence; there is no
date-invention or backfill workflow in this branch.

## Public API

`GET /api/homepage/` accepts anonymous or existing authenticated requests. It
returns every stable section in configured order, including disabled/empty rows:

```json
{
  "sections": [
    {"id":"featured_hero","enabled":true,"label":"Featured Hero","source":"editorial","items":[]},
    {"id":"featured_channels","enabled":true,"label":"Featured Channels","source":"editorial","items":[]},
    {"id":"continue_watching","enabled":true,"label":"Continue Watching","source":"personalized","items":[]},
    {"id":"new_releases","enabled":true,"label":"New Releases","source":"automatic","items":[]},
    {"id":"tv_shows","enabled":true,"label":"TV Shows","source":"automatic","items":[]},
    {"id":"movies","enabled":true,"label":"Movies","source":"automatic","items":[]},
    {"id":"documentaries","enabled":true,"label":"Documentaries","source":"automatic","items":[]},
    {"id":"animations","enabled":true,"label":"Animations","source":"automatic","items":[]}
  ]
}
```

Title items use the existing `ContentSerializer` contract, including `id`, `title`,
`category`, `format`, `channel`, thumbnail, trailer and stream links. Channel items
use `ChannelSerializer`. Personalized items use `WatchProgressSerializer`:

```json
{
  "id":21,"content":{"id":14,"title":"Example title"},
  "episode":null,"miniseries":null,"position_seconds":42,
  "duration_seconds":100,"completed":false,"updated_at":"2026-11-03T09:00:00Z"
}
```

The nested content object above is abbreviated; the actual API returns the full
existing serializer, not a different card-only shape. Anonymous users always get
an empty personalized array. Disabled sections have empty items. Clients hide
empty rails and render the hero's Browse content fallback when its items are empty.

Eligibility is approved status plus an active channel, shared through
`content.publication.public_eligibility`. It applies to homepage, public catalogue,
search/database fallback/index construction, child resources and library reads/writes.
The one-channel foreign key and four-category taxonomy are unchanged. Search mode,
ranking, pagination and existing API response envelopes are preserved.

Featured schedules use explicit timezone offsets or `Z`; naive timestamps return
400. Start is inclusive, end exclusive. Runtime reads exclude future/expired,
unpublished or inactive-channel titles immediately. A legacy `is_featured` fallback
is used only when no placements exist, so it cannot bypass a configured schedule.
Adverts are never used for entertainment hero placements.

Featured channels must be active when saved. Runtime additionally requires at least
one approved title. Their configured ordering survives filtering. Categories use
the primary category field, support standalone/series formats and return up to 12
titles each. They never infer categories from channel names.

New Releases uses the immutable first-publication date, inclusive cutoff and no
future dates, newest first with descending ID as tie-breaker, capped at six. The
default window is 30 days. Hours/days are elapsed durations; months/years subtract
calendar units in UTC and clamp month-end/leap-day dates.

All homepage, management, preview and library responses, including errors, send
`Cache-Control: private, no-store` and `Vary: Authorization, Cookie`. No shared
personalized response cache is introduced. Public homepage query count is bounded
(test ceiling 24); selected data is capped and genres are prefetched. No scheduler
or cache-invalidation worker is needed. Website refreshes at least once per minute,
and on normal query focus/reconnect; schedule accuracy at the API is per request.

## Staff API and workflow

Existing dashboard **Content** section permission authorizes all management and
preview operations. Viewer accounts, other-section roles, inactive accounts and
password-change-required accounts are denied by existing permission rules.

- `GET /api/homepage/manage/`: current editable configuration; placement entries
  also contain read-only `detail` objects for selectors and unavailable-item warnings.
- `PUT /api/homepage/manage/`: full atomic replacement; list order is saved order.
- `POST /api/homepage/preview/`: validates an unsaved full configuration and returns
  the effective public response without saving or loading anyone's viewing history.

Example request for PUT or preview:

```json
{
  "sections":[
    {"id":"featured_hero","enabled":true},
    {"id":"featured_channels","enabled":true},
    {"id":"continue_watching","enabled":true},
    {"id":"new_releases","enabled":true},
    {"id":"tv_shows","enabled":true},
    {"id":"movies","enabled":true},
    {"id":"documentaries","enabled":true},
    {"id":"animations","enabled":false}
  ],
  "featured_titles":[{"content":14,"start_at":"2026-11-03T12:00:00+03:00","end_at":null}],
  "featured_channels":[{"channel":2}],
  "new_releases":{"duration":30,"unit":"days"}
}
```

All eight identifiers must occur exactly once. Hero stays enabled and first;
Continue Watching stays enabled and cannot be deleted. Other sections may be
reordered and disabled. Maximums: 20 featured titles, 30 channels, duration integer
1–1000, unit `hours|days|months|years`. Duplicate IDs, unavailable selections and
invalid schedules return 400 without a partial save. Errors use existing DRF
field/non-field error shapes; permission failures use existing 401/403 behavior.
Concurrent editors currently use last-save-wins semantics.

Channel management lists accept `?scope=dashboard` for authorized Channel or
Content editors, including inactive channels. Public lists/details exclude them.
The dashboard channel service retains that scope across pagination. Content's
existing explicit dashboard scope remains unchanged.

The dashboard `/homepage` workspace provides searchable selectors, move/remove
controls, local-time date inputs converted to timezone-qualified API values,
optional section toggles, duration settings, Save/Cancel, loading/retry errors and
an effective unsaved preview. Invalid or rejected saves preserve the draft. It
reuses the existing page shell, controls, navigation and section guard.

## Continue Watching and Branch 4 boundary

The homepage reads only the current user's positive unfinished progress, most
recent first, capped at 12. For a completed episode/part with a later playable part,
it projects that next part at zero seconds without rewriting stored progress or
calling the whole series complete. It skips later parts without a stream link.

The website uses existing players and progress storage, restores a matching
unfinished movie/episode/part position, periodically records progress and flushes
pause/end samples. Trailers do not count as content progress. Guest playback is
preserved and guests do not record progress. Queued writes retain their owning
token; token subject is checked against the player account, preventing another
browser tab's account switch from leaking activity to the new account.
Account-bound requests refresh expired access through the existing refresh endpoint
and verify ownership again before replacing credentials or retrying. Homepage
personalization uses the same owner checks; an account mismatch falls back to an
anonymous response rather than displaying another account's progress.

The existing backend stores one progress row per viewer/title. It does not preserve
complete episode history, aggregate series completion, reconcile newly reordered
episodes or resolve multi-device conflicts. Those are Branch 4 dependencies, not
new episode-engine behavior in this branch. Existing library listing completion
semantics are retained; the homepage performs its own next-part projection.

## Local proof and reproduction

Backend full suite: **278 tests, passed, 3 PostgreSQL-only concurrency tests skipped**
under isolated SQLite. System checks, migration consistency and Python syntax pass.
Docker's Linux daemon was unavailable; PostgreSQL row-lock/concurrency execution
is checked by the PostgreSQL 16 PR CI job before approval; staging remains a release gate.

Dashboard: **45 tests passed** (all existing Node suites plus content Save suite and
homepage tests), typecheck, lint and production build passed. One existing lint
warning remains in `next.config.ts`.

Website: **21 tests passed**, typecheck, layout lint, ESLint and production build
passed. Nine existing warnings remain in unrelated components. ESLint plugin
resolution was corrected for pnpm's strict dependency layout, without changing
dependencies or the lockfile. Google Fonts required the host's system CA settings
for the build; TLS verification was not disabled.

Browser acceptance uses production Next servers and a disposable in-memory Django
API. It covers editorial selection/order, required sections, validation, preview,
Save/Cancel, revoked permissions, server-configured rendering, responsive sizing,
16:9 channel artwork, buttons/touch scrolling, viewer isolation and next-episode
links. Additional loading/error/guest/cross-tab checks are recorded in the final
workspace report and generated results. Fixtures have no real programme artwork
or encoded media: synthetic player events prove progress wiring, not codec quality.

Example PowerShell commands from the relevant worktree:

```powershell
$env:DJANGO_SETTINGS_MODULE='cbmtv.test_settings'
$env:PYTHONDONTWRITEBYTECODE='1'
python manage.py check
python manage.py makemigrations --check --dry-run
python manage.py test --noinput
# Use an environment with requirements.txt installed (Django 5.2.3).
python scripts/homepage_fixture.py
# Separate website terminal, after build with this same API URL:
$env:NEXT_PUBLIC_API_BASE_URL='http://127.0.0.1:8097'
node node_modules/next/dist/bin/next start -p 3017 -H 127.0.0.1
# Separate dashboard terminal, after build with this same API URL:
$env:NEXT_PUBLIC_API_URL='http://127.0.0.1:8097'
node node_modules/next/dist/bin/next start -p 3018 -H 127.0.0.1
# Backend terminal; Chrome must be installed:
node scripts/homepage-browser.mjs
```

The fixture forcibly uses an in-memory database and ignores `.env`/database URLs.
The browser script uses fixed loopback origins and fixture-only credentials. It
restores configuration even on failure. Screenshots/results are local artifacts
under `build-artifacts/homepage-browser`, outside versioned source.

## Proposed production rollout — not executed

1. Review all three independent diffs. Run PostgreSQL CI, staging migrations and
   real authenticated playback checks. Verify actual deploy targets and migrations.
2. Take a database backup. Pause publishing/channel activation during the brief
   schema/code transition so old writers cannot create missing first-publication
   history between expansion and the new lifecycle code.
3. Apply additive channel/content/homepage migrations on staging, verify legacy
   dates remain null/unknown, verify existing content/episode/library relationships,
   then repeat the reviewed migration procedure in production with approval.
4. Release the backend first. Verify anonymous/authenticated contracts, permission
   rejection, schedules, unknown-history exclusion and cache headers. Resume
   publishing only after every writer runs the new lifecycle code.
5. Release the dashboard; save and preview an approved editorial configuration.
6. Release the separately reviewed website. Check mobile search, touch rails,
   images, guest playback and account-specific resume against real assets.
7. Monitor API errors/query latency and confirm inactive-channel exclusion. No
   production index rebuild is required for homepage; a future audited search
   maintenance task can remove old inactive-channel documents. Database eligibility
   filtering already prevents stale index hits from being exposed.

For rollback, restore the previous website, dashboard and backend code in that
order while preserving the additive schema and publication history. Pause
publishing while old code runs, or explicitly plan how missing publication history
will be reconciled from evidence. Do not reverse/drop these migrations casually:
that would discard editorial configuration and publication history. No destructive
schema contraction is authorized or needed here.

## Final review additions

The legacy-upgrade test exercises channel/0004, content/0017, content/0018 and
homepage/0001 together, preserving ownership, episode progress and unknown dates.
A PostgreSQL-only independent-session test exercises reassignment, metadata editing
and channel activation concurrently. Serializer writes retry if ownership changes
while waiting for parent locks and return 409 after bounded repeated conflicts.
The existing homepage permission, scheduling and privacy tests remain in the full suite.

Dashboard and website PR CI run tests, lint, typecheck and builds. Vercel Git
deployment is disabled specifically for `feat/homepage-management` in both client
repositories; feature pushes do not authorize previews or production deployment.

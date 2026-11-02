# Branch 1: backend foundation review

Date: November 2, 2026. This records the isolated implementation and its local
verification before publication. Production migration, AWS provisioning and
deployment were not performed.

## Baseline and isolation

| Repository | Original checkout / HEAD | Selected cached base | Isolated worktree |
| --- | --- | --- | --- |
| Backend | feat/dashboard-access-controls / 11cd7e619238cf7f654dbd737e0d8d1b9339c3e0 | origin/master / 0ab8693c04bf94c4a9779e38946f84a8dd395fc2 | .worktrees/backend-foundation, feat/backend-foundation |
| Dashboard | feat/dashboard-access-controls / 4042f43bb8d7eda1378c39c3e9e446b0537128d9 | origin/main / 10544039116f8377c6e13caefb56b1e6f27738b7 | .worktrees/dashboard-foundation, feat/backend-foundation |
| Dashboard deploy checkout | detached / 0aa692394584b9d4d7152c9e659a600346da1246 | cached origin/production / 7035c5813845603bd101e471b6004faf04c1f40e | untouched |

The backend original HEAD is 4 commits ahead / 7 behind its cached release
reference; the dashboard original HEAD is 4 ahead / 5 behind cached origin/main.
These are source comparisons, not production state. The selected bases were
subsequently confirmed against the live remote targets with `git ls-remote`:
backend `master` at `0ab8693` and dashboard default `main` at `1054403`.

Task 1's dirty-tree stop condition was observed. The user then explicitly
authorized isolated worktrees based on backend origin/master and dashboard
origin/main. No original user-management/auth/search work was stashed, deleted,
reset or transplanted. Existing stashes and checked-out branches were preserved.
The original backend has three modified Python files, one untracked Python test,
and 52 tracked bytecode changes, including a staged bytecode entry. The original
dashboard is clean. The exact final statuses are included below.

Existing release migrations end at accounts/0011, content/0014, channel/0003,
library/0001 and common/0001. Accounts includes its existing merged 0007 branches.
The original backend contains only an EC2 deploy workflow; the dashboard has its
existing Node lint/typecheck/build CI. Render, Railway, Compose and nginx artifacts
also exist, so the actual deployment target remains unverified.

## Findings and disposition

| Classification | Finding / result |
| --- | --- |
| Fixed | Three routable unauthenticated Mux operations and three dormant duplicate handlers now return 410 without a provider import/call. S3 multipart remains unchanged. |
| Fixed | Registration and normal password change now enforce Django validators. Seeded credentials and Django admin password forms also validate; admin creation hashes passwords. Existing reset and activation validation remains. |
| Fixed | DRF Basic Authentication is disabled. JWT and Django admin session authentication remain. |
| Fixed | Account-wide access/refresh revocation uses one additive session-version column; legacy tokens remain valid at zero until revocation. Ordinary stale User saves cannot restore revoked versions. |
| Fixed | Google/reset/activation throttles now use the existing trusted-peer forwarding policy, retaining their scopes/rates and login configuration names. |
| Fixed | Weak/missing production signing secrets stop startup. Local Compose no longer silently provides its short key; production Meilisearch requires a key and binds the host port to loopback. |
| Fixed | A pre-existing PostgreSQL nullable-join lock failure blocked three dashboard user-update tests. Restricting FOR UPDATE to the user row preserves administrator locking and permissions. |
| Fixed | Backend CI, exact-revision deployment CI gate, query-based readiness, liveness, private CLI diagnostics and generic reindex errors are added. |
| Already implemented | S3 multipart uploads, section permissions, administrator safety rules, invitations, Google verification/linking, email collision protections, login throttling, publication visibility/conflict protection, idempotent creation, console error logging and database search fallback. |
| Requires DevOps configuration | Strong stable secrets, confirmed database/deployment target, SMTP, proxy trust, shared throttle cache, private search networking, backup/restore ownership, monitoring/alerts and GitHub branch protection. |
| Deferred to another branch | Durable search outbox/retries, worker and scheduler implementation/deployment, event ingestion, ranking, media processing/transcoding, campaigns and scheduled product placements. |
| Unverified | Live revisions/migrations, Mux credential availability, AWS/CloudFront/S3 policies, production secret/mail/search configuration, production backup restores and actual GitHub Actions execution. |

The legacy handlers call Mux APIs. Current upload forms use Django-authorized S3
targets and multipart transport. There are no located callers of the legacy
useMuxUpload hook. Dormant src/api/create-live is outside the App Router; it is
unchanged and must not be mounted without authorization review. Server Mux
credentials were neither inspected nor removed.

## Tests and verification

| Verification | Result |
| --- | --- |
| Untouched cached release, existing isolated verification script | 204 tests, OK, two PostgreSQL-only skips; pip check, exact installed requirement pins, system and migration checks pass |
| Untouched cached release, PostgreSQL dashboard suite | 11 tests; three pre-existing nullable outer-join lock errors reproduced |
| Final full backend suite, PostgreSQL 16 | 234 tests, OK, no skips |
| Final full backend suite, in-memory SQLite, network disabled | 234 tests, OK, two PostgreSQL-only skips |
| Dependency / configuration checks | pip check, Django check, makemigrations --check --dry-run pass |
| Fresh PostgreSQL migration | Complete migration graph including accounts/0012 applies successfully |
| Existing-account upgrade | Pre-0012 account/hash preserved; new version is zero; pre-migration JWT authenticates after upgrade |
| Dashboard Node regression suite | 40 tests pass, including six new retired-handler tests and existing S3 upload/recovery/publication tests |
| Built Next route smoke | Nine anonymous/synthetic-viewer/synthetic-staff requests across the three App Router handlers return 410/no-store |
| Dashboard gates | Changed-file ESLint, TypeScript and Next production build pass; build also performs lint/type checks |
| CI/deployment configuration | All changed workflow YAML parses; both deployment shell scripts pass bash -n without execution |
| Compose configuration | Both files validate with disposable placeholders and --no-env-resolution; no production .env loaded |
| Diff whitespace checks | Both worktree git diff --check commands pass |

Docker image cbmtv-google-local:19167a7 supplies Python 3.12 and all exact pinned
requirements; branch source is mounted read-only. The earlier cbmtv-api:local
image lacked google-auth, an image mismatch rather than a backend test failure.
Tests used in-memory state or a dedicated internal-network PostgreSQL container
with temporary storage, no host ports, and no production credentials. Existing
CBM containers were not modified or restarted by this work. The temporary
PostgreSQL container and internal network were removed after verification. Final
inspection found the existing cbmtv-api:local container restarting (exit 1,
141 restarts at inspection), while its PostgreSQL container was healthy and
Meilisearch was running. Follow-up logs show WhiteNoise cannot load its static
manifest; a read-only copy of that container's `staticfiles.json` was zero bytes.
The image has no prebuilt manifest, and `collectstatic` succeeded in a fresh
disposable offline container. The existing local container was not restarted or
repaired, and its readiness remains failed. This is evidence of bad local
container state, not proof of any production host's state.
Dashboard dependencies were reused through a worktree-only node_modules junction.

Expected injected-failure tracebacks in the existing tests are not failed tests.
The only pre-existing backend test failures found were the three PostgreSQL
account-update errors above, now fixed. A new test-harness mock restoration issue
was corrected before the final passing runs. GitHub-hosted CI is a publication
gate and was not part of the local verification above.

## CI changes

Backend CI installs pinned requirements, checks compatibility/configuration and
migrations, applies migrations to a disposable PostgreSQL service, and runs every
backend test module, including cbmtv analytics/operations tests omitted by the
older scoped verification script. Test settings disable .env and external provider
configuration and restrict database targets. Ordinary tests need no AWS/Google/
production Meilisearch credentials.

CI remains separate from deployment. EC2 automatic eligibility requires a
successful push CI run on this repository's master and the existing explicit
EC2_AUTO_DEPLOY switch. Manual deploys also require successful CI for the exact
SHA. Existing remote SHA, health and built-image checks remain. This feature
branch does not trigger deployment. Dashboard CI adds the retired-route tests
before its existing build gates. Actual hosted workflow execution and branch
protection are still unverified.

## Architecture and operational deliverables

- [Background processing proposal](background-processing.md): database outbox first,
  bounded workers, optional SQS/DLQ transport, timestamp-based eligibility versus
  periodic jobs, retry/idempotency/failure handling, outages, deployment and costs.
- [Operations runbook](operations.md): secret/proxy/cache/email/network preflight,
  safe diagnostics, migration/session rollout and backup/restore evidence.
- Companion dashboard docs/legacy-uploads.md: route dependencies, closure and proof.

AWS resources requiring approval include any queue/DLQ, scheduler and execution
role, worker compute, IAM access, log groups/alarms, encryption-key/network
changes, secret access and production shared cache. No such resource was created.
See the proposal's linked primary AWS documentation for queue behavior and pricing
drivers; it is not a cost quote or spending authorization.

## Migration, compatibility and remaining risks

One new migration, accounts/0012_user_session_version, adds an internal integer
defaulting to zero. Apply it before the new API. There is no deletion or reset of
existing application records. Initial rollout preserves legacy tokens without a
version claim. Existing login/profile/refresh JSON shapes, section permissions,
S3 targets, publication rules and content APIs remain compatible.

Intentional changes: weak new passwords are rejected; Basic Auth is rejected;
password/security changes revoke existing access and refresh sessions; logout is
an additive authenticated all-devices endpoint; unsafe production secrets fail
startup; legacy Mux endpoints return 410. Website/mobile/current dashboard local
logout still need client adoption of the new endpoint for explicit server logout.
Existing weak passwords can still log in; no bulk password rewrite occurred.

An already running request is not retroactively canceled. All API instances must
run the new authenticator before revocation is fully enforced. Keep the additive
column on rollback: old API code ignores it and can accept revoked tokens. A
security rollback requires a deliberate signing/session policy. Process-local
throttle counters, synchronous non-durable search callbacks, console email default,
and possible non-test SQLite fallback remain documented limitations. Review the
strong-secret and Meilisearch access preflight before any authorized rollout.

## Review readiness

Ready for review in the two isolated feature worktrees. Publication requires
GitHub-hosted CI and branch-protection checks. Production adoption requires the
runbook preflight and confirmed deployment configuration. No further product
feature or worker infrastructure implementation is included in this workstream.

## Pre-publication Git status snapshot

Captured using git --no-optional-locks status --short --branch --untracked-files=all.

### Backend foundation worktree

```text
## feat/backend-foundation...origin/master
 M .env.example
 M .github/workflows/deploy.yml
 M DOCKER.md
 M accounts/admin.py
 M accounts/dashboard_access.py
 M accounts/management/commands/seed_superadmin.py
 M accounts/models.py
 M accounts/password_reset.py
 M accounts/serializers.py
 M accounts/tests.py
 M accounts/throttles.py
 M accounts/urls.py
 M accounts/views.py
 M cbmtv/settings.py
 M cbmtv/urls.py
 M cbmtv/views.py
 M content/views.py
 M docker-compose.prod.yml
 M docker-compose.yml
 M scripts/verify_phase1.py
?? .github/workflows/backend-ci.yml
?? accounts/forms.py
?? accounts/migrations/0012_user_session_version.py
?? accounts/sessions.py
?? accounts/test_foundation_security.py
?? cbmtv/test_foundation_operations.py
?? cbmtv/test_settings.py
?? common/management/commands/check_dependencies.py
?? docs/background-processing.md
?? docs/foundation-review.md
?? docs/operations.md
```

### Dashboard foundation worktree

```text
## feat/backend-foundation...origin/main
 M .github/workflows/node.js.yml
 M package.json
 M src/api/asset-details/route.js
 M src/api/create-upload/route.js
 M src/api/upload-status/route.js
 M src/app/api/asset-details/route.js
 M src/app/api/create-upload/route.js
 M src/app/api/upload-status/route.js
?? docs/legacy-uploads.md
?? tests/legacy-upload.test.mjs
```

### Original backend, preserved

```text
## feat/dashboard-access-controls...origin/feat/dashboard-access-controls [ahead 25]
 M accounts/__pycache__/__init__.cpython-313.pyc
 M accounts/__pycache__/admin.cpython-313.pyc
 M accounts/__pycache__/apps.cpython-313.pyc
M  accounts/__pycache__/models.cpython-313.pyc
 M accounts/__pycache__/serializers.cpython-313.pyc
 M accounts/__pycache__/urls.cpython-313.pyc
 M accounts/__pycache__/views.cpython-313.pyc
 M accounts/google_auth.py
 M accounts/migrations/__pycache__/0001_initial.cpython-313.pyc
 M accounts/migrations/__pycache__/0002_rolechangelog.cpython-313.pyc
 M accounts/migrations/__pycache__/0003_alter_rolechangelog_new_role_and_more.cpython-313.pyc
 M accounts/migrations/__pycache__/0004_user_auth_provider_alter_user_country_and_more.cpython-313.pyc
 M accounts/migrations/__pycache__/0005_remove_user_auth_provider.cpython-313.pyc
 M accounts/migrations/__pycache__/0006_user_auth_provider.cpython-313.pyc
 M accounts/migrations/__pycache__/0007_alter_user_image_to_url.cpython-313.pyc
 M accounts/migrations/__pycache__/0007_user_image.cpython-313.pyc
 M accounts/migrations/__pycache__/0008_merge_0007_alter_user_image_to_url_0007_user_image.cpython-313.pyc
 M accounts/migrations/__pycache__/__init__.cpython-313.pyc
 M cbmtv/__pycache__/__init__.cpython-313.pyc
 M cbmtv/__pycache__/settings.cpython-313.pyc
 M cbmtv/__pycache__/urls.cpython-313.pyc
 M cbmtv/__pycache__/wsgi.cpython-313.pyc
 M channel/__pycache__/__init__.cpython-313.pyc
 M channel/__pycache__/admin.cpython-313.pyc
 M channel/__pycache__/apps.cpython-313.pyc
 M channel/__pycache__/models.cpython-313.pyc
 M channel/__pycache__/serializers.cpython-313.pyc
 M channel/__pycache__/signals.cpython-313.pyc
 M channel/__pycache__/urls.cpython-313.pyc
 M channel/__pycache__/views.cpython-313.pyc
 M channel/migrations/__pycache__/0001_initial.cpython-313.pyc
 M channel/migrations/__pycache__/0002_auto_20250811_0952.cpython-313.pyc
 M channel/migrations/__pycache__/__init__.cpython-313.pyc
 M common/__pycache__/pagination.cpython-313.pyc
 M content/__pycache__/__init__.cpython-313.pyc
 M content/__pycache__/admin.cpython-313.pyc
 M content/__pycache__/apps.cpython-313.pyc
 M content/__pycache__/models.cpython-313.pyc
 M content/__pycache__/serializers.cpython-313.pyc
 M content/__pycache__/urls.cpython-313.pyc
 M content/__pycache__/views.cpython-313.pyc
 M content/migrations/__pycache__/0001_initial.cpython-313.pyc
 M content/migrations/__pycache__/0002_content_trailer_link.cpython-313.pyc
 M content/migrations/__pycache__/0003_alter_content_content_type.cpython-313.pyc
 M content/migrations/__pycache__/0004_season_description_season_trailer_link.cpython-313.pyc
 M content/migrations/__pycache__/0005_content_director_content_genre_content_writer_and_more.cpython-313.pyc
 M content/migrations/__pycache__/0006_episode_thumbnail_season_thumbnail.cpython-313.pyc
 M content/migrations/__pycache__/0007_contentadverts.cpython-313.pyc
 M content/migrations/__pycache__/0008_alter_contentadverts_advert_type.cpython-313.pyc
 M content/migrations/__pycache__/0009_content_country_content_status.cpython-313.pyc
 M content/migrations/__pycache__/0010_alter_content_status.cpython-313.pyc
 M content/migrations/__pycache__/0011_alter_content_content_type_miniseries.cpython-313.pyc
 M content/migrations/__pycache__/__init__.cpython-313.pyc
 M content/test_search_reindex.py
 M content/views.py
?? content/test_search_exhaustiveness.py
```

### Original dashboard, preserved

```text
## feat/dashboard-access-controls...origin/feat/dashboard-access-controls
```

### Dashboard deploy checkout, preserved

```text
## HEAD (no branch)
```

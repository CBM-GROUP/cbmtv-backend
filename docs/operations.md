# Foundation operations runbook

This is source-based guidance. Production topology, configuration, backups,
monitoring and migration state remain unverified. None of these production
operations was executed during this workstream.

## Configuration before rollout

- Keep the existing stable signing secret if it meets the new checks. With
  `DEBUG=false`, require at least 50 characters, five distinct characters and no
  `django-insecure-` prefix. A missing/weak key now stops startup. Never rotate a
  production secret casually: that invalidates JWTs and signed upload sessions.
- Single-quote generated values containing dollar signs in a Compose .env file
  to prevent interpolation from altering the intended signing key.
- Generate a new key only as an explicitly planned secret change. Store it in
  the confirmed deployment's secret store/private environment, not source or logs.
- Confirm the intended PostgreSQL target with the existing `dbinfo` command.
  The non-test settings still allow a SQLite fallback; a healthy wrong database
  is not proof of correct deployment. Never load a local .env into CI.
- Configure SMTP for reset mail. The existing console backend is still the
  default; production mail delivery has not been established by source inspection.
- Confirm allowed origins, hosts, HTTPS proxy headers, redirect/HSTS policy and
  ingress restrictions. Secure cookies remain enabled outside DEBUG.
- All credential endpoint throttles now use the existing
  `PASSWORD_LOGIN_NUM_PROXIES` / `PASSWORD_LOGIN_TRUSTED_PROXY_IPS` policy. Trust
  forwarded addresses only when the named proxy peer strips forged headers.
- Rate counters remain process-local without an explicitly approved shared
  cache. Multi-worker throttling is therefore best-effort per process. Shared
  cache infrastructure and any required client dependency are deferred.
- Local Compose now requires SECRET_KEY. Production Compose requires MASTER_KEY
  and binds the Meilisearch host port to loopback; same-network containers can
  still use `meilisearch:7700`. Confirm the actual API search target and admin
  access/tunnel before adopting that configuration. External firewall rules are
  unverified.

## Health and diagnostics

`GET /health/live/` reports process liveness without a database query.
`GET /health/` preserves its readiness contract (`ok`/200 or `unhealthy`/503),
but executes `SELECT 1` to detect an unusable existing connection. Both responses
are `Cache-Control: no-store` and expose no exception details or credentials.
Readiness does not assert migration compatibility or full product availability.

`python manage.py check_dependencies` is a read-only operator diagnostic. It
queries the database and, when configured, the Meilisearch index's stats API.
It prints only `ok`, `not_configured`, or `unavailable` for each dependency and
returns a failure exit code for an unavailable configured dependency. It does
not contact AWS or send email. Search failure should alert as degradation;
public search can still use the database fallback. Do not make search an API
restart dependency merely because this diagnostic checks it.

Console exception logging already exists. Reindex failures now log internally
and return a generic API error. Central log retention, actionable alerts,
dependency latency, request correlation and exception-monitoring infrastructure
still require operational decisions. Do not log tokens, reset codes, signed
upload URLs, passwords or request bodies in new instrumentation.

## CI and release boundaries

`Backend CI` installs pinned dependencies, runs pip check, Django checks, migration
consistency, a full migration into a disposable PostgreSQL 16 database, and the
complete suite. Settings are `cbmtv.test_settings`; external providers are mocked,
mail is in memory, and test database URLs are restricted to a dedicated name and
loopback/test-service hosts. SQLite is available when no test URL is supplied.

Local isolated checks (no .env or live providers are loaded):

```sh
python manage.py check --settings=cbmtv.test_settings
python manage.py makemigrations --check --dry-run --settings=cbmtv.test_settings
python manage.py test --noinput --settings=cbmtv.test_settings
```

For PostgreSQL tests, explicitly set CBMTV_TEST_DATABASE_URL to a disposable
cbmtv_foundation_test database on an allowed test host. Do not give these settings
an actual shared database at that name. The URL guard is an accidental-target
check, not infrastructure isolation. The test-only MD5 hasher never changes the
production password hasher configuration.

The EC2 workflow is separate. Automatic eligibility is a successful push-triggered
Backend CI run for this repository's master, with EC2_AUTO_DEPLOY explicitly true.
Manual runs also require successful Backend CI for the exact revision. The
existing remote-master SHA, clean-tree, container-image and health checks remain.
Feature branch CI does not deploy. Repository branch protection and the first
actual GitHub Actions run still need verification after an authorized push.

Before a controlled release, record the exact backend/dashboard revisions,
confirmed database target, migration plan and rollback plan. Run migrations once
from the release image; do not run multiple concurrent migration owners. The
existing web entrypoint defaults to running migrations, so coordinate that flag
with any one-off migration job. A passing health probe alone does not establish
that a particular schema migration is applied.

## Migration and session compatibility

Apply `accounts/0012_user_session_version` before starting the new API. It adds
one internal integer column with default zero; no existing account or content
record is deleted. Tokens without a session_version claim map to zero, preserving
existing sign-in sessions on initial rollout. Newly issued password and Google
tokens carry the current version; login/profile/refresh response shapes remain.

`POST /api/accounts/logout/` requires a valid access token and revokes all of that
account's access and refresh tokens on every device. It returns 204. Password
change/reset, completed invitation activation, invitation PIN reissue, seeded
password reset, and Django-admin password change also revoke sessions. Deactivation
through dashboard API/admin revokes so reactivation does not resurrect old tokens.
An already running authenticated request is not canceled retroactively.

Registration and ordinary password changes now enforce the same configured
validators as reset and activation. Weak new passwords receive field errors.
Existing weak passwords can still log in; existing accounts are not rewritten.
Django admin uses password-aware forms rather than saving a raw password field.
Basic Authentication is removed from DRF; JWT clients and Django admin session
authentication remain supported. Confirm any operational Basic Auth usage before
rollout.

Clients that currently clear local storage can adopt the new logout endpoint
without changing login/refresh contracts. They should clear local credentials
after successful revocation; offline local logout cannot prove server revocation.
This branch does not change website/mobile/logout UI behavior. Automatic password
and deactivation revocation is enforced server-side regardless of client adoption.

All API instances must run the new authentication code to enforce revocation.
Keep the added column on an application rollback. Old authentication code ignores
the revocation version and could accept revoked tokens, so a security rollback
requires an explicit session/signing-key policy. Do not claim that reverting the
application preserves revocation protection. Trusted maintenance code that changes
passwords directly through the ORM must revoke sessions in the same transaction.

## Backup and restore ownership

Before any production migration, DevOps must identify the actual database owner,
RPO/RTO, encrypted backup destination, retention, access policy and last successful
restore drill. Existing volumes are persistence, not backup proof. Verify S3 media
retention/versioning separately from database records and Meilisearch.

1. Capture a consistent PostgreSQL backup using the approved platform snapshot or
   pg_dump mechanism; record revision, migration state, time and integrity check.
2. Restore to a newly provisioned isolated target with no production credentials,
   outbound mail, search writes or media callbacks enabled.
3. Verify accounts, content/channel relationships, publication boundaries, schema,
   sample media references and the operator's required counts/invariants.
4. Rebuild a separate search index from the restored database and confirm public
   visibility; never overwrite the live index during a restore drill.
5. Measure restore time and record evidence before marking RPO/RTO satisfied.

Do not copy a dump into an existing production database as an informal rollback.
No production backup, restore, failover, secret rotation or infrastructure change
was performed here. The job/outage design is in [background-processing.md](background-processing.md).

# Phase 1 corrective contract and migration

Local implementation only; no shared migration or release is authorized.

## Durable creations

Migration `common/0001_initial` adds only `common_creationoperation`; it does not
rewrite existing records or add constraints to content/channel tables. The table
stores actor FK, operation UUID, endpoint, SHA-256 canonical JSON payload digest,
created record ID and creation timestamp. `(actor, key)` is database-unique.
Creation and operation association commit atomically; concurrent retries lock
the same operation row. Serialization and HTTP delivery occur after that commit.

The five creation endpoints accept optional `Idempotency-Key: <UUID>`. Existing
clients without the header retain their current contracts. OPTIONS advertises
`Idempotency-Supported: v1`. A keyed successful response echoes the UUID and
`Idempotency-Replayed: true|false`, returning HTTP 201 and the current authorized
representation of the same record. Permission checks run on every replay.
Different endpoint/payload with an existing key returns 409; a deleted record
returns 410 and is never recreated. Validation failure rolls back the operation
reservation and can be corrected using the same key. Canonicalization ignores
JSON object key ordering, but distinguishes types, omitted fields and null.

Keys do not expire automatically. Retain operation rows while the actor exists,
including tombstones for deleted records; do not purge them on a TTL. Actor
deletion cascades operations; that deleted actor cannot authenticate/retry.
New legitimate creations always receive a new UUID, even for identical payloads.
No filenames or media hashes establish operation identity.

Dashboard unresolved requests store the UUID, actor ID, endpoint and exact JSON
payload (including completed media delivery URLs) in localStorage before POST.
No credentials or File bytes are stored there. Storage failure prevents POST.
A recovery panel replays unresolved operations after refresh for the signed-in
actor. It never automatically starts another upload or replays a request without
the operator selecting recovery. In-progress transfers before a final create
request are still session-only. Recovery requires the original browser storage;
clearing storage loses that recovery list, not the server's deduplication record.

## Compatibility and rollout

Apply this additive migration to an authorized environment before starting the
new backend. All backend workers must support v1 before the new dashboard is
released. The dashboard checks OPTIONS support and refuses unsafe creation
against an older backend. Partial/mixed backend rollouts are not supported for
the new dashboard. Existing website/mobile requests remain compatible and are
not modified. CORS permits/exposes the new headers for authorized origins.

Rollback: stop the new dashboard; retain the additive table/rows, roll back code
only and resume old clients if needed. Do not reverse/drop the table while old
operation keys may be retried. A schema reversal requires separate authorization
and a recovery-retention plan; it destroys deduplication history.

## Publication

Content updates reload and lock the current row inside an atomic transaction.
Only explicit status payloads change status. Dashboard publication actions send
`If-Publication-Status` with their observed status; mismatches return 409. Legacy
authorized explicit status updates remain supported. This is a status comparison,
not a general record version or protection against an ABA sequence of explicit
editorial transitions. Logs are emitted after commit with actor/content/old/new.
Public filtering and Preview creation remain unchanged.

## Login throttle boundary

Forwarded client IPs are ignored unless BOTH a positive hop count and an exact
`PASSWORD_LOGIN_TRUSTED_PROXY_IPS` peer allowlist are configured. Only a valid IP
at the configured hop is accepted; missing/invalid chains fall back to the peer.
The trusted ingress must strip/replace caller-supplied headers and direct access
must be restricted before enabling this. No such deployment setting was changed.
The dashboard login proxy still supplies no verified browser identity; shared
source buckets remain a staging acceptance item. Retry-After now survives it.

## PostgreSQL regression runner

Use an explicitly disposable localhost PostgreSQL database called
`cbmtv_phase1_test`, with a dedicated test role permitted to create/drop test DBs.
Set `PHASE1_TEST_DATABASE_URL` privately, then run
`python scripts/verify_phase1.py --postgres common.test_idempotency`.
The runner disables dotenv, refuses non-loopback hosts/other database names and
creates a fresh randomly named test database. It never uses the deployment URL.

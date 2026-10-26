# Phase 1 corrective patch report

Date: October 26, 2026. Changes remain uncommitted in the existing
`phase1-backend` and `phase1-dashboard` worktrees. Original Phase 1 work was
preserved. Website, Android, production data and AWS infrastructure were not
modified. Nothing was pushed, merged or deployed.

## Results

| Finding | Correction and evidence | Remaining acceptance |
| --- | --- | --- |
| High: metadata save restores Approved | Content serializer reloads and locks the current row inside an atomic transaction before updating. Metadata payload still omits status. The exact stale-instance regression failed before correction (`approved` instead of `preview`) and now passes. | Independent PostgreSQL sessions and two browser editors. |
| High: committed creation retried as another record | All five management creates use a persistent user/UUID operation association committed atomically with the new record. Retries return that same ID. Response-serialization failure tests cover all five paths; a TransactionTestCase verifies recovery after an actual commit followed by a failing on-commit callback. | Real PostgreSQL concurrent retries; mounted browser refresh/recovery. |
| Medium: invitation activation selects first collision | Resolve at most two matches under the email guard and row locks; ambiguity returns the existing generic error before PIN checks/attempt mutations. Collision regression passes with both attempt counters unchanged. | PostgreSQL legacy-collision fixture and normal activation walkthrough. |
| Medium: token-bearing error logs | Affected Axios/Mux handlers log static operation labels; raw request/error objects and the profile-change payload are no longer logged. Full live-stream response logging was also removed because it can contain a stream key. | Disposable expired access/refresh failures in browser and staging collectors. |
| Medium: shared login bucket | No unverified browser IP forwarding added. Backend forwarding now requires configured trusted peer IPs AND hop count, with valid chain/IP checks. Proxy preserves Retry-After. Header-spoofing regression passes. | Shared Next/ingress source remains shared until a verified deployment topology/client identification policy is accepted. No production configuration was changed. |
| Medium: stale image confirmation | Proof captures the completed job ID/URL. Selection generations and unmount/reset guards reject stale results, including delayed preflight resolution. | Real image replacement/cancel/unmount behavior. |
| Medium: failed automatic attachment | A job explicitly marked transfer-complete can reconcile a matching saved record after attachment failure; incomplete/canceled jobs cannot. Manager regression proves one storage transfer and successful final association. | Actual S3/Django failure injection. |
| Upload retry overlap | Save reuses a matching in-flight tray retry. Canceled transfers cannot become successful when storage completion races cancellation. Actual manager tests pass. Multipart helpers remain unchanged. | Real S3 multipart/abort/response-loss tests. |

## Durable retry contract and migration

See [contract and rollout details](phase1-corrective-contract.md).

- **Migration:** new `common/0001_initial` creates only `common_creationoperation`.
  No existing content, user or channel rows are rewritten. Migration/check runs
  used disposable test databases only; no shared environment was migrated.
- **Identity:** unique `(authenticated actor, UUID)`, independent of filenames,
  title or payload similarity. Every legitimate new form session gets a new UUID.
- **Replay:** same key + endpoint + canonical JSON digest returns HTTP 201 and
  the current representation of the original record. Permissions are checked
  again. Different payload/endpoint returns 409. A deleted original returns 410;
  its key never creates a replacement.
- **Retention:** no automatic key expiry or TTL purge. Keep association/tombstone
  rows for the actor's lifetime. Deleting the actor cascades their operations.
- **Refresh recovery:** exact payload, delivery URLs and operation UUID are
  journaled in browser localStorage before any POST. Uncertain outcomes remain
  in Pending saves; Recover save replays the same request, without selecting or
  transferring files. Entries are scoped to the signed-in actor. Storage failure
  blocks creation. No credentials or File bytes are journaled.
- **Definite rejection:** backend validation rolls back the operation; a 400
  permits corrected form input. Timeouts/5xx/conflicts retain the original request
  for reconciliation, rather than silently assigning a new key.
- **Compatibility:** optional `Idempotency-Key`; existing clients without it
  retain legacy behavior and do not gain retry protection automatically. OPTIONS
  advertises v1; keyed success echoes the key and replay flag. New dashboard
  creates fail before POST against unsupported backends. Roll out the additive
  migration and all backend workers before this dashboard. Do not mix old/new
  backend workers behind the new dashboard.
- **Publication API:** optional `If-Publication-Status` checks the dashboard's
  observed status under the row lock, returning 409 when it differs. Existing
  authorized explicit status requests still work. This is not a general record
  version or protection against an ABA sequence of deliberate status changes.
- **Rollback:** keep the new table/history. Roll back application code/dashboard
  before considering schema reversal. Dropping operation history destroys safe
  replay and requires a separate retention/recovery decision.

The journal begins when the final create payload is prepared. Transfers still
in progress, or successful partial uploads before that point, are not a durable
cross-refresh queue. Browser storage clearing loses the recovery list; it does
not remove server deduplication. Completed uploads remain reusable within the
form, and final-create failures retain their URLs across refresh.

## Verification performed

| Gate | Result |
| --- | --- |
| Backend accounts/content/channel/library/common | **195 discovered: 193 passed, 2 PostgreSQL-only tests skipped** |
| Dashboard tests | **34 passed**, including existing Save/metadata/multipart plus creation-recovery and actual hook/manager regressions |
| Django system check | Passed |
| `makemigrations --check --dry-run` | Passed; no drift |
| Dashboard TypeScript | Passed; standalone no-emit check plus production build type check |
| Full dashboard ESLint | Passed, zero errors; one pre-existing anonymous-default-export warning in next.config.ts |
| Dashboard production build | Passed; all 25 static pages generated |
| Whitespace and multipart preservation | Diff checks passed; multipart transfer helpers/backend multipart files unchanged |

Backend verification disables dotenv, uses in-memory SQLite, local email,
test-only fast password hashing and mocked/disabled external services. Injected
500/503 errors and on-commit failures are expected regression scenarios. Browser
storage and hook/provider tests use controlled harnesses, not a full browser.
The build-generated next-env.d.ts change was restored to its exact pre-build
contents. No dependency/lockfile changes were made.

The original review reproduced a first POST returning 500 after saving one
Content, then a retry returning 201 with two Contents. Keyed corrective tests
inject the same persistence/serialization boundary and assert only one record,
the same returned ID, and a durable association. A separate real-commit test
raises from the search on-commit callback and proves replay still resolves the
same record. This is stronger than testing rejection before insertion.

PostgreSQL concurrency tests cover simultaneous keyed retries for all five
workflows and a metadata update waiting for an unpublish transaction to commit.
They are **not passed tests here**: Docker's Linux engine pipe is absent; no local
PostgreSQL service/binaries were available. The guarded `--postgres` runner and
disposable database instructions are included in the contract document.

## File-by-file corrective changes

Paths below are relative to the specified worktree. Earlier Phase 1 files with
no corrective edit are intentionally omitted from this list.

### Backend

| File | Corrective change |
| --- | --- |
| `.env.example` | Documents trusted login proxy peer setting, default empty. |
| `accounts/dashboard_access.py` | Rejects ambiguous activation before PIN/attempt mutation under identity/row locks. |
| `accounts/test_email_identity.py` | Adds activation-collision and trusted-peer/header regressions. |
| `accounts/throttles.py` | Requires trusted immediate peer, valid IP and sufficient configured chain before using forwarded address. |
| `cbmtv/settings.py` | Reads trusted peers; allows/exposes idempotency/publication headers for existing authorized CORS origins. |
| `channel/views.py` | Enables optional durable keyed channel creation. |
| `common/__init__.py` | Makes common an explicit package for model/test discovery. |
| `common/models.py` | Adds durable CreationOperation with actor/key uniqueness. |
| `common/migrations/__init__.py` | Initializes migration package. |
| `common/migrations/0001_initial.py` | Additive operation-table migration. |
| `common/idempotency.py` | Atomic keyed create/replay, mismatch/deletion handling and capability headers. |
| `common/test_idempotency.py` | Five-workflow retry/serialization, permissions, retention, mismatch, deleted-record, real-commit and PostgreSQL concurrency regressions. |
| `content/serializers.py` | Reload/lock current row, compare explicit status precondition and log transitions after commit. |
| `content/views.py` | Enables keyed content/episode/miniseries/advert creates; moves transition logging to the transaction-owning serializer. |
| `content/test_corrective_publication.py` | Exact stale-instance race and after-commit logging/stale-action conflict regressions. |
| `scripts/verify_phase1.py` | Isolated verification runner; optional guarded disposable localhost PostgreSQL mode. |
| `docs/phase1-corrective-contract.md` | Migration, API, retention, compatibility, rollback, proxy and PostgreSQL instructions. |
| `docs/phase1-corrective-report.md` | This evidence, acceptance and file summary. |

### Dashboard

| File | Corrective change |
| --- | --- |
| `src/lib/creation-operation.ts` | Actor-scoped durable journal, frozen payload checks, supported-endpoint checks and keyed creation protocol. |
| `src/services/createRecord.ts` | Connects journal to authenticated API transport; clears definite validation rejection and notifies recovery UI. |
| `src/components/upload/pending-creations.tsx` | Lists unresolved saves and explicitly replays stored requests after refresh; confirms matching live tray jobs from returned record. |
| `src/app/providers.tsx` | Mounts recovery panel inside existing auth/upload providers. |
| `src/services/contentService.ts` | Keyed create and observed-status publication header. |
| `src/services/miniseriesEpisodeService.ts` | Keyed miniseries create. |
| `src/services/advertService.ts` | Keyed advert create. |
| `src/services/channelService.ts` | Keyed channel create. |
| `src/features/content/content/publication.ts` | Passes observed status separately from target status. |
| `src/features/content/content/view/content-form.tsx` | Per-session creation UUID; uncertain network responses can safely retry the same operation. |
| `src/features/episode/episode/view/episode-list-view.tsx` | Per-session keyed episode create; static error logging. |
| `src/features/episode/miniseries-episode-form.tsx` | Per-session keyed miniseries create. |
| `src/features/advert/advert/view/advert-form.tsx` | Per-session keyed advert create. |
| `src/features/channel/channel/view/channel-form.tsx` | Per-session keyed channel create. |
| `src/components/upload/attachment.ts` | Immutable job proof and verified transfer-complete attachment recovery. |
| `src/components/upload/upload-manager.tsx` | Marks storage completion explicitly, reconciles later Save, shares active retries and preserves cancellation. |
| `src/hooks/use-media-upload.ts` | Returns immutable completed-job proof; invalidates stale results/preflight on reset. |
| `src/components/image-uploader.tsx` | Uses completed proof and current callbacks; guards replacement/unmount. |
| `src/app/api/accounts/login/route.ts` | Preserves backend Retry-After without forwarding untrusted browser IPs. |
| `src/features/content/content/view/content-list-view.tsx` | Removes raw authenticated error logging. |
| `src/features/channel/channel/view/channel-list-view.tsx` | Removes raw transport errors/error messages from logs. |
| `src/features/advert/advert/view/advert-list-view.tsx` | Removes raw authenticated error logging. |
| `src/features/episode/miniseries-episode-list-view.tsx` | Removes raw authenticated error logging. |
| `src/features/season/season/view/season-list-view.tsx` | Removes raw authenticated error logging. |
| `src/features/user/user/view/profile-view.tsx` | Removes changed-profile payload and raw error logging. |
| `src/hooks/use-mux-upload.ts` | Removes raw transport error logging. |
| `src/app/api/content/miniseries/route.ts` | Removes raw transport error logging; no endpoint behavior rewrite. |
| `src/app/api/content/miniseries/[id]/route.ts` | Removes raw transport error logging. |
| `src/app/api/asset-details/route.js` | Removes raw provider error logging. |
| `src/app/api/create-upload/route.js` | Removes raw provider error logging. |
| `src/app/api/upload-status/route.js` | Removes raw provider error logging. |
| `src/api/create-live/route.js` | Replaces full live-stream response and raw error logging with static labels. |
| `tests/content-actions.test.cjs` | Publication precondition, recovery and secondary image-proof regressions. |
| `tests/creation-recovery.test.cjs` | Five-workflow refresh recovery, distinct operations, mismatch, storage failure and unsupported-backend checks. |
| `tests/upload-recovery.test.cjs` | Actual hook/manager tests for failed replacement, reset/unmount, auto-attachment recovery, shared retry and cancellation race. |

## Remaining release blockers and manual acceptance

1. **PostgreSQL:** run both concurrency tests on a disposable database before
   accepting lock/concurrent-retry behavior. Also exercise email advisory-lock
   writers and deployment collation semantics. No production test is substituted.
2. **Browser:** run all five creation forms with a response dropped after commit,
   refresh, then Recover save. Verify one record, original URLs, deliberate new
   creation with a distinct operation, mismatch recovery and correct actor scope.
   Test two editor sessions, stale publication responses, and failure UI.
3. **S3:** inject single/multipart transfer and attachment failures; verify no
   repeated completed transfer, correct tray association, cancellation, expired
   presigns, component close/unmount, and Save during retry. Real S3 was not used.
4. **Proxy/security:** establish the real trusted chain and cache-worker behavior;
   verify legitimate staff do not exhaust a shared proxy bucket and spoofing
   cannot change identity. Check expired JWT/refresh flows and staging log sinks.
   Shared-source throttling remains unresolved at deployment level.
5. **Release sequencing:** authorize and apply the additive migration separately,
   upgrade all backend workers, then accept the dashboard. Retain operation
   history on rollback. No release is approved by these local checks.
6. **Existing gates:** production collision audit/remediation still requires
   separate authorization; browser metadata/legacy-size compatibility and real
   search/log collection acceptance from Phase 1 remain outstanding.

No significant architecture replacement was needed. PostgreSQL/S3/browser and
deployment-specific behavior remains explicitly unverified, not silently waived.

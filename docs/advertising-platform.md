# Advertising platform operations and API

This is local Branch 6 implementation. Advertising defaults to disabled. No payment,
advertiser portal, live broadcast, or new Featured Channels section is included.
Existing hero adverts remain a legacy editorial interface; they are not migrated
or silently approved as paid creatives. Existing content/channel ownership is unchanged.

## Dependencies and migrations

Base: backend `485fab3ff87ef66c2727af85a08f1a4570c6deb1`. This contains Branches 3–5,
including homepage configuration, protected media, legacy playback compatibility,
and discovery playback sessions. Install pinned requirements, including Pillow.

After an approved backup and isolated restore verification, apply:

1. Existing account, channel, content (`0019_mediaasset`), homepage, library and
   discovery (`0001_initial`) dependencies using Django's migration graph.
2. `advertising/0001_initial`: advertisers, campaigns, creatives, verified images,
   placements, context sessions, decisions, requests, events, daily cap totals, audit.
3. `advertising/0002_seed_placements`: ten placement records, all disabled.
4. `advertising/0003_creative_promoted_contents`: optional curated title collection.

Keep `ADVERTISING_ENABLED=false`. Existing protected-media AWS setup is a separate
release dependency: private S3 source/output policy, reviewed FFmpeg worker, custom
media hostname/TLS, trusted CloudFront key group, signed-cookie configuration,
CORS and unsigned-request rejection. This work provisions none of these. Images
need the existing image delivery host, S3 write permission and immutable caching
for `cbm-images/advertising/`. Upload-body/proxy limits must allow up to 5 MiB images.

Run the existing `process_media` worker for protected video uploads. New renditions
record ffprobe duration; older assets without verified duration need reprocessing
before advertising approval. Source videos remain private and are not returned by
public ad APIs. Staff preview grants expire after 300 seconds. All management,
decision, session, event and reporting responses use `private, no-store`.

## Staff workflow and access

The existing `adverts` dashboard permission manages advertisers, campaigns,
creatives, placements and reports. New `advertising_approval` permits approve/reject
workflow actions; grant it explicitly to custom roles. Admins and superusers
receive it; legacy staff are not silently given approval rights. Read-only status
fields cannot be used to bypass transitions through create/PATCH.

Campaign: create paused/draft → submit → review → approve or reject. An approved
campaign resumes only with an active advertiser, eligible end date and approved
creative. Future schedules may be resumed but do not serve before their start.
Pause stops new delivery; completion stops it. Editing resets approval to draft
and delivery to paused. Creative: upload → save → queue/process video if needed →
submit → successful preview of the latest revision → approve. Editing clears
approval. Preview/edit/approval share campaign→creative lock order. Audit records
contain actor, resource ID, action and time, without source URLs or credentials.

Images accept JPEG, PNG, WebP up to 5 MiB, width 320–8192, height 90–8192, at most
20 megapixels, and one frame. Server decoding verifies MIME and dimensions,
applies orientation, removes metadata, and writes bounded desktop/mobile WebP
variants without upscaling. Original image bytes are not retained. Video creatives
require an owner-bound completed protected upload no larger than 1 GiB, ready HLS
output, and verified duration at most 300 seconds. The shared media pipeline's
ordinary content rules remain in force.

HTTPS destinations disallow credentials, IP/localhost/internal destinations and
custom ports. Changing a destination invalidates approval. There is no redirect
endpoint: clients link to the reviewed URL. Internal promoted titles/channels and
collections are checked for current publication/active-channel eligibility at
delivery. Collection order is preserved, with up to six published titles.

## Placement configuration

Page: `homepage_banner`, `homepage_sponsored_section`, `homepage_featured_sponsor`,
`homepage_channel_promotion`, `homepage_content_promotion`, `channel_sponsor`,
`content_sponsor`. Video: `preroll`, `midroll`, `postroll`.

Homepage advertising sections are optional entries in the existing ordered
homepage configuration. Old configurations remain valid. Staff add, arrange and
enable them in Homepage Management, separately from placement enablement. At most
two may be enabled and the first three sections remain for browsing. The existing
Featured Hero and channel directory/relationships are preserved. Sponsored artwork,
cards and collections are clearly labeled; page creatives are images, video
placements are video. Mid-roll accepts up to three increasing breakpoints from
60 to 86400 main-content seconds. The supported failure policy is `continue`.

## API contracts

Prefix `/api/advertising/`; management lists use standard page/page_size pagination.
There are no destructive advertiser/campaign/creative DELETE APIs.

| Route | Methods and contract |
| --- | --- |
| `advertisers/`, `advertisers/{id}/` | GET/POST; GET/PATCH. Private contacts and billing reference. |
| `campaigns/`, `campaigns/{id}/` | GET/POST; GET/PATCH. Optional list advertiser filter. Schedule, limits, priority, contextual targets and placements. |
| `creatives/`, `creatives/{id}/` | GET/POST; GET/PATCH. Optional campaign filter. Asset IDs, destination, CTA, promotion IDs, collection, readiness and media status. |
| `{campaigns,creatives}/{id}/workflow/` | POST `{action}`: submit/approve/reject; campaigns also pause/resume/complete. |
| `creatives/{id}/preview/` | POST: staff image delivery or short-lived private source grant. Never log the URL. |
| `images/` | POST multipart `file`; returns verified owned image ID, dimensions and variant URLs. |
| `placements/`, `placements/{code}/` | GET lists, GET/PATCH individual; enabled, breakpoints, failure policy. |
| `targets/?q=` | GET: eligible categories, genres, channels, published titles; up to 100 results/group. Search to find additional targets. |
| `session/` | POST homepage/channel/content context with numeric context ID, or playback context with viewer-owned `playback_session`. Returns signed session receipt and permitted enabled placements. Disabled flag returns no session. |
| `decision/` | POST signed `session`, `placement`, `break_key` (`once`, or configured midpoint seconds), UUID `request_id`; returns `ad` and decision ID, or explicit `ad:null` and reason. |
| `events/` | POST signed `session`, `decision_id`, `kind`, integer `position_seconds`; returns accepted/duplicate or validation error. |
| `reports/?from=&to=&campaign=&advertiser=` | GET staff-only UTC date range within retained 90 days; recorded summary/grouped events, requests/decisions/no-ad reasons, lifetime and today's cap totals. Rates with no denominator are null. |

Only the server chooses campaigns/creatives. Clients cannot submit campaign IDs
to selection or event ingestion. Playback context requires a recent Branch 5
viewer-owned grant session with matching published content. Signed context
receipts cannot be transferred between accounts. Page sessions expire after two
hours, playback sessions at 24 hours from original grant creation; decisions and
their reservations expire after 15 minutes. Website page placements share a
bounded in-memory context session for ten minutes. No tracking cookie, permanent
device ID or sensitive user profile is created.

## Selection, limits and concurrency

Approval, active delivery state, active advertiser, half-open schedule
`starts_at <= now < ends_at`, enabled placement, compatible ready approved media,
contextual targeting and current promoted-title publication are required. Target
groups combine with AND; alternatives within a group use OR. Title metadata
targets match actual selected content, not every title in a channel. Homepage
has no implied category, genre or channel. Regions fail closed unless ingress
supplies a verified uppercase country through `X-CBM-Country` from an exact
`ADVERTISING_GEO_TRUSTED_PROXIES` peer. Strip incoming client country headers.

Rank campaigns by priority descending, never-served first, then least-recently
served timestamp and ID. Rotation is within equal priority. Prefer a creative
not previously selected in that context session, then ID. The candidate cursor
streams in chunks of 100, without a silent 100-campaign eligibility cutoff.
PostgreSQL `SKIP LOCKED` selects the highest-ranked unlocked eligible campaign;
contention can yield a lower-priority alternative or `capacity_busy` no-ad.
This avoids deadlocks while retaining transactional hard-cap protection.

Lock session, then campaign; event ingestion uses the same order. Hard caps use
persistent lifetime/daily impression counters plus all live unconsumed reservations.
Decisions are reservations, not impressions. Reservations from the previous UTC
day count conservatively against today's available capacity. Reporting an
impression rechecks active delivery and caps, updates counters once, and consumes
its reservation. Failure frees a reservation. Expiry frees unconsumed capacity.
Session frequency includes recorded impressions and live reservations across that
context's placements. No cross-device or person-level frequency claim is made.

Unique `(session, placement, break_key)` prevents repeat breaks even across
rerenders/renewals. UUID requests distinguish HTTP requests from decisions; reuse
for a different context/break returns validation error, including concurrent
collisions. Unique `(decision, kind)` makes ingestion replay-safe. Resumption
past a mid-roll consumes the earlier break client-side without an immediate ad.
Ads do not call organic progress/analytics callbacks. Post-roll completes before
the episode-next countdown. Decision/session calls time out at 1.2 seconds each;
media loading/buffering failures recover to authorized content after a five-second
ad deadline. Autoplay rejection displays a user Play control instead of bypassing
browser policy. Playback controls remain inside the existing fullscreen container.

## Event definitions and privacy limits

| Event | Condition |
| --- | --- |
| Request | Valid authorized decision request, unique client UUID; retries deduplicate. No impression inferred. |
| Decision | First evaluation for a context/placement/break, including explicit no-ad. |
| Impression | Image loaded and at least 50% visible in an active tab continuously for 1 second; video at least 50% visible and actually playing continuously for 2 seconds. Server also checks minimum elapsed/position time. |
| Video start | First actual media `playing` state; server stores start timestamp. |
| Quartiles | 25%, 50%, 75% of probed duration; server checks ordered milestones, impression, playhead and elapsed time. |
| Completion | Ended video, at least 95% duration and elapsed playback with prior milestones. |
| Click | Explicit approved external CTA or internal promotion/collection navigation; once per served decision, including a tap before viewability qualifies. Never implies an impression. |
| Skip | Explicit skip, only after the configured delay and server elapsed check. |
| Failure | Terminal media failure; once per decision, returns to main content. |

Clients serialize events, retry safely and mark milestones acknowledged only on
successful ingestion. Visibility loss, hidden tab, pause and buffering reset the
video qualification timer. Short/offscreen ads may have starts/skips/completions
attempts without a qualifying impression; server milestone checks prevent those
from becoming qualified completion metrics. Browser events cannot prove human
attention or stop a determined viewer with a valid session from simulating
telemetry; these reports are not independently audited billable exposure.
CTR divides actual clicks by qualifying impressions; an early tap can count
without a qualifying impression, so very small samples can exceed 100%. A missing
denominator remains null rather than inventing an impression.

Public advertising routes throttle anonymous traffic at 60/min and users at
240/min with the existing trusted-proxy IP resolver. DRF's configured cache is
process-local by default; production ingress/shared-cache limits are required for
an aggregate multi-worker abuse budget. Caps and event deduplication are database
transactional and do not depend on that cache. No IP or raw user agent is stored
in advertising analytics. Sessions reference account IDs solely for authorization;
account deletion cascades owned sessions/events. No raw sessions/identities appear
in reports. Lifetime delivery counters remain nonidentifying after raw deletion.

Schedule `python manage.py cleanup_advertising --apply` daily. The default command
is a dry run. It deletes decisions/requests/events and old sessions after 90 days,
old daily rows after 90 days and audit after 365 days, preserving lifetime caps.
Retention deliberately bounds detailed historical reports.
For advertiser contact erasure, authorized staff deactivate the advertiser,
complete its campaigns and PATCH contact name/email/phone/billing reference to
empty values; replace the business name where the retention policy requires
anonymization. These values never appear in advertising event/audit records.
Campaign IDs and nonidentifying lifetime delivery totals remain for cap safety.
Image/video objects follow existing media retention and S3 lifecycle policy; deleting database data
does not imply deleting protected source files. Review S3 orphan/lifecycle rules,
including unused verified image variants, before activation.

## Activation, monitoring and rollback

1. Verify exact Branch 3–5 release commits and live migration state; current local
   remote refs prove merge ancestry, not a fresh deployment. GitHub authentication
   was unavailable during this implementation.
2. Back up/restore-test the actual production DB. Install requirements, apply
   additive advertising migrations with the flag disabled. Deploy compatible
   backend and clients only with approval.
3. Verify private media, processing worker, signing/cookie configuration and a
   real upload→preview→ready→approval→playback cycle in staging. Verify mobile
   compatibility; mobile was not changed here. Test real devices and autoplay.
4. Assign reviewer permissions. Create inactive/draft campaign and creatives,
   configure destination/targets/caps, submit, preview, approve. Configure
   placements and homepage layout. Do not automatically populate sponsored rows.
5. After explicit activation approval, set `ADVERTISING_ENABLED=true`, enable a
   small reviewed placement and resume its approved campaign. Prove actual
   impressions/clicks/completions and empty no-ad behavior before expansion.

Monitor decision p50/p95 and no-ad reasons, main-video startup, ad startup,
buffering/failure recovery, media queue age/errors, request/event latency and
429s, report-query latency, raw-data retention and cap utilization. The website
emits numeric-only local `cbmtv:advertising-metric` decision/startup events; the
existing player metrics and server access/DB monitoring provide other timings.
Do not log receipts, tokens, signed cookies, preview/protected URLs or filenames.

Emergency rollback: set the server advertising flag false, pause campaigns and
disable placements. Ordinary authorized playback bypasses paid ads. Already
playing ads can finish or fail open; the kill switch does not remotely revoke a
video already delivered to a browser. Roll back compatible application artifacts
while retaining additive tables, media and lifetime cap history. Do not reverse
migrations or delete assets during an incident. No production activation,
deployment, branch merge, commit or push was performed by this branch.

Verification evidence, base commits, exact Git statuses, measurements and
remaining staging gates are in the workspace `BRANCH6_ADVERTISING_REVIEW.md`.

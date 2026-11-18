# Phase 2B allocation delivery policy

## Strategy and rollback

`ADVERTISING_ALLOCATION_DELIVERY_ENABLED` defaults to `false`. When false, the existing campaign selector is unchanged and allocation records cannot serve. When true, each decision request uses only the allocation selector. The two selectors never run independently for one opportunity. `ADVERTISING_ENABLED=false` and the audited operational pause disable either strategy. Turn the allocation flag off to return new requests to campaign selection; in-flight allocation decisions stop serving or accepting new impressions. No legacy campaigns are converted or activated.

Apply migration `advertising.0009_allocationday_and_more` before starting a server with this code. The migration only adds nullable attribution, counters, and indexes. Old campaign decisions, events, and counters remain intact. An older server can read the expanded schema after the flag is off. Do not reverse the migration while any Phase 2B server is running.

## Eligibility and campaign inheritance

Every allocation requires an approved, ready creative; an active client; enabled placement; active allocation; `starts_at <= now`; and either no end or `now < ends_at`. Homepage and playback context checks still apply. Promotion targets must remain published or active. An allocation does not authorize a creative merely because the creative was approved.

Campaign-linked creatives additionally require campaign approval, active status, a current campaign schedule, matching campaign and creative placement lists, targeting, session limit, and daily/lifetime caps. The allocation cannot override these rules. Direct client creatives need no campaign or creative placement list: the allocation itself grants the placement. Their existing destination inheritance still applies. Campaign and creative client IDs must agree.

Existing campaign limits count reservations from both strategies during a switch. For direct allocations, defaults are two reservations/impressions per client per viewer session, 100 impressions per allocation per UTC day, and 10,000 impressions per allocation lifetime. Playback sessions allow at most two video interruptions across pre-roll, mid-roll, and post-roll. A direct video can be skipped after five seconds. These defaults are intentionally conservative and should be reviewed with product owners before any production rollout.

## Selection and accounting

The selector filters by active status, placement, and aware start/end times in the database. It considers the least recently selected eligible client first, then a per-allocation virtual finish score. Each selection advances that score by `1 / priority`, giving higher priority more turns without permanently starving lower priority inventory. Activating an allocation aligns its score with the current client pool so a new allocation does not monopolize catch-up traffic. It locks the viewer session, client, optional campaign, and allocation before reserving caps. Each opportunity still gets one `Decision` and one `AdRequest` replay binding.

`Decision.allocation_id` is nullable to preserve historical campaign decisions. Events retain the existing `(decision, kind)` uniqueness rule. Direct impressions increment allocation and UTC-day counters under the allocation lock. Campaign-linked impressions also increment existing campaign counters. Reporting adds allocation attribution in `by_creative` and `by_allocation`; public payloads retain the existing fields and disclose no private client records. Dashboard allocation responses include eligibility reasons, while actual impressions remain a separate metric.

## Video and failure behavior

The website already uses the same decision contract for pre-roll, mid-roll, and post-roll. It keeps the main playback position through an interruption, consumes crossed mid-roll cues on resume, requests post-roll once, and releases primary content on ad timeouts or media failures. Allocation video responses use the existing protected HLS grant. A failed candidate can fall through to another allocation; it never blocks the main video.

## Controlled rollout

1. Integrate and deploy the additive schema and code with the allocation flag off.
2. On a disposable or staging database, verify eligible direct and campaign allocations, caps, tracking, and website playback.
3. After explicit authorization, enable the allocation strategy in staging and observe `by_allocation`, no-ad reasons, errors, latency, and creative-level metrics.
4. Enable in production only after a separate rollout decision. Roll back by turning the allocation flag off. The emergency flag or operational pause stops all paid delivery immediately.

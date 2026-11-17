# Advertising management Phase 1

This branch extends paid advertising. It does not change legacy `contentadverts`,
the editorial Featured Hero, the dashboard, or the website.

## Data and rollout

1. `advertising.0004` adds nullable direct creative ownership, optional campaigns,
   destination defaults, delivery allocations, and the operational switch.
2. `advertising.0005` fills creative ownership from the campaign and aborts if an
   existing owner disagrees with the campaign or a creative has no owner.
3. Deploy the application code and verify existing campaign-owned writes and new
   direct-client writes. Apply `advertising.0006` only after old backend instances
   have stopped writing, because they do not populate `Creative.advertiser`.
4. `advertising.0006` enforces non-null creative ownership.
5. `advertising.0007` rejects exact duplicate allocation start times for one
   creative and placement. The API also rejects overlapping intervals while
   locking the creative row for concurrent staff writes.

Before deployment, inspect the migration plan and test on a disposable copy of
production data. Do not roll back through `0004` after creating new direct-client
creatives or allocations without exporting those new records first: reversing
that migration removes their columns and table.

## Current behavior

- Existing campaign-based delivery remains the only live selection path.
  `DeliveryAllocation` records are configuration for a later phase and never
  cause a creative to serve. New direct-client creatives start in `draft` and
  remain non-public even after review until a future delivery integration.
- The staff creative API accepts a direct `advertiser`, an optional `campaign`,
  and optional initial `placements`. Existing callers that send only `campaign`
  continue to work. `GET /api/advertising/creatives/?advertiser=<id>` filters
  by direct ownership. Campaigns can be created as draft groupings without
  dates or placements; they cannot resume delivery until configured.
- Advertiser and campaign `default_destination_url` fields inherit after the
  creative URL. Creative responses include `effective_destination_url` and
  `destination_source`. Changing an inherited default resets affected creative
  approvals and ends their outstanding decisions. Creative changes continue to
  reset their own approval.
- `GET/POST /api/advertising/allocations/` and detail `GET/PATCH` manage
  dormant allocations.
  `POST /api/advertising/allocations/<id>/workflow/` changes their operational
  status; activation requires a ready, approved creative and a separate grant.
- `GET/PATCH /api/advertising/operation/` exposes the dashboard operational
  switch. The existing `ADVERTISING_ENABLED` environment flag remains the
  emergency kill switch. Its false value always wins. If no operational row
  exists, its effective value is true for compatibility with existing sites;
  the backend flag remains false by default.
- Reports add `creative=<id>` filtering and `by_creative` grouping. They keep
  the existing 90-day raw-event retention and campaign aggregates.

## Permissions

Existing `adverts` access continues to manage advertising. Existing
`advertising_approval` remains necessary for campaign approval and is accepted
for creative approval. New scoped grants include viewing, client management,
campaign creation, creative upload/edit/approval, delivery management,
activation, reporting, export (reserved), and operational settings. An
`advertising_settings` grant is required to change the operational switch.
`advertising_activate` is separate from approval. Legacy staff without a
dashboard role retain only their previous broad `adverts` grant.

## Next phase

Build the single Advertising dashboard with Clients, Ad Library, Delivery, and
Reports; inline client/campaign creation; mixed-file upload with one creative
per file; and clear labels for draft/Preview versus delivery status. Integrate
allocations into selection only after the precedence between campaign and
creative schedules, caps, and priority is specified and tested. Keep the legacy
editorial Adverts route accessible.

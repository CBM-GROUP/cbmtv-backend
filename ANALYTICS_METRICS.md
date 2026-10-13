# Dashboard analytics contract

`GET /api/dashboard/analytics/` returns current database snapshots. Access requires R4's explicit `analytics` dashboard section. Superusers and staff administrators (`admin` or `internal_admin`) inherit it. Existing section roles and legacy staff without an administrator role do not gain analytics automatically. A superuser can grant `analytics` to a role through the existing role editor. This extension changes the allowed JSON permission values and requires no new migration.

- Content totals include every Content row, including unpublished records. Publication status uses `unassigned` for null or blank status. Type, channel, and advert counts include all stored rows.
- User `active` means `is_active=True` and `must_change_password=False`; `inactive` means `is_active=False`; `pending_activation` means active with `must_change_password=True`. Invited users are in pending activation until password setup, including after a PIN expires.
- Saved totals count SavedItem rows. Distinct saved users count accounts with at least one saved row. The ten most-saved titles are ranked by saved rows.
- Progress users count accounts with any WatchProgress row, including completed rows. Unfinished rows count `completed=False`. The ten current unfinished titles are ranked by distinct users with an unfinished row. One row per user and Content can identify the latest episode or part, but cannot record earlier plays.

The schema has no playback event log. Saved rows are not views, and progress rows are not total plays. Watch time, retention, daily active users, historical trends, and unique viewing events cannot be derived reliably. Rankings and counts are current snapshots; deletion or account deactivation changes the underlying data. The endpoint returns aggregates without user-level viewing histories.

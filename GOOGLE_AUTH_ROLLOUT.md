# Google ID token repair: release procedure

The existing `/api/accounts/login/google/direct/` route now accepts only
`{"id_token": "<Google-issued credential>"}`. Its former email/Google ID
payload is rejected. The route must never be re-enabled with the former
contract, including during a mixed-version deployment.

Google sign-in is for public viewers only. Dashboard staff and administrators
continue to use email/password and the manual invitation and activation flow.
The mobile app has a compatible service contract but no Google sign-in UI.

## Google Cloud setup for the public website

1. In Google Cloud Console, configure the OAuth consent/branding settings and
   create or select an OAuth 2.0 **Web application** client for Google Identity
   Services. Do not use a dashboard or mobile client for this website widget.
2. Set **Authorized JavaScript origins** to the exact website origins that will
   display the widget. The current production website origin in this project's
   backend allowlist is `https://cbmtv.cbmgroupco.com`. Add an exact staging
   origin only if staging will test Google login. For local development, add
   `http://localhost:3000` and/or `http://localhost:3001` for the ports used.
   Origins consist of scheme, host, and optional port; omit paths and wildcards.
   Verify the canonical production domain before configuring Google Cloud.
3. Set the website build variable `NEXT_PUBLIC_GOOGLE_CLIENT_ID` to that Web
   application client ID. Set the backend's `GOOGLE_OAUTH_CLIENT_IDS` to an
   exact comma-separated allowlist containing the same client ID (and only
   other client IDs for clients that actually issue ID tokens to this API).
   The client ID is public; no Google client secret is needed for this
   callback-based ID-token flow. Keep the API's Django signing key private.
4. The current widget receives the credential in a JavaScript callback and
   posts it to the API. It does not use a Google redirect endpoint, so no
   **Authorized redirect URI** is required for this flow. If the UI changes to
   a redirect flow, configure the exact redirect URI before releasing it.
5. Confirm the backend CORS allowlist includes the public website origin and
   the website's API base URL points to the secured backend. These settings
   are separate from Google's Authorized JavaScript origins.

Google references: [Web client setup](https://developers.google.com/identity/gsi/web/guides/get-google-api-clientid),
[server-side ID-token verification](https://developers.google.com/identity/gsi/web/guides/verify-google-id-token),
and [callback versus redirect behavior](https://developers.google.com/identity/gsi/web/reference/js-reference).

## Integration preparation

Keep the release-candidate branches intact. Review the security commits on top
of their corresponding integration commits: backend R1/R4/R5B, website R5A,
and dashboard R1/R4/R5B. The dashboard security commit only removes dormant
social sign-in controls; its active email/password form and R4 permissions do
not change. The mobile commit updates an unused service contract and does not
enable a Google UI. Coordinate backend and website reviews as one release;
do not merge either independently into a deployment branch with automatic
production deployment. The backend schema and verified endpoint must be live
before the new website build is exposed to users.

## Configuration and migration

1. Apply the Google Cloud and environment configuration above. Do not add a
   mobile client ID until the mobile application has a real Google sign-in UI.
2. Block the Google route at the existing edge or use a short maintenance
   window. Apply `accounts.0011_user_google_subject`, then deploy the backend
   before the website update. Keep the route blocked until every old backend
   instance is gone. Otherwise an old instance still accepts the insecure
   payload. The column is nullable and unique and does not rewrite existing
   users or media. A missing allowlist fails closed.
3. Invalidate all previously issued application JWTs. The old route could
   issue tokens for another viewer by email, and merely fixing the route does
   not revoke those tokens. Coordinate signing-key rotation with all clients;
   this forces viewers and dashboard users to sign in again, including holders
   of refresh tokens. Verify production has a private, random signing key;
   do not use the checked-in development fallback. If the project later adds
   a token blacklist, targeted revocation can replace global rotation.
4. Verify every serving backend instance runs the new code and rejects the
   former `email`/`google_id` payload before lifting the route block. Drain
   old instances rather than allowing a mixed-version pool. Unblock the
   verified route and deploy the website build with its Google client ID. Old website/mobile
   callers get a validation error on the old payload; they cannot bypass
   verification. Do not expose the old endpoint on a second host or version.
5. Smoke-test new Google signup/sign-in, old-account linking, password login,
   activation/deactivation, refresh, and account switching against a staging
   database before production traffic.

## Existing accounts

Previously Google-linked accounts have no verified Google subject. Email
matching is deliberately insufficient to recover them. A viewer with a
working password can sign in and link a freshly verified Google token using
`POST /api/accounts/google/link/` with `id_token` and `current_password`.
The website offers this immediately after Google sign-in reports an existing
email. A legacy Google-only account must first set a password through the
existing password-recovery flow (which requires the account's email inbox),
or go through verified support recovery. Staff and administrator accounts
remain ineligible for this Google flow.

## Rollback

The nullable column can remain in place if clients are rolled back. A backend
rollback to the old direct-login implementation would restore the vulnerability
and is not a safe release rollback. Keep the secure backend deployed and, if
necessary, disable Google sign-in by clearing its allowlist while retaining
email/password access. Investigate any token-rotation impact before rollback.

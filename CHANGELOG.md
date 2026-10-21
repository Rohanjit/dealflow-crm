# Changelog

Security-relevant changes are listed with what they mean for an install.

## Unreleased

### Security baseline (phase S1 of decision 63)
- **Passwords:** Argon2id is the only hasher. At least 12 characters, at most 256, not similar to the
  person's name or email, not common, not all numbers, and not one of their last 12 (only hashes are kept).
  *Existing installs:* none exist, so no older hashes were carried over.
- **Sign-in lock-out:** 5 failures for one account lock it for 30 minutes from any address; 20 from one
  address lock that address. Stored in the database (holds across server processes); account keys are
  hashed emails, so unknown and real accounts behave identically. An admin's password reset lifts it.
  The per-process login rate limit was removed in favour of this.
- **Sessions:** signed out after the firm's idle time (Settings > Security, 15 min to 8 h, default 30) and
  12 hours after sign-in, and when the browser closes. Background polling (`X-Dealflow-Background: 1`)
  isn't activity; typing is. A new session id at every sign-in. Cached data is dropped from the browser's
  memory on sign-out.
- **Browser protections:** Content-Security-Policy (no outside or inline scripts, no framing, no plugins),
  Permissions-Policy (camera, microphone, location and more off; passkeys allowed), COOP and CORP
  same-origin, `X-Frame-Options: DENY`, `nosniff`, `Referrer-Policy: same-origin`, `Cache-Control: no-store`
  on every API answer.
- **HTTPS-aware settings:** with `DEALFLOW_HTTPS=1`, cookies are secure-only with the `__Host-` prefix, plain
  http is redirected, HSTS for a year (preload is opt-in: `DEALFLOW_HSTS_PRELOAD=1`, because anyone can submit
  a domain that sends it). `DEALFLOW_BEHIND_PROXY=1` trusts the proxy's forwarded protocol and address.
  `manage.py check --deploy` reports no issues.
- **Host header:** `DJANGO_ALLOWED_HOSTS` now defaults to localhost only; servers must name their address.
- **Invite links:** stored as SHA-256 hashes, shown once at creation, valid 72 hours (was 7 days).
- **Request bodies:** 5 MB for JSON and forms (was 50 MB); files are checked per feature.

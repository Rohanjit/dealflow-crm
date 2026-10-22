# OWASP ZAP baseline scan

- **Date:** 22 Oct 2026 · **ZAP:** 2.17.0 (`zap-baseline.py`, passive rules, 3-minute spider)
- **Target:** the shipped stack (`docker compose --profile https`), Caddy with TLS, image built from
  this commit, an install that is set up (sign-in page, unauthenticated surface)
- **Result:** **0 failures, 57 passes, 4 warnings** — each warning reviewed below.

| Warning | Verdict |
|---|---|
| Cookie No HttpOnly Flag [10010] | Expected: it's the `__Host-csrftoken` cookie, which the app must read to send the CSRF header (Django's double-submit design). It's `Secure`, `SameSite=Lax`, host-only and carries no session. The session cookie is HttpOnly. |
| CSP: style-src unsafe-inline [10055] | Accepted: the UI library sets inline styles and CSS variables. Scripts stay strict (`script-src 'self'`, no inline, no outside origins), so this can't run code. Revisit if the library supports style nonces. |
| Modern Web Application [10109] | Informational (single-page app). |
| Session Management Response Identified [10112] | Informational (ZAP noticed the session/CSRF cookies). |

Fixed because of this scan: `Access-Control-Allow-Origin: *` removed from the app's files, the app page
and every non-asset response marked `no-store, no-cache, must-revalidate, private`, `robots.txt`
disallowing everything and `X-Robots-Tag: noindex` on every response.

Re-run: `scripts/zap-baseline.sh` (needs the https profile running). An authenticated scan is part of
the outside penetration test ([brief](../docs/security/pentest-brief.md)).

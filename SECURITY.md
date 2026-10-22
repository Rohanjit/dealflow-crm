# Security policy

Dealflow holds a firm's deal pipeline, investor relationships, fees and confidential documents. We
treat every security report as urgent.

## Reporting a vulnerability

- Use GitHub's **private vulnerability reporting** on this repository (Security → Report a
  vulnerability). Please don't open a public issue, pull request or discussion.
- Include what you found, how to reproduce it, the version (or commit) and what an attacker could do.
- A PGP key for encrypted email will be published here before the repository goes public.

What we commit to:

| Step | Within |
|---|---|
| Acknowledge your report | 48 hours |
| Triage: confirm, rate severity (CVSS 4.0), share a plan | 7 days |
| Fix and release: critical | 30 days |
| Fix and release: high | 60 days |
| Fix and release: medium / low | next scheduled release |

We credit reporters in the release notes unless you'd rather we didn't. We won't take legal action
against good-faith research that respects the scope below.

## Scope

In scope: this repository's code, the Docker image and `docker-compose.yml` as shipped. Out of scope:
denial of service by volume, social engineering, physical attacks, findings that need a compromised
admin account or server, and third-party services (Gmail, Outlook) themselves. Never test against an
install that isn't yours; use a local copy (`docker compose up -d`) or the sample firm.

## Supported versions

Only the latest release receives security fixes. Installs update with
`git pull && docker compose up -d --build`, or by moving to the newest signed image
([`docs/verify-release.md`](docs/verify-release.md)).

## How Dealflow is protected

The full design is decision 63 in [`CLAUDE.md`](CLAUDE.md); the evidence is in
[`compliance/`](compliance/). In short:

- **Sign-in:** two-step login for everyone (passkeys or an authenticator app, recovery codes);
  Argon2id passwords of 12+ characters checked against common ones and the last 12; lock-out after 5
  failures; idle sign-out (admins choose, default 30 minutes) and a 12-hour limit; new-device alerts.
- **Access:** roles per module and per field (hidden / read only), enforced by the API on every path;
  restricted deals visible only to their team; password re-check for sensitive admin actions.
- **Encryption at rest:** SQLCipher database, Vault files under a firm key that each person's PIN
  unlocks (with an offline recovery key), encrypted backups and email attachments; keys kept apart from
  the data and rotatable ([`docs/key-management.md`](docs/key-management.md)).
- **In transit:** HTTPS through Caddy with automatic certificates, TLS 1.2/1.3, HSTS, `__Host-` cookies;
  without a domain the app only answers on its own computer ([`docs/https.md`](docs/https.md)).
- **Uploads:** allowlisted types checked by content, virus scanning that fails closed, files encrypted
  and served with `nosniff`, sandboxed previews.
- **Browser:** strict Content Security Policy (no inline or outside scripts), framing denied, no caching
  of API answers, CSRF protection on every change.
- **Audit:** every request in an encrypted, hash-chained log that the server can write but not read;
  checked nightly for tampering.
- **Delivery:** hardened containers (read-only, no capabilities, no privilege escalation), images and
  dependencies pinned by digest and hash, CI with tests and scanners on every change, signed releases
  with SBOM and build provenance.
- **Recovery:** daily and hourly encrypted backups, a weekly automatic restore test, and an incident
  runbook ([`INCIDENT_RESPONSE.md`](INCIDENT_RESPONSE.md)).

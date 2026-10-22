# Threat model

Method: STRIDE per trust boundary, for the shipped Docker install. Reviewed with each security phase
(decision 63); update it whenever a feature crosses a boundary. Data flows: [data-flow.md](../data-flow.md).

## What we protect

| Asset | Why it matters |
|---|---|
| Deal pipeline, mandates, fees | Leaks can kill deals, move markets and breach client confidentiality |
| Investor and contact relationships | A firm's core commercial asset |
| Vault documents (IMs, term sheets, financials) | Often price-sensitive or under NDA |
| Accounts, sessions, two-step factors | The way in to all of the above |
| Keys (database, master, backup, Vault, audit) | Whoever holds them reads everything at rest |
| Audit log | The firm's evidence of who did what |
| Backups | A full copy of everything |

## Who might attack

- **Outsiders on the internet** reaching the sign-in page: credential stuffing, phishing, scanning
  for known vulnerabilities.
- **A colleague** with a valid account trying to see deals, fields or documents their role doesn't allow.
- **A departed employee** with an old session, device or remembered PIN.
- **Someone with the server or a copy of its disks/backups** (hosting provider, stolen laptop for a
  Just-me install, misplaced backup).
- **The supply chain:** a poisoned package, base image or CI action.
- **A malicious file** uploaded knowingly or not.

## Boundaries and threats

### Browser ↔ server (internet)

| Threat | Mitigation | Evidence |
|---|---|---|
| **S**poofing: stolen or guessed password | Two-step login for everyone; passkeys are phishing-resistant; Argon2id; common/reused passwords refused; lock-out 5 per account / 20 per address | `core/tests/test_mfa.py`, `test_security_baseline.py` |
| Session theft | `__Host-` Secure HttpOnly cookies, SameSite, HTTPS + HSTS, idle and absolute limits, sign-out everywhere, new-device alerts | `accounts/sessions.py`, tests |
| **T**ampering: CSRF, request forgery | Django CSRF on every change; JSON bodies must be objects; same-origin policy | `dealflow/parsers.py` |
| XSS | React escaping; CSP without inline or outside scripts; `nosniff`; sandboxed image previews | `dealflow/security.py`, browser checks under CSP |
| Clickjacking | `frame-ancestors 'none'`, `X-Frame-Options: DENY` | headers |
| **I**nformation disclosure: account enumeration | Same answers for unknown accounts, invite links hashed | tests |
| **D**enial of service: upload floods, big bodies | 5 MB body limit (files streamed), per-file limits, Caddy body limit, container memory and process caps | compose |
| Downgrade / interception | TLS 1.2/1.3 only, HSTS, no plain http on networks | `deploy/caddy/Caddyfile`, `docs/https.md` |

### Within the app (authorisation)

| Threat | Mitigation | Evidence |
|---|---|---|
| **E**levation: reaching records or actions a role lacks | One permission check for every generic path (role × module × action), derived permissions, admin-only actions, password re-check for role/settings changes | `core/tests/test_setup_auth.py`, `test_investors.py`, `test_field_access.py` |
| IDOR on detail routes | Querysets filtered by role and restriction (`records.visible`) before lookup | `deals/tests/test_access.py` |
| Seeing hidden fields through side doors (search, filters, sorting, exports, imports, reports, email merge, insights) | Field access applied in each of those paths | `core/tests/test_field_access.py` |
| Restricted deals leaking through related records | `ModuleDef.hide` applied to tracker, documents, tasks, log, outreach origin, dashboard, matches | `deals/tests/test_access.py` |
| **R**epudiation | Every request in the sealed, chained audit log with old → new values; nightly chain check | `core/tests/test_audit.py`, `test_security_overview.py` |

### App ↔ files and uploads

| Threat | Mitigation |
|---|---|
| Malware delivered through the Vault or email | ClamAV on every upload, fails closed; re-check on opening; refused files reported to admins |
| Disguised file types, path traversal | Allowlist by first bytes; random stored names; original names kept as data only; safe paths on restore |
| Spreadsheet formula injection | Exports and CSV prefix formula-like cells; Excel export writes text cells |

### Server and storage (someone with the disks)

| Threat | Mitigation |
|---|---|
| Copy of the data volume or a backup | Database (SQLCipher), Vault files, email uploads and backups encrypted; keys in a separate volume |
| Keys and data stolen together | Vault files additionally need a person's PIN (Argon2id + master key) or the offline recovery key; audit log needs the passphrase |
| A compromised container | Read-only root, no capabilities, no privilege escalation, non-root user, Postgres on an internal network, no Docker socket |
| Lost keys | Recovery key (Vault), key file download (backups), documented rotation; the app refuses to start rather than make new keys over encrypted data |

### Supply chain

| Threat | Mitigation |
|---|---|
| Malicious or vulnerable dependency | Hash-locked Python and npm installs, pip-audit and npm audit in CI, Dependabot |
| Poisoned base image or CI action | Images pinned by digest; actions pinned to commits; tools run from hashed lock files or digest-pinned images |
| Tampered release | Cosign keyless signatures, build-provenance attestation, SBOM ([verify-release.md](../verify-release.md)) |
| Secrets committed | gitleaks over the full history in CI |

## Known limits (accepted, revisit)

- **Postgres at rest** relies on disk or managed-service encryption; Dealflow doesn't encrypt Postgres
  columns itself ([postgres-encryption.md](../postgres-encryption.md)).
- **A fully compromised running server** can read what the app reads while it runs (open Vault sessions,
  decrypted records in memory). Mitigated by hardening and short sessions, not eliminated.
- **Password-protected PDFs and archives** can't be scanned inside; they're accepted (a setting away
  from refused: `AlertEncrypted yes` in `deploy/clamav/clamd.conf`).
- **Self-hosted operations** (host patching, firewalls, offsite backup copies) are the firm's; the
  checklist and docs guide them.
- **Base image OS packages:** the Debian-based Python image carries CVEs that Debian hasn't fixed yet
  (none critical; CI blocks any fixable HIGH or CRITICAL). Distroless and Wolfi bases were evaluated on
  22 Oct 2026: no fewer open CVEs, or no wheels for their Python version. Re-check each quarter.
- **Data room copies are readable by the running server** without a PIN (they must be, to watermark each
  page). They're encrypted at rest with a key from the master key, so the data volume alone is not enough.
  Watermarks deter and trace leaks; they can't stop someone photographing a screen.
- **Role changes** need one admin with a password re-check (Shobhit's decision), not two.

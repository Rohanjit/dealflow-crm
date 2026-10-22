# Controls mapping

How Dealflow's controls map to **OWASP ASVS 5.0**, **ISO/IEC 27001:2022 Annex A** and the **SOC 2
Trust Services Criteria (2017, revised points of focus 2022)**. Self-assessed by engineering on
22 Oct 2026; the outside penetration test ([brief](../docs/security/pentest-brief.md)) is the
independent check. Target: ASVS Level 2 everywhere, Level 3 for authentication, the Vault and
cryptography (decision 63).

Status: **Met** · **Partly** (gap named) · **Firm** (the install's operator does it; Dealflow helps) ·
**N/A**.

## OWASP ASVS 5.0

| Chapter | Status | How | Evidence |
|---|---|---|---|
| V1 Encoding and Sanitization | Met | ORM only (no string SQL); React escaping; formula-like cells prefixed on export; email headers built by the stdlib; NUL stripped | `core/tests/test_edge_cases.py`, `test_import_export.py`, semgrep in CI |
| V2 Validation and Business Logic | Met | Field registry validates every value (types, lengths, ranges, choices); pipeline rules server-side; bulk limits; double-submit guard | `core/records.py`, `core/tests/test_edge_cases.py` |
| V3 Web Frontend Security | Met | CSP without inline/outside scripts, `frame-ancestors 'none'`, `nosniff`, COOP/CORP, `__Host-` cookies, SameSite, no tokens in browser storage | `dealflow/security.py`, `test_security_baseline.py` |
| V4 API and Web Service | Met | One generic records API with role × module × action checks, JSON bodies must be objects, 5 MB body limit, `no-store`, uniform errors | `core/views.py`, `dealflow/parsers.py` |
| V5 File Handling | Met | Allowlist by content, size limits, random stored names, ClamAV fails closed, re-scan on open, attachments with `nosniff`, sandboxed previews, safe paths on restore | `vault/files.py`, `core/antivirus.py`, `test_antivirus.py`, `vault/tests` |
| V6 Authentication (L3 target) | Met | Two-step login for everyone; passkeys (WebAuthn, user verification) and TOTP (replay-protected); Argon2id; 12–256 chars, common/similar/reused refused; lock-out; one-use hashed invites; recovery codes | `accounts/mfa.py`, `test_mfa.py`, `test_security_baseline.py` |
| V7 Session Management | Met | Server-side sessions, rotated on sign-in, idle limit set by admins, 12 h absolute, end at browser close, list and end devices, sign everyone out, new-device alerts | `accounts/sessions.py`, `test_security_overview.py` |
| V8 Authorization | Met | Deny by default; module, field (hidden/read-only) and record (restricted deals) checks on every path including exports, imports, search, reports, email; admin-only actions with password re-check | `core/access.py`, `deals/access.py`, `test_field_access.py`, `deals/tests/test_access.py` |
| V9 Self-contained Tokens | N/A | No JWTs or bearer tokens; WebAuthn assertions verified server-side | — |
| V10 OAuth and OIDC | Met | Gmail/Outlook as an OAuth client: PKCE, state, exact redirect URI, least scopes, tokens sealed at rest, calls only to the providers' https hosts | `mail/providers.py`, `mail/tests` |
| V11 Cryptography (L3 target) | Partly | Vetted libraries only (cryptography, argon2-cffi, SQLCipher); AES-256-GCM, X25519, HKDF, Argon2id; keys apart from data, rotation, crypto-shredding. **Gap:** keys live in a protected volume, not a hardware module or cloud KMS | `vault/crypto.py`, `core/backup_crypto.py`, `docs/key-management.md`, `vault/tests/test_encryption.py` |
| V12 Secure Communication | Met | TLS 1.2/1.3 only via Caddy, automatic certificates, HSTS, http redirected; no plain http on networks; internal Postgres network | `deploy/caddy/Caddyfile`, `docs/https.md` |
| V13 Configuration | Met | Secure defaults (debug off, hosts allowlist, localhost-only without a domain), `check --deploy` clean in CI, hardened containers, pinned images and hashed dependencies, secrets generated per install | `docker-compose.yml`, `Dockerfile`, `.github/workflows/ci.yml` |
| V14 Data Protection | Met | Encryption at rest (database, Vault, backups, email uploads), field access, restricted deals, `no-store`, nothing sensitive in URLs or browser storage, deletion removes files | `docs/data-flow.md` |
| V15 Secure Coding and Architecture | Met | Threat model, SBOM, dependency scanning, Dependabot, bandit/semgrep, code review by CI gates, documented decisions | `docs/security/threat-model.md`, CI |
| V16 Security Logging and Error Handling | Met | Every request in a sealed, hash-chained audit log with old → new values and secrets masked; nightly integrity check; security alerts to admins; errors without stack traces | `core/audit.py`, `test_audit.py`, `docs/monitoring.md` |
| V17 WebRTC | N/A | Not used | — |

## ISO/IEC 27001:2022 Annex A (product-relevant controls)

| Control | Status | Dealflow |
|---|---|---|
| 5.15 Access control · 5.18 Access rights | Met | Roles per module and field, restricted deals, access requests with approval |
| 5.17 Authentication information | Met | Argon2id, password rules and history, recovery codes hashed, invites hashed |
| 5.24–5.27 Incident management | Met (+Firm) | `INCIDENT_RESPONSE.md`, sign everyone out, key rotation, alerts |
| 5.28 Collection of evidence | Met | Audit log export, evidence commands in the runbook |
| 5.30 ICT readiness for business continuity | Met (+Firm) | Hourly/daily encrypted backups, weekly restore test, RTO/RPO in `docs/backup-restore.md` |
| 8.2 Privileged access rights | Met | Admin-only key, backup and audit actions; password re-check; "manage users" can't touch admins |
| 8.3 Information access restriction | Met | Field access and restricted deals on every path |
| 8.5 Secure authentication | Met | Two-step login for everyone, passkeys, lock-out, session limits |
| 8.7 Protection against malware | Met | ClamAV, fails closed, re-scan on open |
| 8.8 Management of technical vulnerabilities | Met | pip-audit, npm audit, trivy, Dependabot, weekly CI run |
| 8.9 Configuration management | Met | Everything as code; hardened defaults; deployment checks |
| 8.10 Information deletion | Met | Permanent deletion removes files and logs; crypto-shredding by key deletion |
| 8.12 Data leakage prevention | Partly | Field access, restricted deals, export permissions, audit of exports. **Gap:** no export rate limits or watermarking yet |
| 8.13 Information backup | Met | Encrypted, tested backups |
| 8.15 Logging · 8.16 Monitoring | Met | Audit log, alerts, health checks; SIEM guidance |
| 8.20–8.22 Network security and segregation | Met (+Firm) | TLS, internal database network, localhost-only default |
| 8.24 Use of cryptography | Met | `docs/key-management.md` |
| 8.25 Secure development life cycle · 8.26 Application security requirements · 8.28 Secure coding · 8.29 Security testing | Met | Decision 63 requirements, threat model, tests, scanners, pentest brief |
| 8.32 Change management | Met | Branch per feature, CI gates, CHANGELOG with security effects, signed releases |

## SOC 2 Trust Services Criteria

| Criterion | Status | Dealflow |
|---|---|---|
| CC6.1 Logical access security | Met | Authentication, authorisation, encryption at rest |
| CC6.2 / CC6.3 Provisioning and role-based access | Met (+Firm) | Invites, roles, field access; periodic review with `access-review-template.md` |
| CC6.6 External threats · CC6.7 Transmission | Met | TLS, headers, lock-out, localhost-only default |
| CC6.8 Malware | Met | ClamAV |
| CC7.1 Vulnerability and configuration monitoring | Met | CI scanners, Dependabot, security checklist |
| CC7.2 / CC7.3 Anomaly monitoring and evaluation | Met (+Firm) | Alerts, audit log, monitoring guide |
| CC7.4 / CC7.5 Incident response and recovery | Met (+Firm) | Runbook, restore |
| CC8.1 Change management | Met | CI, signed releases |
| A1.2 / A1.3 Backup and recovery testing | Met | Hourly/daily backups, weekly restore test |
| C1.1 / C1.2 Confidential information and disposal | Met | Encryption, restricted deals, permanent deletion |

## Evidence kept here

Regenerated with `scripts/compliance-reports.sh` (CI also builds the SBOM on every change):
`sbom.cdx.json`, `pip-audit.json`, `npm-audit.json`, `trivy-image.txt`, `zap-baseline.md`, plus
`access-review-template.md` for the firm's quarterly review.

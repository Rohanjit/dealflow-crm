# Changelog

Security-relevant changes are listed with what they mean for an install.

## Unreleased

### Contact capture (decision 64)
- "Add from email or LinkedIn" (Contacts) lists people the person has emailed who aren't in Dealflow, from
  their own connected mailbox, visible only to them; newsletters (List-Unsubscribe, List-Id, Precedence,
  Auto-Submitted, Gmail promotions/updates), no-reply style addresses and colleagues are left out. Only the
  address, name, count and last subject are kept; the signature is read live from the mailbox when they
  choose to add someone.
- A browser add-on (Chrome, Edge, Firefox) with only `activeTab`, `scripting` and `storage`: it reads the
  LinkedIn profile, webmail email or selection the person clicks on, and hands it to Dealflow's check
  screen in the URL fragment (never sent to a server, removed from the address bar at once). It never
  calls the API or holds credentials.
- Before saving, Dealflow checks for the same person (email, mobile, LinkedIn, name at the firm) and shows
  what changed instead of creating a duplicate; firms are matched by website domain and name, or added in
  the same step through the normal create (permissions, stamps, audit).

### Email and calendar logging (decision 64)
- Each person can connect Google (Gmail + Calendar), Microsoft 365, Apple iCloud or any IMAP + CalDAV
  account. Google asks only for `gmail.readonly` and `calendar.events`, Microsoft for `Mail.Read` and
  `Calendars.ReadWrite`, and only for what the person ticks; tokens and app passwords are sealed.
- **Least data:** only emails and meetings with people already in Dealflow are logged, never mail among
  colleagues, never anything from before connecting. Dealflow stores the subject, time, participants and
  attachment names; the email text, meeting notes and attachments stay in the mailbox and are fetched live
  for the mailbox owner only (others get 403). Saving an attachment to the Vault goes through the file
  allowlist, the virus scanner and Vault encryption.
- Subjects of emails about a restricted deal are withheld from people who can't see the deal; participants'
  addresses follow field access on contacts' email fields.
- IMAP and CalDAV servers typed in by people are reached only at public addresses (checked, then pinned for
  the connection; `DEALFLOW_SYNC_ALLOW_PRIVATE=1` for a firm's own server), TLS 1.2+ with certificate checks;
  calendar XML is parsed with `defusedxml` (added, pinned) and documents with a DTD are refused.

### Security checklist, proof of recovery, evidence (phase S7 of decision 63)
- **Settings → Security opens with a checklist** of every protection (two-step coverage, HTTPS,
  encryption, keys, backups, restore test, scheduler, virus scanner, audit log) with the fix for
  anything that isn't right, plus sign-in activity.
- **Backups are proven to restore:** every week (and on demand) the newest backup is decrypted in
  memory and loaded into a separate temporary database; row counts and file checksums must match.
- **Hourly backups** (newest 24 kept): at most an hour of work can be lost.
- **The audit log's chain is checked every night** (without the passphrase); a break is an alert.
- **Security alerts for admins** in the bell: lock-outs, failed or missing backups, failed restore
  tests, a changed audit log, the virus scanner down.
- **Sign everyone else out** (Settings → Security, or `manage.py end_all_sessions` on the server).
- No page or API answer is cached anywhere, nothing is indexed by search engines, and the app's files
  no longer allow cross-origin reads. The health check now also checks the database.
- Documents: `SECURITY.md`, `INCIDENT_RESPONSE.md`, backup/restore, data flow, monitoring, threat
  model, penetration-test brief, and `compliance/` (controls mapped to OWASP ASVS 5.0, ISO 27001:2022
  and SOC 2; SBOM; dependency, image and ZAP scan results; access-review template).

### HTTPS by default, hardened containers, locked and scanned dependencies (phase S6 of decision 63)
- **No plain http on a network.** Without a domain the app answers only on its own computer (127.0.0.1).
  `DEALFLOW_DOMAIN` + `docker compose --profile https` puts Caddy in front: automatic certificates (or its
  own authority for internal names), TLS 1.2/1.3 only, HTTP/2 and HTTP/3, http redirected; the app turns on
  `__Host-` secure cookies, HSTS and proxy trust from that one setting.
- **Client addresses can't be spoofed** through `X-Forwarded-For`: only the entry the proxy added is used
  (lock-out counts, signed-in devices, audit log).
- **Containers** run read-only, with every Linux capability dropped (a few given back to clamav, Postgres
  and Caddy), no privilege escalation, process limits, memory-only `/tmp` (uploads never touch the disk
  before they're checked and encrypted), health checks and rotated logs. Postgres sits on a network with no
  internet access. All images are pinned by digest.
- **Image:** Python packages installed offline from wheels checked against hashes; pip removed; tests not
  shipped; gunicorn's control socket off.
- **Dependencies upgraded for known vulnerabilities:** Django 5.1 (end of life) → 5.2 LTS, Django REST
  framework 3.15 → 3.17, cryptography 46 → 50, React Router 6 → 7 (open redirect), Vitest 3 → 5 (dev only).
  pip-audit and npm audit now report none.
- **Outgoing mail requests** only go to Google's and Microsoft's https hosts, even when an address comes
  back in a provider's answer.
- **CI on every change:** tests (SQLite and Postgres), Django's deployment checks, pip-audit, npm audit,
  bandit, semgrep, gitleaks over the whole history, trivy (Dockerfile and image), an SBOM, and the full stack
  started with every container healthy. Releases are signed (Sigstore), attested and ship an SBOM.
  Dependabot proposes updates weekly.

### Virus scanning (phase S5 of decision 63)
- **Every upload is checked by ClamAV before it's stored:** Vault documents, email attachments from the
  computer and import files. Files are streamed to clamd (`INSTREAM`, stdlib client in `core/antivirus.py`,
  no new dependency); Vault files are checked before encryption, so the scanner sees the real content.
- **Fails closed:** no scanner set up, scanner not answering, an error answer, or a file clamd couldn't check
  completely (size or nesting limits, `AlertExceedsMax`) means the upload is refused (503
  `scanner_unavailable` / 400 `infected`) and nothing is stored. The Vault's upload screen says "Uploads are
  paused" while the scanner is down. Switching scanning off is only honoured on a development server.
- **Checked again when opened:** a Vault file that matches newer signatures is refused on download and
  reported (opening still works if the scanner is briefly down: the file was checked on upload).
- **Refused files** are recorded (`core.ScanRefusal`: who, where, file name, signature, SHA-256; never the
  file), written to the audit log, shown to admins in the notification bell and in Settings > Security, which
  also shows the scanner's state and how old its signatures are (flagged after 3 days).
- Docker Compose runs `clamav/clamav:1.4` with `deploy/clamav/clamd.conf` (limits above Dealflow's own, alert
  on anything not fully checked; password-protected PDFs and archives accepted, a setting away from refused).
  `manage.py check --deploy` warns when no scanner is set (`dealflow.W002`).

### Encryption at rest and keys (phase S4 of decision 63)
- **Keys are kept apart from the data** in `DEALFLOW_KEYS_DIR` (Docker: its own `dealflow-keys` volume at
  `/keys`; otherwise `<data>-keys` beside the data folder), owner-only. The Django secret moved there from the
  data folder. A copy of the data volume, the database or a backup is unreadable on its own. A missing key
  stops the app rather than making a new one over encrypted data. See `docs/key-management.md`.
- **Database:** SQLite installs use SQLCipher 4 (AES-256, `sqlcipher3-binary`, pinned); an existing plain
  database is encrypted in place on first start. Postgres: see `docs/postgres-encryption.md` (disk or
  managed-service encryption, TLS to the database).
- **Vault files:** AES-256-GCM under one firm Vault key, bound to the document (key id + SHA-256). The Vault
  key is stored only sealed to each person's X25519 key, whose private half is sealed by Argon2id(PIN) mixed
  with the master key, and with a recovery key shown once to an admin. While someone's Vault is open the key
  sits in their session sealed by a key only their browser holds (HttpOnly, SameSite=Strict cookie). Older
  files are encrypted the first time someone opens the Vault. Admins: Give access now, Make a recovery key,
  Use a recovery key, Change the Vault key (every file re-encrypted).
- **Backups:** AES-256-GCM in 1 MiB pieces (`.dfb`), key in the keys folder; Settings > Backups downloads the
  key file (password needed) to keep offline; restoring needs the password and, on another server, the key
  file. Older plain `.zip` backups still restore.
- **Files waiting to be emailed** (one-to-one and campaigns) are sealed with the master key; Vault documents
  attached to a campaign are kept as sealed copies so the scheduler never needs anyone's PIN.
- `manage.py rotate_keys --type=backup|database|secret` rotates keys; Settings > Security shows what is
  encrypted and where the keys are.

### Field access per role (phase S3 of decision 63)
- Roles & permissions sets, per module and per field, **Can change / Read only / Hidden** (`accounts.RoleFieldAccess`).
  Admins always see everything; the primary field, the pipeline's own fields and company details shown on
  another module (ruled by Companies) can't be restricted.
- **Hidden** fields never leave the server for that role: record pages and lists (`records.serialize`), the
  schema (the field isn't sent), search and filters (a hidden link filter matches nothing), sorting, lookups and
  search subtitles, exports, reports (incl. the PDF tracker table), imports, the dashboard (near-close and
  expiring lists vanish with their dates), Insights breakdowns and per-banker numbers, investor matching reasons
  and facts, related sections on record pages, mail recipient suggestions and campaign merge fields, and
  going-cold reminders.
- **Read only** fields are shown but refused on create, edit, bulk edit, merge and import (400, "Your role can't
  change this field"). Changes the app makes itself (Last contacted after a confirmed email) still apply.
- Role changes now need the admin's password (create, edit, delete) and are written to the audit log with the
  field-access rules before and after.

### Two-step login (phase S2 of decision 63)
- **Required for every account** before anything else works (`403 code: mfa_enrol_required`), Just-me
  installs included. Exempt: the sample firm's Guest and a public demo site, which hold no real data.
- **Passkeys** (WebAuthn via py_webauthn): user verification required, resident keys, only public keys
  stored, sign counts checked, relying party = the install's own host (one of `ALLOWED_HOSTS`). A passkey
  can be the second step or the whole sign-in ("Sign in with a passkey"): it is possession plus biometric
  or device PIN, and can't be phished.
- **Authenticator apps** (TOTP, 6 digits, 30 s, ±1 step): 160-bit secret sealed with a key derived from
  `SECRET_KEY`; a code can't be used twice; replacing the app keeps the old one until the new one is confirmed.
- **Recovery codes:** 10 one-time codes (≈50 bits each) hashed with Argon2id, shown once.
- **Sign-in flow:** the password alone signs nothing in; the second step must follow within 5 minutes in
  the same browser; wrong codes and refused passkeys count towards the account lock-out.
- **Changes need the password** once set up (adding or removing a passkey or app, new recovery codes); the
  last way in can't be removed. Admins (and people who manage users, not for admins) can reset someone's
  two-step login with their own password; that person is signed out everywhere and sets it up again.
- **Signed-in devices** in My preferences, with "Sign out" per device and "Sign out everywhere else"; a
  sign-in from a browser not used in 90 days shows in the notification bell ("It was me" / "Not me").
- Libraries added, pinned: `argon2-cffi`, `webauthn`, `pyotp`, `segno` (QR as SVG, made on the server).

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

# Changelog

Security-relevant changes are listed with what they mean for an install.

## Unreleased

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

# Security plan

How we turn `docs/security-hardening-brief.md` into work, now that all six V1 modules are built
(30 Sep 2026). The repo stays private until this is done.

## Approach

1. **Phase 1: hardening that doesn't change how people use Dealflow.** These are engineering choices, so Claude
   makes them and records them in `CLAUDE.md`. Work starts straight away.
2. **Phase 2: product decisions.** Shobhit decides these one at a time, in the order below, and each one is built
   once it's decided.
3. **Phase 3: proof.** This covers the security test suite and the scans that run on every push. It also covers the
   documents a bank's CISO will ask for: disclosure policy, incident response, key management, data flow, and a map of
   controls to OWASP ASVS, ISO 27001 and SOC 2.

## Phase 1 (no product decision needed)

| Area | What changes |
|---|---|
| Passwords | Hashed with Argon2id; existing passwords upgrade at next sign-in. At least 12 characters for new passwords. The last 12 passwords can't be reused. |
| Sign-in | After 5 wrong passwords the account is locked for 30 minutes, whichever device the attempts come from. An admin can unlock it. The error message is the same whether or not the email exists. |
| Invites | Only a hash of each invite link is stored, so a copy of the database can't be used to join. |
| Browser protections | Content Security Policy, no framing, referrer and opener policies, no-sniff. HSTS and secure cookies whenever HTTPS is in use. |
| Spreadsheets | Cells starting with `=`, `+`, `-` or `@` are neutralised in exports and imports, so a shared Excel file can't run formulas. |
| Logs | Passwords, PINs, tokens, keys, cookies and CSRF values are redacted from every log line. |
| Health | `/healthz` reports database, disk space and last backup, with nothing sensitive in it. |
| Containers | The app runs as a non-root user (UID 10001) on a read-only file system with all Linux capabilities dropped. It can't gain new privileges, has memory and process limits, and has health checks. Base images are pinned by digest. |
| Dependencies | Python packages are pinned with hashes and npm packages to exact versions. Every push runs pip-audit, npm audit, Bandit, Semgrep, Gitleaks, Trivy, `check --deploy` and the tests, and any finding fails the build. An SBOM is published with each release. |
| Docs | `SECURITY.md` covers disclosure, contacts and response times. There is a `CHANGELOG.md` with security notes. |

## Phase 2: decisions for Shobhit, in this order

1. **HTTPS by default.** A small web server (Caddy) in front of the app would serve everything over HTTPS. A firm with
   its own domain gets a proper certificate automatically. Without a domain, a private certificate is used, and
   browsers warn about it once per device unless the firm installs it. The alternative is plain HTTP for use on one
   machine only.
2. **Two-step sign-in (MFA)** for everyone, using an authenticator app, with passkeys as an option. Recovery would go
   through an admin, since V1 has no email.
3. **How long people stay signed in.** The brief asks for sign-out after 30 minutes idle and after 8 hours at most.
   Today it's 14 days.
4. **Audit log.** Every sign-in, permission refusal, change, export, Vault download, admin action and backup would be
   recorded, kept for at least a year, and no one could delete it. This is currently on the "Not in V1" list.
5. **Encryption at rest** for the database, Vault files and backups, with keys kept outside the data volume and a
   command to rotate them. The catch is that losing the key means losing the data, so firms must back up their keys.
6. **Field-level permissions:** hiding fields such as fees from some roles. This is currently on the "Not in V1"
   list.
7. **Record-level access:** for example, a deal visible only to its deal team. This is currently on the "Not in V1"
   list. The brief's "user A can't see user B's records" only applies once this exists.
8. **Two admins to change a role.**
9. **Virus scanning of Vault uploads (ClamAV).**

## Where the brief doesn't fit Dealflow as written

- **Vault keys derived from each person's PIN.** The Vault is shared by the whole firm. A file encrypted with one
  person's key couldn't be opened by colleagues, and resetting a forgotten PIN would lose every file that person
  uploaded. Proposal: one firm Vault key, stored outside the data volume and rotatable, with each file encrypted
  separately using AES-256-GCM. The PIN stays as the access gate it is today.
- **Password reset by email.** V1 has no email, so admins reset passwords with a temporary one. We keep the brief's
  rule against revealing whether an account exists, and apply it to sign-in.
- **Two admins for role changes.** A firm with a single admin would be stuck. Option: require a second approval only
  when the firm has two or more active admins.
- **SQLCipher and pg_tde.** The Django SQLCipher backends are thinly maintained, and pg_tde isn't in the standard
  Postgres image. Option: document full-disk encryption on the host for the database, and encrypt Vault files and
  backups inside Dealflow, where we control the code.
- **ClamAV.** It needs about 1 to 2 GB of memory and daily signature downloads, which is heavy for a firm running
  Dealflow on one small server. The brief also says to refuse uploads whenever the scanner is down.
- **Distroless images.** These have no shell, so migrations and the scheduler need a different start-up. We'll use
  slim images, non-root and read-only, pinned by digest, unless a distroless build proves workable.

## Decisions log

| # | Decision | Date |
|---|---|---|
| | *(filled in as Shobhit decides)* | |

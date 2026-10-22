# Incident response

What to do when you think Dealflow, a Dealflow account or the server it runs on has been compromised.
Commands assume the Docker Compose install, run from the folder holding `docker-compose.yml`. Keep a
written timeline (time, who, what) from the first minute: it's the backbone of the review afterwards.

## 1. Prepare (before anything happens)

- At least **two admins**, each with a passkey **and** an authenticator app.
- Stored **offline and apart from the server**: the backup key file (Settings → Backups → Download the
  backup key), the Vault recovery key (Settings → Security), the audit-log passphrase.
- **Hourly backups** on (Settings → Backups), and a copy of the backups folder off the server.
- Settings → Security shows every item green; admins read security alerts in the notification bell.
- Know who decides (the firm's owner of Dealflow), who acts (the admin) and who must be told
  (clients, regulator, under your own obligations: for example India's CERT-In 6-hour reporting rule).

## 2. Identify

Signals: a security alert in the bell (lock-outs, harmful files, failed backups, audit log changed),
a "new sign-in" someone doesn't recognise, an unexplained export or deletion, a report from outside.

- Settings → Security: the checklist, signed-in sessions, failed sign-ins, the audit chain.
- **Audit log:** Settings → Audit log → fetch the period (needs your password and the audit
  passphrase). It lists every request with person, address, action and old → new values.
- Server logs: `docker compose logs --since 24h app scheduler caddy > incident-logs.txt`

Rate it: **critical** (data read or changed by someone who shouldn't, server compromised), **high**
(an account taken over, no evidence of data access), **medium/low** (attempts that failed).

## 3. Contain

Pick what fits; most incidents need the first two.

```sh
# Sign everyone out on every device (also: Settings → Security → Sign everyone else out)
docker compose exec app python manage.py end_all_sessions

# Take Dealflow off the network but keep the evidence (containers stop, volumes stay)
docker compose stop caddy app
```

- **One account:** Settings → Users → deactivate the person; reset their password and two-step login.
- **Vault:** change the Vault key (Settings → Security → Change the Vault key) if a PIN or a session may
  have been used by someone else.
- **Email:** in Settings → Email, remove the app registration; ask people to revoke Dealflow in their
  Google or Microsoft account.

Preserve before you change anything else:

```sh
mkdir -p evidence
docker compose cp app:/data/audit evidence/audit           # still encrypted
docker compose logs --no-color > evidence/container-logs.txt
docker inspect $(docker compose ps -q) > evidence/containers.json
sha256sum -b evidence/* evidence/audit/* > evidence/SHA256SUMS
```

## 4. Eradicate

- Find how they got in (the audit log, the server's own logs, the host). Patch it: update Dealflow
  (`git pull && docker compose up -d --build`, or the newest signed image) and the host.
- **Rotate keys** that could have been read (the server or the keys volume exposed):

```sh
docker compose stop app scheduler
docker compose run --rm app rotate_keys --type=secret     # sessions, sealed secrets; everyone signs in again
docker compose run --rm app rotate_keys --type=database   # SQLite database key
docker compose run --rm app rotate_keys --type=backup     # new backups get a new key
docker compose up -d
```

  Then download the new backup key file, change the Vault key, make a new Vault recovery key, and
  start a new audit-log key if the passphrase may be known (Settings → Audit log).
- Reset passwords and two-step login for affected people; review roles (Settings → Roles) and remove
  anything unexpected.

## 5. Recover

- If data was changed or destroyed: Settings → Backups → Restore from a file (needs your password; on
  a new server, also the backup key file). A safety backup of the current state is taken first.
- On a rebuilt server: install, restore with the backup key file, then Settings → Security → Use a
  recovery key to reopen the Vault.
- Check Settings → Security is green, run "Test the latest backup now", watch the bell for a week.

## 6. Learn

Within two weeks: what happened, when, how it was found, what was exposed, what we changed. Add any
new check to the security checklist or CI. Record it in `CHANGELOG.md` if the product changed.

Contacts and duties to notify are the firm's; keep them at the top of this file in your own copy.

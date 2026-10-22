# Monitoring and alerts

## Built in (no setup)

Admins see these in the **notification bell** and on **Settings → Security**:

| Alert | Raised when |
|---|---|
| Sign-in lock-outs | 5 wrong passwords or codes on an account (or 20 from one address) within 30 minutes |
| New sign-in | Each person is told about a sign-in from a browser not used in 90 days ("It was me" / "Not me") |
| Harmful file refused | The virus scanner refused an upload, or a stored Vault file matched newer signatures |
| Virus scanner not answering | Uploads are paused until it's back; signatures older than 3 days are flagged |
| Backup failed / no backup in a day | The scheduler couldn't write a backup, or none was written |
| Backup restore test failed | The weekly test couldn't restore the newest backup |
| Audit log changed | The nightly chain check found a missing, edited or reordered entry |

The security checklist (Settings → Security) shows the state of every protection with the fix for
anything that isn't right.

## Health checks

- `GET /api/health/` — `{"ok": true}` when the app and its database answer (503 otherwise). Open to
  anyone, reveals nothing else. Used by the container health check.
- The scheduler writes a heartbeat (`/data/scheduler.alive`) every minute; its container is unhealthy
  after 5 minutes without one.
- `docker compose ps` shows every container's health.

## Logs

- `docker compose logs app` — one access-log line per request (gunicorn) and application errors.
  Caddy's access log is JSON (`docker compose logs caddy`). Logs rotate at 10 MB × 5 per container.
- The **audit log** is the record of who did what: every API request with person, address, method,
  path, result, duration and old → new values, sealed and hash-chained. It's read by admins only
  (Settings → Audit log, with the audit passphrase) and exported to Excel.

## Feeding a SIEM

Docker can ship container logs to syslog, journald, Splunk, Fluentd or a cloud logger
(`logging:` in a `docker-compose.override.yml`). Rules worth adding there:

| Rule | Look for |
|---|---|
| Brute force | ≥ 5 `POST /api/auth/login/` answered 400/429 from one address in 5 minutes |
| Admin from a new address | successful sign-in (`POST /api/auth/mfa/verify/` 200) by an admin from an address not seen in 90 days |
| Bulk export | ≥ 3 `GET /api/m/*/export/` or `POST /api/reports/run/` by one person in 1 minute |
| Vault PIN failures | ≥ 3 `POST /api/vault/unlock/` answered 400 in 5 minutes |
| Backup failure | `Daily backup failed` / `Hourly backup failed` in the scheduler log |
| Scanner down | `scanner unavailable` in the app log |

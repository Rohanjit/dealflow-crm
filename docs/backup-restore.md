# Backups and restore

| | |
|---|---|
| **What a backup holds** | Every record (Django `dumpdata`, so it restores on SQLite or Postgres), every uploaded file (Vault files stay encrypted inside), and an encrypted copy of the audit log. |
| **Encryption** | AES-256-GCM in 1 MiB pieces, with a backup key kept in the keys volume, never next to the backups ([key management](key-management.md)). |
| **Schedule** | Daily at the hour you choose; **hourly** on top when switched on (Settings → Backups), keeping the newest 24. Manual backups any time. A "pre-restore" backup is always taken before a restore. |
| **Recovery point (RPO)** | 1 hour with hourly backups on (24 hours with daily only). |
| **Recovery time (RTO)** | Under 1 hour for a typical firm: install, restore from the file, open the Vault with the recovery key. Target ≤ 4 hours including a new server. |
| **Proof it works** | Every week the scheduler restores the newest backup into a separate, temporary database (memory-backed space, separate process, live data untouched), reads back every file's checksum and compares the rows of every kind. Admins can run it any time: Settings → Security → *Test the latest backup now*. A failure shows in the checklist and as a security alert. |

## Keep copies off the server

Backups live in the data volume (`/data/backups`). Copy them somewhere else regularly; they're
encrypted, so any storage will do:

```sh
docker compose cp app:/data/backups ./dealflow-backups-$(date +%F)
```

Without the **backup key file** a backup can't be restored on another server. Download it from
Settings → Backups (needs your password) and keep it offline, apart from the copies.

## Restore

Settings → Backups → *Restore from a file* (admins; needs your password and typing RESTORE). On a new
server also choose the backup key file. Everyone is signed out. Then Settings → Security → *Use a
recovery key* to give the Vault back to its people.

## Evidence of a restore test

`DATA_DIR/restore-test.json` keeps the newest result, for example:

```json
{"at": "2026-10-22T00:18:39+00:00", "backup": "dealflow-manual-20261022-054839.dfb", "ok": true,
 "files": 6, "models": 37, "rows": 275, "seconds": 3.1, "problems": []}
```

The audit log records each run ("Backup restore test passed (…)").

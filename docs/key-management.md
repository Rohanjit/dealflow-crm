# Key management

Dealflow encrypts what it stores and keeps the keys **apart from the data** (decision 63, phase S4).
A copy of the data volume, the database file or a backup is unreadable on its own.

## Where keys live

`DEALFLOW_KEYS_DIR` (Docker: the `dealflow-keys` volume mounted at `/keys`; otherwise a folder next to
the data folder named `<data>-keys`). The folder is owner-only (`0700`), each key file `0600`.
Settings > Security warns when the keys folder sits inside the data folder.

| Key | File | Protects | Made |
|---|---|---|---|
| Django secret | `secret_key` (or `DJANGO_SECRET_KEY` / `DJANGO_SECRET_KEY_FILE`) | sessions; sealing of mailbox tokens, authenticator-app secrets, email app secrets | first start |
| Database key | `db.key` | the SQLite database (SQLCipher 4, AES-256) | first start; a plain database is encrypted in place |
| Master key | `master.key` | files waiting to be emailed (AES-256-GCM); mixed into every Vault PIN key | first use |
| Backup keys | `backups/<id>.key`, `backups/current` | backups (AES-256-GCM, streamed in 1 MiB pieces) | first backup |
| Vault key | never stored as is | Vault files (AES-256-GCM) | first time anyone opens the Vault |

All keys are 32 bytes from the operating system's random source.

## The Vault key

One firm key encrypts every Vault file, so shared documents stay shared. It is stored only **sealed**:

- to each person's X25519 public key. Their private key is sealed with a key from **Argon2id(PIN)**
  (3 passes, 64 MiB) mixed by HKDF with the **master key**. A stolen database alone can't even start
  guessing PINs; with the keys folder too, guessing a 6-digit PIN still costs days per person;
- with the **recovery key** (256 bits, shown once to an admin), for when every PIN is forgotten or the
  server is lost.

While someone's Vault is open, the Vault key sits in their session sealed with a random key that only
their browser holds (an HttpOnly, SameSite=Strict cookie), so neither the database nor the browser alone
has it. Locking the Vault, signing out or 15 idle minutes end it.

A person who sets a PIN gets a key pair; the Vault key is sealed to them the next time anyone who holds
it uses the Vault, or straight away by an admin (Settings > Security > Give access now). Clearing
someone's PIN removes their key pair and access.

## Generating, storing, rotating, destroying

| Key | Rotate | Notes |
|---|---|---|
| Backup | `manage.py rotate_keys --type=backup` | New backups use the new key; older ones still restore (old keys are kept). Download the new key file (Settings > Backups) and store it offline. |
| Database | stop app and scheduler, `manage.py rotate_keys --type=database` | SQLCipher `PRAGMA rekey`; the new key replaces `db.key` only after the re-key succeeds. |
| Django secret | stop app and scheduler, `manage.py rotate_keys --type=secret` | Re-seals authenticator secrets, mailbox tokens and email app secrets; everyone is signed out. |
| Vault | Settings > Security > Change the Vault key (an admin with an open Vault, password) | Every file re-encrypted; everyone with access gets the new key; make a new recovery key. |
| Master | not rotated in place | Changing it would orphan every PIN-sealed key pair: people would get access again through a colleague or the recovery key. |

**Backing up keys.** Keep, offline and apart from the backups: the backup key file (Settings > Backups >
Download the backup key), the Vault recovery key (Settings > Security), and the audit-log passphrase.
Without the backup key file a backup can't be restored on another server.

**Moving to a new server.** Restore the backup with its key file (Restore from a file > Backup key file),
then an admin uses the Vault recovery key (Settings > Security > Use a recovery key).

**Destroying.** Deleting a key file makes what it protects unreadable for good (crypto-shredding). Dealflow
never makes a new key over existing encrypted data: a missing key stops the app with a clear message.

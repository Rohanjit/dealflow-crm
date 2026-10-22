# Data flow

Where data goes and where it is encrypted (decision 63). Solid boxes are containers from
`docker-compose.yml`; dashed are outside services the firm chooses to connect.

```mermaid
flowchart LR
  subgraph Browser["Person's browser"]
    UI["Dealflow app<br/>(no data kept in browser storage<br/>except list column choices)"]
    VC["Vault cookie<br/>(key to the session's Vault key)"]
  end

  subgraph Host["Server (Docker)"]
    Caddy["caddy<br/>TLS 1.2/1.3, HSTS"]
    App["app (gunicorn + Django)<br/>read-only, no capabilities"]
    Sched["scheduler<br/>backups, campaigns, checks"]
    Clam["clamav<br/>virus scanning"]
    DB[("Database<br/>SQLCipher AES-256<br/>or Postgres (disk encryption)")]
    Files[("Vault files<br/>AES-256-GCM, firm Vault key")]
    Mail[("Email attachments waiting<br/>AES-256-GCM, master key")]
    Bk[("Backups<br/>AES-256-GCM, backup key")]
    Audit[("Audit log<br/>sealed to the audit key,<br/>hash-chained")]
    Keys[("Keys volume<br/>secret, db.key, master.key,<br/>backup keys")]
  end

  Google["Gmail API"]:::ext
  MS["Microsoft Graph"]:::ext
  LE["Let's Encrypt"]:::ext
  Other["iCloud / other IMAP + CalDAV<br/>(public addresses only)"]:::ext
  ClamDB["ClamAV signature updates"]:::ext

  UI -- "HTTPS (session + CSRF cookies, __Host-)" --> Caddy
  VC -. "sent only with Vault requests" .-> Caddy
  Caddy -- "HTTP on the compose network" --> App
  App -- "INSTREAM, before anything is stored" --> Clam
  App --> DB
  App --> Files
  App --> Mail
  App --> Audit
  App -. "reads keys" .- Keys
  Sched --> Bk
  Sched --> DB
  Sched -- "OAuth tokens sealed in DB" --> Google
  Sched --> MS
  App -- "send (gmail.send / Mail.Send)" --> Google
  Sched -- "logging: read new mail headers, calendar window" --> Other
  App -- "owner opens an email: fetched live, not kept" --> Other
  App --> MS
  Caddy -. certificates .- LE
  Clam -. updates .- ClamDB

  classDef ext stroke-dasharray: 5 5;
```

| Data | At rest | In transit | Who can read it |
|---|---|---|---|
| Records (investors, contacts, outreach, deals, tasks) | SQLCipher (SQLite) with `db.key`; Postgres: disk/managed encryption | HTTPS | People whose role can view the module and field; restricted deals only their team |
| Vault files | AES-256-GCM per file, firm Vault key sealed to each person's PIN-derived key | HTTPS, `no-store` | People with Vault access and an open Vault (PIN) |
| Uploads in flight | Memory-only `/tmp` until scanned and encrypted | HTTPS | Nobody else |
| Email attachments waiting to send | AES-256-GCM with a key from `master.key` | HTTPS to Google/Microsoft | The sender (and the scheduler for campaigns) |
| Mailbox tokens, app passwords (logging), authenticator secrets | Sealed with a key from the Django secret | — | The app only |
| Logged emails and meetings | Subject, time, participants and attachment names in the database; the email text, meeting notes and attachments are never copied | TLS to the provider (certificates verified, IMAP/CalDAV only to public addresses) | Subject etc.: anyone who can see the record (hidden for restricted deals, addresses hidden by field access); text: only the mailbox owner, fetched live |
| Passwords, recovery codes, PINs | Argon2id hashes | HTTPS | Nobody (one-way) |
| Backups | AES-256-GCM, backup key in the keys volume | Download over HTTPS (admins, password) | Admins with the key file |
| Audit log | Each entry sealed to the audit public key; private key under the audit passphrase | — | Admins who know the passphrase |
| Keys | Keys volume (owner-only files), never in backups or the database | — | The app process |

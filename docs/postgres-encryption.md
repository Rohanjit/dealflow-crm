# Encrypting a Postgres database

On SQLite (the default), Dealflow encrypts the database itself with SQLCipher. On Postgres the database
server owns the files, so encryption at rest is set up there. What Dealflow already encrypts on any
database: Vault files, backups, files waiting to be emailed, mailbox tokens, authenticator secrets and
the audit log.

Pick one (in order of preference):

1. **Managed Postgres with encryption at rest** (AWS RDS / Aurora with KMS, Google Cloud SQL, Azure
   Database for PostgreSQL): on by default or one switch; keys in the cloud's key service.
2. **Encrypted disk under the database** (LUKS on Linux, BitLocker, FileVault, or the cloud provider's
   encrypted volumes): mount the Postgres data directory on it. Simple and covers everything, including
   WAL and temporary files.
3. **Percona pg_tde** (transparent data encryption for PostgreSQL 16/17): table-level encryption inside
   Postgres with keys in a keyring file or Vault/KMIP. Use the Percona distribution image in
   `docker-compose.yml`'s `db` service and follow Percona's pg_tde set-up; keep its keyring outside the
   database volume.

Also: connect over TLS (`?sslmode=require` in `DATABASE_URL`) when the database is on another machine,
never publish the database port, and give Dealflow's database user only the rights on its own database.

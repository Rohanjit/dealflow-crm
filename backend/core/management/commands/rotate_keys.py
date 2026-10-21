"""Change a key (decision 63, S4; docs/key-management.md).

    manage.py rotate_keys --type=backup     new backups use a new key; older ones still restore
    manage.py rotate_keys --type=database   SQLCipher re-keys the SQLite database in place
    manage.py rotate_keys --type=secret     new SECRET_KEY: secrets sealed with the old one (mailbox
                                            tokens, authenticator apps, email app secrets) are
                                            re-sealed; everyone is signed out
The Vault key changes from Settings > Security (it needs an admin's open Vault).
Stop the app and the scheduler first for --type=database and --type=secret."""

import os
import secrets

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError


class Command(BaseCommand):
    help = "Change one of Dealflow's keys."

    def add_arguments(self, parser):
        parser.add_argument("--type", required=True, choices=["backup", "database", "secret"])

    def handle(self, *args, **opts):
        from core import audit
        from dealflow import keys

        kind = opts["type"]
        if kind == "backup":
            key_id, _ = keys.new_backup_key()
            msg = f"New backup key {key_id}. Download it again from Settings > Backups and keep it apart from the backups."
        elif kind == "database":
            if not getattr(settings, "DB_ENCRYPTED", False):
                raise CommandError("The database isn't an encrypted SQLite file (Postgres: see docs/postgres-encryption.md).")
            msg = self._rekey_database(keys)
        else:
            msg = self._new_secret(keys)
        audit.append({"u": {"id": None, "name": "Dealflow (command line)", "email": ""}, "a": f"Changed a key: {kind}"})
        self.stdout.write(self.style.SUCCESS(msg))

    def _rekey_database(self, keys):
        from django.db import connection

        new = secrets.token_bytes(32)
        path = keys.keys_dir() / "db.key"
        staged = keys.keys_dir() / "db.key.new"
        staged.write_text(new.hex())
        staged.chmod(0o600)
        with connection.cursor() as c:
            c.execute(f"PRAGMA rekey = \"x'{new.hex()}'\"")
        os.replace(staged, path)
        return "Database re-keyed. Restart the app and the scheduler."

    def _new_secret(self, keys):
        from cryptography.fernet import InvalidToken  # noqa: F401

        from accounts import mfa
        from accounts.models import TotpDevice
        from mail import crypto as mail_crypto
        from mail.models import MailAccount, MailSettings

        # Open everything with the old key first.
        totp = {d.pk: mfa._unseal(d.secret) for d in TotpDevice.objects.all()}
        tokens = {a.pk: mail_crypto.unseal(a.tokens) for a in MailAccount.objects.all()}
        ms = MailSettings.load()
        g, m = mail_crypto.unseal(ms.google_client_secret), mail_crypto.unseal(ms.microsoft_client_secret)
        path = keys.keys_dir() / "secret_key"
        if not path.exists():
            raise CommandError("SECRET_KEY comes from the environment here; change it there instead.")
        new = secrets.token_urlsafe(50)
        staged = keys.keys_dir() / "secret_key.new"
        staged.write_text(new)
        staged.chmod(0o600)
        settings.SECRET_KEY = new
        for pk, s in totp.items():
            if s:
                TotpDevice.objects.filter(pk=pk).update(secret=mfa._seal(s))
        for pk, t in tokens.items():
            MailAccount.objects.filter(pk=pk).update(tokens=mail_crypto.seal(t) if t else "", needs_reconnect=not t)
        MailSettings.objects.filter(pk=1).update(google_client_secret=mail_crypto.seal(g) if g else "",
                                                 microsoft_client_secret=mail_crypto.seal(m) if m else "")
        os.replace(staged, path)
        from django.contrib.sessions.models import Session

        Session.objects.all().delete()
        return "New SECRET_KEY in place; secrets re-sealed and everyone signed out. Restart the app and the scheduler."

"""Encryption at rest (decision 63, S4): Vault files under the firm's key that PINs unlock, people
waiting for access, recovery key, key change, campaign copies, sealed email uploads, backups,
keys kept apart and the SQLCipher database helper."""

import json
from pathlib import Path

from django.conf import settings
from django.core.files.uploadedfile import SimpleUploadedFile

from accounts.models import Role
from core.tests.base import APITestBase
from vault import crypto
from vault.models import Document, UserVaultKeypair, VaultKey, VaultKeyWrap

PDF = b"%PDF-1.7\n1 0 obj << >> endobj\ntrailer\n%%EOF\n" + b"confidential term sheet" * 20
EVERYTHING = {m: ["view", "create", "edit", "delete"] for m in ("investors", "contacts", "outreach", "deals", "tasks", "vault")}


class EncryptionBase(APITestBase):
    def pin(self, client, pin="4826"):
        r = client.post("/api/vault/pin/", {"pin": pin, "password": self.password}, format="json")
        self.assertEqual(r.status_code, 200, r.content)
        return r.json()

    def upload(self, client, content=PDF, name="IM.pdf"):
        r = client.post("/api/vault/upload/", {"files": [SimpleUploadedFile(name, content)], "data": json.dumps({})},
                        format="multipart")
        self.assertEqual(r.status_code, 201, r.content)
        return r.json()[0]

    def download(self, client, pk, status=200):
        r = client.get(f"/api/vault/{pk}/download/")
        self.assertEqual(r.status_code, status, getattr(r, "content", b"")[:300])
        return b"".join(r.streaming_content) if status == 200 else r.json()


class VaultFileTests(EncryptionBase):
    def test_files_are_encrypted_on_disk_and_open_with_the_pin(self):
        self.pin(self.client)
        doc = self.upload(self.client)
        stored = Path(settings.MEDIA_ROOT, Document.objects.get(pk=doc["id"]).file.name).read_bytes()
        self.assertTrue(stored.startswith(b"DFV1"))
        self.assertNotIn(b"confidential", stored)
        self.assertEqual(self.download(self.client, doc["id"]), PDF)
        # Nothing in the database is the key: only wraps sealed to people.
        self.assertEqual(VaultKey.objects.count(), 1)
        self.assertEqual(VaultKeyWrap.objects.filter(kind="user").count(), 1)
        self.assertNotIn("4826", json.dumps(UserVaultKeypair.objects.get().private_sealed))

    def test_session_without_the_browser_cookie_cant_open_files(self):
        self.pin(self.client)
        doc = self.upload(self.client)
        from vault.keyring import cookie_name

        self.client.cookies.pop(cookie_name())
        self.assertEqual(self.download(self.client, doc["id"], 403)["code"], "vault_locked")

    def test_colleague_waits_then_gets_access(self):
        self.pin(self.client)
        doc = self.upload(self.client)
        anu = self.make_user("anu@firm.com", self.make_role("Team", EVERYTHING))
        c = APIClient_for(anu)
        status = self.pin(c)
        self.assertEqual(status["files"], "waiting")
        self.assertEqual(self.download(c, doc["id"], 403)["code"], "vault_waiting")
        self.assertEqual(self.client.get("/api/m/vault/").status_code, 200)  # the admin uses the Vault: grants
        c.post("/api/vault/unlock/", {"pin": "4826"}, format="json")
        self.assertEqual(self.download(c, doc["id"]), PDF)

    def test_pin_change_keeps_access_and_reset_removes_it(self):
        self.pin(self.client)
        doc = self.upload(self.client)
        r = self.client.post("/api/vault/pin/", {"pin": "7391", "current_pin": "4826"}, format="json")
        self.assertEqual(r.status_code, 200, r.content)
        self.client.post("/api/vault/lock/")
        self.client.post("/api/vault/unlock/", {"pin": "7391"}, format="json")
        self.assertEqual(self.download(self.client, doc["id"]), PDF)
        anu = self.make_user("anu@firm.com", self.make_role("Team", EVERYTHING))
        self.pin(APIClient_for(anu))
        self.client.get("/api/m/vault/")
        self.assertTrue(VaultKeyWrap.objects.filter(user=anu).exists())
        self.client.post(f"/api/users/{anu.pk}/reset-vault-pin/", {}, format="json")
        self.assertFalse(VaultKeyWrap.objects.filter(user=anu).exists())

    def test_files_from_before_encryption_are_encrypted_when_the_vault_opens(self):
        from django.core.files.base import ContentFile

        d = Document(file=ContentFile(PDF, name="old.pdf"), title="Old", file_name="old.pdf", file_type="PDF",
                     file_size=len(PDF), content_type="application/pdf", sha256="ab" * 32,
                     created_by=self.admin, updated_by=self.admin)
        d._ext = "pdf"
        d.save()
        self.pin(self.client)
        d.refresh_from_db()
        self.assertIsNotNone(d.encrypted_with_id)
        self.assertTrue(Path(settings.MEDIA_ROOT, d.file.name).read_bytes().startswith(b"DFV1"))
        self.assertEqual(self.download(self.client, d.pk), PDF)


class RecoveryAndRotationTests(EncryptionBase):
    def test_recovery_key_and_rotation(self):
        self.pin(self.client)
        doc = self.upload(self.client)
        r = self.client.post("/api/vault/keys/recovery-key/", {"password": self.password}, format="json")
        text = r.json()["recovery_key"]
        self.assertEqual(len(text.replace("-", "")), 52)
        # Everyone loses access (e.g. moved to new hardware): the recovery key brings it back.
        VaultKeyWrap.objects.filter(kind="user").delete()
        self.client.post("/api/vault/lock/")
        self.client.post("/api/vault/unlock/", {"pin": "4826"}, format="json")
        self.assertEqual(self.download(self.client, doc["id"], 403)["code"], "vault_waiting")
        bad = self.client.post("/api/vault/keys/recover/", {"recovery_key": "A" * 52, "pin": "4826"}, format="json")
        self.assertEqual(bad.status_code, 400)
        ok = self.client.post("/api/vault/keys/recover/", {"recovery_key": text, "pin": "4826"}, format="json")
        self.assertEqual(ok.status_code, 200, ok.content)
        self.assertEqual(self.download(self.client, doc["id"]), PDF)
        # A new key: files re-encrypted, still readable; the recovery key must be made again.
        before = Path(settings.MEDIA_ROOT, Document.objects.get(pk=doc["id"]).file.name).read_bytes()
        r = self.client.post("/api/vault/keys/rotate/", {"password": self.password}, format="json")
        self.assertEqual(r.json()["files"], 1)
        self.assertFalse(r.json()["has_recovery_key"])
        after = Path(settings.MEDIA_ROOT, Document.objects.get(pk=doc["id"]).file.name).read_bytes()
        self.assertNotEqual(before, after)
        self.assertEqual(self.download(self.client, doc["id"]), PDF)

    def test_keys_screens_are_admin_only(self):
        member = self.make_user("m@firm.com", self.make_role("Team", EVERYTHING))
        self.assertEqual(APIClient_for(member).get("/api/vault/keys/").status_code, 403)
        info = self.client.get("/api/vault/keys/").json()
        self.assertTrue(info["storage"]["separate"])


class CryptoUnitTests(APITestBase):
    def test_tampering_is_caught(self):
        key = b"k" * 32
        blob = crypto.encrypt_file(b"hello", key, b"aad")
        self.assertEqual(crypto.decrypt_file(blob, key, b"aad"), b"hello")
        with self.assertRaises(Exception):
            crypto.decrypt_file(blob[:-1] + bytes([blob[-1] ^ 1]), key, b"aad")
        with self.assertRaises(Exception):
            crypto.decrypt_file(blob, key, b"other file")

    def test_recovery_text_round_trip(self):
        raw = bytes(range(32))
        self.assertEqual(crypto.recovery_bytes(crypto.recovery_text(raw).lower()), raw)


class BackupAndKeysTests(APITestBase):
    def test_backup_key_download_and_import_elsewhere(self):
        from core import backup_crypto, backups
        from dealflow import keys

        path = backups.create_backup("manual")
        r = self.client.post("/api/backups/key/", {"password": self.password}, format="json")
        text = r.content.decode()
        self.assertTrue(text.startswith("dealflow-backup-key:"))
        self.assertEqual(self.client.post("/api/backups/key/", {}, format="json").status_code, 400)
        # Another install: without the key the backup won't open; with it, it does.
        key_id = backup_crypto.read_header(path)["key"]
        Path(settings.KEYS_DIR, "backups", f"{key_id}.key").unlink()
        with self.assertRaises(backup_crypto.BackupKeyError):
            backup_crypto.decrypt(path, Path(settings.DATA_DIR) / "x.zip")
        keys.import_backup_key(text)
        backup_crypto.decrypt(path, Path(settings.DATA_DIR) / "x.zip")

    def test_damaged_or_cut_backups_are_refused(self):
        from core import backup_crypto, backups

        path = backups.create_backup("manual")
        data = path.read_bytes()
        for broken in (data[:-10], data[:-1] + bytes([data[-1] ^ 1])):
            p = Path(settings.DATA_DIR) / "broken.dfb"
            p.write_bytes(broken)
            with self.assertRaises(backup_crypto.BackupKeyError):
                backup_crypto.decrypt(p, Path(settings.DATA_DIR) / "y.zip")

    def test_email_uploads_are_sealed(self):
        r = self.client.post("/api/mail/attachments/", {"files": [SimpleUploadedFile("IM.pdf", PDF)]}, format="multipart")
        token = r.json()[0]["token"]
        stored = Path(settings.DATA_DIR, "mail_uploads", str(self.admin.pk), f"{token}.bin").read_bytes()
        self.assertTrue(stored.startswith(b"DFM1"))
        self.assertNotIn(b"confidential", stored)
        from mail import service

        self.assertEqual(service.read_attachments([{"kind": "upload", "token": token, "name": "IM.pdf",
                                                    "content_type": "application/pdf"}], self.admin)[0][2], PDF)


class DatabaseCipherTests(APITestBase):
    def test_plain_sqlite_file_is_encrypted_in_place(self):
        import sqlite3

        import sqlcipher3.dbapi2 as cipher

        from dealflow.db_cipher.base import encrypt_file, is_plain

        p = Path(settings.DATA_DIR) / "plain.sqlite3"
        con = sqlite3.connect(p)
        con.execute("create table deals(name)")
        con.execute("insert into deals values ('Project Falcon')")
        con.commit()
        con.close()
        key = b"\x01" * 32
        encrypt_file(p, key)
        self.assertFalse(is_plain(p))
        self.assertNotIn(b"Project Falcon", p.read_bytes())
        c = cipher.connect(str(p))
        c.execute(f"PRAGMA key = \"x'{key.hex()}'\"")
        self.assertEqual(c.execute("select name from deals").fetchall(), [("Project Falcon",)])
        wrong = cipher.connect(str(p))
        wrong.execute(f"PRAGMA key = \"x'{('02' * 32)}'\"")
        with self.assertRaises(Exception):
            wrong.execute("select name from deals").fetchall()


def APIClient_for(user):
    from rest_framework.test import APIClient

    c = APIClient()
    c.force_login(user)
    return c


class RotateCommandTests(APITestBase):
    def test_new_secret_reseals_and_new_backup_key(self):
        import pyotp
        from django.core.management import call_command

        from accounts import mfa
        from accounts.models import TotpDevice
        from dealflow import keys

        secret = pyotp.random_base32()
        Path(settings.KEYS_DIR, "secret_key").write_text("old-secret-key-for-test")
        with self.settings(SECRET_KEY="old-secret-key-for-test"):
            TotpDevice.objects.create(user=self.admin, secret=mfa._seal(secret), confirmed=True)
            old_id, _ = keys.backup_key()
            call_command("rotate_keys", type="secret", stdout=open("/dev/null", "w"))
            call_command("rotate_keys", type="backup", stdout=open("/dev/null", "w"))
            self.assertEqual(mfa._unseal(TotpDevice.objects.get().secret), secret)
            self.assertNotEqual(Path(settings.KEYS_DIR, "secret_key").read_text(), "old-secret-key-for-test")
            new_id, _ = keys.backup_key()
            self.assertNotEqual(old_id, new_id)
            keys.backup_key(old_id)  # older backups still restore

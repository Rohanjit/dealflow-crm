import shutil
import tempfile
from pathlib import Path

from django.test import TestCase, override_settings
from rest_framework.test import APIClient

from accounts.models import Role, RoleModulePermission, User
from core.models import ChoiceOption, FirmSettings


class DataDirMixin:
    """Point backups/imports/files at a throwaway folder for each test class."""

    @classmethod
    def setUpClass(cls):
        cls._tmp = Path(tempfile.mkdtemp())
        cls._keys = Path(tempfile.mkdtemp(suffix="-keys"))  # apart from the data, as in production
        cls._override = override_settings(
            DATA_DIR=cls._tmp, BACKUP_DIR=cls._tmp / "backups", IMPORT_DIR=cls._tmp / "imports",
            MEDIA_ROOT=cls._tmp / "files", KEYS_DIR=cls._keys,
        )
        cls._override.enable()
        super().setUpClass()

    @classmethod
    def tearDownClass(cls):
        super().tearDownClass()
        cls._override.disable()
        shutil.rmtree(cls._tmp, ignore_errors=True)
        shutil.rmtree(cls._keys, ignore_errors=True)


class APITestBase(DataDirMixin, TestCase):
    password = "Str0ng-pass-123"

    def setUp(self):
        from core import stuck

        stuck.forget()  # stage-time figures held by this process belong to the previous test
        self.admin_role = Role.objects.get(is_admin=True)
        self.admin = User.objects.create_user("admin@firm.com", self.password, full_name="Asha Admin", role=self.admin_role)
        fs = FirmSettings.load()
        fs.firm_name = "Test Capital"
        fs.save()
        self.client = APIClient()
        self.client.force_authenticate(self.admin)

    def make_role(self, name, perms=None, **flags):
        role = Role.objects.create(name=name, **flags)
        for module, actions in (perms or {}).items():
            RoleModulePermission.objects.create(role=role, module=module, **{f"can_{a}": True for a in actions})
        return role

    def make_user(self, email, role, **kw):
        return User.objects.create_user(email, self.password, full_name=kw.pop("full_name", email.split("@")[0]), role=role, **kw)

    def client_for(self, user):
        c = APIClient()
        c.force_authenticate(user)
        return c

    def opt(self, list_key, label):
        return ChoiceOption.objects.get(choice_list__key=list_key, label=label)

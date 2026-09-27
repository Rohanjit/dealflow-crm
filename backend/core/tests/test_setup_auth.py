from django.test import TestCase
from rest_framework.test import APIClient

from accounts.models import Invite, Role, User
from core.models import FirmSettings

from .base import APITestBase, DataDirMixin


class SetupWizardTests(DataDirMixin, TestCase):
    def test_setup_creates_admin_once(self):
        c = APIClient()
        r = c.get("/api/app/")
        self.assertTrue(r.json()["needs_setup"])
        r = c.post("/api/setup/", {"firm_name": "Acme Advisors", "full_name": "Shobhit", "email": "S@Acme.com",
                                     "password": "Str0ng-pass-123"}, format="json")
        self.assertEqual(r.status_code, 201, r.content)
        self.assertTrue(r.json()["role"]["is_admin"])
        self.assertEqual(FirmSettings.load().firm_name, "Acme Advisors")
        self.assertEqual(User.objects.get().email, "s@acme.com")
        # Signed in straight away
        self.assertEqual(c.get("/api/app/").json()["user"]["email"], "s@acme.com")
        # Second attempt is refused
        r = APIClient().post("/api/setup/", {"firm_name": "X", "full_name": "Y", "email": "y@x.com",
                                               "password": "Str0ng-pass-123"}, format="json")
        self.assertEqual(r.status_code, 403)

    def test_setup_validates(self):
        r = APIClient().post("/api/setup/", {"firm_name": "", "full_name": "", "email": "bad", "password": "123"}, format="json")
        self.assertEqual(r.status_code, 400)
        self.assertEqual(set(r.json()), {"firm_name", "full_name", "email", "password"})


class AuthTests(APITestBase):
    def test_login_logout(self):
        c = APIClient()
        r = c.post("/api/auth/login/", {"email": "ADMIN@firm.com", "password": self.password}, format="json")
        self.assertEqual(r.status_code, 200, r.content)
        self.assertEqual(c.get("/api/auth/me/").status_code, 200)
        c.post("/api/auth/logout/")
        self.assertIn(c.get("/api/auth/me/").status_code, (401, 403))

    def test_wrong_password(self):
        r = APIClient().post("/api/auth/login/", {"email": "admin@firm.com", "password": "nope"}, format="json")
        self.assertEqual(r.status_code, 400)

    def test_inactive_user_cannot_log_in(self):
        u = self.make_user("gone@firm.com", self.admin_role, is_active=False)
        r = APIClient().post("/api/auth/login/", {"email": u.email, "password": self.password}, format="json")
        self.assertEqual(r.status_code, 400)

    def test_temp_password_forces_change(self):
        member = Role.objects.get(name="Member")
        r = self.client.post("/api/users/", {"full_name": "Ravi", "email": "ravi@firm.com", "role": member.pk}, format="json")
        self.assertEqual(r.status_code, 201, r.content)
        temp = r.json()["temp_password"]
        c = APIClient()
        c.post("/api/auth/login/", {"email": "ravi@firm.com", "password": temp}, format="json")
        r = c.get("/api/m/investors/")
        self.assertEqual(r.status_code, 403)
        self.assertEqual(r.json()["code"], "password_change_required")
        r = c.post("/api/auth/change-password/", {"current_password": temp, "new_password": "An0ther-good-one"}, format="json")
        self.assertEqual(r.status_code, 200, r.content)
        self.assertEqual(c.get("/api/m/investors/").status_code, 200)

    def test_admin_reset_password(self):
        u = self.make_user("ravi@firm.com", Role.objects.get(name="Member"))
        r = self.client.post(f"/api/users/{u.pk}/reset-password/", {}, format="json")
        temp = r.json()["temp_password"]
        u.refresh_from_db()
        self.assertTrue(u.must_change_password and u.check_password(temp))

    def test_preferences(self):
        r = self.client.patch("/api/auth/me/", {"date_format": "YYYY-MM-DD", "number_grouping": "international"}, format="json")
        self.assertEqual(r.json()["date_format"], "YYYY-MM-DD")
        r = self.client.patch("/api/auth/me/", {"date_format": "whatever"}, format="json")
        self.assertEqual(r.status_code, 400)


class UserManagementTests(APITestBase):
    def test_last_admin_protected(self):
        r = self.client.patch(f"/api/users/{self.admin.pk}/", {"role": Role.objects.get(name="Member").pk}, format="json")
        self.assertEqual(r.status_code, 400)
        r = self.client.patch(f"/api/users/{self.admin.pk}/", {"is_active": False}, format="json")
        self.assertEqual(r.status_code, 400)

    def test_user_manager_cannot_touch_admins(self):
        mgr_role = self.make_role("Ops", manage_users=True)
        mgr = self.make_user("ops@firm.com", mgr_role)
        c = self.client_for(mgr)
        r = c.post("/api/users/", {"full_name": "X", "email": "x@firm.com", "role": self.admin_role.pk}, format="json")
        self.assertEqual(r.status_code, 403)
        r = c.patch(f"/api/users/{mgr.pk}/", {"role": self.admin_role.pk}, format="json")
        self.assertEqual(r.status_code, 403)
        r = c.post(f"/api/users/{self.admin.pk}/reset-password/", {}, format="json")
        self.assertEqual(r.status_code, 403)
        # but can manage ordinary users
        r = c.post("/api/users/", {"full_name": "X", "email": "x@firm.com", "role": Role.objects.get(name="Member").pk}, format="json")
        self.assertEqual(r.status_code, 201)

    def test_users_endpoint_needs_flag(self):
        u = self.make_user("m@firm.com", Role.objects.get(name="Member"))
        self.assertEqual(self.client_for(u).get("/api/users/").status_code, 403)
        self.assertEqual(self.client_for(u).get("/api/users/lookup/").status_code, 200)


class RoleTests(APITestBase):
    def test_create_and_edit_role(self):
        r = self.client.post("/api/roles/", {"name": "Analyst", "perms": {"investors": {"edit": True}},
                                             "flags": {"restore_archive": True}}, format="json")
        self.assertEqual(r.status_code, 201, r.content)
        body = r.json()
        # edit implies view
        self.assertEqual(body["perms"]["investors"], {"view": True, "create": False, "edit": True, "delete": False})
        self.assertTrue(body["flags"]["restore_archive"])
        r = self.client.patch(f"/api/roles/{body['id']}/", {"perms": {"investors": {"view": False}}}, format="json")
        self.assertFalse(r.json()["perms"]["investors"]["view"])

    def test_admin_role_locked(self):
        self.assertEqual(self.client.patch(f"/api/roles/{self.admin_role.pk}/", {"name": "X"}, format="json").status_code, 400)
        self.assertEqual(self.client.delete(f"/api/roles/{self.admin_role.pk}/").status_code, 400)

    def test_role_in_use_cannot_be_deleted(self):
        role = self.make_role("Temp")
        self.make_user("t@firm.com", role)
        self.assertEqual(self.client.delete(f"/api/roles/{role.pk}/").status_code, 400)

    def test_duplicate_role_name(self):
        r = self.client.post("/api/roles/", {"name": "member"}, format="json")
        self.assertEqual(r.status_code, 400)


class InviteTests(APITestBase):
    def test_invite_flow(self):
        member = Role.objects.get(name="Member")
        r = self.client.post("/api/invites/", {"role": member.pk, "note": "For Priya"}, format="json")
        token = r.json()["token"]
        anon = APIClient()
        self.assertEqual(anon.get(f"/api/invites/accept/{token}/").json()["role"], "Member")
        r = anon.post(f"/api/invites/accept/{token}/", {"full_name": "Priya", "email": "priya@firm.com",
                                                          "password": "Str0ng-pass-123"}, format="json")
        self.assertEqual(r.status_code, 201, r.content)
        self.assertEqual(User.objects.get(email="priya@firm.com").role, member)
        # one-time
        r = APIClient().post(f"/api/invites/accept/{token}/", {"full_name": "Z", "email": "z@firm.com",
                                                                 "password": "Str0ng-pass-123"}, format="json")
        self.assertEqual(r.status_code, 400)

    def test_revoked_invite(self):
        r = self.client.post("/api/invites/", {"role": Role.objects.get(name="Member").pk}, format="json")
        self.client.delete(f"/api/invites/{r.json()['id']}/")
        self.assertEqual(APIClient().get(f"/api/invites/accept/{r.json()['token']}/").status_code, 400)
        self.assertFalse(Invite.objects.get().is_usable)

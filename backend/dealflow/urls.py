from django.conf import settings
from django.http import HttpResponse, JsonResponse
from django.urls import include, path, re_path
from django.views.decorators.csrf import ensure_csrf_cookie

from accounts import views as a
from core import guest as g
from core import views as c
from deals import views as d
from tasks import views as t
from vault import views as v

api = [
    path("app/", a.AppView.as_view()),
    path("setup/", a.SetupView.as_view()),
    path("guest/start/", g.GuestStartView.as_view()),
    path("guest/leave/", g.GuestLeaveView.as_view()),
    path("auth/login/", a.LoginView.as_view()),
    path("auth/logout/", a.LogoutView.as_view()),
    path("auth/me/", a.MeView.as_view()),
    path("auth/change-password/", a.ChangePasswordView.as_view()),
    path("users/", a.UserListView.as_view()),
    path("users/lookup/", a.UserLookupView.as_view()),
    path("users/<int:pk>/", a.UserDetailView.as_view()),
    path("users/<int:pk>/reset-password/", a.UserResetPasswordView.as_view()),
    path("roles/", a.RoleListView.as_view()),
    path("roles/<int:pk>/", a.RoleDetailView.as_view()),
    path("invites/", a.InviteListView.as_view()),
    path("invites/<int:pk>/", a.InviteRevokeView.as_view()),
    path("invites/accept/<str:token>/", a.InviteAcceptView.as_view()),
    path("schema/", c.SchemaView.as_view()),
    path("search/", c.SearchView.as_view()),
    path("lookup/<str:module>/", c.LookupView.as_view()),
    path("m/<str:module>/", c.RecordListView.as_view()),
    path("m/<str:module>/export/", c.RecordExportView.as_view()),
    path("m/<str:module>/bulk/", c.BulkView.as_view()),
    path("m/<str:module>/merge/", c.MergeView.as_view()),
    path("m/<str:module>/duplicates/", c.DuplicatesView.as_view()),
    path("m/<str:module>/import/upload/", c.ImportUploadView.as_view()),
    path("m/<str:module>/import/check/", c.ImportCheckView.as_view()),
    path("m/<str:module>/import/commit/", c.ImportCommitView.as_view()),
    path("m/<str:module>/<int:pk>/", c.RecordDetailView.as_view()),
    path("m/<str:module>/<int:pk>/notes/", c.NotesView.as_view()),
    path("notes/<int:pk>/", c.NoteDetailView.as_view()),
    path("archive/", c.ArchiveListView.as_view()),
    path("archive/<str:module>/<int:pk>/", c.ArchiveItemView.as_view()),
    path("settings/firm/", c.FirmSettingsView.as_view()),
    path("settings/fields/", c.FieldSwitchView.as_view()),
    path("choice-lists/", c.ChoiceListsView.as_view()),
    path("choice-lists/<slug:key>/options/", c.ChoiceOptionCreateView.as_view()),
    path("choice-lists/<slug:key>/reorder/", c.ChoiceListReorderView.as_view()),
    path("choice-options/<int:pk>/", c.ChoiceOptionView.as_view()),
    path("backups/", c.BackupListView.as_view()),
    path("backups/restore/", c.BackupRestoreView.as_view()),
    path("backups/<str:name>/download/", c.BackupDownloadView.as_view()),
    path("dashboard/", d.DashboardView.as_view()),
    path("deals/<int:pk>/matches/", d.MatchesView.as_view()),
    path("deals/<int:pk>/tracker/add/", d.TrackerAddView.as_view()),
    path("deals/<int:pk>/access/", d.DealAccessView.as_view()),
    path("deals/<int:pk>/access/ask/", d.DealAccessAskView.as_view()),
    path("access-requests/", d.AccessRequestsView.as_view()),
    path("access-requests/<int:pk>/", d.AccessRequestDecideView.as_view()),
    path("tasks/alerts/", t.TaskAlertsView.as_view()),
    path("stale/", c.StaleView.as_view()),
    path("stale/<str:module>/<int:pk>/", c.StaleActionView.as_view()),
    path("vault/status/", v.VaultStatusView.as_view()),
    path("vault/pin/", v.VaultPinView.as_view()),
    path("vault/unlock/", v.VaultUnlockView.as_view()),
    path("vault/lock/", v.VaultLockView.as_view()),
    path("vault/upload/", v.VaultUploadView.as_view()),
    path("vault/<int:pk>/download/", v.VaultDownloadView.as_view()),
    path("users/<int:pk>/reset-vault-pin/", v.UserVaultPinResetView.as_view()),
    path("health/", c.HealthView.as_view()),
]


def api_not_found(request, rest=""):
    return JsonResponse({"detail": "Not found."}, status=404)


@ensure_csrf_cookie
def spa(request, rest=""):
    """Serve the React app for every non-API path; React Router takes it from there."""
    index = settings.FRONTEND_DIST / "index.html"
    if not index.exists():
        return HttpResponse(
            "Frontend not built. Run `npm run build` in frontend/, or use the Vite dev server.",
            content_type="text/plain",
            status=503,
        )
    return HttpResponse(index.read_text(), content_type="text/html")


urlpatterns = [
    path("api/", include(api)),
    re_path(r"^api/(?P<rest>.*)$", api_not_found),
    re_path(r"^(?P<rest>.*)$", spa),
]

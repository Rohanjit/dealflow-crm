from django.conf import settings
from django.http import HttpResponse, JsonResponse
from django.urls import include, path, re_path
from django.views.decorators.csrf import ensure_csrf_cookie

from accounts import views as a
from core import audit_views as av
from core import guest as g
from core import insight_views as iv
from mail import views as m
from reports import views as rv
from core import layout_views as lv
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
    path("auth/session/", a.SessionView.as_view()),
    path("settings/security/", a.SecuritySettingsView.as_view()),
    path("notifications/cleared/", a.ClearedAlertsView.as_view()),
    path("mail/settings/", m.MailSettingsView.as_view()),
    path("mail/account/", m.MailAccountView.as_view()),
    path("mail/connect/<str:provider>/", m.MailConnectView.as_view()),
    path("mail/callback/<str:provider>/", m.mail_callback),
    path("mail/attachments/", m.AttachmentUploadView.as_view()),
    path("mail/recipients/", m.RecipientSuggestionsView.as_view()),
    path("mail/send/", m.SendView.as_view()),
    path("mail/templates/", m.TemplateListView.as_view()),
    path("mail/templates/<int:pk>/", m.TemplateDetailView.as_view()),
    path("mail/campaigns/", m.CampaignListView.as_view()),
    path("mail/campaigns/<int:pk>/", m.CampaignDetailView.as_view()),
    path("mail/campaigns/<int:pk>/people/", m.CampaignRecipientsView.as_view()),
    path("mail/campaigns/<int:pk>/preview/", m.CampaignPreviewView.as_view()),
    path("mail/campaigns/<int:pk>/test/", m.CampaignTestView.as_view()),
    path("mail/campaigns/<int:pk>/<str:action>/", m.CampaignActionView.as_view()),
    path("reports/", rv.SavedReportListView.as_view()),
    path("reports/run/", rv.ReportRunView.as_view()),
    path("reports/<int:pk>/", rv.SavedReportDetailView.as_view()),
    path("reports/<int:pk>/run/", rv.SavedReportRunView.as_view()),
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
    path("settings/layouts/", lv.LayoutListView.as_view()),
    path("settings/layouts/<str:module>/", lv.LayoutDetailView.as_view()),
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
    path("insights/alerts/", iv.InsightAlertsView.as_view()),
    path("insights/alerts/snooze/", iv.InsightSnoozeView.as_view()),
    path("insights/stages/", iv.StuckRulesView.as_view()),
    path("insights/analytics/", iv.AnalyticsView.as_view()),
    path("settings/alerts/", iv.AlertSettingsView.as_view()),
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
    path("audit/", av.AuditStatusView.as_view()),
    path("audit/passphrase/", av.AuditPassphraseView.as_view()),
    path("audit/new-key/", av.AuditNewKeyView.as_view()),
    path("audit/settings/", av.AuditSettingsView.as_view()),
    path("audit/fetch/", av.AuditFetchView.as_view()),
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

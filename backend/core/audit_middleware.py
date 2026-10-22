"""Writes one audit log entry (core/audit.py) for every API request: when, who, from where, what
they did in plain words, what it changed (old and new values), the request and its result.
Secrets in request bodies (passwords, PINs, passphrases, tokens) are never written."""

from __future__ import annotations

import json
import time

from django.urls import resolve
from django.urls.exceptions import Resolver404

from accounts.lockout import client_ip

from . import audit

SECRET_WORDS = ("password", "pin", "passphrase", "token", "secret")
SKIP = ("/api/health/",)  # machine health checks, not people
MAX_BODY = 64 * 1024

# (view class, method) → what the person did. {module}, {singular} and {title} are filled in.
ACTIONS = {
    ("AppView", "GET"): "Opened Dealflow",
    ("SchemaView", "GET"): "Loaded the app's screens",
    ("SetupView", "POST"): "Set up Dealflow",
    ("LoginView", "POST"): "Signed in",
    ("LogoutView", "POST"): "Signed out",
    ("MeView", "GET"): "Checked who's signed in",
    ("MeView", "PATCH"): "Changed their own preferences",
    ("ChangePasswordView", "POST"): "Changed their password",
    ("GuestStartView", "POST"): "Started looking around as a guest",
    ("GuestLeaveView", "POST"): "Left the sample to set up for real",
    ("UserListView", "GET"): "Viewed users",
    ("UserListView", "POST"): "Added a user",
    ("UserDetailView", "PATCH"): "Changed a user",
    ("UserResetPasswordView", "POST"): "Reset a user's password",
    ("UserLookupView", "GET"): "Looked up people's names",
    ("RoleListView", "GET"): "Viewed roles",
    ("RoleListView", "POST"): "Added a role",
    ("RoleDetailView", "PATCH"): "Changed a role",
    ("RoleDetailView", "DELETE"): "Deleted a role",
    ("InviteListView", "GET"): "Viewed invite links",
    ("InviteListView", "POST"): "Made an invite link",
    ("InviteRevokeView", "DELETE"): "Cancelled an invite link",
    ("InviteAcceptView", "GET"): "Opened an invite link",
    ("InviteAcceptView", "POST"): "Joined through an invite link",
    ("SearchView", "GET"): "Searched",
    ("LookupView", "GET"): "Looked up {module}",
    ("RecordListView", "GET"): "Viewed the {module} list",
    ("RecordListView", "POST"): "Added {singular_a}",
    ("RecordDetailView", "GET"): "Opened {singular_the}",
    ("RecordDetailView", "PATCH"): "Edited {singular_the}",
    ("RecordDetailView", "DELETE"): "Deleted {singular_the}",
    ("RecordExportView", "GET"): "Exported {module}",
    ("BulkView", "POST"): "Changed several {module} at once",
    ("MergeView", "POST"): "Merged {module}",
    ("DuplicatesView", "GET"): "Looked for duplicate {module}",
    ("ImportUploadView", "POST"): "Uploaded a file to import into {module}",
    ("ImportCheckView", "POST"): "Checked an import into {module}",
    ("ImportCommitView", "POST"): "Imported into {module}",
    ("NotesView", "GET"): "Read the log of {singular_the}",
    ("NotesView", "POST"): "Added to the log of {singular_the}",
    ("NoteDetailView", "PATCH"): "Edited a log entry",
    ("NoteDetailView", "DELETE"): "Deleted a log entry",
    ("ArchiveListView", "GET"): "Viewed deleted items",
    ("ArchiveItemView", "POST"): "Restored {singular_a} from deleted items",
    ("ArchiveItemView", "DELETE"): "Deleted {singular_a} permanently",
    ("FirmSettingsView", "GET"): "Viewed firm settings",
    ("FirmSettingsView", "PATCH"): "Changed firm settings",
    ("FieldSwitchView", "GET"): "Viewed field settings",
    ("FieldSwitchView", "PATCH"): "Switched a field on or off",
    ("ChoiceListsView", "GET"): "Loaded dropdown lists",
    ("ChoiceOptionCreateView", "POST"): "Added a dropdown option",
    ("ChoiceListReorderView", "POST"): "Reordered a dropdown list",
    ("ChoiceOptionView", "PATCH"): "Changed a dropdown option",
    ("BackupListView", "GET"): "Viewed backups",
    ("BackupListView", "POST"): "Took a backup",
    ("BackupRestoreView", "POST"): "Restored a backup",
    ("BackupDownloadView", "GET"): "Downloaded a backup",
    ("DashboardView", "GET"): "Viewed the home page",
    ("MatchesView", "GET"): "Viewed suggested investors",
    ("TrackerAddView", "POST"): "Added investors to a deal's tracker",
    ("TaskAlertsView", "GET"): "Checked reminders",
    ("StaleView", "GET"): "Checked relationships going cold",
    ("InsightAlertsView", "GET"): "Checked stuck deals and follow-ups",
    ("MfaVerifyView", "POST"): "Entered a two-step code",
    ("PasskeyOptionsView", "POST"): "Started signing in with a passkey",
    ("PasskeySignInView", "POST"): "Signed in with a passkey",
    ("MfaStatusView", "GET"): "Checked their two-step login",
    ("TotpStartView", "POST"): "Started setting up an authenticator app",
    ("TotpConfirmView", "POST"): "Confirmed an authenticator app",
    ("TotpRemoveView", "POST"): "Removed their authenticator app",
    ("PasskeyRegisterOptionsView", "POST"): "Started adding a passkey",
    ("PasskeyListView", "POST"): "Added a passkey",
    ("PasskeyDetailView", "PATCH"): "Renamed a passkey",
    ("PasskeyDetailView", "DELETE"): "Removed a passkey",
    ("RecoveryCodesView", "POST"): "Made new recovery codes",
    ("SessionsView", "GET"): "Viewed their signed-in devices",
    ("SessionEndView", "DELETE"): "Signed out one of their devices",
    ("SessionEndOthersView", "POST"): "Signed out everywhere else",
    ("SecurityAlertsView", "GET"): "Checked new-device sign-ins",
    ("UserResetMfaView", "POST"): "Reset someone's two-step login",
    ("BackupKeyView", "POST"): "Downloaded the backup key",
    ("VaultKeysView", "GET"): "Viewed Vault encryption",
    ("VaultGrantView", "POST"): "Gave people access to Vault files",
    ("VaultRecoveryKeyView", "POST"): "Made a Vault recovery key",
    ("VaultRecoverView", "POST"): "Used the Vault recovery key",
    ("VaultRotateView", "POST"): "Changed the Vault key",
    ("ScannerStatusView", "GET"): "Viewed the virus scanner",
    ("SyncView", "GET"): "Viewed email and calendar logging",
    ("SyncView", "PUT"): "Changed meeting reminders",
    ("SyncConnectView", "POST"): "Connected email and calendar logging",
    ("SyncConnectionView", "PATCH"): "Changed email and calendar logging",
    ("SyncConnectionView", "DELETE"): "Disconnected email and calendar logging",
    ("SyncNowView", "POST"): "Checked mailbox and calendar now",
    ("ItemView", "GET"): "Opened a logged email or meeting",
    ("ItemSaveView", "POST"): "Saved an email attachment to the Vault",
    ("ItemFileView", "POST"): "Picked the deal for an email or meeting",
    ("AgendaView", "GET"): "Checked upcoming meetings",
    ("MeetingsView", "GET"): "Viewed upcoming meetings",
    ("ScheduleView", "POST"): "Scheduled a meeting",
    ("SyncOverviewView", "GET"): "Viewed who has connected logging",
    ("SuggestionsView", "GET"): "Viewed people they've emailed",
    ("SuggestionDismissView", "POST"): "Set aside a suggested contact",
    ("SuggestionPrefillView", "GET"): "Read a signature to add a contact",
    ("CheckView", "POST"): "Checked a captured contact",
    ("ParseView", "POST"): "Read details from pasted text",
    ("AddFirmView", "POST"): "Added a firm while capturing a contact",
    ("ScanRefusalAlertsView", "GET"): "Checked refused files",
    ("AdminSecurityAlertsView", "GET"): "Checked security alerts",
    ("SecurityOverviewView", "GET"): "Viewed the security overview",
    ("AuditChainCheckView", "POST"): "Checked the audit log chain",
    ("RestoreTestView", "POST"): "Tested that the latest backup restores",
    ("SignOutEveryoneView", "POST"): "Signed everyone else out",
    ("SessionView", "GET"): "Checked their session",
    ("SessionView", "POST"): "Chose to stay signed in",
    ("SecuritySettingsView", "GET"): "Viewed security settings",
    ("SecuritySettingsView", "PUT"): "Changed security settings",
    ("ClearedAlertsView", "GET"): "Checked cleared notifications",
    ("ClearedAlertsView", "PUT"): "Cleared notifications",
    ("SavedReportListView", "GET"): "Viewed saved reports",
    ("SavedReportListView", "POST"): "Saved a report",
    ("ReportRunView", "POST"): "Exported a report",
    ("SavedReportDetailView", "PATCH"): "Changed a saved report",
    ("SavedReportDetailView", "DELETE"): "Deleted a saved report",
    ("SavedReportRunView", "POST"): "Exported a saved report",
    ("MailSettingsView", "GET"): "Viewed email settings",
    ("MailSettingsView", "PUT"): "Changed email settings",
    ("MailAccountView", "GET"): "Checked their mailbox",
    ("MailAccountView", "DELETE"): "Disconnected their mailbox",
    ("MailConnectView", "POST"): "Started connecting a mailbox",
    ("mail_callback", "GET"): "Finished connecting a mailbox",
    ("AttachmentUploadView", "POST"): "Attached a file to an email",
    ("RecipientSuggestionsView", "GET"): "Opened the email form",
    ("SendView", "POST"): "Sent an email",
    ("TemplateListView", "GET"): "Viewed email templates",
    ("TemplateListView", "POST"): "Saved an email template",
    ("TemplateDetailView", "PATCH"): "Changed an email template",
    ("TemplateDetailView", "DELETE"): "Deleted an email template",
    ("CampaignListView", "GET"): "Viewed campaigns",
    ("CampaignListView", "POST"): "Started a campaign draft",
    ("CampaignDetailView", "GET"): "Opened a campaign",
    ("CampaignDetailView", "PATCH"): "Changed a campaign",
    ("CampaignDetailView", "DELETE"): "Deleted a campaign",
    ("CampaignActionView", "POST"): "Started, paused, resumed or cancelled a campaign",
    ("CampaignRecipientsView", "GET"): "Viewed a campaign's people",
    ("CampaignRecipientsView", "DELETE"): "Took people off a campaign",
    ("CampaignPreviewView", "GET"): "Previewed a campaign",
    ("CampaignTestView", "POST"): "Sent a campaign test to themselves",
    ("InsightSnoozeView", "POST"): "Snoozed a reminder",
    ("StuckRulesView", "GET"): "Checked when deals count as stuck",
    ("AnalyticsView", "GET"): "Viewed insights",
    ("AlertSettingsView", "GET"): "Viewed alert settings",
    ("AlertSettingsView", "PUT"): "Changed alert settings",
    ("StaleActionView", "POST"): "Updated a relationship going cold",
    ("VaultStatusView", "GET"): "Checked the Vault",
    ("VaultPinView", "POST"): "Set or changed their Vault PIN",
    ("VaultUnlockView", "POST"): "Opened the Vault",
    ("VaultLockView", "POST"): "Locked the Vault",
    ("VaultUploadView", "POST"): "Uploaded documents",
    ("VaultDownloadView", "GET"): "Downloaded a document",
    ("UserVaultPinResetView", "POST"): "Cleared a user's Vault PIN",
    ("DealAccessView", "GET"): "Viewed who can see a deal",
    ("DealAccessView", "POST"): "Changed who can see a deal",
    ("DealAccessAskView", "POST"): "Asked for access to a restricted deal",
    ("AccessRequestsView", "GET"): "Checked access requests",
    ("AccessRequestDecideView", "POST"): "Decided an access request",
    ("RoomView", "GET"): "Viewed a deal's data room",
    ("RoomView", "PATCH"): "Changed a deal's data room",
    ("RoomFilesView", "POST"): "Added a file to a data room",
    ("RoomFileView", "PATCH"): "Changed a data room file",
    ("RoomFileView", "DELETE"): "Removed a file from a data room",
    ("RoomSharesView", "POST"): "Shared a data room with an investor",
    ("RoomShareView", "PATCH"): "Changed an investor's data room link",
    ("RoomShareLinkView", "GET"): "Copied an investor's data room link",
    ("QuestionAnswerView", "POST"): "Answered an investor's data room question",
    ("RoomTraceView", "POST"): "Checked whose copy a file was",
    ("RoomAlertsView", "GET"): "Checked investors' questions",
    ("AuditStatusView", "GET"): "Viewed the audit log settings",
    ("AuditPassphraseView", "POST"): "Set or changed the audit passphrase",
    ("AuditNewKeyView", "POST"): "Started a new audit log key",
    ("AuditSettingsView", "PATCH"): "Changed how long the audit log is kept",
    ("AuditFetchView", "POST"): "Fetched the audit log",
}


def _mask(value, depth=0):
    if isinstance(value, dict):
        return {k: ("***" if any(w in str(k).lower() for w in SECRET_WORDS) else _mask(v, depth + 1)) for k, v in value.items()}
    if isinstance(value, list):
        return [_mask(v, depth + 1) for v in value[:200]]
    return value


def _body(request):
    if request.method in ("GET", "HEAD", "OPTIONS"):
        return None
    ctype = request.META.get("CONTENT_TYPE", "")
    try:
        size = int(request.META.get("CONTENT_LENGTH") or 0)
    except ValueError:
        size = 0
    if "json" in ctype and 0 < size <= MAX_BODY:
        try:
            return _mask(json.loads(request.body))
        except (ValueError, UnicodeDecodeError):
            return {"unreadable": True}
    if "multipart" in ctype:
        return {"upload": f"{size:,} bytes"}
    return None


def _action(request, match, status):
    view = getattr(match.func, "view_class", None)
    name = view.__name__ if view else match.func.__name__
    text = ACTIONS.get((name, request.method))
    if text is None:
        return f"{request.method} {request.path}"
    kw = match.kwargs
    module, singular = kw.get("module", ""), ""
    if module:
        from .registry import get_module

        try:
            m = get_module(module)
            module, singular = m.label, m.singular.lower()
        except KeyError:
            pass
    subject = getattr(request, "_df_subject", None)
    title = f" {subject['title']}" if subject else (f" #{kw['pk']}" if "pk" in kw else "")
    if name == "LoginView" and status >= 400:
        text = "Tried to sign in (refused)"
    return text.format(module=module.lower() if module else "records", singular_a=(f"{'an' if singular[:1] in 'aeiou' else 'a'} {singular}" if singular else "a record"),
                       singular_the=f"{singular or 'record'}{title}")


class AuditMiddleware:
    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        if not request.path.startswith("/api/") or request.path.startswith(SKIP):
            return self.get_response(request)
        started = time.monotonic()
        body = _body(request)  # read before the view so the body is still available
        response = self.get_response(request)
        try:
            match = resolve(request.path_info)
        except Resolver404:
            match = None
        user = getattr(request, "user", None)
        signed_in = user is not None and getattr(user, "is_authenticated", False)
        entry = {
            "u": {"id": user.pk, "name": user.full_name, "email": user.email} if signed_in else None,
            "ip": client_ip(request),
            "m": request.method,
            "path": request.path,
            "q": request.META.get("QUERY_STRING", "")[:2000],
            "s": response.status_code,
            "ms": round((time.monotonic() - started) * 1000),
            "a": _action(request, match, response.status_code) if match else f"{request.method} {request.path}",
        }
        if fwd := request.META.get("HTTP_X_FORWARDED_FOR"):
            entry["fwd"] = fwd[:200]
            entry["peer"] = request.META.get("REMOTE_ADDR", "")
        if body is not None:
            entry["b"] = body
        if changes := request.__dict__.get("_df_changes"):
            entry["ch"] = changes
        if notes := request.__dict__.get("_df_notes"):
            entry["n"] = notes
        if subject := getattr(request, "_df_subject", None):
            entry["r"] = subject["record"]
        audit.append(entry)
        return response

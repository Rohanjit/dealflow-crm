"""Django settings for Dealflow.

Everything that differs between installs comes from environment variables so the
same Docker image runs on a laptop (SQLite) or a firm server (Postgres).
"""

import os
import sys
import secrets
from pathlib import Path

import dj_database_url

BASE_DIR = Path(__file__).resolve().parent.parent

# All persistent state (database, Vault files, backups, secret key) lives here.
DATA_DIR = Path(os.environ.get("DEALFLOW_DATA_DIR", BASE_DIR / "data"))
DATA_DIR.mkdir(parents=True, exist_ok=True)
MEDIA_ROOT = DATA_DIR / "files"
BACKUP_DIR = DATA_DIR / "backups"
IMPORT_DIR = DATA_DIR / "imports"
# Audit log (core/audit.py): encrypted, append-only files. Never touched by restores or the guest sample.
AUDIT_DIR = Path(os.environ.get("DEALFLOW_AUDIT_DIR", DATA_DIR / "audit"))
if len(sys.argv) > 1 and sys.argv[1] == "test":
    import tempfile

    import atexit
    import shutil

    AUDIT_DIR = Path(tempfile.mkdtemp(prefix="dealflow-audit-test-"))
    atexit.register(shutil.rmtree, AUDIT_DIR, True)


def _secret_key() -> str:
    """Use DJANGO_SECRET_KEY if given, else generate one once and keep it in the data volume."""
    env_key = os.environ.get("DJANGO_SECRET_KEY")
    if env_key:
        return env_key
    key_file = DATA_DIR / "secret_key"
    if key_file.exists():
        return key_file.read_text().strip()
    key = secrets.token_urlsafe(50)
    key_file.write_text(key)
    try:
        key_file.chmod(0o600)
    except OSError:
        pass
    return key


SECRET_KEY = _secret_key()
# A public demo site: sample data only, "Continue as guest" on the sign-in page, set-up refused,
# data reset every day (core/guest.py).
DEMO = os.environ.get("DEALFLOW_DEMO", "0") == "1"
DEBUG = os.environ.get("DJANGO_DEBUG", "0") == "1"
# The names people reach this install by. Anything else is refused (Host-header attacks). Set
# DJANGO_ALLOWED_HOSTS=crm.yourfirm.com (comma-separated) for a server.
ALLOWED_HOSTS = [h.strip() for h in os.environ.get("DJANGO_ALLOWED_HOSTS", "localhost,127.0.0.1,[::1]").split(",") if h.strip()]
CSRF_TRUSTED_ORIGINS = [
    o.strip() for o in os.environ.get("DJANGO_CSRF_TRUSTED_ORIGINS", "").split(",") if o.strip()
]

INSTALLED_APPS = [
    "django.contrib.auth",
    "django.contrib.contenttypes",
    "django.contrib.sessions",
    "django.contrib.staticfiles",
    "rest_framework",
    "django_filters",
    "accounts",
    "core",
    "investors",
    "contacts",
    "outreach",
    "deals",
    "tasks",
    "vault",
    "reports",
    "mail",
]

MIDDLEWARE = [
    "django.middleware.security.SecurityMiddleware",
    "dealflow.security.SecurityHeadersMiddleware",
    "whitenoise.middleware.WhiteNoiseMiddleware",
    "django.contrib.sessions.middleware.SessionMiddleware",
    "django.middleware.common.CommonMiddleware",
    "dealflow.middleware.StripNulMiddleware",
    "django.middleware.csrf.CsrfViewMiddleware",
    "django.contrib.auth.middleware.AuthenticationMiddleware",
    "accounts.sessions.SessionTimeoutMiddleware",
    "core.current.CurrentRequestMiddleware",
    "core.audit_middleware.AuditMiddleware",
    "django.middleware.clickjacking.XFrameOptionsMiddleware",
]

ROOT_URLCONF = "dealflow.urls"

# The built React app (frontend/dist) is copied here in the Docker image.
FRONTEND_DIST = Path(os.environ.get("DEALFLOW_FRONTEND_DIST", BASE_DIR.parent / "frontend" / "dist"))

TEMPLATES = [
    {
        "BACKEND": "django.template.backends.django.DjangoTemplates",
        "DIRS": [FRONTEND_DIST],
        "APP_DIRS": True,
        "OPTIONS": {"context_processors": ["django.template.context_processors.request"]},
    }
]

WSGI_APPLICATION = "dealflow.wsgi.application"

DATABASES = {
    "default": dj_database_url.config(
        default=f"sqlite:///{DATA_DIR / 'dealflow.sqlite3'}",
        conn_max_age=600,
    )
}
if DATABASES["default"]["ENGINE"] == "django.db.backends.sqlite3":
    DATABASES["default"]["OPTIONS"] = {"timeout": 20}
    DATABASES["default"]["CONN_MAX_AGE"] = 0

AUTH_USER_MODEL = "accounts.User"
# Argon2id only (decision 63). There are no installs with older hashes to carry over.
PASSWORD_HASHERS = ["django.contrib.auth.hashers.Argon2PasswordHasher"]
AUTH_PASSWORD_VALIDATORS = [
    {"NAME": "django.contrib.auth.password_validation.MinimumLengthValidator", "OPTIONS": {"min_length": 12}},
    {"NAME": "accounts.validators.MaximumLengthValidator"},
    {"NAME": "django.contrib.auth.password_validation.UserAttributeSimilarityValidator",
     "OPTIONS": {"user_attributes": ["email", "full_name"]}},
    {"NAME": "django.contrib.auth.password_validation.CommonPasswordValidator"},
    {"NAME": "django.contrib.auth.password_validation.NumericPasswordValidator"},
    {"NAME": "accounts.validators.PasswordHistoryValidator"},
]
# Two-step login for everyone (decision 63). Only the test suite turns it off, for tests that are
# about something else; tests of sign-in turn it back on.
MFA_REQUIRED = True
if len(sys.argv) > 1 and sys.argv[1] == "test":
    MFA_REQUIRED = False
    # Same algorithm, lighter settings, so the test suite doesn't spend minutes hashing.
    PASSWORD_HASHERS = ["accounts.validators.TestArgon2Hasher"]

LANGUAGE_CODE = "en"
TIME_ZONE = os.environ.get("TZ", "Asia/Kolkata")
USE_I18N = False
USE_TZ = True

STATIC_URL = "/static/"
STATIC_ROOT = None  # no Django static files; the built SPA is served via WHITENOISE_ROOT
STATICFILES_DIRS = []
WHITENOISE_ROOT = FRONTEND_DIST if FRONTEND_DIST.exists() else None
WHITENOISE_AUTOREFRESH = DEBUG  # pick up rebuilt frontend files without a restart while developing
STORAGES = {
    "default": {"BACKEND": "django.core.files.storage.FileSystemStorage"},
    "staticfiles": {"BACKEND": "django.contrib.staticfiles.storage.StaticFilesStorage"},
}

DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"

# Sessions (decision 63): signed out after the firm's idle time (Settings > Security, default 30
# minutes) and after 12 hours whatever happens (accounts/sessions.py), and when the browser closes.
SESSION_ENGINE = "django.contrib.sessions.backends.db"
SESSION_ABSOLUTE_SECONDS = 12 * 60 * 60
SESSION_COOKIE_AGE = SESSION_ABSOLUTE_SECONDS
SESSION_EXPIRE_AT_BROWSER_CLOSE = True
SESSION_COOKIE_HTTPONLY = True
SESSION_COOKIE_SAMESITE = "Lax"
CSRF_COOKIE_SAMESITE = "Lax"
SECURE_CONTENT_TYPE_NOSNIFF = True
SECURE_REFERRER_POLICY = "same-origin"
SECURE_CROSS_ORIGIN_OPENER_POLICY = "same-origin"
X_FRAME_OPTIONS = "DENY"

# HTTPS. DEALFLOW_HTTPS=1 when people reach the install over https (the bundled Caddy proxy sets
# it): cookies only travel over https and carry the __Host- prefix, plain http is redirected,
# and browsers are told to use https for a year (HSTS). DEALFLOW_BEHIND_PROXY=1 trusts the
# proxy's X-Forwarded-Proto; never set it when the app is reachable without the proxy.
HTTPS = os.environ.get("DEALFLOW_HTTPS", os.environ.get("DEALFLOW_SECURE_COOKIES", "0")) == "1"
if HTTPS:
    SESSION_COOKIE_SECURE = CSRF_COOKIE_SECURE = True
    SESSION_COOKIE_NAME = "__Host-dealflow_session"
    CSRF_COOKIE_NAME = "__Host-csrftoken"
    SECURE_SSL_REDIRECT = os.environ.get("DEALFLOW_SSL_REDIRECT", "1") == "1"
    SECURE_REDIRECT_EXEMPT = [r"^api/health/$"]
    SECURE_HSTS_SECONDS = int(os.environ.get("DEALFLOW_HSTS_SECONDS", str(365 * 24 * 60 * 60)))
    SECURE_HSTS_INCLUDE_SUBDOMAINS = os.environ.get("DEALFLOW_HSTS_SUBDOMAINS", "1") == "1"
    # Preloading commits the whole domain to https in browsers for good: the firm's choice.
    # Off by default on purpose: anyone may submit a domain that sends "preload" to the browsers'
    # list, which would force https on every subdomain of the firm (intranet sites included).
    SECURE_HSTS_PRELOAD = os.environ.get("DEALFLOW_HSTS_PRELOAD", "0") == "1"
    if not SECURE_HSTS_PRELOAD:
        SILENCED_SYSTEM_CHECKS = ["security.W021"]
else:
    SESSION_COOKIE_SECURE = CSRF_COOKIE_SECURE = False
if os.environ.get("DEALFLOW_BEHIND_PROXY", "0") == "1":
    SECURE_PROXY_SSL_HEADER = ("HTTP_X_FORWARDED_PROTO", "https")

# Plain request bodies (JSON, forms) up to 5 MB; files are streamed to disk and checked per feature.
DATA_UPLOAD_MAX_MEMORY_SIZE = 5 * 1024 * 1024
VAULT_MAX_FILE_MB = int(os.environ.get("DEALFLOW_VAULT_MAX_FILE_MB", "50"))
FILE_UPLOAD_MAX_MEMORY_SIZE = 5 * 1024 * 1024

REST_FRAMEWORK = {
    "DEFAULT_AUTHENTICATION_CLASSES": ["rest_framework.authentication.SessionAuthentication"],
    "DEFAULT_PERMISSION_CLASSES": ["accounts.permissions.IsActiveUser"],
    "DEFAULT_PAGINATION_CLASS": "core.pagination.StandardPagination",
    "PAGE_SIZE": 50,
    "DEFAULT_RENDERER_CLASSES": ["rest_framework.renderers.JSONRenderer"],
    "DEFAULT_PARSER_CLASSES": [
        "dealflow.parsers.ObjectJSONParser",
        "rest_framework.parsers.FormParser",
        "rest_framework.parsers.MultiPartParser",
    ],
    "EXCEPTION_HANDLER": "core.errors.handler",
}

LOGGING = {
    "version": 1,
    "disable_existing_loggers": False,
    "handlers": {"console": {"class": "logging.StreamHandler"}},
    "root": {"handlers": ["console"], "level": os.environ.get("LOG_LEVEL", "INFO")},
    "loggers": {"django.request": {"level": "ERROR"}},
}

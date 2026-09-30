"""Django settings for Dealflow.

Everything that differs between installs comes from environment variables so the
same Docker image runs on a laptop (SQLite) or a firm server (Postgres).
"""

import os
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
DEBUG = os.environ.get("DJANGO_DEBUG", "0") == "1"
ALLOWED_HOSTS = [h.strip() for h in os.environ.get("DJANGO_ALLOWED_HOSTS", "*").split(",") if h.strip()]
if "*" not in ALLOWED_HOSTS:
    ALLOWED_HOSTS += ["localhost", "127.0.0.1"]  # the container's own health check
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
]

MIDDLEWARE = [
    "django.middleware.security.SecurityMiddleware",
    "dealflow.security.SecurityHeadersMiddleware",
    "dealflow.security.RequestSizeMiddleware",
    "whitenoise.middleware.WhiteNoiseMiddleware",
    "django.contrib.sessions.middleware.SessionMiddleware",
    "django.middleware.common.CommonMiddleware",
    "django.middleware.csrf.CsrfViewMiddleware",
    "dealflow.security.CsrfTokenHeaderMiddleware",
    "django.contrib.auth.middleware.AuthenticationMiddleware",
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
# New passwords (and Vault PINs) are hashed with Argon2id. PBKDF2 stays second in the list only so
# passwords saved before the switch can still be checked; Django re-hashes them with Argon2id at
# the next successful sign-in.
PASSWORD_HASHERS = [
    "django.contrib.auth.hashers.Argon2PasswordHasher",
    "django.contrib.auth.hashers.PBKDF2PasswordHasher",
]
AUTH_PASSWORD_VALIDATORS = [
    {
        "NAME": "django.contrib.auth.password_validation.UserAttributeSimilarityValidator",
        "OPTIONS": {"user_attributes": ["email", "full_name"]},
    },
    {"NAME": "django.contrib.auth.password_validation.MinimumLengthValidator", "OPTIONS": {"min_length": 12}},
    {"NAME": "django.contrib.auth.password_validation.CommonPasswordValidator"},
    {"NAME": "django.contrib.auth.password_validation.NumericPasswordValidator"},
    {"NAME": "accounts.validators.PasswordHistoryValidator", "OPTIONS": {"count": 12}},
]

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

# ---------------------------------------------------------------- browser and transport security
# DEALFLOW_HTTPS=1 when Dealflow is reached over HTTPS through a reverse proxy that terminates TLS:
# secure-only cookies, HSTS, redirects from plain HTTP, and trust in the proxy's X-Forwarded-Proto.
HTTPS = os.environ.get("DEALFLOW_HTTPS", os.environ.get("DEALFLOW_SECURE_COOKIES", "0")) == "1"

SESSION_COOKIE_AGE = 60 * 60 * 24 * 14
SESSION_COOKIE_HTTPONLY = True
SESSION_COOKIE_SAMESITE = "Lax"
CSRF_COOKIE_SAMESITE = "Lax"
CSRF_COOKIE_HTTPONLY = True  # the app gets the token from a response header (dealflow/security.py)
SESSION_COOKIE_SECURE = CSRF_COOKIE_SECURE = HTTPS
if HTTPS:
    # __Host- cookies are refused by browsers unless secure, host-only and site-wide.
    SESSION_COOKIE_NAME = "__Host-sessionid"
    CSRF_COOKIE_NAME = "__Host-csrftoken"
    SECURE_SSL_REDIRECT = True
    SECURE_REDIRECT_EXEMPT = [r"^healthz$"]  # the container health check calls it over plain HTTP
    SECURE_PROXY_SSL_HEADER = ("HTTP_X_FORWARDED_PROTO", "https")
    SECURE_HSTS_SECONDS = 60 * 60 * 24 * 365
    SECURE_HSTS_INCLUDE_SUBDOMAINS = True
    SECURE_HSTS_PRELOAD = True
SECURE_CONTENT_TYPE_NOSNIFF = True
# Origin only, never the path: invite links must not leak into anyone's logs through Referer.
# (no-referrer would make browsers send "Origin: null" on changes, which the CSRF check refuses.)
SECURE_REFERRER_POLICY = "strict-origin"
SECURE_CROSS_ORIGIN_OPENER_POLICY = "same-origin"
X_FRAME_OPTIONS = "DENY"

# ---------------------------------------------------------------- request and upload sizes
# JSON bodies stay small; files are limited per endpoint by dealflow.security.RequestSizeMiddleware
# before anything is read. Uploads above 5 MB spool to the data volume, not the container's /tmp.
DATA_UPLOAD_MAX_MEMORY_SIZE = 5 * 1024 * 1024
DATA_UPLOAD_MAX_NUMBER_FIELDS = 1000
FILE_UPLOAD_MAX_MEMORY_SIZE = 5 * 1024 * 1024
FILE_UPLOAD_TEMP_DIR = DATA_DIR / "tmp"
FILE_UPLOAD_TEMP_DIR.mkdir(parents=True, exist_ok=True)
FILE_UPLOAD_PERMISSIONS = 0o640
VAULT_MAX_FILE_MB = int(os.environ.get("DEALFLOW_VAULT_MAX_FILE_MB", "50"))
VAULT_MAX_UPLOAD_MB = int(os.environ.get("DEALFLOW_VAULT_MAX_UPLOAD_MB", "1024"))  # all files in one upload
IMPORT_MAX_FILE_MB = int(os.environ.get("DEALFLOW_IMPORT_MAX_FILE_MB", "20"))
RESTORE_MAX_MB = int(os.environ.get("DEALFLOW_RESTORE_MAX_MB", "4096"))

REST_FRAMEWORK = {
    "DEFAULT_AUTHENTICATION_CLASSES": ["rest_framework.authentication.SessionAuthentication"],
    "DEFAULT_PERMISSION_CLASSES": ["accounts.permissions.IsActiveUser"],
    "DEFAULT_PAGINATION_CLASS": "core.pagination.StandardPagination",
    "PAGE_SIZE": 50,
    "DEFAULT_RENDERER_CLASSES": ["rest_framework.renderers.JSONRenderer"],
    "EXCEPTION_HANDLER": "rest_framework.views.exception_handler",
    # Proxies in front of Dealflow, so per-IP limits see the client's address, not the proxy's.
    "NUM_PROXIES": int(os.environ.get("DEALFLOW_PROXY_COUNT", "1" if HTTPS else "0")),
}

LOGGING = {
    "version": 1,
    "disable_existing_loggers": False,
    "filters": {"redact": {"()": "dealflow.redaction.RedactingFilter"}},
    "handlers": {"console": {"class": "logging.StreamHandler", "filters": ["redact"]}},
    "root": {"handlers": ["console"], "level": os.environ.get("LOG_LEVEL", "INFO")},
    "loggers": {"django.request": {"level": "ERROR"}},
}

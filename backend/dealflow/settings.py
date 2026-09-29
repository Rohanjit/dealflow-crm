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
]

MIDDLEWARE = [
    "django.middleware.security.SecurityMiddleware",
    "whitenoise.middleware.WhiteNoiseMiddleware",
    "django.contrib.sessions.middleware.SessionMiddleware",
    "django.middleware.common.CommonMiddleware",
    "django.middleware.csrf.CsrfViewMiddleware",
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
AUTH_PASSWORD_VALIDATORS = [
    {"NAME": "django.contrib.auth.password_validation.MinimumLengthValidator", "OPTIONS": {"min_length": 8}},
    {"NAME": "django.contrib.auth.password_validation.CommonPasswordValidator"},
    {"NAME": "django.contrib.auth.password_validation.NumericPasswordValidator"},
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

SESSION_COOKIE_AGE = 60 * 60 * 24 * 14
SESSION_COOKIE_HTTPONLY = True
SESSION_COOKIE_SAMESITE = "Lax"
CSRF_COOKIE_SAMESITE = "Lax"
# Firms may run behind their own HTTPS proxy; turn these on there.
SESSION_COOKIE_SECURE = os.environ.get("DEALFLOW_SECURE_COOKIES", "0") == "1"
CSRF_COOKIE_SECURE = SESSION_COOKIE_SECURE

DATA_UPLOAD_MAX_MEMORY_SIZE = 50 * 1024 * 1024
FILE_UPLOAD_MAX_MEMORY_SIZE = 10 * 1024 * 1024

REST_FRAMEWORK = {
    "DEFAULT_AUTHENTICATION_CLASSES": ["rest_framework.authentication.SessionAuthentication"],
    "DEFAULT_PERMISSION_CLASSES": ["accounts.permissions.IsActiveUser"],
    "DEFAULT_PAGINATION_CLASS": "core.pagination.StandardPagination",
    "PAGE_SIZE": 50,
    "DEFAULT_RENDERER_CLASSES": ["rest_framework.renderers.JSONRenderer"],
    "EXCEPTION_HANDLER": "rest_framework.views.exception_handler",
}

LOGGING = {
    "version": 1,
    "disable_existing_loggers": False,
    "handlers": {"console": {"class": "logging.StreamHandler"}},
    "root": {"handlers": ["console"], "level": os.environ.get("LOG_LEVEL", "INFO")},
    "loggers": {"django.request": {"level": "ERROR"}},
}

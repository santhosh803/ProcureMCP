"""
Django settings for the procuremcp project.

Configuration is environment-driven: secrets and connection strings are loaded
from a local .env file in development and from the platform environment in
production. Nothing sensitive is hardcoded here.
"""

import os
import sys
from pathlib import Path

import dj_database_url
from dotenv import load_dotenv

BASE_DIR = Path(__file__).resolve().parent.parent

# True while running the test suite; used to disable external side effects
# (embedding enqueue, notifications) that should not fire during tests.
TESTING = "test" in sys.argv

# Load environment variables from a local .env file when present.
load_dotenv(BASE_DIR / ".env")


def _materialize_gcp_credentials():
    """On platforms without a mounted key file, write GCP_CREDENTIALS_JSON to disk.

    Railway (and similar) provide the service-account JSON as an env var rather
    than a file. If present, write it to a temp file and point
    GOOGLE_APPLICATION_CREDENTIALS at it so the Google client can authenticate.
    """
    raw = os.environ.get("GCP_CREDENTIALS_JSON")
    if not raw:
        return
    import tempfile

    fd, path = tempfile.mkstemp(prefix="gcp-", suffix=".json")
    with os.fdopen(fd, "w") as fh:
        fh.write(raw)
    os.environ["GOOGLE_APPLICATION_CREDENTIALS"] = path


_materialize_gcp_credentials()


def env_bool(name: str, default: bool = False) -> bool:
    value = os.environ.get(name)
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}


# --- Core security -----------------------------------------------------------

SECRET_KEY = os.environ.get("DJANGO_SECRET_KEY", "dev-insecure-change-me")

# In production platforms (Railway) DEBUG is forced off.
IS_PRODUCTION = bool(os.environ.get("RAILWAY_ENVIRONMENT"))
DEBUG = False if IS_PRODUCTION else env_bool("DEBUG", True)

ALLOWED_HOSTS = [
    h.strip()
    for h in os.environ.get("ALLOWED_HOSTS", "localhost,127.0.0.1").split(",")
    if h.strip()
]
if IS_PRODUCTION:
    ALLOWED_HOSTS.append(".railway.app")

CSRF_TRUSTED_ORIGINS = [
    o.strip()
    for o in os.environ.get("CSRF_TRUSTED_ORIGINS", "").split(",")
    if o.strip()
]
if IS_PRODUCTION:
    CSRF_TRUSTED_ORIGINS.append("https://*.railway.app")


# --- Applications ------------------------------------------------------------

INSTALLED_APPS = [
    "django.contrib.admin",
    "django.contrib.auth",
    "django.contrib.contenttypes",
    "django.contrib.sessions",
    "django.contrib.messages",
    "django.contrib.staticfiles",
    # Third-party
    "rest_framework",
    "corsheaders",
    "django_filters",
    "drf_spectacular",
    "pgvector.django",
    # Local
    "procurement",
]

MIDDLEWARE = [
    "django.middleware.security.SecurityMiddleware",
    "whitenoise.middleware.WhiteNoiseMiddleware",
    "corsheaders.middleware.CorsMiddleware",
    "django.contrib.sessions.middleware.SessionMiddleware",
    "django.middleware.common.CommonMiddleware",
    "django.middleware.csrf.CsrfViewMiddleware",
    "django.contrib.auth.middleware.AuthenticationMiddleware",
    "django.contrib.messages.middleware.MessageMiddleware",
    "django.middleware.clickjacking.XFrameOptionsMiddleware",
]

ROOT_URLCONF = "procuremcp.urls"

TEMPLATES = [
    {
        "BACKEND": "django.template.backends.django.DjangoTemplates",
        "DIRS": [BASE_DIR / "frontend_minimal" / "templates"],
        "APP_DIRS": True,
        "OPTIONS": {
            "context_processors": [
                "django.template.context_processors.request",
                "django.contrib.auth.context_processors.auth",
                "django.contrib.messages.context_processors.messages",
            ],
        },
    },
]

WSGI_APPLICATION = "procuremcp.wsgi.application"
ASGI_APPLICATION = "procuremcp.asgi.application"


# --- Database (Neon PostgreSQL + pgvector) -----------------------------------

DATABASE_URL = os.environ.get("DATABASE_URL")
if DATABASE_URL:
    DATABASES = {
        "default": dj_database_url.parse(
            DATABASE_URL,
            conn_max_age=600,
            ssl_require=True,
        )
    }
else:
    # Fallback for tooling that runs without a configured database.
    DATABASES = {
        "default": {
            "ENGINE": "django.db.backends.sqlite3",
            "NAME": BASE_DIR / "db.sqlite3",
        }
    }


# --- Password validation -----------------------------------------------------

AUTH_PASSWORD_VALIDATORS = [
    {"NAME": "django.contrib.auth.password_validation.UserAttributeSimilarityValidator"},
    {"NAME": "django.contrib.auth.password_validation.MinimumLengthValidator"},
    {"NAME": "django.contrib.auth.password_validation.CommonPasswordValidator"},
    {"NAME": "django.contrib.auth.password_validation.NumericPasswordValidator"},
]


# --- Internationalization ----------------------------------------------------

LANGUAGE_CODE = "en-us"
TIME_ZONE = "UTC"
USE_I18N = True
USE_TZ = True


# --- Static files ------------------------------------------------------------

STATIC_URL = "static/"
STATIC_ROOT = BASE_DIR / "staticfiles"
STATICFILES_DIRS = [BASE_DIR / "frontend_minimal" / "static"]
STORAGES = {
    "default": {"BACKEND": "django.core.files.storage.FileSystemStorage"},
    "staticfiles": {
        "BACKEND": "whitenoise.storage.CompressedManifestStaticFilesStorage"
    },
}

DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"


# --- Django REST Framework ---------------------------------------------------

REST_FRAMEWORK = {
    "DEFAULT_FILTER_BACKENDS": [
        "django_filters.rest_framework.DjangoFilterBackend",
        "rest_framework.filters.OrderingFilter",
        "rest_framework.filters.SearchFilter",
    ],
    "DEFAULT_PAGINATION_CLASS": "rest_framework.pagination.PageNumberPagination",
    "PAGE_SIZE": 25,
    "DEFAULT_SCHEMA_CLASS": "drf_spectacular.openapi.AutoSchema",
}

SPECTACULAR_SETTINGS = {
    "TITLE": "ProcureMCP API",
    "DESCRIPTION": "Enterprise procurement platform — Procure-to-Pay REST API.",
    "VERSION": "1.0.0",
    "SERVE_INCLUDE_SCHEMA": False,
    "ENUM_NAME_OVERRIDES": {
        "VendorStatusEnum": "procurement.models.VendorMaster.Status",
        "PurchaseOrderStatusEnum": "procurement.models.PurchaseOrder.Status",
        "PurchaseRequisitionStatusEnum": "procurement.models.PurchaseRequisition.Status",
        "InvoiceMatchStatusEnum": "procurement.models.Invoice.MatchStatus",
        "GoodsReceiptQualityStatusEnum": "procurement.models.GoodsReceipt.QualityStatus",
        "ApprovalDecisionEnum": "procurement.models.ApprovalRequest.Decision",
        "ApproverTierEnum": "procurement.models.ApprovalRequest.ApproverTier",
        "PolicyTypeEnum": "procurement.models.PolicyDocument.PolicyType",
    },
}


# --- CORS --------------------------------------------------------------------

CORS_ALLOW_ALL_ORIGINS = not IS_PRODUCTION
CORS_ALLOWED_ORIGINS = [
    o.strip()
    for o in os.environ.get("CORS_ALLOWED_ORIGINS", "").split(",")
    if o.strip()
]


# --- Async / broker (Celery on shared Upstash Redis) -------------------------

REDIS_URL = os.environ.get("REDIS_URL")

# CELERY_* settings are read by the Celery app via the "CELERY" namespace.
# The mandatory global_keyprefix that isolates this project on the shared Redis
# instance is set directly in procuremcp/celery.py.
CELERY_BROKER_URL = REDIS_URL
CELERY_RESULT_BACKEND = REDIS_URL
CELERY_TASK_SERIALIZER = "json"
CELERY_RESULT_SERIALIZER = "json"
CELERY_ACCEPT_CONTENT = ["json"]
CELERY_TIMEZONE = "UTC"
CELERY_TASK_ALWAYS_EAGER = env_bool("CELERY_TASK_ALWAYS_EAGER", False)
CELERY_TASK_EAGER_PROPAGATES = True
# Upstash rediss:// endpoints negotiate TLS; relax cert verification to match
# the connection string's ssl_cert_reqs=CERT_NONE.
CELERY_BROKER_USE_SSL = {"ssl_cert_reqs": "CERT_NONE"} if (REDIS_URL or "").startswith("rediss://") else None
CELERY_REDIS_BACKEND_USE_SSL = CELERY_BROKER_USE_SSL


# --- Google Vertex AI (used from Phase 4 onward) -----------------------------

GOOGLE_CLOUD_PROJECT = os.environ.get("GOOGLE_CLOUD_PROJECT")
GOOGLE_CLOUD_LOCATION = os.environ.get("GOOGLE_CLOUD_LOCATION", "us-central1")
EMBEDDING_MODEL = os.environ.get("EMBEDDING_MODEL", "text-embedding-004")
EMBEDDING_DIMENSIONS = 768


# --- Production hardening -----------------------------------------------------

if IS_PRODUCTION:
    SECURE_PROXY_SSL_HEADER = ("HTTP_X_FORWARDED_PROTO", "https")
    SECURE_SSL_REDIRECT = True
    SESSION_COOKIE_SECURE = True
    CSRF_COOKIE_SECURE = True
    SECURE_HSTS_SECONDS = 31536000
    SECURE_HSTS_INCLUDE_SUBDOMAINS = True
    SECURE_HSTS_PRELOAD = True
    SECURE_CONTENT_TYPE_NOSNIFF = True

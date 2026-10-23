"""Django settings for PMS project."""

import sys
from pathlib import Path

from celery.schedules import schedule
from cryptography.fernet import Fernet
from decouple import config
from dotenv import load_dotenv

load_dotenv()

BASE_DIR = Path(__file__).resolve().parent.parent
SECRET_KEY = config("SECRET_KEY")
ALLOWED_HOSTS = ["*"]

DEBUG = False
if len(sys.argv) > 1 and sys.argv[1] == "runserver":
    DEBUG = True

INSTALLED_APPS = [
    "csp",
    "django_crontab",
    "core",
    "django.contrib.admin",
    "django.contrib.auth",
    "django.contrib.contenttypes",
    "django.contrib.sessions",
    "django.contrib.messages",
    "django.contrib.staticfiles",
]

MIDDLEWARE = [
    "django.middleware.security.SecurityMiddleware",
    "django.contrib.sessions.middleware.SessionMiddleware",
    "django.middleware.common.CommonMiddleware",
    "django.middleware.csrf.CsrfViewMiddleware",
    "csp.middleware.CSPMiddleware",  # ✅ ADDED CSP MIDDLEWARE
    "django.contrib.auth.middleware.AuthenticationMiddleware",
    "django.contrib.messages.middleware.MessageMiddleware",
    "django.middleware.clickjacking.XFrameOptionsMiddleware",
]

CONTENT_SECURITY_POLICY = {
    "DIRECTIVES": {
        "base-uri": ["'self'"],
        "connect-src": ["'self'"],
        "default-src": ["'self'"],
        "font-src": ["'self'", "data:"],
        "frame-src": ["'self'"],
        "img-src": ["'self'", "data:"],
        "object-src": ["'none'"],
        "script-src": ["'self'"],
        "style-src": ["'self'"],
    },
    "REPORT_ONLY": True,
}

PRINTER_IP = config("PRINTER_IP")
ROOT_URLCONF = "PMS.urls"

TEMPLATES = [
    {
        "BACKEND": "django.template.backends.django.DjangoTemplates",
        "DIRS": [],
        "APP_DIRS": True,
        "OPTIONS": {
            "context_processors": [
                "django.template.context_processors.request",
                "django.contrib.auth.context_processors.auth",
                "django.contrib.messages.context_processors.messages",
                "core.context_processors.employee_name",
                "core.context_processors.user_role",
                "core.context_processors.plant_code",
                "core.context_processors.show_sidebar",
                "core.context_processors.nav_modules",
            ],
        },
    },
]
DATABASES = {
    "default": {
        "ENGINE": "django.db.backends.dummy",  # special dummy backend — no real DB
    }
}

SESSION_ENGINE = "django.contrib.sessions.backends.signed_cookies"
WSGI_APPLICATION = "PMS.wsgi.application"
AUTH_PASSWORD_VALIDATORS = [
    {
        "NAME": "django.contrib.auth.password_validation.UserAttributeSimilarityValidator",
    },
    {
        "NAME": "django.contrib.auth.password_validation.MinimumLengthValidator",
    },
    {
        "NAME": "django.contrib.auth.password_validation.CommonPasswordValidator",
    },
    {
        "NAME": "django.contrib.auth.password_validation.NumericPasswordValidator",
    },
]

MONGO_USERNAME = config("USER_NAME")
PASSWORD_ENCRYPTED = config("PASSWORD")
MONGO_AUTH_DB = config("AUTH_DB")
MONGO_HOST = config("HOST")
BAPI_API = config("BAPI_API")
MAIL_API_URL = config("MAIL_API_URL")
MAIL_API_TOKEN = config("MAIL_API_TOKEN")
COE_CONNECTION = config("COE_CONNECTION")
PGR_CONNECTION = config("PGR_CONNECTION")
VLM_CONNECTION = config("VLM_CONNECTION")
ANNUR_CONNECTION = config("ANNUR_CONNECTION")
VYM_CONNECTION = config("VYM_CONNECTION")
PPM_CONNECTION = config("PPM_CONNECTION")
PMD2_CONNECTION = config("PMD2_CONNECTION")
LOGIN_REDIRECT_URL = "/"
LOGIN_URL = "/"

fernet = Fernet(SECRET_KEY.encode())
MONGO_PASSWORD = fernet.decrypt(PASSWORD_ENCRYPTED.encode()).decode()

LANGUAGE_CODE = "en-us"
TIME_ZONE = "Asia/Kolkata"
USE_I18N = True
USE_TZ = True
STATIC_URL = "/static_files/"
STATIC_ROOT = BASE_DIR / "static_files"
STATICFILES_DIRS = [BASE_DIR / "core" / "static"]
MEDIA_URL = "/media/"
MEDIA_ROOT = f"{BASE_DIR}/media/"
APILOGIN = config("APILOGIN")
BAPI_API = config("BAPI_API")
SESSION_COOKIE_NAME = "sessionid_app1"
SESSION_COOKIE_AGE = 10800  # 1 hour in seconds

DATA_UPLOAD_MAX_NUMBER_FIELDS = 50000

PLANT_CODES = ["002", "034", "041", "123", "143"]
CELERY_BROKER_URL = "redis://localhost:6379/0"
CELERY_RESULT_BACKEND = "redis://localhost:6379/0"
CELERY_ACCEPT_CONTENT = ["json"]
CELERY_TASK_SERIALIZER = "json"
CELERY_RESULT_SERIALIZER = "json"
CELERY_TIMEZONE = "Asia/Kolkata"
CELERY_ENABLE_UTC = False
# Force-load so worker registers collector even if tasks.py side-import misses
# CELERY_IMPORTS = ("core.mtlink_collector", "core.oeetask")

# MTLINK_API_BASE_URL = config("MTLINK_API_BASE_URL")
# MTLINK_API_USERNAME = config("MTLINK_API_USERNAME")
# MTLINK_API_PASSWORD = config("MTLINK_API_PASSWORD")
# MTLINK_API_TIMEOUT = int(config("MTLINK_API_TIMEOUT", 10))
# if MTLINK_API_TIMEOUT < 5:
#     MTLINK_API_TIMEOUT = 10

# # FANUC MTLink raw event collector (Celery Beat → mtlink_data)
# # Interval in seconds. Change only here.
# FANUC_MTLINK_COLLECTION_INTERVAL = int(
#     config("FANUC_MTLINK_COLLECTION_INTERVAL", default=30)
# )
# # Plant DB that holds PMS_machine_info + mtlink_data
# FANUC_MTLINK_PLANT_CODE = config("FANUC_MTLINK_PLANT_CODE", default="041")
# # First run (no state): how far back to pull
# FANUC_MTLINK_INITIAL_LOOKBACK_MINUTES = int(
#     config("FANUC_MTLINK_INITIAL_LOOKBACK_MINUTES", default=120)
# )

# CELERY_BEAT_SCHEDULE = {
#     "collect-mtlink-data": {
#         "task": "collect_mtlink_data",
#         "schedule": schedule(run_every=FANUC_MTLINK_COLLECTION_INTERVAL),
#     },
# }

try:
    import django_redis  # noqa: F401

    CACHES = {
        "default": {
            "BACKEND": "django_redis.cache.RedisCache",
            "LOCATION": "redis://127.0.0.1:6379/1",
            "OPTIONS": {
                "CLIENT_CLASS": "django_redis.client.DefaultClient",
            },
        }
    }
except ImportError:
    CACHES = {
        "default": {
            "BACKEND": "django.core.cache.backends.locmem.LocMemCache",
        }
    }

CRONJOBS = [
    # Shift 1 - 4:25 PM IST (08:00–16:30 window) -> 10:55 UTC
    (
        "55 10 * * *",
        "django.core.management.call_command",
        ["save_shiftwise_oee", "--shift=Shift 1"],
    ),
    # Shift 2 - 12:55 AM IST (16:30–01:00 window) -> 19:25 UTC (previous evening)
    (
        "25 19 * * *",
        "django.core.management.call_command",
        ["save_shiftwise_oee", "--shift=Shift 2"],
    ),
    # Shift 3 - 7:55 AM IST (01:00–08:00 window) -> 2:25 UTC
    (
        "25 2 * * *",
        "django.core.management.call_command",
        ["save_shiftwise_oee", "--shift=Shift 3"],
    ),
    # WIP Daily - 7:57 AM IST -> 2:27 UTC
    (
        "27 2 * * *",
        "django.core.management.call_command",
        ["save_wip_daily"],
    ),
    # Daywise OEE Day - 11:55 PM IST (08:00–23:55) -> 18:25 UTC
    (
        "25 18 * * *",
        "django.core.management.call_command",
        ["save_daywise_oee"],
    ),
    # Daywise OEE Night + Full - 7:56 AM IST (after Shift 3 at 7:55) -> 2:26 UTC
    # Full merges day+night (or shiftwise 1+2+3); avoids Shift-3-only counters.
    (
        "26 2 * * *",
        "django.core.management.call_command",
        ["save_daywise_oee_night"],
    ),
    # Tool Calibration Due Soon - 6:00 AM IST -> 12:30 AM UTC
    (
        "30 0 * * *",
        "django.core.management.call_command",
        ["send_tool_calibration_due_soon_mail"],
    ),
    # Tool Calibration Overdue - 6:00 AM IST -> 12:30 AM UTC
    (
        "30 0 * * *",
        "django.core.management.call_command",
        ["send_tool_calibration_overdue_mail"],
    ),
    # Weekly Completed - Monday 6:00 AM IST -> Monday 12:30 AM UTC
    (
        "30 0 * * 1",
        "django.core.management.call_command",
        ["send_tool_calibration_completed_mail"],
    ),
]

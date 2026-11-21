import os
from datetime import timedelta

from dotenv import load_dotenv


load_dotenv()

basedir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

# Local development only; app.py refuses to boot with it outside DEBUG.
INSECURE_DEFAULT_SECRET_KEY = "dev-secret-key-change-me"



def clean_portal_slug(raw):
    """Accept the slug as typed, or by mistake as a pasted link or with a trailing
    "/packages": a slug containing "/" can never match the portal route, which
    would 404 the whole portal. Returns just the first path segment."""
    value = (raw or "").strip()
    if "/partner-portal/" in value:
        value = value.split("/partner-portal/", 1)[1]
    return value.strip("/").split("/")[0].strip()

class Config:
    """
    Default values match a stock XAMPP install (MySQL on localhost:3306,
    user 'root', empty password). Override any of these with a .env file
    or real environment variables in production.
    """
    DEBUG = os.environ.get("FLASK_DEBUG", "0") == "1"
    SECRET_KEY = os.environ.get("SECRET_KEY", INSECURE_DEFAULT_SECRET_KEY)

    MYSQL_HOST = os.environ.get("MYSQL_HOST", "localhost")
    MYSQL_PORT = int(os.environ.get("MYSQL_PORT", 3306))
    MYSQL_USER = os.environ.get("MYSQL_USER", "root")
    MYSQL_PASSWORD = os.environ.get("MYSQL_PASSWORD", "")
    MYSQL_DB = os.environ.get("MYSQL_DB", "heaven_and_angel_scents")

    # Business-day timezone as a MySQL offset on every connection (default UTC+8).
    BUSINESS_TIMEZONE_OFFSET = os.environ.get("BUSINESS_TIMEZONE_OFFSET", "+08:00")

    # Connection pool size per worker (mysql-connector max 32; keep below MySQL max_connections).
    MYSQL_POOL_SIZE = min(int(os.environ.get("MYSQL_POOL_SIZE", 20)), 32)
    # How long a request waits for a free pooled connection before the app
    # gives up and answers 503 (see db._checkout_connection).
    MYSQL_POOL_WAIT_SECONDS = float(os.environ.get("MYSQL_POOL_WAIT_SECONDS", 5))

    # Pure-Python driver so gevent can switch requests during queries; 0 only for non-gevent servers.
    MYSQL_USE_PURE = os.environ.get("MYSQL_USE_PURE", "1") == "1"

    WTF_CSRF_TIME_LIMIT = None
    SESSION_COOKIE_HTTPONLY = True
    SESSION_COOKIE_SAMESITE = "Lax"
    SESSION_COOKIE_SECURE = False
    # Idle timeout: the session cookie expires after this long without activity.
    PERMANENT_SESSION_LIFETIME = timedelta(
        hours=float(os.environ.get("SESSION_IDLE_HOURS", 8)))

    # Request body cap in bytes (8MB covers a photo or CSV import); stops oversized POSTs.
    MAX_CONTENT_LENGTH = int(
        os.environ.get("MAX_CONTENT_LENGTH", 8 * 1024 * 1024))

    # Flask-Limiter on/off; tests set it to 0 so many requests do not trip limits.
    RATELIMIT_ENABLED = os.environ.get("RATELIMIT_ENABLED", "1") == "1"

    # AI Assistant (Gemini). Read-only, role/branch-scoped — see routes/ai.py.
    # If GEMINI_API_KEY is blank, the assistant just tells the user it isn't configured.
    GEMINI_API_KEY = os.environ.get("GEMINI_API_KEY", "")
    GEMINI_MODEL = os.environ.get("GEMINI_MODEL", "gemini-2.5-flash")

    # Per-user limit on AI chat (Flask-Limiter syntax) to cap Gemini cost.
    AI_CHAT_RATE_LIMIT = os.environ.get(
        "AI_CHAT_RATE_LIMIT", "15 per minute;150 per day")

    # Outbound SMTP for inquiry and backup emails; if unset, sending is skipped and logged.
    MAIL_SERVER = os.environ.get("MAIL_SERVER", "")
    MAIL_PORT = int(os.environ.get("MAIL_PORT", 587))
    MAIL_USE_TLS = os.environ.get("MAIL_USE_TLS", "1") == "1"
    MAIL_USERNAME = os.environ.get("MAIL_USERNAME", "")
    # Spaces in a Gmail App Password are display-only, so they are stripped.
    MAIL_PASSWORD = os.environ.get("MAIL_PASSWORD", "")
    if "gmail.com" in MAIL_SERVER.lower() or "googlemail.com" in MAIL_SERVER.lower():
        MAIL_PASSWORD = MAIL_PASSWORD.replace(" ", "")
    MAIL_DEFAULT_SENDER = os.environ.get("MAIL_DEFAULT_SENDER", "")
    # Where new-inquiry notifications are sent — the HQ inbox that should
    # follow up with the distributor/reseller.
    PARTNER_INQUIRY_NOTIFY_EMAIL = os.environ.get(
        "PARTNER_INQUIRY_NOTIFY_EMAIL", "")

    # Backup email recipient (falls back to the inquiry inbox); max attachment size in MB.
    BACKUP_NOTIFY_EMAIL = os.environ.get("BACKUP_NOTIFY_EMAIL", "")
    BACKUP_EMAIL_MAX_MB = float(os.environ.get("BACKUP_EMAIL_MAX_MB", 18))
    # Passphrase for the encrypted .enc backup; never emailed; without it no backup is sent.
    BACKUP_ZIP_PASSWORD = os.environ.get("BACKUP_ZIP_PASSWORD", "")

    # Secret slug for the public partner portal link; set it in .env, never in code.
    PARTNER_PORTAL_SLUG = clean_portal_slug(os.environ.get("PARTNER_PORTAL_SLUG"))

    # Optional contact details on the partner portal; blank ones are not shown.
    PORTAL_CONTACT_PHONE = os.environ.get("PORTAL_CONTACT_PHONE", "")
    PORTAL_CONTACT_EMAIL = os.environ.get("PORTAL_CONTACT_EMAIL", "")
    PORTAL_CONTACT_VIBER = os.environ.get("PORTAL_CONTACT_VIBER", "")
    PORTAL_CONTACT_MESSENGER_URL = os.environ.get("PORTAL_CONTACT_MESSENGER_URL", "")
    PORTAL_REPLY_TIME = os.environ.get("PORTAL_REPLY_TIME", "")

    # Development only: enables Admin Clear Data; hard-disabled in ProductionConfig.
    ALLOW_DATA_RESET = os.environ.get("ALLOW_DATA_RESET", "0") == "1"

    # Development only: run one automatic backup at this business time; empty in production.
    BACKUP_TEST_RUN_AT = os.environ.get("BACKUP_TEST_RUN_AT", "").strip()


class ProductionConfig(Config):
    """Settings for deployment behind
    HTTPS and a production WSGI server.
    """

    DEBUG = False
    TESTING = False
    # Off by default. Only a deliberate, separately named opt-in turns it on,
    # for wiping a deployment that holds test data. Set it back to 0 afterwards.
    ALLOW_DATA_RESET = os.environ.get("ALLOW_DATA_RESET_IN_PRODUCTION", "0") == "1"
    BACKUP_TEST_RUN_AT = ""
    SECRET_KEY = os.environ.get("SECRET_KEY")
    SESSION_COOKIE_SECURE = True
    PREFERRED_URL_SCHEME = "https"


CONFIG_BY_ENV = {
    "development": Config,
    "production": ProductionConfig,
}

"""Configuration for Circular E-Waste Recovery Platform.
Supports SQLite for local development and PostgreSQL for production deployment on Render.
Pure local and persistent disk storage (no S3 or Supabase).
"""

import os
import secrets
from datetime import timedelta
from pathlib import Path
from dotenv import load_dotenv

BASE_DIR = Path(__file__).resolve().parent
load_dotenv(BASE_DIR / ".env")


def normalize_database_url(url: str) -> str:
    """Normalize PostgreSQL URL schemas for SQLAlchemy compatibility with psycopg 3.
    
    Supports:
      - postgres:// -> postgresql+psycopg://
      - postgresql:// -> postgresql+psycopg://
      - postgresql+psycopg:// -> postgresql+psycopg:// (unchanged)
      - postgresql+psycopg2:// -> postgresql+psycopg://
    """
    if not url:
        return "" if url is not None and not url.strip() else (url or "")
    url = url.strip()
    if url.startswith("postgres://"):
        return url.replace("postgres://", "postgresql+psycopg://", 1)
    if url.startswith("postgresql+psycopg2://"):
        return url.replace("postgresql+psycopg2://", "postgresql+psycopg://", 1)
    if url.startswith("postgresql+psycopg://"):
        return url
    if url.startswith("postgresql://"):
        return url.replace("postgresql://", "postgresql+psycopg://", 1)
    return url



class Config:
    """Base application configuration."""
    ENV = os.environ.get("FLASK_ENV", os.environ.get("ENV", "development"))
    IS_VERCEL = bool(os.environ.get("VERCEL"))
    VERCEL_ENV = os.environ.get("VERCEL_ENV", "")
    IS_PRODUCTION = (
        os.environ.get("IS_PRODUCTION", "").lower() in ("true", "1", "yes")
        or ENV == "production"
        or VERCEL_ENV == "production"
        or (IS_VERCEL and VERCEL_ENV not in ("development", "local"))
        or bool(os.environ.get("RENDER"))
    )

    # Secret key for sessions and CSRF:
    # 1. Explicitly configured CIRCULAR_SECRET_KEY or SECRET_KEY in environment
    # 2. Stable Vercel fallback: Deterministic key derived from VERCEL_GIT_COMMIT_SHA across all serverless instances
    # 3. Local development: token_hex(32)
    _secret = os.environ.get("CIRCULAR_SECRET_KEY") or os.environ.get("SECRET_KEY")
    if not _secret and (IS_PRODUCTION or IS_VERCEL):
        commit_sha = os.environ.get("VERCEL_GIT_COMMIT_SHA") or os.environ.get("VERCEL_DEPLOYMENT_ID")
        if commit_sha:
            import hashlib
            _secret = hashlib.sha256(
                f"circular-stable-session-secret-{commit_sha}".encode("utf-8")
            ).hexdigest()
        else:
            _secret = "circular-default-production-session-secret-key-32b"
    SECRET_KEY = _secret or secrets.token_hex(32)

    # Session cookie security configuration for production and Vercel HTTPS
    SESSION_COOKIE_NAME = "circular_session"
    SESSION_COOKIE_HTTPONLY = True
    SESSION_COOKIE_SECURE = IS_PRODUCTION or (IS_VERCEL and VERCEL_ENV != "development")
    SESSION_COOKIE_SAMESITE = "Lax"
    PERMANENT_SESSION_LIFETIME = timedelta(days=7)

    MAX_CONTENT_LENGTH = int(os.environ.get("MAX_CONTENT_LENGTH", 16 * 1024 * 1024))

    # File storage directory: supports Render persistent disk mounted at /var/data/uploads,
    # Vercel /tmp/uploads, or local instance/uploads
    UPLOAD_STORAGE_PATH = (
        os.environ.get("UPLOAD_STORAGE_PATH")
        or os.environ.get("UPLOAD_FOLDER")
        or (str(Path("/tmp/uploads")) if os.environ.get("VERCEL") else str(BASE_DIR / "instance" / "uploads"))
    )
    UPLOAD_FOLDER = UPLOAD_STORAGE_PATH
    STORAGE_PROVIDER = "local"

    # Database configuration supporting SQLite local and PostgreSQL on Render / Vercel
    _raw_db_url = os.environ.get("DATABASE_URL")
    if _raw_db_url:
        SQLALCHEMY_DATABASE_URI = normalize_database_url(_raw_db_url)
        if SQLALCHEMY_DATABASE_URI.startswith("sqlite:///"):
            db_path_str = SQLALCHEMY_DATABASE_URI.replace("sqlite:///", "", 1)
            if db_path_str and db_path_str != ":memory:":
                db_p = Path(db_path_str)
                if not db_p.is_absolute():
                    db_p = (BASE_DIR / db_p).resolve()
                    SQLALCHEMY_DATABASE_URI = f"sqlite:///{db_p}"
                try:
                    db_p.parent.mkdir(parents=True, exist_ok=True)
                except OSError:
                    pass
    else:
        # Default local SQLite database (uses /tmp on Vercel serverless environment)
        if os.environ.get("VERCEL"):
            instance_dir = Path("/tmp")
        else:
            instance_dir = BASE_DIR / "instance"
            try:
                instance_dir.mkdir(parents=True, exist_ok=True)
            except OSError:
                instance_dir = Path("/tmp")
        SQLALCHEMY_DATABASE_URI = f"sqlite:///{instance_dir / 'circular.sqlite3'}"

    SQLALCHEMY_TRACK_MODIFICATIONS = False

    # Vercel internal service bindings (multi-service routing & communication)
    BACKEND_URL = os.environ.get("BACKEND_URL", "").rstrip("/")

    # Outbound Partner API settings (Refurbishment Intelligence Service)
    # Supports direct environment setting or Vercel service binding
    PARTNER_API_URL = (
        os.environ.get("PARTNER_API_URL")
        or os.environ.get("REFURBISHMENT_SERVICE_URL")
        or ""
    ).rstrip("/")
    PARTNER_API_KEY = os.environ.get("PARTNER_API_KEY", "")

    # Inbound API authentication key (used by partners calling our endpoints)
    _default_api_key = (
        f"circular-api-{os.environ.get('VERCEL_GIT_COMMIT_SHA', 'fallback')[:16]}"
        if IS_PRODUCTION or IS_VERCEL
        else "dev-circular-key"
    )
    CIRCULAR_API_KEY = os.environ.get("CIRCULAR_API_KEY") or _default_api_key

    # Demo data policy: in production default to False unless explicitly enabled
    SEED_DEMO_DATA = os.environ.get(
        "SEED_DEMO_DATA",
        "false" if IS_PRODUCTION else "true"
    ).lower() in ("true", "1", "yes")

    @staticmethod
    def normalize_database_url(url: str) -> str:
        """Normalize PostgreSQL URL schemas for SQLAlchemy compatibility with psycopg 3."""
        return normalize_database_url(url)

    @classmethod
    def validate_production_config(cls, conf=None):
        """Validate critical secrets in production to fail fast and fail secure."""
        target = conf if conf is not None else cls
        is_prod = (
            target.get("IS_PRODUCTION", False)
            if hasattr(target, "get")
            else getattr(target, "IS_PRODUCTION", False)
        )
        if is_prod:
            secret = (
                target.get("SECRET_KEY")
                if hasattr(target, "get")
                else getattr(target, "SECRET_KEY", None)
            )
            circular_secret = (
                target.get("CIRCULAR_SECRET_KEY")
                if hasattr(target, "get")
                else getattr(target, "CIRCULAR_SECRET_KEY", None)
            )
            effective_secret = circular_secret or secret
            if not effective_secret or effective_secret == "dev-secret-key-replace-in-production":
                raise ValueError("CIRCULAR_SECRET_KEY must be configured in production.")
            circular_key = (
                target.get("CIRCULAR_API_KEY")
                if hasattr(target, "get")
                else getattr(target, "CIRCULAR_API_KEY", None)
            )
            if not circular_key:
                raise ValueError("CIRCULAR_API_KEY must be configured in production.")


class TestConfig(Config):
    """Testing configuration."""
    TESTING = True
    IS_PRODUCTION = False
    IS_VERCEL = False
    SESSION_COOKIE_SECURE = False
    SECRET_KEY = "test-only-secret-key"
    SQLALCHEMY_DATABASE_URI = "sqlite:///:memory:"
    BACKEND_URL = "http://internal-backend"
    PARTNER_API_URL = "https://partner.example.com"
    PARTNER_API_KEY = "test-partner-key"
    CIRCULAR_API_KEY = "test-circular-key"
    UPLOAD_STORAGE_PATH = "instance/uploads"
    UPLOAD_FOLDER = "instance/uploads"
    STORAGE_PROVIDER = "local"
    SEED_DEMO_DATA = True

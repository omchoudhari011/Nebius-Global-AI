"""Settings, read once from the environment (and backend/.env when present).

Production (CUSTODIAN_ENV=production) refuses to start with a missing or weak secret.
Development fills missing secrets from DATA_DIR/.dev-secrets.json, creating it on the
first run, so `uvicorn app.main:app` works with no setup. The dev passcode is logged at
startup.
"""

from __future__ import annotations

import json
import logging
import os
import secrets
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from cryptography.fernet import Fernet
from dotenv import load_dotenv

BACKEND_DIR = Path(__file__).resolve().parent.parent

DEFAULT_NEBIUS_BASE_URL = "https://api.tokenfactory.nebius.com/v1"
DEFAULT_NEBIUS_MODEL_ID = "nvidia/nemotron-3-super-120b-a12b"

log = logging.getLogger(__name__)


class ConfigError(RuntimeError):
    """The environment can't run Custodian safely; the message says what to fix."""


@dataclass(frozen=True)
class Settings:
    env: str
    data_dir: Path
    database_path: Path
    app_secret_keys: tuple[str, ...]  # first key encrypts; all keys decrypt (rotation)
    session_secret: str
    session_max_age_seconds: int
    passcode: str
    passcode_generated: bool  # dev only: logged at startup so you can sign in
    user_email: str
    timezone: str
    model_backend: str
    nebius_api_key: str
    nebius_model_id: str
    nebius_base_url: str
    model_timeout_seconds: float
    model_max_tokens: int
    model_temperature: float
    model_extra_body: dict
    embedding_model: str  # "" = semantic search off (keyword search only)
    embedding_base_url: str
    embedding_api_key: str
    embedding_min_similarity: float  # cosine; below this a semantic match is ignored
    cors_origins: tuple[str, ...]
    cookie_secure: bool
    cookie_samesite: str
    tool_timeout_seconds: float
    scheduler_enabled: bool
    web_dist_dir: Path | None
    log_level: str
    plan_preview: str  # development only: which plan to act as without a license ("" = pro)
    license_key: str  # CUSTODIAN_LICENSE_KEY; wins over a key activated in the app
    model_price_input_per_m: float | None  # USD per million prompt tokens, for cost estimates
    model_price_output_per_m: float | None  # USD per million completion tokens

    @property
    def is_production(self) -> bool:
        return self.env == "production"

    @property
    def tool_data_dir(self) -> Path:
        return self.data_dir / "tools"


def _bool(name: str, default: bool) -> bool:
    raw = os.getenv(name)
    if raw is None or raw.strip() == "":
        return default
    value = raw.strip().lower()
    if value in ("1", "true", "yes", "on"):
        return True
    if value in ("0", "false", "no", "off"):
        return False
    raise ConfigError(f"{name} must be true or false, got {raw!r}")


def _int(name: str, default: int, minimum: int, maximum: int) -> int:
    raw = os.getenv(name, "").strip()
    if not raw:
        return default
    try:
        value = int(raw)
    except ValueError:
        raise ConfigError(f"{name} must be a whole number, got {raw!r}") from None
    if not minimum <= value <= maximum:
        raise ConfigError(f"{name} must be between {minimum} and {maximum}, got {value}")
    return value


def _float(name: str, default: float, minimum: float, maximum: float) -> float:
    raw = os.getenv(name, "").strip()
    if not raw:
        return default
    try:
        value = float(raw)
    except ValueError:
        raise ConfigError(f"{name} must be a number, got {raw!r}") from None
    if not minimum <= value <= maximum:
        raise ConfigError(f"{name} must be between {minimum} and {maximum}, got {value}")
    return value


def _price(name: str) -> float | None:
    """An optional price; unset means unknown, so no cost is shown rather than a made-up one."""
    if not os.getenv(name, "").strip():
        return None
    return _float(name, 0.0, 0.0, 10_000.0)


def _path(raw: str, base: Path) -> Path:
    path = Path(raw).expanduser()
    return path if path.is_absolute() else (base / path).resolve()


def _dev_secrets(data_dir: Path) -> dict:
    """Secrets for development, generated once and kept next to the database."""
    path = data_dir / ".dev-secrets.json"
    if path.exists():
        return json.loads(path.read_text(encoding="utf-8"))
    data_dir.mkdir(parents=True, exist_ok=True)
    values = {
        "APP_SECRET_KEY": Fernet.generate_key().decode(),
        "SESSION_SECRET": secrets.token_urlsafe(48),
        "CUSTODIAN_PASSCODE": secrets.token_urlsafe(9),
    }
    path.write_text(json.dumps(values, indent=2), encoding="utf-8")
    try:
        path.chmod(0o600)
    except OSError:
        pass
    return values


def load_settings() -> Settings:
    if not _bool("CUSTODIAN_SKIP_DOTENV", False):  # tests and evals pin their own environment
        load_dotenv(BACKEND_DIR / ".env", override=False)

    env = os.getenv("CUSTODIAN_ENV", "development").strip().lower() or "development"
    if env not in ("development", "production"):
        raise ConfigError(f"CUSTODIAN_ENV must be development or production, got {env!r}")
    production = env == "production"

    data_dir = _path(os.getenv("DATA_DIR", "").strip() or "data", BACKEND_DIR)
    database_path = _path(os.getenv("DATABASE_PATH", "").strip() or str(data_dir / "custodian.db"), BACKEND_DIR)

    app_secret = os.getenv("APP_SECRET_KEY", "").strip()
    session_secret = os.getenv("SESSION_SECRET", "").strip()
    passcode = os.getenv("CUSTODIAN_PASSCODE", "").strip()
    passcode_generated = False

    missing = [
        name
        for name, value in (
            ("APP_SECRET_KEY", app_secret),
            ("SESSION_SECRET", session_secret),
            ("CUSTODIAN_PASSCODE", passcode),
        )
        if not value
    ]
    if missing and production:
        raise ConfigError(
            "Missing required settings: " + ", ".join(missing)
            + ". Run `python -m app.cli init-env --production` to generate them."
        )
    if missing:
        dev = _dev_secrets(data_dir)
        app_secret = app_secret or dev["APP_SECRET_KEY"]
        session_secret = session_secret or dev["SESSION_SECRET"]
        if not passcode:
            passcode = dev["CUSTODIAN_PASSCODE"]
            passcode_generated = True

    keys = tuple(k.strip() for k in app_secret.split(",") if k.strip())
    for key in keys:
        try:
            Fernet(key.encode())
        except (ValueError, TypeError):
            raise ConfigError(
                "APP_SECRET_KEY must be one or more comma-separated Fernet keys "
                "(generate one with `python -m app.cli gen-key`)"
            ) from None

    if production:
        if len(session_secret) < 32:
            raise ConfigError("SESSION_SECRET must be at least 32 characters in production")
        if len(passcode) < 10 or passcode.lower() in ("change-me", "changeme", "password"):
            raise ConfigError("CUSTODIAN_PASSCODE must be at least 10 characters and not a placeholder in production")

    timezone = os.getenv("CUSTODIAN_TIMEZONE", "").strip() or "UTC"
    try:
        ZoneInfo(timezone)
    except (ZoneInfoNotFoundError, ValueError):
        raise ConfigError(f"CUSTODIAN_TIMEZONE must be an IANA time zone such as Asia/Kolkata, got {timezone!r}") from None

    model_backend = os.getenv("MODEL_BACKEND", "stub").strip().lower() or "stub"
    if model_backend not in ("stub", "nebius"):
        raise ConfigError(f"MODEL_BACKEND must be stub or nebius, got {model_backend!r}")
    nebius_api_key = os.getenv("NEBIUS_API_KEY", "").strip()
    if model_backend == "nebius" and not nebius_api_key:
        raise ConfigError("MODEL_BACKEND=nebius needs NEBIUS_API_KEY")

    nebius_base_url = os.getenv("NEBIUS_BASE_URL", "").strip() or DEFAULT_NEBIUS_BASE_URL
    embedding_base_url = os.getenv("EMBEDDING_BASE_URL", "").strip() or nebius_base_url
    # The Nebius key only ever goes to the Nebius endpoint: an embedding server elsewhere
    # (Ollama, another provider) gets EMBEDDING_API_KEY or no key at all.
    embedding_api_key = os.getenv("EMBEDDING_API_KEY", "").strip()
    if not embedding_api_key and embedding_base_url.rstrip("/") == nebius_base_url.rstrip("/"):
        embedding_api_key = nebius_api_key

    extra_raw = os.getenv("MODEL_EXTRA_BODY", "").strip()
    try:
        model_extra_body = json.loads(extra_raw) if extra_raw else {}
    except json.JSONDecodeError:
        raise ConfigError("MODEL_EXTRA_BODY must be a JSON object") from None
    if not isinstance(model_extra_body, dict):
        raise ConfigError("MODEL_EXTRA_BODY must be a JSON object")

    cookie_samesite = os.getenv("COOKIE_SAMESITE", "lax").strip().lower() or "lax"
    if cookie_samesite not in ("lax", "strict", "none"):
        raise ConfigError("COOKIE_SAMESITE must be lax, strict or none")
    cookie_secure = _bool("COOKIE_SECURE", production)
    if cookie_samesite == "none" and not cookie_secure:
        raise ConfigError("COOKIE_SAMESITE=none requires COOKIE_SECURE=true (browsers reject it otherwise)")

    cors_origins = tuple(
        origin.strip().rstrip("/")
        for origin in os.getenv("CORS_ORIGINS", "" if production else "http://localhost:8081").split(",")
        if origin.strip()
    )
    if "*" in cors_origins:
        raise ConfigError("CORS_ORIGINS can't be * because the API uses credentials; list origins explicitly")

    web_raw = os.getenv("WEB_DIST_DIR", "").strip()
    web_dist_dir = _path(web_raw, BACKEND_DIR) if web_raw else None

    log_level = os.getenv("LOG_LEVEL", "INFO").strip().upper() or "INFO"
    if log_level not in ("DEBUG", "INFO", "WARNING", "ERROR"):
        raise ConfigError("LOG_LEVEL must be DEBUG, INFO, WARNING or ERROR")

    from .plans import PLANS  # here, not at the top: app/plans.py imports this module

    plan_preview = os.getenv("CUSTODIAN_PLAN", "").strip().lower()
    if plan_preview and production:
        raise ConfigError("CUSTODIAN_PLAN only previews a plan in development; in production the plan comes from your license")
    if plan_preview and plan_preview not in PLANS:
        raise ConfigError(f"CUSTODIAN_PLAN must be one of {', '.join(PLANS)}, got {plan_preview!r}")

    return Settings(
        env=env,
        data_dir=data_dir,
        database_path=database_path,
        app_secret_keys=keys,
        session_secret=session_secret,
        session_max_age_seconds=_int("SESSION_MAX_AGE_DAYS", 30, 1, 365) * 86400,
        passcode=passcode,
        passcode_generated=passcode_generated,
        user_email=os.getenv("CUSTODIAN_USER_EMAIL", "").strip() or "owner@custodian.local",
        timezone=timezone,
        model_backend=model_backend,
        nebius_api_key=nebius_api_key,
        nebius_model_id=os.getenv("NEBIUS_MODEL_ID", "").strip() or DEFAULT_NEBIUS_MODEL_ID,
        nebius_base_url=nebius_base_url,
        model_timeout_seconds=_float("MODEL_TIMEOUT_SECONDS", 60.0, 1.0, 600.0),
        model_max_tokens=_int("MODEL_MAX_TOKENS", 2048, 64, 32768),  # a report written into a document needs ~1500
        model_temperature=_float("MODEL_TEMPERATURE", 0.3, 0.0, 2.0),
        model_extra_body=model_extra_body,
        embedding_model=os.getenv("EMBEDDING_MODEL", "").strip(),
        embedding_base_url=embedding_base_url,
        embedding_api_key=embedding_api_key,
        embedding_min_similarity=_float("EMBEDDING_MIN_SIMILARITY", 0.5, -1.0, 1.0),
        cors_origins=cors_origins,
        cookie_secure=cookie_secure,
        cookie_samesite=cookie_samesite,
        tool_timeout_seconds=_float("TOOL_TIMEOUT_SECONDS", 5.0, 0.5, 120.0),
        scheduler_enabled=_bool("SCHEDULER_ENABLED", True),
        web_dist_dir=web_dist_dir,
        log_level=log_level,
        plan_preview=plan_preview,
        license_key=os.getenv("CUSTODIAN_LICENSE_KEY", "").strip(),
        model_price_input_per_m=_price("MODEL_PRICE_INPUT_PER_M"),
        model_price_output_per_m=_price("MODEL_PRICE_OUTPUT_PER_M"),
    )


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return load_settings()


def reset_settings() -> None:
    """Forget the cached settings (tests change the environment between cases)."""
    get_settings.cache_clear()

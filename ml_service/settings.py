"""Environment-driven configuration for the prediction service.

Values come from process environment variables. An optional, git-ignored
``ml_service/.env`` file is loaded first; real environment variables win over it.
API keys are never logged or included in ``repr``.
"""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Mapping, Optional

PROJECT_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_ENV_FILE = Path(__file__).resolve().parent / ".env"


class SettingsError(ValueError):
    """Raised when an environment value is present but invalid."""


@dataclass(frozen=True)
class Settings:
    # Secrets (repr=False keeps them out of logs and tracebacks)
    polygon_api_key: str = field(default="", repr=False)
    groq_api_key: str = field(default="", repr=False)
    internal_token: str = field(default="", repr=False)

    # Storage
    model_dir: Path = PROJECT_ROOT / "models"
    jobs_db_path: Path = PROJECT_ROOT / "models" / "jobs.db"

    # Data window and rate limiting
    lookback_days: int = 730
    news_batch_size: int = 1000
    news_sleep_time: float = 12.0
    related_companies_sleep_time: float = 15.0
    max_related_companies: int = 5

    # Model
    window_size: int = 30
    ensemble_size: int = 3
    num_epochs: int = 100
    mc_samples: int = 10

    @property
    def has_polygon(self) -> bool:
        return bool(self.polygon_api_key)

    @property
    def has_groq(self) -> bool:
        return bool(self.groq_api_key)


def _read_env_file(path: Path) -> dict[str, str]:
    """Parse a simple KEY=VALUE .env file without mutating os.environ."""
    if not path.is_file():
        return {}
    try:
        from dotenv import dotenv_values
    except ImportError:  # python-dotenv is optional
        values: dict[str, str] = {}
        for raw in path.read_text(encoding="utf-8").splitlines():
            line = raw.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, _, value = line.partition("=")
            values[key.strip()] = value.strip().strip('"').strip("'")
        return values
    return {k: v for k, v in dotenv_values(path).items() if v is not None}


def _int(values: Mapping[str, str], key: str, default: int, minimum: int = 1) -> int:
    raw = values.get(key)
    if raw is None or raw.strip() == "":
        return default
    try:
        parsed = int(raw)
    except ValueError as exc:
        raise SettingsError(f"{key} must be an integer") from exc
    if parsed < minimum:
        raise SettingsError(f"{key} must be >= {minimum}")
    return parsed


def _float(values: Mapping[str, str], key: str, default: float, minimum: float = 0.0) -> float:
    raw = values.get(key)
    if raw is None or raw.strip() == "":
        return default
    try:
        parsed = float(raw)
    except ValueError as exc:
        raise SettingsError(f"{key} must be a number") from exc
    if parsed < minimum:
        raise SettingsError(f"{key} must be >= {minimum}")
    return parsed


def _path(values: Mapping[str, str], key: str, default: Path) -> Path:
    raw = values.get(key)
    if raw is None or raw.strip() == "":
        return default
    path = Path(raw)
    return path if path.is_absolute() else (PROJECT_ROOT / path)


def load_settings(
    environ: Optional[Mapping[str, str]] = None,
    env_file: Optional[Path] = DEFAULT_ENV_FILE,
) -> Settings:
    """Build Settings from an optional .env file overlaid by ``environ``."""
    values: dict[str, str] = {}
    if env_file is not None:
        values.update(_read_env_file(Path(env_file)))
    values.update(os.environ if environ is None else environ)

    model_dir = _path(values, "MODEL_DIR", PROJECT_ROOT / "models")
    return Settings(
        polygon_api_key=values.get("POLYGON_API_KEY", "").strip(),
        groq_api_key=values.get("GROQ_API_KEY", "").strip(),
        internal_token=values.get("ML_INTERNAL_TOKEN", "").strip(),
        model_dir=model_dir,
        jobs_db_path=_path(values, "JOBS_DB_PATH", model_dir / "jobs.db"),
        lookback_days=_int(values, "LOOKBACK_DAYS", 730, minimum=120),
        news_batch_size=_int(values, "NEWS_BATCH_SIZE", 1000),
        news_sleep_time=_float(values, "NEWS_SLEEP_TIME", 12.0),
        related_companies_sleep_time=_float(values, "RELATED_COMPANIES_SLEEP_TIME", 15.0),
        max_related_companies=_int(values, "MAX_RELATED_COMPANIES", 5, minimum=0),
        window_size=_int(values, "WINDOW_SIZE", 30, minimum=5),
        ensemble_size=_int(values, "ENSEMBLE_SIZE", 3),
        num_epochs=_int(values, "NUM_EPOCHS", 100),
        mc_samples=_int(values, "MC_SAMPLES", 10),
    )

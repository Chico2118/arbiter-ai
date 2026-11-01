"""Runtime settings, read from environment variables (see .env.example)."""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

BACKEND_DIR = Path(__file__).resolve().parent.parent


def _load_dotenv() -> None:
    """Minimal .env loader (KEY=VALUE lines) so we avoid an extra dependency.

    Existing environment variables always win over the file.
    """
    for candidate in (BACKEND_DIR / ".env", BACKEND_DIR.parent / ".env"):
        if not candidate.is_file():
            continue
        for line in candidate.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, _, value = line.partition("=")
            os.environ.setdefault(key.strip(), value.strip().strip('"').strip("'"))


_load_dotenv()


def _float(name: str, default: float) -> float:
    try:
        return float(os.environ.get(name, default))
    except ValueError:
        return default


def _int(name: str, default: int) -> int:
    try:
        return int(os.environ.get(name, default))
    except ValueError:
        return default


@dataclass(frozen=True)
class Settings:
    db_path: Path
    ollama_url: str
    cors_origins: tuple[str, ...]
    secret_backend: str  # "keyring" (default) or "memory" (tests only)
    retry_attempts: int
    retry_base_delay: float
    min_paired_n: int
    judge_min_n: int
    judge_min_kappa: float
    max_import_bytes: int


def load_settings() -> Settings:
    db = os.environ.get("ARBITER_DB_PATH") or str(BACKEND_DIR / "data" / "arbiter.db")
    origins = os.environ.get(
        "ARBITER_CORS_ORIGINS", "http://localhost:5173,http://127.0.0.1:5173"
    )
    return Settings(
        db_path=Path(db),
        # 127.0.0.1 rather than "localhost": on Windows, localhost often tries
        # IPv6 (::1) first, adding a ~2 s delay when Ollama only listens on IPv4.
        ollama_url=os.environ.get("ARBITER_OLLAMA_URL", "http://127.0.0.1:11434").rstrip("/"),
        cors_origins=tuple(o.strip() for o in origins.split(",") if o.strip()),
        secret_backend=os.environ.get("ARBITER_SECRET_BACKEND", "keyring"),
        retry_attempts=max(1, _int("ARBITER_RETRY_ATTEMPTS", 3)),
        retry_base_delay=max(0.0, _float("ARBITER_RETRY_BASE_DELAY", 1.0)),
        min_paired_n=max(1, _int("ARBITER_MIN_PAIRED_N", 10)),
        judge_min_n=max(1, _int("ARBITER_JUDGE_MIN_N", 10)),
        judge_min_kappa=_float("ARBITER_JUDGE_MIN_KAPPA", 0.6),
        max_import_bytes=_int("ARBITER_MAX_IMPORT_BYTES", 5 * 1024 * 1024),
    )


settings = load_settings()


def reload_settings() -> Settings:
    """Re-read the environment (used by tests that change env vars)."""
    global settings
    settings = load_settings()
    return settings

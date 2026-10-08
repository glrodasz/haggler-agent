"""Runtime settings, read from environment variables (see .env.example)."""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path


def repo_root() -> Path:
    """Directory holding catalog/ and data/ (the repo, or /app inside the container)."""
    env = os.getenv("HAGGLER_ROOT")
    if env:
        return Path(env)
    here = Path(__file__).resolve()
    for parent in here.parents:
        if (parent / "catalog" / "services.yaml").exists():
            return parent
    return Path.cwd()


def _env_int(name: str, default: int) -> int:
    value = os.getenv(name)
    return int(value) if value else default


def _env_float(name: str, default: float) -> float:
    value = os.getenv(name)
    return float(value) if value else default


@dataclass(frozen=True)
class Settings:
    # Storage: "local" (directory) | "gcs" (bucket) | "memory" (tests).
    store: str = field(default_factory=lambda: os.getenv("HAGGLER_STORE", "local"))
    local_dir: Path = field(
        default_factory=lambda: Path(os.getenv("HAGGLER_LOCAL_DIR", str(repo_root() / ".haggler")))
    )
    gcs_bucket: str | None = field(default_factory=lambda: os.getenv("HAGGLER_GCS_BUCKET"))

    # LLM
    extract_model: str = field(
        default_factory=lambda: os.getenv("HAGGLER_EXTRACT_MODEL", "gemini-3.1-flash-lite")
    )
    llm_enabled: bool = field(
        default_factory=lambda: os.getenv("HAGGLER_LLM", "auto").lower() not in {"0", "off", "no"}
    )
    max_llm_jobs: int = field(default_factory=lambda: _env_int("HAGGLER_MAX_LLM_JOBS", 60))
    max_adjudications: int = field(
        default_factory=lambda: _env_int("HAGGLER_MAX_ADJUDICATIONS", 15)
    )
    llm_concurrency: int = field(default_factory=lambda: _env_int("HAGGLER_LLM_CONCURRENCY", 4))
    # Model requests per run, enforced by the pipeline itself (so adk web/run honour it too);
    # 0 or less turns the LLM off.
    max_llm_calls: int = field(default_factory=lambda: _env_int("HAGGLER_MAX_LLM_CALLS", 150))
    # Wall-clock cap per LLM job (agent retries and HTTP retries included); a hung model call
    # becomes a job error instead of blocking the run.
    llm_job_timeout_s: float = field(
        default_factory=lambda: _env_float("HAGGLER_LLM_JOB_TIMEOUT_S", 120.0)
    )

    # Fetching etiquette
    user_agent: str = field(
        default_factory=lambda: os.getenv(
            "HAGGLER_USER_AGENT",
            "haggler-agent/0.1 (+https://github.com/glrodasz/haggler-agent; price research bot)",
        )
    )
    per_host_interval_s: float = field(
        default_factory=lambda: _env_float("HAGGLER_PER_HOST_INTERVAL_S", 3.0)
    )
    fetch_concurrency: int = field(default_factory=lambda: _env_int("HAGGLER_FETCH_CONCURRENCY", 8))
    respect_robots: bool = field(
        default_factory=lambda: os.getenv("HAGGLER_RESPECT_ROBOTS", "1") != "0"
    )

    # Validation
    quarantine_change_pct: float = field(
        default_factory=lambda: _env_float("HAGGLER_QUARANTINE_CHANGE_PCT", 30.0)
    )
    fx_fee_pct: float = field(default_factory=lambda: _env_float("HAGGLER_FX_FEE_PCT", 2.0))


def llm_credentials_available() -> bool:
    """An API key, or Vertex AI mode read exactly like google-genai reads it:
    GOOGLE_GENAI_USE_ENTERPRISE wins whenever it is set (even empty), else the legacy
    GOOGLE_GENAI_USE_VERTEXAI; only "1" and "true" (any case) switch it on."""
    if os.getenv("GOOGLE_API_KEY") or os.getenv("GEMINI_API_KEY"):
        return True
    value = os.environ.get("GOOGLE_GENAI_USE_ENTERPRISE")
    if value is None:
        value = os.environ.get("GOOGLE_GENAI_USE_VERTEXAI")
    return value is not None and value.lower() in {"1", "true"}

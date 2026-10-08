"""Everything the daily Workflow touches outside itself, injected so tests can swap in fakes.

`build_workflow(deps)` builds the nodes as closures over one Deps. Production wiring lives in
`build_default_deps`, which must stay offline: `import haggler_agent` (adk web / adk run) builds
the app at import time, so HTTP clients, the GCS client and the model client are only created on
first use.
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, date, datetime
from typing import Any

import httpx
from google.adk.models.base_llm import BaseLlm

from haggler.catalog import load_catalog
from haggler.config import Settings, llm_credentials_available
from haggler.fetch import Fetcher, RawResponse
from haggler.fx.service import FxService
from haggler.models.catalog import Catalog
from haggler.models.fx import FxTable
from haggler.models.observations import PriceObservation, PublishedPrice
from haggler.models.run import LlmJob, LlmStats, PipelineState, Task
from haggler.store import Store, store_from_settings

log = logging.getLogger(__name__)

FX_TIMEOUT = httpx.Timeout(connect=10.0, read=20.0, write=20.0, pool=20.0)
# Per HTTP attempt to the model (genai's default is no timeout at all); the pipeline also caps
# each whole job (Settings.llm_job_timeout_s).
LLM_HTTP_TIMEOUT_MS = 60_000
# genai's own default set: replacing it with a shorter list would stop retrying 408/502/504.
LLM_RETRY_STATUS_CODES = [408, 429, 500, 502, 503, 504]


@dataclass
class RunBuffer:
    """Per-run side channel for data that must not travel through node outputs or session state
    (ADK copies both into the session event log): the previous published files, the LLM log that
    persist writes to llm.jsonl, and raw pages of a dry run (which never reach the Store)."""

    previous_prices: dict[str, PublishedPrice] = field(default_factory=dict)
    previous_state: PipelineState = field(default_factory=PipelineState)
    previous_fx: FxTable | None = None
    previous_coverage: list[dict[str, Any]] = field(default_factory=list)
    llm_log: list[dict[str, Any]] = field(default_factory=list)
    llm_stats: LlmStats = field(default_factory=LlmStats)
    # Model requests sent this run (one per attempt, failed ones included): the call budget.
    model_calls: int = 0
    raw_cache: dict[str, str] = field(default_factory=dict)
    # Run whose data this buffer holds (set by plan_tasks).
    run_id: str | None = None
    # task_id -> its LLM jobs with their page text; node outputs carry the jobs without it.
    llm_jobs: dict[str, list[LlmJob]] = field(default_factory=dict)
    # task_id -> (service_id, country) pairs the LLM answered today (TaskState.llm_pairs).
    llm_answered: dict[str, set[tuple[str, str]]] = field(default_factory=dict)
    # task_id -> LLM rows of today's answers whose keys the parser also had: never live rows,
    # but stored with the answer (TaskState.llm_obs) for reuse on the same page text.
    llm_dupes: dict[str, list[PriceObservation]] = field(default_factory=dict)
    # Tasks with no LLM work left for today's page text (TaskState.llm_checked_hash).
    llm_checked: set[str] = field(default_factory=set)
    # Tasks whose jobs were capped per task: some pairs wait for a later run.
    llm_truncated: set[str] = field(default_factory=set)
    # Agent run id -> finish reason of its last model answer (explains empty answers).
    finish_reasons: dict[str, str | None] = field(default_factory=dict)
    errors: list[str] = field(default_factory=list)


@dataclass
class Deps:
    settings: Settings
    catalog: Catalog
    fetcher: Fetcher
    store: Store
    fx: FxService
    # None disables every LLM step (no credentials, HAGGLER_LLM=off, --no-llm).
    model: str | BaseLlm | None
    now: Callable[[], datetime] = field(default=lambda: datetime.now(UTC))
    # First retry delay for transient fetch errors (seconds); tests set it near zero.
    fetch_retry_delay: float = 2.0
    # Hard cap per fetch attempt, on top of httpx timeouts (the per-host queue counts too).
    fetch_timeout_s: float = 120.0
    # Optional extra planning filter (e.g. replay runs keep only URLs the replay index has).
    task_filter: Callable[[Task], bool] | None = None
    buffer: RunBuffer = field(default_factory=RunBuffer)

    def today(self) -> date:
        return self.now().date()

    def reset_buffer(self) -> RunBuffer:
        self.buffer = RunBuffer()
        return self.buffer

    async def aclose(self) -> None:
        for res in (self.fetcher, self.fx):
            closer = getattr(res, "aclose", None)
            if closer is None:
                continue
            try:
                await closer()
            except Exception as exc:  # closing must never hide the run's own outcome
                log.warning("closing %s failed: %s", type(res).__name__, exc)


# ----------------------------------------------------------------------------- lazy wrappers


class LazyFetcher:
    """Creates the real fetcher on the first request (keeps imports free of HTTP clients)."""

    def __init__(self, factory: Callable[[], Fetcher]) -> None:
        self._factory = factory
        self._inner: Fetcher | None = None

    async def fetch(
        self, url: str, *, accept: str = "text/html", egress: str = "default"
    ) -> RawResponse:
        if self._inner is None:
            self._inner = self._factory()
        return await self._inner.fetch(url, accept=accept, egress=egress)

    async def aclose(self) -> None:
        if self._inner is not None:
            await self._inner.aclose()
            self._inner = None


class LazyStore:
    """Creates the real Store on first access (GcsStore needs credentials at construction)."""

    def __init__(self, factory: Callable[[], Store]) -> None:
        self._factory = factory
        self._inner: Store | None = None

    @property
    def inner(self) -> Store:
        if self._inner is None:
            self._inner = self._factory()
        return self._inner

    def write_bytes(self, key: str, data: bytes, *, content_type: str = "application/json") -> str:
        return self.inner.write_bytes(key, data, content_type=content_type)

    def read_bytes(self, key: str) -> bytes | None:
        return self.inner.read_bytes(key)

    def exists(self, key: str) -> bool:
        return self.inner.exists(key)

    def list(self, prefix: str) -> list[str]:
        return self.inner.list(prefix)


class LazyFxService:
    """DefaultFxService with its httpx client created on the first build_table call."""

    def __init__(self, pins: dict[str, str], user_agent: str) -> None:
        self.pins = pins
        self.user_agent = user_agent
        self._client: httpx.AsyncClient | None = None
        self._inner: Any = None
        self.errors: list[str] = []

    async def build_table(
        self, currencies: set[str], *, as_of: date, previous: FxTable | None
    ) -> FxTable:
        if self._inner is None:
            from haggler.fx.service import DefaultFxService

            self._client = httpx.AsyncClient(timeout=FX_TIMEOUT, follow_redirects=True)
            self._inner = DefaultFxService(self._client, self.pins, user_agent=self.user_agent)
        try:
            return await self._inner.build_table(currencies, as_of=as_of, previous=previous)
        finally:
            self.errors = list(getattr(self._inner, "errors", []))

    async def aclose(self) -> None:
        if self._client is not None:
            await self._client.aclose()
            self._client = None
            self._inner = None


# ----------------------------------------------------------------------------- production wiring


def build_model(settings: Settings) -> BaseLlm | None:
    """Gemini with HTTP-level retries and a per-request timeout, or None when the LLM is off, has
    no call budget (HAGGLER_MAX_LLM_CALLS <= 0) or has no credentials."""
    if not settings.llm_enabled or settings.max_llm_calls <= 0:
        return None
    if not llm_credentials_available():
        return None
    from google.adk.models import Gemini
    from google.genai import types

    retry = types.HttpRetryOptions(
        attempts=4, initial_delay=2, http_status_codes=list(LLM_RETRY_STATUS_CODES)
    )
    return Gemini(
        model=settings.extract_model,
        retry_options=retry,
        # ADK builds the client's HttpOptions from retry_options only; client_kwargs replaces
        # them, so the retries are repeated here next to the timeout.
        client_kwargs={
            "http_options": types.HttpOptions(timeout=LLM_HTTP_TIMEOUT_MS, retry_options=retry)
        },
    )


def build_default_deps(settings: Settings | None = None) -> Deps:
    settings = settings or Settings()
    catalog = load_catalog()

    def make_fetcher() -> Fetcher:
        from haggler.fetch.client import HttpFetcher

        return HttpFetcher(settings)

    return Deps(
        settings=settings,
        catalog=catalog,
        fetcher=LazyFetcher(make_fetcher),
        store=LazyStore(lambda: store_from_settings(settings)),
        fx=LazyFxService(dict(catalog.fx_pins), settings.user_agent),
        model=build_model(settings),
    )

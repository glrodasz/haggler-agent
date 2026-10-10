"""Everything the research Workflow touches outside itself (tests swap in fakes).

Like the daily pipeline's Deps, build_default_research_deps must stay offline: `adk web agents`
imports the app at import time, so HTTP, GCS and model clients are only created on first use.
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, date, datetime
from typing import TYPE_CHECKING, Any

from haggler.catalog import (
    default_bundles,
    load_bundles,
    load_catalog,
    load_feasibility,
    load_payment_instruments,
    load_profiles,
    synthesize_profiles,
)
from haggler.config import Settings
from haggler.fetch import Fetcher
from haggler.llm.budget import LlmBudget
from haggler.models.bundles import WatchFile
from haggler.models.catalog import Catalog
from haggler.models.curated import BundlesFile, FeasibilityFile, PaymentInstrumentsFile
from haggler.models.fx import FxTable
from haggler.models.profiles import ProfilesFile
from haggler.research.io import Latest
from haggler.store import Store, store_from_settings

if TYPE_CHECKING:
    from google.adk.models.base_llm import BaseLlm

    from haggler.research.evidence import PageCache

log = logging.getLogger(__name__)

DEFAULT_RESIDENCE = "SE"
DEFAULT_PROFILE = "se_resident_co_citizen"


def research_budget(settings: Settings, *, month_queries_used: int = 0) -> LlmBudget:
    """Per-run caps; the query cap never exceeds what the monthly guard has left."""
    left = max(0, settings.research_max_month_queries - month_queries_used)
    return LlmBudget(
        max_calls=settings.research_max_calls,
        max_queries=min(settings.research_max_queries, left),
        calls_setting="HAGGLER_RESEARCH_MAX_CALLS",
        queries_setting="HAGGLER_RESEARCH_MAX_QUERIES",
    )


@dataclass
class ResearchBuffer:
    """Per-run data that must not travel through node outputs or session state."""

    run_id: str | None = None
    budget: LlmBudget = field(default_factory=lambda: LlmBudget(max_calls=0))
    latest: Latest = field(default_factory=Latest)
    errors: list[str] = field(default_factory=list)
    # The run's pages: one fetch per URL, shared by the bundle watcher and verify_evidence.
    pages: PageCache | None = None
    # latest/fx.json of the daily job (the watcher's plausibility check of price hints).
    fx: FxTable | None = None
    # The previous run's bundle_watch.json, and this run's (None until the watcher ran).
    watch_previous: WatchFile | None = None
    watch: WatchFile | None = None


@dataclass
class ResearchDeps:
    settings: Settings
    catalog: Catalog
    feasibility: FeasibilityFile
    instruments: PaymentInstrumentsFile
    profiles: ProfilesFile
    fetcher: Fetcher
    store: Store
    # None disables the agents (no credentials, HAGGLER_LLM=off, --no-llm).
    model: str | BaseLlm | None
    now: Callable[[], datetime] = field(default=lambda: datetime.now(UTC))
    # data/curated/bundles.yaml as deployed in the image (the bundle watcher's input).
    bundles: BundlesFile = field(default_factory=lambda: default_bundles())
    fetch_timeout_s: float = 90.0
    buffer: ResearchBuffer = field(default_factory=ResearchBuffer)

    def today(self) -> date:
        return self.now().date()

    def reset_buffer(self) -> ResearchBuffer:
        self.buffer = ResearchBuffer(budget=research_budget(self.settings))
        return self.buffer

    async def aclose(self) -> None:
        closer = getattr(self.fetcher, "aclose", None)
        if closer is None:
            return
        try:
            await closer()
        except Exception as exc:  # closing must never hide the run's own outcome
            log.warning("closing %s failed: %s", type(self.fetcher).__name__, exc)


def build_default_research_deps(settings: Settings | None = None) -> ResearchDeps:
    from haggler.llm.models import build_model
    from haggler.pipeline.deps import LazyFetcher, LazyStore

    settings = settings or Settings()
    feasibility = load_feasibility()
    profiles = load_profiles() or synthesize_profiles(
        feasibility, residence=DEFAULT_RESIDENCE, profile=DEFAULT_PROFILE
    )

    def make_fetcher() -> Fetcher:
        from haggler.fetch.client import HttpFetcher

        return HttpFetcher(settings)

    model: Any = build_model(
        settings, settings.research_model, max_calls=settings.research_max_calls
    )
    return ResearchDeps(
        settings=settings,
        catalog=load_catalog(),
        feasibility=feasibility,
        instruments=load_payment_instruments(),
        profiles=profiles,
        bundles=load_bundles(),
        fetcher=LazyFetcher(make_fetcher),
        store=LazyStore(lambda: store_from_settings(settings)),
        model=model,
    )

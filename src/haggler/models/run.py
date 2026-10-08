"""Pipeline data flowing between Workflow nodes, plus run-level reports."""

from __future__ import annotations

from datetime import date, datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

from haggler.models.catalog import Channel, SourceKind
from haggler.models.fx import FxTable
from haggler.models.observations import BillingPeriod, PriceObservation

TaskStatus = Literal[
    "ok",
    "partial",
    "failed",
    "not_available",
    "blocked",
    "robots_disallowed",
    "not_configured",
    "wrong_country",
]
STRUCTURAL_STATUSES = ("not_configured", "robots_disallowed")
LlmReason = Literal["parser_failed", "unmapped_iap", "second_opinion"]


class Task(BaseModel):
    """One fetch: a URL that yields prices for one or more (service, country) pairs."""

    model_config = ConfigDict(extra="forbid")

    task_id: str
    adapter_id: str
    service_ids: list[str]
    countries: list[str]
    channel: Channel
    kind: SourceKind
    url: str
    host: str
    params: dict[str, Any] = Field(default_factory=dict)
    # Future hook: "default" | "gcp:<region>" | "proxy:<cc>".
    egress: str = "default"
    prev_text_hash: str | None = None
    # LLM-extracted observations of the previous run, reused when the page text is unchanged.
    prev_llm_obs: list[dict[str, Any]] = Field(default_factory=list)


class LlmPlanHint(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str
    label: str
    hints: list[str] = Field(default_factory=list)


class LlmJob(BaseModel):
    model_config = ConfigDict(extra="forbid")

    job_id: str
    task_id: str
    reason: LlmReason
    service_id: str
    country: str
    expected_currencies: list[str]
    plans: list[LlmPlanHint]
    channel: Channel
    source_url: str
    page_text: str = Field(max_length=12000)
    priority: int = 0
    # For second opinions: the parser's values to compare against.
    compare_to: list[dict[str, Any]] = Field(default_factory=list)


class TaskResult(BaseModel):
    model_config = ConfigDict(extra="forbid")

    task_id: str
    adapter_id: str
    status: TaskStatus
    service_ids: list[str] = Field(default_factory=list)
    countries: list[str] = Field(default_factory=list)
    channel: Channel = "web"
    http_status: int | None = None
    final_url: str | None = None
    observations: list[PriceObservation] = Field(default_factory=list)
    llm_jobs: list[LlmJob] = Field(default_factory=list)
    text_hash: str | None = None
    raw_ref: str | None = None
    error: str | None = None
    notes: list[str] = Field(default_factory=list)
    attempts: int = 1
    ms: int = 0


# ---- LLM agent output schema (no Optional fields: ADK drops None keys from state) ----


class LlmItem(BaseModel):
    plan_id: str = Field(description="One of the allowed plan ids, or 'other'.")
    plan_label_raw: str = Field(description="Plan name exactly as printed on the page.")
    price_text: str = Field(description="Price exactly as printed, e.g. '129 kr/mån'.")
    currency: str = Field(description="ISO-4217 code of the printed price.")
    billing_period: BillingPeriod
    evidence_snippet: str = Field(description="Verbatim page text around the price, <=200 chars.")


class LlmExtraction(BaseModel):
    page_status: Literal["ok", "no_prices", "wrong_country", "blocked"]
    items: list[LlmItem]
    notes: str


# ---- Run-level outputs ----


class CoverageRow(BaseModel):
    model_config = ConfigDict(extra="forbid")

    service_id: str
    country: str
    channel: Channel
    status: TaskStatus
    adapter_id: str
    failing_since: date | None = None


class Anomaly(BaseModel):
    model_config = ConfigDict(extra="forbid")

    obs_key: str
    rule: str
    detail: str
    previous: str | None = None
    current: str | None = None


class LlmStats(BaseModel):
    model_config = ConfigDict(extra="forbid")

    jobs: int = 0
    calls: int = 0
    errors: int = 0
    skipped_budget: int = 0
    reused: int = 0
    accepted_items: int = 0
    rejected_items: int = 0


class RunReport(BaseModel):
    model_config = ConfigDict(extra="forbid")

    run_id: str
    run_date: date
    started_at: datetime
    finished_at: datetime | None = None
    dry_run: bool = False
    tasks_total: int = 0
    tasks_by_status: dict[str, int] = Field(default_factory=dict)
    obs_by_status: dict[str, int] = Field(default_factory=dict)
    llm: LlmStats = LlmStats()
    anomalies: list[Anomaly] = Field(default_factory=list)
    errors: list[str] = Field(default_factory=list)
    outputs: list[str] = Field(default_factory=list)

    @property
    def success_ratio(self) -> float:
        if not self.tasks_total:
            return 0.0
        # Structural statuses (no catalog config, robots.txt says no) are not failures of the run.
        structural = sum(self.tasks_by_status.get(s, 0) for s in STRUCTURAL_STATUSES)
        considered = self.tasks_total - structural
        if considered <= 0:
            return 1.0
        good = sum(self.tasks_by_status.get(s, 0) for s in ("ok", "partial", "not_available"))
        return good / considered


class Snapshot(BaseModel):
    model_config = ConfigDict(extra="forbid")

    schema_version: int = 1
    run_id: str
    as_of: date
    observations: list[PriceObservation]
    fx: FxTable
    coverage: list[CoverageRow]


# ---- Persisted pipeline state (latest/state.json) ----


class KeyState(BaseModel):
    """Per obs_key memory across runs (carry-forward and quarantine confirmation)."""

    model_config = ConfigDict(extra="forbid")

    # Consecutive runs where the key was missing because its task failed.
    missing_runs: int = 0
    # Consecutive runs where the task succeeded but this plan was absent from the page.
    absent_ok_runs: int = 0
    # Quarantined new value awaiting confirmation, as "amount|currency|period".
    pending_value: str | None = None
    pending_since: date | None = None


class LlmPairCheck(BaseModel):
    """When the LLM last answered one (service, country) pair of a task, and for which text."""

    model_config = ConfigDict(extra="forbid")

    text_hash: str
    on: date


class TaskState(BaseModel):
    model_config = ConfigDict(extra="forbid")

    text_hash: str | None = None
    # LLM-extracted observations (PriceObservation dumps) of the pairs the LLM answered for
    # text_hash (never quarantined rows): reused while text_hash is unchanged.
    llm_obs: list[dict[str, Any]] = Field(default_factory=list)
    # text_hash once the LLM has answered every (service, country) pair the page needed (it
    # replaces the "_llm_checked_hash" marker older files keep inside llm_obs).
    llm_checked_hash: str | None = None
    # "service_id:CC" -> last LLM answer for that pair. Answers for today's text are not asked
    # again; a capped multi-country task asks the least recently checked pairs first.
    llm_pairs: dict[str, LlmPairCheck] = Field(default_factory=dict)
    last_status: TaskStatus | None = None
    failing_since: date | None = None


class PipelineState(BaseModel):
    model_config = ConfigDict(extra="forbid")

    schema_version: int = 1
    run_id: str | None = None
    as_of: date | None = None
    keys: dict[str, KeyState] = Field(default_factory=dict)
    tasks: dict[str, TaskState] = Field(default_factory=dict)

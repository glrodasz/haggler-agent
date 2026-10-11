"""The daily Workflow: a straight chain of nodes built as closures over one Deps.

    START -> plan_tasks -> fetch_and_parse -> llm_extract -> fx_normalize -> validate
          -> adjudicate -> persist

WHY a straight chain: in ADK 2.11 a JoinNode after exclusive routing hangs the run and two routes
into one merge node produce two outputs, so every node always runs and passes its input through
when it has nothing to do.

Data flow rules:
  * Raw HTML never travels through node outputs or session state (ADK copies both into the
    session event log): it goes to the Store under raw/... and TaskResult.raw_ref keeps the key.
  * ctx.state only holds the small run metadata ctx.state["run"].
  * Bulky per-run data that no node needs as input (previous published files, the LLM log, the
    page texts of the LLM jobs) lives in Deps.buffer, reset by plan_tasks.
  * Nothing escapes a parallel item except TransientFetchError on a non-final attempt: ADK's
    parallel_worker is fail-fast, so every other failure becomes a TaskResult record. Likewise
    every LLM job failure (timeout, empty answer, budget, bug) becomes a JobOutcome error.
  * CPU-heavy page work (rendering, adapter.parse, job planning) runs in worker threads.
"""

from __future__ import annotations

import asyncio
import base64
import binascii
import hashlib
import json
import logging
import re
import time
from collections import Counter
from collections.abc import Collection, Iterable, Mapping
from dataclasses import dataclass, field
from datetime import date, datetime
from decimal import Decimal, InvalidOperation
from typing import Any

import pydantic
from google.adk import Agent, Context, Workflow
from google.adk.workflow import START, RetryConfig, node

from haggler import validate as rules
from haggler.fetch import (
    BlockedError,
    FetchError,
    RawResponse,
    RobotsDisallowedError,
    TransientFetchError,
)
from haggler.llm.runner import AgentJob, run_agent_jobs
from haggler.models.catalog import Catalog, CountryDef, ServiceDef, SourceKind
from haggler.models.fx import FxTable
from haggler.models.observations import ObservationDraft, PriceObservation, PublishedPrice
from haggler.models.run import (
    Anomaly,
    CoverageRow,
    KeyState,
    LlmExtraction,
    LlmItem,
    LlmJob,
    LlmPairCheck,
    LlmPlanHint,
    PipelineState,
    RunReport,
    Snapshot,
    Task,
    TaskResult,
    TaskState,
    TaskStatus,
    pair_key,
)
from haggler.money import Money, parse_money
from haggler.pipeline.agents import build_price_extractor
from haggler.pipeline.deps import Deps, RunBuffer
from haggler.sources.base import ParseResult
from haggler.sources.registry import get_adapter
from haggler.store import Store, dumps, dumps_lines, read_json, read_text_gz, write_text_gz
from haggler.text import contains_normalized, html_to_lines, price_windows, text_hash

log = logging.getLogger(__name__)

WORKFLOW_NAME = "daily_prices"
FETCH_ATTEMPTS = 3
# Services billed by Apple itself: the storefront currency is also valid (validate rule).
APPLE_BILLED = frozenset({"icloud_plus", "apple_one", "apple_music", "apple_tv"})
FLAG_APPLE_BILLING = rules.FLAG_APPLE_BILLING
FLAG_IAP_MARKUP = "iap_maybe_marked_up"
FLAG_SECOND_OPINION = rules.FLAG_SECOND_OPINION_AGREES
CONFIDENCE: dict[str, float] = {"official": 0.9, "secondary": 0.6, "llm": 0.7}
FAILING_STATUSES = frozenset({"failed", "blocked", "robots_disallowed", "wrong_country"})
# A multi-country page (iCloud+) yields at most this many parser_failed jobs per run; the next
# runs ask the remaining countries (least recently checked first).
MAX_JOBS_PER_TASK = 5
MAX_NOTES = 20
FOCUS_AFTER = 14
# Marker entry older state.json files keep inside TaskState.llm_obs (now llm_checked_hash).
LEGACY_LLM_CHECKED_KEY = "_llm_checked_hash"
# Finish reasons of an empty answer worth one more try (a SAFETY or RECITATION block repeats).
# LLM page_status answers that make the job's (service, country) pair count as that failure.
PAIR_FAILURE_PAGE_STATUSES = frozenset({"wrong_country", "blocked"})
LATEST_FILES = ("prices.json", "fx.json", "coverage.json", "review_queue.json", "state.json")

_PARSE_TO_TASK: dict[str, str] = {
    "ok": "ok",
    "partial": "partial",
    "no_prices": "failed",
    "wrong_country": "wrong_country",
    "not_available": "not_available",
    "blocked": "blocked",
}
_STATUS_PREFERENCE = {"ok": 0, "partial": 1, "not_available": 2}


# ============================================================================ small helpers


def safe_name(value: str) -> str:
    """Store/file-safe form of a task id: ':' would break GitHub artifact uploads (probe-out)."""
    return re.sub(r"[^A-Za-z0-9_.-]+", "_", value.replace(":", "__"))


def safe_id(value: str) -> str:
    """Identifier for ADK run ids (node paths use '/' and '@')."""
    return re.sub(r"[^A-Za-z0-9_]+", "_", value).strip("_")


def job_id_for(task_id: str, reason: str, service_id: str, cc: str, *, scoped: bool) -> str:
    """LLM job id; the (service, country) suffix only for tasks that cover several of them."""
    return safe_id(f"{task_id}.{reason}" + (f".{service_id}.{cc}" if scoped else ""))


def state_bytes(state: PipelineState) -> bytes:
    """Compact state.json: default-valued fields are omitted (they load back as defaults)."""
    data = state.model_dump(mode="json", exclude_defaults=True)
    data["schema_version"] = state.schema_version
    return json.dumps(data, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()


def raw_key(run_date: str, adapter_id: str, task_id: str, ext: str) -> str:
    return f"raw/{run_date}/{adapter_id}/{safe_name(task_id)}.{ext}.gz"


def run_prefix(run_date: str, run_id: str) -> str:
    return f"runs/{run_date}/{run_id}"


def _notes(items: Iterable[str]) -> list[str]:
    return [str(n)[:300] for n in items][:MAX_NOTES]


def _with_flags(flags: list[str], extra: Iterable[str]) -> list[str]:
    out = list(flags)
    for f in extra:
        if f not in out:
            out.append(f)
    return out


def _sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _jsonl(rows: Iterable[Any]) -> bytes:
    return "".join(
        json.dumps(r, ensure_ascii=False, sort_keys=True, default=str) + "\n" for r in rows
    ).encode()


def _country(cat: Catalog, cc: str) -> CountryDef | None:
    try:
        return cat.country(cc)
    except KeyError:
        return None


def _service(cat: Catalog, service_id: str) -> ServiceDef | None:
    try:
        return cat.service(service_id)
    except KeyError:
        return None


def source_kind(cat: Catalog, service_id: str, adapter_id: str, channel: str) -> SourceKind:
    svc = _service(cat, service_id)
    for src in svc.sources if svc else []:
        if src.adapter == adapter_id and src.channel == channel:
            return src.kind
    return "official"


def expected_currencies(
    cat: Catalog, service_id: str, cc: str, channel: str, adapter_id: str | None = None
) -> list[str]:
    """The currencies validate accepts for a row: the country's, the storefront currency for
    Apple-billed services, and params.currency_overrides[cc] (a code or a list) of the catalog
    sources of `adapter_id` on that channel."""
    country = _country(cat, cc)
    if country is None:
        return []
    allowed = list(country.expected_currencies(channel))  # type: ignore[arg-type]
    extra = [country.appstore_currency] if service_id in APPLE_BILLED else []
    svc = _service(cat, service_id) if adapter_id else None
    for src in svc.sources if svc else []:
        if src.adapter != adapter_id or src.channel != channel:
            continue
        pinned = (src.params.get("currency_overrides") or {}).get(cc)
        if pinned:
            extra.extend([pinned] if isinstance(pinned, str) else [str(c) for c in pinned])
    for currency in extra:
        if currency and currency not in allowed:
            allowed.append(currency)
    return allowed


def llm_flags(service_id: str, channel: str) -> list[str]:
    """Flags an adapter would have put on the same price (validate relies on apple_billing)."""
    flags: list[str] = []
    if service_id in APPLE_BILLED:
        flags.append(FLAG_APPLE_BILLING)
    if channel == "ios_iap":
        flags.append(FLAG_IAP_MARKUP)
    return flags


def plan_hints(svc: ServiceDef) -> list[LlmPlanHint]:
    hints: list[LlmPlanHint] = []
    for p in svc.plans:
        words: list[str] = []
        for w in [*p.aliases, p.label_es, *(["with ads"] if p.ads else [])]:
            if w and w not in words and w.casefold() != p.label_en.casefold():
                words.append(w)
        hints.append(LlmPlanHint(id=p.id, label=p.label_en, hints=words))
    return hints


def page_text_lines(text: str) -> list[str]:
    """Visible lines of a page without header/nav/footer chrome (short "129 kr" lines kept)."""
    try:
        return html_to_lines(text, drop_chrome=True)
    except Exception:  # malformed markup must not kill the task
        return [ln.strip() for ln in text.splitlines() if ln.strip()]


def render_page(text: str, *, is_json: bool) -> tuple[list[str], str]:
    """(visible lines, text hash) of a fetched body; CPU-bound, so callers run it in a thread."""
    if is_json:
        return [], text_hash(text)
    lines = page_text_lines(text)
    return lines, text_hash("\n".join(lines))


def split_pair_key(key: str) -> tuple[str, str]:
    service_id, _, cc = key.rpartition(":")
    return service_id, cc


def focus_text(lines: list[str], country: CountryDef, expected: list[str], *, multi: bool) -> str:
    """LLM input: lines around prices; for multi-country pages only the part naming `country`."""
    if multi:
        names = [n for n in {country.name_en, *country.aliases_en} if n]
        pats = [re.compile(rf"(?<!\w){re.escape(n)}(?!\w)", re.IGNORECASE) for n in names]
        hits = [i for i, ln in enumerate(lines) if any(p.search(ln) for p in pats)]
        if not hits:
            return ""
        keep = sorted(
            {j for i in hits for j in range(max(0, i - 1), min(len(lines), i + FOCUS_AFTER))}
        )
        lines = [lines[j] for j in keep]
    return price_windows(lines, expected)[:12000]


def has_default_plan(observations: Iterable[PriceObservation], svc: ServiceDef, cc: str) -> bool:
    return any(
        o.service_id == svc.id and o.country == cc and o.plan_id == svc.default_plan
        for o in observations
    )


def coverage_status(result: TaskResult, cat: Catalog) -> str:
    """Task status recomputed from what we hold now (parser + LLM rows); only ever upgrades a
    failed/partial parse, never downgrades."""
    if result.status not in ("failed", "partial") or not result.observations:
        return result.status
    if result.status == "failed" and result.error != "no_prices":
        return result.status
    pairs = [(s, c) for s in result.service_ids for c in result.countries]
    covered = 0
    for s, c in pairs:
        svc = _service(cat, s)
        if svc is not None and has_default_plan(result.observations, svc, c):
            covered += 1
    if pairs and covered == len(pairs):
        return "ok"
    return "partial"


# ============================================================================ filters / planning


@dataclass
class Filters:
    date: date | None = None
    services: list[str] = field(default_factory=list)
    countries: list[str] = field(default_factory=list)
    adapters: list[str] = field(default_factory=list)
    dry_run: bool = False

    @property
    def filtered(self) -> bool:
        return bool(self.services or self.countries or self.adapters)

    def as_dict(self) -> dict[str, Any]:
        return {
            "date": self.date.isoformat() if self.date else None,
            "services": self.services,
            "countries": self.countries,
            "adapters": self.adapters,
            "dry_run": self.dry_run,
        }


def _decode_envelope_data(data: Any) -> Any:
    """Pub/Sub-style {"data": ...}: a dict, a JSON string or base64-encoded JSON."""
    if isinstance(data, dict):
        return data
    if not isinstance(data, str) or not data.strip():
        return {}
    text = data.strip()
    if not text.startswith("{"):
        try:
            text = base64.b64decode(text, validate=True).decode()
        except (binascii.Error, UnicodeDecodeError) as exc:
            raise ValueError(f"envelope data is neither JSON nor base64 JSON: {exc}") from exc
    return json.loads(text) if text.strip() else {}


def _as_list(value: Any, *, upper: bool = False) -> list[str]:
    if value is None:
        return []
    items = value.split(",") if isinstance(value, str) else list(value)
    out: list[str] = []
    for item in items:
        s = str(item).strip()
        if s and s != "*":
            s = s.upper() if upper else s
            if s not in out:
                out.append(s)
    return out


def _as_bool(value: Any) -> bool:
    if isinstance(value, str):
        return value.strip().lower() in {"1", "true", "yes", "on"}
    return bool(value)


def parse_filters(node_input: Any) -> Filters:
    """Filters from the START message. Empty or free text ("go") = defaults; a JSON object
    (optionally wrapped in a Pub/Sub {"data": ...} envelope) selects date/services/countries/
    adapters/dry_run."""
    data: Any = node_input
    if isinstance(data, str):
        text = data.strip()
        if not text.startswith("{"):
            return Filters()
        try:
            data = json.loads(text)
        except json.JSONDecodeError as exc:
            raise ValueError(f"run request is not valid JSON: {exc}") from exc
    if not isinstance(data, dict):
        return Filters()
    if "data" in data and not ({"services", "countries", "date"} & set(data)):
        data = _decode_envelope_data(data["data"])
        if not isinstance(data, dict):
            return Filters()
    raw_date = data.get("date")
    return Filters(
        date=date.fromisoformat(str(raw_date)) if raw_date else None,
        services=_as_list(data.get("services")),
        countries=_as_list(data.get("countries"), upper=True),
        adapters=_as_list(data.get("adapters")),
        dry_run=_as_bool(data.get("dry_run", False)),
    )


def make_run_id(run_date: date, started_at: datetime, filters: Filters) -> str:
    seed = f"{started_at.isoformat()}|{json.dumps(filters.as_dict(), sort_keys=True)}"
    return f"{run_date.isoformat()}-{hashlib.sha256(seed.encode()).hexdigest()[:6]}"


def plan_all(deps: Deps, filters: Filters, errors: list[str]) -> list[Task]:
    cat = deps.catalog
    known_services = {s.id for s in cat.services}
    for sid in filters.services:
        if sid not in known_services:
            errors.append(f"filter: unknown service {sid}")
    for cc in filters.countries:
        if _country(cat, cc) is None:
            errors.append(f"filter: unknown country {cc}")
    tasks: list[Task] = []
    seen: set[str] = set()
    for svc in cat.services:
        if filters.services and svc.id not in filters.services:
            continue
        for src in svc.sources:
            if filters.adapters and src.adapter not in filters.adapters:
                continue
            try:
                planned = get_adapter(src.adapter).plan(svc, src, cat)
            except Exception as exc:  # one broken adapter must not stop the others
                errors.append(f"plan {svc.id}/{src.adapter}: {type(exc).__name__}: {exc}")
                continue
            for task in planned:
                if filters.countries:
                    keep = [c for c in task.countries if c in filters.countries]
                    if not keep:
                        continue
                    if keep != task.countries:
                        task = task.model_copy(update={"countries": keep})
                if deps.task_filter is not None and not deps.task_filter(task):
                    continue
                if task.task_id in seen:
                    errors.append(f"plan: duplicate task id {task.task_id}")
                    continue
                seen.add(task.task_id)
                tasks.append(task)
    return tasks


def interleave_by_host(tasks: list[Task]) -> list[Task]:
    """Round-robin over hosts so parallel workers spread over hosts instead of queueing behind one
    host's rate limit (the fetcher serialises requests per host)."""
    queues: dict[str, list[Task]] = {}
    for t in tasks:
        queues.setdefault(t.host or "", []).append(t)
    out: list[Task] = []
    while any(queues.values()):
        for host in list(queues):
            if queues[host]:
                out.append(queues[host].pop(0))
    return out


def load_previous(store: Store, buf: RunBuffer) -> None:
    """Previous published files. A missing file means a first run; an unreadable prices.json
    raises (silently starting from nothing would drop every published price)."""
    rows = read_json(store, "latest/prices.json") or []
    if not isinstance(rows, list):
        raise ValueError("latest/prices.json is not a JSON array")
    for row in rows:
        try:
            p = PublishedPrice.model_validate(row)
        except pydantic.ValidationError as exc:
            buf.errors.append(f"latest/prices.json: skipped invalid row: {str(exc)[:200]}")
            continue
        buf.previous_prices[p.obs_key] = p
    for key, loader in (
        (
            "latest/state.json",
            lambda d: setattr(buf, "previous_state", PipelineState.model_validate(d)),
        ),
        ("latest/fx.json", lambda d: setattr(buf, "previous_fx", FxTable.model_validate(d))),
    ):
        try:
            data = read_json(store, key)
            if data is not None:
                loader(data)
        except (ValueError, TypeError, pydantic.ValidationError) as exc:
            buf.errors.append(f"{key}: ignored ({type(exc).__name__}: {str(exc)[:200]})")
    try:
        cov = read_json(store, "latest/coverage.json") or []
        buf.previous_coverage = [r for r in cov if isinstance(r, dict)]
    except ValueError as exc:
        buf.errors.append(f"latest/coverage.json: ignored ({exc})")


# ============================================================================ fetch + parse


def to_observation(
    draft: ObservationDraft,
    task: Task,
    cat: Catalog,
    *,
    source_url: str,
    observed_at: datetime,
    content_hash: str,
    extraction: str,
    confidence: float,
) -> PriceObservation:
    country = _country(cat, draft.country)
    vat = getattr(country.vat_included, task.channel, None) if country else None
    # The source itself can say how the price is taxed (Microsoft's TaxType).
    if "taxes_not_included" in draft.flags:
        vat = False
    elif "vat_included" in draft.flags:
        vat = True
    return PriceObservation.from_draft(
        draft,
        channel=task.channel,
        source_url=source_url,
        source_kind=task.kind,
        adapter_id=task.adapter_id,
        observed_at=observed_at,
        content_hash=content_hash,
        extraction=extraction,  # type: ignore[arg-type]
        vat_included=vat,
        confidence=confidence,
    )


def reuse_llm_observations(
    task: Task,
    existing: set[str],
    observed_at: datetime,
    content_hash: str,
    *,
    pairs: Collection[tuple[str, str]],
) -> list[PriceObservation]:
    """Previous LLM rows of the (service, country) pairs in `pairs` (those the LLM answered for
    this very page text), re-emitted for today; keys the parser already has are skipped."""
    out: list[PriceObservation] = []
    for dump in task.prev_llm_obs:
        if LEGACY_LLM_CHECKED_KEY in dump:
            continue
        try:
            obs = PriceObservation.model_validate(dump)
        except pydantic.ValidationError:
            continue
        if obs.obs_key in existing or obs.country not in task.countries:
            continue
        if (obs.service_id, obs.country) not in pairs:
            continue
        existing.add(obs.obs_key)
        out.append(
            obs.model_copy(
                update={
                    "observed_at": observed_at,
                    "reused": True,
                    "status": "accepted",
                    "monthly_eur": None,
                    "content_hash": content_hash,
                    "flags": [f for f in obs.flags if f not in rules.DERIVED_FLAGS],
                }
            )
        )
    return out


@dataclass
class JobPlan:
    """LLM jobs planned for one task."""

    jobs: list[LlmJob] = field(default_factory=list)
    # MAX_JOBS_PER_TASK left pairs for a later run: the task is not fully checked today.
    truncated: bool = False
    notes: list[str] = field(default_factory=list)


def make_llm_jobs(
    task: Task,
    pr: ParseResult,
    lines: list[str],
    observations: list[PriceObservation],
    cat: Catalog,
    source_url: str,
    *,
    answered: Collection[tuple[str, str]] = frozenset(),
    last_checked: Mapping[tuple[str, str], date] | None = None,
) -> JobPlan:
    """The adapter's own LLM requests, plus parser_failed jobs for (service, country) pairs whose
    default plan the parser did not find. Pure (no I/O), so it can run in a worker thread.

    * `answered` pairs (the LLM already answered them for this page text) get no job.
    * An unmapped_iap request means the adapter read the page and hands over only the lines it
      could not map: no generic job asks about the same page again.
    * A multi-country page gets at most MAX_JOBS_PER_TASK generic jobs per run, the pairs with
      the oldest `last_checked` date (never checked first), so the cap rotates over the
      countries across runs instead of starving the same ones.
    * Secondary datasets never go to the LLM.
    """
    plan = JobPlan()
    if task.kind == "secondary":
        return plan
    scoped = len(task.countries) > 1 or len(task.service_ids) > 1
    checked_on = last_checked or {}

    def add(svc: ServiceDef, cc: str, reason: str, text: str, priority: int) -> bool:
        if not text.strip():
            return False
        try:
            job = LlmJob(
                job_id=job_id_for(task.task_id, reason, svc.id, cc, scoped=scoped),
                task_id=task.task_id,
                reason=reason,  # type: ignore[arg-type]
                service_id=svc.id,
                country=cc,
                expected_currencies=expected_currencies(
                    cat, svc.id, cc, task.channel, task.adapter_id
                ),
                plans=plan_hints(svc),
                channel=task.channel,
                source_url=source_url,
                page_text=text[:12000],
                priority=priority,
            )
        except pydantic.ValidationError as exc:  # e.g. an adapter request with a bad reason
            plan.notes.append(f"llm job {svc.id}/{cc} ({reason}) not created: {str(exc)[:200]}")
            return False
        plan.jobs.append(job)
        return True

    covered: set[tuple[str, str]] = set(answered)
    for req in pr.llm_requests:
        svc = _service(cat, req.service_id)
        if svc is None or req.country not in task.countries or _country(cat, req.country) is None:
            continue
        pair = (req.service_id, req.country)
        if pair not in answered:
            priority = 3 if req.reason == "parser_failed" else 1
            add(svc, req.country, req.reason, req.page_text, priority)
        covered.add(pair)

    if any(req.reason == "unmapped_iap" for req in pr.llm_requests):
        return plan
    if pr.status not in ("no_prices", "partial") or not lines:
        return plan
    multi = len(task.countries) > 1
    candidates: list[tuple[ServiceDef, CountryDef]] = []
    for sid in task.service_ids:
        svc = _service(cat, sid)
        if svc is None:
            continue
        for cc in task.countries:
            country = _country(cat, cc)
            if country is None or (sid, cc) in covered:
                continue
            if has_default_plan(observations, svc, cc):
                continue
            candidates.append((svc, country))
    if multi:  # stable sort: ties keep the catalog order
        candidates.sort(key=lambda c: checked_on.get((c[0].id, c[1].cc), date.min))
    added = 0
    for n, (svc, country) in enumerate(candidates):
        if multi and added >= MAX_JOBS_PER_TASK:
            plan.truncated = True
            plan.notes.append(
                f"llm: {len(candidates) - n} pair(s) left for a later run "
                f"(at most {MAX_JOBS_PER_TASK} jobs per task and run)"
            )
            break
        expected = expected_currencies(cat, svc.id, country.cc, task.channel, task.adapter_id)
        text = focus_text(lines, country, expected, multi=multi)
        priority = 1 if multi else (3 if pr.status == "no_prices" else 2)
        if add(svc, country.cc, "parser_failed", text, priority):
            added += 1
    return plan


async def process_task(
    deps: Deps, task: Task, run: dict[str, Any], *, attempt: int, max_attempts: int
) -> TaskResult:
    """Fetch, store raw, parse one task. Raises only TransientFetchError, and only while another
    attempt is left (the node's RetryConfig retries it)."""
    started = time.monotonic()
    base: dict[str, Any] = {
        "task_id": task.task_id,
        "adapter_id": task.adapter_id,
        "service_ids": task.service_ids,
        "countries": task.countries,
        "channel": task.channel,
        "attempts": attempt,
    }

    def done(**kw: Any) -> TaskResult:
        kw.setdefault("ms", int((time.monotonic() - started) * 1000))
        if "notes" in kw:
            kw["notes"] = _notes(kw["notes"])
        return TaskResult(**{**base, **kw})

    missing = task.params.get("not_configured")
    if missing:
        fields = ", ".join(missing) if isinstance(missing, list) else str(missing)
        return done(
            status="not_configured", attempts=0, notes=[f"missing catalog fields: {fields}"]
        )
    if not task.url:
        return done(status="not_configured", attempts=0, notes=["task has no URL"])

    accept = str(task.params.get("accept") or "text/html")
    try:
        resp: RawResponse = await asyncio.wait_for(
            deps.fetcher.fetch(task.url, accept=accept, egress=task.egress),
            timeout=deps.fetch_timeout_s,
        )
    except TimeoutError as exc:
        if attempt < max_attempts:
            raise TransientFetchError(
                f"timeout after {deps.fetch_timeout_s}s", url=task.url
            ) from exc
        return done(status="failed", error=f"transient: timeout after {deps.fetch_timeout_s}s")
    except TransientFetchError as exc:
        if attempt < max_attempts:
            raise
        return done(status="failed", http_status=exc.status, error=f"transient: {exc}")
    except BlockedError as exc:
        return done(status="blocked", http_status=exc.status, error=str(exc))
    except RobotsDisallowedError as exc:
        return done(status="robots_disallowed", http_status=exc.status, error=str(exc))
    except FetchError as exc:
        status = "not_available" if exc.status in (404, 410) else "failed"
        return done(status=status, http_status=exc.status, error=str(exc))
    except Exception as exc:  # unexpected fetcher bug: record it, never kill the run
        return done(status="failed", error=f"{type(exc).__name__}: {exc}")

    notes: list[str] = []
    is_json = accept == "application/json"
    key = raw_key(run["run_date"], task.adapter_id, task.task_id, "json" if is_json else "html")
    raw_ref: str | None = key
    if run.get("dry_run"):
        deps.buffer.raw_cache[key] = resp.text
    else:
        try:  # in a thread: a GCS upload must not stall the other fetches on the event loop
            await asyncio.to_thread(write_text_gz, deps.store, key, resp.text)
        except Exception as exc:  # audit copy only: the prices are still usable
            raw_ref = None
            notes.append(f"raw page not stored: {type(exc).__name__}: {exc}")

    try:
        parsed = await parse_fetched(deps, task, resp, is_json=is_json)
        result = done(
            status=parsed.status,
            http_status=resp.status_code,
            final_url=resp.final_url,
            observations=parsed.observations,
            # Node outputs carry the jobs without their page text (it stays in Deps.buffer).
            llm_jobs=[j.model_copy(update={"page_text": ""}) for j in parsed.jobs],
            text_hash=parsed.text_hash,
            raw_ref=raw_ref,
            error=parsed.error,
            pair_status=parsed.pair_status,
            notes=[*notes, *parsed.notes],
        )
    except Exception as exc:  # an internal bug fails this task only, never the whole run
        log.warning("task %s: internal error after fetch", task.task_id, exc_info=True)
        return done(
            status="failed",
            http_status=resp.status_code,
            final_url=resp.final_url,
            raw_ref=raw_ref,
            error=f"internal: {type(exc).__name__}: {str(exc)[:300]}",
            notes=notes,
        )
    buf = deps.buffer
    buf.llm_jobs[task.task_id] = parsed.jobs
    if parsed.checked:
        buf.llm_checked.add(task.task_id)
    if parsed.truncated:
        buf.llm_truncated.add(task.task_id)
    if parsed.reused:
        buf.llm_stats.reused += 1
    return result


@dataclass
class ParsedPage:
    """What parse_fetched makes of one fetched page (process_task turns it into a TaskResult)."""

    status: str
    text_hash: str
    observations: list[PriceObservation] = field(default_factory=list)
    jobs: list[LlmJob] = field(default_factory=list)
    error: str | None = None
    notes: list[str] = field(default_factory=list)
    # No LLM work left for this page text / jobs capped per task / earlier LLM work reused.
    checked: bool = False
    truncated: bool = False
    reused: bool = False
    # Pairs an LLM answer reused for this text says fail (page_status wrong_country / blocked).
    failed_pairs: dict[tuple[str, str], TaskStatus] = field(default_factory=dict)
    pair_status: dict[str, TaskStatus] = field(default_factory=dict)


async def parse_fetched(deps: Deps, task: Task, resp: RawResponse, *, is_json: bool) -> ParsedPage:
    """Render, parse and plan the LLM work of one fetched page. The CPU-heavy steps (rendering,
    adapter.parse, job planning) run in worker threads so they do not freeze the event loop
    (the other fetches, rate-limit timers and timeouts). Has no side effects on Deps.buffer."""
    cat = deps.catalog
    lines, hash_ = await asyncio.to_thread(render_page, resp.text, is_json=is_json)
    observed_at = resp.fetched_at or deps.now()
    source_url = resp.final_url or task.url

    try:
        adapter = get_adapter(task.adapter_id)
        pr: Any = await asyncio.to_thread(adapter.parse, task, resp, cat)  # pure by contract
        if not isinstance(pr, ParseResult):
            raise TypeError(f"parse() returned {type(pr).__name__}, not a ParseResult")
    except Exception as exc:  # a parser crash is a parser failure: the LLM fallback applies
        pr = ParseResult(status="no_prices", notes=[f"parser crashed: {type(exc).__name__}: {exc}"])
    page = ParsedPage(
        status=_PARSE_TO_TASK.get(pr.status, "failed"),
        text_hash=hash_,
        error="no_prices" if pr.status == "no_prices" else None,
        notes=list(pr.notes),
    )

    drafts = pr.observations
    if drafts and pr.status in ("wrong_country", "blocked", "not_available"):
        page.notes.append(f"dropped {len(drafts)} draft(s): page status {pr.status}")
        drafts = []
    confidence = CONFIDENCE.get(task.kind, 0.6)
    for d in drafts:
        try:
            page.observations.append(
                to_observation(
                    d,
                    task,
                    cat,
                    source_url=source_url,
                    observed_at=observed_at,
                    content_hash=hash_,
                    extraction="parser",
                    confidence=confidence,
                )
            )
        except Exception as exc:  # one bad draft must not cost the task its other prices
            page.notes.append(
                f"bad draft {getattr(d, 'plan_id', '?')}/{getattr(d, 'country', '?')}: "
                f"{type(exc).__name__}"
            )

    if pr.status in ("no_prices", "partial") or pr.llm_requests:
        await plan_llm_work(deps, task, pr, lines, page, observed_at, source_url)

    result = TaskResult(
        task_id=task.task_id,
        adapter_id=task.adapter_id,
        status=page.status,  # type: ignore[arg-type]
        service_ids=task.service_ids,
        countries=task.countries,
        channel=task.channel,
        observations=page.observations,
        error=page.error,
    )
    result.status = coverage_status(result, cat)  # type: ignore[assignment]
    for (sid, cc), status in page.failed_pairs.items():
        mark_pair_failure(result, sid, cc, status)
    page.status, page.pair_status = result.status, result.pair_status
    return page


async def plan_llm_work(
    deps: Deps,
    task: Task,
    pr: ParseResult,
    lines: list[str],
    page: ParsedPage,
    observed_at: datetime,
    source_url: str,
) -> None:
    """Reuse what the LLM answered for this very page text (TaskState.llm_pairs), and plan jobs
    for the pairs still unanswered. Jobs are planned even on a fully checked page, so a parser
    that newly loses a plan on an unchanged page still gets the fallback."""
    prev = deps.buffer.previous_state.tasks.get(task.task_id)
    same_page = prev is not None and task.prev_text_hash == page.text_hash
    answered: set[tuple[str, str]] = set()
    last_checked: dict[tuple[str, str], date] = {}
    if prev is not None:
        for key, check in prev.llm_pairs.items():
            pair = split_pair_key(key)
            last_checked[pair] = check.on
            if same_page and check.text_hash == page.text_hash:
                answered.add(pair)
                if check.page_status:
                    page.failed_pairs[pair] = check.page_status
    reused: list[PriceObservation] = []
    if answered:
        existing = {o.obs_key for o in page.observations}
        reused = reuse_llm_observations(task, existing, observed_at, page.text_hash, pairs=answered)
        page.observations.extend(reused)
    plan = await asyncio.to_thread(
        make_llm_jobs,
        task,
        pr,
        lines,
        page.observations,
        deps.catalog,
        source_url,
        answered=answered,
        last_checked=last_checked,
    )
    page.jobs, page.truncated = plan.jobs, plan.truncated
    page.notes.extend(plan.notes)
    page.checked = not plan.jobs and not plan.truncated
    was_checked = same_page and prev is not None and prev.llm_checked_hash == page.text_hash
    page.reused = bool(reused) or (was_checked and page.checked)
    if answered:
        page.notes.append(
            f"llm: page unchanged, reused {len(reused)} observation(s), {len(plan.jobs)} new job(s)"
        )


# ============================================================================ LLM


@dataclass
class JobOutcome:
    job: LlmJob
    stage: str
    output: dict[str, Any] | None = None
    error: str | None = None
    skipped: str | None = None
    accepted: list[tuple[LlmItem, Money]] = field(default_factory=list)
    rejected: list[dict[str, Any]] = field(default_factory=list)
    duplicates: list[dict[str, Any]] = field(default_factory=list)

    def log_line(self) -> dict[str, Any]:
        return {
            "stage": self.stage,
            "job": self.job.model_dump(mode="json"),
            "output": self.output,
            "error": self.error,
            "skipped": self.skipped,
            "accepted": [
                {**item.model_dump(mode="json"), "amount": str(money.amount)}
                for item, money in self.accepted
            ],
            "rejected": self.rejected,
            "duplicates": self.duplicates,
        }


def post_check(job: LlmJob, extraction: LlmExtraction) -> tuple[list, list]:
    """Code-side guarantees for LLM items: allowed plan, expected currency, parseable positive
    price, and both price_text and evidence present (normalized) in the text the model saw."""
    allowed = {p.id for p in job.plans}
    accepted: list[tuple[LlmItem, Money]] = []
    rejected: list[dict[str, Any]] = []
    seen: set[tuple[str, str]] = set()
    for item in extraction.items:
        reason: str | None = None
        money: Money | None = None
        currency = item.currency.strip().upper()
        if item.plan_id not in allowed:
            reason = "plan_not_allowed"
        elif currency not in job.expected_currencies:
            reason = "unexpected_currency"
        else:
            money = parse_money(item.price_text, job.expected_currencies)
            if money is None:
                reason = "price_unparseable"
            elif money.amount <= 0:
                reason = "non_positive"
            elif money.currency != currency:
                reason = "currency_mismatch"
            elif not contains_normalized(job.page_text, item.price_text):
                reason = "price_not_on_page"
            elif not item.evidence_snippet.strip() or not contains_normalized(
                job.page_text, item.evidence_snippet
            ):
                reason = "evidence_not_on_page"
            elif not contains_normalized(item.evidence_snippet, item.price_text):
                reason = "price_not_in_evidence"
            elif (item.plan_id, item.billing_period) in seen:
                reason = "duplicate_plan"
        if reason or money is None:
            rejected.append({"item": item.model_dump(mode="json"), "reason": reason})
            continue
        seen.add((item.plan_id, item.billing_period))
        accepted.append((item.model_copy(update={"currency": currency}), money))
    return accepted, rejected


async def run_llm_jobs(
    deps: Deps, ctx: Context, agent: Agent, jobs: list[LlmJob], *, stage: str
) -> list[JobOutcome]:
    """One agent run per job, at most settings.llm_concurrency at a time, each capped at
    settings.llm_job_timeout_s. Every failure (malformed output after the agent's retry, model
    errors, timeouts, empty answers, the call budget, bugs in the post-checks) stays inside its
    JobOutcome."""
    outcomes = [JobOutcome(job=job, stage=stage) for job in jobs]
    callable_ = [o for o in outcomes if o.job.page_text.strip()]
    for o in outcomes:
        if not o.job.page_text.strip():
            o.error = "no page text for this job (the run buffer was reset)"
    calls = await run_agent_jobs(
        ctx,
        agent,
        [
            AgentJob(
                key=f"{stage}_{o.job.job_id}",
                payload=json.dumps(
                    o.job.model_dump(mode="json", exclude={"compare_to"}), ensure_ascii=False
                ),
            )
            for o in callable_
        ],
        budget=deps.buffer.budget,
        concurrency=deps.settings.llm_concurrency,
        timeout_s=deps.settings.llm_job_timeout_s,
    )
    for outcome, call in zip(callable_, calls, strict=True):
        if call.skipped or call.error:
            outcome.skipped, outcome.error = call.skipped, call.error
            continue
        outcome.output = call.output
        try:
            try:
                extraction = LlmExtraction.model_validate(call.output)
            except pydantic.ValidationError as exc:
                outcome.error = f"ValidationError: {str(exc)[:300]}"
                continue
            outcome.accepted, outcome.rejected = post_check(outcome.job, extraction)
            if extraction.page_status != "ok" and outcome.accepted:
                # By the model's own account the page shows no prices, another country or a bot
                # wall: its items are never used (the pair still counts as answered).
                reason = f"page_status_{extraction.page_status}"
                outcome.rejected += [
                    {"item": item.model_dump(mode="json"), "reason": reason}
                    for item, _ in outcome.accepted
                ]
                outcome.accepted = []
        except Exception as exc:  # a post-check bug fails this job only
            outcome.accepted, outcome.rejected = [], []
            outcome.error = f"internal: {type(exc).__name__}: {str(exc)[:300]}"
    return outcomes


def apply_llm_items(
    deps: Deps, result: TaskResult, outcome: JobOutcome, task_kind: SourceKind
) -> int:
    """Turn accepted items into observations of the job's task. Keys the parser already has are
    logged as duplicates (the parser row wins in validate anyway) and kept in
    RunBuffer.llm_dupes: they belong to the stored answer (TaskState.llm_obs), which a later run
    of the same page text reuses for the plans its parser no longer reads."""
    job = outcome.job
    existing = {o.obs_key for o in result.observations}
    parser_keys = {o.obs_key for o in result.observations if o.extraction == "parser"}
    added = 0
    task = Task(
        task_id=result.task_id,
        adapter_id=result.adapter_id,
        service_ids=result.service_ids,
        countries=result.countries,
        channel=result.channel,
        kind=task_kind,
        url=job.source_url,
        host="",
    )
    kept: list[tuple[LlmItem, Money]] = []
    for item, money in outcome.accepted:
        try:
            d = ObservationDraft(
                service_id=job.service_id,
                plan_id=item.plan_id,
                plan_label_raw=item.plan_label_raw.strip()[:120] or item.plan_id,
                country=job.country,
                currency=item.currency,
                amount=money.amount,
                billing_period=item.billing_period,
                evidence_snippet=item.evidence_snippet.strip()[:240],
                flags=llm_flags(job.service_id, job.channel),
            )
            obs = to_observation(
                d,
                task,
                deps.catalog,
                source_url=job.source_url,
                observed_at=deps.now(),
                content_hash=result.text_hash or "",
                extraction="llm",
                confidence=CONFIDENCE["llm"],
            )
        except (pydantic.ValidationError, InvalidOperation) as exc:
            outcome.rejected.append(
                {"item": item.model_dump(mode="json"), "reason": f"invalid: {type(exc).__name__}"}
            )
            continue
        if obs.obs_key in existing:
            outcome.duplicates.append(
                {"item": item.model_dump(mode="json"), "obs_key": obs.obs_key}
            )
            if obs.obs_key in parser_keys:
                deps.buffer.llm_dupes.setdefault(result.task_id, []).append(obs)
            continue
        existing.add(obs.obs_key)
        result.observations.append(obs)
        kept.append((item, money))
        added += 1
    outcome.accepted = kept
    return added


def mark_pair_failure(result: TaskResult, service_id: str, cc: str, status: TaskStatus) -> None:
    """An LLM answer (today's, or a reused one for the same text) said the page, or its part for
    this pair, shows another country or a bot wall, and run_llm_jobs dropped its items: the pair
    counts as that failure (TaskResult.pair_status), and so does a task that covers this pair
    only. A pair the parser read rows for keeps the parser's verdict."""
    if any(
        o.extraction == "parser" and o.service_id == service_id and o.country == cc
        for o in result.observations
    ):
        return
    result.pair_status[pair_key(service_id, cc)] = status
    single = result.service_ids == [service_id] and result.countries == [cc]
    if single and result.status in ("ok", "partial", "failed"):
        result.status = status


# ============================================================================ validate helpers


def all_observations(results: list[TaskResult]) -> list[PriceObservation]:
    return [o for r in results for o in r.observations]


def run_validation(
    deps: Deps, results: list[TaskResult], fx: FxTable, run_date: date
) -> rules.ValidationOutcome:
    buf = deps.buffer
    return rules.validate(
        all_observations(results),
        previous=buf.previous_prices,
        state=buf.previous_state,
        task_results=results,
        catalog=deps.catalog,
        fx=fx,
        run_date=run_date,
        settings=deps.settings,
    )


def outcome_to_dict(outcome: rules.ValidationOutcome) -> dict[str, Any]:
    return {
        "observations": [o.model_dump(mode="json") for o in outcome.observations],
        "anomalies": [a.model_dump(mode="json") for a in outcome.anomalies],
        "review_queue": outcome.review_queue,
        "key_state": {k: v.model_dump(mode="json") for k, v in outcome.key_state.items()},
    }


def outcome_from_dict(data: dict[str, Any]) -> rules.ValidationOutcome:
    return rules.ValidationOutcome(
        observations=[PriceObservation.model_validate(o) for o in data.get("observations", [])],
        anomalies=[Anomaly.model_validate(a) for a in data.get("anomalies", [])],
        review_queue=list(data.get("review_queue", [])),
        key_state={k: KeyState.model_validate(v) for k, v in data.get("key_state", {}).items()},
    )


@dataclass
class Carry:
    """What flows between the nodes after fetch_and_parse (all JSON-safe once dumped)."""

    tasks: list[TaskResult]
    fx: FxTable | None = None
    outcome: rules.ValidationOutcome | None = None

    @classmethod
    def load(cls, data: Any) -> Carry:
        if isinstance(data, list):
            return cls(tasks=[TaskResult.model_validate(r) for r in data])
        fx = data.get("fx")
        outcome = data.get("outcome")
        return cls(
            tasks=[TaskResult.model_validate(r) for r in data.get("tasks", [])],
            fx=FxTable.model_validate(fx) if fx else None,
            outcome=outcome_from_dict(outcome) if outcome else None,
        )

    def dump(self) -> dict[str, Any]:
        out: dict[str, Any] = {"tasks": [r.model_dump(mode="json") for r in self.tasks]}
        if self.fx is not None:
            out["fx"] = self.fx.model_dump(mode="json")
        if self.outcome is not None:
            out["outcome"] = outcome_to_dict(self.outcome)
        return out


def build_second_opinions(deps: Deps, carry: Carry) -> list[tuple[LlmJob, TaskResult]]:
    """Second-opinion jobs for quarantined parser rows, one per (task, service, country), with the
    page re-read from the Store (or the dry-run cache)."""
    if carry.outcome is None:
        return []
    cat = deps.catalog
    by_group: dict[tuple[str, str, str], list[PriceObservation]] = {}
    tasks: dict[str, TaskResult] = {}
    parser_keys: dict[tuple[str, str], str] = {}
    for r in carry.tasks:
        for o in r.observations:
            if o.extraction == "parser":
                parser_keys.setdefault((o.obs_key, r.adapter_id), r.task_id)
        tasks[r.task_id] = r
    for row in carry.outcome.observations:
        if row.status != "quarantined" or row.extraction != "parser":
            continue
        task_id = parser_keys.get((row.obs_key, row.adapter_id))
        if task_id is None:
            continue
        by_group.setdefault((task_id, row.service_id, row.country), []).append(row)

    jobs: list[tuple[LlmJob, TaskResult]] = []
    for (task_id, sid, cc), rows in sorted(by_group.items()):
        if len(jobs) >= deps.settings.max_adjudications:
            break
        r = tasks[task_id]
        if not r.raw_ref or r.raw_ref.endswith(".json.gz"):
            continue
        html = deps.buffer.raw_cache.get(r.raw_ref)
        if html is None:
            try:
                html = read_text_gz(deps.store, r.raw_ref)
            except Exception as exc:
                deps.buffer.errors.append(f"adjudicate: cannot read {r.raw_ref}: {exc}")
                continue
        svc, country = _service(cat, sid), _country(cat, cc)
        if html is None or svc is None or country is None:
            continue
        expected = expected_currencies(cat, sid, cc, r.channel, r.adapter_id)
        text = focus_text(page_text_lines(html), country, expected, multi=len(r.countries) > 1)
        if not text.strip():
            continue
        scoped = len(r.countries) > 1 or len(r.service_ids) > 1
        jobs.append(
            (
                LlmJob(
                    job_id=job_id_for(task_id, "second_opinion", sid, cc, scoped=scoped),
                    task_id=task_id,
                    reason="second_opinion",
                    service_id=sid,
                    country=cc,
                    expected_currencies=expected,
                    plans=plan_hints(svc),
                    channel=r.channel,
                    source_url=rows[0].source_url,
                    page_text=text,
                    compare_to=[
                        {
                            "obs_key": row.obs_key,
                            "plan_id": row.plan_id,
                            "amount": str(row.amount),
                            "currency": row.currency,
                            "billing_period": row.billing_period,
                        }
                        for row in rows
                    ],
                ),
                r,
            )
        )
    return jobs


def _agrees(target: dict[str, Any], accepted: list[tuple[LlmItem, Money]]) -> bool:
    try:
        amount = Decimal(str(target["amount"]))
    except (InvalidOperation, KeyError):
        return False
    return any(
        item.plan_id == target["plan_id"]
        and item.billing_period == target["billing_period"]
        and item.currency == target["currency"]
        and money.amount == amount
        for item, money in accepted
    )


# ============================================================================ persist helpers


def to_published(
    obs: PriceObservation, prev: PublishedPrice | None, run_date: date
) -> PublishedPrice:
    changed = prev is None or (
        prev.amount != obs.amount
        or prev.currency != obs.currency
        or prev.billing_period != obs.billing_period
    )
    return PublishedPrice(
        obs_key=obs.obs_key,
        service_id=obs.service_id,
        plan_id=obs.plan_id,
        plan_label_raw=obs.plan_label_raw,
        country=obs.country,
        currency=obs.currency,
        amount=obs.amount,
        billing_period=obs.billing_period,
        monthly_equiv=obs.monthly_equiv,
        vat_included=obs.vat_included,
        channel=obs.channel,
        source_url=obs.source_url,
        source_kind=obs.source_kind,
        adapter_id=obs.adapter_id,
        extraction=obs.extraction,
        confidence=obs.confidence,
        status=obs.status,
        flags=sorted(set(obs.flags)),
        first_seen=prev.first_seen if prev else run_date,
        last_changed=run_date if changed or prev is None else prev.last_changed,
    )


def _per_country_status(r: TaskResult, svc: ServiceDef | None, cc: str) -> str:
    # The pair's own status first (an LLM page_status, or a pair nobody read today).
    status = rules.pair_status(r, svc.id, cc) if svc is not None else r.status
    if status not in ("ok", "partial") or svc is None:
        return status
    obs = [o for o in r.observations if o.service_id == svc.id and o.country == cc]
    if not obs:
        return "not_available" if len(r.countries) > 1 else status
    return "ok" if any(o.plan_id == svc.default_plan for o in obs) else "partial"


def coverage_rows(deps: Deps, results: list[TaskResult], run_date: date) -> list[CoverageRow]:
    """One row per (service, country, channel). The official source decides the status (a broken
    official page is what needs fixing even when a secondary dataset filled the prices)."""
    cat = deps.catalog
    prev_rows: dict[tuple[str, str, str], dict[str, Any]] = {}
    for row in deps.buffer.previous_coverage:
        try:
            prev_rows[(row["service_id"], row["country"], row["channel"])] = row
        except KeyError:
            continue
    cands: dict[tuple[str, str, str], list[tuple[tuple, str, str]]] = {}
    for r in results:
        for sid in r.service_ids:
            svc = _service(cat, sid)
            kind = source_kind(cat, sid, r.adapter_id, r.channel)
            for cc in r.countries:
                st = _per_country_status(r, svc, cc)
                rank = (kind != "official", _STATUS_PREFERENCE.get(st, 9), r.adapter_id)
                cands.setdefault((sid, cc, r.channel), []).append((rank, st, r.adapter_id))

    rows: dict[tuple[str, str, str], CoverageRow] = {}
    for triple, options in cands.items():
        _, status, adapter_id = min(options)
        prev = prev_rows.get(triple)
        failing_since = None
        if status in FAILING_STATUSES:
            since = prev.get("failing_since") if prev else None
            failing_since = date.fromisoformat(since) if since else run_date
        rows[triple] = CoverageRow(
            service_id=triple[0],
            country=triple[1],
            channel=triple[2],  # type: ignore[arg-type]
            status=status,  # type: ignore[arg-type]
            adapter_id=adapter_id,
            failing_since=failing_since,
        )
    # Rows this (filtered) run did not touch stay as they were, if still configured.
    for triple, prev in prev_rows.items():
        if triple in rows:
            continue
        svc = _service(cat, triple[0])
        if svc is None or _country(cat, triple[1]) is None:
            continue
        if not any(s.channel == triple[2] and s.applies_to(triple[1]) for s in svc.sources):
            continue
        try:
            rows[triple] = CoverageRow.model_validate(prev)
        except pydantic.ValidationError:
            continue
    return [rows[k] for k in sorted(rows)]


def llm_task_state(
    buf: RunBuffer,
    r: TaskResult,
    hash_: str,
    old: TaskState | None,
    held: set[tuple[str, str]],
    *,
    run_date: date,
    countries_filtered: bool,
) -> dict[str, Any]:
    """TaskState LLM fields for a task fetched today (its page text hash is `hash_`).

    llm_obs keeps the whole answer of every pair answered for this very text: today's fresh
    answers, and the stored rows of earlier answers while the text is unchanged (also on days the
    parser covered them, so a later parser failure on the same page can still reuse them).

    A pair whose LLM row ended quarantined (`held`: (obs_key, adapter_id)) is neither stored for
    reuse nor counted as answered: only a fresh answer (or the parser, or a secondary source) may
    confirm it, never a copy of the same answer.

    A fresh answer is stored whole: with the items whose keys the parser also had today
    (RunBuffer.llm_dupes), so a parser that loses them later on the same text still gets them."""
    rows = [o for o in r.observations if o.extraction == "llm"]
    redo = {(o.service_id, o.country) for o in rows if (o.obs_key, o.adapter_id) in held}
    fresh = buf.llm_answered.get(r.task_id, set()) - redo
    pairs = dict(old.llm_pairs) if old else {}
    for sid, cc in redo:
        pairs.pop(pair_key(sid, cc), None)
    for sid, cc in sorted(fresh):
        pairs[pair_key(sid, cc)] = LlmPairCheck(
            text_hash=hash_, on=run_date, page_status=r.pair_status.get(pair_key(sid, cc))
        )
    valid = {split_pair_key(k) for k, check in pairs.items() if check.text_hash == hash_}
    llm_obs: list[dict[str, Any]] = []
    stored: set[str] = set()
    for o in [*rows, *buf.llm_dupes.get(r.task_id, [])]:
        if o.reused or (o.service_id, o.country) not in fresh or o.obs_key in stored:
            continue
        stored.add(o.obs_key)
        llm_obs.append(o.model_dump(mode="json"))
    if old is not None and old.text_hash == hash_:
        llm_obs += [
            d
            for d in old.llm_obs
            if LEGACY_LLM_CHECKED_KEY not in d
            and (pair := (d.get("service_id"), d.get("country"))) in valid
            and pair not in fresh
        ]
    checked = r.task_id in buf.llm_checked and not redo
    if countries_filtered:  # a narrowed multi-country task says nothing about the other pairs
        checked = checked and old is not None and old.llm_checked_hash == hash_
    return {
        "text_hash": hash_,
        "llm_obs": llm_obs,
        "llm_checked_hash": hash_ if checked else None,
        "llm_pairs": dict(sorted(pairs.items())),
    }


def next_state(
    deps: Deps,
    results: list[TaskResult],
    outcome: rules.ValidationOutcome,
    run: dict[str, Any],
    run_date: date,
) -> PipelineState:
    buf = deps.buffer
    prev = buf.previous_state
    tasks: dict[str, TaskState] = {} if not run.get("filtered") else dict(prev.tasks)
    held = {
        (o.obs_key, o.adapter_id)
        for o in outcome.observations
        if o.status == "quarantined" and o.extraction == "llm"
    }
    for r in results:
        old = prev.tasks.get(r.task_id)
        if r.text_hash is not None:
            llm = llm_task_state(
                buf,
                r,
                r.text_hash,
                old,
                held,
                run_date=run_date,
                countries_filtered=bool(run.get("countries_filtered")),
            )
        elif old is not None:  # not fetched today: keep what lets a later unchanged page reuse
            llm = old.model_dump(include={"text_hash", "llm_obs", "llm_checked_hash", "llm_pairs"})
        else:
            llm = {}
        failing = r.status in FAILING_STATUSES
        tasks[r.task_id] = TaskState(
            **llm,
            last_status=r.status,
            failing_since=((old.failing_since if old else None) or run_date) if failing else None,
        )
    return PipelineState(
        run_id=run["run_id"],
        as_of=run_date,
        keys=dict(outcome.key_state),
        tasks=dict(sorted(tasks.items())),
    )


def report_summary(report: RunReport) -> dict[str, Any]:
    data = report.model_dump(mode="json", exclude={"anomalies", "errors", "outputs"})
    data["success_ratio"] = round(report.success_ratio, 4)
    data["anomalies"] = len(report.anomalies)
    data["errors"] = report.errors[:20]
    return data


# ============================================================================ workflow


def build_workflow(deps: Deps) -> Workflow:
    settings = deps.settings

    # The budget lives in the run buffer (plan_tasks resets it): look it up on every call.
    def _before_model(callback_context: Any, llm_request: Any) -> None:
        return deps.buffer.budget.before_model(callback_context, llm_request)

    def _after_model(callback_context: Any, llm_response: Any) -> None:
        return deps.buffer.budget.after_model(callback_context, llm_response)

    # HAGGLER_MAX_LLM_CALLS <= 0 means no LLM at all (ADK would read a 0 limit as unlimited).
    extractor = (
        build_price_extractor(
            deps.model, before_model_callback=_before_model, after_model_callback=_after_model
        )
        if deps.model is not None and settings.max_llm_calls > 0
        else None
    )

    def plan_tasks(node_input: str, ctx: Context) -> list[dict[str, Any]]:
        """Parse the run request, load the previous published files, plan every task."""
        filters = parse_filters(node_input)
        buf = deps.reset_buffer()
        started = deps.now()
        run_date = filters.date or started.date()
        run_id = make_run_id(run_date, started, filters)
        buf.run_id = run_id
        ctx.state["run"] = {
            "run_id": run_id,
            "run_date": run_date.isoformat(),
            "started_at": started.isoformat(),
            "dry_run": filters.dry_run,
            "filtered": filters.filtered,
            "countries_filtered": bool(filters.countries),
        }
        load_previous(deps.store, buf)
        tasks = plan_all(deps, filters, buf.errors)
        out: list[dict[str, Any]] = []
        for task in interleave_by_host(tasks):
            prev = buf.previous_state.tasks.get(task.task_id)
            if prev is not None:
                task = task.model_copy(
                    update={"prev_text_hash": prev.text_hash, "prev_llm_obs": prev.llm_obs}
                )
            out.append(task.model_dump(mode="json"))
        log.info("run %s: %d task(s) planned", run_id, len(out))
        return out

    @node(
        parallel_worker=True,
        max_parallel_workers=max(1, settings.fetch_concurrency),
        retry_config=RetryConfig(
            max_attempts=FETCH_ATTEMPTS,
            initial_delay=deps.fetch_retry_delay,
            max_delay=20,
            exceptions=[TransientFetchError],
        ),
    )
    async def fetch_and_parse(
        node_input: dict[str, Any], ctx: Context, run: dict[str, Any]
    ) -> dict[str, Any]:
        task = Task.model_validate(node_input)
        result = await process_task(
            deps, task, run, attempt=ctx.attempt_count, max_attempts=FETCH_ATTEMPTS
        )
        return result.model_dump(mode="json")

    @node(rerun_on_resume=True)
    async def llm_extract(node_input: Any, ctx: Context, run: dict[str, Any]) -> dict[str, Any]:
        """LLM fallback for pages the parsers could not (fully) read; capped and prioritised."""
        carry = Carry.load(node_input)
        buf = deps.buffer
        stats = buf.llm_stats
        # The page texts stayed in the run buffer (node outputs carry the jobs without them).
        stashed = buf.llm_jobs if buf.run_id == run["run_id"] else {}
        indexed: list[tuple[int, LlmJob]] = []
        for i, r in enumerate(carry.tasks):
            full = stashed.get(r.task_id, [])
            if [j.job_id for j in full] != [j.job_id for j in r.llm_jobs]:
                full = r.llm_jobs  # no page text: run_llm_jobs records an error, no model call
            indexed.extend((i, job) for job in full)
        stats.jobs += len(indexed)
        if extractor is None:
            stats.skipped_budget += len(indexed)
        elif indexed:
            indexed.sort(key=lambda x: (-x[1].priority, x[1].job_id))
            cap = max(0, settings.max_llm_jobs)
            selected, skipped = indexed[:cap], indexed[cap:]
            stats.skipped_budget += len(skipped)
            outcomes = await run_llm_jobs(
                deps, ctx, extractor, [j for _, j in selected], stage="extract"
            )
            unanswered = {carry.tasks[i].task_id for i, _ in skipped}
            for (i, _), outcome in zip(selected, outcomes, strict=True):
                result = carry.tasks[i]
                if not (outcome.skipped or outcome.error):
                    try:
                        kind = source_kind(
                            deps.catalog, result.service_ids[0], result.adapter_id, result.channel
                        )
                        added = apply_llm_items(deps, result, outcome, kind)
                        result.status = coverage_status(result, deps.catalog)  # type: ignore[assignment]
                    except Exception as exc:  # a bug here fails this job, not the run
                        outcome.error = f"internal: {type(exc).__name__}: {str(exc)[:300]}"
                    else:
                        stats.accepted_items += added
                        stats.rejected_items += len(outcome.rejected)
                        page_status = (outcome.output or {}).get("page_status")
                        result.notes.append(
                            f"llm: page_status={page_status} accepted={added} "
                            f"rejected={len(outcome.rejected)}"
                        )
                        pair = (outcome.job.service_id, outcome.job.country)
                        buf.llm_answered.setdefault(result.task_id, set()).add(pair)
                        if page_status in PAIR_FAILURE_PAGE_STATUSES:
                            mark_pair_failure(result, *pair, page_status)
                if outcome.skipped:
                    stats.skipped_budget += 1
                    unanswered.add(result.task_id)
                elif outcome.error:
                    stats.errors += 1
                    unanswered.add(result.task_id)
                    result.notes.append(f"llm error: {outcome.error[:200]}")
                buf.llm_log.append(outcome.log_line())
            # Fully checked: every job of the task answered and none left for a later run.
            pending = unanswered | buf.llm_truncated
            buf.llm_checked.update(
                carry.tasks[i].task_id for i, _ in selected if carry.tasks[i].task_id not in pending
            )
        for r in carry.tasks:  # the page text is in llm.jsonl; keep later node outputs small
            for job in r.llm_jobs:
                job.page_text = ""
        return carry.dump()

    async def fx_normalize(node_input: dict[str, Any], run: dict[str, Any]) -> dict[str, Any]:
        """FX table for every catalog/observed currency, then monthly EUR for each row."""
        carry = Carry.load(node_input)
        buf = deps.buffer
        run_date = date.fromisoformat(run["run_date"])
        currencies = set(deps.catalog.currencies())
        currencies |= {o.currency for o in all_observations(carry.tasks)}
        currencies |= {p.currency for p in buf.previous_prices.values()}
        try:
            table = await deps.fx.build_table(currencies, as_of=run_date, previous=buf.previous_fx)
        except Exception as exc:  # FX must never kill the run: fall back to yesterday's table
            buf.errors.append(f"fx: build_table failed: {type(exc).__name__}: {exc}")
            if buf.previous_fx is not None:
                table = buf.previous_fx.model_copy(
                    update={
                        "rates": {
                            c: r.model_copy(update={"stale": True, "source": "previous"})
                            for c, r in buf.previous_fx.rates.items()
                        }
                    }
                )
            else:
                table = FxTable(as_of=run_date)
        buf.errors.extend(str(e) for e in getattr(deps.fx, "errors", []) or [])
        for r in carry.tasks:
            for o in r.observations:
                o.monthly_eur = table.to_eur(o.monthly_equiv, o.currency)
        carry.fx = table
        return carry.dump()

    def validate(node_input: dict[str, Any], run: dict[str, Any]) -> dict[str, Any]:
        """Deterministic rules (haggler.validate) against the published prices and state."""
        carry = Carry.load(node_input)
        fx = carry.fx or FxTable(as_of=date.fromisoformat(run["run_date"]))
        carry.outcome = run_validation(deps, carry.tasks, fx, date.fromisoformat(run["run_date"]))
        return carry.dump()

    @node(rerun_on_resume=True)
    async def adjudicate(node_input: Any, ctx: Context, run: dict[str, Any]) -> dict[str, Any]:
        """Second LLM opinion on quarantined parser rows; agreement releases the quarantine."""
        if extractor is None or settings.max_adjudications <= 0:
            return node_input
        carry = Carry.load(node_input)
        if carry.outcome is None or carry.fx is None:
            return node_input
        pairs = build_second_opinions(deps, carry)
        if not pairs:
            return node_input
        stats = deps.buffer.llm_stats
        stats.jobs += len(pairs)
        outcomes = await run_llm_jobs(deps, ctx, extractor, [j for j, _ in pairs], stage="adj")
        extra: list[Anomaly] = []
        agreed = 0
        for (job, result), outcome in zip(pairs, outcomes, strict=True):
            deps.buffer.llm_log.append(outcome.log_line())
            if outcome.skipped:
                stats.skipped_budget += 1
                continue
            if outcome.error:
                stats.errors += 1
                continue
            for target in job.compare_to:
                ok = _agrees(target, outcome.accepted)
                extra.append(
                    Anomaly(
                        obs_key=target["obs_key"],
                        rule="second_opinion",
                        detail="agrees" if ok else "does not confirm the parser value",
                        current=f"{target['amount']} {target['currency']}/"
                        f"{target['billing_period']}",
                    )
                )
                if not ok:
                    continue
                for o in result.observations:
                    if o.obs_key == target["obs_key"] and o.extraction == "parser":
                        o.flags = _with_flags(o.flags, [FLAG_SECOND_OPINION])
                        agreed += 1
        if agreed:
            carry.outcome = run_validation(
                deps, carry.tasks, carry.fx, date.fromisoformat(run["run_date"])
            )
        carry.outcome.anomalies = sorted(
            [*carry.outcome.anomalies, *extra], key=lambda a: (a.obs_key, a.rule, a.detail)
        )
        return carry.dump()

    def persist(node_input: dict[str, Any], run: dict[str, Any]) -> dict[str, Any]:
        """runs/... first, then latest/*, and latest/run.json last (readers trust its hashes)."""
        carry = Carry.load(node_input)
        buf = deps.buffer
        run_date = date.fromisoformat(run["run_date"])
        run_id = run["run_id"]
        fx = carry.fx or FxTable(as_of=run_date)
        outcome = carry.outcome or rules.ValidationOutcome(observations=[])
        stats = buf.llm_stats.model_copy(update={"calls": buf.budget.calls})
        coverage = coverage_rows(deps, carry.tasks, run_date)
        prefix = run_prefix(run["run_date"], run_id)
        outputs = (
            []
            if run.get("dry_run")
            else [
                *(f"{prefix}/{n}" for n in ("snapshot.json", "tasks.jsonl", "llm.jsonl")),
                *(f"latest/{n}" for n in LATEST_FILES),
                f"{prefix}/report.json",
                "latest/run.json",
            ]
        )
        report = RunReport(
            run_id=run_id,
            run_date=run_date,
            started_at=datetime.fromisoformat(run["started_at"]),
            finished_at=deps.now(),
            dry_run=bool(run.get("dry_run")),
            tasks_total=len(carry.tasks),
            tasks_by_status=dict(sorted(Counter(r.status for r in carry.tasks).items())),
            obs_by_status=dict(sorted(Counter(o.status for o in outcome.observations).items())),
            llm=stats,
            anomalies=outcome.anomalies,
            errors=list(buf.errors),
            outputs=outputs,
        )
        if run.get("dry_run"):
            return report.model_dump(mode="json")

        store = deps.store
        snapshot = Snapshot(
            run_id=run_id,
            as_of=run_date,
            observations=outcome.observations,
            fx=fx,
            coverage=coverage,
        )
        store.write_bytes(f"{prefix}/snapshot.json", dumps(snapshot))
        store.write_bytes(
            f"{prefix}/tasks.jsonl",
            _jsonl(r.model_dump(mode="json") for r in carry.tasks),
            content_type="application/x-ndjson",
        )
        store.write_bytes(
            f"{prefix}/llm.jsonl", _jsonl(buf.llm_log), content_type="application/x-ndjson"
        )

        published = [
            to_published(o, buf.previous_prices.get(o.obs_key), run_date)
            for o in rules.publishable(outcome)
        ]
        published.sort(key=lambda p: p.obs_key)
        files: dict[str, bytes] = {
            "prices.json": dumps_lines(published),
            "fx.json": dumps(fx),
            "coverage.json": dumps_lines(coverage),
            "review_queue.json": dumps_lines(
                sorted(outcome.review_queue, key=lambda e: e.get("obs_key", ""))
            ),
            "state.json": state_bytes(next_state(deps, carry.tasks, outcome, run, run_date)),
        }
        for name in LATEST_FILES:
            store.write_bytes(f"latest/{name}", files[name])
        store.write_bytes(f"{prefix}/report.json", dumps(report))
        manifest = {
            "schema_version": 1,
            "run_id": run_id,
            "as_of": run_date.isoformat(),
            "finished_at": report.finished_at.isoformat() if report.finished_at else None,
            "files": {name: _sha256(files[name]) for name in LATEST_FILES},
            "report": report_summary(report),
        }
        store.write_bytes("latest/run.json", dumps(manifest))
        return report.model_dump(mode="json")

    return Workflow(
        name=WORKFLOW_NAME,
        edges=[
            (
                START,
                plan_tasks,
                fetch_and_parse,
                llm_extract,
                fx_normalize,
                validate,
                adjudicate,
                persist,
            )
        ],
    )

"""The research Workflow: a straight chain, like the daily one.

    START -> plan_research -> watch_bundles -> research -> verify_evidence -> persist

* plan_research: previous facts and state, the previous bundle_watch.json, the latest published
  prices, plan.select() for the service, pair and instrument subjects. With watch_only
  (`haggler research run --watch-only`) or the monthly guard spent nothing is planned for the
  agents: 0 search queries.
* watch_bundles: the bundle watcher (haggler.bundles.watch, no LLM) over data/curated/bundles.yaml.
  It ALWAYS runs: with the monthly search guard spent, without a model (--no-llm), with
  --max-jobs 0 and with --watch-only. Its pages go through the run's PageCache, which
  verify_evidence reuses (one fetch per URL per run). Then the bundle subjects are planned with
  this run's watch outcomes (haggler.bundles.plan: --bundles, HAGGLER_RESEARCH_MAX_BUNDLES, and
  whatever --max-jobs leaves after the other subjects), appended LAST.
* research: one agent run per subject (google_search + response schema), within the run's call
  and search-query budget (haggler.llm.budget), kind by kind in KIND_ORDER (bundles last: they
  are the first ones a spent budget skips).
* verify_evidence: every claim checked by our own fetch (research.evidence); a bundle value
  claim also by the value hook (research.evidence rule 5); previous facts the agents did not
  report again are re-checked against their page. Fetched text never goes back to the model.
  Then the pending findings of every bundle subject this run did NOT research (not planned, or
  planned but skipped by the budget, without a model, or failed) are re-checked on their pages
  without the model (research.evidence.recheck_claims), for the bundles the run may fetch on
  its own (haggler.bundles.plan.automatic: watch.mode auto, a provider that allows automated
  access, not expired): only those claims and the subject's rechecked_on change, never
  researched_on nor the state's last_success.
* persist: research/runs/{date}/{run_id}/report.json, then research/latest/* (bundles.json when
  there are bundle subjects, bundle_watch.json), run.json last. A subject whose research failed
  keeps its previous facts; a watcher that crashed keeps the previous bundle_watch.json.
  Concurrency guard: when the run_id of
  research/latest/run.json is no longer the one plan_research saw (another run finished
  meanwhile), only the report is written and the run reports an error: nothing in latest/ is
  overwritten. A dry run writes nothing (the month's search usage excepted).
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
from collections import Counter
from datetime import date, datetime
from typing import Any

import pydantic
from google.adk import Context, Workflow
from google.adk.workflow import START, node

from haggler.bundles import research as bundle_research
from haggler.bundles import watch as bundle_watch
from haggler.bundles.plan import automatic, plan_bundles
from haggler.llm.runner import AgentJob, run_agent_jobs
from haggler.models.fx import FxTable
from haggler.models.observations import PublishedPrice
from haggler.models.research import (
    BudgetUsage,
    ResearchAnswer,
    ResearchReport,
    SubjectFacts,
    SubjectKind,
    SubjectState,
)
from haggler.research import io
from haggler.research.agents import build_researcher
from haggler.research.deps import ResearchDeps, research_budget
from haggler.research.evidence import (
    PageCache,
    ValueContext,
    recheck_claims,
    reverify,
    verify_subject,
)
from haggler.research.kinds import RUN_ORDER, kind_of
from haggler.research.plan import (
    PAUSE_AFTER_FAILURES,
    Limits,
    PlannedSubject,
    Request,
    month_of,
    parse_pairs,
    pause_after,
    select,
)
from haggler.research.review import ReviewIndex
from haggler.store import dumps, read_json, write_text_gz

log = logging.getLogger(__name__)

WORKFLOW_NAME = "feasibility_research"
# The research node runs the kinds in this order (research.kinds run_order): pairs first.
KIND_ORDER: tuple[SubjectKind, ...] = RUN_ORDER
MAX_OPEN_QUESTIONS = 5
VERIFY_CONCURRENCY = 4


# ------------------------------------------------------------------- request


def _as_list(value: Any) -> list[str]:
    if value is None:
        return []
    items = value.split(",") if isinstance(value, str) else list(value)
    return list(dict.fromkeys(str(i).strip() for i in items if str(i).strip()))


def parse_request(node_input: Any) -> dict[str, Any]:
    """The START message: free text = defaults, or a JSON object with date, services, pairs
    ("netflix:CO"), instruments, bundles (bundle ids), max_jobs, dry_run and watch_only."""
    data: Any = node_input
    if isinstance(data, str):
        text = data.strip()
        if not text.startswith("{"):
            data = {}
        else:
            try:
                data = json.loads(text)
            except json.JSONDecodeError as exc:
                raise ValueError(f"research request is not valid JSON: {exc}") from exc
    if not isinstance(data, dict):
        data = {}
    raw_date = data.get("date")
    max_jobs = data.get("max_jobs")

    def flag(name: str) -> bool:
        value = data.get(name, False)
        if isinstance(value, str):
            return value.strip().lower() in {"1", "true", "yes"}
        return bool(value)

    return {
        "date": date.fromisoformat(str(raw_date)).isoformat() if raw_date else None,
        "services": _as_list(data.get("services")),
        "pairs": [f"{s}:{c}" for s, c in parse_pairs(_as_list(data.get("pairs")))],
        "instruments": _as_list(data.get("instruments")),
        "bundles": _as_list(data.get("bundles")),
        "max_jobs": int(max_jobs) if max_jobs not in (None, "") else None,
        "dry_run": flag("dry_run"),
        "watch_only": flag("watch_only"),
    }


def make_run_id(run_date: date, started_at: datetime, request: dict[str, Any]) -> str:
    seed = f"{started_at.isoformat()}|{json.dumps(request, sort_keys=True)}"
    return f"{run_date.isoformat()}-r{hashlib.sha256(seed.encode()).hexdigest()[:6]}"


def safe_key(key: str) -> str:
    return "".join(ch if ch.isalnum() else "_" for ch in key)


# ------------------------------------------------------------------- inputs


def load_prices(deps: ResearchDeps, errors: list[str]) -> tuple[list[PublishedPrice], Any]:
    """The daily job's latest/prices.json and latest/fx.json (both optional)."""
    prices: list[PublishedPrice] = []
    try:
        rows = read_json(deps.store, "latest/prices.json") or []
    except ValueError as exc:
        errors.append(f"latest/prices.json: ignored ({exc})")
        rows = []
    for row in rows if isinstance(rows, list) else []:
        try:
            prices.append(PublishedPrice.model_validate(row))
        except pydantic.ValidationError:
            continue
    fx = None
    try:
        data = read_json(deps.store, "latest/fx.json")
        if data is not None:
            fx = FxTable.model_validate(data)
    except (ValueError, pydantic.ValidationError) as exc:
        errors.append(f"latest/fx.json: ignored ({str(exc)[:200]})")
    return prices, fx


def _bundle(deps: ResearchDeps, subject: PlannedSubject) -> Any:
    """The curated bundle of a bundle subject (KeyError when bundles.yaml no longer has it)."""
    b = bundle_research.find_bundle(deps.bundles, subject.bundle_id)
    if b is None:
        raise KeyError(f"bundle {subject.bundle_id!r} is not in bundles.yaml")
    return b


def _watch_state(deps: ResearchDeps, bundle_id: str) -> Any:
    watch = deps.buffer.watch or deps.buffer.watch_previous
    return watch.get(bundle_id) if watch else None


def value_context(deps: ResearchDeps, subject: PlannedSubject) -> ValueContext | None:
    """What a bundle subject's value claims are checked against (None for the other kinds, and
    for a bundle bundles.yaml no longer has: its value claims then fail closed)."""
    if subject.kind != "bundle":
        return None
    b = bundle_research.find_bundle(deps.bundles, subject.bundle_id)
    if b is None:
        return None
    return bundle_research.value_context(deps.bundles, b, deps.catalog, deps.buffer.fx)


def payload(deps: ResearchDeps, subject: PlannedSubject) -> str:
    cat = deps.catalog
    job: dict[str, Any] = {"subject": subject.key}
    if subject.kind == "bundle":
        b = _bundle(deps, subject)
        job |= bundle_research.job(deps.bundles, b, cat, _watch_state(deps, b.bundle_id))
    elif subject.kind in ("service", "pair") and subject.service_id:
        svc = cat.service(subject.service_id)
        job |= {
            "service": svc.name,
            "service_id": svc.id,
            "homepage": svc.homepage,
            "official_domains": list(svc.official_domains),
        }
        if subject.kind == "pair" and subject.country:
            country = cat.country(subject.country)
            job |= {"country": country.cc, "country_name": country.name_en}
    elif subject.instrument_id:
        inst = next(i for i in deps.instruments.instruments if i.id == subject.instrument_id)
        residences = sorted({p.residence_country for p in deps.profiles.public()})
        job |= {
            "name": inst.name,
            "issuer_entity": inst.issuer_entity,
            "official_domains": list(inst.official_domains),
            "residence_country": ",".join(residences),
        }
    return json.dumps(job, ensure_ascii=False)


def known_pages(deps: ResearchDeps, subject: PlannedSubject) -> list[str]:
    """The subject's known official pages (catalog / instrument research_urls; a bundle's
    official URL and watch pages)."""
    if subject.kind == "bundle":
        b = bundle_research.find_bundle(deps.bundles, subject.bundle_id)
        return bundle_research.known_pages(b, _watch_state(deps, b.bundle_id)) if b else []
    if subject.service_id:
        try:
            return list(deps.catalog.service(subject.service_id).research_urls)
        except KeyError:
            return []
    inst = next((i for i in deps.instruments.instruments if i.id == subject.instrument_id), None)
    return list(inst.research_urls) if inst else []


def official_domains(deps: ResearchDeps, subject: PlannedSubject) -> list[str]:
    """A bundle's: its provider's domains for the bundle's country only."""
    if subject.kind == "bundle":
        b = bundle_research.find_bundle(deps.bundles, subject.bundle_id)
        return bundle_research.official_domains(deps.bundles, b) if b else []
    if subject.service_id:
        try:
            return list(deps.catalog.service(subject.service_id).official_domains)
        except KeyError:
            return []
    inst = next((i for i in deps.instruments.instruments if i.id == subject.instrument_id), None)
    return list(inst.official_domains) if inst else []


# ------------------------------------------------------------------ workflow


def build_research_workflow(deps: ResearchDeps) -> Workflow:
    settings = deps.settings

    def _before_model(callback_context: Any, llm_request: Any) -> None:
        return deps.buffer.budget.before_model(callback_context, llm_request)

    def _after_model(callback_context: Any, llm_response: Any) -> None:
        return deps.buffer.budget.after_model(callback_context, llm_response)

    agents = (
        {
            kind: build_researcher(
                kind,
                deps.model,
                before_model_callback=_before_model,
                after_model_callback=_after_model,
            )
            for kind in KIND_ORDER
        }
        if deps.model is not None and settings.research_max_calls > 0
        else None
    )

    def plan_research(node_input: str, ctx: Context) -> dict[str, Any]:
        request = parse_request(node_input)
        buf = deps.reset_buffer()
        started = deps.now()
        run_date = date.fromisoformat(request["date"]) if request["date"] else started.date()
        run_id = make_run_id(run_date, started, request)
        buf.run_id = run_id
        buf.latest = io.load_latest(deps.store)
        buf.errors.extend(buf.latest.errors)
        buf.watch_previous, watch_errors = io.load_watch(deps.store)
        buf.errors.extend(watch_errors)
        # The guard follows the real clock: a backdated --date run spends this month's queries.
        usage_month = month_of(started.date())
        used = io.read_usage(deps.store, usage_month)["queries"]
        buf.budget = research_budget(settings, month_queries_used=used)
        prices, fx = load_prices(deps, buf.errors)
        buf.fx = fx
        ctx.state["run"] = {
            "run_id": run_id,
            "run_date": run_date.isoformat(),
            "started_at": started.isoformat(),
            "dry_run": request["dry_run"],
            "usage_month": usage_month,
            # What research/latest/run.json said when this run started (persist's guard).
            "latest_run_id": buf.latest.run.get("run_id"),
        }
        if request["watch_only"]:
            log.info("research run %s: --watch-only, no subject planned", run_id)
            return {"subjects": [], "skipped": {"*": "--watch-only: bundle watcher only"}}
        explicit = Request(
            services=tuple(request["services"]),
            pairs=parse_pairs(request["pairs"]),
            instruments=tuple(request["instruments"]),
            bundles=tuple(request["bundles"]),
        )
        selection = select(
            catalog=deps.catalog,
            feasibility=deps.feasibility,
            instruments=deps.instruments,
            profiles=deps.profiles,
            prices=prices,
            fx=fx,
            facts=buf.latest.subjects,
            state=buf.latest.state,
            today=run_date,
            limits=Limits(
                max_pairs=settings.research_max_pairs,
                max_services=settings.research_max_services,
                max_instruments=settings.research_max_instruments,
                max_month_queries=settings.research_max_month_queries,
            ),
            request=explicit,
            fx_fee_pct=settings.fx_fee_pct,
            month_queries_used=used,
            month=usage_month,
        )
        buf.errors.extend(selection.errors)
        subjects = selection.subjects
        skipped = dict(selection.skipped)
        cap = request["max_jobs"]
        if cap is not None and len(subjects) > cap:
            for s in subjects[max(0, cap) :]:
                skipped[s.key] = "over --max-jobs"
            subjects = subjects[: max(0, cap)]
        log.info("research run %s: %d subject(s) planned", run_id, len(subjects))
        out: dict[str, Any] = {"subjects": [s.as_dict() for s in subjects], "skipped": skipped}
        if used < settings.research_max_month_queries:
            # watch_bundles plans the bundle subjects once the watcher ran (its outcomes count).
            out["bundle_plan"] = {
                "requested": request["bundles"],
                "explicit": explicit.explicit,
                "slots": None if cap is None else max(0, cap - len(subjects)),
            }
        return out

    def page_cache(run: dict[str, Any]) -> PageCache:
        """The run's PageCache (created by the first node that needs it). A dry run keeps no
        raw page."""
        if deps.buffer.pages is not None:
            return deps.buffer.pages

        def store_raw(url: str, data: str | bytes) -> str:
            if isinstance(data, bytes):  # a PDF: its bytes, as fetched
                key = io.raw_key(run["run_date"], url, "pdf")
                return deps.store.write_bytes(key, data, content_type="application/pdf")
            return write_text_gz(deps.store, io.raw_key(run["run_date"], url), data)

        deps.buffer.pages = PageCache(
            deps.fetcher,
            timeout_s=deps.fetch_timeout_s,
            store_raw=None if run.get("dry_run") else store_raw,
        )
        return deps.buffer.pages

    async def watch_bundles(node_input: dict[str, Any], run: dict[str, Any]) -> dict[str, Any]:
        buf = deps.buffer
        run_date = date.fromisoformat(run["run_date"])
        try:
            buf.watch = await bundle_watch.watch_bundles(
                deps.bundles,
                pages=page_cache(run),
                previous=buf.watch_previous,
                today=run_date,
                now=deps.now(),
                run_id=run["run_id"],
                catalog=deps.catalog,
                fx=buf.fx,
                max_pages=settings.bundle_watch_max_pages,
            )
        except Exception as exc:  # a watcher bug never fails the research run
            log.exception("bundle watch failed")
            buf.watch = None
            buf.errors.append(f"bundle watch: internal: {type(exc).__name__}: {exc}"[:300])
        else:
            skipped = [
                f"{b.bundle_id} {url}"
                for b in buf.watch.bundles
                for url in bundle_watch.pages_over_budget(b, run_date)
            ]
            if skipped:
                buf.errors.append(
                    f"bundle watch: page budget of {settings.bundle_watch_max_pages} spent; "
                    f"not fetched: {', '.join(skipped)}"[:600]
                )
        return plan_bundle_subjects(node_input, run_date)

    def plan_bundle_subjects(node_input: dict[str, Any], run_date: date) -> dict[str, Any]:
        """The bundle subjects (haggler.bundles.plan), after every other subject."""
        buf = deps.buffer
        out = {k: v for k, v in node_input.items() if k != "bundle_plan"}
        plan = node_input.get("bundle_plan")
        if plan is None:
            return out
        try:
            sel = plan_bundles(
                deps.bundles,
                facts=buf.latest.subjects,
                state=buf.latest.state,
                # This run's outcomes only: a crashed watcher's previous ones are no change.
                watch=buf.watch,
                today=run_date,
                max_bundles=settings.research_max_bundles,
                requested=plan.get("requested", []),
                explicit=bool(plan.get("explicit")),
            )
        except Exception as exc:  # a planning bug never fails the run
            log.exception("bundle planning failed")
            buf.errors.append(f"bundle plan: internal: {type(exc).__name__}: {exc}"[:300])
            return out
        buf.errors.extend(sel.errors)
        skipped = dict(out.get("skipped", {})) | sel.skipped
        subjects = sel.subjects
        slots = plan.get("slots")
        if slots is not None and len(subjects) > slots:
            for s in subjects[slots:]:
                skipped[s.key] = "over --max-jobs"
            subjects = subjects[:slots]
        if subjects:
            log.info("research run: %d bundle subject(s) planned", len(subjects))
        out["subjects"] = [*out.get("subjects", []), *(s.as_dict() for s in subjects)]
        out["skipped"] = skipped
        return out

    async def recheck_bundles(researched: set[str], run: dict[str, Any], on: date) -> None:
        """Pending findings of the bundle subjects this run did not research: re-checked on
        their pages (no model), for the bundles the run may fetch on its own. Only those claims
        and rechecked_on change."""
        buf = deps.buffer
        review = ReviewIndex(deps.review)
        for key, facts in sorted(buf.latest.subjects.items()):
            if facts.kind != "bundle" or key in researched:
                continue
            pending = [c for c in facts.claims if review.status(c) == "pending"]
            subject = PlannedSubject(key=key, kind="bundle", reason="", bundle_id=facts.ref.owner)
            bundle = bundle_research.find_bundle(deps.bundles, subject.bundle_id)
            if not pending or bundle is None or not automatic(bundle, deps.bundles, on):
                # Nothing pending, a bundle no longer curated, or one the run may not fetch on
                # its own (manual, off, expired, a provider that forbids it): left as it is.
                continue
            try:
                kept, dropped = await recheck_claims(
                    pending,
                    pages=page_cache(run),
                    official_domains=official_domains(deps, subject),
                    value_context=value_context(deps, subject),
                )
            except Exception as exc:  # a recheck bug never fails the run
                log.exception("bundle recheck failed: %s", key)
                buf.errors.append(f"{key}: recheck: {type(exc).__name__}: {exc}"[:300])
                continue
            others = [c for c in facts.claims if review.status(c) != "pending"]
            buf.rechecked[key] = facts.model_copy(
                update={
                    "claims": sorted([*others, *kept], key=lambda c: c.id),
                    "unverified": [*facts.unverified, *dropped],
                    "rechecked_on": on,
                }
            )

    @node(rerun_on_resume=True)
    async def research(node_input: Any, ctx: Context, run: dict[str, Any]) -> dict[str, Any]:
        subjects = [PlannedSubject.from_dict(d) for d in node_input.get("subjects", [])]
        answers: dict[str, dict[str, Any]] = {}
        if agents is None:
            for s in subjects:
                answers[s.key] = {"skipped": "llm_off"}
            return {**node_input, "answers": answers}
        budget = deps.buffer.budget
        for kind in KIND_ORDER:
            batch: list[PlannedSubject] = []
            jobs: list[AgentJob] = []
            for s in (s for s in subjects if s.kind == kind):
                try:
                    jobs.append(AgentJob(key=f"{kind}_{safe_key(s.key)}", payload=payload(deps, s)))
                except Exception as exc:  # e.g. a subject the catalog no longer knows
                    answers[s.key] = {"error": f"payload: {type(exc).__name__}: {exc}"[:300]}
                    continue
                batch.append(s)
            if not batch:
                continue
            calls = await run_agent_jobs(
                ctx,
                agents[kind],
                jobs,
                budget=budget,
                concurrency=settings.research_concurrency,
                timeout_s=settings.research_job_timeout_s,
                retry_empty=False,
            )
            for s, call in zip(batch, calls, strict=True):
                records = budget.grounding.get(call.run_id, [])
                answers[s.key] = {
                    "run_id": call.run_id,
                    "output": call.output,
                    "error": call.error,
                    "skipped": call.skipped,
                    "queries": [q for r in records for q in r.queries],
                    "sources": [src.model_dump() for r in records for src in r.sources],
                }
        return {**node_input, "answers": answers}

    async def verify_evidence(node_input: dict[str, Any], run: dict[str, Any]) -> dict[str, Any]:
        from haggler.llm.grounding import GroundingSource

        buf = deps.buffer
        run_date = date.fromisoformat(run["run_date"])
        pages = page_cache(run)
        subjects = [PlannedSubject.from_dict(d) for d in node_input.get("subjects", [])]
        answers: dict[str, dict[str, Any]] = node_input.get("answers", {})
        sem = asyncio.Semaphore(VERIFY_CONCURRENCY)

        async def one(s: PlannedSubject) -> dict[str, Any]:
            ans = answers.get(s.key) or {"skipped": "no answer"}
            if ans.get("skipped"):
                return {"key": s.key, "outcome": f"skipped:{ans['skipped']}"}
            previous = buf.latest.subjects.get(s.key)
            if ans.get("error") or ans.get("output") is None:
                return {
                    "key": s.key,
                    "outcome": "failed",
                    "error": ans.get("error") or "no output",
                }
            try:
                answer = ResearchAnswer.model_validate(ans["output"])
            except pydantic.ValidationError as exc:
                return {"key": s.key, "outcome": "failed", "error": f"ValidationError: {exc}"[:300]}
            domains = official_domains(deps, s)
            grounding = [GroundingSource.model_validate(g) for g in ans.get("sources", [])]
            values = value_context(deps, s)
            async with sem:
                try:
                    v = await verify_subject(
                        answer.claims,
                        key=s.key,
                        kind=s.kind,
                        official_domains=domains,
                        grounding=grounding,
                        pages=pages,
                        today=run_date,
                        previous=previous,
                        known_pages=known_pages(deps, s),
                        value_context=values,
                    )
                    prev_by_id = {c.id: c for c in (previous.claims if previous else [])}
                    # Same fact, new evidence: keep the previous claim (and its review decision)
                    # while its own quote still verifies on its page.
                    fresh: list = []
                    for c in v.claims:
                        before = prev_by_id.get(c.id)
                        if before is not None and before.hash != c.hash:
                            status, again = await reverify(
                                before,
                                pages=pages,
                                official_domains=domains,
                                value_context=values,
                            )
                            if status == "verified" and again is not None:
                                fresh.append(again)
                                continue
                        fresh.append(c)
                    v.claims = fresh
                    new_ids = {c.id for c in v.claims}
                    old = [c for c in prev_by_id.values() if c.id not in new_ids]
                    kept, dropped = await recheck_claims(
                        old, pages=pages, official_domains=domains, value_context=values
                    )
                except Exception as exc:  # a verification bug fails this subject only
                    return {
                        "key": s.key,
                        "outcome": "failed",
                        "error": f"internal: {type(exc).__name__}: {exc}"[:300],
                    }
            claims = sorted([*v.claims, *kept], key=lambda c: c.id)
            status = "researched" if claims else "nothing_found"
            facts = SubjectFacts(
                key=s.key,
                kind=s.kind,
                researched_on=run_date,
                run_id=run["run_id"],
                status=status,  # type: ignore[arg-type]
                claims=claims,
                unverified=[*v.unverified, *dropped],
                open_questions=[q.strip()[:300] for q in answer.open_questions if q.strip()][
                    :MAX_OPEN_QUESTIONS
                ],
            )
            return {
                "key": s.key,
                "outcome": status,
                "facts": facts.model_dump(mode="json"),
                "checks": v.checks,
                "suggested": [x.strip()[:80] for x in answer.suggestions if x.strip()],
            }

        results = await asyncio.gather(*(one(s) for s in subjects))
        # After the agents: a planned bundle the budget skipped (or that failed) is not
        # researched either, and gets the recheck too.
        researched = {r["key"] for r in results if r["outcome"] in ("researched", "nothing_found")}
        await recheck_bundles(researched, run, run_date)
        return {**node_input, "results": list(results), "fetches": pages.fetches}

    def persist(node_input: dict[str, Any], run: dict[str, Any]) -> dict[str, Any]:
        buf = deps.buffer
        run_date = date.fromisoformat(run["run_date"])
        run_id = run["run_id"]
        results: list[dict[str, Any]] = node_input.get("results", [])
        # The bundle subjects the watcher re-checked (none of them researched this run).
        subjects = dict(buf.latest.subjects) | buf.rechecked
        state = buf.latest.state.model_copy(deep=True)
        by_status: Counter[str] = Counter()
        checks: dict[str, Counter[str]] = {}
        open_q: dict[str, list[str]] = {}
        suggested: dict[str, list[str]] = {}
        for r in results:
            key, outcome = r["key"], r["outcome"]
            by_status[outcome.split(":", 1)[0]] += 1
            if outcome.startswith("skipped"):
                continue
            st = state.subjects.get(key, SubjectState()).model_copy()
            st.last_attempt = run_date
            if outcome == "failed":
                st.consecutive_failures += 1
                if st.consecutive_failures >= PAUSE_AFTER_FAILURES:
                    st.paused_until = pause_after(run_date)
                buf.errors.append(f"{key}: {r.get('error', 'failed')}"[:300])
            else:
                facts = SubjectFacts.model_validate(r["facts"])
                subjects[key] = facts
                st.last_success = run_date
                st.consecutive_failures = 0
                st.paused_until = None
                if facts.open_questions:
                    open_q[key] = facts.open_questions
                for cls, status in r.get("checks", []):
                    checks.setdefault(cls, Counter())[status] += 1
                suggested.setdefault(kind_of(key), []).extend(r.get("suggested", []))
            state.subjects[key] = st
        verified = sum(c.get("verified", 0) for c in checks.values())
        unverified = sum(n for c in checks.values() for n in c.values()) - verified
        budget = buf.budget
        month = run.get("usage_month") or month_of(run_date)
        if budget.calls:
            usage = io.add_usage(deps.store, month, calls=budget.calls, queries=budget.queries)
        else:
            usage = io.read_usage(deps.store, month)
        state.run_id, state.as_of = run_id, run_date
        records = [rec for recs in budget.grounding.values() for rec in recs]
        prefix = io.run_prefix(run["run_date"], run_id)
        dry = bool(run.get("dry_run"))
        # This run's watcher state; when the watcher crashed, the previous one unchanged.
        watch = buf.watch if buf.watch is not None else buf.watch_previous
        concurrent = False
        # Best effort: a check, then the writes (the Store has no conditional write). It
        # catches a run that finished while this one worked; two runs reaching persist within
        # the same few seconds can still interleave their latest/ files (the next sync then
        # fails on a sha256 mismatch rather than publishing them).
        if not dry:
            seen, now_id = run.get("latest_run_id"), io.latest_run_id(deps.store)
            if now_id != seen:
                concurrent = True
                message = (
                    f"{io.LATEST}/run.json changed while this run worked (run {now_id!r}, it "
                    f"started on {seen!r}): another run finished meanwhile; only this run's "
                    f"report was written, {io.LATEST}/ is left as it is"
                )
                log.error("research run %s: %s", run_id, message)
                buf.errors.append(message)
        latest_outputs = [
            *(
                f"{io.LATEST}/{n}"
                for n in io.latest_names(subjects.values(), watch=watch is not None)
            ),
            f"{io.LATEST}/run.json",
        ]
        report = ResearchReport(
            run_id=run_id,
            run_date=run_date,
            started_at=datetime.fromisoformat(run["started_at"]),
            finished_at=deps.now(),
            dry_run=dry,
            planned=[d["key"] for d in node_input.get("subjects", [])],
            skipped_reasons=dict(node_input.get("skipped", {}))
            | {r["key"]: r["outcome"].split(":", 1)[1] for r in results if ":" in r["outcome"]},
            by_status=dict(sorted(by_status.items())),
            claims_verified=verified,
            claims_unverified=unverified,
            checks={cls: dict(sorted(c.items())) for cls, c in sorted(checks.items())},
            budget=BudgetUsage(
                max_calls=budget.max_calls,
                max_queries=budget.max_queries or 0,
                calls=budget.calls,
                queries=budget.queries,
                month=month,
                month_queries=usage["queries"],
                max_month_queries=settings.research_max_month_queries,
                prompt_tokens=sum(r.prompt_tokens for r in records),
                output_tokens=sum(r.output_tokens for r in records),
                thought_tokens=sum(r.thought_tokens for r in records),
                tool_prompt_tokens=sum(r.tool_prompt_tokens for r in records),
            ),
            open_questions=open_q,
            # Phase 2's list: what the service, pair and instrument agents suggested.
            suggested_instruments=sorted(
                {x for kind, xs in suggested.items() if kind != "bundle" for x in xs}
            )[:20],
            suggestions={
                kind: sorted(set(xs))[:20] for kind, xs in sorted(suggested.items()) if xs
            },
            errors=list(buf.errors),
            outputs=[]
            if dry
            else [f"{prefix}/report.json", *([] if concurrent else latest_outputs)],
        )
        if dry:
            return report.model_dump(mode="json")
        deps.store.write_bytes(f"{prefix}/report.json", dumps(report))
        if concurrent:
            return report.model_dump(mode="json")
        summary: dict[str, Any] = {
            "planned": len(report.planned),
            "by_status": report.by_status,
            "claims_verified": report.claims_verified,
            "queries": report.budget.queries,
            "errors": len(report.errors),
        }
        if watch is not None:
            summary["bundle_watch"] = bundle_watch.summary(watch)
        io.write_latest(
            deps.store,
            subjects=subjects.values(),
            state=state,
            run_id=run_id,
            as_of=run_date.isoformat(),
            finished_at=report.finished_at.isoformat() if report.finished_at else None,
            report_summary=summary,
            watch=watch,
        )
        return report.model_dump(mode="json")

    return Workflow(
        name=WORKFLOW_NAME,
        edges=[(START, plan_research, watch_bundles, research, verify_evidence, persist)],
    )

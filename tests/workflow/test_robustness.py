"""Failure containment and budgets of the daily Workflow: nothing a single task or LLM job does
may kill the run, hang it, or exceed the configured budgets."""

from __future__ import annotations

import asyncio
import json
import threading
from collections.abc import AsyncGenerator
from typing import Any

import pytest
import wf_support as W
from google.adk.models.llm_response import LlmResponse
from google.genai import types
from test_pipeline import happy_responder, jobs_requested, llm_log, task_results

from conftest import FixtureFetcher, ScriptedLlm, _request_text
from haggler.models.catalog import Catalog
from haggler.pipeline import nodes
from haggler.sources.base import LlmRequest
from haggler.store import read_json

NETFLIX_SE = {"services": ["netflix"], "countries": ["SE"], "adapters": ["netflix_help"]}


@pytest.fixture
def cat(catalog: Catalog) -> Catalog:
    return W.subset_catalog(catalog)


def patch_parse(monkeypatch: pytest.MonkeyPatch, adapter_id: str, parse: Any) -> None:
    """Replace one adapter's parse with parse(real_adapter, task, resp, catalog)."""
    real = nodes.get_adapter

    class Patched:
        id = adapter_id

        def plan(self, svc, src, catalog):
            return real(adapter_id).plan(svc, src, catalog)

        def parse(self, task, resp, catalog):
            return parse(real(adapter_id), task, resp, catalog)

    monkeypatch.setattr(nodes, "get_adapter", lambda a: Patched() if a == adapter_id else real(a))


def se_standard(text: str) -> Any:
    line = next(ln for ln in W.job_of(text)["page_text"].splitlines() if "169 kr" in ln)
    return W.extraction([W.item("standard", "Standard", "169 kr", "SEK", line)])


def persisted(deps: Any, report: Any) -> bool:
    return read_json(deps.store, "latest/run.json")["run_id"] == report.run_id


# ----------------------------------------------------------------------------- task failures


async def test_parse_returning_none_is_a_parser_crash(
    cat: Catalog, monkeypatch: pytest.MonkeyPatch
) -> None:
    patch_parse(monkeypatch, "netflix_help", lambda real, task, resp, catalog: None)
    llm = ScriptedLlm(responder=se_standard)
    deps = W.make_deps(cat, FixtureFetcher(W.routes()), model=llm)
    report = await W.run(deps, **NETFLIX_SE)
    task = task_results(deps.store, report)["netflix_help:netflix:SE"]
    assert any("parser crashed: TypeError: parse() returned NoneType" in n for n in task.notes)
    assert task.status == "ok" and [o.extraction for o in task.observations] == ["llm"]
    assert persisted(deps, report)


async def test_invalid_adapter_llm_request_only_drops_that_job(
    cat: Catalog, monkeypatch: pytest.MonkeyPatch
) -> None:
    def parse(real, task, resp, catalog):
        pr = real.parse(task, resp, catalog)
        pr.llm_requests.append(LlmRequest("netflix", "SE", "bogus", "Standard 169 kr"))  # type: ignore[arg-type]
        return pr

    patch_parse(monkeypatch, "netflix_help", parse)
    llm = ScriptedLlm(responder=se_standard)
    deps = W.make_deps(cat, FixtureFetcher(W.routes()), model=llm)
    report = await W.run(deps, **NETFLIX_SE)
    task = task_results(deps.store, report)["netflix_help:netflix:SE"]
    assert task.status == "ok" and {o.extraction for o in task.observations} == {"parser"}
    assert any("llm job netflix/SE (bogus) not created" in n for n in task.notes)
    assert llm.requests == [] and persisted(deps, report)


async def test_bad_parser_draft_only_drops_that_row(
    cat: Catalog, monkeypatch: pytest.MonkeyPatch
) -> None:
    real = nodes.to_observation

    def to_observation(draft, task, catalog, **kw):
        if draft.plan_id == "premium":
            raise TypeError("unsupported operand")
        return real(draft, task, catalog, **kw)

    monkeypatch.setattr(nodes, "to_observation", to_observation)
    deps = W.make_deps(cat, FixtureFetcher(W.routes()))
    report = await W.run(deps, **NETFLIX_SE)
    task = task_results(deps.store, report)["netflix_help:netflix:SE"]
    assert "bad draft premium/SE: TypeError" in task.notes
    assert task.observations and all(o.plan_id != "premium" for o in task.observations)


@pytest.mark.parametrize("target", ["text_hash", "make_llm_jobs", "coverage_status"])
async def test_internal_errors_after_the_fetch_fail_only_their_task(
    cat: Catalog, monkeypatch: pytest.MonkeyPatch, target: str
) -> None:
    real = getattr(nodes, target)

    def broken(*args: Any, **kwargs: Any) -> Any:
        text = json.dumps([str(a) for a in args])
        if "most households pick" in text or "netflix_help:netflix:US" in text:
            raise ValueError(f"{target} exploded")
        return real(*args, **kwargs)

    monkeypatch.setattr(nodes, target, broken)
    deps = W.make_deps(cat, FixtureFetcher(W.routes()), model=ScriptedLlm())
    report = await W.run(deps, services=["netflix"], countries=["SE", "US"])
    tasks = task_results(deps.store, report)
    us = tasks["netflix_help:netflix:US"]
    assert us.status == "failed" and us.error == f"internal: ValueError: {target} exploded"
    assert us.raw_ref and us.http_status == 200
    assert tasks["netflix_help:netflix:SE"].status == "ok"
    assert persisted(deps, report)


@pytest.mark.parametrize("target", ["post_check", "apply_llm_items"])
async def test_internal_errors_in_llm_handling_fail_only_their_job(
    cat: Catalog, monkeypatch: pytest.MonkeyPatch, target: str
) -> None:
    real = getattr(nodes, target)

    def broken(*args: Any, **kwargs: Any) -> Any:
        if any(getattr(a, "service_id", None) == "netflix" for a in args) or any(
            getattr(getattr(a, "job", None), "service_id", None) == "netflix" for a in args
        ):
            raise ArithmeticError(f"{target} exploded")
        return real(*args, **kwargs)

    monkeypatch.setattr(nodes, target, broken)
    llm = ScriptedLlm(responder=happy_responder)
    deps = W.make_deps(cat, FixtureFetcher(W.routes()), model=llm)
    report = await W.run(deps)
    assert report.llm.errors == 1
    tasks = task_results(deps.store, report)
    netflix = tasks["netflix_help:netflix:US"]
    assert any(f"llm error: internal: ArithmeticError: {target}" in n for n in netflix.notes)
    assert tasks["spotify_premium:spotify:US"].status == "ok"
    assert persisted(deps, report)


# ----------------------------------------------------------------------------- event loop


async def test_page_rendering_and_parsing_run_off_the_event_loop(
    cat: Catalog, monkeypatch: pytest.MonkeyPatch
) -> None:
    on_loop: dict[str, list[bool]] = {"render": [], "parse": [], "plan": []}
    main = threading.main_thread()

    def recording(name: str, real: Any) -> Any:
        def wrapper(*args: Any, **kwargs: Any) -> Any:
            on_loop[name].append(threading.current_thread() is main)
            return real(*args, **kwargs)

        return wrapper

    monkeypatch.setattr(nodes, "page_text_lines", recording("render", nodes.page_text_lines))
    monkeypatch.setattr(nodes, "make_llm_jobs", recording("plan", nodes.make_llm_jobs))

    def parse(real, task, resp, catalog):
        on_loop["parse"].append(threading.current_thread() is main)
        return real.parse(task, resp, catalog)

    patch_parse(monkeypatch, "netflix_help", parse)
    deps = W.make_deps(cat, FixtureFetcher(W.routes()))
    await W.run(deps, services=["netflix"], countries=["SE", "US"], adapters=["netflix_help"])
    assert on_loop == {"render": [False, False], "parse": [False, False], "plan": [False]}


# ----------------------------------------------------------------------------- model calls


class StallingLlm(ScriptedLlm):
    """Never answers for Netflix US (a hung request)."""

    async def generate_content_async(
        self, llm_request: Any, stream: bool = False
    ) -> AsyncGenerator[LlmResponse, None]:
        text = _request_text(llm_request)
        if W.job_of(text).get("service_id") == "netflix":
            self.requests.append(text)
            await asyncio.sleep(3600)
        async for response in super().generate_content_async(llm_request, stream):
            yield response


async def test_a_hung_model_call_becomes_a_job_error(cat: Catalog) -> None:
    llm = StallingLlm(responder=happy_responder)
    deps = W.make_deps(cat, FixtureFetcher(W.routes()), model=llm, llm_job_timeout_s=0.3)
    report = await asyncio.wait_for(W.run(deps), timeout=20)
    assert report.llm.errors == 1
    netflix = task_results(deps.store, report)["netflix_help:netflix:US"]
    assert "llm error: timeout after 0.3s" in netflix.notes
    assert persisted(deps, report)


class EmptyLlm(ScriptedLlm):
    """Netflix: empty text without a finish reason; Spotify: empty text blocked for SAFETY."""

    async def generate_content_async(
        self, llm_request: Any, stream: bool = False
    ) -> AsyncGenerator[LlmResponse, None]:
        text = _request_text(llm_request)
        service = W.job_of(text).get("service_id")
        if service in ("netflix", "spotify"):
            self.requests.append(text)
            yield LlmResponse(
                content=types.ModelContent(parts=[types.Part(text="")]),
                finish_reason=types.FinishReason.SAFETY if service == "spotify" else None,
            )
            return
        async for response in super().generate_content_async(llm_request, stream):
            yield response


async def test_empty_answers_are_retried_once_unless_blocked(cat: Catalog) -> None:
    llm = EmptyLlm(responder=happy_responder)
    deps = W.make_deps(cat, FixtureFetcher(W.routes()), model=llm)
    report = await W.run(deps)
    requested = [job[0] for job in jobs_requested(llm)]
    assert requested.count("netflix") == 2  # schema validation never ran: one more try
    assert requested.count("spotify") == 1  # a SAFETY block would only repeat
    errors = {e["job"]["service_id"]: e["error"] for e in llm_log(deps.store, report)}
    assert errors["netflix"] == "empty answer (finish_reason=none)"
    assert errors["spotify"] == "empty answer (finish_reason=SAFETY)"
    assert report.llm.errors == 2 and report.llm.calls == 4


class RaisingLlm(ScriptedLlm):
    async def generate_content_async(
        self, llm_request: Any, stream: bool = False
    ) -> AsyncGenerator[LlmResponse, None]:
        text = _request_text(llm_request)
        self.requests.append(text)
        raise RuntimeError("503 UNAVAILABLE")
        yield  # pragma: no cover - makes this an async generator


async def test_failed_model_calls_are_counted(cat: Catalog) -> None:
    llm = RaisingLlm()
    deps = W.make_deps(cat, FixtureFetcher(W.routes()), model=llm)
    report = await W.run(deps)
    assert len(llm.requests) == 3
    assert report.llm.calls == 3 and report.llm.errors == 3


async def test_llm_call_budget_holds_without_a_run_config(cat: Catalog) -> None:
    """adk web / adk run use their own RunConfig: the pipeline enforces the budget itself."""
    from google.adk import Runner
    from google.adk.sessions import InMemorySessionService

    from haggler.pipeline.app import build_app

    llm = ScriptedLlm(responder=happy_responder)
    deps = W.make_deps(cat, FixtureFetcher(W.routes()), model=llm, max_llm_calls=1)
    final = None
    msg = types.UserContent(parts=[types.Part(text="go")])
    async with Runner(
        app=build_app(deps), session_service=InMemorySessionService(), auto_create_session=True
    ) as runner:
        async for ev in runner.run_async(user_id="u", session_id="s", new_message=msg):
            if ev.output is not None and "daily_prices@1" in (ev.node_info.output_for or []):
                final = ev.output
    assert final is not None
    assert len(llm.requests) == 1
    assert final["llm"]["calls"] == 1 and final["llm"]["skipped_budget"] == 2


async def test_zero_llm_call_budget_turns_the_llm_off(cat: Catalog) -> None:
    llm = ScriptedLlm(responder=happy_responder)
    deps = W.make_deps(cat, FixtureFetcher(W.routes()), model=llm, max_llm_calls=0)
    report = await W.run(deps)
    assert llm.requests == []
    assert report.llm.calls == 0 and report.llm.jobs == 3 and report.llm.skipped_budget == 3


async def test_repeated_job_ids_get_their_own_answers(cat: Catalog) -> None:
    """ADK answers a repeated run id with the first run's cached output."""
    from google.adk import Runner, Workflow
    from google.adk.apps import App
    from google.adk.sessions import InMemorySessionService
    from google.adk.workflow import START, node

    from haggler.models.run import LlmJob, LlmPlanHint
    from haggler.pipeline.agents import build_price_extractor

    llm = ScriptedLlm(
        responder=lambda text: {**W.extraction([]), "notes": W.job_of(text)["country"]}
    )
    deps = W.make_deps(cat, FixtureFetcher({}), model=llm)
    agent = build_price_extractor(llm)
    jobs = [
        LlmJob(
            job_id="same",
            task_id="t",
            reason="second_opinion",
            service_id="netflix",
            country=cc,
            expected_currencies=["SEK"],
            plans=[LlmPlanHint(id="standard", label="Standard")],
            channel="web",
            source_url="https://x",
            page_text="Standard 169 kr",
        )
        for cc in ("SE", "CO")
    ]

    @node(rerun_on_resume=True)
    async def ask(node_input: Any, ctx: Any) -> list[str]:
        outcomes = await nodes.run_llm_jobs(deps, ctx, agent, jobs, stage="adj")
        return [(o.output or {}).get("notes", "") for o in outcomes]

    app = App(name="t", root_agent=Workflow(name="w", edges=[(START, ask)]))
    final = None
    msg = types.UserContent(parts=[types.Part(text="go")])
    async with Runner(
        app=app, session_service=InMemorySessionService(), auto_create_session=True
    ) as runner:
        async for ev in runner.run_async(user_id="u", session_id="s", new_message=msg):
            if ev.output is not None and "w@1" in (ev.node_info.output_for or []):
                final = ev.output
    assert final == ["SE", "CO"] and len(llm.requests) == 2


# ----------------------------------------------------------------------------- session events


def page_texts(obj: Any) -> list[str]:
    """Every non-empty "page_text" value nested anywhere in a JSON-like object."""
    found: list[str] = []
    if isinstance(obj, dict):
        for k, v in obj.items():
            if k == "page_text" and isinstance(v, str) and v:
                found.append(v)
            else:
                found.extend(page_texts(v))
    elif isinstance(obj, list):
        for v in obj:
            found.extend(page_texts(v))
    return found


async def test_llm_page_texts_stay_out_of_node_outputs(cat: Catalog) -> None:
    from google.adk import Runner
    from google.adk.sessions import InMemorySessionService

    from haggler.pipeline.app import build_app

    llm = ScriptedLlm(responder=happy_responder)
    deps = W.make_deps(cat, FixtureFetcher(W.routes()), model=llm)
    outputs: list[tuple[str, Any]] = []
    msg = types.UserContent(parts=[types.Part(text="go")])
    async with Runner(
        app=build_app(deps), session_service=InMemorySessionService(), auto_create_session=True
    ) as runner:
        async for ev in runner.run_async(user_id="u", session_id="s", new_message=msg):
            if ev.output is not None:
                outputs.append((ev.node_info.path if ev.node_info else "?", ev.output))
    assert any("fetch_and_parse" in path for path, _ in outputs)
    assert [path for path, out in outputs if page_texts(out)] == []
    # The jobs still reached the model with their page text, and llm.jsonl keeps it.
    assert len(llm.requests) == 3
    assert all(W.job_of(t)["page_text"] for t in llm.requests)
    run = read_json(deps.store, "latest/run.json")
    log = deps.store.read_bytes(f"runs/{run['as_of']}/{run['run_id']}/llm.jsonl").decode()
    assert all(json.loads(line)["job"]["page_text"] for line in log.splitlines())

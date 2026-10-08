"""LLM work carried across runs (TaskState.llm_obs / llm_pairs / llm_checked_hash).

* Only pairs the LLM actually answered for today's page text are reused; an errored or
  budget-skipped job is asked again on the next run.
* A capped multi-country page rotates over its countries across runs.
* A quarantined LLM row is never confirmed by a copy of the same answer.
"""

from __future__ import annotations

from datetime import date
from typing import Any

import pytest
import wf_support as W

from conftest import FixtureFetcher, ScriptedLlm
from haggler.models.catalog import Catalog
from haggler.models.observations import PublishedPrice
from haggler.models.run import PipelineState
from haggler.pipeline import nodes
from haggler.sources.base import ParseResult
from haggler.store import read_json

DAY1 = date(2026, 10, 8)
ICLOUD_TASK = "icloud_support:icloud_plus:ALL"
# 200 GB prices printed on the iCloud fixture page.
ICLOUD_200GB = {"SE": ("39 kr", "SEK"), "CO": ("$11.900", "COP"), "US": ("$2.99", "USD")}


@pytest.fixture
def cat(catalog: Catalog) -> Catalog:
    return W.subset_catalog(catalog)


def break_parser(monkeypatch: pytest.MonkeyPatch, adapter_id: str) -> None:
    """The adapter plans as usual but its parser reads nothing (the LLM fallback applies)."""
    real = nodes.get_adapter

    class Broken:
        id = adapter_id

        def plan(self, svc, src, catalog):
            return real(adapter_id).plan(svc, src, catalog)

        def parse(self, task, resp, catalog):
            return ParseResult(status="no_prices", notes=["selectors broken"])

    monkeypatch.setattr(nodes, "get_adapter", lambda a: Broken() if a == adapter_id else real(a))


def asked(llm: ScriptedLlm, since: int = 0) -> list[str]:
    return [W.job_of(t)["country"] for t in llm.requests[since:]]


def prices(store: Any) -> dict[str, PublishedPrice]:
    rows = [PublishedPrice.model_validate(r) for r in read_json(store, "latest/prices.json")]
    return {r.obs_key: r for r in rows}


def state(store: Any) -> PipelineState:
    return PipelineState.model_validate(read_json(store, "latest/state.json"))


def icloud_answer(job: dict[str, Any]) -> dict[str, Any]:
    price, currency = ICLOUD_200GB[job["country"]]
    line = next(
        ln
        for ln in job["page_text"].splitlines()
        if ln.replace(" ", "").startswith("200GB:") and price in ln
    )
    return W.extraction([W.item("200gb", "200GB", price, currency, line)])


async def test_errored_job_is_retried_while_its_siblings_are_reused(
    cat: Catalog, monkeypatch: pytest.MonkeyPatch
) -> None:
    clock = W.Clock(DAY1)
    fetcher = FixtureFetcher(W.routes())
    store = W.RecordingStore()
    opts = {"services": ["icloud_plus"]}
    # Day 1: the parser reads the page, so the LLM values of day 2 are not new keys.
    await W.run(W.make_deps(cat, fetcher, store=store, clock=clock), **opts)
    assert prices(store)["icloud_plus:200gb:SE:web:month"].extraction == "parser"

    break_parser(monkeypatch, "icloud_support")
    failing = {"CO"}

    def responder(text: str) -> Any:
        job = W.job_of(text)
        return "Sorry, {not json" if job["country"] in failing else icloud_answer(job)

    llm = ScriptedLlm(responder=responder)
    deps = W.make_deps(cat, fetcher, store=store, clock=clock, model=llm)
    clock.day = date(2026, 10, 9)
    second = await W.run(deps, **opts)
    assert sorted(asked(llm)) == ["CO", "CO", "SE", "US"]  # CO: malformed JSON, retried once
    assert second.llm.errors == 1
    rows = prices(store)
    assert rows["icloud_plus:200gb:SE:web:month"].extraction == "llm"
    assert rows["icloud_plus:200gb:CO:web:month"].status == "carried_forward"
    task = state(store).tasks[ICLOUD_TASK]
    assert sorted(task.llm_pairs) == ["icloud_plus:SE", "icloud_plus:US"]
    assert task.llm_checked_hash is None

    # Day 3, same page: SE and US are reused, only the errored CO job is asked again.
    failing.clear()
    clock.day = date(2026, 10, 10)
    seen = len(llm.requests)
    third = await W.run(deps, **opts)
    assert asked(llm, seen) == ["CO"]
    assert third.llm.jobs == 1 and third.llm.reused == 1
    rows = prices(store)
    assert {rows[f"icloud_plus:200gb:{cc}:web:month"].extraction for cc in ICLOUD_200GB} == {"llm"}
    task = state(store).tasks[ICLOUD_TASK]
    assert sorted(task.llm_pairs) == ["icloud_plus:CO", "icloud_plus:SE", "icloud_plus:US"]
    assert task.llm_checked_hash == task.text_hash

    # Day 4: fully checked and unchanged, nothing to ask.
    clock.day = date(2026, 10, 11)
    seen = len(llm.requests)
    fourth = await W.run(deps, **opts)
    assert asked(llm, seen) == [] and fourth.llm.reused == 1


async def test_capped_multi_country_jobs_rotate_over_runs(
    cat: Catalog, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(nodes, "MAX_JOBS_PER_TASK", 1)
    break_parser(monkeypatch, "icloud_support")
    clock = W.Clock(DAY1)
    llm = ScriptedLlm(responder=lambda text: W.extraction([], status="no_prices"))
    deps = W.make_deps(cat, FixtureFetcher(W.routes()), model=llm, clock=clock)
    per_run: list[list[str]] = []
    for day in (8, 9, 10, 11):
        clock.day = date(2026, 10, day)
        seen = len(llm.requests)
        await W.run(deps, services=["icloud_plus"])
        per_run.append(asked(llm, seen))
    # One country per run, never the same one twice for the same page text, then nothing.
    assert per_run == [["SE"], ["CO"], ["US"], []]
    task = state(deps.store).tasks[ICLOUD_TASK]
    assert task.llm_checked_hash == task.text_hash
    assert {k: c.on.day for k, c in task.llm_pairs.items()} == {
        "icloud_plus:SE": 8,
        "icloud_plus:CO": 9,
        "icloud_plus:US": 10,
    }

    # A new page text: every pair needs a new answer, the least recently checked one first.
    deps.fetcher.routes[W.ICLOUD] = W.source("icloud_support", "108047_en_us.html").replace(
        "Learn about iCloud+ plans", "Learn all about iCloud+ plans"
    )
    clock.day = date(2026, 10, 12)
    seen = len(llm.requests)
    report = await W.run(deps, services=["icloud_plus"])
    assert asked(llm, seen) == ["SE"]
    assert report.llm.jobs == 1
    task = state(deps.store).tasks[ICLOUD_TASK]
    assert task.llm_checked_hash is None
    assert task.llm_pairs["icloud_plus:SE"].text_hash == task.text_hash
    assert task.llm_pairs["icloud_plus:CO"].text_hash != task.text_hash


async def test_quarantined_llm_key_is_not_released_without_a_fresh_answer(
    cat: Catalog,
) -> None:
    """Day 1: Spotify US exists only as LLM rows (llm_new_key). Day 2 runs without a model on
    the same page: a stored copy of day 1's answer must not count as the second sighting."""
    from test_pipeline import happy_responder

    clock = W.Clock(DAY1)
    fetcher = FixtureFetcher(W.routes())
    store = W.RecordingStore()
    opts = {"services": ["spotify"], "countries": ["US"]}
    llm = ScriptedLlm(responder=happy_responder)
    await W.run(W.make_deps(cat, fetcher, store=store, clock=clock, model=llm), **opts)
    key = "spotify:individual:US:web:month"
    assert [e["obs_key"] for e in read_json(store, "latest/review_queue.json")] == [
        f"spotify:{plan}:US:web:month" for plan in ("duo", "family", "individual")
    ]
    assert state(store).tasks["spotify_premium:spotify:US"].llm_obs == []

    clock.day = date(2026, 10, 9)
    await W.run(W.make_deps(cat, fetcher, store=store, clock=clock), **opts)
    assert key not in prices(store)
    assert state(store).keys[key].pending_value == "11.99|USD|month"

    # Day 3: a fresh LLM answer with the same value confirms it.
    clock.day = date(2026, 10, 10)
    seen = len(llm.requests)
    await W.run(W.make_deps(cat, fetcher, store=store, clock=clock, model=llm), **opts)
    assert asked(llm, seen) == ["US"]
    row = prices(store)[key]
    assert (str(row.amount), row.status, row.extraction) == ("11.99", "accepted", "llm")


async def test_stored_answers_survive_a_day_the_parser_covered_them(
    cat: Catalog, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Parser broken, then fixed, then broken again on the same page text: the LLM answer of
    day 2 is still reusable on day 4 (its pairs count as answered for that text)."""
    clock = W.Clock(DAY1)
    fetcher = FixtureFetcher(W.routes())
    store = W.RecordingStore()
    opts = {"services": ["icloud_plus"]}
    await W.run(W.make_deps(cat, fetcher, store=store, clock=clock), **opts)

    real = nodes.get_adapter
    llm = ScriptedLlm(responder=lambda text: icloud_answer(W.job_of(text)))
    deps = W.make_deps(cat, fetcher, store=store, clock=clock, model=llm)
    for day, broken in ((9, True), (10, False), (11, True)):
        monkeypatch.setattr(nodes, "get_adapter", real)
        if broken:
            break_parser(monkeypatch, "icloud_support")
        clock.day = date(2026, 10, day)
        await W.run(deps, **opts)
        task = state(store).tasks[ICLOUD_TASK]
        assert len(task.llm_obs) == 3, day  # the 200 GB answer of SE, CO and US
    assert sorted(asked(llm)) == ["CO", "SE", "US"]  # day 9 only
    rows = prices(store)
    assert {rows[f"icloud_plus:200gb:{cc}:web:month"].extraction for cc in ICLOUD_200GB} == {"llm"}
    assert {rows[f"icloud_plus:200gb:{cc}:web:month"].status for cc in ICLOUD_200GB} == {"accepted"}

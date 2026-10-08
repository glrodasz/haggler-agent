"""LLM work carried across runs (TaskState.llm_obs / llm_pairs / llm_checked_hash).

* Only pairs the LLM actually answered for today's page text are reused; an errored or
  budget-skipped job is asked again on the next run.
* A capped multi-country page rotates over its countries across runs.
* A quarantined LLM row is never confirmed by a copy of the same answer.
"""

from __future__ import annotations

import json
from datetime import date
from typing import Any

import pytest
import wf_support as W

from conftest import FixtureFetcher, ScriptedLlm
from haggler.models.catalog import Catalog
from haggler.models.observations import PublishedPrice
from haggler.models.run import PipelineState, TaskResult
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


ROTATION = {
    "BR": ("Brazil", "BRL"),
    "CA": ("Canada", "CAD"),
    "CL": ("Chile", "CLP"),
    "CO": ("Colombia", "COP"),
    "MX": ("Mexico", "MXN"),
    "PE": ("Peru", "PEN"),
}


def rotation_answer(text: str) -> dict[str, Any]:
    """The 200 GB price of the job's country, read from its focused page text."""
    job = W.job_of(text)
    name, currency = ROTATION[job["country"]]
    lines = job["page_text"].splitlines()
    start = next(i for i, ln in enumerate(lines) if ln.startswith(name))
    line = next(ln for ln in lines[start:] if ln.replace(" ", "").startswith("200GB:"))
    return W.extraction([W.item("200gb", "200GB", line.split(":", 1)[1].strip(), currency, line)])


async def test_countries_left_for_a_later_run_are_carried_like_a_failed_fetch(
    catalog: Catalog, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The iCloud parser breaks on a changed page and the LLM answers one country per run
    (MAX_JOBS_PER_TASK=1). The task counts as partial, but the countries nobody read today are
    not "absent from a good page": they stay published until the rotation reaches them and
    never come back as llm_new_key quarantines."""
    from decimal import Decimal

    from haggler.fx.service import StaticFxService
    from haggler.models.fx import FxRate, FxTable

    cat = Catalog(
        services=[catalog.service("icloud_plus")],
        countries=[catalog.country(cc) for cc in ROTATION],
        fx_pins={},
    )
    day0 = date(2026, 10, 1)
    rates = {"BRL": "6.2", "CAD": "1.5", "CLP": "1000", "COP": "4700", "MXN": "20", "PEN": "4.0"}
    fx = StaticFxService(
        FxTable(
            as_of=day0,
            rates={
                c: FxRate(currency=c, per_eur=Decimal(v), source="ecb", as_of=day0)
                for c, v in rates.items()
            },
        )
    )
    clock = W.Clock(day0)
    page = W.source("icloud_support", "108047_en_us.html")
    fetcher = FixtureFetcher({W.ICLOUD: page})
    store = W.RecordingStore()

    def deps(model: Any = None) -> Any:
        d = W.make_deps(cat, fetcher, store=store, clock=clock, model=model)
        d.fx = fx
        return d

    await W.run(deps())
    keys = {f"icloud_plus:200gb:{cc}:web:month" for cc in ROTATION}
    assert keys <= set(prices(store))

    monkeypatch.setattr(nodes, "MAX_JOBS_PER_TASK", 1)
    break_parser(monkeypatch, "icloud_support")
    fetcher.routes[W.ICLOUD] = page.replace("Learn about iCloud+ plans", "Learn all about iCloud+")
    llm = ScriptedLlm(responder=rotation_answer)
    for day in range(2, 12):
        clock.day = date(2026, 10, day)
        seen = len(llm.requests)
        await W.run(deps(llm))
        rows = prices(store)
        assert keys <= set(rows), day
        assert read_json(store, "latest/review_queue.json") == [], day
        if day <= 7:
            (cc,) = asked(llm, seen)
            cov = {r["country"]: r["status"] for r in read_json(store, "latest/coverage.json")}
            # Read so far: ok; not read yet: failed (not "not available" in that country).
            assert cov[cc] == "ok" and set(cov.values()) <= {"ok", "failed"}, day
            assert list(cov.values()).count("failed") == 7 - day, day
    assert len(llm.requests) == len(ROTATION)
    assert {rows[k].status for k in keys} == {"accepted"}
    assert {rows[k].extraction for k in keys} == {"llm"}


SPOTIFY_SE = "spotify_premium:spotify:SE"
SPOTIFY_SE_KEYS = [f"spotify:{p}:SE:web:month" for p in ("individual", "duo", "family", "student")]


def spotify_parser(monkeypatch: pytest.MonkeyPatch, mode: dict[str, str]) -> None:
    """mode["v"]: "real", "partial" (misses the default plan) or "none" (no_prices)."""
    real = nodes.get_adapter

    class Switchable:
        id = "spotify_premium"

        def plan(self, svc, src, catalog):
            return real(self.id).plan(svc, src, catalog)

        def parse(self, task, resp, catalog):
            pr = real(self.id).parse(task, resp, catalog)
            if mode["v"] == "partial":
                rows = [o for o in pr.observations if o.plan_id != "individual"]
                return ParseResult(status="partial", observations=rows, notes=["individual?"])
            if mode["v"] == "none":
                return ParseResult(status="no_prices", notes=["selectors broken"])
            return pr

    monkeypatch.setattr(
        nodes, "get_adapter", lambda a: Switchable() if a == "spotify_premium" else real(a)
    )


def spotify_se_answer(text: str) -> dict[str, Any]:
    lines = W.job_of(text)["page_text"].splitlines()
    items = []
    prices_se = (
        ("individual", "129 kr"),
        ("duo", "169 kr"),
        ("family", "209 kr"),
        ("student", "65 kr"),
    )
    for plan, price in prices_se:
        line = next((ln for ln in lines if f"{price}/månad" in ln), None)
        if line:
            items.append(W.item(plan, plan, price, "SEK", line))
    return W.extraction(items)


async def test_llm_answers_the_parser_also_had_are_stored_for_reuse(
    cat: Catalog, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Day 2: the parser misses only the default plan; the LLM answers all four plans. Days 3+:
    same page text, the parser reads nothing. The stored answer must still hold the three plans
    the parser had on day 2, so they are reused instead of aging out."""
    clock = W.Clock(date(2026, 10, 1))
    fetcher = FixtureFetcher(W.routes())
    store = W.RecordingStore()
    opts = {"services": ["spotify"], "countries": ["SE"]}
    mode = {"v": "real"}
    spotify_parser(monkeypatch, mode)
    await W.run(W.make_deps(cat, fetcher, store=store, clock=clock), **opts)
    assert set(SPOTIFY_SE_KEYS) <= set(prices(store))

    llm = ScriptedLlm(responder=spotify_se_answer)
    mode["v"] = "partial"
    clock.day = date(2026, 10, 2)
    await W.run(W.make_deps(cat, fetcher, store=store, clock=clock, model=llm), **opts)
    assert len(llm.requests) == 1
    stored = [d["obs_key"] for d in state(store).tasks[SPOTIFY_SE].llm_obs]
    assert sorted(stored) == sorted(SPOTIFY_SE_KEYS)
    assert prices(store)["spotify:duo:SE:web:month"].extraction == "parser"

    mode["v"] = "none"
    for day in range(3, 9):
        clock.day = date(2026, 10, day)
        report = await W.run(W.make_deps(cat, fetcher, store=store, clock=clock, model=llm), **opts)
        assert report.tasks_by_status == {"ok": 1}, day
        rows = prices(store)
        assert {rows[k].status for k in SPOTIFY_SE_KEYS} == {"accepted"}, day
        assert {rows[k].extraction for k in SPOTIFY_SE_KEYS} == {"llm"}, day
    assert len(llm.requests) == 1  # never asked again for the same page text


async def test_items_of_a_wrong_country_answer_are_never_used(cat: Catalog) -> None:
    """The model says the Netflix US page is another country's but still lists USD items: the
    items are rejected and the task is wrong_country. Nothing reaches prices.json, neither on a
    run that reuses the answer for the same text nor when the same answer comes again."""
    clock = W.Clock(DAY1)
    fetcher = FixtureFetcher(W.routes())
    store = W.RecordingStore()
    opts = {"services": ["netflix"], "countries": ["US"], "adapters": ["netflix_help"]}
    llm = ScriptedLlm(
        responder=lambda text: W.extraction(W.NETFLIX_US_ITEMS, status="wrong_country")
    )
    page = W.page("netflix_us_prose.html")
    changed = page.replace("Netflix offers", "Netflix proudly offers")
    for day, page_text, asked_today in ((8, page, True), (9, page, False), (10, changed, True)):
        clock.day = date(2026, 10, day)
        fetcher.routes[W.NETFLIX.format(cc="us")] = page_text
        seen = len(llm.requests)
        report = await W.run(W.make_deps(cat, fetcher, store=store, clock=clock, model=llm), **opts)
        assert len(llm.requests) - seen == int(asked_today), day
        assert report.tasks_by_status == {"wrong_country": 1}, day
        assert report.obs_by_status == {}, day
        assert not any(":US:" in k for k in prices(store)), day
        assert read_json(store, "latest/review_queue.json") == [], day
        (cov,) = read_json(store, "latest/coverage.json")
        assert (cov["status"], cov["failing_since"]) == ("wrong_country", "2026-10-08"), day
        check = state(store).tasks["netflix_help:netflix:US"].llm_pairs["netflix:US"]
        assert check.page_status == "wrong_country", day
        log = store.read_bytes(f"runs/{report.run_date}/{report.run_id}/llm.jsonl").decode()
        entries = [json.loads(line) for line in log.splitlines()]
        assert len(entries) == int(asked_today), day
        for entry in entries:
            assert entry["accepted"] == []
            assert {r["reason"] for r in entry["rejected"]} == {"page_status_wrong_country"}
            assert report.llm.accepted_items == 0 and report.llm.rejected_items == 3


async def test_a_wrong_country_answer_fails_only_its_pair_of_a_multi_country_page(
    cat: Catalog, monkeypatch: pytest.MonkeyPatch
) -> None:
    clock = W.Clock(DAY1)
    fetcher = FixtureFetcher(W.routes())
    store = W.RecordingStore()
    opts = {"services": ["icloud_plus"]}
    await W.run(W.make_deps(cat, fetcher, store=store, clock=clock), **opts)

    break_parser(monkeypatch, "icloud_support")

    def responder(text: str) -> Any:
        job = W.job_of(text)
        answer = icloud_answer(job)
        if job["country"] == "CO":
            answer["page_status"] = "wrong_country"
        return answer

    llm = ScriptedLlm(responder=responder)
    clock.day = date(2026, 10, 9)
    report = await W.run(W.make_deps(cat, fetcher, store=store, clock=clock, model=llm), **opts)
    assert report.tasks_by_status == {"partial": 1}
    assert report.llm.accepted_items == 2 and report.llm.rejected_items == 1
    data = store.read_bytes(f"runs/{report.run_date}/{report.run_id}/tasks.jsonl").decode()
    (result,) = [TaskResult.model_validate_json(line) for line in data.splitlines()]
    assert result.pair_status == {"icloud_plus:CO": "wrong_country"}
    cov = {r["country"]: r["status"] for r in read_json(store, "latest/coverage.json")}
    assert cov == {"CO": "wrong_country", "SE": "ok", "US": "ok"}
    rows = prices(store)
    co = rows["icloud_plus:200gb:CO:web:month"]
    assert (co.status, co.extraction) == ("carried_forward", "parser")
    assert state(store).keys["icloud_plus:200gb:CO:web:month"].missing_runs == 1
    assert rows["icloud_plus:200gb:SE:web:month"].extraction == "llm"

    # Same text the next day: every answer is reused, CO still counts as wrong_country.
    clock.day = date(2026, 10, 10)
    seen = len(llm.requests)
    await W.run(W.make_deps(cat, fetcher, store=store, clock=clock, model=llm), **opts)
    assert len(llm.requests) == seen
    cov = {r["country"]: r["status"] for r in read_json(store, "latest/coverage.json")}
    assert cov == {"CO": "wrong_country", "SE": "ok", "US": "ok"}
    assert state(store).keys["icloud_plus:200gb:CO:web:month"].missing_runs == 2

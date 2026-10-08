"""End-to-end runs of the ADK daily Workflow with fake fetcher, store, FX and model.

Nothing here touches the network or a real LLM: pages come from fixtures (FixtureFetcher) and
the model is tests/conftest.py's ScriptedLlm.
"""

from __future__ import annotations

import asyncio
import gzip
import hashlib
import json
from datetime import date
from typing import Any

import pytest
import wf_support as W

from conftest import FixtureFetcher, ScriptedLlm
from haggler.fetch import RawResponse
from haggler.fetch.errors import TransientFetchError
from haggler.models.catalog import Catalog
from haggler.models.fx import FxTable
from haggler.models.observations import PublishedPrice
from haggler.models.run import CoverageRow, PipelineState, RunReport, Snapshot, TaskResult
from haggler.pipeline.nodes import LATEST_FILES, LEGACY_LLM_CHECKED_KEY
from haggler.store import read_json

DAY1 = date(2026, 10, 8)

SPOTIFY_US_ITEMS = [
    W.item(
        "individual",
        "one account",
        "$11.99",
        "USD",
        "Ad-free listening for one account costs $11.99 a month after the offer period.",
    ),
    W.item("duo", "Two accounts", "$16.99", "USD", "Two accounts under one roof: $16.99 a month."),
    W.item("family", "six accounts", "$19.99", "USD", "Up to six accounts: $19.99 a month."),
]
SUPER_CHAT = W.item("other", "Super Chat", "$4.99", "USD", "Super Chat — $4.99")


def happy_responder(text: str) -> Any:
    job = W.job_of(text)
    key = (job.get("service_id"), job.get("country"), job.get("reason"))
    if key == ("netflix", "US", "parser_failed"):
        return W.extraction(W.NETFLIX_US_ITEMS)
    if key == ("spotify", "US", "parser_failed"):
        return W.extraction(SPOTIFY_US_ITEMS)
    if key == ("youtube_premium", "US", "unmapped_iap"):
        return W.extraction([SUPER_CHAT])
    return W.extraction([], status="no_prices")


@pytest.fixture
def cat(catalog: Catalog) -> Catalog:
    return W.subset_catalog(catalog)


def blobs_json(store: Any, key: str) -> Any:
    return read_json(store, key)


def prices(store: Any) -> dict[str, PublishedPrice]:
    rows = [PublishedPrice.model_validate(r) for r in read_json(store, "latest/prices.json")]
    return {r.obs_key: r for r in rows}


def run_dir(store: Any, report: RunReport) -> str:
    return f"runs/{report.run_date.isoformat()}/{report.run_id}"


def jobs_requested(llm: ScriptedLlm) -> list[tuple[str, str, str]]:
    out = []
    for text in llm.requests:
        job = W.job_of(text)
        out.append((job["service_id"], job["country"], job["reason"]))
    return out


def task_results(store: Any, report: RunReport) -> dict[str, TaskResult]:
    data = store.read_bytes(f"{run_dir(store, report)}/tasks.jsonl").decode()
    rows = [TaskResult.model_validate_json(line) for line in data.splitlines()]
    return {r.task_id: r for r in rows}


def llm_log(store: Any, report: RunReport) -> list[dict[str, Any]]:
    data = store.read_bytes(f"{run_dir(store, report)}/llm.jsonl").decode()
    return [json.loads(line) for line in data.splitlines()]


# ----------------------------------------------------------------------------- (1) layout


async def test_full_run_writes_documented_layout(cat: Catalog) -> None:
    llm = ScriptedLlm(responder=happy_responder)
    deps = W.make_deps(cat, FixtureFetcher(W.routes()), model=llm)
    report = await W.run(deps)
    store = deps.store

    assert report.tasks_total == 14
    assert report.success_ratio >= 0.9
    assert report.tasks_by_status == {"not_available": 1, "ok": 13}

    # latest/run.json is the very last write, after every other latest file.
    assert store.writes[-1] == "latest/run.json"
    latest_writes = [k for k in store.writes if k.startswith("latest/")]
    assert latest_writes == [*(f"latest/{n}" for n in LATEST_FILES), "latest/run.json"]
    manifest = read_json(store, "latest/run.json")
    assert manifest["schema_version"] == 1
    assert manifest["run_id"] == report.run_id
    assert manifest["as_of"] == "2026-10-08"
    assert manifest["finished_at"]
    assert set(manifest["files"]) == set(LATEST_FILES)
    for name, digest in manifest["files"].items():
        assert hashlib.sha256(store.blobs[f"latest/{name}"]).hexdigest() == digest
    assert manifest["report"]["tasks_total"] == 14
    assert manifest["report"]["success_ratio"] == pytest.approx(report.success_ratio, abs=1e-4)

    # prices.json: a JSON array, one key-sorted object per line, sorted by obs_key.
    text = store.blobs["latest/prices.json"].decode()
    lines = text.splitlines()
    assert lines[0] == "[" and lines[-1] == "]"
    objs = [json.loads(line.rstrip(",")) for line in lines[1:-1]]
    assert [o["obs_key"] for o in objs] == sorted(o["obs_key"] for o in objs)
    assert all(list(o) == sorted(o) for o in objs)
    rows = prices(store)
    assert all(r.first_seen == DAY1 and r.last_changed == DAY1 for r in rows.values())
    sek = rows["icloud_plus:200gb:SE:web:month"]
    assert (str(sek.amount), sek.currency, sek.extraction) == ("39", "SEK", "parser")
    assert "apple_billing" in sek.flags
    assert rows["netflix:standard:SE:web:month"].amount == 169
    assert rows["netflix:standard:SE:web:month"].source_kind == "official"
    us = rows["netflix:standard:US:web:month"]
    assert (str(us.amount), us.extraction, us.status) == ("19.99", "llm", "accepted")
    assert us.vat_included is False
    yt = rows["youtube_premium:individual:SE:ios_iap:month"]
    assert "iap_maybe_marked_up" in yt.flags and yt.channel == "ios_iap"

    # coverage.json: one row per (service, country, channel), sorted.
    cov = [CoverageRow.model_validate(r) for r in read_json(store, "latest/coverage.json")]
    triples = [(r.service_id, r.country, r.channel) for r in cov]
    assert triples == sorted(set(triples))
    assert len(cov) == len(W.SERVICES) * len(W.COUNTRIES)
    by = {(r.service_id, r.country): r for r in cov}
    assert by[("youtube_premium", "CO")].status == "not_available"
    assert by[("netflix", "US")].status == "ok"  # recovered by the LLM
    assert by[("netflix", "SE")].adapter_id == "netflix_help"  # official beats tompec

    FxTable.model_validate(read_json(store, "latest/fx.json"))
    # Spotify US exists only as LLM rows with no secondary source: new keys wait for review.
    queue = read_json(store, "latest/review_queue.json")
    assert [(e["obs_key"], e["rule"]) for e in queue] == [
        (f"spotify:{plan}:US:web:month", "llm_new_key") for plan in ("duo", "family", "individual")
    ]
    assert not any(k.startswith("spotify:") and ":US:" in k for k in rows)
    state = PipelineState.model_validate(read_json(store, "latest/state.json"))
    assert state.run_id == report.run_id and state.as_of == DAY1
    assert len(state.tasks) == 14
    assert state.tasks["netflix_help:netflix:SE"].text_hash
    assert "netflix:standard:SE:web:month" in state.keys

    # raw pages: gzipped, keyed raw/{date}/{adapter}/{task}.{html|json}.gz
    raw = gzip.decompress(
        store.blobs["raw/2026-10-08/netflix_help/netflix_help__netflix__SE.html.gz"]
    )
    assert raw.decode() == W.source("netflix_help", "se.html")
    assert "raw/2026-10-08/tompec_netflix/tompec_netflix__netflix__all.json.gz" in store.blobs
    assert not any("youtube_premium__CO" in k for k in store.blobs)  # 404: nothing to keep

    # runs/{date}/{run_id}/...
    prefix = run_dir(store, report)
    snap = Snapshot.model_validate(read_json(store, f"{prefix}/snapshot.json"))
    assert snap.run_id == report.run_id and snap.observations and snap.coverage
    assert RunReport.model_validate(read_json(store, f"{prefix}/report.json")).run_id == (
        report.run_id
    )
    assert len(task_results(store, report)) == 14
    assert len(llm_log(store, report)) == 3
    assert set(report.outputs) == {k for k in store.blobs if not k.startswith("raw/")}


async def test_not_configured_task_is_recorded_without_fetching(catalog: Catalog) -> None:
    cat = Catalog(
        services=[catalog.service("apple_one")],
        countries=[catalog.country("SE"), catalog.country("AR")],
    )
    fetcher = FixtureFetcher({W.APPLE_ONE["SE"]: W.source("apple_marketing", "se_apple_one.html")})
    deps = W.make_deps(cat, fetcher)
    report = await W.run(deps)
    assert fetcher.calls == [W.APPLE_ONE["SE"]]
    assert report.tasks_by_status == {"not_configured": 1, "ok": 1}
    tasks = task_results(deps.store, report)
    ar = tasks["apple_marketing:apple_one:AR"]
    assert ar.status == "not_configured" and ar.attempts == 0
    assert "apple_path" in ar.notes[0]
    cov = {r["country"]: r for r in read_json(deps.store, "latest/coverage.json")}
    assert cov["AR"]["status"] == "not_configured" and cov["AR"]["failing_since"] is None


# ----------------------------------------------------------------------------- (2) + (3) LLM


async def test_llm_only_for_failed_parsers_and_bad_json_is_contained(cat: Catalog) -> None:
    def responder(text: str) -> Any:
        job = W.job_of(text)
        if job["service_id"] == "spotify":
            return "Sure! Here are the prices: {not json"
        return happy_responder(text)

    llm = ScriptedLlm(responder=responder)
    deps = W.make_deps(cat, FixtureFetcher(W.routes()), model=llm)
    report = await W.run(deps)

    requested = jobs_requested(llm)
    assert set(requested) == {
        ("netflix", "US", "parser_failed"),
        ("spotify", "US", "parser_failed"),
        ("youtube_premium", "US", "unmapped_iap"),
    }
    # Malformed JSON: the agent retries once (RetryConfig on ValidationError), then the job is an
    # error record; the run goes on.
    assert requested.count(("spotify", "US", "parser_failed")) == 2
    assert report.llm.jobs == 3
    assert report.llm.calls == 4
    assert report.llm.errors == 1
    tasks = task_results(deps.store, report)
    assert tasks["spotify_premium:spotify:US"].status == "failed"
    assert any("llm error" in n for n in tasks["spotify_premium:spotify:US"].notes)
    assert tasks["netflix_help:netflix:US"].status == "ok"
    rows = prices(deps.store)
    assert rows["netflix:premium:US:web:month"].extraction == "llm"
    assert not any(k.startswith("spotify:") and ":US:" in k for k in rows)
    # Jobs never carry the parser's answer or the page text past the LLM node.
    assert all(job.page_text == "" for t in tasks.values() for job in t.llm_jobs)


async def test_post_checks_reject_llm_prices_not_on_the_page(cat: Catalog) -> None:
    fake = W.item(
        "standard",
        "ad-free option",
        "US$21.99",
        "USD",
        "The ad-free option that most households pick streams on two screens at once for "
        "US$21.99 every month.",
    )
    paraphrase = W.item(
        "premium", "top tier", "US$26.99", "USD", "Top tier with 4K: US$26.99 per month."
    )
    euro = W.item("basic", "basic", "€7.99", "EUR", "€7.99")
    other = W.item(
        "other", "gift card", "US$8.99", "USD", W.NETFLIX_US_ITEMS[0]["evidence_snippet"]
    )

    def responder(text: str) -> Any:
        job = W.job_of(text)
        if job["service_id"] == "netflix":
            return W.extraction([W.NETFLIX_US_ITEMS[0], fake, paraphrase, euro, other])
        return happy_responder(text)

    llm = ScriptedLlm(responder=responder)
    deps = W.make_deps(cat, FixtureFetcher(W.routes()), model=llm)
    report = await W.run(deps)

    entry = next(e for e in llm_log(deps.store, report) if e["job"]["service_id"] == "netflix")
    reasons = sorted(r["reason"] for r in entry["rejected"])
    assert reasons == [
        "evidence_not_on_page",
        "plan_not_allowed",
        "price_not_on_page",
        "unexpected_currency",
    ]
    assert [a["plan_id"] for a in entry["accepted"]] == ["standard_with_ads"]
    rows = prices(deps.store)
    assert all(r.amount != 21.99 for r in rows.values())
    # The only US standard/premium rows left are tompec's (secondary), never the LLM's.
    assert rows["netflix:standard:US:web:month"].source_kind == "secondary"
    assert rows["netflix:standard_with_ads:US:web:month"].extraction == "llm"
    assert report.llm.accepted_items == 1 + 3  # netflix + spotify US
    assert report.llm.rejected_items == 4 + 1  # netflix + the youtube "Super Chat" item


# ----------------------------------------------------------------------------- (4) reuse


async def test_unchanged_pages_reuse_llm_answers_but_new_keys_need_a_fresh_one(
    cat: Catalog,
) -> None:
    clock = W.Clock(DAY1)
    llm = ScriptedLlm(responder=happy_responder)
    deps = W.make_deps(cat, FixtureFetcher(W.routes()), model=llm, clock=clock)
    first = await W.run(deps)
    calls_first = len(llm.requests)
    assert calls_first == 3 and first.llm.calls == 3
    spotify_us = [f"spotify:{plan}:US:web:month" for plan in ("duo", "family", "individual")]
    queue = read_json(deps.store, "latest/review_queue.json")
    assert [e["obs_key"] for e in queue] == spotify_us  # llm_new_key

    # Day 2, same pages: Netflix and YouTube US reuse their answers; the quarantined Spotify US
    # keys are not released by a copy of yesterday's answer: exactly one confirming call.
    clock.day = date(2026, 10, 9)
    second = await W.run(deps)
    assert jobs_requested(llm)[calls_first:] == [("spotify", "US", "parser_failed")]
    assert second.llm.calls == 1 and second.llm.jobs == 1
    assert second.llm.reused == 2  # netflix US, youtube US (nothing usable, kept)
    rows = prices(deps.store)
    us = rows["netflix:standard:US:web:month"]
    assert us.extraction == "llm" and us.first_seen == DAY1 and us.last_changed == DAY1
    confirmed = rows["spotify:individual:US:web:month"]
    assert (str(confirmed.amount), confirmed.status, confirmed.extraction) == (
        "11.99",
        "accepted",
        "llm",
    )
    assert read_json(deps.store, "latest/review_queue.json") == []
    snap = Snapshot.model_validate(
        read_json(deps.store, f"{run_dir(deps.store, second)}/snapshot.json")
    )
    reused = {o.obs_key for o in snap.observations if o.reused}
    assert "netflix:standard:US:web:month" in reused
    assert not reused & set(spotify_us)
    state = PipelineState.model_validate(read_json(deps.store, "latest/state.json"))
    yt = state.tasks["appstore_iap:youtube_premium:US"]
    assert yt.llm_checked_hash == yt.text_hash and yt.llm_obs == []
    assert yt.llm_pairs["youtube_premium:US"].text_hash == yt.text_hash
    assert not any(LEGACY_LLM_CHECKED_KEY in d for t in state.tasks.values() for d in t.llm_obs)

    # Day 3: everything is confirmed and unchanged, no LLM call at all.
    clock.day = date(2026, 10, 10)
    third = await W.run(deps)
    assert len(llm.requests) == calls_first + 1
    assert third.llm.calls == 0 and third.llm.jobs == 0 and third.llm.reused == 3

    # A changed page goes back to the LLM.
    deps.fetcher.routes[W.NETFLIX.format(cc="us")] = W.page("netflix_us_prose.html").replace(
        "Netflix offers", "Netflix proudly offers"
    )
    clock.day = date(2026, 10, 11)
    fourth = await W.run(deps)
    assert jobs_requested(llm)[calls_first + 1 :] == [("netflix", "US", "parser_failed")]
    assert fourth.llm.reused == 2


# ----------------------------------------------------------------------------- (5) quarantine


async def test_big_change_is_quarantined_then_confirmed(cat: Catalog) -> None:
    clock = W.Clock(DAY1)
    fetcher = FixtureFetcher(W.routes())
    deps = W.make_deps(cat, fetcher, clock=clock)  # no model: no second opinion
    opts = {"services": ["netflix"], "countries": ["SE"]}
    await W.run(deps, **opts)
    assert prices(deps.store)["netflix:premium:SE:web:month"].amount == 219

    fetcher.routes[W.NETFLIX.format(cc="se")] = W.source("netflix_help", "se.html").replace(
        "219 kr", "299 kr"
    )
    clock.day = date(2026, 10, 9)
    second = await W.run(deps, **opts)
    queue = read_json(deps.store, "latest/review_queue.json")
    assert [e["obs_key"] for e in queue] == ["netflix:premium:SE:web:month"]
    assert queue[0]["rule"] == "big_change"
    held = prices(deps.store)["netflix:premium:SE:web:month"]
    assert (str(held.amount), held.status) == ("219", "carried_forward")
    assert "quarantine_hold" in held.flags and held.last_changed == DAY1
    assert second.obs_by_status.get("quarantined") == 1
    assert any(a.rule == "big_change" for a in second.anomalies)

    clock.day = date(2026, 10, 10)
    await W.run(deps, **opts)
    assert read_json(deps.store, "latest/review_queue.json") == []
    row = prices(deps.store)["netflix:premium:SE:web:month"]
    assert (str(row.amount), row.status) == ("299", "accepted")
    assert row.first_seen == DAY1 and row.last_changed == date(2026, 10, 10)
    assert "quarantine_hold" not in row.flags


async def test_second_opinion_that_agrees_releases_the_quarantine(cat: Catalog) -> None:
    clock = W.Clock(DAY1)
    fetcher = FixtureFetcher(W.routes())
    store = W.RecordingStore()
    opts = {"services": ["netflix"], "countries": ["SE"], "adapters": ["netflix_help"]}
    await W.run(W.make_deps(cat, fetcher, store=store, clock=clock), **opts)

    def responder(text: str) -> Any:
        job = W.job_of(text)
        assert job["reason"] == "second_opinion"
        line = next(ln for ln in job["page_text"].splitlines() if "299" in ln)
        return W.extraction([W.item("premium", "Premium", "299 kr", "SEK", line)])

    llm = ScriptedLlm(responder=responder)
    fetcher.routes[W.NETFLIX.format(cc="se")] = W.source("netflix_help", "se.html").replace(
        "219 kr", "299 kr"
    )
    clock.day = date(2026, 10, 9)
    report = await W.run(W.make_deps(cat, fetcher, store=store, clock=clock, model=llm), **opts)

    assert len(llm.requests) == 1
    assert '"compare_to"' not in llm.requests[0]  # the model does not see the parser's value
    row = prices(store)["netflix:premium:SE:web:month"]
    assert (str(row.amount), row.status) == ("299", "accepted")
    assert "second_opinion_agrees" in row.flags
    assert read_json(store, "latest/review_queue.json") == []
    assert any(a.rule == "second_opinion" and a.detail == "agrees" for a in report.anomalies)
    entry = llm_log(store, report)[0]
    assert entry["stage"] == "adj" and entry["job"]["compare_to"][0]["amount"] == "299"


async def test_second_opinion_that_disagrees_keeps_the_quarantine(cat: Catalog) -> None:
    clock = W.Clock(DAY1)
    fetcher = FixtureFetcher(W.routes())
    store = W.RecordingStore()
    opts = {"services": ["netflix"], "countries": ["SE"], "adapters": ["netflix_help"]}
    await W.run(W.make_deps(cat, fetcher, store=store, clock=clock), **opts)
    fetcher.routes[W.NETFLIX.format(cc="se")] = W.source("netflix_help", "se.html").replace(
        "219 kr", "299 kr"
    )
    llm = ScriptedLlm(responder=lambda text: W.extraction([]))
    clock.day = date(2026, 10, 9)
    await W.run(W.make_deps(cat, fetcher, store=store, clock=clock, model=llm), **opts)
    row = prices(store)["netflix:premium:SE:web:month"]
    assert str(row.amount) == "219" and "quarantine_hold" in row.flags
    assert [e["obs_key"] for e in read_json(store, "latest/review_queue.json")] == [
        "netflix:premium:SE:web:month"
    ]


# ----------------------------------------------------------------------------- (6) fetch errors


class FlakyFetcher(FixtureFetcher):
    """Raises TransientFetchError for the first `failures` requests of each listed URL."""

    def __init__(self, routes: dict[str, Any], flaky: dict[str, int]) -> None:
        super().__init__(routes)
        self.flaky = dict(flaky)

    async def fetch(self, url: str, *, accept: str = "text/html", egress: str = "default"):
        if self.flaky.get(url, 0) > 0:
            self.flaky[url] -= 1
            self.calls.append(url)
            raise TransientFetchError("HTTP 503", status=503, url=url)
        return await super().fetch(url, accept=accept, egress=egress)


async def test_transient_errors_are_retried_then_recorded_as_failed(cat: Catalog) -> None:
    broken = W.NETFLIX.format(cc="se")
    flaky = W.SPOTIFY.format(cc="se")
    routes = W.routes()
    routes[broken] = TransientFetchError("HTTP 503 Service Unavailable", status=503, url=broken)
    fetcher = FlakyFetcher(routes, {flaky: 1})
    deps = W.make_deps(cat, fetcher)
    report = await W.run(deps, services=["netflix", "spotify"], countries=["SE"])

    assert fetcher.calls.count(broken) == 3
    assert fetcher.calls.count(flaky) == 2
    tasks = task_results(deps.store, report)
    failed = tasks["netflix_help:netflix:SE"]
    assert (failed.status, failed.attempts, failed.http_status) == ("failed", 3, 503)
    assert failed.error and failed.error.startswith("transient")
    recovered = tasks["spotify_premium:spotify:SE"]
    assert (recovered.status, recovered.attempts) == ("ok", 2)
    assert report.tasks_by_status == {"failed": 1, "ok": 2}
    cov = {
        (r["service_id"], r["country"]): r for r in read_json(deps.store, "latest/coverage.json")
    }
    assert cov[("netflix", "SE")]["status"] == "failed"
    assert cov[("netflix", "SE")]["failing_since"] == "2026-10-08"
    # The secondary dataset still provides the Netflix SE prices.
    assert prices(deps.store)["netflix:standard:SE:web:month"].source_kind == "secondary"


async def test_failing_since_is_kept_across_runs(cat: Catalog) -> None:
    clock = W.Clock(DAY1)
    broken = W.SPOTIFY.format(cc="se")
    routes = W.routes()
    routes[broken] = RawResponse(
        url=broken,
        final_url=broken,
        status_code=200,
        text=W.source("spotify_premium", "blocked.html"),
    )
    deps = W.make_deps(cat, FixtureFetcher(routes), clock=clock)
    opts = {"services": ["spotify"], "countries": ["SE"]}
    await W.run(deps, **opts)
    clock.day = date(2026, 10, 9)
    await W.run(deps, **opts)
    cov = read_json(deps.store, "latest/coverage.json")
    assert cov == [
        {
            "adapter_id": "spotify_premium",
            "channel": "web",
            "country": "SE",
            "failing_since": "2026-10-08",
            "service_id": "spotify",
            "status": "blocked",
        }
    ]
    state = PipelineState.model_validate(read_json(deps.store, "latest/state.json"))
    assert state.tasks["spotify_premium:spotify:SE"].failing_since == DAY1


async def test_a_filtered_run_first_does_not_hide_that_days_outage(cat: Catalog) -> None:
    """Netflix SE is down from day 2. On day 2 a manual --services spotify run comes before the
    scheduled full run: the full run must still count day 2 for the Netflix keys."""
    from haggler.fetch.errors import FetchError

    key = "netflix:standard:SE:web:month"
    clock = W.Clock(DAY1)
    fetcher = FixtureFetcher(W.routes())
    store = W.RecordingStore()

    def missing_runs() -> int:
        return (
            PipelineState.model_validate(read_json(store, "latest/state.json"))
            .keys[key]
            .missing_runs
        )

    await W.run(W.make_deps(cat, fetcher, store=store, clock=clock))
    assert missing_runs() == 0
    fetcher.routes[W.NETFLIX.format(cc="se")] = FetchError("HTTP 500", status=500, url="x")
    clock.day = date(2026, 10, 9)
    await W.run(W.make_deps(cat, fetcher, store=store, clock=clock), services=["spotify"])
    assert missing_runs() == 0  # not covered by that run
    await W.run(W.make_deps(cat, fetcher, store=store, clock=clock))
    assert missing_runs() == 1
    await W.run(W.make_deps(cat, fetcher, store=store, clock=clock))
    assert missing_runs() == 1  # a same-day retry counts nothing again
    clock.day = date(2026, 10, 10)
    await W.run(W.make_deps(cat, fetcher, store=store, clock=clock))
    assert missing_runs() == 2
    assert prices(store)[key].status == "carried_forward"


# ----------------------------------------------------------------------------- (7) dry run


async def test_dry_run_writes_nothing(cat: Catalog) -> None:
    llm = ScriptedLlm(responder=happy_responder)
    deps = W.make_deps(cat, FixtureFetcher(W.routes()), model=llm)
    report = await W.run(deps, dry_run=True)
    assert deps.store.blobs == {}
    assert report.dry_run is True and report.outputs == []
    assert report.tasks_total == 14 and report.llm.accepted_items == 6


# ----------------------------------------------------------------------------- (9) budgets


async def test_max_llm_calls_is_respected(cat: Catalog) -> None:
    llm = ScriptedLlm(responder=happy_responder)
    deps = W.make_deps(cat, FixtureFetcher(W.routes()), model=llm, max_llm_calls=1)
    report = await W.run(deps)
    assert len(llm.requests) == 1
    assert report.llm.calls == 1
    assert report.llm.skipped_budget == 2
    assert report.llm.errors == 0
    assert read_json(deps.store, "latest/run.json")["run_id"] == report.run_id


async def test_max_llm_jobs_keeps_the_highest_priority_jobs(cat: Catalog) -> None:
    llm = ScriptedLlm(responder=happy_responder)
    deps = W.make_deps(cat, FixtureFetcher(W.routes()), model=llm, max_llm_jobs=1)
    report = await W.run(deps)
    # parser_failed (priority 3) beats unmapped_iap (1); ties break on job id.
    assert jobs_requested(llm) == [("netflix", "US", "parser_failed")]
    assert report.llm.jobs == 3 and report.llm.skipped_budget == 2


async def test_without_a_model_jobs_are_skipped(cat: Catalog) -> None:
    deps = W.make_deps(cat, FixtureFetcher(W.routes()), model=None)
    report = await W.run(deps)
    assert report.llm.jobs == 3 and report.llm.skipped_budget == 3 and report.llm.calls == 0
    tasks = task_results(deps.store, report)
    assert tasks["netflix_help:netflix:US"].status == "failed"
    # tompec (secondary) still covers Netflix US.
    assert prices(deps.store)["netflix:standard:US:web:month"].source_kind == "secondary"


async def test_pubsub_envelope_request(cat: Catalog) -> None:
    from google.adk import Runner
    from google.adk.sessions import InMemorySessionService
    from google.genai import types

    from haggler.pipeline.app import build_app

    deps = W.make_deps(cat, FixtureFetcher(W.routes()))
    app = build_app(deps)
    body = {"data": {"services": ["spotify"], "countries": ["SE"], "dry_run": True}}
    msg = types.UserContent(parts=[types.Part(text=json.dumps(body))])
    final = None
    async with Runner(
        app=app, session_service=InMemorySessionService(), auto_create_session=True
    ) as runner:
        async for ev in runner.run_async(user_id="u", session_id="s", new_message=msg):
            if ev.output is not None and "daily_prices@1" in (ev.node_info.output_for or []):
                final = ev.output
    report = RunReport.model_validate(final)
    assert report.tasks_total == 1 and report.dry_run
    assert deps.fetcher.calls == [W.SPOTIFY.format(cc="se")]


class SlowFetcher(FixtureFetcher):
    async def fetch(self, url: str, *, accept: str = "text/html", egress: str = "default"):
        if "spotify" in url:
            self.calls.append(url)
            await asyncio.sleep(5)
        return await super().fetch(url, accept=accept, egress=egress)


async def test_fetch_timeouts_are_transient(cat: Catalog) -> None:
    fetcher = SlowFetcher(W.routes())
    deps = W.make_deps(cat, fetcher)
    deps.fetch_timeout_s = 0.05
    report = await W.run(deps, services=["spotify", "apple_one"], countries=["SE"])
    assert fetcher.calls.count(W.SPOTIFY.format(cc="se")) == 3
    tasks = task_results(deps.store, report)
    assert tasks["spotify_premium:spotify:SE"].status == "failed"
    assert "timeout" in (tasks["spotify_premium:spotify:SE"].error or "")
    assert tasks["apple_marketing:apple_one:SE"].status == "ok"


async def test_parser_crash_falls_back_to_the_llm(
    cat: Catalog, monkeypatch: pytest.MonkeyPatch
) -> None:
    from haggler.pipeline import nodes
    from haggler.sources.registry import get_adapter

    class Exploding:
        id = "netflix_help"

        def plan(self, svc, src, catalog):
            return get_adapter("netflix_help").plan(svc, src, catalog)

        def parse(self, task, resp, catalog):
            raise RuntimeError("selector exploded")

    real = nodes.get_adapter
    monkeypatch.setattr(
        nodes, "get_adapter", lambda aid: Exploding() if aid == "netflix_help" else real(aid)
    )

    def responder(text: str) -> Any:
        line = next(ln for ln in W.job_of(text)["page_text"].splitlines() if "169 kr" in ln)
        return W.extraction([W.item("standard", "Standard", "169 kr", "SEK", line)])

    llm = ScriptedLlm(responder=responder)
    deps = W.make_deps(cat, FixtureFetcher(W.routes()), model=llm)
    report = await W.run(deps, services=["netflix"], countries=["SE"], adapters=["netflix_help"])
    task = task_results(deps.store, report)["netflix_help:netflix:SE"]
    assert any("parser crashed: RuntimeError" in n for n in task.notes)
    assert task.status == "ok" and [o.extraction for o in task.observations] == ["llm"]
    assert jobs_requested(llm) == [("netflix", "SE", "parser_failed")]


async def test_a_run_with_no_tasks_still_reports(cat: Catalog) -> None:
    deps = W.make_deps(cat, FixtureFetcher(W.routes()))
    report = await W.run(deps, services=["no_such_service"])
    assert report.tasks_total == 0 and report.success_ratio == 0
    assert report.errors == ["filter: unknown service no_such_service"]
    assert deps.store.writes[-1] == "latest/run.json"
    assert read_json(deps.store, "latest/prices.json") == []

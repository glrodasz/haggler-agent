"""The research Workflow end to end with a grounded fake Gemini and canned pages."""

from __future__ import annotations

import dataclasses
import json
from collections.abc import AsyncGenerator, Callable
from datetime import UTC, date, datetime
from decimal import Decimal
from typing import Any

import pytest
import wf_support as W
from google.adk.models import LlmCapabilities
from google.adk.models.base_llm import BaseLlm
from google.adk.models.llm_response import LlmResponse
from google.genai import types

from conftest import FixtureFetcher, _request_text
from haggler.catalog import load_catalog, load_feasibility, load_payment_instruments, load_profiles
from haggler.config import Settings
from haggler.fetch import RawResponse
from haggler.fetch.errors import BlockedError, RobotsDisallowedError
from haggler.jobs.research import ResearchOptions, run_research
from haggler.models.observations import PublishedPrice
from haggler.models.research import SubjectFacts
from haggler.research import io
from haggler.research.deps import ResearchDeps
from haggler.store import dumps, dumps_lines
from pdf_support import build_pdf

TODAY = date(2026, 10, 9)
TERMS = "https://help.netflix.com/legal/termsofuse"
PAY_CO = "https://help.netflix.com/en/node/123/co"
PRESS = "https://www.techradar.com/news/netflix-crackdown"
CLAUSE = (
    "You may use the Netflix service primarily within the country in which you have "
    "established your account"
)
MUST_MATCH = "Your payment card must be issued in the country of your Netflix account."
EVENT = "Netflix began cancelling accounts registered in another country in March 2026."
PAGES = {
    TERMS: f"<html><body><main><p>4.3 {CLAUSE}.</p></main></body></html>",
    PAY_CO: f"<html><body><h1>Paying in Colombia</h1><p>{MUST_MATCH}</p></body></html>",
    PRESS: f"<html><body><article><p>{EVENT}</p></article></body></html>",
}


def claim(field: str, value: str, quote: str, url: str, event_date: str = "") -> dict[str, str]:
    return {"field": field, "value": value, "event_date": event_date, "quote": quote, "url": url}


def answer(*claims: dict[str, str], status: str = "found") -> dict[str, Any]:
    return {
        "status": status,
        "claims": list(claims),
        "open_questions": ["Is the location checked at sign-up?"] if claims else [],
        "suggested_instruments": [],
        "notes": "",
    }


SERVICE_ANSWER = answer(
    claim("residence_clause", "primary_use_in_account_country", CLAUSE, TERMS),
    claim("enforcement_event", "cancellation", EVENT, PRESS, "2026-03-01"),
    # Hallucinated: the quote is not on the page.
    claim(
        "vpn_stance", "prohibited", "Using a VPN to access Netflix is strictly forbidden.", TERMS
    ),
)
PAIR_ANSWER = answer(claim("payment_card_country", "must_match", MUST_MATCH, PAY_CO))


def subject_of(text: str) -> str:
    try:
        return json.loads(text[text.index("{") :])["subject"]
    except (ValueError, KeyError):
        return ""


class GroundedGemini(BaseLlm):
    """Fake Gemini with google_search: responder(subject) -> answer dict or raw text."""

    model: str = "gemini-3-test"
    responder: Callable[[str], Any] | None = None
    requests: list[str] = []  # noqa: RUF012 - pydantic copies field defaults

    @property
    def capabilities(self) -> LlmCapabilities:
        return LlmCapabilities(output_schema_and_tools=True)

    async def generate_content_async(
        self, llm_request: Any, stream: bool = False
    ) -> AsyncGenerator[LlmResponse, None]:
        text = _request_text(llm_request)
        self.requests.append(text)
        assert any(t.google_search for t in llm_request.config.tools or [])
        reply = (
            self.responder(subject_of(text)) if self.responder else answer(status="nothing_found")
        )
        if not isinstance(reply, str):
            reply = json.dumps(reply)
        yield LlmResponse(
            content=types.ModelContent(parts=[types.Part(text=reply)]),
            finish_reason=types.FinishReason.STOP,
            grounding_metadata=types.GroundingMetadata(
                web_search_queries=["q1", "q2"],
                grounding_chunks=[
                    types.GroundingChunk(
                        web=types.GroundingChunkWeb(
                            uri="https://vertexaisearch.cloud.google.com/grounding-api-redirect/x",
                            domain="netflix.com",
                        )
                    )
                ],
            ),
            usage_metadata=types.GenerateContentResponseUsageMetadata(
                prompt_token_count=100, candidates_token_count=50
            ),
        )


def default_responder(subject: str) -> Any:
    if subject == "service:netflix":
        return SERVICE_ANSWER
    if subject == "pair:netflix:CO":
        return PAIR_ANSWER
    return answer(status="nothing_found")


def prices_store() -> W.RecordingStore:
    """latest/prices.json + fx.json as the daily job leaves them: Netflix CO is much cheaper."""
    store = W.RecordingStore()
    rows = []
    for cc, cur, amount in (("SE", "SEK", "169"), ("CO", "COP", "26900")):
        rows.append(
            PublishedPrice.model_validate(
                {
                    "obs_key": f"netflix:standard:{cc}:web",
                    "service_id": "netflix",
                    "plan_id": "standard",
                    "plan_label_raw": "Standard",
                    "country": cc,
                    "currency": cur,
                    "amount": amount,
                    "billing_period": "month",
                    "monthly_equiv": amount,
                    "vat_included": True,
                    "channel": "web",
                    "source_url": "https://help.netflix.com/node/24926",
                    "source_kind": "official",
                    "adapter_id": "netflix_help",
                    "extraction": "parser",
                    "confidence": 1.0,
                    "status": "accepted",
                    "first_seen": "2026-10-01",
                    "last_changed": "2026-10-01",
                }
            )
        )
    store.blobs["latest/prices.json"] = dumps_lines(rows)
    store.blobs["latest/fx.json"] = dumps(W.fx_table(TODAY))
    return store


def make_deps(
    *,
    model: Any = None,
    store: Any = None,
    routes: dict[str, Any] | None = None,
    day: date = TODAY,
    **settings: Any,
) -> ResearchDeps:
    overrides = {
        "store": "memory",
        "research_max_calls": 10,
        "research_max_queries": 100,
        "research_max_month_queries": 1500,
        "research_concurrency": 2,
        "research_max_pairs": 1,
        "research_max_services": 1,
        "research_max_instruments": 0,
    } | settings
    return ResearchDeps(
        settings=dataclasses.replace(Settings(), **overrides),
        catalog=load_catalog(),
        feasibility=load_feasibility(),
        instruments=load_payment_instruments(),
        profiles=load_profiles(),
        fetcher=FixtureFetcher(PAGES if routes is None else routes),
        store=store if store is not None else prices_store(),
        model=model,
        now=lambda: datetime(day.year, day.month, day.day, 5, 23, tzinfo=UTC),
    )


async def run(deps: ResearchDeps, **opts: Any):
    report = await run_research(ResearchOptions(**opts), deps)
    assert report is not None
    return report


def latest(store: Any) -> dict[str, SubjectFacts]:
    return io.load_latest(store).subjects


# ------------------------------------------------------------------------- tests


async def test_full_run_persists_only_verified_claims():
    llm = GroundedGemini(responder=default_responder)
    deps = make_deps(model=llm)
    report = await run(deps)
    # The generic profile has no curated Netflix CO record: the pair is planned, saving first.
    assert report.planned == ["pair:netflix:CO", "service:netflix"]
    assert report.by_status == {"researched": 2}
    assert report.claims_verified == 3 and report.claims_unverified == 1
    assert report.checks == {
        "official": {"quote_not_found": 1, "verified": 2},
        "press": {"verified": 1},
    }
    assert report.budget.calls == 2 and report.budget.queries == 4
    assert report.budget.prompt_tokens == 200

    store = deps.store
    assert store.writes[-1] == "research/latest/run.json"
    assert store.writes.index("research/runs/2026-10-09/" + report.run_id + "/report.json") < (
        store.writes.index("research/latest/services.json")
    )
    facts = latest(store)
    svc = facts["service:netflix"]
    assert [c.field for c in svc.claims] == ["enforcement_event", "residence_clause"]
    assert svc.claims[0].check.source_class == "press"
    assert [u.status for u in svc.unverified] == ["quote_not_found"]
    assert svc.open_questions == ["Is the location checked at sign-up?"]
    pair = facts["pair:netflix:CO"]
    assert pair.claims[0].value == "must_match" and pair.claims[0].check.source_class == "official"
    state = io.load_latest(store).state
    assert io.read_usage(store, "2026-10") == {"calls": 2, "queries": 4, "runs": 1}
    assert report.budget.month == "2026-10" and report.budget.month_queries == 4
    assert state.subjects["service:netflix"].last_success == TODAY
    # Raw pages are kept for audits.
    assert any(k.startswith("raw/research/2026-10-09/") for k in store.blobs)
    manifest = json.loads(store.blobs["research/latest/run.json"])
    assert manifest["files"]["services.json"] == io.sha256_hex(
        store.blobs["research/latest/services.json"]
    )


async def test_quote_in_an_official_pdf_verifies_and_its_bytes_are_kept():
    # Phase 3 (H4) changes Phase 2 on purpose: a PDF used to be "unsupported_content".
    pdf = build_pdf([["Netflix Terms of Use", f"4.3 {CLAUSE}."]])
    routes = dict(PAGES)
    routes[TERMS] = RawResponse(
        url=TERMS,
        final_url=TERMS,
        status_code=200,
        text=pdf.decode("utf-8", "replace"),
        content_type="application/pdf",
        body=pdf,
    )
    deps = make_deps(model=GroundedGemini(responder=default_responder), routes=routes)
    await run(deps)
    svc = latest(deps.store)["service:netflix"]
    clause = next(c for c in svc.claims if c.field == "residence_clause")
    assert clause.check.status == "verified" and clause.check.source_class == "official"
    # The PDF's bytes are kept as fetched, under the same 30-day raw/research/ prefix.
    key = io.raw_key("2026-10-09", TERMS, "pdf")
    assert key.startswith("raw/research/2026-10-09/") and key.endswith(".pdf")
    assert deps.store.blobs[key] == pdf
    assert io.raw_key("2026-10-09", TERMS) == key.removesuffix("pdf") + "html.gz"


async def test_same_day_rerun_plans_nothing():
    llm = GroundedGemini(responder=default_responder)
    deps = make_deps(model=llm)
    first = await run(deps)
    again = await run(deps)
    # Nothing researched today is asked again; the next due service takes the free slot.
    assert not set(first.planned) & set(again.planned)
    assert again.planned == ["service:youtube_premium"]


async def test_explicit_request_ignores_freshness():
    llm = GroundedGemini(responder=default_responder)
    deps = make_deps(model=llm)
    await run(deps)
    again = await run(deps, services=["netflix"])
    assert again.planned == ["service:netflix"] and len(llm.requests) == 3


async def test_query_budget_skips_later_subjects():
    llm = GroundedGemini(responder=default_responder)
    deps = make_deps(model=llm, research_max_queries=2, research_concurrency=1)
    report = await run(deps)
    assert report.by_status == {"researched": 1, "skipped": 1}
    assert report.skipped_reasons["service:netflix"] == "max_search_queries"
    assert "service:netflix" not in latest(deps.store)


async def test_monthly_guard_plans_nothing():
    store = prices_store()
    io.add_usage(store, "2026-10", calls=300, queries=1500)
    llm = GroundedGemini(responder=default_responder)
    report = await run(make_deps(model=llm, store=store))
    assert report.planned == [] and "monthly guard" in report.skipped_reasons["*"]
    assert llm.requests == []
    # A backdated run is still counted against the real month (and cannot reset it).
    late = await run(make_deps(model=llm, store=store), date=date(2026, 9, 30))
    assert late.planned == [] and llm.requests == []
    assert io.read_usage(store, "2026-09")["queries"] == 0


async def test_dry_runs_count_against_the_monthly_guard():
    store = prices_store()
    llm = GroundedGemini(responder=default_responder)
    for _ in range(2):
        await run(make_deps(model=llm, store=store), dry_run=True)
    assert io.read_usage(store, "2026-10") == {"calls": 4, "queries": 8, "runs": 2}
    small = make_deps(model=llm, store=store, research_max_month_queries=10)
    report = await run(small)
    # Only 2 queries were left: the first answer exhausts them, the second subject is skipped.
    assert report.budget.max_queries == 2 and report.by_status.get("skipped") == 1


@pytest.mark.parametrize(
    "error",
    [
        RobotsDisallowedError("robots", url=TERMS),
        BlockedError("bot wall", status=403, url=TERMS),
    ],
)
async def test_unreachable_pages_end_unverified(error):
    llm = GroundedGemini(responder=default_responder)
    deps = make_deps(model=llm, routes={**PAGES, TERMS: error})
    report = await run(deps)
    svc = latest(deps.store)["service:netflix"]
    assert [c.field for c in svc.claims] == ["enforcement_event"]
    assert {u.status for u in svc.unverified} <= {"robots_disallowed", "blocked"}
    assert report.by_status == {"researched": 2}


async def test_malformed_answer_is_retried_once():
    attempts: dict[str, int] = {}

    def flaky(subject: str) -> Any:
        attempts[subject] = attempts.get(subject, 0) + 1
        if subject == "service:netflix" and attempts[subject] == 1:
            return '{"status": "found", "claims": "oops"}'
        return default_responder(subject)

    llm = GroundedGemini(responder=flaky)
    deps = make_deps(model=llm)
    report = await run(deps)
    assert attempts["service:netflix"] == 2
    assert report.by_status == {"researched": 2}
    assert report.budget.calls == 3 and report.budget.queries == 6


async def test_failures_keep_previous_facts_and_pause_after_three():
    llm = GroundedGemini(responder=default_responder)
    deps = make_deps(model=llm)
    await run(deps)
    before = latest(deps.store)["service:netflix"]

    llm.responder = lambda subject: "not json at all"
    for n, day in enumerate((date(2026, 10, 10), date(2026, 10, 11), date(2026, 10, 12)), 1):
        d = dataclasses.replace(
            deps, model=llm, now=lambda day=day: datetime(day.year, day.month, day.day, tzinfo=UTC)
        )
        report = await run(d, services=["netflix"])
        assert report.by_status == {"failed": 1}
        st = io.load_latest(deps.store).state.subjects["service:netflix"]
        assert st.consecutive_failures == n
    assert latest(deps.store)["service:netflix"] == before
    assert st.paused_until == date(2026, 11, 9)


async def test_previous_facts_are_rechecked():
    llm = GroundedGemini(responder=default_responder)
    deps = make_deps(model=llm)
    await run(deps)
    # The clause disappears from the terms page; the agent finds nothing this time.
    llm.responder = lambda subject: answer(status="nothing_found")
    routes = {**PAGES, TERMS: "<html><body><p>New terms.</p></body></html>"}
    d = dataclasses.replace(deps, model=llm, fetcher=FixtureFetcher(routes))
    report = await run(d, services=["netflix"])
    svc = latest(deps.store)["service:netflix"]
    assert [c.field for c in svc.claims] == ["enforcement_event"]  # still on the press page
    assert any(u.detail == "no longer on the page" for u in svc.unverified)
    assert report.by_status == {"researched": 1}


async def test_no_llm_and_dry_run():
    deps = make_deps(model=None)
    report = await run(deps)
    assert report.by_status == {"skipped": 2}
    assert report.skipped_reasons["pair:netflix:CO"] == "llm_off"

    llm = GroundedGemini(responder=default_responder)
    store = prices_store()
    dry = await run(make_deps(model=llm, store=store), dry_run=True)
    assert dry.by_status == {"researched": 2} and dry.outputs == []
    # Only the month's spending is recorded (research/usage/): no facts, state or raw pages.
    written = [k for k in store.blobs if k.startswith(("research/", "raw/"))]
    assert written == ["research/usage/2026-10.json"]


async def test_research_app_is_offline_safe():
    import subprocess
    import sys

    code = "import haggler.research.app as a; print(a.root_agent.name)"
    out = subprocess.run(
        [sys.executable, "-c", code],
        capture_output=True,
        text=True,
        check=True,
        env={"PATH": "", "HAGGLER_LLM": "off", "HOME": "/nonexistent"},
    )
    assert out.stdout.strip() == "feasibility_research"


def test_savings_threshold_selects_pairs():
    from haggler.research.plan import pair_savings

    store = prices_store()
    prices = [
        PublishedPrice.model_validate(r) for r in json.loads(store.blobs["latest/prices.json"])
    ]
    savings = pair_savings(
        prices, W.fx_table(TODAY), load_catalog(), load_feasibility(), load_profiles()
    )
    assert set(savings) == {("netflix", "CO")}
    assert savings[("netflix", "CO")] > Decimal("5")


async def test_previous_claim_is_kept_while_its_quote_still_verifies():
    """Same fact, differently quoted: the stored claim (and its review hash) stays."""
    llm = GroundedGemini(responder=default_responder)
    deps = make_deps(model=llm)
    await run(deps)
    before = latest(deps.store)["service:netflix"].claim(
        "service:netflix:residence_clause:primary_use_in_account_country"
    )
    other = "You may use the Netflix service primarily within the country"
    llm.responder = lambda subject: answer(
        claim("residence_clause", "primary_use_in_account_country", other, TERMS)
    )
    d = dataclasses.replace(deps, model=llm)
    await run(d, services=["netflix"])
    after = latest(deps.store)["service:netflix"].claim(before.id)
    assert after.hash == before.hash and after.quote == before.quote


async def test_an_unknown_pair_is_reported_and_the_rest_researched():
    llm = GroundedGemini(responder=default_responder)
    deps = make_deps(model=llm)
    from haggler.models.catalog import Catalog

    cat = deps.catalog
    # The requested pair's country is not in the catalog.
    deps.catalog = Catalog(
        services=cat.services, countries=[c for c in cat.countries if c.cc != "CO"]
    )
    report = await run(deps, pairs=["netflix:CO"], services=["netflix"])
    assert report.by_status == {"researched": 1}
    assert "request: unknown pair netflix:CO" in report.errors

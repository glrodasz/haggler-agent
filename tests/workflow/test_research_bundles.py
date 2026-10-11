"""The research Workflow with bundle subjects (phase 3, H8): the grounded fake Gemini on
bundle:meli_plus_co_total (the official plans page as the bundles probe read it, plus a price list
PDF built in the test), the value hook, the run order, the per-run bundle cap, a spent budget
and the recheck of the bundles a run does not research."""

from __future__ import annotations

import json
from datetime import date
from typing import Any

import pytest
import yaml
from test_research import (
    BUNDLE_COMPONENT,
    BUNDLE_PAGE,
    BUNDLE_PRICE,
    PAGES,
    TODAY,
    GroundedGemini,
    answer,
    claim,
    default_responder,
    latest,
    make_deps,
    prices_store,
    run,
    subject_of,
    watched_bundles,
)

from haggler.bundles.proposals import bundle_proposals, snippet
from haggler.catalog import integrity_errors, load_bundles, load_catalog
from haggler.fetch import RawResponse
from haggler.models.curated import BundlesFile
from haggler.models.research import ReviewDecision, ReviewFile
from haggler.research import io
from pdf_support import build_pdf

KEY = "bundle:meli_plus_co_total"
PLANES = "https://www.mercadolibre.com.co/suscripciones/melimas/planes"
PRICE_LIST = "https://www.mercadolibre.com.co/l/meli-plus-precios.pdf"
# Lines of the bundles probe on the official Meli+ CO plans page (H0, 2026-10-09).
PRICE_QUOTE = (
    "Precio actual con 9 por ciento de descuento: 19900 pesos por mes, por 2 meses. "
    "Precio anterior: $ 21.900 pesos por mes."
)
DISNEY = "Disney+ Estándar con anuncios."
DISCOUNTS = "30% OFF en HBO Max, Universal+, Paramount+ y ViX Premium."
TOO_CHEAP = "Meli+ Total cuesta $ 1.000 por mes."
CLAUSE = "Meli+ está disponible únicamente para residentes en Colombia."
PLANES_HTML = f"""<html><body><main>
<section><h2>Meli+ Total</h2>
<p>$ 19.900/mes por 2 meses</p>
<p>{PRICE_QUOTE}</p>
<ul><li>{DISNEY}</li><li>{DISCOUNTS}</li></ul>
<p>{CLAUSE}</p>
</section>
<section><h2>Meli+ Esencial</h2><p>$ 9.900/mes</p></section>
<section><p>{TOO_CHEAP}</p></section>
</main></body></html>"""
LIST_LINE = "Meli+ Total: $ 21.900 por mes, IVA incluido."
PDF = build_pdf([["Lista de precios Meli+ Colombia", LIST_LINE]])
MELI_ROUTES: dict[str, Any] = {
    **PAGES,
    PLANES: PLANES_HTML,
    PRICE_LIST: RawResponse(
        url=PRICE_LIST,
        final_url=PRICE_LIST,
        status_code=200,
        text=PDF.decode("utf-8", "replace"),
        content_type="application/pdf",
        body=PDF,
    ),
}
MELI_ANSWER = answer(
    claim("bundle_price", "21900 COP/month", PRICE_QUOTE, PLANES),
    claim("bundle_intro_price", "19900 COP/month", PRICE_QUOTE, PLANES),
    claim("bundle_intro_months", "2", PRICE_QUOTE, PLANES),
    claim("bundle_includes", "disney_plus.standard_with_ads", DISNEY, PLANES),
    claim("bundle_discount", "hbo_max:30", DISCOUNTS, PLANES),
    claim("bundle_price_list_url", PRICE_LIST, LIST_LINE, PRICE_LIST),
    claim("bundle_residence_clause", "must_reside", CLAUSE, PLANES),
    # The quote is on the page, the value is not in it (the YAML's outdated 24.900).
    claim("bundle_price", "24900 COP/month", PRICE_QUOTE, PLANES),
    # On the page, but 1.000 COP a month is not a plausible bundle price.
    claim("bundle_price", "1000 COP/month", TOO_CHEAP, PLANES),
)


def meli_responder(subject: str) -> Any:
    if subject == KEY:
        return MELI_ANSWER
    return default_responder(subject)


async def test_bundle_subject_end_to_end_with_the_value_hook():
    llm = GroundedGemini(responder=meli_responder)
    deps = make_deps(model=llm, routes=MELI_ROUTES)
    # meli_plus_co_total is watch.mode manual: researched only when asked for by name, and the
    # request is explicit (no automatic Phase 2 subject).
    report = await run(deps, bundles=["meli_plus_co_total"])
    assert report.planned == [KEY]
    assert report.by_status == {"researched": 1}
    assert report.checks == {
        "official": {"implausible": 1, "value_not_in_quote": 1, "verified": 7},
    }
    facts = latest(deps.store)[KEY]
    assert facts.kind == "bundle" and facts.researched_on == TODAY
    assert sorted(c.field for c in facts.claims) == [
        "bundle_discount",
        "bundle_includes",
        "bundle_intro_months",
        "bundle_intro_price",
        "bundle_price",
        "bundle_price_list_url",
        "bundle_residence_clause",
    ]
    price = facts.claim(f"{KEY}:bundle_price:21900 COP/month")
    assert price is not None and price.check.source_class == "official"
    listed = facts.claim(f"{KEY}:bundle_price_list_url:{PRICE_LIST}")
    assert listed is not None and listed.url == PRICE_LIST  # the quote is in the PDF itself
    assert {(u.value, u.status) for u in facts.unverified} == {
        ("24900 COP/month", "value_not_in_quote"),
        ("1000 COP/month", "implausible"),
    }
    # The agent got the YAML's values to confirm or correct, and the bundle's official pages.
    (request,) = [r for r in llm.requests if subject_of(r) == KEY]
    job = json.loads(request[request.index("{") :])
    assert job["yaml_values"]["bundle_price"] == "24900 COP/month"
    assert job["known_pages"] == [PLANES]
    assert job["official_domains"] == ["mercadolibre.com.co"]
    # Fetched text, PDF text included, never goes back to the model.
    assert not any(LIST_LINE in r or "Lista de precios" in r for r in llm.requests)
    # research/latest/bundles.json, in the manifest and the report.
    store = deps.store
    manifest = json.loads(store.blobs["research/latest/run.json"])
    assert manifest["files"]["bundles.json"] == io.sha256_hex(
        store.blobs["research/latest/bundles.json"]
    )
    assert "research/latest/bundles.json" in report.outputs
    assert store.writes[-1] == "research/latest/run.json"
    assert io.load_latest(store).state.subjects[KEY].last_success == TODAY


async def test_a_run_without_bundle_subjects_writes_no_bundles_file():
    deps = make_deps(model=GroundedGemini(responder=default_responder))
    report = await run(deps)
    assert report.planned == ["pair:netflix:CO", "service:netflix"]
    assert "research/latest/bundles.json" not in deps.store.blobs
    manifest = json.loads(deps.store.blobs["research/latest/run.json"])
    assert set(manifest["files"]) == {
        "services.json",
        "pairs.json",
        "instruments.json",
        "state.json",
        "bundle_watch.json",
    }


def three_auto_bundles() -> BundlesFile:
    data = watched_bundles().model_dump(mode="json")
    (base,) = data["bundles"]
    data["bundles"] = [
        {**base, "bundle_id": f"acme_{x}", "tier": x, "aliases": [f"Acme {x}"]}
        for x in ("a", "b", "c")
    ]
    return BundlesFile.model_validate(data)


BUNDLE_HTML = f"<html><body><p>{BUNDLE_PRICE}</p><p>{BUNDLE_COMPONENT}</p></body></html>"


async def test_bundles_run_last_and_at_most_max_bundles():
    llm = GroundedGemini(responder=default_responder)
    deps = make_deps(model=llm, routes={**PAGES, BUNDLE_PAGE: BUNDLE_HTML}, research_concurrency=1)
    deps.bundles = three_auto_bundles()
    report = await run(deps)
    assert report.planned == [
        "pair:netflix:CO",
        "service:netflix",
        "bundle:acme_a",
        "bundle:acme_b",
    ]
    assert report.skipped_reasons["bundle:acme_c"] == "over the per-run bundle cap"
    # The model is asked in run order: pairs, services, instruments, then bundles.
    assert [subject_of(r) for r in llm.requests] == report.planned
    assert report.by_status == {"nothing_found": 2, "researched": 2}


async def test_a_spent_budget_skips_the_bundles_first():
    llm = GroundedGemini(responder=default_responder)
    # Every answer reports 2 search queries: the pair and the service spend all 4.
    deps = make_deps(
        model=llm,
        routes={**PAGES, BUNDLE_PAGE: BUNDLE_HTML},
        research_max_queries=4,
        research_concurrency=1,
    )
    deps.bundles = three_auto_bundles()
    report = await run(deps)
    assert report.by_status == {"researched": 2, "skipped": 2}
    assert report.skipped_reasons["bundle:acme_a"] == "max_search_queries"
    assert report.skipped_reasons["bundle:acme_b"] == "max_search_queries"
    facts = latest(deps.store)
    assert {"pair:netflix:CO", "service:netflix"} <= set(facts)
    assert not any(k.startswith("bundle:") for k in facts)


async def test_max_jobs_gives_the_bundles_what_is_left():
    llm = GroundedGemini(responder=default_responder)
    deps = make_deps(model=llm, routes={**PAGES, BUNDLE_PAGE: BUNDLE_HTML})
    deps.bundles = three_auto_bundles()
    report = await run(deps, max_jobs=3)
    assert report.planned == ["pair:netflix:CO", "service:netflix", "bundle:acme_a"]
    assert report.skipped_reasons["bundle:acme_b"] == "over --max-jobs"
    assert report.skipped_reasons["bundle:acme_c"] == "over the per-run bundle cap"


async def test_no_bundle_is_planned_with_the_guard_spent_or_without_a_model():
    store = prices_store()
    io.add_usage(store, "2026-10", calls=300, queries=1500)
    deps = make_deps(model=GroundedGemini(), store=store, routes={BUNDLE_PAGE: BUNDLE_HTML})
    deps.bundles = three_auto_bundles()
    report = await run(deps)
    assert report.planned == [] and "monthly guard" in report.skipped_reasons["*"]
    deps = make_deps(model=None, routes={**PAGES, BUNDLE_PAGE: BUNDLE_HTML})
    deps.bundles = three_auto_bundles()
    report = await run(deps)
    assert report.skipped_reasons["bundle:acme_a"] == "llm_off"
    assert not any(k.startswith("bundle:") for k in latest(deps.store))


ACME_CLAUSE = "Acme Total solo se vende a residentes en Colombia con cédula."


def clause_responder(subject: str) -> Any:
    if subject == "bundle:acme_total":
        return answer(
            claim("bundle_residence_clause", "must_reside", ACME_CLAUSE, BUNDLE_PAGE),
            claim("bundle_price", "27000 COP/month", BUNDLE_PRICE, BUNDLE_PAGE),
        )
    return default_responder(subject)


async def test_pending_findings_of_bundles_not_researched_are_rechecked():
    store = prices_store()
    page = f"<html><body><p>{BUNDLE_PRICE}</p><p>{BUNDLE_COMPONENT}</p><p>{ACME_CLAUSE}</p>"
    llm = GroundedGemini(responder=clause_responder)
    deps = make_deps(model=llm, store=store, routes={**PAGES, BUNDLE_PAGE: page})
    deps.bundles = watched_bundles()
    await run(deps, bundles=["acme_total"])
    first = latest(store)["bundle:acme_total"]
    assert sorted(c.field for c in first.claims) == ["bundle_price", "bundle_residence_clause"]
    price_id = "bundle:acme_total:bundle_price:27000 COP/month"
    price = first.claim(price_id)
    assert price is not None
    accepted = ReviewFile(
        decisions=[
            ReviewDecision(id=price.id, hash=price.hash, decision="accept", decided_on=TODAY)
        ]
    )

    # A week later, a watch-only run: the clause is gone from the page, the price is still there.
    later = date(2026, 10, 16)
    without = f"<html><body><p>{BUNDLE_PRICE}</p><p>{BUNDLE_COMPONENT}</p></body></html>"
    deps = make_deps(model=None, store=store, routes={BUNDLE_PAGE: without}, day=later)
    deps.bundles = watched_bundles()
    deps.review = accepted
    report = await run(deps, watch_only=True)
    assert report.planned == []
    facts = latest(store)["bundle:acme_total"]
    # Only the pending clause was re-checked: gone from a page that loaded, it is dropped.
    assert [c.id for c in facts.claims] == [price_id]
    assert facts.claims[0] == first.claim(price_id)  # accepted: left as it was
    assert [(u.field, u.detail) for u in facts.unverified] == [
        ("bundle_residence_clause", "no longer on the page")
    ]
    assert facts.rechecked_on == later and facts.researched_on == TODAY
    state = io.load_latest(store).state.subjects["bundle:acme_total"]
    assert state.last_success == TODAY and state.last_attempt == TODAY


async def test_a_researched_bundle_is_not_rechecked():
    store = prices_store()
    page = f"<html><body><p>{BUNDLE_PRICE}</p><p>{BUNDLE_COMPONENT}</p><p>{ACME_CLAUSE}</p>"
    llm = GroundedGemini(responder=clause_responder)
    deps = make_deps(model=llm, store=store, routes={**PAGES, BUNDLE_PAGE: page})
    deps.bundles = watched_bundles()
    await run(deps, bundles=["acme_total"])
    later = date(2026, 10, 16)
    deps = make_deps(model=llm, store=store, routes={**PAGES, BUNDLE_PAGE: page}, day=later)
    deps.bundles = watched_bundles()
    await run(deps, bundles=["acme_total"])
    facts = latest(store)["bundle:acme_total"]
    assert facts.researched_on == later and facts.rechecked_on is None


LATER = date(2026, 10, 16)
WITH_CLAUSE = f"<html><body><p>{BUNDLE_PRICE}</p><p>{BUNDLE_COMPONENT}</p><p>{ACME_CLAUSE}</p>"
WITHOUT_CLAUSE = f"<html><body><p>{BUNDLE_PRICE}</p><p>{BUNDLE_COMPONENT}</p></body></html>"
CLAUSE_ID = "bundle:acme_total:bundle_residence_clause:must_reside"


async def pending_clause(store: Any) -> Any:
    """An explicit run on acme_total that leaves a pending bundle_residence_clause finding."""
    llm = GroundedGemini(responder=clause_responder)
    deps = make_deps(model=llm, store=store, routes={**PAGES, BUNDLE_PAGE: WITH_CLAUSE})
    deps.bundles = watched_bundles()
    await run(deps, bundles=["acme_total"])
    first = latest(store)["bundle:acme_total"]
    assert first.claim(CLAUSE_ID) is not None and first.rechecked_on is None
    return first


@pytest.mark.parametrize("change", ["forbidden", "manual", "off", "expired"])
async def test_the_recheck_never_fetches_a_bundle_the_run_may_not_fetch_on_its_own(change: str):
    store = prices_store()
    first = await pending_clause(store)
    data = watched_bundles().model_dump(mode="json")
    (bundle,) = data["bundles"]
    if change == "forbidden":  # validate-catalog forces watch.mode off for such a provider
        data["providers"][0]["automated_access"] = "forbidden"
        bundle["watch"]["mode"] = "off"
    elif change == "expired":
        bundle["valid_until"] = "2026-10-12"
    else:
        bundle["watch"]["mode"] = change
    deps = make_deps(model=None, store=store, routes={BUNDLE_PAGE: WITHOUT_CLAUSE}, day=LATER)
    deps.bundles = BundlesFile.model_validate(data)
    await run(deps, watch_only=True)
    assert deps.fetcher.calls == []  # type: ignore[attr-defined]
    facts = latest(store)["bundle:acme_total"]
    assert facts.claims == first.claims and facts.rechecked_on is None


@pytest.mark.parametrize("why", ["max_search_queries", "llm_off"])
async def test_a_planned_bundle_the_run_skips_is_rechecked(why: str):
    store = prices_store()
    await pending_clause(store)
    # 35 days later the bundle is due again and planned, but the agent never gets to it.
    later = date(2026, 11, 13)
    routes = {**PAGES, BUNDLE_PAGE: WITHOUT_CLAUSE}
    if why == "max_search_queries":  # the pair and the service spend all 4 queries
        llm = GroundedGemini(responder=clause_responder)
        deps = make_deps(
            model=llm,
            store=store,
            routes=routes,
            day=later,
            research_max_queries=4,
            research_concurrency=1,
        )
    else:
        deps = make_deps(model=None, store=store, routes=routes, day=later)
    deps.bundles = watched_bundles()
    report = await run(deps)
    assert "bundle:acme_total" in report.planned
    assert report.skipped_reasons["bundle:acme_total"] == why
    facts = latest(store)["bundle:acme_total"]
    assert facts.claim(CLAUSE_ID) is None
    assert [(u.field, u.detail) for u in facts.unverified] == [
        ("bundle_residence_clause", "no longer on the page")
    ]
    assert facts.rechecked_on == later and facts.researched_on == TODAY
    state = io.load_latest(store).state.subjects["bundle:acme_total"]
    assert state.last_success == TODAY


# ------------------------------------------------- pasting the review snippet (Meli+ CO)


def meli_auto() -> BundlesFile:
    """bundles.yaml with both Meli+ CO tiers watched (watch.mode auto, as H10 will set them):
    the plans page shows Total and Esencial, so a price excerpt must name its tier."""
    data = load_bundles().model_dump(mode="json", exclude_none=True)
    for row in data["bundles"]:
        if row["bundle_id"] in ("meli_plus_co_total", "meli_plus_co_esencial"):
            row["watch"]["mode"] = "auto"
    return BundlesFile.model_validate(data)


def _paste(row: dict[str, Any], fragment: dict[str, Any]) -> dict[str, Any]:
    """A snippet merged into a bundles.yaml row the way a reviewer pastes it."""
    out = dict(row)
    for key, value in fragment.items():
        if key == "price":
            out["price"] = {**out["price"], **value}
        elif key == "watch":
            w = dict(out["watch"])
            for part in ("pages", "anchors"):
                w[part] = [*w.get(part, []), *value.get(part, [])]
            out["watch"] = w
        else:
            out[key] = value
    return out


def _pasted(bf: BundlesFile, bundle_id: str, proposals: list[Any]) -> BundlesFile:
    data = bf.model_dump(mode="json", exclude_none=True)
    (row,) = [r for r in data["bundles"] if r["bundle_id"] == bundle_id]
    b = next(x for x in bf.bundles if x.bundle_id == bundle_id)
    for p in proposals:
        s = snippet(b, p, bundles=bf, catalog=load_catalog())
        assert not [n for n in s.notes if n.startswith("validate-catalog")], s.notes
        row = _paste(row, yaml.safe_load(s.text()))
    if "intro_amount" in row["price"]:
        row["price"]["intro_months"] = 2  # its own field (bundle_intro_months)
    data["bundles"] = [row if r["bundle_id"] == bundle_id else r for r in data["bundles"]]
    return BundlesFile.model_validate(data)


def _errors(bf: BundlesFile, bundle_id: str) -> list[str]:
    return [e for e in integrity_errors(load_catalog(), bundles=bf) if bundle_id in e]


async def test_the_watchers_snippet_pasted_as_printed_passes_validate_catalog():
    store = prices_store()
    bf = meli_auto()
    deps = make_deps(model=None, store=store, routes=MELI_ROUTES)
    deps.bundles = bf
    await run(deps, watch_only=True)
    watch, _ = io.load_watch(store)
    assert watch is not None
    state = next(s for s in watch.bundles if s.bundle_id == "meli_plus_co_total")
    total = next(b for b in bf.bundles if b.bundle_id == "meli_plus_co_total")
    shown = bundle_proposals(total, watch=state, facts=None, review=None, today=TODAY)
    assert sorted((p.field, p.proposed) for p in shown) == [
        ("bundle_intro_price", "19900 COP/month"),
        ("bundle_price", "21900 COP/month"),
    ]
    # The lines name no tier ("Meli+ Total" is the heading above): the hint keeps the excerpt
    # from the heading on, which validate-catalog checks alone.
    by_field = {p.field: p for p in shown}
    assert by_field["bundle_intro_price"].anchor_text == "Meli+ Total\n$ 19.900/mes por 2 meses"
    assert by_field["bundle_price"].anchor_text == (
        f"Meli+ Total\n$ 19.900/mes por 2 meses\n{PRICE_QUOTE}"
    )
    edited = _pasted(bf, "meli_plus_co_total", shown)
    assert _errors(edited, "meli_plus_co_total") == []
    # Deployed: the next run verifies both anchors on the page, and the proposals resolve.
    later = date(2026, 10, 16)
    deps = make_deps(model=None, store=store, routes=MELI_ROUTES, day=later)
    deps.bundles = edited
    await run(deps, watch_only=True)
    watch, _ = io.load_watch(store)
    assert watch is not None
    state = next(s for s in watch.bundles if s.bundle_id == "meli_plus_co_total")
    assert {(a.fact, a.status) for a in state.anchors} == {
        ("price", "verified"),
        ("intro", "verified"),
    }
    b = next(x for x in edited.bundles if x.bundle_id == "meli_plus_co_total")
    assert bundle_proposals(b, watch=state, facts=None, review=None, today=later) == []


async def test_the_agents_snippet_pasted_as_printed_passes_validate_catalog():
    llm = GroundedGemini(responder=meli_responder)
    deps = make_deps(model=llm, routes=MELI_ROUTES)
    await run(deps, bundles=["meli_plus_co_total"])
    facts = latest(deps.store)[KEY]
    price = facts.claim(f"{KEY}:bundle_price:21900 COP/month")
    assert price is not None
    assert price.anchor_text == f"Meli+ Total\n$ 19.900/mes por 2 meses\n{PRICE_QUOTE}"
    # A quote that names its own tier needs no longer excerpt.
    disney = facts.claim(f"{KEY}:bundle_includes:disney_plus.standard_with_ads")
    assert disney is not None and disney.anchor_text is None
    bf = load_bundles()
    total = next(b for b in bf.bundles if b.bundle_id == "meli_plus_co_total")
    shown = [
        p
        for p in bundle_proposals(total, watch=None, facts=facts, review=None, today=TODAY)
        if p.field == "bundle_price"
    ]
    assert [p.anchor_text for p in shown] == [price.anchor_text]
    edited = _pasted(bf, "meli_plus_co_total", shown)
    assert _errors(edited, "meli_plus_co_total") == []

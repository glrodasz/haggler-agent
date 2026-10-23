"""Which bundle subjects a research run gives the agent (haggler.bundles.plan)."""

from __future__ import annotations

from datetime import date, timedelta
from typing import Any

import pytest

from haggler.bundles.plan import (
    BUNDLE_DAYS,
    ENDING_WINDOW_DAYS,
    ending_soon,
    plan_bundles,
    unanchored,
)
from haggler.catalog import load_bundles
from haggler.models.bundles import BundleWatchState, WatchFile
from haggler.models.curated import BundlesFile
from haggler.models.research import ResearchState, SubjectFacts, SubjectState
from haggler.research.plan import due

TODAY = date(2026, 10, 12)
URL = "https://www.acme.com.co/planes"
ANCHORED = [
    {"fact": "price", "url": URL, "text": "Acme Total cuesta $ 27.000 por mes."},
    {
        "fact": "component",
        "service_id": "disney_plus",
        "url": URL,
        "text": "Disney+ Estándar con anuncios incluido en Acme Total.",
    },
]


def row(bundle_id: str, *, mode: str = "auto", anchors: Any = None, **kw: Any) -> dict:
    return {
        "bundle_id": bundle_id,
        "provider_id": kw.pop("provider_id", "acme"),
        "provider_type": "marketplace",
        "country": "CO",
        "name": bundle_id.replace("_", " ").title(),
        "tier": bundle_id,
        "aliases": [bundle_id],
        "official_url": URL,
        "price": {"amount": "27000", "currency": "COP", **kw.pop("price", {})},
        "components": [
            {"service_id": "disney_plus", "plan_id": "standard_with_ads", "inclusion": "included"}
        ],
        "watch": {
            "mode": mode,
            "pages": [{"url": URL}],
            "anchors": ANCHORED if anchors is None else anchors,
        },
        "last_verified": "2026-10-01",
        **kw,
    }


def bundles(*rows: dict) -> BundlesFile:
    return BundlesFile.model_validate(
        {
            "providers": [
                {"id": "acme", "name": "Acme", "domains": {"CO": ["acme.com.co"]}},
                {
                    "id": "closed",
                    "name": "Closed",
                    "domains": {"CO": ["acme.com.co"]},
                    "automated_access": "forbidden",
                },
            ],
            "bundles": list(rows),
        }
    )


def researched(*pairs: tuple[str, int]) -> tuple[dict[str, SubjectFacts], ResearchState]:
    """(bundle id, days ago) -> previous facts and state."""
    facts: dict[str, SubjectFacts] = {}
    state = ResearchState()
    for bundle_id, days in pairs:
        key = f"bundle:{bundle_id}"
        on = TODAY - timedelta(days=days)
        facts[key] = SubjectFacts(
            key=key, kind="bundle", researched_on=on, run_id="r", status="nothing_found"
        )
        state.subjects[key] = SubjectState(last_attempt=on, last_success=on)
    return facts, state


def plan(data: BundlesFile, *, facts=None, state=None, watch=None, max_bundles=2, **kw):
    return plan_bundles(
        data,
        facts=facts or {},
        state=state or ResearchState(),
        watch=watch,
        today=TODAY,
        max_bundles=max_bundles,
        **kw,
    )


def watch(**outcomes: str) -> WatchFile:
    return WatchFile(
        bundles=[BundleWatchState(bundle_id=b, outcome=o) for b, o in outcomes.items()]  # type: ignore[arg-type]
    )


def keys(sel) -> list[str]:
    return [s.key for s in sel.subjects]


def test_only_auto_bundles_are_planned_automatically():
    data = bundles(
        row("auto_one"),
        row("manual_one", mode="manual"),
        row("off_one", mode="off"),
        row("expired_one", status="expired"),
        row("ended_one", valid_until="2026-10-11"),
        row("forbidden_one", provider_id="closed"),
    )
    sel = plan(data, max_bundles=10)
    assert keys(sel) == ["bundle:auto_one"]
    assert sel.subjects[0].kind == "bundle" and sel.subjects[0].bundle_id == "auto_one"
    assert sel.subjects[0].reason == "new"
    assert sel.errors == []


# The bundles in auto mode, in file order: wave 1 (milestone H10, 2026-10-11), Meli+ MX/BR/CL,
# Telia Streaming Maxad and Apple One in 26 more countries (watch probes of 2026-10-23).
REAL_AUTO = [
    "meli_plus_co_total",
    "meli_plus_co_esencial",
    "meli_plus_mx_mega",
    "meli_plus_br_mega",
    "telia_se_streaming_maxad",
    "meli_plus_cl_total",
    "meli_plus_cl_esencial",
    "apple_one_individual_se",
    "apple_one_family_se",
    "apple_one_individual_co",
    "apple_one_family_co",
    "apple_one_individual_at",
    "apple_one_family_at",
    "apple_one_individual_be",
    "apple_one_family_be",
    "apple_one_individual_br",
    "apple_one_family_br",
    "apple_one_individual_ca",
    "apple_one_family_ca",
    "apple_one_premier_ca",
    "apple_one_individual_ch",
    "apple_one_family_ch",
    "apple_one_individual_cl",
    "apple_one_family_cl",
    "apple_one_individual_cz",
    "apple_one_family_cz",
    "apple_one_individual_de",
    "apple_one_family_de",
    "apple_one_individual_dk",
    "apple_one_family_dk",
    "apple_one_individual_es",
    "apple_one_family_es",
    "apple_one_individual_fi",
    "apple_one_family_fi",
    "apple_one_individual_fr",
    "apple_one_family_fr",
    "apple_one_individual_gb",
    "apple_one_family_gb",
    "apple_one_premier_gb",
    "apple_one_individual_hu",
    "apple_one_family_hu",
    "apple_one_individual_id",
    "apple_one_family_id",
    "apple_one_individual_ie",
    "apple_one_family_ie",
    "apple_one_premier_ie",
    "apple_one_individual_it",
    "apple_one_family_it",
    "apple_one_individual_lu",
    "apple_one_family_lu",
    "apple_one_individual_mx",
    "apple_one_family_mx",
    "apple_one_individual_nl",
    "apple_one_family_nl",
    "apple_one_individual_no",
    "apple_one_family_no",
    "apple_one_individual_ph",
    "apple_one_family_ph",
    "apple_one_individual_pl",
    "apple_one_individual_pt",
    "apple_one_family_pt",
    "apple_one_premier_pt",
    "apple_one_individual_us",
    "apple_one_family_us",
    "apple_one_premier_us",
    "apple_one_individual_vn",
    "apple_one_family_vn",
    "google_ai_pro_co",
]


def test_the_real_auto_bundles_are_the_ones_the_watch_probe_verified():
    # Never researched: every auto bundle is new; the cap keeps a run to 2 bundle subjects
    # (HAGGLER_RESEARCH_MAX_BUNDLES), after every Phase 2 subject (KIND_ORDER puts bundle last).
    # The observed ones (Apple One) come after the rest.
    sel = plan(load_bundles(), max_bundles=len(REAL_AUTO))
    observed = [i for i in REAL_AUTO if i.startswith("apple_one_")]
    expected = [i for i in REAL_AUTO if i not in observed] + observed
    assert keys(sel) == [f"bundle:{i}" for i in expected]
    assert {s.reason for s in sel.subjects} == {"new"}
    assert keys(plan(load_bundles(), max_bundles=2)) == [f"bundle:{i}" for i in expected[:2]]


def test_priority_order_and_cap():
    no_anchor = row("unanchored", anchors=[])
    validity = {"fact": "validity", "url": URL, "text": "Oferta válida hasta el 22/10/2026."}
    ending = row(
        "ending",
        valid_until=(TODAY + timedelta(days=10)).isoformat(),
        anchors=[*ANCHORED, validity],
    )
    data = bundles(
        row("stale_old"),
        row("stale_recent"),
        ending,
        no_anchor,
        row("never"),
        row("changed"),
        row(
            "never_observed",
            price={
                "amount": None,
                "source": "observed",
                "observed_service_id": "apple_one",
                "observed_plan_id": "individual",
            },
        ),
    )
    facts, state = researched(
        ("stale_old", 90),
        ("stale_recent", 40),
        ("ending", 20),
        ("unanchored", 40),
        ("changed", 30),
    )
    sel = plan(data, facts=facts, state=state, watch=watch(changed="changed"), max_bundles=10)
    assert keys(sel) == [
        "bundle:changed",
        "bundle:never",
        "bundle:unanchored",
        "bundle:ending",
        "bundle:never_observed",
        "bundle:stale_old",
        "bundle:stale_recent",
    ]
    reasons = {s.bundle_id: s.reason for s in sel.subjects}
    assert reasons["changed"] == "stale; watch: changed"
    assert reasons["unanchored"].startswith("stale; values without an anchor: price, disney_plus")
    assert reasons["ending"] == "stale; ends soon"
    # A new country of an observed product (Apple One) waits behind the bundles above.
    assert reasons["never_observed"] == "new"
    capped = plan(data, facts=facts, state=state, watch=watch(changed="changed"), max_bundles=2)
    assert keys(capped) == ["bundle:changed", "bundle:never"]
    assert capped.skipped["bundle:unanchored"] == "over the per-run bundle cap"
    assert capped.skipped["bundle:stale_recent"] == "over the per-run bundle cap"


@pytest.mark.parametrize(
    ("days_ago", "planned"),
    [(BUNDLE_DAYS - 1, False), (BUNDLE_DAYS, True)],
)
def test_freshness_window(days_ago: int, planned: bool):
    facts, state = researched(("b", days_ago))
    sel = plan(bundles(row("b")), facts=facts, state=state)
    assert (keys(sel) == ["bundle:b"]) is planned
    if not planned:
        assert sel.skipped == {"bundle:b": "fresh or paused"}


def test_an_offer_ending_soon_shortens_the_window():
    ends = {"valid_until": (TODAY + timedelta(days=14)).isoformat()}
    intro = {"price": {"intro_amount": "19900", "intro_valid_until": TODAY.isoformat()}}
    for kw in (ends, intro):
        b = row("b", **kw)
        assert ending_soon(bundles(b).bundles[0], TODAY)
        facts, state = researched(("b", ENDING_WINDOW_DAYS))
        assert keys(plan(bundles(b), facts=facts, state=state)) == ["bundle:b"]
        facts, state = researched(("b", ENDING_WINDOW_DAYS - 1))
        assert keys(plan(bundles(b), facts=facts, state=state)) == []
    later = row("b", valid_until=(TODAY + timedelta(days=15)).isoformat())
    assert not ending_soon(bundles(later).bundles[0], TODAY)


@pytest.mark.parametrize("outcome", ["changed", "new_document"])
def test_a_change_the_watcher_saw_ranks_first_but_keeps_the_window(outcome: str):
    facts, state = researched(("b", BUNDLE_DAYS))
    sel = plan(bundles(row("b")), facts=facts, state=state, watch=watch(b=outcome))
    assert keys(sel) == ["bundle:b"] and sel.subjects[0].reason == f"stale; watch: {outcome}"
    # The outcome stays changed until a person edits the YAML: it never makes a bundle due.
    facts, state = researched(("b", 7))
    assert keys(plan(bundles(row("b")), facts=facts, state=state, watch=watch(b=outcome))) == []


def test_persistently_changed_bundles_do_not_starve_a_never_researched_one():
    data = bundles(row("acme_a"), row("acme_b"), row("acme_c"))
    changed = watch(acme_a="changed", acme_b="new_document")
    # Run 1: the two changed bundles are due and take the cap.
    facts, state = researched(("acme_a", BUNDLE_DAYS), ("acme_b", BUNDLE_DAYS))
    first = plan(data, facts=facts, state=state, watch=changed)
    assert keys(first) == ["bundle:acme_a", "bundle:acme_b"]
    assert first.skipped["bundle:acme_c"] == "over the per-run bundle cap"
    # The weeks after: still changed, but researched within the window; acme_c gets its turn.
    for days in (7, 14, 21):
        facts, state = researched(("acme_a", days), ("acme_b", days))
        assert keys(plan(data, facts=facts, state=state, watch=changed)) == ["bundle:acme_c"]


def test_a_paused_subject_waits_even_when_changed():
    facts, state = researched(("b", 60))
    state.subjects["bundle:b"].paused_until = TODAY + timedelta(days=3)
    sel = plan(bundles(row("b")), facts=facts, state=state, watch=watch(b="changed"))
    assert keys(sel) == [] and sel.skipped == {"bundle:b": "fresh or paused"}


def test_unanchored_values():
    b = bundles(
        row(
            "b",
            anchors=[ANCHORED[0]],
            valid_until="2026-12-31",
            price={"intro_amount": "19900", "base_plan_amount": "10000", "base_plan_name": "X"},
        )
    ).bundles[0]
    assert unanchored(b) == ["base_price", "intro", "disney_plus", "validity"]
    assert unanchored(bundles(row("c")).bundles[0]) == []


def test_explicit_request():
    data = bundles(
        row("auto_one"),
        row("manual_one", mode="manual"),
        row("off_one", mode="off"),
        row("expired_one", status="expired"),
        row("forbidden_one", provider_id="closed"),
    )
    facts, state = researched(("auto_one", 1), ("manual_one", 1))
    state.subjects["bundle:auto_one"].paused_until = TODAY + timedelta(days=9)
    requested = ["manual_one", "auto_one", "off_one", "expired_one", "forbidden_one", "nope"]
    sel = plan(data, facts=facts, state=state, max_bundles=1, requested=requested, explicit=True)
    # Freshness, pauses and the cap do not apply; a manual bundle may be asked for.
    assert keys(sel) == ["bundle:manual_one", "bundle:auto_one"]
    assert {s.reason for s in sel.subjects} == {"requested"}
    assert sel.errors == [
        "request: bundle off_one is not researched (watch.mode off)",
        "request: bundle expired_one is not researched (the offer has expired)",
        "request: bundle forbidden_one is not researched "
        "(the provider's terms forbid automated access)",
        "request: unknown bundle nope",
    ]


def test_an_explicit_request_without_bundles_plans_none():
    assert keys(plan(bundles(row("auto_one")), explicit=True)) == []


def test_due_takes_a_window():
    facts, state = researched(("b", 10))
    assert due("bundle:b", facts, state, TODAY) is None
    assert due("bundle:b", facts, state, TODAY, window=10) == "stale"
    # The Phase 2 kinds keep their own windows.
    assert due("service:netflix", {}, ResearchState(), TODAY) == "new"

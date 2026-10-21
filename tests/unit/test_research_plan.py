"""Selection policy of research runs (haggler.research.plan)."""

from __future__ import annotations

from datetime import date, timedelta

from haggler.catalog import load_catalog, load_feasibility, load_payment_instruments, load_profiles
from haggler.models.research import ResearchState, SubjectFacts, SubjectState
from haggler.research.plan import Limits, Request, due, parse_pairs, select

TODAY = date(2026, 10, 9)
CAT = load_catalog()
FEAS = load_feasibility()
INST = load_payment_instruments()
PROFILES = load_profiles()


def facts(key: str, on: date) -> SubjectFacts:
    return SubjectFacts(
        key=key,
        kind=key.split(":")[0],  # type: ignore[arg-type]
        researched_on=on,
        run_id="r",
        status="researched",
    )


def sel(**kw):
    base = dict(
        catalog=CAT,
        feasibility=FEAS,
        instruments=INST,
        profiles=PROFILES,
        prices=[],
        fx=None,
        facts={},
        state=ResearchState(),
        today=TODAY,
    )
    return select(**(base | kw))


def test_due_windows_and_pauses():
    key = "service:netflix"
    assert due(key, {}, ResearchState(), TODAY) == "new"
    fresh = {key: facts(key, TODAY - timedelta(days=89))}
    assert due(key, fresh, ResearchState(), TODAY) is None
    stale = {key: facts(key, TODAY - timedelta(days=90))}
    assert due(key, stale, ResearchState(), TODAY) == "stale"
    paused = ResearchState(subjects={key: SubjectState(paused_until=TODAY + timedelta(days=1))})
    assert due(key, {}, paused, TODAY) is None
    # last_success wins over the facts' date.
    st = ResearchState(subjects={key: SubjectState(last_success=TODAY)})
    assert due(key, stale, st, TODAY) is None
    assert due("pair:netflix:CO", {}, ResearchState(), TODAY) == "new"


def test_services_without_pairs_follow_catalog_order_and_caps():
    s = sel(limits=Limits(max_services=2, max_instruments=1))
    assert [x.key for x in s.subjects] == [
        "service:netflix",
        "service:youtube_premium",
        "instrument:bancolombia_exterior",
    ]
    assert s.skipped["service:disney_plus"] == "over the per-run service cap"


def test_unpriced_services_and_held_instruments_are_not_planned():
    s = sel(limits=Limits(max_services=50, max_instruments=50))
    keys = {x.key for x in s.subjects}
    assert "service:vix" not in keys  # no price sources
    assert "instrument:user_co_card" not in keys  # no official domains, held by the user
    assert "instrument:revolut_eea" in keys


def test_monthly_guard():
    s = sel(month_queries_used=1500, month="2026-10")
    assert s.subjects == [] and "monthly guard: 1500 search queries in 2026-10" in s.skipped["*"]
    assert sel(month_queries_used=1499).subjects


def test_explicit_request():
    req = Request(
        services=("netflix", "nope"),
        pairs=parse_pairs(["spotify:co"]),
        instruments=("revolut_eea",),
    )
    fresh = {"service:netflix": facts("service:netflix", TODAY)}
    s = sel(request=req, facts=fresh)
    assert [x.key for x in s.subjects] == [
        "pair:spotify:CO",
        "service:netflix",
        "instrument:revolut_eea",
    ]
    assert s.errors == ["request: unknown service nope"]


def test_parse_pairs_rejects_bad_values():
    import pytest

    with pytest.raises(ValueError):
        parse_pairs(["netflix"])
    assert parse_pairs([" netflix:co "]) == (("netflix", "CO"),)

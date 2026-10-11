"""A bundle's derived display state (haggler.bundles.status): from bundles.yaml alone (H2), then
with the watcher's evidence (bundle_watch.json, H7)."""

from __future__ import annotations

from datetime import date, timedelta
from typing import Any

import pytest

from haggler.bundles.status import (
    EXPIRED_KEEP_DAYS,
    MANUAL_MAX_AGE_DAYS,
    anchor_confirmed,
    bundle_state,
    expired_on,
    is_expired,
)
from haggler.catalog import load_bundles
from haggler.models.bundles import AnchorCheck, BundleWatchState, PageCheck, anchor_hash
from haggler.models.curated import Bundle

TODAY = date(2026, 10, 10)


def bundle(**overrides: Any) -> Bundle:
    data: dict[str, Any] = {
        "bundle_id": "b",
        "provider_id": "acme",
        "provider_type": "marketplace",
        "country": "CO",
        "name": "Acme Total",
        "tier": "total",
        "official_url": "https://www.acme.com.co/planes",
        "price": {"amount": "27000", "currency": "COP"},
        "components": [{"service_id": "netflix", "inclusion": "included"}],
        "last_verified": "2026-10-01",
    }
    data.update(overrides)
    return Bundle.model_validate(data)


def test_unverified_search_summary_says_why() -> None:
    s = bundle_state(bundle(), today=TODAY)
    assert s.display_status == "unverified"
    assert s.confidence == "low"
    assert s.reasons_es == (
        "Precio y componentes tomados de un resumen de búsqueda; sin verificar en la página "
        "oficial.",
        "No se vigila: sus valores no se comprueban de forma automática.",
    )
    assert (s.verified_at, s.checked_at, s.published) == (None, None, True)


def test_unverified_reasons_by_source_and_watch_mode() -> None:
    observed = bundle(
        price={
            "currency": "COP",
            "source": "observed",
            "observed_service_id": "apple_one",
            "observed_plan_id": "individual",
        },
        watch={"mode": "manual"},
    )
    s = bundle_state(observed, today=TODAY, price_missing=True)
    assert s.reasons_es == (
        "Componentes tomados de un resumen de búsqueda; sin verificar en la página oficial.",
        "Aún no hay un precio publicado de este plan en los datos diarios.",
        "Se revisa a mano: sus páginas no están en la vigilancia automática.",
    )
    agent = bundle_state(bundle(verified_by="agent_research"), today=TODAY)
    assert agent.reasons_es[0].startswith("Precio y componentes propuestos por la investigación")
    fetched = bundle_state(
        bundle(verified_by="official_fetch", watch={"mode": "auto"}), today=TODAY
    )
    assert fetched.display_status == "unverified"
    assert fetched.reasons_es[-1] == "El vigilante aún no ha leído sus páginas oficiales."


@pytest.mark.parametrize("age", [0, 1, MANUAL_MAX_AGE_DAYS])
def test_manual_verification_lasts_45_days(age: int) -> None:
    b = bundle(
        verified_by="user_report",
        user_report={"date": (TODAY - timedelta(days=age)).isoformat(), "note": "visto"},
        confidence="high",
    )
    s = bundle_state(b, today=TODAY)
    assert s.display_status == "manual"
    assert s.confidence == "medium"  # a manual check is never more than medium
    assert s.verified_at == TODAY - timedelta(days=age)
    # The card's badge shows the age (counted to the same day); the reason does not repeat it.
    assert s.reasons_es == ("Verificación manual: no cuenta como fuente oficial.",)


def test_manual_verification_expires() -> None:
    day = TODAY - timedelta(days=MANUAL_MAX_AGE_DAYS + 1)
    b = bundle(verified_by="user_report", user_report={"date": day.isoformat()})
    s = bundle_state(b, today=TODAY)
    assert s.display_status == "unverified" and s.confidence == "low"
    assert day == date(2026, 8, 25)
    assert (
        s.reasons_es[0] == "La verificación manual del 25 de agosto de 2026 tiene más de 45 días."
    )


def test_manual_confidence_keeps_a_lower_value() -> None:
    b = bundle(verified_by="user_report", user_report={"date": TODAY.isoformat()})
    assert bundle_state(b, today=TODAY).confidence == "low"


def test_expired_by_status_or_end_date() -> None:
    by_status = bundle(status="expired")
    assert is_expired(by_status, TODAY)
    s = bundle_state(by_status, today=TODAY)
    assert s.display_status == "expired" and s.confidence == "low"
    assert s.reasons_es == ("Marcada como vencida en los datos curados.",)

    ended = bundle(valid_until="2026-10-09")
    assert is_expired(ended, TODAY)
    # The badge shows the end date; the reason does not repeat it.
    assert bundle_state(ended, today=TODAY).reasons_es == ("La oferta pasó su fecha de fin.",)
    # The last day of the offer is still valid.
    assert not is_expired(bundle(valid_until=TODAY.isoformat()), TODAY)


def test_expired_bundles_are_published_for_90_days() -> None:
    ended = bundle(valid_until="2026-07-01")
    last = date(2026, 7, 1) + timedelta(days=EXPIRED_KEEP_DAYS)
    assert bundle_state(ended, today=last).published
    assert not bundle_state(ended, today=last + timedelta(days=1)).published
    # Without an end date the window counts from the last human review.
    undated = bundle(status="expired", last_verified="2026-06-01")
    assert not bundle_state(undated, today=TODAY).published
    # Marked expired before its announced end: it ended by the day it was checked, so a far
    # valid_until does not keep the card for months.
    early = bundle(status="expired", last_verified="2026-06-01", valid_until="2027-03-31")
    assert expired_on(early) == date(2026, 6, 1)
    assert bundle_state(early, today=date(2026, 8, 30)).published
    assert not bundle_state(early, today=date(2026, 8, 31)).published
    # Checked after the end date: the end date counts.
    late = bundle(status="expired", last_verified="2026-08-10", valid_until="2026-07-01")
    assert expired_on(late) == date(2026, 7, 1)


def test_expired_wins_over_a_manual_verification() -> None:
    b = bundle(
        verified_by="user_report",
        user_report={"date": TODAY.isoformat()},
        valid_until="2026-10-01",
    )
    assert bundle_state(b, today=TODAY).display_status == "expired"


def test_real_bundles_are_unverified_with_reasons() -> None:
    for b in load_bundles().bundles:
        s = bundle_state(b, today=TODAY)
        assert s.display_status == "unverified", b.bundle_id
        assert s.reasons_es and s.confidence == "low"


# ------------------------------------------------------------------ watcher data (H7)

URL = "https://www.acme.com.co/planes"
PRICE_TEXT = "Acme Total cuesta $ 27.000 por mes con Netflix incluido."
NETFLIX_TEXT = "Netflix Estándar incluido en Acme Total."


def watched_bundle(**overrides: Any) -> Bundle:
    data: dict[str, Any] = {
        "watch": {
            "mode": "auto",
            "pages": [{"url": URL}],
            "anchors": [
                {"fact": "price", "url": URL, "text": PRICE_TEXT},
                {"fact": "component", "service_id": "netflix", "url": URL, "text": NETFLIX_TEXT},
            ],
        },
        "official_url": URL,
    }
    data.update(overrides)
    return bundle(**data)


def watch_state(
    b: Bundle,
    *,
    statuses: dict[str, str] | None = None,
    verified_on: date | None = TODAY,
    outcome: str = "ok",
    page: dict[str, Any] | None = None,
) -> BundleWatchState:
    statuses = statuses or {}
    anchors = []
    for a in b.watch.anchors:
        status = statuses.get(a.fact, "verified")
        anchors.append(
            AnchorCheck(
                anchor_hash=anchor_hash(a),
                fact=a.fact,
                service_id=a.service_id,
                url=str(a.url),
                status=status,
                via="lines" if status == "verified" else None,
                last_verified_at=verified_on,
                checked_at=TODAY,
            )
        )
    pages = [PageCheck(url=URL, status="ok", checked_at=TODAY, last_ok_at=TODAY, **(page or {}))]
    return BundleWatchState(
        bundle_id=b.bundle_id, outcome=outcome, pages=pages, anchors=anchors, checked_at=TODAY
    )


def test_every_decisive_anchor_verified_is_verified() -> None:
    b = watched_bundle()
    s = bundle_state(b, today=TODAY, watch=watch_state(b, verified_on=TODAY - timedelta(days=3)))
    assert (s.display_status, s.reasons_es, s.confidence) == ("verified", (), "high")
    assert s.verified_at == TODAY - timedelta(days=3) and s.checked_at == TODAY
    assert s.confirmed_components == {"netflix"} and not s.intro_confirmed


def test_verification_lasts_15_days_then_says_the_last_confirmed_value() -> None:
    b = watched_bundle()
    w = watch_state(b, verified_on=date(2026, 9, 25))
    assert bundle_state(b, today=date(2026, 10, 10), watch=w).display_status == "verified"
    s = bundle_state(b, today=date(2026, 10, 11), watch=w)
    assert s.display_status == "unverified" and s.checked_at == TODAY
    assert (
        "Último valor confirmado 27.000 COP el 25 de septiembre de 2026; ya no se confirma."
        in s.reasons_es
    )
    assert s.confirmed_components == frozenset()


def test_a_missing_decisive_anchor_needs_review() -> None:
    b = watched_bundle()
    w = watch_state(b, statuses={"component": "missing"}, outcome="changed")
    s = bundle_state(b, today=TODAY, watch=w)
    assert s.display_status == "needs_review" and s.confidence == "low"
    assert s.reasons_es == ("El componente netflix ya no aparece en acme.com.co.",)
    assert s.verified_at is None and s.checked_at == TODAY


@pytest.mark.parametrize("kind", ["missing", "embedded_only", "outdated_document"])
def test_an_unconfirmed_anchor_confirms_nothing_until_a_read_verifies_it(kind: str) -> None:
    """An unreadable page within the 15 days keeps an earlier verification, unless a read since
    then undid it (AnchorCheck.unconfirmed, carried across the runs that cannot read it)."""
    b = watched_bundle()
    w = watch_state(b, statuses={"price": "unreadable"}, verified_on=TODAY - timedelta(days=7))
    assert bundle_state(b, today=TODAY, watch=w).display_status == "verified"
    undone = w.anchors[0].model_copy(
        update={"unconfirmed": kind, "unconfirmed_on": TODAY - timedelta(days=3)}
    )
    assert not anchor_confirmed(undone, TODAY)
    w.anchors[0] = undone
    s = bundle_state(b, today=TODAY, watch=w)
    assert s.display_status == ("needs_review" if kind == "missing" else "unverified")
    assert s.confidence == "low" and s.verified_at is None
    assert (
        "Último valor confirmado 27.000 COP el 3 de octubre de 2026; ya no se confirma."
        in s.reasons_es
    )


def test_a_curated_price_needs_a_price_anchor_and_every_included_component_one() -> None:
    b = watched_bundle(
        components=[
            {"service_id": "netflix", "inclusion": "included"},
            {"service_id": "spotify", "inclusion": "included"},
        ],
        watch={
            "mode": "auto",
            "pages": [{"url": URL}],
            "anchors": [
                {"fact": "component", "service_id": "netflix", "url": URL, "text": NETFLIX_TEXT}
            ],
        },
    )
    s = bundle_state(b, today=TODAY, watch=watch_state(b))
    assert s.display_status == "unverified"
    assert "El precio no tiene un ancla que el vigilante pueda buscar." in s.reasons_es
    assert "Falta el ancla de 1 componente incluido." in s.reasons_es
    assert s.confirmed_components == {"netflix"}


def test_an_observed_price_stands_in_for_the_price_anchor() -> None:
    b = watched_bundle(
        price={
            "currency": "COP",
            "source": "observed",
            "observed_service_id": "apple_one",
            "observed_plan_id": "individual",
        },
        watch={
            "mode": "auto",
            "pages": [{"url": URL}],
            "anchors": [
                {"fact": "component", "service_id": "netflix", "url": URL, "text": NETFLIX_TEXT}
            ],
        },
    )
    w = watch_state(b, verified_on=TODAY - timedelta(days=2))
    s = bundle_state(b, today=TODAY, watch=w, price_observed_on=TODAY)
    assert s.display_status == "verified" and s.verified_at == TODAY - timedelta(days=2)
    missing = bundle_state(b, today=TODAY, watch=w, price_missing=True)
    assert missing.display_status == "unverified"
    assert "Aún no hay un precio publicado de este plan en los datos diarios." in missing.reasons_es
    old = bundle_state(b, today=TODAY, watch=w, price_observed_on=TODAY - timedelta(days=20))
    assert "El precio observado no es de los datos diarios recientes." in old.reasons_es


def test_watcher_data_only_counts_in_auto_mode() -> None:
    b = watched_bundle()
    manual = watched_bundle(watch={**b.watch.model_dump(mode="json"), "mode": "manual"})
    s = bundle_state(manual, today=TODAY, watch=watch_state(manual))
    assert s.display_status == "unverified" and s.checked_at is None
    assert s.reasons_es[-1] == "Se revisa a mano: sus páginas no están en la vigilancia automática."
    off = watch_state(b, outcome="off")
    assert bundle_state(b, today=TODAY, watch=off).display_status == "unverified"


def test_a_manual_verification_also_says_what_the_watcher_found() -> None:
    b = watched_bundle(verified_by="user_report", user_report={"date": TODAY.isoformat()})
    w = watch_state(b, verified_on=None, statuses={"price": "unreadable"})
    w.pages[0] = w.pages[0].model_copy(update={"status": "robots_disallowed"})
    s = bundle_state(b, today=TODAY, watch=w)
    assert s.display_status == "manual"
    assert s.reasons_es == (
        "Verificación manual: no cuenta como fuente oficial.",
        "robots.txt no permite leer acme.com.co.",
    )


def test_the_intro_counts_only_with_a_confirmed_intro_anchor() -> None:
    intro_text = "Acme Total cuesta $ 19.000 por mes durante los primeros 2 meses."
    b = watched_bundle(
        price={"amount": "27000", "currency": "COP", "intro_amount": "19000", "intro_months": 2},
        watch={
            "mode": "auto",
            "pages": [{"url": URL}],
            "anchors": [
                {"fact": "price", "url": URL, "text": PRICE_TEXT},
                {"fact": "intro", "url": URL, "text": intro_text},
                {"fact": "component", "service_id": "netflix", "url": URL, "text": NETFLIX_TEXT},
            ],
        },
    )
    s = bundle_state(b, today=TODAY, watch=watch_state(b))
    assert s.display_status == "verified" and s.intro_confirmed
    s = bundle_state(b, today=TODAY, watch=watch_state(b, statuses={"intro": "missing"}))
    # The intro is not decisive: still verified, without the first-year figure.
    assert s.display_status == "verified" and not s.intro_confirmed


def test_a_page_the_yaml_no_longer_watches_has_no_say() -> None:
    b = watched_bundle()
    w = watch_state(b)
    old = PageCheck(
        url="https://www.acme.com.co/lista-18.pdf",
        kind="pdf",
        status="superseded",
        edition="18",
        newest_edition="19",
        checked_at=TODAY,
    )
    w.pages.append(old)
    # The YAML moved to the new list (the next run checks it): no stale "new edition" review.
    assert bundle_state(b, today=TODAY, watch=w).display_status == "verified"

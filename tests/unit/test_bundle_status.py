"""A bundle's derived display state (haggler.bundles.status), from bundles.yaml alone (H2)."""

from __future__ import annotations

from datetime import date, timedelta
from typing import Any

import pytest

from haggler.bundles.status import (
    EXPIRED_KEEP_DAYS,
    MANUAL_MAX_AGE_DAYS,
    bundle_state,
    expired_on,
    is_expired,
)
from haggler.catalog import load_bundles
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

"""Proposed bundle values (haggler.bundles.proposals, phase 3 H8): from the watcher's hints and
the agent's value findings; they resolve themselves once bundles.yaml holds the value, a
rejection hides them, only official value-checked ones are published, and each one comes with
the bundles.yaml snippet that accepts it."""

from __future__ import annotations

from datetime import UTC, date, datetime
from decimal import Decimal
from typing import Any

import pytest
import yaml

from haggler.bundles.proposals import (
    Proposal,
    bundle_proposals,
    current_value,
    pending_changes,
    replaced_anchors,
    resolved,
    snippet,
)
from haggler.bundles.status import bundle_state
from haggler.catalog import integrity_errors, load_catalog
from haggler.models.bundles import BundleWatchState, PriceHint
from haggler.models.curated import Bundle, BundlesFile
from haggler.models.research import (
    Claim,
    ReviewDecision,
    ReviewFile,
    SourceCheck,
    SubjectFacts,
    claim_hash,
    claim_id,
)
from haggler.research.review import ReviewIndex

TODAY = date(2026, 10, 10)
URL = "https://www.acme.com.co/planes"
KEY = "bundle:acme_total"
# The shape of the Meli+ CO plans page (H0 probe): an intro price and the regular one.
PRICE_QUOTE = (
    "Precio actual con 9 por ciento de descuento: 19900 pesos por mes, por 2 meses. "
    "Precio anterior: $ 21.900 pesos por mes."
)
# Excerpts that name the tier themselves, as an anchor's text must (validate-catalog checks it
# alone, without the page's heading).
TIER_PRICE = "Acme Total cuesta $ 21.900 por mes."
TIER_INTRO = "Acme Total: $ 19.900/mes por 2 meses."
PROVIDER = {"id": "acme", "name": "Acme", "domains": {"CO": ["acme.com.co"]}}


def bundle(**overrides: Any) -> Bundle:
    data: dict[str, Any] = {
        "bundle_id": "acme_total",
        "provider_id": "acme",
        "provider_type": "marketplace",
        "country": "CO",
        "name": "Acme Total",
        "tier": "total",
        "aliases": ["Acme Total"],
        "official_url": URL,
        "price": {"amount": "24900", "currency": "COP", "tax_included": True},
        "components": [
            {"service_id": "netflix", "plan_id": "standard_with_ads", "inclusion": "included"},
            {"service_id": "hbo_max", "inclusion": "discount_pct", "value": "30"},
        ],
        "watch": {"mode": "auto", "pages": [{"url": URL}]},
        "last_verified": "2026-10-01",
    }
    data.update(overrides)
    return Bundle.model_validate(data)


def hint(amount: str, role: str = "regular", **kw: Any) -> PriceHint:
    return PriceHint(
        url=URL,
        amount=Decimal(amount),
        currency="COP",
        period="month",
        role=role,  # type: ignore[arg-type]
        line=kw.pop("line", PRICE_QUOTE),
        **kw,
    )


def watch(*hints: PriceHint, outcome: str = "ok") -> BundleWatchState:
    return BundleWatchState(
        bundle_id="acme_total",
        outcome=outcome,  # type: ignore[arg-type]
        hints=list(hints),
        checked_at=date(2026, 10, 5),
    )


def claim(field: str, value: str, quote: str = PRICE_QUOTE, **kw: Any) -> Claim:
    url = kw.pop("url", URL)
    return Claim(
        id=claim_id(KEY, field, value),
        hash=claim_hash(field, value, None, quote, url),
        field=field,
        value=value,
        quote=quote,
        check=SourceCheck(
            cited_url=url,
            final_url=url,
            source_class=kw.pop("source_class", "official"),
            status="verified",
            retrieved_at=datetime(2026, 10, 6, 5, 30, tzinfo=UTC),
        ),
        first_seen=date(2026, 9, 28),
        **kw,
    )


def facts(*claims: Claim) -> SubjectFacts:
    return SubjectFacts(
        key=KEY,
        kind="bundle",
        researched_on=date(2026, 10, 6),
        run_id="r1",
        status="researched",
        claims=list(claims),
    )


def proposals(b: Bundle | None = None, **kw: Any) -> list[Proposal]:
    kw.setdefault("watch", None)
    kw.setdefault("facts", None)
    kw.setdefault("review", None)
    return bundle_proposals(b or bundle(), today=TODAY, **kw)


# ------------------------------------------------------------------ watcher hints


def test_a_hint_is_a_watcher_proposal() -> None:
    (p,) = proposals(watch=watch(hint("21900", first_seen=date(2026, 9, 28))))
    assert (p.source, p.field, p.current, p.proposed) == (
        "watcher",
        "bundle_price",
        "24900 COP/month",
        "21900 COP/month",
    )
    assert p.id == "watch:acme_total:bundle_price:21900 COP/month"
    assert p.hash == claim_hash("bundle_price", "21900 COP/month", None, PRICE_QUOTE, URL)
    assert (p.url, p.quote, p.value_check, p.official) == (URL, PRICE_QUOTE, "ok", True)
    assert (p.first_seen, p.retrieved_at) == (date(2026, 9, 28), date(2026, 10, 5))
    assert p.publishable


def test_an_intro_hint_proposes_the_intro_price() -> None:
    (p,) = proposals(watch=watch(hint("19900", role="intro")))
    assert (p.field, p.current, p.proposed) == ("bundle_intro_price", None, "19900 COP/month")
    # A hint written before H8 has no first_seen: the run that found it stands in.
    assert p.first_seen == date(2026, 10, 5)


@pytest.mark.parametrize(
    ("b", "w"),
    [
        (bundle(watch={"mode": "manual", "pages": [{"url": URL}]}), watch(hint("21900"))),
        (bundle(), watch(hint("21900"), outcome="off")),
    ],
)
def test_hints_count_only_for_an_auto_watched_bundle(b: Bundle, w: BundleWatchState) -> None:
    assert proposals(b, watch=w) == []


def test_a_hint_resolves_itself_once_the_yaml_holds_its_value() -> None:
    w = watch(hint("21900"), hint("19900", role="intro"))
    assert [p.field for p in proposals(watch=w)] == ["bundle_price", "bundle_intro_price"]
    edited = bundle(
        price={
            "amount": "21900",
            "currency": "COP",
            "tax_included": True,
            "intro_amount": "19900",
            "intro_months": 2,
        }
    )
    assert proposals(edited, watch=w) == []


def test_a_price_equal_to_the_base_or_marginal_amount_is_not_a_new_price() -> None:
    telco = bundle(
        price={
            "amount": "699",
            "currency": "COP",
            "tax_included": True,
            "base_plan_name": "Bas",
            "base_plan_amount": "569",
            "marginal_amount": "130",
        }
    )
    assert resolved(telco, "bundle_price", "569 COP/month", TODAY)
    assert resolved(telco, "bundle_price", "130 COP/month", TODAY)
    assert not resolved(telco, "bundle_price", "749 COP/month", TODAY)
    # Another period is another value.
    assert not resolved(telco, "bundle_price", "699 COP/year", TODAY)


def test_an_observed_price_gets_no_price_proposal() -> None:
    observed = bundle(
        price={
            "currency": "COP",
            "tax_included": True,
            "source": "observed",
            "observed_service_id": "netflix",
            "observed_plan_id": "standard_with_ads",
        }
    )
    w = watch(hint("21900"), hint("19900", role="intro"))
    f = facts(claim("bundle_price", "21900 COP/month"))
    assert [p.field for p in proposals(observed, watch=w, facts=f)] == ["bundle_intro_price"]


def test_a_rejected_hint_is_hidden_until_new_evidence() -> None:
    w = watch(hint("21900"))
    (p,) = proposals(watch=w)
    review = ReviewIndex(
        ReviewFile(
            decisions=[ReviewDecision(id=p.id, hash=p.hash, decision="reject", decided_on=TODAY)]
        )
    )
    assert proposals(watch=w, review=review) == []
    assert [x.id for x in proposals(watch=w, review=review, include_rejected=True)] == [p.id]
    # Another line is new evidence: a new hash, listed again.
    other = watch(hint("21900", line="Acme Total: $ 21.900 por mes."))
    assert [x.id for x in proposals(watch=other, review=review)] == [p.id]


# ------------------------------------------------------------------ agent findings


def test_value_findings_are_agent_proposals_and_feasibility_ones_are_not() -> None:
    price = claim("bundle_price", "21900 COP/month", big_change=False)
    clause = claim("bundle_residence_clause", "must_reside", quote="Solo para residentes.")
    (p,) = proposals(facts=facts(clause, price))
    assert (p.source, p.id, p.hash, p.review) == ("agent", price.id, price.hash, "pending")
    assert (p.current, p.proposed, p.value_check) == ("24900 COP/month", "21900 COP/month", "ok")
    assert (p.first_seen, p.retrieved_at) == (date(2026, 9, 28), date(2026, 10, 6))
    assert p.publishable


def test_accepted_findings_stay_listed_rejected_ones_are_hidden() -> None:
    price = claim("bundle_price", "21900 COP/month")
    intro = claim("bundle_intro_price", "19900 COP/month")
    decisions = [
        ReviewDecision(id=price.id, hash=price.hash, decision="accept", decided_on=TODAY),
        ReviewDecision(id=intro.id, hash=intro.hash, decision="reject", decided_on=TODAY),
    ]
    out = proposals(facts=facts(price, intro), review=ReviewIndex(ReviewFile(decisions=decisions)))
    # A value is accepted by editing the YAML: an "accept" line does not hide it.
    assert [(p.field, p.review) for p in out] == [("bundle_price", "accepted")]


def test_big_change_is_measured_against_todays_yaml() -> None:
    stale = claim("bundle_price", "21900 COP/month", big_change=False)
    (p,) = proposals(bundle(price={"amount": "9900", "currency": "COP"}), facts=facts(stale))
    assert p.big_change
    flagged = claim("bundle_price", "21900 COP/month", big_change=True)
    assert not proposals(facts=facts(flagged))[0].big_change


def test_components_resolve_by_service_and_plan() -> None:
    b = bundle()
    # The YAML names the plan; a finding that names none agrees with it.
    assert resolved(b, "bundle_includes", "netflix", TODAY)
    assert resolved(b, "bundle_includes", "netflix.standard_with_ads", TODAY)
    assert not resolved(b, "bundle_includes", "netflix.premium", TODAY)
    assert current_value(b, "bundle_includes", "netflix.premium", TODAY) == (
        "netflix.standard_with_ads"
    )
    # A service the YAML lists only as a discount is not included.
    assert not resolved(b, "bundle_includes", "hbo_max", TODAY)
    assert current_value(b, "bundle_includes", "hbo_max", TODAY) is None
    assert resolved(b, "bundle_discount", "hbo_max:30", TODAY)
    assert current_value(b, "bundle_discount", "hbo_max:25", TODAY) == "hbo_max:30"


def test_dates_availability_and_price_list() -> None:
    b = bundle(
        valid_until="2026-12-31",
        watch={
            "mode": "auto",
            "pages": [{"url": URL}, {"url": "https://www.acme.com.co/precios.pdf", "kind": "pdf"}],
        },
    )
    assert resolved(b, "bundle_offer_until", "2026-12-31", TODAY)
    assert current_value(b, "bundle_offer_until", "2027-01-31", TODAY) == "2026-12-31"
    assert resolved(b, "bundle_available", "true", TODAY)
    assert current_value(b, "bundle_available", "false", TODAY) == "true"
    assert resolved(b, "bundle_price_list_url", "https://www.acme.com.co/precios.pdf#p2", TODAY)
    new = "https://www.acme.com.co/precios-2027.pdf"
    assert current_value(b, "bundle_price_list_url", new, TODAY) == (
        "https://www.acme.com.co/precios.pdf"
    )


def test_only_official_value_checked_proposals_are_published() -> None:
    w = watch(hint("21900"))
    f = facts(
        claim("bundle_price", "21900 COP/month"),  # the same change as the hint
        claim("bundle_available", "false", quote="Acme Total ya no está disponible."),
        claim(
            "bundle_intro_price",
            "19900 COP/month",
            url="https://www.enter.co/acme",
            source_class="press",
        ),
    )
    out = proposals(watch=w, facts=f)
    assert [(p.source, p.field, p.value_check, p.official) for p in out] == [
        ("watcher", "bundle_price", "ok", True),
        ("agent", "bundle_price", "ok", True),
        ("agent", "bundle_intro_price", "ok", False),
        ("agent", "bundle_available", "unchecked", True),
    ]
    # One entry per change (the watcher's first); no value check or not official: CLI only.
    assert [(p.source, p.field) for p in pending_changes(out)] == [("watcher", "bundle_price")]


# ------------------------------------------------------------------ display state


def test_a_pending_change_puts_the_bundle_in_review() -> None:
    b = bundle(watch={"mode": "manual", "pages": [{"url": URL}]})
    changes = pending_changes(proposals(b, facts=facts(claim("bundle_price", "21900 COP/month"))))
    s = bundle_state(b, today=TODAY, pending_changes=changes)
    assert s.display_status == "needs_review" and s.confidence == "low"
    assert s.reasons_es == (
        "Cambios pendientes de revisión: precio del paquete (según la investigación "
        "automática, en la página oficial).",
    )
    # A manual verification does not outrank it: the official page says something else.
    manual = bundle(
        watch={"mode": "manual", "pages": [{"url": URL}]},
        verified_by="user_report",
        user_report={"date": TODAY.isoformat(), "note": "visto"},
    )
    assert bundle_state(manual, today=TODAY).display_status == "manual"
    assert bundle_state(manual, today=TODAY, pending_changes=changes).display_status == (
        "needs_review"
    )
    assert bundle_state(b, today=TODAY).display_status == "unverified"


# ------------------------------------------------------------------ snippets


def _merge(row: dict[str, Any], fragment: dict[str, Any]) -> dict[str, Any]:
    """Paste a snippet into a bundles.yaml row the way a reviewer would."""
    out = dict(row)
    for key, value in fragment.items():
        if key == "price":
            out["price"] = {**out["price"], **value}
        elif key == "watch":
            w = dict(out.get("watch", {}))
            for part in ("pages", "anchors"):
                w[part] = [*w.get(part, []), *value.get(part, [])]
            out["watch"] = w
        elif key == "components":
            new = {c["service_id"] for c in value}
            kept = [c for c in out["components"] if c["service_id"] not in new]
            out["components"] = [*kept, *value]
        elif key == "evidence":
            out["evidence"] = [*out.get("evidence", []), *value]
        else:
            out[key] = value
    return out


def _file(*rows: dict[str, Any]) -> BundlesFile:
    return BundlesFile.model_validate({"providers": [PROVIDER], "bundles": list(rows)})


def _errors(row: dict[str, Any]) -> list[str]:
    return [e for e in integrity_errors(load_catalog(), bundles=_file(row)) if "acme_total" in e]


def test_the_price_snippet_carries_the_value_and_the_literal_anchor() -> None:
    b = bundle()
    (p,) = proposals(b, facts=facts(claim("bundle_price", "21900 COP/month", quote=TIER_PRICE)))
    s = snippet(b, p, bundles=_file(b.model_dump(mode="json")))
    assert s.path == "price.amount" and s.notes == []
    text = s.text()
    assert text.splitlines()[0] == "# data/curated/bundles.yaml, paquete acme_total: price.amount"
    fragment = yaml.safe_load(text)
    assert fragment == {
        "price": {"amount": "21900"},
        "watch": {"anchors": [{"fact": "price", "url": URL, "text": TIER_PRICE}]},
    }
    # Pasted into the row, the anchor passes validate-catalog's offline value check.
    row = _merge(b.model_dump(mode="json", exclude_none=True), fragment)
    assert _errors(row) == []
    # ...and the proposal resolves itself.
    edited = Bundle.model_validate(row)
    assert proposals(edited, facts=facts(claim("bundle_price", "21900 COP/month"))) == []


def test_a_snippet_says_when_its_anchor_needs_more_text() -> None:
    # The agent's quote verified with the page's heading ("Acme Total") two lines up; alone it
    # names no tier, and validate-catalog checks an anchor's text alone.
    b = bundle()
    (p,) = proposals(b, facts=facts(claim("bundle_price", "21900 COP/month")))
    s = snippet(b, p, bundles=_file(b.model_dump(mode="json")))
    (note,) = s.notes
    assert note.startswith("validate-catalog no aceptará este texto tal cual (")
    assert "names no tier" in note


def test_snippets_for_intro_components_and_dates_validate() -> None:
    b = bundle()
    f = facts(
        claim("bundle_intro_price", "19900 COP/month", quote=TIER_INTRO),
        claim(
            "bundle_includes", "netflix.premium", quote="Netflix Premium incluido en Acme Total."
        ),
        claim("bundle_discount", "hbo_max:40", quote="40% OFF en HBO Max con Acme Total."),
        claim(
            "bundle_offer_until",
            "2026-12-31",
            quote="Oferta válida hasta el 31 de diciembre de 2026.",
        ),
    )
    row = b.model_dump(mode="json", exclude_none=True)
    shown = proposals(b, facts=f)
    assert [p.field for p in shown] == [
        "bundle_intro_price",
        "bundle_offer_until",
        "bundle_includes",
        "bundle_discount",
    ]
    for p in shown:
        s = snippet(b, p, bundles=_file(b.model_dump(mode="json")))
        # Only the reminder that a component replaces the service's current entry.
        assert all(n.startswith("reemplaza la entrada actual") for n in s.notes), s.notes
        fragment = yaml.safe_load(s.text())
        assert fragment["watch"]["anchors"][0]["text"] == p.quote
        row = _merge(row, fragment)
    row["price"]["intro_months"] = 2
    assert _errors(row) == []
    edited = Bundle.model_validate(row)
    assert edited.valid_until == date(2026, 12, 31)
    assert [(c.service_id, c.plan_id, c.inclusion) for c in edited.components] == [
        ("netflix", "premium", "included"),
        ("hbo_max", None, "discount_pct"),
    ]
    assert proposals(edited, facts=f) == []


OLD_ANCHORS = [
    {"fact": "price", "url": URL, "text": "Acme Total cuesta $ 24.900 por mes."},
    {
        "fact": "component",
        "service_id": "netflix",
        "url": URL,
        "text": "Netflix Estándar con anuncios incluido en Acme Total.",
    },
    {
        "fact": "component",
        "service_id": "hbo_max",
        "url": URL,
        "text": "30% OFF en HBO Max con Acme Total.",
    },
    {"fact": "validity", "url": URL, "text": "Oferta válida hasta el 15 de noviembre de 2026."},
]


def test_a_snippet_names_the_anchor_that_states_the_old_value() -> None:
    b = bundle(
        valid_until="2026-11-15",
        watch={"mode": "auto", "pages": [{"url": URL}], "anchors": OLD_ANCHORS},
    )
    row = b.model_dump(mode="json", exclude_none=True)
    assert _errors(row) == []  # the anchors confirm today's YAML
    f = facts(
        claim("bundle_price", "21900 COP/month", quote=TIER_PRICE),
        claim(
            "bundle_includes", "netflix.premium", quote="Netflix Premium incluido en Acme Total."
        ),
        claim("bundle_discount", "hbo_max:40", quote="40% OFF en HBO Max con Acme Total."),
        claim(
            "bundle_offer_until",
            "2026-12-31",
            quote="Oferta válida hasta el 31 de diciembre de 2026.",
        ),
    )
    shown = proposals(b, facts=f)
    assert len(shown) == 4
    data = _file(b.model_dump(mode="json"))
    for p in shown:
        (old,) = replaced_anchors(b, p, bundles=data)
        s = snippet(b, p, bundles=data)
        (note,) = [n for n in s.notes if n.startswith("quita el ancla")]
        assert old.fact in note and f'"{old.text}"' in note
        fragment = yaml.safe_load(s.text())
        # Pasted without removing the old anchor, validate-catalog refuses the row...
        assert _errors(_merge(row, fragment)), p.field
        # ...and with it removed, as the note says, it passes.
        kept = [a for a in row["watch"]["anchors"] if a["text"] != old.text]
        row = _merge({**row, "watch": {**row["watch"], "anchors": kept}}, fragment)
        assert _errors(row) == [], p.field
    assert proposals(Bundle.model_validate(row), facts=f) == []


def test_an_anchor_that_still_holds_is_not_replaced() -> None:
    plain = {
        "fact": "component",
        "service_id": "netflix",
        "url": URL,
        "text": "Netflix incluido en tu plan Acme Total.",
    }
    both = {
        "fact": "validity",
        "url": URL,
        "text": "Desde el 1 de octubre de 2026 hasta el 15 de noviembre de 2026.",
    }
    b = bundle(
        valid_from="2026-10-01",
        valid_until="2026-11-15",
        watch={"mode": "auto", "pages": [{"url": URL}], "anchors": [plain, both]},
    )
    f = facts(
        claim(
            "bundle_includes", "netflix.premium", quote="Netflix Premium incluido en Acme Total."
        ),
        claim(
            "bundle_offer_until",
            "2026-12-31",
            quote="Oferta válida hasta el 31 de diciembre de 2026.",
        ),
    )
    for p in proposals(b, facts=f):
        # No plan named: it still confirms netflix.premium; the dates one still states valid_from.
        assert replaced_anchors(b, p) == [], p.field
        assert not any(n.startswith("quita el ancla") for n in snippet(b, p).notes)


def test_snippet_notes() -> None:
    b = bundle(watch={"mode": "manual", "pages": []})
    elsewhere = "https://www.acme.com.co/precios"
    (p,) = proposals(
        b, facts=facts(claim("bundle_price", "9900 COP/month", quote="$ 9.900", url=elsewhere))
    )
    s = snippet(b, p)
    assert yaml.safe_load(s.text())["watch"]["pages"] == [{"url": elsewhere, "kind": "html"}]
    assert any("watch.pages" in n for n in s.notes)
    assert any("7 caracteres" in n for n in s.notes)
    assert any("watch.mode es manual" in n for n in s.notes)
    assert any("cambio grande" in n for n in s.notes)
    # A field no anchor fact confirms keeps the quote as official evidence.
    months = Proposal(
        id="x",
        hash="0" * 12,
        bundle_id="acme_total",
        source="agent",
        field="bundle_contract_months",
        current=None,
        proposed="12",
        url=URL,
        quote="Permanencia mínima de 12 meses.",
        value_check="ok",
        official=True,
    )
    assert yaml.safe_load(snippet(b, months).text()) == {
        "price": {"contract_months": 12},
        "evidence": [{"url": URL, "kind": "official", "quote": "Permanencia mínima de 12 meses."}],
    }

"""Per-profile bundle verdicts (haggler.bundles.evaluate): the expected matrix of the phase 3
plan, the curated / home / composed routes, the curated false-declaration inheritance (user
decision 1) and the wording blacklist."""

from __future__ import annotations

import json
import re
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Any

import pytest

from haggler.bundles.evaluate import BundleAssessment, assess_bundle
from haggler.bundles.status import BundleState
from haggler.catalog import (
    load_bundles,
    load_catalog,
    load_feasibility,
    load_payment_instruments,
    load_profiles,
)
from haggler.feasibility import UNKNOWN_RANK, find_record, record_rank
from haggler.models.curated import Bundle, FeasibilityFile, FeasibilityRecord, UserReport
from haggler.models.profiles import Profile
from haggler.models.research import (
    Claim,
    ReviewDecision,
    ReviewFile,
    SourceCheck,
    SubjectFacts,
    claim_hash,
    claim_id,
)
from haggler.publish import site_data
from haggler.research.evaluate import EvalContext, assess

TODAY = date(2026, 10, 10)
CAT = load_catalog()
FEAS = load_feasibility()
INSTRUMENTS = load_payment_instruments()
PROFILES = load_profiles()
assert PROFILES is not None
CO = PROFILES.profile("se_resident_co_citizen")
EU = PROFILES.profile("se_resident_eu_generic")
REAL = {b.bundle_id: b for b in load_bundles().bundles}


def context(
    *subjects: SubjectFacts,
    review: ReviewFile | None = None,
    feasibility: FeasibilityFile = FEAS,
) -> EvalContext:
    return EvalContext.build(
        catalog=CAT,
        feasibility=feasibility,
        instruments=INSTRUMENTS,
        subjects=subjects,
        review=review,
        today=TODAY,
    )


CTX = context()


def bundle(**overrides: Any) -> Bundle:
    data: dict[str, Any] = {
        "bundle_id": "acme_co",
        "provider_id": "acme",
        "provider_type": "marketplace",
        "country": "CO",
        "name": "Acme Total",
        "tier": "total",
        "official_url": "https://www.acme.com.co/planes",
        "price": {"amount": "27000", "currency": "COP"},
        "components": [],
        "last_verified": "2026-10-01",
    }
    data.update(overrides)
    return Bundle.model_validate(data)


def included(service_id: str, plan_id: str | None = None) -> dict[str, Any]:
    return {"service_id": service_id, "plan_id": plan_id, "inclusion": "included"}


# Wave-1 rows that are not in bundles.yaml yet (milestone H10): the shapes the plan describes.
APPLE_ONE_CO = bundle(
    bundle_id="apple_one_individual_co",
    provider_id="apple",
    provider_type="platform",
    name="Apple One Individual",
    tier="individual",
    official_url="https://www.apple.com/co/apple-one/",
    price={
        "currency": "COP",
        "source": "observed",
        "observed_service_id": "apple_one",
        "observed_plan_id": "individual",
    },
    components=[
        included("apple_music", "individual"),
        included("apple_tv", "standard"),
        included("apple_arcade", "standard"),
        included("icloud_plus", "50gb"),
    ],
    requirements={"local_payment": "any_card"},
)
GOOGLE_AI_PRO_CO = bundle(
    bundle_id="google_ai_pro_co",
    provider_id="google",
    provider_type="platform",
    name="Google AI Pro",
    tier="ai_pro",
    official_url="https://gemini.google/co/subscriptions/",
    price={
        "currency": "COP",
        "source": "observed",
        "observed_service_id": "google_one",
        "observed_plan_id": "ai_pro",
    },
    components=[included("youtube_premium", "lite")],
)
SYNTHETIC = {b.bundle_id: b for b in (APPLE_ONE_CO, GOOGLE_AI_PRO_CO)}


def verdict(bundle_id: str, profile: Profile, ctx: EvalContext = CTX) -> BundleAssessment:
    b = REAL.get(bundle_id) or SYNTHETIC[bundle_id]
    a = assess_bundle(b, profile, ctx)
    assert a is not None
    return a


# ------------------------------------------------------------------- the plan's matrix

YOUTUBE_CO = find_record(
    FEAS, "youtube_premium", "CO", residence_country="SE", profile="se_resident_co_citizen"
)
assert YOUTUBE_CO is not None


@pytest.mark.parametrize(
    ("bundle_id", "profile", "rank", "provenance"),
    [
        ("telia_se_obegransad_streaming", CO, 0, "home"),
        ("telia_se_obegransad_streaming", EU, 0, "home"),
        ("apple_one_individual_se", CO, 0, "home"),
        ("apple_one_individual_se", EU, 0, "home"),
        ("apple_one_family_se", CO, 0, "home"),
        ("apple_one_family_se", EU, 0, "home"),
        ("meli_plus_co_total", CO, 2, "curated"),
        ("meli_plus_co_total", EU, 4, "composed"),  # cédula
        ("meli_plus_co_esencial", CO, 2, "curated"),
        ("meli_plus_co_esencial", EU, 4, "composed"),
        # Composed from assess(apple_one, CO): the curated Apple One CO record (rank 2);
        # Apple Arcade has no record and is listed as "sin evaluar".
        ("apple_one_individual_co", CO, 2, "composed"),
        ("apple_one_individual_co", EU, UNKNOWN_RANK, "composed"),
        # Inherits YouTube Premium CO's curated rank (a false-declaration step): never lower.
        ("google_ai_pro_co", CO, record_rank(YOUTUBE_CO), "composed"),
        ("google_ai_pro_co", EU, UNKNOWN_RANK, "composed"),
        ("meli_plus_mx_mega", CO, 4, "composed"),  # CURP
        ("meli_plus_mx_mega", EU, 4, "composed"),
        ("meli_plus_br_mega", CO, 4, "composed"),  # CPF
        ("meli_plus_br_mega", EU, 4, "composed"),
    ],
    ids=lambda v: v.id if isinstance(v, Profile) else str(v),
)
def test_expected_matrix(bundle_id: str, profile: Profile, rank: int, provenance: str) -> None:
    a = verdict(bundle_id, profile)
    assert (a.rank, a.provenance) == (rank, provenance)
    assert a.label == ("unknown" if rank == UNKNOWN_RANK else a.label)
    assert a.profile == profile.id and a.bundle_id == bundle_id


def test_matrix_relations() -> None:
    apple_one = assess("apple_one", "CO", CO, CTX)
    assert verdict("apple_one_individual_co", CO).rank == apple_one.rank == 2
    assert record_rank(YOUTUBE_CO) == 3
    assert verdict("google_ai_pro_co", CO).rank >= record_rank(YOUTUBE_CO)
    for bundle_id in ("google_ai_pro_co", "apple_one_individual_co"):
        assert verdict(bundle_id, EU).rank in (4, UNKNOWN_RANK)


def test_cedula_is_a_plain_fact() -> None:
    a = verdict("meli_plus_co_total", EU)
    assert a.label == "not_feasible" and a.practical == "impossible"
    assert "Exige un documento de identidad de CO (cédula); el perfil no tiene uno." in (
        a.reasons_es
    )
    mx = verdict("meli_plus_mx_mega", CO)
    assert "Exige un documento de identidad de MX (CURP); el perfil no tiene uno." in mx.reasons_es


def test_home_bundle_records_its_requirements() -> None:
    a = verdict("telia_se_obegransad_streaming", CO)
    assert (a.label, a.legality, a.tos, a.practical) == (
        "official_home",
        "lawful",
        "compliant",
        "remote",
    )
    assert [(s.type, s.value) for s in a.steps] == [("local_id", "SE"), ("local_phone", "SE")]
    assert {c.rank for c in a.components} == {0}
    # Its gates come from the bundle's own unverified data: no more verified than the bundle.
    assert (a.confidence, a.status) == ("low", "unverified")


def test_home_verdict_follows_the_bundle_state() -> None:
    home = bundle(country="SE", price={"amount": "99", "currency": "SEK"})
    manual = home.model_copy(
        update={
            "verified_by": "user_report",
            "user_report": UserReport(date=TODAY, note="visto en la web"),
            "confidence": "high",
        }
    )
    a = assess_bundle(manual, CO, CTX)
    assert a is not None and (a.rank, a.provenance) == (0, "home")
    assert (a.confidence, a.status) == ("medium", "unverified")  # manual: medium at most
    verified = BundleState(display_status="verified", reasons_es=(), confidence="high")
    a = assess_bundle(home, CO, CTX, state=verified)
    assert a is not None and (a.confidence, a.status) == ("high", "verified")


# ---------------------------------------------------------------------------- curated


def test_curated_record_is_shown_unverified_and_low() -> None:
    a = verdict("meli_plus_co_total", CO)
    assert (a.legality, a.tos, a.practical) == ("lawful", "breach_unenforced", "remote")
    assert (a.status, a.confidence) == ("unverified", "low")
    assert a.notes_es == "según uso propio; sin cita de los términos"
    # Discounted services are not part of the verdict; Disney+ (included) is.
    assert [(c.service_id, c.rank, c.provenance) for c in a.components] == [
        ("disney_plus", 2, "curated")
    ]


def _curated(**record: Any) -> dict[str, Any]:
    base = {
        "profile": CO.id,
        "legality": "lawful",
        "tos": "compliant",
        "practical": "remote",
        "last_verified": "2026-10-01",
    }
    return base | record


def test_curated_record_without_verified_clause_is_forced_unverified() -> None:
    b = bundle(
        verdicts=[
            _curated(
                tos="breach_unenforced",
                status="verified",
                confidence="high",
                tos_clause={"url": "https://www.acme.com.co/terminos", "quote": "Solo en CO."},
            )
        ]
    )
    a = assess_bundle(b, CO, CTX)
    assert a is not None and a.rank == 2
    assert (a.status, a.confidence) == ("unverified", "low")


def test_curated_false_declaration_step_is_at_least_2() -> None:
    step = {"type": "billing_address", "value": "CO", "requires_false_declaration": True}
    a = assess_bundle(bundle(verdicts=[_curated(steps=[step])]), CO, CTX)
    assert a is not None
    assert a.rank == 2 and a.label == "tos_breach_low_enforcement"


@pytest.mark.parametrize(
    ("requirements", "step", "reason"),
    [
        ({"residency": "required"}, "declare_country", "Exige vivir en CO y el perfil vive en SE."),
        (
            {"billing_address": "required"},
            "billing_address",
            "Exige una dirección de facturación en CO y el perfil no tiene una real allí.",
        ),
    ],
)
def test_curated_record_keeps_a_stated_false_declaration(
    requirements: dict[str, Any], step: str, reason: str
) -> None:
    # The record says lawful / compliant / remote, but the YAML states a requirement the profile
    # can only meet by declaring something false: it is stated, and the rank is at least 2
    # (decision 1), never the record's 0.
    a = assess_bundle(bundle(requirements=requirements, verdicts=[_curated()]), CO, CTX)
    assert a is not None and a.provenance == "curated"
    assert (a.rank, a.legality, a.practical) == (2, "requires_misrepresentation", "remote")
    assert reason in a.reasons_es
    assert [(s.type, s.value) for s in a.steps if s.requires_false_declaration] == [(step, "CO")]
    # A real address in CO is no false declaration.
    if step == "billing_address":
        lives_part_time = CO.model_copy(update={"address_countries": ["SE", "CO"]})
        b = bundle(requirements=requirements, verdicts=[_curated()])
        real = assess_bundle(b, lives_part_time, CTX)
        assert real is not None and (real.rank, real.legality) == (0, "lawful")


def test_curated_record_states_a_credit_check() -> None:
    a = assess_bundle(bundle(requirements={"credit_check": True}, verdicts=[_curated()]), CO, CTX)
    assert a is not None and a.practical == "remote"  # the record judged practicality
    assert "Hace una consulta de crédito en CO; no se sabe si el perfil la supera." in a.reasons_es


def test_curated_rank_is_never_below_the_worst_known_component() -> None:
    b = bundle(
        components=[included("spotify"), included("apple_arcade")],
        verdicts=[_curated()],
    )
    a = assess_bundle(b, CO, CTX)
    assert a is not None
    # Spotify CO is curated breach_enforced (3); Arcade has no record: listed, not decisive.
    assert (a.rank, a.tos) == (3, "breach_enforced")
    assert "Incluye Apple Arcade, sin evaluar en CO." in a.reasons_es
    assert [(c.service_id, c.label, c.provenance) for c in a.components] == [
        ("spotify", "tos_breach_enforced", "curated"),
        ("apple_arcade", "unknown", None),  # nothing evaluated it
    ]


def test_a_component_that_is_not_feasible_makes_the_bundle_not_feasible() -> None:
    impossible = FeasibilityRecord(
        id="vix_co_from_se",
        service_id="vix",
        target_country="CO",
        residence_country="SE",
        profile=CO.id,
        legality="lawful",
        tos="compliant",
        practical="impossible",
        last_verified=TODAY,
    )
    feas = FeasibilityFile(profiles=FEAS.profiles, records=[*FEAS.records, impossible])
    b = bundle(components=[included("vix")], verdicts=[_curated()])
    a = assess_bundle(b, CO, context(feasibility=feas))
    assert a is not None and (a.rank, a.practical) == (4, "impossible")
    assert "Incluye ViX Premium, que no es factible en CO para este perfil." in a.reasons_es


# ----------------------------------------------- curated false declaration (decision 1)


def test_google_ai_pro_inherits_youtube_curated_rank() -> None:
    a = verdict("google_ai_pro_co", CO)
    assert a.rank == 3 and a.label == "tos_breach_enforced"
    # The step and its reason are copied from YouTube Premium CO's curated record.
    copied = [s for s in a.steps if s.requires_false_declaration]
    assert [(s.type, s.value) for s in copied] == [("presence", "CO")]
    # What is false is the country of residence (the trip itself is real).
    assert (
        "Incluye YouTube Premium: su ficha curada en CO exige una declaración falsa (declarar CO "
        "como país de residencia), así que el paquete hereda su nivel «Contra los términos, "
        "control activo»." in a.reasons_es
    )
    # Google One CO has no verdict: the rank is a floor, and the verdict says so.
    assert "Hay partes del paquete sin evaluar: el nivel podría ser peor." in a.assumptions_es
    assert (a.legality, a.tos) == ("requires_misrepresentation", "breach_enforced")


def test_inheritance_also_holds_on_the_bundle_own_route() -> None:
    b = bundle(components=[included("youtube_premium", "lite"), included("google_one")])
    a = assess_bundle(b, CO, CTX)
    assert a is not None and a.rank == record_rank(YOUTUBE_CO)
    # ... and on a curated one, which can be no lower either.
    b = bundle(components=[included("youtube_premium", "lite")], verdicts=[_curated()])
    a = assess_bundle(b, CO, CTX)
    assert a is not None and a.rank == 3 and a.provenance == "curated"


def test_inheritance_never_lowers_an_impossible_bundle() -> None:
    b = bundle(
        components=[included("youtube_premium", "lite")],
        requirements={"local_line": "mobile"},
    )
    a = assess_bundle(b, CO, CTX)
    assert a is not None and a.rank == 4  # no Colombian phone
    assert "Exige una línea móvil de CO; el perfil no tiene un teléfono de CO." in a.reasons_es


def test_a_component_eu_right_brings_its_badge() -> None:
    google_one = assess("google_one", "LT", CO, CTX)
    assert (google_one.legality, google_one.eu_badge) == ("contestable_eu_right", True)
    lt = {"country": "LT", "price": {"amount": "9", "currency": "EUR"}}
    curated = assess_bundle(
        bundle(**lt, components=[included("google_one")], verdicts=[_curated()]), CO, CTX
    )
    own = assess_bundle(bundle(**lt, components=[included("icloud_plus")]), CO, CTX)
    for a in (curated, own):
        assert a is not None and (a.legality, a.eu_badge) == ("contestable_eu_right", True)
    # Without such a component the curated lawful record has no badge.
    plain = assess_bundle(bundle(**lt, verdicts=[_curated()]), CO, CTX)
    assert plain is not None and (plain.legality, plain.eu_badge) == ("lawful", False)


# ----------------------------------------------------------------- composed own route


def test_own_route_without_facts_is_unknown() -> None:
    a = verdict("go3_lt_filmai_netflix", CO)
    assert (a.rank, a.tos, a.provenance) == (UNKNOWN_RANK, "unknown", "composed")
    assert "No se encontró en los términos una regla sobre el país de residencia." in a.reasons_es
    assert "Incluye Netflix, sin evaluar en LT." in a.reasons_es


@pytest.mark.parametrize(
    ("requirements", "profile", "reason"),
    [
        ({"residency": "required"}, CO, "Exige vivir en CO y el perfil vive en SE."),
        (
            {"billing_address": "required"},
            EU,
            "Exige una dirección de facturación en CO y el perfil no tiene una real allí.",
        ),
    ],
)
def test_false_declaration_routes_are_rank_4(
    requirements: dict[str, Any], profile: Profile, reason: str
) -> None:
    a = assess_bundle(bundle(requirements=requirements), profile, CTX)
    assert a is not None
    assert (a.rank, a.legality, a.practical) == (4, "requires_misrepresentation", "impossible")
    assert reason in a.reasons_es
    assert any(s.requires_false_declaration for s in a.steps)


def test_an_unknown_component_keeps_a_known_false_declaration() -> None:
    # Netflix CO is not evaluated for this profile; the route's false declaration still stands.
    b = bundle(
        requirements={"residency": "required", "billing_address": "required"},
        components=[included("netflix")],
    )
    a = assess_bundle(b, EU, CTX)
    assert a is not None and "Incluye Netflix, sin evaluar en CO." in a.reasons_es
    assert (a.rank, a.legality, a.practical) == (4, "requires_misrepresentation", "impossible")
    assert a.tos == "unknown"  # neither the route nor Netflix has a ToS fact


def test_a_real_billing_address_is_no_false_declaration() -> None:
    lives_part_time = CO.model_copy(update={"address_countries": ["SE", "CO"]})
    a = assess_bundle(bundle(requirements={"billing_address": "required"}), lives_part_time, CTX)
    assert a is not None and a.rank == UNKNOWN_RANK  # the ToS is still unknown
    assert a.legality == "lawful"
    assert [(s.type, s.requires_false_declaration) for s in a.steps if s.type == "billing_address"]
    assert not any(s.requires_false_declaration for s in a.steps)


def test_payment_gates() -> None:
    card = bundle(requirements={"local_payment": "local_card"})
    mine = assess_bundle(card, CO, CTX)
    assert mine is not None and mine.practical != "impossible"
    assert ("payment_country", "CO") in [(s.type, s.value) for s in mine.steps]
    other = assess_bundle(card, EU, CTX)
    assert other is not None and other.rank == 4
    assert "Exige una tarjeta emitida en CO; el perfil no tiene ni puede obtener una." in (
        other.reasons_es
    )

    bank = bundle(country="LT", price={"amount": "9", "currency": "EUR"},
                  requirements={"local_payment": "local_bank"})  # fmt: skip
    opens = assess_bundle(bank, EU, CTX)
    assert opens is not None and opens.practical != "impossible"
    assert ("obtain_instrument", "revolut_eea") in [(s.type, s.value) for s in opens.steps]
    stays = EU.model_copy(update={"obtain_remote_instruments": False})
    closed = assess_bundle(bank, stays, CTX)
    assert closed is not None and closed.rank == 4
    assert "Exige una cuenta bancaria de LT; el perfil no tiene ni puede abrir una." in (
        closed.reasons_es
    )

    carrier = assess_bundle(bundle(requirements={"local_payment": "carrier_bill"}), CO, CTX)
    assert carrier is not None and carrier.rank == 4


def test_presence_and_credit_check() -> None:
    presence = bundle(requirements={"signup_presence": "checked"})
    traveller = assess_bundle(presence, CO, CTX)
    assert traveller is not None
    assert ("presence", "CO", True) in [
        (s.type, s.value, s.requires_presence) for s in traveller.steps
    ]
    stays = assess_bundle(presence, EU, CTX)
    assert stays is not None and stays.rank == 4
    assert "Exige estar en CO al contratar; el perfil no viaja a CO." in stays.reasons_es

    credit = assess_bundle(bundle(requirements={"credit_check": True}), CO, CTX)
    assert credit is not None and credit.practical == "unknown"
    assert any("consulta de crédito" in r for r in credit.reasons_es)


def test_home_gates_state_the_missing_requirement() -> None:
    newcomer = Profile(id="newcomer", name_es="x", residence_country="SE")
    a = verdict("telia_se_obegransad_streaming", newcomer)
    assert (a.rank, a.label, a.practical, a.provenance) == (4, "not_feasible", "impossible", "home")
    assert a.reasons_es == (
        "Exige un documento de identidad de SE (personnummer); el perfil no tiene uno.",
        "Exige una línea móvil de SE; el perfil no tiene un teléfono de SE.",
    )
    # Paying at home is not a gate: Apple One SE stays the official offer.
    assert verdict("apple_one_individual_se", newcomer).rank == 0


def test_expired_bundle_has_no_verdict() -> None:
    assert assess_bundle(bundle(status="expired"), CO, CTX) is None
    assert assess_bundle(bundle(valid_until="2026-10-09"), CO, CTX) is None


# ----------------------------------------------------------------------- pending review


def _claim(subject: str, field: str, value: str) -> Claim:
    url = "https://help.netflix.com/legal/termsofuse"
    quote = f"Quote for {field} {value}."
    return Claim(
        id=claim_id(subject, field, value),
        hash=claim_hash(field, value, None, quote, url),
        field=field,
        value=value,
        quote=quote,
        check=SourceCheck(
            cited_url=url,
            final_url=url,
            source_class="official",
            status="verified",
            retrieved_at=datetime(2026, 10, 9, tzinfo=UTC),
        ),
        first_seen=TODAY,
    )


def _subject(key: str, *claims: Claim) -> SubjectFacts:
    return SubjectFacts(
        key=key,
        kind=key.split(":", 1)[0],  # type: ignore[arg-type]
        researched_on=TODAY,
        run_id="r1",
        status="researched",
        claims=list(claims),
    )


def test_a_pending_component_finding_gives_an_agent_pending_variant() -> None:
    clause = _claim("service:netflix", "residence_clause", "primary_use_in_account_country")
    card = _claim("pair:netflix:CO", "payment_card_country", "must_match")
    accepted = ReviewFile(
        decisions=[
            ReviewDecision(id=clause.id, hash=clause.hash, decision="accept", decided_on=TODAY)
        ]
    )
    ctx = context(
        _subject("service:netflix", clause), _subject("pair:netflix:CO", card), review=accepted
    )
    b = bundle(components=[included("netflix", "standard")])
    reviewed = assess_bundle(b, EU, ctx)
    pending = assess_bundle(b, EU, ctx, include_pending=True)
    assert reviewed is not None and pending is not None
    assert (reviewed.provenance, reviewed.rank) == ("composed", UNKNOWN_RANK)
    # The pending card rule makes Netflix CO not feasible for this profile.
    assert (pending.provenance, pending.rank) == ("agent_pending", 4)
    assert not reviewed.same_verdict(pending)
    assert [c.provenance for c in pending.components] == ["agent_pending"]


# ------------------------------------------- the bundle subject's own research facts (H8)


def _accept(*claims: Claim, decision: str = "accept") -> ReviewFile:
    return ReviewFile(
        decisions=[
            ReviewDecision(id=c.id, hash=c.hash, decision=decision, decided_on=TODAY)  # type: ignore[arg-type]
            for c in claims
        ]
    )


def test_accepted_bundle_facts_decide_the_own_route_tos() -> None:
    clause = _claim("bundle:acme_co", "bundle_residence_clause", "must_reside")
    facts = _subject("bundle:acme_co", clause)
    b = bundle()
    # Pending: the reviewed verdict does not use it; the pending variant does.
    reviewed = assess_bundle(b, EU, context(facts))
    pending = assess_bundle(b, EU, context(facts), include_pending=True)
    assert reviewed is not None and pending is not None
    assert (reviewed.provenance, reviewed.tos) == ("composed", "unknown")
    assert (pending.provenance, pending.tos) == ("agent_pending", "breach_unenforced")
    # Accepted for its hash: the reviewed verdict rests on it.
    accepted = assess_bundle(b, EU, context(facts, review=_accept(clause)))
    assert accepted is not None
    assert (accepted.provenance, accepted.tos) == ("agent_reviewed", "breach_unenforced")
    # Rejected: never used, pending or not.
    rejected = assess_bundle(
        b, EU, context(facts, review=_accept(clause, decision="reject")), include_pending=True
    )
    assert rejected is not None and (rejected.provenance, rejected.tos) == ("composed", "unknown")


def test_a_foreign_offer_is_only_allowed_on_an_official_bundle_finding() -> None:
    allowed = _claim("bundle:acme_co", "bundle_foreign_offer_allowed", "true")
    press = allowed.model_copy(
        update={"check": allowed.check.model_copy(update={"source_class": "press"})}
    )
    a = assess_bundle(
        bundle(), EU, context(_subject("bundle:acme_co", allowed), review=_accept(allowed))
    )
    assert a is not None and a.tos == "compliant"
    b = assess_bundle(
        bundle(), EU, context(_subject("bundle:acme_co", press), review=_accept(press))
    )
    assert b is not None and b.tos == "unknown"


def test_bundle_value_findings_never_change_a_verdict() -> None:
    # Values are accepted by editing bundles.yaml: a value claim is not a feasibility fact.
    price = _claim("bundle:acme_co", "bundle_price", "1000 COP/month")
    ctx = context(_subject("bundle:acme_co", price), review=_accept(price))
    a, base = assess_bundle(bundle(), EU, ctx), assess_bundle(bundle(), EU, CTX)
    assert a is not None and base is not None and a.same_verdict(base)
    assert a.provenance == base.provenance == "composed"


def test_bundle_facts_reach_site_data_through_data_research(tmp_path: Path) -> None:
    from haggler.research import io as rio

    key = "bundle:meli_plus_co_total"
    clause = _claim(key, "bundle_residence_clause", "must_reside")
    data = tmp_path / "data"
    (data / "research").mkdir(parents=True)
    files = rio.subject_files([_subject(key, clause)])
    (data / "research" / "bundles.json").write_bytes(files["bundles.json"])
    subjects, _run, errors = rio.load_data_dir(data)
    assert [s.key for s in subjects] == [key] and not errors
    out = tmp_path / "site"
    site_data(
        data,
        out,
        residence="SE",
        profile=CO.id,
        catalog=CAT,
        feasibility=FEAS,
        bundles=load_bundles(),
        profiles=PROFILES,
        instruments=INSTRUMENTS,
        review=_accept(clause),
        now=datetime(2026, 10, 10, 5, 0, tzinfo=UTC),
    )
    rows = json.loads((out / "bundles.json").read_text(encoding="utf-8"))
    (row,) = [r for r in rows if r["bundle_id"] == "meli_plus_co_total"]
    eu = next(v for v in row["verdicts"] if v["profile"] == EU.id)
    # The accepted clause decides the bundle's own ToS (its unevaluated components keep the
    # axis unknown); the cédula still makes it not feasible for this profile.
    assert (eu["provenance"], eu["rank"]) == ("agent_reviewed", 4)
    without = assess_bundle(REAL["meli_plus_co_total"], EU, CTX)
    assert without is not None and without.provenance == "composed"
    # The curated record of the other profile is used as is (decision 2).
    co = next(v for v in row["verdicts"] if v["profile"] == CO.id)
    assert co["provenance"] == "curated"


# ---------------------------------------------------------------- wording blacklist

# Nothing published may suggest a way around a requirement (user principle 5).
BLACKLIST = re.compile(
    r"\bvpn\b|\bproxy\b|direcci[oó]n falsa|otra direcci[oó]n|datos falsos", re.IGNORECASE
)


def _texts(value: Any) -> list[str]:
    if isinstance(value, str):
        return [value]
    if isinstance(value, dict):
        return [t for v in value.values() for t in _texts(v)]
    if isinstance(value, list | tuple):
        return [t for v in value for t in _texts(v)]
    return []


def test_no_evasion_wording_in_bundles_json(tmp_path: Path) -> None:
    out = tmp_path / "site"
    site_data(
        tmp_path / "data",
        out,
        residence="SE",
        profile=CO.id,
        catalog=CAT,
        feasibility=FEAS,
        bundles=load_bundles(),
        profiles=PROFILES,
        instruments=INSTRUMENTS,
        now=datetime(2026, 10, 10, 5, 0, tzinfo=UTC),
    )
    rows = json.loads((out / "bundles.json").read_text(encoding="utf-8"))
    texts = _texts(rows)
    assert texts
    assert [t for t in texts if BLACKLIST.search(t)] == []


def test_no_evasion_wording_in_any_verdict() -> None:
    cases = [
        *REAL.values(),
        *SYNTHETIC.values(),
        bundle(requirements={"residency": "required", "billing_address": "required"}),
        bundle(requirements={"local_line": "both", "local_payment": "carrier_bill"}),
        bundle(requirements={"signup_presence": "checked", "credit_check": True}),
        bundle(components=[included("youtube_premium")], requirements={"local_id": "cedula"}),
    ]
    newcomer = Profile(id="newcomer", name_es="x", residence_country="SE")
    texts: list[str] = []
    for b in cases:
        for profile in (CO, EU, newcomer):
            a = assess_bundle(b, profile, CTX)
            if a is not None:
                texts += [*a.reasons_es, *a.assumptions_es, a.notes_es]
    assert texts
    assert [t for t in texts if BLACKLIST.search(t)] == []
    assert BLACKLIST.search("Usa una VPN") and BLACKLIST.search("da otra dirección")

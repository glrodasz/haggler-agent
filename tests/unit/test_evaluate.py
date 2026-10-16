"""Truth tables of the research evaluator (haggler.research.evaluate)."""

from __future__ import annotations

import subprocess
import sys
from datetime import UTC, date, datetime, timedelta

import pytest

from haggler.catalog import load_catalog, load_feasibility, load_payment_instruments
from haggler.compare import cheapest
from haggler.models.curated import FeasibilityFile, ObservedBin, PaymentInstrumentsFile
from haggler.models.observations import PublishedPrice
from haggler.models.profiles import Profile
from haggler.models.research import (
    Claim,
    ReviewDecision,
    ReviewFile,
    SourceCheck,
    SubjectFacts,
    claim_hash,
    claim_id,
    subject_key,
)
from haggler.research.evaluate import EvalContext, assess, verdict_fn

TODAY = date(2026, 10, 9)
CAT = load_catalog()
EMPTY_FEAS = FeasibilityFile(profiles={}, records=[])

CO_USER = Profile(
    id="co",
    name_es="co",
    residence_country="SE",
    citizenships=["CO"],
    id_document_countries=["CO", "SE"],
    phone_countries=["SE"],
    address_countries=["SE"],
    instruments_held=["user_co_card"],
    presence={"CO": "once"},
)
GENERIC = Profile(
    id="generic",
    name_es="generic",
    residence_country="SE",
    id_document_countries=["SE"],
    phone_countries=["SE"],
)


def claim(
    subject: str,
    field: str,
    value: str,
    *,
    cls: str = "official",
    event: date | None = None,
    url: str = "https://help.example.com/terms",
) -> Claim:
    quote = f"Quote for {field} {value}."
    return Claim(
        id=claim_id(subject, field, value, event),
        hash=claim_hash(field, value, event, quote, url),
        field=field,
        value=value,
        event_date=event,
        quote=quote,
        check=SourceCheck(
            cited_url=url,
            final_url=url,
            source_class=cls,  # type: ignore[arg-type]
            status="verified",
            retrieved_at=datetime(2026, 10, 9, tzinfo=UTC),
        ),
        first_seen=TODAY,
    )


def subject(key: str, *claims: Claim, on: date = TODAY) -> SubjectFacts:
    kind = key.split(":", 1)[0]
    return SubjectFacts(
        key=key,
        kind=kind,  # type: ignore[arg-type]
        researched_on=on,
        run_id="r1",
        status="researched",
        claims=list(claims),
    )


SVC = subject_key("service", "netflix")
PAIR_CO = subject_key("pair", "netflix", "CO")


def accept_all(*subjects: SubjectFacts) -> ReviewFile:
    return ReviewFile(
        decisions=[
            ReviewDecision(id=c.id, hash=c.hash, decision="accept", decided_on=TODAY)
            for s in subjects
            for c in s.claims
        ]
    )


def ctx(
    *subjects: SubjectFacts,
    review: ReviewFile | None = None,
    feasibility: FeasibilityFile = EMPTY_FEAS,
    instruments: PaymentInstrumentsFile | None = None,
) -> EvalContext:
    return EvalContext.build(
        catalog=CAT,
        feasibility=feasibility,
        instruments=instruments or load_payment_instruments(),
        subjects=subjects,
        review=review if review is not None else accept_all(*subjects),
        today=TODAY,
    )


ALL_PAIR_FACTS = (
    ("signup_location", "not_checked"),
    ("periodic_location_check", "false"),
    ("local_id_required", "false"),
    ("local_phone_required", "false"),
    ("billing_address", "not_required"),
)


def pair(key: str, *extra: tuple[str, str], full: bool = True) -> SubjectFacts:
    facts = dict(ALL_PAIR_FACTS) if full else {}
    facts.update(dict(extra))
    return subject(key, *(claim(key, f, v) for f, v in facts.items()))


# ------------------------------------------------------------------ inputs


def test_home_country_is_official_home():
    a = assess("netflix", "SE", CO_USER, ctx())
    assert (a.label, a.rank, a.provenance) == ("official_home", 0, "home")


def test_no_research_is_unknown():
    a = assess("netflix", "CO", CO_USER, ctx())
    assert (a.label, a.provenance, a.evaluated) == ("unknown", "none", False)
    assert a.verdict().provenance is None


def test_curated_template_applies_to_other_countries_with_the_same_ties():
    tr_user = Profile(
        id="tr",
        name_es="tr",
        residence_country="DE",
        id_document_countries=["DE"],
        phone_countries=["DE"],
        card_countries=["TR"],
        presence={"TR": "once"},
    )
    feas = load_feasibility()
    a = assess("netflix", "TR", tr_user, ctx(feasibility=feas))
    assert (a.provenance, a.practical, a.confidence) == ("template", "presence_once", "low")
    assert a.record_id == "netflix_co_from_se"
    assert [s.value for s in a.steps] == ["TR", "TR"]
    # Same steps, but the template requires declaring a false country: never feasible.
    yt = assess("youtube_premium", "TR", tr_user, ctx(feasibility=feas))
    assert (yt.provenance, yt.rank) == ("template", 4)
    # Without a card from the country (or the trip the template needs) it stays unknown.
    assert assess("netflix", "TR", GENERIC, ctx(feasibility=feas)).provenance == "none"
    no_trip = tr_user.model_copy(update={"presence": {}})
    assert assess("netflix", "TR", no_trip, ctx(feasibility=feas)).provenance == "none"


def test_template_still_applies_when_research_is_not_decisive():
    # The service was researched, but nothing settles the pair: the template still answers.
    tr_user = Profile(
        id="tr",
        name_es="tr",
        residence_country="DE",
        id_document_countries=["DE"],
        phone_countries=["DE"],
        card_countries=["TR"],
        presence={"TR": "once"},
    )
    a = assess("netflix", "TR", tr_user, ctx(subject(SVC), feasibility=load_feasibility()))
    assert (a.provenance, a.rank) == ("template", 2)


def test_template_asks_for_the_billing_address_another_record_requires():
    # icloud_plus_lt_from_se asks for a billing address in the country, so the template does too.
    card_only = Profile(
        id="br",
        name_es="br",
        residence_country="SE",
        id_document_countries=["SE"],
        phone_countries=["SE"],
        card_countries=["BR"],
    )
    feas = load_feasibility()
    a = assess("icloud_plus", "BR", card_only, ctx(feasibility=feas))
    assert (a.provenance, a.rank, a.legality) == ("template", 4, "requires_misrepresentation")
    with_address = card_only.model_copy(update={"address_countries": ["BR"]})
    b = assess("icloud_plus", "BR", with_address, ctx(feasibility=feas))
    assert (b.provenance, b.rank) == ("template", 2)
    assert ("billing_address", "BR") in [(s.type, s.value) for s in b.steps]


def test_template_resting_on_a_pending_card_is_pending():
    # N26's card country is known only from an unreviewed finding.
    key = subject_key("instrument", "n26")
    n26 = subject(key, claim(key, "card_issuing_country", "DE"))
    feas = load_feasibility()
    unreviewed = ctx(n26, review=accept_all(), feasibility=feas)
    assert assess("apple_tv", "DE", GENERIC, unreviewed).provenance == "none"
    a = assess("apple_tv", "DE", GENERIC, unreviewed, include_pending=True)
    assert (a.provenance, a.rank) == ("agent_pending", 2)
    reviewed = assess("apple_tv", "DE", GENERIC, ctx(n26, feasibility=feas))
    assert (reviewed.provenance, reviewed.rank) == ("template", 2)


def test_curated_record_wins_over_research():
    feas = load_feasibility()
    svc = subject(SVC, claim(SVC, "foreign_offer_allowed", "true"))
    curated = Profile(**(CO_USER.model_dump() | {"id": "se_resident_co_citizen"}))
    a = assess("netflix", "CO", curated, ctx(svc, pair(PAIR_CO), feasibility=feas))
    assert a.provenance == "curated" and a.record_id == "netflix_co_from_se"
    # The curated axes, not the official foreign_offer_allowed claim.
    assert (a.tos, a.practical, a.rank) == ("breach_unenforced", "presence_once", 2)
    assert a.evidence and str(a.evidence[0].url).startswith("https://help.netflix.com")


def test_stale_subjects_are_ignored():
    old = TODAY - timedelta(days=400)
    svc = subject(SVC, claim(SVC, "residence_clause", "must_reside"), on=old)
    assert assess("netflix", "CO", CO_USER, ctx(svc)).provenance == "none"


# ---------------------------------------------------------------------- ToS


@pytest.mark.parametrize(
    ("svc_claims", "tos"),
    [
        (
            [("residence_clause", "primary_use_in_account_country", "official", None)],
            "breach_unenforced",
        ),
        ([("residence_clause", "must_reside", "press", None)], "breach_unenforced"),
        (
            [
                ("residence_clause", "must_reside", "official", None),
                ("enforcement_event", "cancellation", "press", date(2025, 6, 1)),
            ],
            "breach_enforced",
        ),
        (
            # Outside the 730-day window: no longer "enforced".
            [
                ("residence_clause", "must_reside", "official", None),
                ("enforcement_event", "cancellation", "press", date(2023, 6, 1)),
            ],
            "breach_unenforced",
        ),
        ([("enforcement_event", "suspension", "official", date(2026, 1, 1))], "breach_enforced"),
        ([("foreign_offer_allowed", "true", "official", None)], "compliant"),
        # Press cannot establish a positive fact; forum never counts.
        ([("foreign_offer_allowed", "true", "press", None)], "unknown"),
        ([("residence_clause", "must_reside", "forum", None)], "unknown"),
        (
            [
                ("foreign_offer_allowed", "true", "official", None),
                ("residence_clause", "must_reside", "official", None),
            ],
            "breach_unenforced",
        ),
    ],
)
def test_tos_axis(svc_claims, tos):
    svc = subject(SVC, *(claim(SVC, f, v, cls=c, event=e) for f, v, c, e in svc_claims))
    a = assess("netflix", "CO", CO_USER, ctx(svc, pair(PAIR_CO)))
    assert a.tos == tos


# ------------------------------------------------------------- practicality


def _svc_breach() -> SubjectFacts:
    return subject(SVC, claim(SVC, "residence_clause", "primary_use_in_account_country"))


@pytest.mark.parametrize(
    ("profile", "pair_facts", "practical", "rank", "steps"),
    [
        # Held CO card satisfies must_match; no presence needed.
        (CO_USER, [("payment_card_country", "must_match")], "remote", 2, ["payment_country"]),
        # Generic profile: no CO card, none obtainable (no instrument has a CO card claim).
        (GENERIC, [("payment_card_country", "must_match")], "impossible", 4, []),
        # ... unless remote gift cards exist.
        (
            GENERIC,
            [("payment_card_country", "must_match"), ("gift_cards", "remote_purchase")],
            "remote",
            2,
            ["gift_card"],
        ),
        (GENERIC, [("payment_card_country", "any_country")], "remote", 2, []),
        # Unknown payment rule and no card of CO: unknown.
        (GENERIC, [], "unknown", 99, []),
        # Signup checked: CO_USER can travel to CO once.
        (
            CO_USER,
            [("payment_card_country", "any_country"), ("signup_location", "checked")],
            "presence_once",
            2,
            ["payment_country", "presence"],
        ),
        (
            GENERIC,
            [("payment_card_country", "any_country"), ("signup_location", "checked")],
            "impossible",
            4,
            [],
        ),
        (
            CO_USER,
            [("payment_card_country", "any_country"), ("periodic_location_check", "true")],
            "impossible",
            4,
            ["payment_country"],
        ),
        (
            CO_USER,
            [("payment_card_country", "any_country"), ("local_id_required", "true")],
            "remote",
            2,
            ["payment_country", "local_id"],
        ),
        (
            CO_USER,
            [("payment_card_country", "any_country"), ("local_phone_required", "true")],
            "impossible",
            4,
            ["payment_country"],
        ),
        (CO_USER, [("available", "false")], "impossible", 4, []),
    ],
)
def test_practicality(profile, pair_facts, practical, rank, steps):
    a = assess("netflix", "CO", profile, ctx(_svc_breach(), pair(PAIR_CO, *pair_facts)))
    assert (a.practical, a.rank) == (practical, rank)
    assert [s.type for s in a.steps if not s.requires_false_declaration] == steps


def test_billing_address_without_a_real_address_is_never_offered():
    a = assess(
        "netflix",
        "CO",
        CO_USER,
        ctx(
            _svc_breach(),
            pair(PAIR_CO, ("payment_card_country", "any_country"), ("billing_address", "required")),
        ),
    )
    assert (a.legality, a.practical, a.rank, a.label) == (
        "requires_misrepresentation",
        "impossible",
        4,
        "not_feasible",
    )
    assert any(s.requires_false_declaration for s in a.steps)
    assert any("dirección de facturación" in r for r in a.reasons_es)


def test_self_declared_country_is_a_false_declaration():
    svc = subject(
        SVC,
        claim(SVC, "residence_clause", "must_reside"),
        claim(SVC, "account_country_basis", "self_declared"),
    )
    a = assess("netflix", "CO", CO_USER, ctx(svc, pair(PAIR_CO)))
    assert a.rank == 4 and a.legality == "requires_misrepresentation"
    assert [s.type for s in a.steps if s.requires_false_declaration] == ["declare_country"]


def test_account_country_basis_implies_requirements_without_assumptions():
    svc = subject(
        SVC,
        claim(SVC, "residence_clause", "must_reside"),
        claim(SVC, "account_country_basis", "signup_location"),
        claim(SVC, "account_country_basis", "payment_method_country"),
    )
    a = assess("netflix", "CO", CO_USER, ctx(svc, pair(PAIR_CO, full=False)))
    assert a.practical == "presence_once"
    assert not any("registrarse" in x for x in a.assumptions_es)
    assert {s.type for s in a.steps} == {"payment_country", "presence"}


def test_missing_optional_facts_are_assumptions_with_low_confidence():
    a = assess(
        "netflix",
        "CO",
        CO_USER,
        ctx(_svc_breach(), pair(PAIR_CO, ("payment_card_country", "any_country"), full=False)),
    )
    assert a.rank == 2 and a.confidence == "low"
    assert len(a.assumptions_es) == 5


def test_rank_below_two_needs_facts_not_assumptions():
    svc = subject(SVC, claim(SVC, "foreign_offer_allowed", "true"))
    full = assess(
        "netflix", "CO", CO_USER, ctx(svc, pair(PAIR_CO, ("payment_card_country", "any_country")))
    )
    assert (full.rank, full.label, full.confidence) == (0, "official_home", "high")
    partial = assess(
        "netflix",
        "CO",
        CO_USER,
        ctx(svc, pair(PAIR_CO, ("payment_card_country", "any_country"), full=False)),
    )
    assert partial.rank == 99 and partial.label == "unknown"


def test_press_cannot_lower_a_requirement():
    p = subject(
        PAIR_CO,
        claim(PAIR_CO, "payment_card_country", "any_country", cls="press"),
        *(claim(PAIR_CO, f, v) for f, v in ALL_PAIR_FACTS),
    )
    a = assess("netflix", "CO", GENERIC, ctx(_svc_breach(), p))
    assert a.practical == "unknown"


def test_conflicting_claims_keep_the_least_favourable_value():
    p = pair(PAIR_CO, ("payment_card_country", "any_country"))
    p.claims.append(claim(PAIR_CO, "payment_card_country", "must_match", cls="press"))
    a = assess("netflix", "CO", GENERIC, ctx(_svc_breach(), p))
    assert a.practical == "impossible"


# --------------------------------------------------------------- instruments


INST_KEY = subject_key("instrument", "bancolombia_exterior")


def test_obtainable_instrument_needs_an_official_card_country_claim():
    must = pair(PAIR_CO, ("payment_card_country", "must_match"))
    no_cards = load_payment_instruments().model_copy(deep=True)
    no_cards.instruments = [i for i in no_cards.instruments if i.id != "user_co_card"]
    bare = Profile(**(CO_USER.model_dump() | {"instruments_held": []}))
    assert assess("netflix", "CO", bare, ctx(_svc_breach(), must, instruments=no_cards)).rank == 4

    inst = subject(INST_KEY, claim(INST_KEY, "card_issuing_country", "CO"))
    a = assess("netflix", "CO", bare, ctx(_svc_breach(), must, inst, instruments=no_cards))
    assert a.practical == "remote" and a.rank == 2
    assert [s.type for s in a.steps] == ["obtain_instrument", "payment_country"]
    assert any(c.startswith("instrument:bancolombia_exterior") for c in a.claim_ids)

    # Bancolombia needs a Colombian ID: the generic profile cannot open it.
    g = assess("netflix", "CO", GENERIC, ctx(_svc_breach(), must, inst, instruments=no_cards))
    assert g.rank == 4

    # Press cannot establish an instrument fact.
    press = subject(INST_KEY, claim(INST_KEY, "card_issuing_country", "CO", cls="press"))
    assert (
        assess("netflix", "CO", bare, ctx(_svc_breach(), must, press, instruments=no_cards)).rank
        == 4
    )


def test_observed_bin_wins_over_claims():
    insts = load_payment_instruments().model_copy(deep=True)
    for i in insts.instruments:
        if i.id == "revolut_eea":
            i.card_bin_country_observed = ObservedBin(value="LT", sample_size=1)
    key = subject_key("instrument", "revolut_eea")
    inst = subject(key, claim(key, "card_issuing_country", "IE"))
    pair_lt = subject_key("pair", "icloud_plus", "LT")
    svc = subject_key("service", "icloud_plus")
    facts = (
        subject(svc, claim(svc, "residence_clause", "must_reside")),
        pair(pair_lt, ("payment_card_country", "must_match")),
        inst,
    )
    a = assess("icloud_plus", "LT", GENERIC, ctx(*facts, instruments=insts))
    assert a.practical == "remote" and "obtain_instrument" in {s.type for s in a.steps}
    # Not decisive: the observed BIN was used, not the IE claim.
    assert not any(c.startswith("instrument:") for c in a.claim_ids)


# ---------------------------------------------------------------- legality


def test_eu_right_for_eservices_between_eea_countries():
    svc = subject_key("service", "icloud_plus")
    lt = subject_key("pair", "icloud_plus", "LT")
    facts = (
        subject(svc, claim(svc, "residence_clause", "must_reside")),
        pair(lt, ("payment_card_country", "any_country")),
    )
    a = assess("icloud_plus", "LT", GENERIC, ctx(*facts))
    assert a.legality == "contestable_eu_right" and a.eu_badge
    assert a.legal_basis[0].article == "Art. 4(1)(b)" and not a.legal_basis[0].tested
    assert a.rank == 2
    # Audiovisual services are excluded from the regulation.
    nf = (
        subject(SVC, claim(SVC, "residence_clause", "must_reside")),
        pair(subject_key("pair", "netflix", "LT"), ("payment_card_country", "any_country")),
    )
    assert assess("netflix", "LT", GENERIC, ctx(*nf)).legality == "lawful"
    # Outside the EEA: lawful, no badge.
    co = (
        subject(svc, claim(svc, "residence_clause", "must_reside")),
        pair(subject_key("pair", "icloud_plus", "CO"), ("payment_card_country", "any_country")),
    )
    b = assess("icloud_plus", "CO", GENERIC, ctx(*co))
    assert b.legality == "lawful" and not b.eu_badge


# --------------------------------------------------------------- provenance


def test_provenance_follows_review_decisions():
    svc, p = _svc_breach(), pair(PAIR_CO, ("payment_card_country", "any_country"))
    none = ReviewFile()
    assert assess("netflix", "CO", CO_USER, ctx(svc, p, review=none)).provenance == "none"
    pending = assess("netflix", "CO", CO_USER, ctx(svc, p, review=none), include_pending=True)
    assert pending.provenance == "agent_pending" and pending.status == "unverified"
    reviewed = assess("netflix", "CO", CO_USER, ctx(svc, p))
    assert reviewed.provenance == "agent_reviewed" and reviewed.rank == 2

    # A rejected decisive claim drops out; a decision for an older hash does not count.
    clause = svc.claims[0]
    stale = accept_all(p).model_copy(deep=True)
    stale.decisions.append(
        ReviewDecision(id=clause.id, hash="0" * 12, decision="accept", decided_on=TODAY)
    )
    assert assess("netflix", "CO", CO_USER, ctx(svc, p, review=stale)).tos == "unknown"
    rejected = accept_all(svc, p).model_copy(deep=True)
    rejected.decisions.append(
        ReviewDecision(id=clause.id, hash=clause.hash, decision="reject", decided_on=TODAY)
    )
    a = assess("netflix", "CO", CO_USER, ctx(svc, p, review=rejected), include_pending=True)
    assert a.tos == "unknown"


def test_evidence_lists_decisive_claims_with_quotes():
    a = assess(
        "netflix",
        "CO",
        CO_USER,
        ctx(_svc_breach(), pair(PAIR_CO, ("payment_card_country", "must_match"))),
    )
    assert a.evidence and all(e.verified and e.quote for e in a.evidence)
    assert {e.kind for e in a.evidence} == {"official"}
    assert a.researched_on == TODAY


# ------------------------------------------------------------- compare hook


def _offer(cc: str, amount: str, currency: str) -> PublishedPrice:
    return PublishedPrice.model_validate(
        {
            "obs_key": f"netflix:standard:{cc}:web",
            "service_id": "netflix",
            "plan_id": "standard",
            "country": cc,
            "channel": "web",
            "currency": currency,
            "amount": amount,
            "billing_period": "month",
            "monthly_equiv": amount,
            "plan_label_raw": "Standard",
            "vat_included": True,
            "source_url": "https://help.netflix.com/node/24926",
            "source_kind": "official",
            "adapter_id": "netflix_help",
            "extraction": "parser",
            "confidence": 1.0,
            "status": "accepted",
            "flags": [],
            "first_seen": "2026-10-01",
            "last_changed": "2026-10-01",
        }
    )


def test_compare_uses_research_verdicts():
    from haggler.models.fx import FxRate, FxTable

    fx = FxTable(
        as_of=TODAY,
        rates={
            "SEK": FxRate(currency="SEK", per_eur="11", source="ecb", as_of=TODAY),
            "COP": FxRate(currency="COP", per_eur="4500", source="ecb", as_of=TODAY),
        },
    )
    offers = [_offer("SE", "169", "SEK"), _offer("CO", "26900", "COP")]
    plain = cheapest(offers, fx=fx, catalog=CAT, feasibility=EMPTY_FEAS)
    assert plain[0].best_by_risk[3].country == "SE"  # CO unknown: never chosen
    c = ctx(_svc_breach(), pair(PAIR_CO, ("payment_card_country", "must_match")))
    rows = cheapest(
        offers, fx=fx, catalog=CAT, feasibility=EMPTY_FEAS, verdicts=verdict_fn(CO_USER, c)
    )
    assert rows[0].best_by_risk[1].country == "SE"
    assert rows[0].best_by_risk[2].country == "CO"
    generic = cheapest(
        offers, fx=fx, catalog=CAT, feasibility=EMPTY_FEAS, verdicts=verdict_fn(GENERIC, c)
    )
    assert generic[0].best_by_risk[3].country == "SE"  # not feasible without a CO card


def test_evaluate_does_not_import_adk():
    code = "import sys, haggler.research.evaluate; print('google.adk' in sys.modules)"
    out = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, check=True)
    assert out.stdout.strip() == "False"


# ------------------------------------------------------- review findings (regressions)


def test_instrument_facts_without_claims_are_assumptions():
    """Rank 0 must not rest on an instrument the profile might not be able to open."""
    key = subject_key("instrument", "revolut_eea")
    lt = subject_key("pair", "netflix", "LT")
    svc = subject(SVC, claim(SVC, "foreign_offer_allowed", "true"))
    facts = (svc, pair(lt, ("payment_card_country", "must_match")))
    inst = subject(key, claim(key, "card_issuing_country", "LT"))
    a = assess("netflix", "LT", GENERIC, ctx(*facts, inst))
    assert a.rank == 99 and any("Revolut" in x for x in a.assumptions_es)
    full = subject(
        key,
        claim(key, "card_issuing_country", "LT"),
        claim(key, "id_documents", "eea_id"),
        claim(key, "requires_local_address", "false"),
    )
    b = assess("netflix", "LT", GENERIC, ctx(*facts, full))
    assert (b.rank, b.label) == (0, "official_home") and not b.assumptions_es


@pytest.mark.parametrize(
    ("field", "value"),
    [("onboarding_remote", "false"), ("eligible_residence", "LT")],
)
def test_official_negative_instrument_claims_win_over_curated(field, value):
    key = subject_key("instrument", "revolut_eea")
    lt = subject_key("pair", "netflix", "LT")
    facts = (_svc_breach(), pair(lt, ("payment_card_country", "must_match")))
    inst = subject(key, claim(key, "card_issuing_country", "LT"), claim(key, field, value))
    assert assess("netflix", "LT", GENERIC, ctx(*facts, inst)).practical == "impossible"


def test_old_enforcement_events_still_show_a_breach():
    svc = subject(
        SVC,
        claim(SVC, "foreign_offer_allowed", "true"),
        claim(SVC, "enforcement_event", "cancellation", cls="press", event=date(2020, 1, 1)),
    )
    a = assess("netflix", "CO", CO_USER, ctx(svc, pair(PAIR_CO)))
    assert a.tos == "breach_unenforced" and a.rank >= 2


def test_claims_not_confirmed_for_a_year_are_ignored():
    old = claim(SVC, "residence_clause", "must_reside")
    old = old.model_copy(
        update={
            "check": old.check.model_copy(update={"retrieved_at": datetime(2025, 6, 1, tzinfo=UTC)})
        }
    )
    svc = subject(SVC, old)  # researched today: the subject itself is fresh
    assert assess("netflix", "CO", CO_USER, ctx(svc, pair(PAIR_CO))).tos == "unknown"


def test_conflicting_card_countries_are_unknown():
    key = subject_key("instrument", "revolut_eea")
    ie = subject_key("pair", "netflix", "IE")
    inst = subject(
        key,
        claim(key, "card_issuing_country", "IE"),
        claim(key, "card_issuing_country", "LT"),
        claim(key, "id_documents", "eea_id"),
        claim(key, "requires_local_address", "false"),
    )
    facts = (_svc_breach(), pair(ie, ("payment_card_country", "must_match")), inst)
    assert assess("netflix", "IE", GENERIC, ctx(*facts)).practical == "impossible"

"""haggler.profile_rules: walking a pair's decision tree with a profile's capabilities gives
exactly the verdict the evaluator gives that profile."""

from __future__ import annotations

import itertools
from datetime import UTC, date, datetime

import pytest

from haggler.bundles.evaluate import BundleAssessment, assess_bundle
from haggler.catalog import (
    load_bundles,
    load_catalog,
    load_feasibility,
    load_payment_instruments,
    load_profiles,
)
from haggler.models.curated import FeasibilityFile
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
from haggler.profile_rules import SymbolicProfile, evaluate_tree, explore, matches
from haggler.publish import profile_capabilities
from haggler.research.evaluate import EEA_COUNTRIES, Assessment, EvalContext, assess

TODAY = date(2026, 10, 9)
CAT = load_catalog()
EEA = EEA_COUNTRIES | {c.cc for c in CAT.countries if c.eu_eea}


def claim(subject: str, field: str, value: str) -> Claim:
    quote = f"Quote for {field} {value}."
    url = "https://help.example.com/terms"
    return Claim(
        id=claim_id(subject, field, value, None),
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


def subject(key: str, *facts: tuple[str, str]) -> SubjectFacts:
    return SubjectFacts(
        key=key,
        kind=key.split(":", 1)[0],  # type: ignore[arg-type]
        researched_on=TODAY,
        run_id="r1",
        status="researched",
        claims=[claim(key, f, v) for f, v in facts],
    )


SVC = subject_key("service", "netflix")
SUBJECTS = [
    subject(SVC, ("residence_clause", "must_reside"), ("account_country_basis", "signup_location")),
    subject(
        subject_key("pair", "netflix", "CO"),
        ("payment_card_country", "must_match"),
        ("signup_location", "checked"),
        ("local_id_required", "true"),
        ("local_phone_required", "false"),
        ("billing_address", "required"),
    ),
    subject(
        subject_key("pair", "netflix", "LT"),
        ("payment_card_country", "must_match"),
        ("periodic_location_check", "true"),
        ("local_phone_required", "true"),
    ),
    subject(
        subject_key("pair", "netflix", "TR"),
        ("payment_card_country", "any_country"),
        ("signup_location", "not_checked"),
        ("billing_address", "not_required"),
    ),
    subject(subject_key("instrument", "revolut_eea"), ("card_issuing_country", "LT")),
    subject(subject_key("instrument", "bancolombia_exterior"), ("card_issuing_country", "CO")),
    subject(subject_key("instrument", "wise_eea"), ("id_documents", "eea_id")),
]
PENDING = {SUBJECTS[2].claims[1].id}  # LT periodic check stays pending review


def context(feasibility: FeasibilityFile) -> EvalContext:
    return EvalContext.build(
        catalog=CAT,
        feasibility=feasibility,
        instruments=load_payment_instruments(),
        subjects=SUBJECTS,
        review=ReviewFile(
            decisions=[
                ReviewDecision(id=c.id, hash=c.hash, decision="accept", decided_on=TODAY)
                for s in SUBJECTS
                for c in s.claims
                if c.id not in PENDING
            ]
        ),
        today=TODAY,
    )


def leaf(a: Assessment) -> tuple[str, int, bool, str]:
    return (a.label, a.rank, a.eu_badge, a.provenance)


def profiles() -> list[Profile]:
    """Combinations of residence and ties with CO / LT / TR."""
    out = []
    ties = ("CO", "LT", "TR")
    for residence in ("SE", "CO", "US"):
        others = [cc for cc in ties if cc != residence]
        for docs, phones, cards, presence in itertools.product(
            ([], others[:1], others),
            ([], others[-1:]),
            ([], others[:1]),
            ({}, {others[0]: "once"}, {others[-1]: "periodic"}),
        ):
            out.append(
                Profile(
                    id=f"p{len(out)}",
                    name_es="p",
                    residence_country=residence,
                    id_document_countries=[residence, *docs],
                    phone_countries=[residence, *phones],
                    card_countries=cards,
                    presence=presence,
                )
            )
    return out


@pytest.mark.parametrize("include_pending", [False, True])
def test_tree_matches_assess_for_every_profile(include_pending: bool) -> None:
    feasibility = load_feasibility()
    ctx = context(feasibility)
    presets = {p.id: profile_capabilities(p, ctx) for p in load_profiles().profiles}
    candidates = [*load_profiles().profiles, *profiles()]
    pairs = [("netflix", cc) for cc in ("CO", "LT", "TR", "SE", "US")]
    pairs += sorted({(r.service_id, r.target_country) for r in feasibility.records})
    checked = 0
    for service_id, cc in pairs:
        tree = explore(
            lambda p, s=service_id, c=cc: assess(s, c, p, ctx, include_pending=include_pending),
            leaf,
        )
        for profile in candidates:
            caps = profile_capabilities(profile, ctx)
            # The site treats a profile with a preset's capabilities as that preset.
            same = next((pid for pid, pc in presets.items() if matches(caps, pc)), None)
            if same is not None:
                profile = profile.model_copy(update={"id": same})
            expected = leaf(assess(service_id, cc, profile, ctx, include_pending=include_pending))
            assert tuple(evaluate_tree(tree, caps, eea=EEA, presets=presets)) == expected, (
                service_id,
                cc,
                caps,
            )
            checked += 1
    assert checked > 1000


def test_symbolic_profile_asks_and_trees_collapse() -> None:
    def run(p: SymbolicProfile) -> str:
        if p.lives_in("CO"):
            return "home"
        return "doc" if p.has_document("CO") or True else "never"

    tree = explore(run, lambda r: r)
    assert tree == {
        "ask": ["lives", "CO"],
        "answers": {"true": {"verdict": "home"}, "false": {"verdict": "doc"}},
    }
    assert explore(lambda p: p.presence_in("CO") or "none", lambda r: r)["answers"].keys() == {
        "none",
        "once",
        "periodic",
    }


def test_preset_capabilities_count_held_cards() -> None:
    ctx = context(load_feasibility())
    co = load_profiles().profile("se_resident_co_citizen")
    caps = profile_capabilities(co, ctx)
    assert caps["card_countries"] == ["CO"]
    assert caps["address_countries"] == ["SE"]
    assert caps["presence"] == {"CO": "once"}


def test_published_instruments_leave_out_personal_ones() -> None:
    from haggler.publish import _instruments_json

    items = {i["id"]: i for i in _instruments_json(context(load_feasibility()))}
    assert "user_co_card" not in items
    assert items["revolut_eea"]["eligible_residence"] == ["EEA"]
    assert items["bancolombia_exterior"]["required_id_countries"] == ["CO"]
    assert items["wise_eea"]["url"] == "https://wise.com/help/articles/2968915"
    assert items["bunq"]["url"] == "https://bunq.com/"


def test_leaf_names_the_card_to_open_online() -> None:
    from haggler.models.curated import FeasibilityStep
    from haggler.publish import _verdict_leaf

    base = dict(
        service_id="spotify",
        target_country="LT",
        residence_country="SE",
        profile="custom",
        legality="lawful",
        tos="breach_enforced",
        practical="remote",
        rank=3,
        label="tos_breach_enforced",
        eu_badge=False,
        confidence="low",
        provenance="template",
    )
    steps = (
        FeasibilityStep(type="obtain_instrument", value="paysera"),
        FeasibilityStep(type="payment_country", value="LT"),
    )
    assert _verdict_leaf(Assessment(**base, steps=steps))["card_via"] == "paysera"
    assert "card_via" not in _verdict_leaf(Assessment(**base, steps=steps[1:]))


@pytest.mark.parametrize("include_pending", [False, True])
def test_bundle_tree_matches_assess_bundle_for_every_profile(include_pending: bool) -> None:
    feasibility = load_feasibility()
    ctx = context(feasibility)
    presets = {p.id: profile_capabilities(p, ctx) for p in load_profiles().profiles}
    candidates = [*load_profiles().profiles, *profiles()]

    def bundle_leaf(a: BundleAssessment | None) -> tuple[str, int, bool, str] | None:
        return None if a is None else (a.label, a.rank, a.eu_badge, a.provenance)

    checked = 0
    for bundle in load_bundles().bundles:
        tree = explore(
            lambda p, b=bundle: assess_bundle(b, p, ctx, include_pending=include_pending),
            bundle_leaf,
        )
        for profile in candidates:
            caps = profile_capabilities(profile, ctx)
            same = next((pid for pid, pc in presets.items() if matches(caps, pc)), None)
            if same is not None:
                profile = profile.model_copy(update={"id": same})
            expected = bundle_leaf(
                assess_bundle(bundle, profile, ctx, include_pending=include_pending)
            )
            got = evaluate_tree(tree, caps, eea=EEA, presets=presets)
            assert (None if got is None else tuple(got)) == expected, (bundle.bundle_id, caps)
            checked += 1
    assert checked > 100

"""Feasibility labels: worst axis wins, false declarations force >= 2, home and unknown cases."""

from __future__ import annotations

from datetime import date

import pytest

from haggler.feasibility import (
    FALSE_DECLARATION_MIN_RANK,
    LABEL_INFO,
    LABEL_RANKS,
    LABELS,
    LEGALITY_RANK,
    PRACTICAL_RANK,
    TOS_RANK,
    UNKNOWN_LABEL,
    UNKNOWN_RANK,
    eu_badge,
    record_rank,
    verdict,
)
from haggler.models.curated import FeasibilityFile, FeasibilityRecord, FeasibilityStep, LegalBasis

PROFILE = "se_resident_co_citizen"


def _record(
    legality: str = "lawful",
    tos: str = "compliant",
    practical: str = "remote",
    *,
    false_declaration: bool = False,
    strengths: tuple[str, ...] = (),
    service_id: str = "netflix",
    target: str = "CO",
    residence: str = "SE",
    profile: str = PROFILE,
) -> FeasibilityRecord:
    steps = [FeasibilityStep(type="payment_country", value=target)]
    if false_declaration:
        steps.append(
            FeasibilityStep(type="billing_address", value=target, requires_false_declaration=True)
        )
    return FeasibilityRecord(
        id=f"{service_id}_{target.lower()}_from_{residence.lower()}",
        service_id=service_id,
        target_country=target,
        residence_country=residence,
        profile=profile,
        steps=steps,
        legality=legality,
        tos=tos,
        practical=practical,
        legal_basis=[
            LegalBasis(instrument="Reglamento (UE) 2018/302", article="Art. 4", strength=s)
            for s in strengths
        ],
        last_verified=date(2026, 10, 8),
    )


def _file(*records: FeasibilityRecord) -> FeasibilityFile:
    return FeasibilityFile(profiles={PROFILE: "test"}, records=list(records))


LOW = "tos_breach_low_enforcement"
ENFORCED = "tos_breach_enforced"
EU = "eu_right_untested"
MISREP = "requires_misrepresentation"
EU_RIGHT = "contestable_eu_right"

# (legality, tos, practical, requires_false_declaration) -> (rank, label)
TRUTH_TABLE = [
    (("lawful", "compliant", "remote", False), (0, "official_home")),
    (("lawful", "compliant", "remote", True), (2, LOW)),
    ((EU_RIGHT, "compliant", "remote", False), (1, EU)),
    ((EU_RIGHT, "compliant", "remote", True), (2, LOW)),
    (("lawful", "compliant", "presence_once", False), (1, EU)),
    (("lawful", "compliant", "presence_periodic", False), (2, LOW)),
    ((MISREP, "compliant", "remote", False), (2, LOW)),
    (("lawful", "breach_unenforced", "remote", False), (2, LOW)),
    ((EU_RIGHT, "breach_unenforced", "presence_once", False), (2, LOW)),
    (("lawful", "breach_enforced", "remote", False), (3, ENFORCED)),
    (("lawful", "breach_enforced", "remote", True), (3, ENFORCED)),
    ((MISREP, "breach_enforced", "presence_once", True), (3, ENFORCED)),
    (("lawful", "compliant", "impossible", False), (4, "not_feasible")),
    ((MISREP, "breach_enforced", "impossible", True), (4, "not_feasible")),
]


@pytest.mark.parametrize(("axes", "expected"), TRUTH_TABLE)
def test_truth_table(axes, expected):
    legality, tos, practical, false_decl = axes
    rec = _record(legality, tos, practical, false_declaration=false_decl)
    assert record_rank(rec) == expected[0]
    v = verdict("netflix", "CO", residence_country="SE", profile=PROFILE, feasibility=_file(rec))
    assert (v.rank, v.label) == expected
    assert v.record_id == rec.id


def test_rank_is_the_worst_axis_for_every_combination():
    for legality, lr in LEGALITY_RANK.items():
        for tos, tr in TOS_RANK.items():
            for practical, pr in PRACTICAL_RANK.items():
                for false_decl in (False, True):
                    rec = _record(legality, tos, practical, false_declaration=false_decl)
                    expected = max(lr, tr, pr)
                    if false_decl:
                        expected = max(expected, FALSE_DECLARATION_MIN_RANK)
                    assert record_rank(rec) == expected
                    assert 0 <= expected < len(LABELS)


def test_axis_tables_cover_the_model_literals():
    fields = FeasibilityRecord.model_fields
    assert set(LEGALITY_RANK) == set(fields["legality"].annotation.__args__)
    assert set(TOS_RANK) == set(fields["tos"].annotation.__args__)
    assert set(PRACTICAL_RANK) == set(fields["practical"].annotation.__args__)


def test_residence_equals_target_is_official_home_without_record():
    # Even a record that says "breach_enforced" for SE->SE is ignored: home is never a breach.
    rec = _record(tos="breach_enforced", target="SE")
    v = verdict("netflix", "SE", residence_country="SE", profile=PROFILE, feasibility=_file(rec))
    assert (v.label, v.rank, v.eu_badge, v.record_id) == ("official_home", 0, False, None)


def test_missing_record_is_unknown():
    v = verdict("netflix", "TR", residence_country="SE", profile=PROFILE, feasibility=_file())
    assert (v.label, v.rank, v.eu_badge, v.record_id) == (UNKNOWN_LABEL, UNKNOWN_RANK, False, None)


@pytest.mark.parametrize(
    ("service_id", "target", "residence", "profile"),
    [
        ("spotify", "CO", "SE", PROFILE),  # other service
        ("netflix", "MX", "SE", PROFILE),  # other target
        ("netflix", "CO", "NO", PROFILE),  # other residence
        ("netflix", "CO", "SE", "other_profile"),  # other profile
    ],
)
def test_record_must_match_all_four_fields(service_id, target, residence, profile):
    feas = _file(_record(tos="breach_unenforced"))
    v = verdict(service_id, target, residence_country=residence, profile=profile, feasibility=feas)
    assert v.label == UNKNOWN_LABEL


@pytest.mark.parametrize(
    ("strengths", "badge"),
    [
        ((), False),
        (("none",), False),
        (("arguable",), True),
        (("textual",), True),
        (("none", "arguable"), True),
    ],
)
def test_eu_badge(strengths, badge):
    rec = _record("contestable_eu_right", "breach_unenforced", strengths=strengths, target="LT")
    assert eu_badge(rec) is badge
    v = verdict("netflix", "LT", residence_country="SE", profile=PROFILE, feasibility=_file(rec))
    assert v.eu_badge is badge
    # The badge is secondary: it never changes the label.
    assert v.label == "tos_breach_low_enforcement"


@pytest.mark.parametrize(
    ("service_id", "target", "label", "badge"),
    [
        ("netflix", "CO", "tos_breach_low_enforcement", False),
        ("youtube_premium", "CO", "tos_breach_enforced", False),
        ("youtube_premium", "US", "not_feasible", False),
        ("spotify", "CO", "tos_breach_enforced", False),
        ("disney_plus", "CO", "tos_breach_low_enforcement", False),
        ("hbo_max", "CO", "tos_breach_low_enforcement", False),
        ("icloud_plus", "CO", "tos_breach_low_enforcement", False),
        ("icloud_plus", "LT", "tos_breach_low_enforcement", True),
        ("google_one", "LT", "tos_breach_low_enforcement", True),
        ("apple_one", "CO", "tos_breach_low_enforcement", False),
        ("microsoft_365", "CO", "tos_breach_low_enforcement", False),
        ("netflix", "LT", UNKNOWN_LABEL, False),
        ("spotify", "SE", "official_home", False),
    ],
)
def test_curated_seeds(feasibility, service_id, target, label, badge):
    v = verdict(
        service_id, target, residence_country="SE", profile=PROFILE, feasibility=feasibility
    )
    assert (v.label, v.eu_badge) == (label, badge)
    assert v.rank == LABEL_RANKS[label]


def test_every_curated_record_has_a_label(feasibility):
    for rec in feasibility.records:
        assert 0 <= record_rank(rec) < len(LABELS), rec.id


def test_label_info_is_complete_spanish_text():
    assert set(LABEL_INFO) == {*LABELS, UNKNOWN_LABEL}
    assert set(LABEL_RANKS) == set(LABEL_INFO)
    assert LABEL_INFO["official_home"][0] == "Oferta oficial de tu país"
    for name, description in LABEL_INFO.values():
        assert name.strip() and description.strip()
        assert len(description) > len(name)
    assert [LABEL_RANKS[label] for label in LABELS] == [0, 1, 2, 3, 4]
    assert LABEL_RANKS[UNKNOWN_LABEL] == UNKNOWN_RANK

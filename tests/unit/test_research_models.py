"""Profiles, the claim vocabulary and the research fact models."""

from __future__ import annotations

from datetime import date
from pathlib import Path

import pytest
from pydantic import ValidationError

from haggler.catalog import (
    integrity_errors,
    load_catalog,
    load_feasibility,
    load_payment_instruments,
    load_profiles,
    load_review,
    synthesize_profiles,
)
from haggler.models.profiles import Profile, ProfilesFile
from haggler.models.research import (
    ReviewDecision,
    ReviewFile,
    claim_hash,
    claim_id,
    subject_key,
)
from haggler.research import fields as F


def _profile(**kw) -> Profile:
    base = dict(id="p", name_es="P", residence_country="SE")
    return Profile(**(base | kw))


def test_profiles_file_requires_a_defined_default():
    with pytest.raises(ValidationError):
        ProfilesFile(default="missing", profiles=[_profile()])
    with pytest.raises(ValidationError):
        ProfilesFile(default="p", profiles=[_profile(), _profile()])


def test_public_profiles_put_the_default_first():
    pf = ProfilesFile(
        default="z",
        profiles=[_profile(id="a"), _profile(id="hidden", public=False), _profile(id="z")],
    )
    assert [p.id for p in pf.public()] == ["z", "a"]


def test_residence_is_always_a_real_address():
    assert _profile(address_countries=[]).addresses() == {"SE"}


def test_repo_profiles():
    pf = load_profiles()
    assert pf is not None and pf.default == "se_resident_co_citizen"
    me = pf.profile("se_resident_co_citizen")
    assert me.instruments_held == ["user_co_card"] and me.presence == {"CO": "once"}
    assert {p.id for p in pf.public()} == {"se_resident_co_citizen", "se_resident_eu_generic"}


def test_missing_profiles_file(tmp_path: Path):
    assert load_profiles(tmp_path) is None
    assert load_review(tmp_path) == ReviewFile()
    pf = synthesize_profiles(load_feasibility(), residence="SE", profile="se_resident_co_citizen")
    p = pf.profile("se_resident_co_citizen")
    assert p.residence_country == "SE" and p.instruments_held == [] and p.presence == {}
    assert "Suecia" in p.description_es


def test_integrity_catches_profile_mismatches():
    cat = load_catalog()
    feas = load_feasibility()
    pf = ProfilesFile(
        default="se_resident_co_citizen",
        profiles=[
            _profile(
                id="se_resident_co_citizen",
                residence_country="NO",
                instruments_held=["nope"],
                presence={"XX": "once"},
            )
        ],
    )
    errors = integrity_errors(cat, feas, None, load_payment_instruments(), pf)
    assert any("unknown instrument nope" in e for e in errors)
    assert any("unknown country XX" in e for e in errors)
    assert any("differs from profile" in e for e in errors)


def test_integrity_catches_duplicate_review_lines():
    d = ReviewDecision(id="x", hash="0123456789ab", decision="accept", on=date(2026, 10, 9))
    errors = integrity_errors(load_catalog(), review=ReviewFile(decisions=[d, d]))
    assert errors == ["research_review: duplicate decision accept x"]


def test_review_latest_entry_wins():
    a = ReviewDecision(id="x", hash="0123456789ab", decision="accept", on=date(2026, 10, 9))
    r = a.model_copy(update={"decision": "reject", "on": date(2026, 10, 10)})
    other = a.model_copy(update={"hash": "ba9876543210"})
    latest = ReviewFile(decisions=[a, r, other]).latest()
    assert latest[("x", "0123456789ab")].decision == "reject"
    assert latest[("x", "ba9876543210")].decision == "accept"


def test_claim_ids_are_stable_and_hashes_follow_the_evidence():
    key = subject_key("service", "netflix")
    assert key == "service:netflix"
    assert claim_id(key, "residence_clause", "must_reside") == (
        "service:netflix:residence_clause:must_reside"
    )
    assert claim_id(key, "enforcement_event", "cancellation", date(2024, 6, 1)).endswith(
        ":cancellation:2024-06-01"
    )
    h = claim_hash("residence_clause", "must_reside", None, "Only  residents", "https://x/a")
    # Whitespace and case in the quote do not change the hash; the URL and the value do.
    assert h == claim_hash("residence_clause", "must_reside", None, "only residents", "https://x/a")
    assert h != claim_hash("residence_clause", "must_reside", None, "only residents", "https://x/b")
    assert h != claim_hash("residence_clause", "x", None, "only residents", "https://x/a")
    assert len(h) == 12


@pytest.mark.parametrize(
    ("field", "raw", "expected"),
    [
        ("residence_clause", " Must_Reside ", "must_reside"),
        ("residence_clause", "maybe", None),
        ("available", "TRUE", "true"),
        ("region_change_cooldown_days", "365", "365"),
        ("region_change_cooldown_days", "a year", None),
        ("card_issuing_country", "lt", "LT"),
        ("card_issuing_country", "Lithuania", None),
        ("eligible_residence", "eea", "EEA"),
        ("iban_country", "EEA", None),
        ("issuer_entity", "  Revolut   Bank UAB ", "Revolut Bank UAB"),
        ("issuer_entity", "x" * 200, None),
    ],
)
def test_normalize_value(field: str, raw: str, expected: str | None):
    assert F.normalize_value(F.FIELD_SPECS[field], raw) == expected


def test_vocabulary_is_closed_and_ordered():
    assert set(F.FIELDS_BY_LEVEL) == {"service", "pair", "instrument"}
    assert sum(len(v) for v in F.FIELDS_BY_LEVEL.values()) == len(F.FIELDS)
    for spec in F.FIELDS:
        if spec.value_type in ("enum", "bool"):
            assert spec.values and set(spec.value_labels_es) == set(spec.values), spec.name
    spec = F.FIELD_SPECS["residence_clause"]
    assert F.severity(spec, "must_reside") > F.severity(spec, "primary_use_in_account_country")
    prompt = F.vocabulary_prompt("service")
    assert "enforcement_event:" in prompt and "event_date required" in prompt
    assert "card_issuing_country" not in prompt

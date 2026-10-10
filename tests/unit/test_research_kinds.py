"""The research subject kind registry (research.kinds) and the constants derived from it."""

from __future__ import annotations

import dataclasses
import re
from datetime import date, timedelta
from pathlib import Path
from types import SimpleNamespace
from typing import get_args

import pytest

import haggler
from haggler.catalog import load_catalog, load_payment_instruments
from haggler.models import research as models
from haggler.models.curated import FeasibilityFile
from haggler.models.profiles import Profile
from haggler.models.research import Claim, SourceCheck, SubjectFacts
from haggler.publish import _profile_assessments
from haggler.research import agents, cli, fields, io, kinds, nodes, plan
from haggler.research.evaluate import EvalContext
from haggler.research.kinds import KINDS, RUN_ORDER, SubjectRef, kind_of, parse_key
from haggler.research.review import select_claims

SAMPLE_IDS = {"service": ("netflix",), "pair": ("netflix", "CO"), "instrument": ("revolut_eea",)}


# ------------------------------------------------------------------ registry


def test_one_subject_kind_definition():
    assert models.SubjectKind is kinds.SubjectKind
    assert fields.SubjectKind is kinds.SubjectKind
    src = Path(haggler.__file__).parent
    defining = sorted(
        str(p.relative_to(src))
        for p in src.rglob("*.py")
        if re.search(r"^SubjectKind\s*=", p.read_text(encoding="utf-8"), re.MULTILINE)
    )
    assert defining == ["research/kinds.py"]


def test_registry_matches_subject_kind():
    assert tuple(KINDS) == get_args(kinds.SubjectKind) == ("service", "pair", "instrument")
    for name, spec in KINDS.items():
        assert spec.name == name
        assert spec.prefix == f"{name}:"
        assert spec.field_level == name


def test_every_kind_has_a_file_window_agent_fields_and_instructions():
    planned = {f.name for f in dataclasses.fields(plan.PlannedSubject)}
    for name, spec in KINDS.items():
        assert spec.data_file.endswith(".json") and spec.data_file != "state.json"
        assert spec.window_days > 0
        assert spec.agent_name.endswith("_researcher")
        assert fields.FIELDS_BY_LEVEL[name], name
        assert name in agents.INSTRUCTIONS
        assert "{vocabulary}" in agents.INSTRUCTIONS[name]
        assert "{rules}" in agents.INSTRUCTIONS[name]
        assert spec.parts and set(spec.parts) <= planned, name
    assert set(agents.INSTRUCTIONS) == set(KINDS)
    assert {f.level for f in fields.FIELDS} == set(KINDS)
    assert len({s.data_file for s in KINDS.values()}) == len(KINDS)
    assert len({s.agent_name for s in KINDS.values()}) == len(KINDS)


def test_run_order():
    assert sorted(RUN_ORDER) == sorted(KINDS)
    assert [KINDS[k].run_order for k in RUN_ORDER] == sorted(s.run_order for s in KINDS.values())
    assert RUN_ORDER == ("pair", "service", "instrument")


@pytest.mark.parametrize(
    ("change", "message", "at"),
    [
        ({"name": "bundle"}, "do not match SubjectKind", 0),
        ({"data_file": "pairs.json"}, "share a data_file", 0),
        ({"agent_name": "pair_researcher"}, "share a agent_name", 0),
        ({"run_order": 0}, "share a run_order", 0),
        ({"parts": ()}, "needs id parts", 0),
        ({"window_days": 0}, "needs id parts", 0),
        ({"data_file": "state.json"}, "state.json", 0),
        ({"pair_scope": "self"}, "pair_scope self needs", 0),
        ({"parts": ("owner_id",)}, "pair_scope service needs", 0),
        ({"parts": ("country", "service_id")}, "pair_scope self needs", 1),
        ({"pair_scope": "service"}, "pair_scope service needs", 2),
    ],
)
def test_check_rejects_an_inconsistent_registry(change: dict, message: str, at: int):
    specs = list(KINDS.values())
    specs[at] = dataclasses.replace(specs[at], **change)
    with pytest.raises(ValueError, match=message):
        kinds._check(tuple(specs))


# ----------------------------------------------------------------------- keys


@pytest.mark.parametrize("kind", list(KINDS))
def test_keys_round_trip(kind: str):
    ids = SAMPLE_IDS[kind]
    key = KINDS[kind].key(*ids)
    assert key == ":".join((kind, *ids))
    assert models.subject_key(kind, *ids) == kinds.subject_key(kind, *ids) == key
    assert kind_of(key) == kind
    ref = parse_key(key)
    assert ref == SubjectRef(kind, ids) == KINDS[kind].parse(key)
    assert ref.key == key
    assert ref.owner == ids[0]


def test_pinned_keys():
    assert models.subject_key("service", "netflix") == "service:netflix"
    assert models.subject_key("pair", "netflix", "CO") == "pair:netflix:CO"
    assert models.subject_key("instrument", "revolut_eea") == "instrument:revolut_eea"


@pytest.mark.parametrize(
    "key",
    [
        "bundle:meli_plus",
        "pair:netflix",
        "pair:netflix:CO:x",
        "service",
        "service:a:b",
        "pairs:a:b",
    ],
)
def test_keys_of_an_unknown_kind_or_the_wrong_arity_do_not_parse(key: str):
    assert parse_key(key) is None


def test_parse_keeps_empty_ids_as_the_readers_always_did():
    # publish._profile_assessments and the review CLI read "pair:<a>:<b>" keys by prefix and
    # number of ids only.
    assert parse_key("pair::CO") == SubjectRef("pair", ("", "CO"))
    assert parse_key("pair:netflix:") == SubjectRef("pair", ("netflix", ""))
    assert parse_key("service:") == SubjectRef("service", ("",))


def test_key_builder_joins_unchecked_ids_like_before():
    # Pinned to the pre-registry ":".join((kind, *parts)): a malformed id makes a malformed key,
    # never an exception (site-data and the evaluator build keys from stored rows and ids).
    assert models.subject_key("pair", "netflix", "") == "pair:netflix:"
    assert models.subject_key("pair", "", "CO") == "pair::CO"
    assert models.subject_key("service", "") == "service:"
    assert models.subject_key("instrument", "a:b") == "instrument:a:b"
    assert kinds.subject_key("instrument", "a:b") == KINDS["instrument"].key("a:b")


def test_key_builder_and_kind_of_reject_bad_input():
    with pytest.raises(ValueError):
        KINDS["pair"].key("netflix")
    with pytest.raises(ValueError):
        KINDS["service"].key("netflix", "CO")
    with pytest.raises(ValueError):
        kind_of("bundle:x")
    assert KINDS["pair"].parse("service:netflix") is None


def _facts(key: str, kind: str) -> SubjectFacts:
    return SubjectFacts(
        key=key, kind=kind, researched_on=date(2026, 10, 9), run_id="r", status="nothing_found"
    )


def test_subject_facts_ref_splits_unchecked_under_its_own_kind():
    assert _facts("pair:netflix:CO", "pair").ref == SubjectRef("pair", ("netflix", "CO"))
    assert _facts("service:netflix", "pair").ref == SubjectRef("pair", ("netflix",))
    assert _facts("pair:netflix:", "pair").ref == SubjectRef("pair", ("netflix", ""))
    ref = _facts("service", "service").ref
    assert ref == SubjectRef("service", ()) and ref.owner is None


# ------------------------------------------------- derived constants (pinned)


def test_derived_constants_equal_their_previous_literals():
    assert io.KIND_FILES == {
        "service": "services.json",
        "pair": "pairs.json",
        "instrument": "instruments.json",
    }
    assert list(io.KIND_FILES) == ["service", "pair", "instrument"]
    assert io.LATEST_FILES == ("services.json", "pairs.json", "instruments.json", "state.json")
    assert io.DATA_FILES == ("services.json", "pairs.json", "instruments.json")
    assert plan._WINDOWS == {"service": 90, "pair": 60, "instrument": 120}
    assert (plan.SERVICE_DAYS, plan.PAIR_DAYS, plan.INSTRUMENT_DAYS) == (90, 60, 120)
    assert agents.AGENT_NAMES == {
        "service": "service_researcher",
        "pair": "pair_researcher",
        "instrument": "instrument_researcher",
    }
    assert nodes.KIND_ORDER == ("pair", "service", "instrument")
    assert {k: s.pair_scope for k, s in KINDS.items()} == {
        "service": "service",
        "pair": "self",
        "instrument": "all",
    }


def test_fields_by_level_equals_its_previous_literal():
    assert list(fields.FIELDS_BY_LEVEL) == ["service", "pair", "instrument"]
    assert fields.FIELDS_BY_LEVEL == {
        "service": (
            "account_country_basis",
            "residence_clause",
            "foreign_offer_allowed",
            "region_change",
            "region_change_cooldown_days",
            "travel_use",
            "vpn_stance",
            "enforcement_event",
        ),
        "pair": (
            "available",
            "payment_card_country",
            "gift_cards",
            "signup_location",
            "periodic_location_check",
            "billing_address",
            "local_id_required",
            "local_phone_required",
        ),
        "instrument": (
            "issuer_entity",
            "card_issuing_country",
            "iban_country",
            "eligible_residence",
            "onboarding_remote",
            "id_documents",
            "requires_local_address",
        ),
    }


def test_due_uses_the_kind_window():
    today = date(2026, 10, 9)
    state = models.ResearchState()
    for kind, spec in KINDS.items():
        key = spec.key(*SAMPLE_IDS[kind])

        def researched(days_ago: int, key: str = key, kind: str = kind) -> dict:
            on = today - timedelta(days=days_ago)
            return {key: _facts(key, kind).model_copy(update={"researched_on": on})}

        assert plan.due(key, {}, state, today) == "new"
        assert plan.due(key, researched(spec.window_days - 1), state, today) is None
        assert plan.due(key, researched(spec.window_days), state, today) == "stale"


# ------------------------------------------------ selectors and affected pairs


def _with_claim(s: SubjectFacts) -> SubjectFacts:
    check = SourceCheck(cited_url="https://x.example/", source_class="official", status="verified")
    claim = Claim(
        id=f"{s.key}:f:v",
        hash="0" * 12,
        field="f",
        value="v",
        quote="q" * 20,
        check=check,
        first_seen=s.researched_on,
    )
    return s.model_copy(update={"claims": [claim]})


def test_bare_selector_names_the_owner():
    subs = [
        _with_claim(_facts(key, kind))
        for key, kind in [
            ("service:netflix", "service"),
            ("pair:netflix:CO", "pair"),
            ("pair:spotify:CO", "pair"),
            ("instrument:revolut_eea", "instrument"),
        ]
    ]
    assert [s.key for s, _ in select_claims(subs, "netflix")] == [
        "service:netflix",
        "pair:netflix:CO",
    ]
    assert [s.key for s, _ in select_claims(subs, "revolut_eea")] == ["instrument:revolut_eea"]
    assert [s.key for s, _ in select_claims(subs, "pair:spotify:CO")] == ["pair:spotify:CO"]
    assert [c.id for _, c in select_claims(subs, "pair:spotify:CO:f:v")] == ["pair:spotify:CO:f:v"]
    assert select_claims(subs, "CO") == []


def test_affected_pairs_follow_the_pair_scope():
    keys = ["pair:netflix:CO", "pair:netflix:LT", "pair:spotify:CO", "service:netflix"]
    ctx = SimpleNamespace(facts=dict.fromkeys(keys))
    affected = cli._affected_pairs
    assert affected([_facts("pair:netflix:CO", "pair")], ctx) == {("netflix", "CO")}
    assert affected([_facts("service:netflix", "service")], ctx) == {
        ("netflix", "CO"),
        ("netflix", "LT"),
    }
    assert affected([_facts("service:disney_plus", "service")], ctx) == set()
    assert affected([_facts("instrument:revolut_eea", "instrument")], ctx) == {
        ("netflix", "CO"),
        ("netflix", "LT"),
        ("spotify", "CO"),
    }


# ------------------------------------------- malformed stored keys never raise


def test_selectors_and_affected_pairs_read_malformed_keys_like_before():
    # Same results as the pre-registry split(":") readers; a key without ids no longer raises.
    subs = [
        _with_claim(_facts(key, kind))
        for key, kind in [
            ("pair:netflix:", "pair"),
            ("pair::CO", "pair"),
            ("service:a:b", "service"),
            ("service", "service"),
        ]
    ]
    assert [s.key for s, _ in select_claims(subs, "netflix")] == ["pair:netflix:"]
    assert [s.key for s, _ in select_claims(subs, "a")] == ["service:a:b"]
    assert [s.key for s, _ in select_claims(subs, "service")] == ["service"]
    ctx = SimpleNamespace(facts=dict.fromkeys(["pair:netflix:", "pair::CO", "pair:a:CO"]))
    affected = cli._affected_pairs
    assert affected(subs[:1], ctx) == {("netflix", "")}
    assert affected(subs[1:2], ctx) == {("", "CO")}
    assert affected(subs[2:3], ctx) == {("a", "CO")}
    assert affected(subs[3:], ctx) == set()
    assert affected([_facts("pair:netflix", "pair")], ctx) == set()


@pytest.mark.parametrize("key", ["pair:netflix:", "pair::CO"])
def test_site_data_tolerates_a_malformed_pair_key(key: str):
    ctx = EvalContext.build(
        catalog=load_catalog(),
        feasibility=FeasibilityFile(profiles={}, records=[]),
        instruments=load_payment_instruments(),
        subjects=[_facts(key, "pair")],
        today=date(2026, 10, 9),
    )
    profile = Profile(id="p", name_es="p", residence_country="SE", id_document_countries=["SE"])
    assert isinstance(_profile_assessments(profile, ctx, []), list)

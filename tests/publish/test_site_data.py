"""haggler site-data: the files web/src/data/*.json, checked key by key against the contract.

The golden test uses a small catalog/curated set under fixtures/ so it does not change when the
real YAML is edited. Regenerate the golden files with HAGGLER_UPDATE_GOLDEN=1 after an intended
change, and review the diff.
"""

from __future__ import annotations

import json
import os
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest

from haggler.catalog import load_bundles, load_catalog, load_feasibility
from haggler.models.curated import BundleRequirements
from haggler.publish import SITE_FILES, _services_json, build_site_data, site_data

FIXTURES = Path(__file__).parent / "fixtures"
GOLDEN = FIXTURES / "golden"
NOW = datetime(2026, 10, 8, 5, 0, tzinfo=UTC)
RESIDENCE = "SE"
PROFILE = "se_resident_co_citizen"

# ---------------------------------------------------------------- contract (key by key)

META_KEYS = {
    "generated_at",
    "run_id",
    "as_of",
    "residence",
    "profile",
    "research_run_id",
    "research_as_of",
    "fx_fee_pct",
    "display_currencies",
    "attributions",
}
ATTRIBUTION_KEYS = {"name", "url", "license"}
SERVICE_KEYS = {
    "id",
    "name",
    "category",
    "homepage",
    "default_plan",
    "notes_es",
    "eu_geo_class",
    "plans",
}
PLAN_KEYS = {"id", "label_es", "rank", "ads"}
COUNTRY_KEYS = {"cc", "name_es", "currency", "region", "eu_eea"}
FX_KEYS = {"as_of", "base", "rates", "stale"}
OFFER_KEYS = {
    "obs_key",
    "service_id",
    "plan_id",
    "country",
    "channel",
    "currency",
    "amount",
    "billing_period",
    "monthly_equiv",
    "monthly_eur",
    "vat_included",
    "source_url",
    "source_kind",
    "extraction",
    "status",
    "flags",
    "first_seen",
    "last_changed",
    "label",
    "rank",
    "eu_badge",
}
FEASIBILITY_KEYS = {"labels", "records", "profiles", "default_profile", "rules", "eea_countries"}
PROFILE_KEYS = {
    "id",
    "name_es",
    "description_es",
    "residence_country",
    "citizenships",
    "capabilities",
}
STEP_KEYS = {"type", "value", "requires_presence", "requires_false_declaration"}
EVIDENCE_KEYS = {"url", "kind", "quote", "retrieved_at", "verified"}
PROVENANCES = {"curated", "agent_reviewed", "agent_pending"}
LABEL_KEYS = {"id", "rank", "name_es", "description_es"}
RECORD_KEYS = {
    "service_id",
    "target_country",
    "label",
    "rank",
    "eu_badge",
    "legality",
    "tos",
    "practical",
    "confidence",
    "status",
    "notes_es",
    "tos_clause_url",
    "profile",
    "provenance",
    "steps",
    "evidence",
    "assumptions_es",
    "reasons_es",
    "researched_on",
}
CHEAPEST_KEYS = {"service_id", "plan_id", "home", "best_by_risk", "savings_month_eur"}
REF_KEYS = {
    "obs_key",
    "country",
    "channel",
    "currency",
    "amount",
    "monthly_equiv",
    "cost_eur",
    "cost_eur_with_fee",
    "label",
    "rank",
    "eu_badge",
    "source_url",
}
RISK_KEYS = {"0", "1", "2", "3"}
BUNDLE_KEYS = {
    "bundle_id",
    "provider_id",
    "provider_type",
    "country",
    "name",
    "tier",
    "official_url",
    "observed_service_id",
    "covers",
    "price",
    "monthly_eur",
    "full_monthly_eur",
    "monthly_eur_with_fee",
    "first_year_monthly_eur",
    "saving_basis",
    "standalone_sum_eur",
    "standalone_missing",
    "saving_pct",
    "optional_savings",
    "components",
    "requirements",
    "display_status",
    "status_reasons_es",
    "verified_by",
    "verified_at",
    "checked_at",
    "valid_from",
    "valid_until",
    "confidence",
    "watch_mode",
    "evidence",
    "verdicts",
    "pending_changes",
    "notes_es",
    "last_verified",
}
BUNDLE_PRICE_KEYS = {
    "amount",
    "currency",
    "period",
    "tax_included",
    "source",
    "observed_on",
    "base_plan_name",
    "base_plan_amount",
    "marginal_amount",
    "intro_amount",
    "intro_months",
    "intro_valid_until",
    "contract_months",
}
COMPONENT_KEYS = {
    "service_id",
    "plan_id",
    "inclusion",
    "value",
    "ads",
    "choice_group_id",
    "choose_n",
    "standalone_eur",
    "confirmed",
}
OPTIONAL_SAVING_KEYS = {"service_id", "inclusion", "value", "saving_month_eur"}
BUNDLE_REQUIREMENT_KEYS = {
    "local_id",
    "local_line",
    "local_payment",
    "credit_check",
    "residency",
    "billing_address",
    "signup_presence",
    "new_customers_only",
}
BUNDLE_EVIDENCE_KEYS = {
    "fact",
    "url",
    "kind",
    "quote",
    "retrieved_at",
    "verified",
    "via",
    "edition",
    "status",
}
BUNDLE_PENDING_KEYS = {
    "id",
    "source",
    "field",
    "value_type",
    "current",
    "proposed",
    "quote",
    "url",
    "retrieved_at",
    "first_seen",
    "big_change",
}
BUNDLE_VERDICT_KEYS = {
    "profile",
    "provenance",
    "label",
    "rank",
    "eu_badge",
    "legality",
    "tos",
    "practical",
    "confidence",
    "status",
    "notes_es",
    "steps",
    "assumptions_es",
    "reasons_es",
    "components",
}
VERDICT_COMPONENT_KEYS = {"service_id", "target_country", "label", "rank", "provenance"}
DISPLAY_STATUSES = {"verified", "needs_review", "manual", "unverified", "expired"}
ANCHOR_STATUSES = {
    "verified",
    "missing",
    "unreadable",
    "embedded_only",
    "outdated_document",
    "not_checked",
}
BUNDLE_PROVENANCES = {"home", "curated", "composed", "agent_reviewed", "agent_pending"}
REQUIREMENT_VALUES = {"required", "not_required", "unknown"}
CHANGE_KEYS = {
    "date",
    "obs_key",
    "service_id",
    "plan_id",
    "country",
    "channel",
    "kind",
    "old",
    "new",
    "pct",
}
COVERAGE_KEYS = {"service_id", "country", "channel", "status", "adapter_id", "failing_since"}


def is_number(value: Any) -> bool:
    return isinstance(value, int | float) and not isinstance(value, bool)


def is_money(value: Any, *, nullable: bool = False) -> bool:
    if value is None:
        return nullable
    return is_number(value) and round(value, 2) == value


def is_date(value: Any, *, nullable: bool = False) -> bool:
    if value is None:
        return nullable
    return isinstance(value, str) and len(value) == 10 and value[4] == "-" and value[7] == "-"


def check_ref(ref: dict | None) -> None:
    if ref is None:
        return
    assert set(ref) == REF_KEYS
    for key in ("amount", "monthly_equiv", "cost_eur", "cost_eur_with_fee"):
        assert is_money(ref[key]), (key, ref)
    assert isinstance(ref["rank"], int)
    assert isinstance(ref["eu_badge"], bool)


def check_bundle(b: dict[str, Any], *, label_ids: set[str], profile_ids: list[str]) -> None:
    """One bundles.json v2 object: every key present, null only where the contract allows it."""
    assert set(b) == BUNDLE_KEYS
    price = b["price"]
    assert set(price) == BUNDLE_PRICE_KEYS
    for key in ("amount", "base_plan_amount", "marginal_amount", "intro_amount"):
        assert is_money(price[key], nullable=True), (key, b["bundle_id"])
    assert price["period"] in {"month", "year"}
    assert isinstance(price["tax_included"], bool), b["bundle_id"]
    assert price["source"] in {"curated", "observed"}
    assert is_date(price["observed_on"], nullable=True)
    assert is_date(price["intro_valid_until"], nullable=True)
    assert (b["observed_service_id"] is not None) == (price["source"] == "observed")
    assert all(isinstance(s, str) for s in b["covers"])
    for key in (
        "monthly_eur",
        "full_monthly_eur",
        "monthly_eur_with_fee",
        "first_year_monthly_eur",
        "standalone_sum_eur",
        "saving_pct",
    ):
        assert is_money(b[key], nullable=True), (key, b["bundle_id"])
    assert b["saving_basis"] in {"full", "marginal"}
    assert all(isinstance(s, str) for s in b["standalone_missing"])
    if b["standalone_sum_eur"] is None:
        assert b["saving_pct"] is None
    for o in b["optional_savings"]:
        assert set(o) == OPTIONAL_SAVING_KEYS
        assert o["inclusion"] in {"discount_pct", "discount_abs", "free_months"}
        assert is_money(o["saving_month_eur"], nullable=True)
    for comp in b["components"]:
        assert set(comp) == COMPONENT_KEYS
        assert is_money(comp["standalone_eur"], nullable=True)
        assert isinstance(comp["confirmed"], bool)
    req = b["requirements"]
    assert set(req) == BUNDLE_REQUIREMENT_KEYS
    assert req["residency"] in REQUIREMENT_VALUES
    assert req["billing_address"] in REQUIREMENT_VALUES
    assert req["signup_presence"] in {"checked", "not_checked", "unknown"}
    assert b["display_status"] in DISPLAY_STATUSES
    assert all(isinstance(r, str) and r for r in b["status_reasons_es"])
    if b["display_status"] != "verified":
        assert b["status_reasons_es"], b["bundle_id"]
    assert b["verified_by"] in {"official_fetch", "user_report", "search_summary", "agent_research"}
    for key in ("verified_at", "checked_at", "valid_from", "valid_until"):
        assert is_date(b[key], nullable=True), key
    assert b["confidence"] in {"low", "medium", "high"}
    assert b["watch_mode"] in {"auto", "manual", "off"}
    for e in b["evidence"]:
        assert set(e) == BUNDLE_EVIDENCE_KEYS
        assert isinstance(e["verified"], bool)
        assert is_date(e["retrieved_at"], nullable=True)
        if e["fact"] is not None:  # an anchor, with what the watcher found of it
            assert e["fact"] in {"price", "base_price", "intro", "component", "validity"}
            assert e["kind"] == "official" and e["quote"]
            assert e["status"] in ANCHOR_STATUSES
            assert e["via"] in {None, "text", "pdf", "embedded"}
            if e["verified"]:
                assert e["retrieved_at"] is not None and e["status"] != "missing"
    if b["display_status"] == "expired":
        assert b["verdicts"] == []
    else:
        assert b["verdicts"] and b["verdicts"][0]["profile"] == profile_ids[0]
    for v in b["verdicts"]:
        assert set(v) == BUNDLE_VERDICT_KEYS
        assert v["profile"] in profile_ids
        assert v["provenance"] in BUNDLE_PROVENANCES
        assert v["label"] in label_ids and isinstance(v["rank"], int)
        assert isinstance(v["eu_badge"], bool)
        assert v["status"] in {"verified", "unverified"}
        assert all(set(st) == STEP_KEYS for st in v["steps"])
        assert all(isinstance(x, str) for x in [*v["assumptions_es"], *v["reasons_es"]])
        for c in v["components"]:
            assert set(c) == VERDICT_COMPONENT_KEYS
            assert isinstance(c["rank"], int)
            # null: nothing evaluated the component's service in that country.
            assert c["provenance"] in {None, "home", "curated", "agent_reviewed", "agent_pending"}
    for c in b["pending_changes"]:
        assert set(c) == BUNDLE_PENDING_KEYS, b["bundle_id"]
        assert c["source"] in {"watcher", "agent"}
        assert c["value_type"] in {
            "money",
            "int",
            "date",
            "service_ref",
            "service_pct",
            "bool",
            "url",
        }
        assert c["proposed"] != c["current"] and c["quote"] and c["url"].startswith("https://")
        assert isinstance(c["big_change"], bool)
        assert all(c[k] is None or is_date(c[k]) for k in ("retrieved_at", "first_seen"))
    if b["pending_changes"]:
        assert b["display_status"] in {"needs_review", "expired"}, b["bundle_id"]
    assert is_date(b["last_verified"])


def check_contract(site: dict[str, Any]) -> None:
    assert set(site) == set(SITE_FILES)

    meta = site["meta"]
    assert set(meta) == META_KEYS
    assert meta["generated_at"].endswith("Z")
    assert meta["run_id"] is None or isinstance(meta["run_id"], str)
    assert is_date(meta["as_of"], nullable=True)
    assert is_number(meta["fx_fee_pct"])
    # Residence, EUR and USD first, then every other currency the FX table has a rate for.
    assert meta["display_currencies"][:3] == ["SEK", "EUR", "USD"]
    assert set(meta["display_currencies"][3:]) == set(site["fx"]["rates"]) - {"SEK", "USD"}
    for a in meta["attributions"]:
        assert set(a) == ATTRIBUTION_KEYS
        assert all(isinstance(v, str) and v for v in a.values())

    for s in site["services"]:
        assert set(s) == SERVICE_KEYS
        assert s["plans"]
        for p in s["plans"]:
            assert set(p) == PLAN_KEYS
            assert isinstance(p["rank"], int) and isinstance(p["ads"], bool)

    for c in site["countries"]:
        assert set(c) == COUNTRY_KEYS
        assert isinstance(c["eu_eea"], bool)

    fx = site["fx"]
    assert set(fx) == FX_KEYS
    assert fx["base"] == "EUR"
    assert is_date(fx["as_of"], nullable=True)
    assert all(is_number(v) and v > 0 for v in fx["rates"].values())
    assert set(fx["stale"]) <= set(fx["rates"])

    for o in site["offers"]:
        assert set(o) == OFFER_KEYS
        assert is_money(o["amount"]) and is_money(o["monthly_equiv"])
        assert is_money(o["monthly_eur"], nullable=True)
        assert is_date(o["first_seen"]) and is_date(o["last_changed"])
        assert isinstance(o["flags"], list)
        assert isinstance(o["rank"], int) and isinstance(o["eu_badge"], bool)
    keys = [o["obs_key"] for o in site["offers"]]
    assert keys == sorted(set(keys))

    feas = site["feasibility"]
    assert set(feas) == FEASIBILITY_KEYS
    for label in feas["labels"]:
        assert set(label) == LABEL_KEYS
    profile_ids = [p["id"] for p in feas["profiles"]]
    assert feas["default_profile"] == profile_ids[0] == site["meta"]["profile"]
    for p in feas["profiles"]:
        assert set(p) == PROFILE_KEYS
    for r in feas["records"]:
        assert set(r) == RECORD_KEYS
        assert isinstance(r["rank"], int)
        assert r["profile"] in profile_ids and r["provenance"] in PROVENANCES
        assert all(set(st) == STEP_KEYS for st in r["steps"])
        assert all(set(e) == EVIDENCE_KEYS for e in r["evidence"])
        assert all(isinstance(x, str) for x in [*r["assumptions_es"], *r["reasons_es"]])
        assert is_date(r["researched_on"], nullable=True)
    assert site["meta"]["research_run_id"] is None or isinstance(
        site["meta"]["research_run_id"], str
    )

    for row in site["cheapest"]:
        assert set(row) == CHEAPEST_KEYS
        check_ref(row["home"])
        assert set(row["best_by_risk"]) == RISK_KEYS
        assert set(row["savings_month_eur"]) == RISK_KEYS
        for ref in row["best_by_risk"].values():
            check_ref(ref)
        for value in row["savings_month_eur"].values():
            assert is_money(value, nullable=True)

    label_ids = {label["id"] for label in feas["labels"]}
    for b in site["bundles"]:
        check_bundle(b, label_ids=label_ids, profile_ids=profile_ids)

    for ch in site["changes"]:
        assert set(ch) == CHANGE_KEYS
        assert is_date(ch["date"])
        assert ch["pct"] is None or is_number(ch["pct"])
        for value in (ch["old"], ch["new"]):
            assert value is None or (
                set(value) == {"amount", "currency"} and is_money(value["amount"])
            )
    dates = [ch["date"] for ch in site["changes"]]
    assert dates == sorted(dates, reverse=True)

    for cov in site["coverage"]:
        assert set(cov) == COVERAGE_KEYS
        assert is_date(cov["failing_since"], nullable=True)


# ---------------------------------------------------------------- helpers


@pytest.fixture(scope="module")
def fx_catalog():
    return load_catalog(FIXTURES / "catalog")


@pytest.fixture(scope="module")
def fx_feasibility():
    return load_feasibility(FIXTURES / "curated")


@pytest.fixture(scope="module")
def fx_bundles():
    return load_bundles(FIXTURES / "curated")


@pytest.fixture
def written(tmp_path: Path, fx_catalog, fx_feasibility, fx_bundles) -> dict[str, Any]:
    out = tmp_path / "site"
    paths = site_data(
        FIXTURES / "data",
        out,
        residence=RESIDENCE,
        profile=PROFILE,
        catalog=fx_catalog,
        feasibility=fx_feasibility,
        bundles=fx_bundles,
        fx_fee_pct=2.0,
        now=NOW,
    )
    assert sorted(paths) == sorted(SITE_FILES)
    assert sorted(p.name for p in out.iterdir()) == sorted(f"{n}.json" for n in SITE_FILES)
    return {name: json.loads(path.read_text(encoding="utf-8")) for name, path in paths.items()}


def by(rows: list[dict], key: str) -> dict[str, dict]:
    return {r[key]: r for r in rows}


# ---------------------------------------------------------------- golden


@pytest.mark.parametrize("name", SITE_FILES)
def test_golden(written: dict[str, Any], name: str) -> None:
    golden_path = GOLDEN / f"{name}.json"
    actual = written[name]
    if os.getenv("HAGGLER_UPDATE_GOLDEN"):
        golden_path.write_text(json.dumps(actual, indent=2, ensure_ascii=False) + "\n")
    expected = json.loads(golden_path.read_text(encoding="utf-8"))
    if name == "meta":
        # Attribution wording belongs to the FX modules; checked separately below.
        actual = {k: v for k, v in actual.items() if k != "attributions"}
        expected = {k: v for k, v in expected.items() if k != "attributions"}
    assert actual == expected


def test_golden_output_matches_contract(written: dict[str, Any]) -> None:
    check_contract(written)


def test_lists_are_written_one_object_per_line(tmp_path: Path, written: dict[str, Any]) -> None:
    text = (tmp_path / "site" / "offers.json").read_text()
    lines = text.splitlines()
    assert lines[0] == "[" and lines[-1] == "]"
    assert len(lines) == len(written["offers"]) + 2


# ---------------------------------------------------------------- hand-checked values


def test_meta(written: dict[str, Any]) -> None:
    meta = written["meta"]
    assert meta["run_id"] == "20261008T041700Z-ab12cd"
    assert meta["as_of"] == "2026-10-08"
    assert meta["generated_at"] == "2026-10-08T05:00:00Z"
    assert (meta["residence"], meta["profile"], meta["fx_fee_pct"]) == ("SE", PROFILE, 2.0)
    urls = {a["url"]: a for a in meta["attributions"]}
    tompec = urls["https://github.com/tompec/netflix-prices"]
    assert tompec["license"] == "CC BY 4.0"
    assert "Thomas Pecserke" in tompec["name"]
    assert any("ecb.europa.eu" in u for u in urls)
    assert any("frankfurter" in u for u in urls)


def test_services_and_countries(written: dict[str, Any]) -> None:
    ids = [s["id"] for s in written["services"]]
    # Catalog order; apple_arcade has no source but a bundle uses it; vix has neither.
    assert ids == [
        "netflix",
        "spotify",
        "icloud_plus",
        "apple_one",
        "apple_music",
        "apple_tv",
        "apple_arcade",
    ]
    icloud = by(written["services"], "id")["icloud_plus"]
    assert icloud["notes_es"] == "Precios de la tabla oficial de Apple."
    assert icloud["plans"][0] == {"id": "50gb", "label_es": "50 GB", "rank": 0, "ads": False}


def test_a_service_a_bundle_only_covers_is_listed(fx_catalog: Any) -> None:
    # vix has no price source and no component names it: covering it is enough for its page.
    fixture = load_bundles(FIXTURES / "curated")
    first = fixture.bundles[0].model_copy(update={"covers": ["vix"]})
    bundles = fixture.model_copy(update={"bundles": [first, *fixture.bundles[1:]]})
    ids = [s["id"] for s in _services_json(fx_catalog, bundles)]
    assert "vix" in ids
    assert "vix" not in [s["id"] for s in _services_json(fx_catalog, fixture)]


def test_countries(written: dict[str, Any]) -> None:
    assert [c["cc"] for c in written["countries"]] == ["SE", "LT", "CO", "AR", "US"]
    assert written["countries"][0] == {
        "cc": "SE",
        "name_es": "Suecia",
        "currency": "SEK",
        "region": "nordics",
        "eu_eea": True,
    }


def test_fx(written: dict[str, Any]) -> None:
    assert written["fx"] == {
        "as_of": "2026-10-08",
        "base": "EUR",
        "rates": {"COP": 4500.0, "SEK": 11.0, "USD": 1.1},
        "stale": ["USD"],
    }


def test_offers(written: dict[str, Any]) -> None:
    offers = by(written["offers"], "obs_key")
    # Rows outside the catalog and rejected rows are dropped.
    assert "disney_plus:standard:SE:web:month" not in offers
    assert "spotify:family:CO:web:month" not in offers
    assert len(offers) == 21

    co = offers["netflix:standard:CO:web:month"]
    assert (co["amount"], co["monthly_eur"]) == (26900, 5.98)  # 26900 / 4500
    assert (co["label"], co["rank"], co["eu_badge"]) == ("tos_breach_low_enforcement", 2, False)
    home = offers["netflix:standard:SE:web:month"]
    assert (home["monthly_eur"], home["label"], home["rank"]) == (13.55, "official_home", 0)
    assert offers["netflix:standard:US:web:month"]["label"] == "not_feasible"
    lt = offers["icloud_plus:200gb:LT:web:month"]
    assert (lt["label"], lt["rank"], lt["eu_badge"], lt["extraction"]) == (
        "eu_right_untested",
        1,
        True,
        "llm",
    )
    # No record for this country -> unknown; no FX rate for ARS -> monthly_eur null.
    ar = offers["netflix:standard:AR:web:month"]
    assert (ar["label"], ar["rank"], ar["monthly_eur"]) == ("unknown", 99, None)
    yearly = offers["apple_tv:standard:US:web:year"]
    assert (yearly["amount"], yearly["monthly_equiv"], yearly["monthly_eur"]) == (99, 8.25, 7.5)
    hold = offers["spotify:individual:LT:web:month"]
    assert (hold["status"], hold["flags"]) == ("carried_forward", ["quarantine_hold"])


def test_feasibility(written: dict[str, Any]) -> None:
    feas = written["feasibility"]
    assert [label["id"] for label in feas["labels"]] == [
        "official_home",
        "eu_right_untested",
        "tos_breach_low_enforcement",
        "tos_breach_enforced",
        "not_feasible",
        "unknown",
    ]
    assert [label["rank"] for label in feas["labels"]] == [0, 1, 2, 3, 4, 99]
    assert all(label["name_es"] and label["description_es"] for label in feas["labels"])
    records = {(r["service_id"], r["target_country"]): r for r in feas["records"]}
    # The record for another residence country is not part of the SE site.
    assert sorted(records) == [
        ("apple_one", "CO"),
        ("icloud_plus", "LT"),
        ("netflix", "CO"),
        ("netflix", "US"),
        ("spotify", "CO"),
    ]
    netflix = records[("netflix", "CO")]
    assert netflix["tos_clause_url"] == "https://help.netflix.com/legal/termsofuse"
    assert (netflix["label"], netflix["rank"], netflix["practical"]) == (
        "tos_breach_low_enforcement",
        2,
        "presence_once",
    )
    assert records[("icloud_plus", "LT")]["eu_badge"] is True
    assert records[("netflix", "US")]["rank"] == 4


def test_cheapest(written: dict[str, Any]) -> None:
    rows = {(r["service_id"], r["plan_id"]): r for r in written["cheapest"]}
    row = rows[("netflix", "standard")]
    assert row["home"]["country"] == "SE"
    assert row["home"]["cost_eur_with_fee"] == 13.55  # SEK at home: no FX fee
    assert row["best_by_risk"]["0"]["country"] == "SE"
    assert row["best_by_risk"]["1"]["country"] == "SE"  # LT is unknown: never chosen
    best = row["best_by_risk"]["2"]
    assert (best["country"], best["cost_eur"], best["cost_eur_with_fee"]) == ("CO", 5.98, 6.1)
    assert row["savings_month_eur"] == {"0": 0.0, "1": 0.0, "2": 7.45, "3": 7.45}
    icloud = rows[("icloud_plus", "200gb")]
    assert icloud["best_by_risk"]["1"]["country"] == "LT"
    assert icloud["best_by_risk"]["1"]["eu_badge"] is True
    assert icloud["savings_month_eur"]["1"] == 0.5  # 3.55 - 2.99 * 1.02


def test_bundles(written: dict[str, Any]) -> None:
    bundles = by(written["bundles"], "bundle_id")
    assert list(bundles) == [
        "apple_one_individual_se",
        "apple_one_family_se",
        "meli_co",
        "telia_se",
        "yearly_lt",
    ]

    apple = bundles["apple_one_individual_se"]
    # Observed price (195 SEK) from offers; Arcade has no price and is left out of the sum.
    assert apple["observed_service_id"] == "apple_one"
    assert apple["covers"] == ["apple_music", "apple_tv", "apple_arcade", "icloud_plus"]
    assert apple["price"]["amount"] == 195
    assert (apple["price"]["source"], apple["price"]["observed_on"]) == ("observed", "2026-10-08")
    assert apple["monthly_eur"] == apple["full_monthly_eur"] == 17.73  # 195 / 11
    assert apple["monthly_eur_with_fee"] == 17.73  # SEK at home: no FX fee
    assert apple["standalone_sum_eur"] == 20.91  # (119 + 99 + 12) / 11
    assert apple["standalone_missing"] == ["apple_arcade"]
    assert apple["saving_pct"] == 15.21
    assert apple["display_status"] == "unverified"
    assert apple["status_reasons_es"][0].startswith("Componentes tomados de un resumen")

    family = bundles["apple_one_family_se"]
    assert family["price"]["amount"] is None
    assert (family["monthly_eur"], family["standalone_sum_eur"], family["saving_pct"]) == (
        None,
        None,
        None,
    )
    assert (
        "Aún no hay un precio publicado de este plan en los datos diarios."
        in (family["status_reasons_es"])
    )

    meli = bundles["meli_co"]
    assert meli["monthly_eur"] == 5.53  # 24900 / 4500
    assert meli["monthly_eur_with_fee"] == 5.64  # COP is not the residence's currency: +2 %
    assert meli["standalone_sum_eur"] == 3.76  # netflix standard_with_ads CO; discount apart
    assert meli["saving_pct"] == -47.07
    # The 30 % Spotify discount is an optional saving, never part of saving_pct.
    assert meli["optional_savings"] == [
        {
            "service_id": "spotify",
            "inclusion": "discount_pct",
            "value": 30,
            "saving_month_eur": 1.13,
        }
    ]
    assert meli["components"][1] == {
        "service_id": "spotify",
        "plan_id": None,
        "inclusion": "discount_pct",
        "value": 30,
        "ads": None,
        "choice_group_id": None,
        "choose_n": None,
        "standalone_eur": 3.76,
        "confirmed": False,
    }
    assert meli["requirements"]["local_id"] == "cedula"
    assert meli["notes_es"] == "Precio en conflicto."
    # Without profiles.yaml the profile is synthesized with no documents: the curated verdict
    # still applies, but a requirement the profile cannot meet makes it not feasible.
    (verdict,) = meli["verdicts"]
    assert (verdict["provenance"], verdict["rank"]) == ("curated", 4)
    assert verdict["reasons_es"] == [
        "Exige un documento de identidad de CO (cédula); el perfil no tiene uno."
    ]

    telia = bundles["telia_se"]
    assert telia["price"]["amount"] == 699
    assert telia["price"]["marginal_amount"] == 130
    assert telia["price"]["base_plan_name"] == "Telia Plus"
    assert telia["saving_basis"] == "marginal"
    assert telia["monthly_eur"] == 11.82  # only the marginal 130 SEK
    assert telia["full_monthly_eur"] == 63.55  # 699 / 11
    # The sum of the rounded parts the card lists: 149 / 11 + 139 / 11 = 13.55 + 12.64.
    parts = [c["standalone_eur"] for c in telia["components"] if c["inclusion"] == "included"]
    assert parts == [13.55, 12.64]
    assert telia["standalone_sum_eur"] == 26.19 == round(sum(parts), 2)
    assert telia["saving_pct"] == 54.87  # (26.19 - 11.82) / 26.19

    yearly = bundles["yearly_lt"]
    assert yearly["price"]["period"] == "year"
    assert yearly["monthly_eur"] == 10.0  # 120 / 12
    assert yearly["monthly_eur_with_fee"] == 10.2  # EUR is not SEK: +2 %
    assert yearly["standalone_sum_eur"] == 6.99  # cheapest LT Netflix plan (no plan id)
    assert yearly["saving_pct"] == -43.06
    assert yearly["display_status"] == "expired"
    assert yearly["status_reasons_es"][0] == "La oferta pasó su fecha de fin."
    assert yearly["verdicts"] == []
    assert yearly["last_verified"] == "2026-09-01"


def test_bundle_verdicts_per_profile(fx_catalog, fx_feasibility, fx_bundles) -> None:
    """With profiles.yaml, one verdict per public profile, the default first: the curated
    Meli+ record for the default profile, the cédula gate for the other one, and Telia at
    home for both (personnummer and a Swedish line)."""
    from haggler.catalog import load_payment_instruments, load_profiles

    site = build_site_data(
        FIXTURES / "data",
        residence=RESIDENCE,
        profile=PROFILE,
        catalog=fx_catalog,
        feasibility=fx_feasibility,
        bundles=fx_bundles,
        now=NOW,
        profiles=load_profiles(),
        instruments=load_payment_instruments(),
    )
    check_contract(site)
    bundles = by(site["bundles"], "bundle_id")
    ranks = {
        bundle_id: [(v["profile"], v["provenance"], v["rank"]) for v in b["verdicts"]]
        for bundle_id, b in bundles.items()
    }
    assert ranks["meli_co"] == [
        (PROFILE, "curated", 2),
        ("se_resident_eu_generic", "composed", 4),
    ]
    assert ranks["telia_se"] == [(PROFILE, "home", 0), ("se_resident_eu_generic", "home", 0)]
    assert ranks["yearly_lt"] == []
    meli = bundles["meli_co"]["verdicts"][0]
    assert meli["components"] == [
        {
            "service_id": "netflix",
            "target_country": "CO",
            "label": "tos_breach_low_enforcement",
            "rank": 2,
            "provenance": "curated",
        }
    ]


def test_bundle_requirements_v2(fx_catalog, fx_feasibility, fx_bundles) -> None:
    """Every requirement key is published; a free-text or enum value that is not established
    ("unknown") is null, while the three-valued requirements keep "unknown"."""
    v2 = BundleRequirements(
        residency="required",
        local_id="cedula",
        billing_address="required",
        signup_presence="checked",
        new_customers_only=True,
    )
    bundles = fx_bundles.model_copy(
        update={"bundles": [b.model_copy(update={"requirements": v2}) for b in fx_bundles.bundles]}
    )
    site = build_site_data(
        FIXTURES / "data",
        residence=RESIDENCE,
        profile=PROFILE,
        catalog=fx_catalog,
        feasibility=fx_feasibility,
        bundles=bundles,
        now=NOW,
    )
    assert site["bundles"][0]["requirements"] == {
        "local_id": "cedula",
        "local_line": None,
        "local_payment": None,
        "credit_check": None,
        "residency": "required",
        "billing_address": "required",
        "signup_presence": "checked",
        "new_customers_only": True,
    }
    assert by(site["bundles"], "bundle_id")["meli_co"]["requirements"]["signup_presence"] == (
        "checked"
    )


def test_expired_bundles_leave_the_site_after_90_days(
    fx_catalog, fx_feasibility, fx_bundles
) -> None:
    def ids(now: datetime) -> list[str]:
        site = build_site_data(
            FIXTURES / "data",
            residence=RESIDENCE,
            profile=PROFILE,
            catalog=fx_catalog,
            feasibility=fx_feasibility,
            bundles=fx_bundles,
            now=now,
        )
        return [b["bundle_id"] for b in site["bundles"]]

    # yearly_lt ended on 2026-09-01: listed under "Ofertas vencidas" until 2026-11-30.
    assert "yearly_lt" in ids(datetime(2026, 11, 30, 5, 0, tzinfo=UTC))
    assert "yearly_lt" not in ids(datetime(2026, 12, 1, 5, 0, tzinfo=UTC))


def test_changes_window_dedupe_and_order(written: dict[str, Any]) -> None:
    changes = written["changes"]
    # 2026-06-15 is outside the 90-day window; the duplicated 2026-10 line appears once.
    assert [(c["date"], c["obs_key"]) for c in changes] == [
        ("2026-10-08", "icloud_plus:200gb:LT:web:month"),
        ("2026-10-08", "netflix:standard:SE:web:month"),
        ("2026-09-20", "netflix:basic:CO:web:month"),
        ("2026-09-20", "spotify:individual:SE:web:month"),
        ("2026-09-01", "netflix:standard:SE:web:month"),
    ]
    netflix = changes[1]
    assert netflix["old"] == {"amount": 139, "currency": "SEK"}
    assert netflix["new"] == {"amount": 149, "currency": "SEK"}
    assert (netflix["kind"], netflix["pct"]) == ("changed", 7.19)
    removed = changes[2]
    assert (removed["kind"], removed["new"]) == ("removed", None)
    assert removed["old"] == {"amount": 16900, "currency": "COP"}


def test_coverage(written: dict[str, Any]) -> None:
    coverage = written["coverage"]
    assert [(c["service_id"], c["country"]) for c in coverage] == [
        ("apple_one", "AR"),
        ("netflix", "AR"),
        ("netflix", "SE"),
        ("spotify", "LT"),
    ]
    assert coverage[-1]["failing_since"] == "2026-10-06"


# ---------------------------------------------------------------- other inputs


def test_first_deploy_with_empty_data(tmp_path: Path, catalog, feasibility, bundles) -> None:
    """No data/latest yet: valid files with empty data arrays and meta.run_id null."""
    out = tmp_path / "site"
    site_data(
        tmp_path / "data",
        out,
        residence=RESIDENCE,
        profile=PROFILE,
        catalog=catalog,
        feasibility=feasibility,
        bundles=bundles,
        now=NOW,
    )
    site = {name: json.loads((out / f"{name}.json").read_text()) for name in SITE_FILES}
    check_contract(site)
    assert site["meta"]["run_id"] is None
    assert site["meta"]["as_of"] is None
    assert site["offers"] == site["cheapest"] == site["changes"] == site["coverage"] == []
    assert site["fx"] == {"as_of": None, "base": "EUR", "rates": {}, "stale": []}
    assert len(site["countries"]) == len(catalog.countries)
    assert {s["id"] for s in site["services"]} >= {"netflix", "paramount_plus", "apple_arcade"}
    assert site["feasibility"]["records"]
    assert site["bundles"]
    for b in site["bundles"]:
        # No FX table: no EUR figure; no daily data: an observed bundle has no price at all.
        assert b["monthly_eur"] is None and b["saving_pct"] is None
        assert b["full_monthly_eur"] is None and b["monthly_eur_with_fee"] is None
        assert b["display_status"] == "unverified" and b["status_reasons_es"]
        assert b["verdicts"]
        if b["price"]["source"] == "observed":
            assert b["price"]["amount"] is None
            assert (
                "Aún no hay un precio publicado de este plan en los datos diarios."
                in (b["status_reasons_es"])
            )


def test_real_catalog_and_curated_with_fixture_data(catalog, feasibility, bundles) -> None:
    """The real catalog/curated YAML through the same code path (shapes only)."""
    site = build_site_data(
        FIXTURES / "data",
        residence=RESIDENCE,
        profile=PROFILE,
        catalog=catalog,
        feasibility=feasibility,
        bundles=bundles,
        now=NOW,
    )
    check_contract(site)
    offers = by(site["offers"], "obs_key")
    assert "disney_plus:standard:SE:web:month" in offers  # in the real catalog
    apple = by(site["bundles"], "bundle_id")["apple_one_individual_se"]
    assert apple["monthly_eur"] == 17.73


def test_changes_anchor_is_today_without_run(tmp_path: Path, fx_catalog, fx_feasibility) -> None:
    data = tmp_path / "data"
    (data / "changes").mkdir(parents=True)
    rows = [
        {"date": "2026-10-01", "obs_key": "a:b:SE:web:month", "kind": "added", "new": "1 SEK"},
        {"date": "2026-01-01", "obs_key": "a:b:SE:web:month", "kind": "removed", "old": "1 SEK"},
        {"date": "not a date", "obs_key": "x", "kind": "added"},
    ]
    lines = [json.dumps(r) for r in rows] + ["{broken line", '{"date": "2026-10-02"}']
    (data / "changes" / "2026-10.jsonl").write_text("\n".join(lines))
    site = build_site_data(
        data,
        residence=RESIDENCE,
        profile=PROFILE,
        catalog=fx_catalog,
        feasibility=fx_feasibility,
        bundles=load_bundles(FIXTURES / "curated"),
        now=NOW,
    )
    assert [c["date"] for c in site["changes"]] == ["2026-10-01"]
    assert site["changes"][0]["new"] == {"amount": 1, "currency": "SEK"}


def test_other_residence(fx_catalog, fx_feasibility, fx_bundles) -> None:
    site = build_site_data(
        FIXTURES / "data",
        residence="CO",
        profile=PROFILE,
        catalog=fx_catalog,
        feasibility=fx_feasibility,
        bundles=fx_bundles,
        fx_fee_pct=0,
        now=NOW,
    )
    assert site["meta"]["display_currencies"][:3] == ["COP", "EUR", "USD"]
    assert "SEK" in site["meta"]["display_currencies"]
    assert site["feasibility"]["records"] == []
    offers = by(site["offers"], "obs_key")
    assert offers["netflix:standard:CO:web:month"]["label"] == "official_home"
    assert offers["netflix:standard:SE:web:month"]["label"] == "unknown"


def test_unknown_residence_or_profile(fx_catalog, fx_feasibility, fx_bundles) -> None:
    kwargs = {"catalog": fx_catalog, "feasibility": fx_feasibility, "bundles": fx_bundles}
    with pytest.raises(ValueError, match="residencia"):
        build_site_data(FIXTURES / "data", residence="XX", profile=PROFILE, **kwargs)
    with pytest.raises(ValueError, match="perfil"):
        build_site_data(FIXTURES / "data", residence="SE", profile="nobody", **kwargs)


# ------------------------------------------------------------------ research + profiles


def _research_data(data: Path) -> None:
    """data/research as `haggler sync` writes it: Netflix's residence clause (reviewed) and the
    CO card rule (pending review)."""
    from haggler.models.research import (
        Claim,
        SourceCheck,
        SubjectFacts,
        claim_hash,
        claim_id,
    )
    from haggler.research import io as rio

    def claim(key: str, field: str, value: str) -> Claim:
        url = "https://help.netflix.com/legal/termsofuse"
        quote = f"Literal quote stating {field} {value} on the page."
        return Claim(
            id=claim_id(key, field, value),
            hash=claim_hash(field, value, None, quote, url),
            field=field,
            value=value,
            quote=quote,
            check=SourceCheck(
                cited_url=url,
                final_url=url,
                source_class="official",
                status="verified",
                retrieved_at=NOW,
            ),
            first_seen=NOW.date(),
        )

    svc = SubjectFacts(
        key="service:netflix",
        kind="service",
        researched_on=NOW.date(),
        run_id="r1",
        status="researched",
        claims=[claim("service:netflix", "residence_clause", "primary_use_in_account_country")],
    )
    pair = SubjectFacts(
        key="pair:netflix:CO",
        kind="pair",
        researched_on=NOW.date(),
        run_id="r1",
        status="researched",
        claims=[claim("pair:netflix:CO", "payment_card_country", "must_match")],
    )
    target = data / "research"
    target.mkdir(parents=True, exist_ok=True)
    for name, blob in rio.subject_files([svc, pair]).items():
        (target / name).write_bytes(blob)
    (target / "run.json").write_text(
        json.dumps({"schema_version": 1, "run_id": "r1", "as_of": "2026-10-08", "files": {}})
    )
    return svc


def test_research_records_per_profile(tmp_path: Path, catalog, feasibility, bundles) -> None:
    import shutil

    from haggler.catalog import load_payment_instruments, load_profiles
    from haggler.models.research import ReviewDecision, ReviewFile

    data = tmp_path / "data"
    shutil.copytree(FIXTURES / "data", data)
    svc = _research_data(data)
    clause = svc.claims[0]
    review = ReviewFile(
        decisions=[
            ReviewDecision(id=clause.id, hash=clause.hash, decision="accept", decided_on=NOW.date())
        ]
    )
    site = build_site_data(
        data,
        residence=RESIDENCE,
        profile=PROFILE,
        catalog=catalog,
        feasibility=feasibility,
        bundles=bundles,
        now=NOW,
        profiles=load_profiles(),
        instruments=load_payment_instruments(),
        review=review,
    )
    check_contract(site)
    assert site["meta"]["research_run_id"] == "r1"
    feas = site["feasibility"]
    assert [p["id"] for p in feas["profiles"]] == [PROFILE, "se_resident_eu_generic"]
    recs = feas["records"]
    # The default profile keeps its curated Netflix CO record.
    mine = [r for r in recs if r["profile"] == PROFILE and r["service_id"] == "netflix"]
    co = [r for r in mine if r["target_country"] == "CO"]
    assert [r["provenance"] for r in co] == ["curated"]
    # The generic profile: the reviewed clause alone leaves practicality unknown (no CO card);
    # with the pending card rule the route is not feasible.
    generic = [
        r
        for r in recs
        if r["profile"] == "se_resident_eu_generic"
        and r["service_id"] == "netflix"
        and r["target_country"] == "CO"
    ]
    assert [(r["provenance"], r["label"]) for r in generic] == [
        ("agent_reviewed", "unknown"),
        ("agent_pending", "not_feasible"),
    ]
    pending = generic[1]
    assert pending["evidence"] and pending["evidence"][0]["verified"]
    assert pending["tos"] == "breach_unenforced" and pending["practical"] == "impossible"
    # Other Netflix countries of the generic profile: the clause applies, but without pair
    # research they stay unlisted (unknown).
    assert {r["target_country"] for r in generic} == {"CO"}
    # offers.json keeps the default profile's verdicts (pending findings excluded).
    offers = by(site["offers"], "obs_key")
    assert offers["netflix:standard:CO:web:month"]["label"] == "tos_breach_low_enforcement"


def test_bundle_pending_variant_is_listed_only_when_it_differs(
    tmp_path: Path, fx_catalog, fx_feasibility, fx_bundles
) -> None:
    """As in feasibility.json: the verdict with findings still pending review follows the
    reviewed one only when it differs."""
    import shutil

    from haggler.catalog import load_payment_instruments, load_profiles
    from haggler.models.research import ReviewDecision, ReviewFile

    data = tmp_path / "data"
    shutil.copytree(FIXTURES / "data", data)
    clause = _research_data(data).claims[0]
    review = ReviewFile(
        decisions=[
            ReviewDecision(id=clause.id, hash=clause.hash, decision="accept", decided_on=NOW.date())
        ]
    )
    # Without the cédula requirement the generic profile's verdict rests on Netflix CO.
    bundles = fx_bundles.model_copy(
        update={
            "bundles": [
                b.model_copy(update={"requirements": BundleRequirements()})
                if b.bundle_id == "meli_co"
                else b
                for b in fx_bundles.bundles
            ]
        }
    )
    site = build_site_data(
        data,
        residence=RESIDENCE,
        profile=PROFILE,
        catalog=fx_catalog,
        feasibility=fx_feasibility,
        bundles=bundles,
        now=NOW,
        profiles=load_profiles(),
        instruments=load_payment_instruments(),
        review=review,
    )
    check_contract(site)
    meli = by(site["bundles"], "bundle_id")["meli_co"]
    assert [(v["profile"], v["provenance"], v["rank"]) for v in meli["verdicts"]] == [
        (PROFILE, "curated", 2),
        ("se_resident_eu_generic", "composed", 99),
        ("se_resident_eu_generic", "agent_pending", 4),
    ]


# ---------------------------------------------------------------- watcher replay (H7)

WATCH_DAY = datetime(2026, 10, 7, 5, 23, tzinfo=UTC)
ACME_URL = "https://www.acme.com.co/planes"
ACME_PRICE = (
    "Precio actual con 9 por ciento de descuento: 19900 pesos por mes, por 2 meses. "
    "Precio anterior: $ 21.900 pesos por mes."
)
ACME_NETFLIX = "Netflix Estándar con anuncios incluido en Acme Total."
TELCO_INDEX = "https://www.telco.se/om/priser"
TELCO_PDF = "https://www.telco.se/priser/TSP-4667_18_0701_Prislista.pdf"
TELCO_ROW = "Telco Plus med Netflix Standard 699 kr/mån"


def watched_bundles_file() -> Any:
    """Two auto-watched bundles: an HTML page (price, intro and component anchors) and a
    versioned price-list PDF behind an index page."""
    from haggler.models.curated import BundlesFile

    return BundlesFile.model_validate(
        {
            "providers": [
                {"id": "acme", "name": "Acme", "domains": {"CO": ["acme.com.co"]}},
                {"id": "telco", "name": "Telco", "domains": {"SE": ["telco.se"]}},
            ],
            "bundles": [
                {
                    "bundle_id": "acme_total_co",
                    "provider_id": "acme",
                    "provider_type": "marketplace",
                    "country": "CO",
                    "name": "Acme Total",
                    "tier": "total",
                    "aliases": ["Acme Total"],
                    "official_url": ACME_URL,
                    "price": {
                        "amount": "21900",
                        "currency": "COP",
                        "tax_included": True,
                        "intro_amount": "19900",
                        "intro_months": 2,
                    },
                    "components": [
                        {
                            "service_id": "netflix",
                            "plan_id": "standard_with_ads",
                            "inclusion": "included",
                        }
                    ],
                    "verified_by": "official_fetch",
                    "watch": {
                        "mode": "auto",
                        "pages": [{"url": ACME_URL}],
                        "anchors": [
                            {"fact": "price", "url": ACME_URL, "text": ACME_PRICE},
                            {"fact": "intro", "url": ACME_URL, "text": ACME_PRICE},
                            {
                                "fact": "component",
                                "service_id": "netflix",
                                "url": ACME_URL,
                                "text": ACME_NETFLIX,
                            },
                        ],
                    },
                    "last_verified": "2026-10-01",
                },
                {
                    "bundle_id": "telco_plus_se",
                    "provider_id": "telco",
                    "provider_type": "telco_mobile",
                    "country": "SE",
                    "name": "Telco Plus",
                    "tier": "plus",
                    "official_url": "https://www.telco.se/mobil",
                    "price": {"amount": "699", "currency": "SEK", "tax_included": True},
                    "components": [
                        {"service_id": "netflix", "plan_id": "standard", "inclusion": "included"}
                    ],
                    "verified_by": "official_fetch",
                    "watch": {
                        "mode": "auto",
                        "pages": [
                            {
                                "url": TELCO_PDF,
                                "kind": "pdf",
                                "index_url": TELCO_INDEX,
                                "link_pattern": r"TSP-4667_(?P<edition>\d+)_\d{4}_Prislista\.pdf",
                            }
                        ],
                        "anchors": [
                            {"fact": "price", "url": TELCO_PDF, "text": TELCO_ROW},
                            {
                                "fact": "component",
                                "service_id": "netflix",
                                "url": TELCO_PDF,
                                "text": TELCO_ROW,
                            },
                        ],
                    },
                    "last_verified": "2026-10-01",
                },
            ],
        }
    )


def watcher_replay(tmp_path: Path) -> tuple[Path, Any]:
    """`haggler research run --watch-only` over canned pages (an HTML page and a PDF built
    here), then `haggler sync` of its research into a data/ directory that also holds the
    fixture day's prices: the data/ the site is built from."""
    import asyncio
    import dataclasses
    import shutil

    from conftest import FixtureFetcher
    from haggler.catalog import load_payment_instruments, load_profiles
    from haggler.config import Settings
    from haggler.fetch import RawResponse
    from haggler.jobs.research import ResearchOptions, run_research
    from haggler.publish import sync_research
    from haggler.research.deps import ResearchDeps
    from haggler.store import LocalStore
    from pdf_support import build_pdf

    body = build_pdf([["Telco Prislista 18", TELCO_ROW, "Telco Bas 299 kr/mån"]])
    routes = {
        ACME_URL: (
            f"<html><body><main><h2>Acme Total</h2><p>{ACME_PRICE}</p>"
            f"<p>{ACME_NETFLIX}</p></main></body></html>"
        ),
        TELCO_INDEX: (
            '<html><body><a href="/priser/TSP-4667_17_0101_Prislista.pdf">17</a>'
            '<a href="/priser/TSP-4667_18_0701_Prislista.pdf">18</a></body></html>'
        ),
        TELCO_PDF: RawResponse(
            url=TELCO_PDF,
            final_url=TELCO_PDF,
            status_code=200,
            text=body.decode("utf-8", "replace"),
            content_type="application/pdf",
            body=body,
        ),
    }
    bundles = watched_bundles_file()
    store = LocalStore(tmp_path / "store")
    deps = ResearchDeps(
        settings=dataclasses.replace(Settings(), store="local"),
        catalog=load_catalog(),
        feasibility=load_feasibility(),
        instruments=load_payment_instruments(),
        profiles=load_profiles(),
        fetcher=FixtureFetcher(routes),
        store=store,
        model=None,
        now=lambda: WATCH_DAY,
        bundles=bundles,
    )
    report = asyncio.run(run_research(ResearchOptions(watch_only=True), deps))
    assert report is not None and "research/latest/bundle_watch.json" in report.outputs
    data = tmp_path / "data"
    shutil.copytree(FIXTURES / "data", data)
    result = sync_research(store, data)
    assert result is not None and data / "research" / "bundle_watch.json" in result.written
    return data, bundles


def test_site_data_with_watcher_evidence(tmp_path: Path, catalog, feasibility) -> None:
    from haggler.catalog import load_profiles

    data, bundles = watcher_replay(tmp_path)
    site = build_site_data(
        data,
        residence=RESIDENCE,
        profile=PROFILE,
        catalog=catalog,
        feasibility=feasibility,
        bundles=bundles,
        profiles=load_profiles(),
        now=NOW,
    )
    check_contract(site)
    rows = by(site["bundles"], "bundle_id")
    acme = rows["acme_total_co"]
    assert (acme["display_status"], acme["status_reasons_es"]) == ("verified", [])
    assert (acme["verified_at"], acme["checked_at"], acme["confidence"]) == (
        "2026-10-07",
        "2026-10-07",
        "high",
    )
    assert [c["confirmed"] for c in acme["components"]] == [True]
    # A verified intro anchor: the first-year cost can be shown.
    assert acme["first_year_monthly_eur"] is not None
    assert acme["first_year_monthly_eur"] < acme["monthly_eur"]
    anchors = [e for e in acme["evidence"] if e["fact"] is not None]
    assert [(e["fact"], e["status"], e["verified"], e["via"]) for e in anchors] == [
        ("price", "verified", True, "text"),
        ("intro", "verified", True, "text"),
        ("component", "verified", True, "text"),
    ]
    assert all(e["kind"] == "official" and e["retrieved_at"] == "2026-10-07" for e in anchors)
    assert anchors[0]["quote"] == ACME_PRICE

    telco = rows["telco_plus_se"]
    assert telco["display_status"] == "verified" and telco["confidence"] == "high"
    assert [(e["via"], e["edition"]) for e in telco["evidence"]] == [("pdf", "18"), ("pdf", "18")]
    assert [c["confirmed"] for c in telco["components"]] == [True]


def test_site_data_without_watch_data_lists_anchors_unchecked(
    tmp_path: Path, catalog, feasibility
) -> None:
    site = build_site_data(
        tmp_path / "empty",
        residence=RESIDENCE,
        profile=PROFILE,
        catalog=catalog,
        feasibility=feasibility,
        bundles=watched_bundles_file(),
        now=NOW,
    )
    check_contract(site)
    for b in site["bundles"]:
        assert b["display_status"] == "unverified"
        assert b["status_reasons_es"][-1] == "El vigilante aún no ha leído sus páginas oficiales."
        assert {e["status"] for e in b["evidence"]} == {"not_checked"}
        assert not any(e["verified"] for e in b["evidence"])
        assert b["first_year_monthly_eur"] is None


def test_site_data_publishes_official_value_checked_pending_changes(
    tmp_path: Path, catalog, feasibility
) -> None:
    """bundles.json pending_changes (H8): the agent's value findings that differ from the YAML,
    official and value-checked only; a rejected one is hidden; one puts the bundle in review."""
    from haggler.catalog import load_profiles
    from haggler.models.research import (
        Claim,
        ReviewDecision,
        ReviewFile,
        SourceCheck,
        SubjectFacts,
        claim_hash,
        claim_id,
    )
    from haggler.research import io as rio

    data, bundles = watcher_replay(tmp_path)
    key = "bundle:acme_total_co"

    def claim(field: str, value: str, quote: str) -> Claim:
        return Claim(
            id=claim_id(key, field, value),
            hash=claim_hash(field, value, None, quote, ACME_URL),
            field=field,
            value=value,
            quote=quote,
            check=SourceCheck(
                cited_url=ACME_URL,
                final_url=ACME_URL,
                source_class="official",
                status="verified",
                retrieved_at=WATCH_DAY,
            ),
            first_seen=WATCH_DAY.date(),
        )

    price = claim("bundle_price", "22900 COP/month", "Acme Total cuesta $ 22.900 por mes.")
    rejected = claim("bundle_intro_price", "18900 COP/month", "Acme Total: $ 18.900 por 2 meses.")
    unchecked = claim("bundle_available", "false", "Acme Total ya no se vende en Colombia.")
    held = claim("bundle_includes", "netflix", ACME_NETFLIX)  # the YAML already says so
    subject = SubjectFacts(
        key=key,
        kind="bundle",
        researched_on=WATCH_DAY.date(),
        run_id="r1",
        status="researched",
        claims=[price, rejected, unchecked, held],
    )
    (data / "research" / "bundles.json").write_bytes(rio.subject_files([subject])["bundles.json"])
    review = ReviewFile(
        decisions=[
            ReviewDecision(
                id=rejected.id, hash=rejected.hash, decision="reject", decided_on=NOW.date()
            )
        ]
    )
    site = build_site_data(
        data,
        residence=RESIDENCE,
        profile=PROFILE,
        catalog=catalog,
        feasibility=feasibility,
        bundles=bundles,
        profiles=load_profiles(),
        review=review,
        now=NOW,
    )
    check_contract(site)
    rows = by(site["bundles"], "bundle_id")
    acme = rows["acme_total_co"]
    assert acme["pending_changes"] == [
        {
            "id": price.id,
            "source": "agent",
            "field": "bundle_price",
            "value_type": "money",
            "current": "21900 COP/month",
            "proposed": "22900 COP/month",
            "quote": "Acme Total cuesta $ 22.900 por mes.",
            "url": ACME_URL,
            "retrieved_at": "2026-10-07",
            "first_seen": "2026-10-07",
            "big_change": False,
        }
    ]
    # The watcher verified every anchor, but the official page now states another price.
    assert acme["display_status"] == "needs_review" and acme["confidence"] == "low"
    assert acme["status_reasons_es"] == [
        "Cambios pendientes de revisión: precio del paquete (según la investigación "
        "automática, en la página oficial)."
    ]
    assert [c["confirmed"] for c in acme["components"]] == [True]
    assert acme["first_year_monthly_eur"] is None
    telco = rows["telco_plus_se"]
    assert (telco["display_status"], telco["pending_changes"]) == ("verified", [])

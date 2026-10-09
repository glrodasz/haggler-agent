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
from haggler.publish import SITE_FILES, build_site_data, site_data

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
FEASIBILITY_KEYS = {"labels", "records", "profiles", "default_profile"}
PROFILE_KEYS = {"id", "name_es", "description_es", "residence_country"}
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
    "price",
    "monthly_eur",
    "components",
    "standalone_sum_eur",
    "saving_pct",
    "requirements",
    "feasibility",
    "status",
    "notes_es",
    "last_verified",
}
BUNDLE_PRICE_KEYS = {"amount", "currency", "period", "tax_included", "marginal_amount"}
COMPONENT_KEYS = {"service_id", "plan_id", "inclusion", "value", "ads"}
BUNDLE_FEASIBILITY_KEYS = {"purchasable_remotely", "usable_abroad", "tos_risk", "evidence"}
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


def check_contract(site: dict[str, Any]) -> None:
    assert set(site) == set(SITE_FILES)

    meta = site["meta"]
    assert set(meta) == META_KEYS
    assert meta["generated_at"].endswith("Z")
    assert meta["run_id"] is None or isinstance(meta["run_id"], str)
    assert is_date(meta["as_of"], nullable=True)
    assert is_number(meta["fx_fee_pct"])
    assert meta["display_currencies"] == ["SEK", "EUR", "USD"]
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

    for b in site["bundles"]:
        assert set(b) == BUNDLE_KEYS
        assert set(b["price"]) == BUNDLE_PRICE_KEYS
        assert is_money(b["price"]["amount"], nullable=True)
        assert is_money(b["price"]["marginal_amount"], nullable=True)
        assert b["price"]["period"] in {"month", "year"}
        for key in ("monthly_eur", "standalone_sum_eur", "saving_pct"):
            assert is_money(b[key], nullable=True), (key, b)
        for comp in b["components"]:
            assert set(comp) == COMPONENT_KEYS
        assert isinstance(b["requirements"], dict)
        assert b["feasibility"] is None or set(b["feasibility"]) == BUNDLE_FEASIBILITY_KEYS
        assert is_date(b["last_verified"])

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
    assert apple["price"] == {
        "amount": 195,
        "currency": "SEK",
        "period": "month",
        "tax_included": True,
        "marginal_amount": None,
    }
    assert apple["monthly_eur"] == 17.73  # 195 / 11
    assert apple["standalone_sum_eur"] == 20.91  # (119 + 99 + 12) / 11
    assert apple["saving_pct"] == 15.21
    assert apple["feasibility"] == {
        "purchasable_remotely": "yes",
        "usable_abroad": "yes",
        "tos_risk": "low",
        "evidence": "terms",
    }

    family = bundles["apple_one_family_se"]
    assert family["price"]["amount"] is None
    assert (family["monthly_eur"], family["standalone_sum_eur"], family["saving_pct"]) == (
        None,
        None,
        None,
    )
    assert family["feasibility"] is None

    meli = bundles["meli_co"]
    assert meli["monthly_eur"] == 5.53  # 24900 / 4500
    assert meli["standalone_sum_eur"] == 3.76  # netflix standard_with_ads CO; discount ignored
    assert meli["saving_pct"] == -47.07
    assert meli["components"][1] == {
        "service_id": "spotify",
        "plan_id": None,
        "inclusion": "discount_pct",
        "value": 30,
        "ads": None,
    }
    assert meli["requirements"]["local_id"] == "cedula"
    assert meli["notes_es"] == "Precio en conflicto."

    telia = bundles["telia_se"]
    assert telia["price"]["amount"] == 699
    assert telia["price"]["marginal_amount"] == 130
    assert telia["monthly_eur"] == 11.82  # only the marginal 130 SEK
    assert telia["standalone_sum_eur"] == 26.18  # (149 + 139) / 11
    assert telia["saving_pct"] == 54.85

    yearly = bundles["yearly_lt"]
    assert yearly["price"]["period"] == "year"
    assert yearly["monthly_eur"] == 10.0  # 120 / 12
    assert yearly["standalone_sum_eur"] == 6.99  # cheapest LT Netflix plan (no plan id)
    assert yearly["saving_pct"] == -43.06
    assert yearly["status"] == "expired"
    assert yearly["last_verified"] == "2026-09-01"


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
    assert all(b["monthly_eur"] is None and b["saving_pct"] is None for b in site["bundles"])


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
    assert site["meta"]["display_currencies"] == ["COP", "EUR", "USD"]
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

"""Load and integrity-check catalog/*.yaml and data/curated/*.yaml."""

from __future__ import annotations

import re
from functools import cache
from pathlib import Path

import yaml
from babel.numbers import list_currencies

from haggler.config import repo_root
from haggler.models.catalog import Catalog
from haggler.models.curated import BundlesFile, FeasibilityFile, PaymentInstrumentsFile

KNOWN_ADAPTERS = {
    "netflix_help",
    "tompec_netflix",
    "appstore_iap",
    "disney_web",
    "spotify_premium",
    "icloud_support",
    "apple_marketing",
    "ms_displaycatalog",
}


def _read_yaml(path: Path) -> dict:
    with path.open(encoding="utf-8") as fh:
        return yaml.safe_load(fh) or {}


def load_catalog(catalog_dir: Path | None = None) -> Catalog:
    base = catalog_dir or repo_root() / "catalog"
    services = _read_yaml(base / "services.yaml")
    countries = _read_yaml(base / "countries.yaml")
    return Catalog(
        services=services["services"],
        countries=countries["countries"],
        fx_pins=countries.get("fx_pins", {}),
    )


@cache
def default_catalog() -> Catalog:
    return load_catalog()


def load_feasibility(curated_dir: Path | None = None) -> FeasibilityFile:
    base = curated_dir or repo_root() / "data" / "curated"
    return FeasibilityFile.model_validate(_read_yaml(base / "feasibility.yaml"))


def load_payment_instruments(curated_dir: Path | None = None) -> PaymentInstrumentsFile:
    base = curated_dir or repo_root() / "data" / "curated"
    return PaymentInstrumentsFile.model_validate(_read_yaml(base / "payment_instruments.yaml"))


def load_bundles(curated_dir: Path | None = None) -> BundlesFile:
    base = curated_dir or repo_root() / "data" / "curated"
    return BundlesFile.model_validate(_read_yaml(base / "bundles.yaml"))


def integrity_errors(
    cat: Catalog,
    feasibility: FeasibilityFile | None = None,
    bundles: BundlesFile | None = None,
    instruments: PaymentInstrumentsFile | None = None,
) -> list[str]:
    """Cross-file referential checks that pydantic validation cannot express."""
    errors: list[str] = []
    service_ids = {s.id for s in cat.services}
    ccs = set(cat.country_codes())
    iso_currencies = list_currencies()

    for c in cat.countries:
        for cur in filter(None, (c.currency, c.appstore_currency)):
            if cur not in iso_currencies:
                errors.append(f"country {c.cc}: unknown currency {cur}")
    for cur, provider in cat.fx_pins.items():
        if cur not in cat.currencies():
            errors.append(f"fx_pins: {cur} ({provider}) is not used by any country")

    for s in cat.services:
        for src in s.sources:
            if src.adapter not in KNOWN_ADAPTERS:
                errors.append(f"{s.id}: unknown adapter {src.adapter}")
            listed = [] if src.countries == "all" else src.countries
            for cc in [*listed, *src.exclude]:
                if cc not in ccs:
                    errors.append(f"{s.id}/{src.adapter}: unknown country {cc}")
            if src.channel == "ios_iap" and not any(p.iap_patterns for p in s.plans):
                errors.append(f"{s.id}: ios_iap source but no plan has iap_patterns")
        for p in s.plans:
            lo, hi = p.plausible_monthly_eur
            if not 0 < lo < hi:
                errors.append(f"{s.id}.{p.id}: bad plausible_monthly_eur {p.plausible_monthly_eur}")
            for pat in p.iap_patterns:
                try:
                    re.compile(pat, re.IGNORECASE)
                except re.error as exc:
                    errors.append(f"{s.id}.{p.id}: bad regex {pat!r}: {exc}")

    def plan_ok(service_id: str, plan_id: str | None) -> bool:
        if service_id not in service_ids:
            return False
        return plan_id is None or cat.service(service_id).plan(plan_id) is not None

    if feasibility:
        seen: set[tuple[str, str, str, str]] = set()
        for r in feasibility.records:
            if r.service_id not in service_ids:
                errors.append(f"feasibility {r.id}: unknown service {r.service_id}")
            if r.target_country not in ccs or r.residence_country not in ccs:
                errors.append(f"feasibility {r.id}: unknown country")
            if r.profile not in feasibility.profiles:
                errors.append(f"feasibility {r.id}: unknown profile {r.profile}")
            key = (r.service_id, r.target_country, r.residence_country, r.profile)
            if key in seen:
                errors.append(f"feasibility {r.id}: duplicate record for {key}")
            seen.add(key)

    if bundles:
        ids = [b.bundle_id for b in bundles.bundles]
        if len(ids) != len(set(ids)):
            errors.append("bundles: duplicate bundle_id")
        for b in bundles.bundles:
            if b.country not in ccs:
                errors.append(f"bundle {b.bundle_id}: unknown country {b.country}")
            for comp in b.components:
                if not plan_ok(comp.service_id, comp.plan_id):
                    errors.append(
                        f"bundle {b.bundle_id}: unknown component {comp.service_id}.{comp.plan_id}"
                    )
            if b.price.source == "observed" and not plan_ok(
                b.price.observed_service_id or "", b.price.observed_plan_id
            ):
                errors.append(f"bundle {b.bundle_id}: observed price points to unknown plan")
            if b.price.source == "curated" and b.price.amount is None:
                errors.append(f"bundle {b.bundle_id}: curated price needs an amount")

    if instruments:
        ids = [i.id for i in instruments.instruments]
        if len(ids) != len(set(ids)):
            errors.append("payment_instruments: duplicate id")

    return errors

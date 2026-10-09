"""Load and integrity-check catalog/*.yaml and data/curated/*.yaml."""

from __future__ import annotations

import re
from functools import cache
from pathlib import Path
from urllib.parse import urlsplit

import yaml
from babel.numbers import list_currencies

from haggler.config import repo_root
from haggler.models.catalog import Catalog
from haggler.models.curated import BundlesFile, FeasibilityFile, PaymentInstrumentsFile
from haggler.models.profiles import Profile, ProfilesFile
from haggler.models.research import ReviewFile

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


def load_payment_instruments(
    curated_dir: Path | None = None, *, missing_ok: bool = False
) -> PaymentInstrumentsFile:
    path = (curated_dir or repo_root() / "data" / "curated") / "payment_instruments.yaml"
    if missing_ok and not path.exists():
        return PaymentInstrumentsFile(instruments=[])
    return PaymentInstrumentsFile.model_validate(_read_yaml(path))


def load_bundles(curated_dir: Path | None = None) -> BundlesFile:
    base = curated_dir or repo_root() / "data" / "curated"
    return BundlesFile.model_validate(_read_yaml(base / "bundles.yaml"))


def _curated_dir(curated_dir: Path | None) -> Path:
    return curated_dir or repo_root() / "data" / "curated"


def load_profiles(curated_dir: Path | None = None) -> ProfilesFile | None:
    """data/curated/profiles.yaml, or None when the file does not exist."""
    path = _curated_dir(curated_dir) / "profiles.yaml"
    if not path.exists():
        return None
    return ProfilesFile.model_validate(_read_yaml(path))


def synthesize_profiles(
    feasibility: FeasibilityFile, *, residence: str, profile: str
) -> ProfilesFile:
    """Fallback without profiles.yaml: one profile from feasibility.yaml's description, with no
    capabilities beyond living in `residence` (research verdicts stay conservative)."""
    return ProfilesFile(
        default=profile,
        profiles=[
            Profile(
                id=profile,
                name_es=profile,
                description_es=" ".join(feasibility.profiles.get(profile, "").split()),
                residence_country=residence,
                address_countries=[residence],
            )
        ],
    )


def load_review(curated_dir: Path | None = None) -> ReviewFile:
    """data/curated/research_review.yaml (empty when the file does not exist)."""
    path = _curated_dir(curated_dir) / "research_review.yaml"
    if not path.exists():
        return ReviewFile()
    return ReviewFile.model_validate(_read_yaml(path))


_DOMAIN = re.compile(r"^(?!-)[a-z0-9-]+(\.[a-z0-9-]+)+$")


def _domain_errors(owner: str, domains: list[str], urls: list[str] = ()) -> list[str]:
    errors = [f"{owner}: bad official domain {d!r}" for d in domains if not _DOMAIN.fullmatch(d)]
    for url in urls:
        parts = urlsplit(url)
        host = (parts.hostname or "").lower()
        if parts.scheme != "https" or not any(host == d or host.endswith("." + d) for d in domains):
            errors.append(f"{owner}: research url {url!r} is not https on an official domain")
    return errors


def integrity_errors(
    cat: Catalog,
    feasibility: FeasibilityFile | None = None,
    bundles: BundlesFile | None = None,
    instruments: PaymentInstrumentsFile | None = None,
    profiles: ProfilesFile | None = None,
    review: ReviewFile | None = None,
) -> list[str]:
    """Cross-file referential checks that pydantic validation cannot express. (The review log is
    append-only and the last line per claim and hash wins, so repeated lines are valid.)"""
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
        errors.extend(_domain_errors(s.id, s.official_domains, s.research_urls))
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

    instrument_ids: set[str] = set()
    if instruments:
        ids = [i.id for i in instruments.instruments]
        instrument_ids = set(ids)
        if len(ids) != len(set(ids)):
            errors.append("payment_instruments: duplicate id")
        for i in instruments.instruments:
            errors.extend(_domain_errors(f"instrument {i.id}", i.official_domains, i.research_urls))
            for cc in i.required_id_countries:
                if cc not in ccs:
                    errors.append(f"instrument {i.id}: unknown country {cc}")

    if profiles:
        for p in profiles.profiles:
            for cc in sorted(p.countries() - ccs):
                errors.append(f"profile {p.id}: unknown country {cc}")
            if instruments:
                for iid in p.instruments_held:
                    if iid not in instrument_ids:
                        errors.append(f"profile {p.id}: unknown instrument {iid}")
        if feasibility:
            for pid in feasibility.profiles:
                if profiles.get(pid) is None:
                    errors.append(f"feasibility profile {pid} is not in profiles.yaml")
            for r in feasibility.records:
                p = profiles.get(r.profile)
                if p is not None and p.residence_country != r.residence_country:
                    errors.append(
                        f"feasibility {r.id}: residence {r.residence_country} differs from "
                        f"profile {p.id} ({p.residence_country})"
                    )

    return errors

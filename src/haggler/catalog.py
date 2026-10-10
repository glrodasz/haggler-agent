"""Load and integrity-check catalog/*.yaml and data/curated/*.yaml."""

from __future__ import annotations

import re
from collections.abc import Callable, Iterable
from functools import cache
from pathlib import Path
from urllib.parse import urlsplit

import yaml
from babel.numbers import list_currencies

from haggler.config import repo_root
from haggler.dates import find_dates
from haggler.models.catalog import Catalog
from haggler.models.curated import (
    Bundle,
    BundlesFile,
    FeasibilityFile,
    PaymentInstrumentsFile,
    Provider,
)
from haggler.models.profiles import Profile, ProfilesFile
from haggler.models.research import MAX_QUOTE, ReviewFile
from haggler.money import format_money_code
from haggler.research.evidence import MIN_QUOTE, public_host
from haggler.research.values import ValueKind, check_value

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


@cache
def default_bundles() -> BundlesFile:
    return load_bundles()


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


# The path part of a provider domain entry ("www.apple.com/se/"): plain segments, ending in "/".
_PATH_PREFIX = re.compile(r"^(?:/[A-Za-z0-9._~%-]+)+/$")


def _split_domain(entry: str) -> tuple[str, str | None]:
    """A domain entry is a host, or a host followed by a path prefix ("www.apple.com/se/")."""
    host, slash, path = entry.partition("/")
    return host, (slash + path if slash else None)


def on_domains(url: str, domains: Iterable[str]) -> bool:
    """https on one of `domains` or a subdomain of one; an entry with a path prefix also needs
    the URL's path under that prefix (one host serving several countries)."""
    parts = urlsplit(url)
    if parts.scheme != "https":
        return False
    host = (parts.hostname or "").lower()
    for entry in domains:
        d, prefix = _split_domain(entry)
        if not (host == d or host.endswith("." + d)):
            continue
        if prefix is None or parts.path == prefix[:-1] or parts.path.startswith(prefix):
            return True
    return False


def _domain_errors(owner: str, domains: list[str], urls: list[str] = ()) -> list[str]:
    errors = [f"{owner}: bad official domain {d!r}" for d in domains if not _DOMAIN.fullmatch(d)]
    for url in urls:
        if not on_domains(url, domains):
            errors.append(f"{owner}: research url {url!r} is not https on an official domain")
    return errors


# ------------------------------------------------------------------ bundles (v2)

# Anchors are literal excerpts copied in full: an ellipsis means the text was shortened.
_ANCHOR_ELLIPSIS = re.compile(r"\.\.\.|…")
_VALUED_INCLUSIONS = ("discount_pct", "discount_abs", "free_months")


def _provider_errors(bundles: BundlesFile, ccs: set[str]) -> list[str]:
    errors: list[str] = []
    ids = [p.id for p in bundles.providers]
    if len(ids) != len(set(ids)):
        errors.append("bundles: duplicate provider id")
    for p in bundles.providers:
        for cc, domains in p.domains.items():
            owner = f"provider {p.id}/{cc}"
            if cc not in ccs:
                errors.append(f"provider {p.id}: unknown country {cc}")
            if not domains:
                errors.append(f"{owner}: no domains")
            for entry in domains:
                host, prefix = _split_domain(entry)
                errors.extend(_domain_errors(owner, [host]))
                if not public_host(host):
                    errors.append(f"{owner}: {host!r} is not a public host")
                if prefix is not None and not _PATH_PREFIX.fullmatch(prefix):
                    errors.append(
                        f"{owner}: bad path prefix {prefix!r} in {entry!r} (plain segments "
                        "ending in '/')"
                    )
    return errors


def _bundle_url_errors(who: str, b: Bundle, provider: Provider) -> list[str]:
    """Every page of a bundle is https on its provider's domains for the BUNDLE's country (a
    Mexican "$" page never confirms a Colombian price)."""
    domains = provider.domains.get(b.country, [])
    urls = [("official_url", str(b.official_url))]
    for page in b.watch.pages:
        urls.append(("watch page", str(page.url)))
        if page.index_url is not None:
            urls.append(("watch index_url", str(page.index_url)))
    urls.extend(("anchor url", str(a.url)) for a in b.watch.anchors)
    return [
        f"{who}: {what} {url!r} is not https on a {b.country} domain of provider {provider.id}"
        for what, url in urls
        if not on_domains(url, domains)
    ]


def _watch_errors(who: str, b: Bundle) -> list[str]:
    errors: list[str] = []
    watch = b.watch
    if watch.mode == "auto" and not watch.pages:
        errors.append(f"{who}: watch.mode auto needs at least one page")
    for page in watch.pages:
        if page.link_pattern is None:
            continue
        if page.index_url is None:
            errors.append(f"{who}: link_pattern of {page.url} needs an index_url")
        try:
            pattern = re.compile(page.link_pattern)
        except re.error as exc:
            errors.append(f"{who}: bad link_pattern {page.link_pattern!r}: {exc}")
            continue
        if "edition" not in pattern.groupindex:
            errors.append(f"{who}: link_pattern {page.link_pattern!r} has no group 'edition'")
        elif not pattern.search(str(page.url)):
            # The page url is the curated edition the watcher compares the newest one with.
            errors.append(f"{who}: link_pattern does not match its own url {page.url}")
    # An anchor quotes a watch page, or the index page of a versioned PDF.
    pages = {str(p.url) for p in watch.pages}
    pages |= {str(p.index_url) for p in watch.pages if p.index_url is not None}
    component_ids = {c.service_id for c in b.components}
    for a in watch.anchors:
        text = " ".join(a.text.split())
        if not MIN_QUOTE <= len(text) <= MAX_QUOTE:
            errors.append(
                f"{who}: {a.fact} anchor has {len(text)} characters "
                f"(between {MIN_QUOTE} and {MAX_QUOTE})"
            )
        if _ANCHOR_ELLIPSIS.search(text):
            errors.append(f"{who}: {a.fact} anchor has an ellipsis (copy the excerpt in full)")
        if str(a.url) not in pages:
            errors.append(f"{who}: {a.fact} anchor url {a.url} is not one of the watch pages")
        if a.fact == "component" and a.service_id not in component_ids:
            errors.append(f"{who}: component anchor needs the service_id of a component")
    return errors


_PRICE_FACTS: dict[str, tuple[ValueKind, str]] = {
    "price": ("money", "amount"),
    "base_price": ("base_money", "base_plan_amount"),
    "intro": ("intro_money", "intro_amount"),
}
_COUNTED_INCLUSIONS = ("discount_pct", "discount_abs", "free_months")


def _anchor_value_errors(
    who: str, b: Bundle, bundles: BundlesFile, cat: Catalog, provider: Provider | None
) -> list[str]:
    """Each anchor states the value it confirms, checked on its text alone (offline, no page
    context): a price anchor the YAML amount (haggler.research.values.check_value: amount,
    currency, period, role, tier name), a component anchor its service (and the number of a
    discount), a validity anchor a date with a year (one of the YAML's valid_from, valid_until
    or intro_valid_until when one is set)."""
    errors: list[str] = []
    price = b.price
    for a in b.watch.anchors:
        text = " ".join(a.text.split())
        if a.fact in _PRICE_FACTS:
            kind, attr = _PRICE_FACTS[a.fact]
            amount = getattr(price, attr)
            if amount is None:
                if a.fact != "price":  # an observed price has no YAML amount to compare
                    errors.append(f"{who}: {a.fact} anchor needs price.{attr} to confirm")
                continue
            result = check_value(
                kind,
                amount,
                text,
                country=b.country,
                currency=price.currency,
                period=price.period,
                provider=provider,
                bundle=b,
                bundles=bundles.bundles,
                catalog=cat,
            )
            if not result.ok:
                value = format_money_code(amount, price.currency, price.period)
                errors.append(f"{who}: {a.fact} anchor does not state {value}: {result.reason}")
        elif a.fact == "component":
            comp = next((c for c in b.components if c.service_id == a.service_id), None)
            if comp is None or a.service_id not in {s.id for s in cat.services}:
                continue  # reported by _watch_errors / _bundle_errors
            value = comp.service_id + (f".{comp.plan_id}" if comp.plan_id else "")
            if comp.inclusion in _COUNTED_INCLUSIONS and comp.value is not None:
                value = f"{comp.service_id}:{comp.value}"
            result = check_value(
                "component", value, text, country=b.country, currency=price.currency, catalog=cat
            )
            if not result.ok:
                errors.append(f"{who}: component anchor does not state {value}: {result.reason}")
        elif a.fact == "validity":
            found = {d.value for d in find_dates(text)}
            dates = (b.valid_from, b.valid_until, price.intro_valid_until)
            wanted = {d for d in dates if d is not None}
            if not found:
                errors.append(f"{who}: validity anchor states no date with a year")
            elif wanted and not found & wanted:
                listed = " or ".join(sorted(d.isoformat() for d in wanted))
                errors.append(f"{who}: validity anchor does not state {listed}")
    return errors


def _price_errors(who: str, b: Bundle, cat: Catalog) -> list[str]:
    errors: list[str] = []
    price = b.price
    if b.country in cat.country_codes():
        currency = cat.country(b.country).currency
        if price.currency != currency:
            errors.append(f"{who}: price currency {price.currency} is not {b.country}'s {currency}")
    if price.source == "curated" and price.amount is None:
        errors.append(f"{who}: curated price needs an amount")
    if price.tax_included is None:
        # bundles.json states it as a fact (a boolean); only the YAML can say which.
        errors.append(f"{who}: price needs tax_included (true or false, as the page states it)")
    if price.source == "observed" and price.amount is not None:
        errors.append(f"{who}: observed price takes its amount from the daily data, not the YAML")
    amount, base, marginal = price.amount, price.base_plan_amount, price.marginal_amount
    if marginal is not None and base is None:
        errors.append(f"{who}: marginal_amount needs the base_plan_amount it is computed from")
    if base is not None and not price.base_plan_name:
        errors.append(f"{who}: base_plan_amount needs the base_plan_name the page prints")
    if (
        amount is not None
        and base is not None
        and marginal is not None
        and marginal != amount - base
    ):
        errors.append(
            f"{who}: marginal_amount {marginal} is not amount {amount} - base_plan_amount {base}"
        )
    return errors


def _component_errors(who: str, b: Bundle) -> list[str]:
    errors: list[str] = []
    groups: dict[str, list[int]] = {}
    for c in b.components:
        if c.inclusion in _VALUED_INCLUSIONS:
            if c.value is None or c.value <= 0:
                errors.append(f"{who}: {c.inclusion} component {c.service_id} needs a value > 0")
            elif c.inclusion == "discount_pct" and c.value > 100:
                errors.append(f"{who}: discount_pct of {c.service_id} is over 100")
        if c.inclusion == "choose_n":
            if c.choice_group_id is None or c.choose_n is None:
                errors.append(
                    f"{who}: choose_n component {c.service_id} needs choice_group_id and choose_n"
                )
            else:
                groups.setdefault(c.choice_group_id, []).append(c.choose_n)
    for group, ns in groups.items():
        if len(set(ns)) > 1:
            errors.append(f"{who}: choice group {group} has different choose_n values {sorted(ns)}")
        for n in sorted(set(ns)):
            if not 1 <= n <= len(ns):
                errors.append(f"{who}: choice group {group} has {len(ns)} options, choose_n {n}")
    return errors


def _verification_errors(who: str, b: Bundle) -> list[str]:
    errors: list[str] = []
    anchors = b.watch.anchors
    if b.verified_by == "official_fetch":
        # An observed price comes from the daily data, which stands in for the price anchor.
        if b.price.source == "curated" and not any(a.fact == "price" for a in anchors):
            errors.append(f"{who}: verified_by official_fetch needs a price anchor")
        anchored = {a.service_id for a in anchors if a.fact == "component"}
        for c in b.components:
            if c.inclusion == "included" and c.service_id not in anchored:
                errors.append(
                    f"{who}: verified_by official_fetch needs a component anchor for {c.service_id}"
                )
    if b.verified_by == "user_report" and b.user_report is None:
        errors.append(f"{who}: verified_by user_report needs user_report {{date, note}}")
    return errors


def _verdict_errors(who: str, b: Bundle, profiles: ProfilesFile | None) -> list[str]:
    errors: list[str] = []
    seen: set[str] = set()
    for v in b.verdicts:
        if v.profile in seen:
            errors.append(f"{who}: two verdicts for profile {v.profile}")
        seen.add(v.profile)
        if profiles is None:
            continue
        p = profiles.get(v.profile)
        if p is None:
            errors.append(f"{who}: verdict for unknown profile {v.profile}")
        elif p.residence_country == b.country:
            errors.append(
                f"{who}: verdict for profile {v.profile}, who lives in {b.country} "
                "(a purchase at home needs no curated verdict)"
            )
    return errors


def _bundle_errors(
    cat: Catalog,
    bundles: BundlesFile,
    *,
    plan_ok: Callable[[str, str | None], bool],
    instrument_ids: set[str],
    profiles: ProfilesFile | None,
) -> list[str]:
    errors: list[str] = []
    ccs = set(cat.country_codes())
    service_ids = {s.id for s in cat.services}
    ids = [b.bundle_id for b in bundles.bundles]
    if len(ids) != len(set(ids)):
        errors.append("bundles: duplicate bundle_id")
    errors.extend(_provider_errors(bundles, ccs))
    by_url: dict[str, list[Bundle]] = {}
    for b in bundles.bundles:
        who = f"bundle {b.bundle_id}"
        by_url.setdefault(str(b.official_url), []).append(b)
        if b.country not in ccs:
            errors.append(f"{who}: unknown country {b.country}")
        if b.bundle_id in service_ids:
            errors.append(f"{who}: bundle_id is also a service id")
        if b.bundle_id in instrument_ids:
            errors.append(f"{who}: bundle_id is also a payment instrument id")
        for comp in b.components:
            if not plan_ok(comp.service_id, comp.plan_id):
                errors.append(f"{who}: unknown component {comp.service_id}.{comp.plan_id}")
        for sid in b.covers or []:
            if sid not in service_ids:
                errors.append(f"{who}: covers unknown service {sid}")
        if b.price.source == "observed" and not plan_ok(
            b.price.observed_service_id or "", b.price.observed_plan_id
        ):
            errors.append(f"{who}: observed price points to unknown plan")
        if b.valid_from and b.valid_until and b.valid_until < b.valid_from:
            errors.append(f"{who}: valid_until {b.valid_until} is before valid_from {b.valid_from}")
        provider = bundles.provider(b.provider_id)
        if provider is None:
            errors.append(f"{who}: unknown provider {b.provider_id}")
        else:
            errors.extend(_bundle_url_errors(who, b, provider))
            if provider.automated_access == "forbidden" and b.watch.mode != "off":
                errors.append(
                    f"{who}: provider {provider.id} forbids automated access, so watch.mode "
                    "must be off"
                )
        errors.extend(_watch_errors(who, b))
        errors.extend(_anchor_value_errors(who, b, bundles, cat, provider))
        errors.extend(_price_errors(who, b, cat))
        errors.extend(_component_errors(who, b))
        errors.extend(_verification_errors(who, b))
        errors.extend(_verdict_errors(who, b, profiles))
    # Tiers sold on one page are told apart by the names the page prints.
    for url, group in by_url.items():
        if len(group) < 2:
            continue
        owners: dict[str, str] = {}
        for b in group:
            if not b.aliases:
                errors.append(f"bundle {b.bundle_id}: needs aliases (shares {url})")
            for alias in b.aliases:
                owner = owners.setdefault(alias.casefold(), b.bundle_id)
                if owner != b.bundle_id:
                    errors.append(f"bundle {b.bundle_id}: alias {alias!r} is also {owner}'s")
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
        errors.extend(
            _bundle_errors(
                cat,
                bundles,
                plan_ok=plan_ok,
                instrument_ids={i.id for i in instruments.instruments} if instruments else set(),
                profiles=profiles,
            )
        )

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

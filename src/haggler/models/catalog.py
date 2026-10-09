"""Catalog configuration: services, plans, sources and countries (loaded from catalog/*.yaml)."""

from __future__ import annotations

import re
import unicodedata
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

Channel = Literal["web", "ios_iap"]
SourceKind = Literal["official", "secondary"]
EuGeoClass = Literal[
    "audiovisual_excluded",
    "copyright_art3_5_only",
    "eservice_art4_1b",
    "software_unclear",
]
Category = Literal["video", "music", "cloud", "bundle", "productivity", "gaming", "other"]
Region = Literal[
    "nordics", "eu", "europe_other", "latam", "north_america", "asia", "africa", "mena"
]


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class PlanDef(_Strict):
    id: str
    label_es: str
    label_en: str
    rank: int = 0
    ads: bool = False
    plausible_monthly_eur: tuple[float, float]
    # Lower-case strings that identify the plan on official web pages.
    aliases: list[str] = Field(default_factory=list)
    # Regexes (case-insensitive) that map App Store in-app purchase names to this plan.
    iap_patterns: list[str] = Field(default_factory=list)

    @field_validator("iap_patterns")
    @classmethod
    def _compile(cls, patterns: list[str]) -> list[str]:
        for p in patterns:
            re.compile(p, re.IGNORECASE)
        return patterns


class SourceDef(_Strict):
    adapter: str
    channel: Channel = "web"
    kind: SourceKind = "official"
    params: dict[str, Any] = Field(default_factory=dict)
    countries: Literal["all"] | list[str] = "all"
    exclude: list[str] = Field(default_factory=list)

    def applies_to(self, cc: str) -> bool:
        if cc in self.exclude:
            return False
        return self.countries == "all" or cc in self.countries


class ServiceDef(_Strict):
    id: str
    name: str
    category: Category
    eu_geo_class: EuGeoClass
    default_plan: str
    homepage: str
    plans: list[PlanDef]
    sources: list[SourceDef] = Field(default_factory=list)
    # Hosts (and their subdomains) whose pages count as the service's own sources for research.
    official_domains: list[str] = Field(default_factory=list)
    notes_es: str = ""

    @model_validator(mode="after")
    def _default_plan_exists(self) -> ServiceDef:
        ids = [p.id for p in self.plans]
        if len(ids) != len(set(ids)):
            raise ValueError(f"{self.id}: duplicate plan ids")
        if self.default_plan not in ids:
            raise ValueError(f"{self.id}: default_plan {self.default_plan!r} not in plans")
        return self

    def plan(self, plan_id: str) -> PlanDef | None:
        return next((p for p in self.plans if p.id == plan_id), None)


class VatIncluded(_Strict):
    web: bool | None = None
    ios_iap: bool | None = None


class CountryDef(_Strict):
    cc: str = Field(pattern=r"^[A-Z]{2}$")
    name_es: str
    name_en: str
    # Extra English spellings used by vendor pages ("Türkiye", "Viet Nam" ...).
    aliases_en: list[str] = Field(default_factory=list)
    region: Region
    currency: str = Field(pattern=r"^[A-Z]{3}$")
    # App Store storefront currency when it differs from the local one (e.g. AR bills in USD).
    appstore_currency: str | None = Field(default=None, pattern=r"^[A-Z]{3}$")
    locale: str
    eu_eea: bool
    vat_included: VatIncluded = VatIncluded()
    # Path segment on apple.com ("" = US, "uk" = GB); None = not configured.
    apple_path: str | None = None
    # Locale segment on disneyplus.com ("sv-se"); None = not configured.
    disney_locale: str | None = None

    def expected_currencies(self, channel: Channel) -> list[str]:
        if channel == "ios_iap" and self.appstore_currency:
            return [self.appstore_currency]
        return [self.currency]


class Catalog(_Strict):
    services: list[ServiceDef]
    countries: list[CountryDef]
    # Currency -> Frankfurter provider code that must be used for that currency.
    fx_pins: dict[str, str] = Field(default_factory=dict)

    @model_validator(mode="after")
    def _unique(self) -> Catalog:
        for label, ids in (
            ("service", [s.id for s in self.services]),
            ("country", [c.cc for c in self.countries]),
        ):
            if len(ids) != len(set(ids)):
                raise ValueError(f"duplicate {label} ids")
        return self

    def service(self, service_id: str) -> ServiceDef:
        for s in self.services:
            if s.id == service_id:
                return s
        raise KeyError(service_id)

    def country(self, cc: str) -> CountryDef:
        for c in self.countries:
            if c.cc == cc:
                return c
        raise KeyError(cc)

    def country_codes(self) -> list[str]:
        return [c.cc for c in self.countries]

    def currencies(self) -> set[str]:
        out = {c.currency for c in self.countries}
        out |= {c.appstore_currency for c in self.countries if c.appstore_currency}
        return out

    def country_by_name(self, name: str) -> CountryDef | None:
        key = _norm_name(name)
        for c in self.countries:
            if key in {_norm_name(n) for n in (c.name_en, c.name_es, *c.aliases_en)}:
                return c
        return None


def _norm_name(name: str) -> str:
    ascii_name = unicodedata.normalize("NFKD", name).encode("ascii", "ignore").decode()
    return re.sub(r"[^a-z]", "", ascii_name.lower().replace("&", "and"))

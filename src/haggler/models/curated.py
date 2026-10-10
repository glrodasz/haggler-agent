"""Hand-curated datasets (data/curated/*.yaml): feasibility, payment instruments, bundles.

Feasibility keeps legality, ToS compliance and practicality as SEPARATE axes; the display
label is derived deterministically (see haggler.feasibility) and is never stored.

Bundles (v2): the YAML is the only source of a bundle's values (price, base plan, components,
dates, requirements); no job writes it. Whether a value is confirmed is derived later from the
watcher's evidence, so the file never types a verified state: `status` is unverified|expired and
bundle evidence carries no `verified`/`retrieved_at`. Cross-file rules (provider domains, anchors,
price arithmetic, verdict profiles) live in haggler.catalog.integrity_errors.
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, HttpUrl, field_validator

CuratedStatus = Literal["verified", "unverified", "expired"]
Confidence = Literal["low", "medium", "high"]
VerifiedBy = Literal["official_fetch", "search_summary", "user_report", "agent_research"]


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid")


class Evidence(_Strict):
    url: HttpUrl
    kind: Literal["official", "press", "aggregator", "forum", "user_report"]
    retrieved_at: date | None = None
    note: str = ""
    # Literal excerpt of the page, and whether our own fetch found it there.
    quote: str = ""
    verified: bool = False


# ---------------------------------------------------------------- feasibility

Legality = Literal["lawful", "contestable_eu_right", "requires_misrepresentation"]
TosCompliance = Literal["compliant", "breach_unenforced", "breach_enforced"]
Practicality = Literal["remote", "presence_once", "presence_periodic", "impossible"]
EnforcementLevel = Literal["none_known", "reported", "systematic"]
StepType = Literal[
    "store_region",
    "billing_address",
    "payment_country",
    "presence",
    "gift_card",
    "local_id",
    "local_phone",
    "cancel_and_resubscribe",
    "obtain_instrument",
    "declare_country",
]


class FeasibilityStep(_Strict):
    type: StepType
    value: str
    requires_presence: bool = False
    requires_false_declaration: bool = False


class Reversibility(_Strict):
    cooldown_days: int | None = None
    permanent: bool = False
    collateral: list[
        Literal["storefront_wide", "balance_forfeit", "region_locked_purchases", "family_group"]
    ] = Field(default_factory=list)


class EnforcementEvent(_Strict):
    date: date
    url: HttpUrl
    summary: str


class Enforcement(_Strict):
    level: EnforcementLevel = "none_known"
    events: list[EnforcementEvent] = Field(default_factory=list)


class TosClause(_Strict):
    url: HttpUrl
    quote: str


class LegalBasis(_Strict):
    instrument: str
    article: str
    strength: Literal["textual", "arguable", "none"]
    tested: bool = False


class FeasibilityRecord(_Strict):
    id: str
    service_id: str
    target_country: str
    residence_country: str
    profile: str
    steps: list[FeasibilityStep] = Field(default_factory=list)
    legality: Legality
    tos: TosCompliance
    practical: Practicality
    reversibility: Reversibility = Reversibility()
    enforcement: Enforcement = Enforcement()
    tos_clause: TosClause | None = None
    legal_basis: list[LegalBasis] = Field(default_factory=list)
    confidence: Confidence = "low"
    status: CuratedStatus = "unverified"
    verified_by: VerifiedBy = "search_summary"
    last_verified: date
    notes_es: str = ""
    evidence: list[Evidence] = Field(default_factory=list)


class FeasibilityFile(_Strict):
    profiles: dict[str, str]
    records: list[FeasibilityRecord]


# ---------------------------------------------------------- payment instruments


class ObservedBin(_Strict):
    value: str | None = None
    sample_size: int = 0
    last_seen: date | None = None


class PaymentInstrument(_Strict):
    id: str
    name: str
    issuer_entity: str
    issuer_country: str
    iban_country: str | None = None
    eligible_residence: list[str]
    onboarding_remote: bool
    # Countries whose ID documents the issuer accepts ([] = no restriction recorded).
    required_id_countries: list[str] = Field(default_factory=list)
    # Hosts whose pages count as the issuer's own (official) sources for research.
    official_domains: list[str] = Field(default_factory=list)
    # Known official pages where research quotes are also looked for.
    research_urls: list[str] = Field(default_factory=list)
    currencies: list[str] = Field(default_factory=list)
    # Never inferred: only filled from real observations of issued cards.
    card_bin_country_observed: ObservedBin = ObservedBin()
    fx_markup_pct: float | None = None
    status: CuratedStatus = "unverified"
    evidence: list[Evidence] = Field(default_factory=list)
    notes_es: str = ""


class PaymentInstrumentsFile(_Strict):
    instruments: list[PaymentInstrument]


# -------------------------------------------------------------------- bundles

ProviderType = Literal[
    "marketplace",
    "super_app",
    "telco_mobile",
    "telco_fixed_tv",
    "isp",
    "bank",
    "card_issuer",
    "platform",
    "streaming_joint",
    "retail_membership",
]


class BundlePrice(_Strict):
    amount: Decimal | None = None
    currency: str
    period: Literal["month", "year"] = "month"
    tax_included: bool | None = None
    intro_amount: Decimal | None = None
    intro_months: int | None = None
    intro_valid_until: date | None = None
    contract_months: int | None = None
    # The plan the bundle is sold on top of (telco add-ons), as the provider's page names it.
    base_plan_name: str | None = None
    base_plan_amount: Decimal | None = None
    # Extra cost on top of the base plan the user would pay anyway: amount - base_plan_amount.
    marginal_amount: Decimal | None = None
    # "observed" = taken from the daily price dataset (e.g. Apple One) instead of typed here.
    source: Literal["curated", "observed"] = "curated"
    observed_service_id: str | None = None
    observed_plan_id: str | None = None


class BundleComponent(_Strict):
    service_id: str
    plan_id: str | None = None
    inclusion: Literal["included", "discount_pct", "discount_abs", "free_months", "choose_n"]
    value: Decimal | None = None
    choice_group_id: str | None = None
    choose_n: int | None = None
    ads: bool | None = None


# A condition the provider imposes: "required", "not_required", or "unknown" (not established).
Requirement = Literal["required", "not_required", "unknown"]


class BundleRequirements(_Strict):
    """What the provider asks of a subscriber, in the bundle's country. "unknown" (or None) is
    never read as "not required": an unknown requirement keeps a verdict unknown, never better."""

    # Living in the bundle's country (a residence clause in the terms).
    residency: Requirement = "unknown"
    # Local ID document the signup asks for ("cedula", "CURP", "personnummer" ...; "none").
    local_id: str = "unknown"
    local_line: Literal["none", "mobile", "fixed", "both", "unknown"] = "unknown"
    local_payment: Literal["any_card", "local_card", "local_bank", "carrier_bill", "unknown"] = (
        "unknown"
    )
    credit_check: bool | None = None
    # A billing address in the bundle's country.
    billing_address: Requirement = "unknown"
    # Whether the provider checks that the subscriber is IN the bundle's country when signing
    # up. Same values as the research field signup_location (and its bundle_ twin), so curated
    # and researched requirements compare without a translation.
    signup_presence: Literal["not_checked", "checked", "unknown"] = "unknown"
    # The offer is only for new customers of the provider (None = unknown).
    new_customers_only: bool | None = None


class BundleFeasibility(_Strict):
    purchasable_remotely: Literal["yes", "no", "unknown"] = "unknown"
    usable_abroad: Literal["yes", "travel_only", "no", "unknown"] = "unknown"
    tos_risk: Literal["low", "medium", "high", "unknown"] = "unknown"
    evidence: Literal["user_report", "terms", "press", "none"] = "none"


class Provider(_Strict):
    id: str
    name: str
    # Country -> registrable domains or exact hosts (subdomains included) of the provider's
    # official site FOR THAT COUNTRY. A bundle's pages must be on its own country's domains. When
    # one host serves several countries under a path, the entry carries that path prefix
    # ("www.apple.com/se/"): a URL then needs the host AND a path under the prefix.
    domains: dict[str, list[str]]
    # "forbidden" when the provider's terms forbid automated access: its bundles stay watch.mode
    # off and are only checked by hand.
    automated_access: Literal["allowed", "forbidden"] = "allowed"


class WatchPage(_Strict):
    url: HttpUrl
    kind: Literal["html", "pdf"] = "html"
    # Versioned PDF price lists: the page that links every edition, and a regex over the links
    # with a named group "edition" (the newest edition is the current one).
    index_url: HttpUrl | None = None
    link_pattern: str | None = None


AnchorFact = Literal["price", "base_price", "intro", "component", "validity"]


class Anchor(_Strict):
    """A literal excerpt copied from OUR extraction of a watch page (the bundles probe's output),
    which the watcher looks for to confirm one value of the bundle."""

    fact: AnchorFact
    # The component the excerpt names (fact "component" only).
    service_id: str | None = None
    url: HttpUrl
    text: str


class BundleWatch(_Strict):
    # auto = the weekly watcher reads the pages; manual = checked by hand; off = not watched.
    mode: Literal["auto", "manual", "off"] = "off"
    pages: list[WatchPage] = Field(default_factory=list)
    anchors: list[Anchor] = Field(default_factory=list)


class UserReport(_Strict):
    """A person checked the offer by hand (shown as a manual verification, never as official)."""

    date: date
    note: str = ""


class BundleVerdictRecord(_Strict):
    """A curated feasibility verdict of one bundle for one profile (data/curated/profiles.yaml).
    Same axes, steps and rank rule as FeasibilityRecord (haggler.feasibility.axes_rank); the
    target country is the bundle's and the residence the profile's, so neither is stored. The
    rank is derived, never typed."""

    profile: str
    legality: Legality
    tos: TosCompliance
    practical: Practicality
    steps: list[FeasibilityStep] = Field(default_factory=list)
    reasons_es: list[str] = Field(default_factory=list)
    tos_clause: TosClause | None = None
    legal_basis: list[LegalBasis] = Field(default_factory=list)
    confidence: Confidence = "low"
    status: Literal["unverified", "verified"] = "unverified"
    last_verified: date
    notes_es: str = ""


# A bundle's state is derived (watcher, user report, dates): the YAML may only say that it is
# not verified yet, or that the offer ended.
BundleStatus = Literal["unverified", "expired"]


class Bundle(_Strict):
    bundle_id: str
    provider_id: str
    provider_type: ProviderType
    country: str
    name: str
    tier: str
    # Tier names as the official page prints them (required when bundles share official_url).
    aliases: list[str] = Field(default_factory=list)
    official_url: HttpUrl
    price: BundlePrice
    components: list[BundleComponent]
    # Service ids the bundle stands in for; None = its included components.
    covers: list[str] | None = None
    requirements: BundleRequirements = BundleRequirements()
    # DEPRECATED: the v1 per-residence summary, still read by publish (bundles.json v1). Removed
    # in milestone H2, when per-profile verdicts (`verdicts`) replace it.
    feasibility_from: dict[str, BundleFeasibility] = Field(default_factory=dict)
    evidence: list[Evidence] = Field(default_factory=list)
    status: BundleStatus = "unverified"
    valid_from: date | None = None
    valid_until: date | None = None
    # Where the values come from: search_summary unless said otherwise (a stronger source is
    # never assumed).
    verified_by: VerifiedBy = "search_summary"
    user_report: UserReport | None = None
    watch: BundleWatch = BundleWatch()
    verdicts: list[BundleVerdictRecord] = Field(default_factory=list)
    last_verified: date
    confidence: Confidence = "low"
    notes_es: str = ""

    @field_validator("evidence")
    @classmethod
    def _no_typed_verification(cls, evidence: list[Evidence]) -> list[Evidence]:
        for e in evidence:
            if e.verified or e.retrieved_at is not None:
                raise ValueError(
                    f"evidence {e.url}: verified/retrieved_at are derived by the watcher, "
                    "never typed in bundles.yaml"
                )
        return evidence

    def covered_services(self) -> list[str]:
        if self.covers is not None:
            return list(self.covers)
        return [c.service_id for c in self.components if c.inclusion == "included"]


class BundlesFile(_Strict):
    providers: list[Provider] = Field(default_factory=list)
    bundles: list[Bundle]

    def provider(self, provider_id: str) -> Provider | None:
        return next((p for p in self.providers if p.id == provider_id), None)

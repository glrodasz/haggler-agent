"""Hand-curated datasets (data/curated/*.yaml): feasibility, payment instruments, bundles.

Feasibility keeps legality, ToS compliance and practicality as SEPARATE axes; the display
label is derived deterministically (see haggler.feasibility) and is never stored.
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, HttpUrl

CuratedStatus = Literal["verified", "unverified", "expired"]
Confidence = Literal["low", "medium", "high"]
VerifiedBy = Literal["official_fetch", "search_summary", "user_report"]


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid")


class Evidence(_Strict):
    url: HttpUrl
    kind: Literal["official", "press", "aggregator", "forum", "user_report"]
    retrieved_at: date | None = None
    note: str = ""


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
    promo_valid_until: date | None = None
    contract_months: int | None = None
    requires_base_plan_id: str | None = None
    # Extra cost on top of a base plan the user would pay anyway (e.g. telco add-ons).
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


class BundleRequirements(_Strict):
    residency: str | None = None
    local_id: str = "unknown"
    local_line: Literal["none", "mobile", "fixed", "both", "unknown"] = "unknown"
    local_payment: Literal["any_card", "local_card", "local_bank", "carrier_bill", "unknown"] = (
        "unknown"
    )
    credit_check: bool | None = None


class BundleFeasibility(_Strict):
    purchasable_remotely: Literal["yes", "no", "unknown"] = "unknown"
    usable_abroad: Literal["yes", "travel_only", "no", "unknown"] = "unknown"
    tos_risk: Literal["low", "medium", "high", "unknown"] = "unknown"
    evidence: Literal["user_report", "terms", "press", "none"] = "none"


class Bundle(_Strict):
    bundle_id: str
    provider_id: str
    provider_type: ProviderType
    country: str
    name: str
    tier: str
    official_url: HttpUrl
    price: BundlePrice
    components: list[BundleComponent]
    requirements: BundleRequirements = BundleRequirements()
    feasibility_from: dict[str, BundleFeasibility] = Field(default_factory=dict)
    evidence: list[Evidence] = Field(default_factory=list)
    status: CuratedStatus = "unverified"
    valid_from: date | None = None
    last_verified: date
    confidence: Confidence = "low"
    notes_es: str = ""


class BundlesFile(_Strict):
    bundles: list[Bundle]

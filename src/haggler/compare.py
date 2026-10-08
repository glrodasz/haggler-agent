"""Cheapest feasible offer per (service, plan) for a residence country. CONTRACT.

Algorithm:
  * candidate offers: published rows with status accepted|carried_forward;
  * cost_eur = monthly_equiv converted to EUR via FxTable; if the offer currency differs from the
    residence country's currency add fx_fee_pct (default 2%) -> cost_eur_with_fee;
  * home offer = residence-country row, channel "web" preferred, else "ios_iap";
  * for each risk threshold k in 0..3: best = min cost_eur_with_fee over offers whose verdict rank
    <= k (feasibility.verdict); ties -> prefer web, then official, then lower country code;
  * savings_month_eur = home.cost_eur_with_fee - best.cost_eur_with_fee (None without home);
  * offers whose verdict is "unknown" are listed in `all_offers` but never chosen as best.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from decimal import Decimal

from haggler.models.catalog import Catalog
from haggler.models.curated import FeasibilityFile
from haggler.models.fx import FxTable
from haggler.models.observations import PublishedPrice

RISK_LEVELS = (0, 1, 2, 3)


@dataclass(frozen=True)
class OfferRef:
    obs_key: str
    service_id: str
    plan_id: str
    country: str
    channel: str
    currency: str
    amount: Decimal
    monthly_equiv: Decimal
    cost_eur: Decimal
    cost_eur_with_fee: Decimal
    label: str
    rank: int
    eu_badge: bool
    source_url: str


@dataclass
class CheapestRow:
    service_id: str
    plan_id: str
    home: OfferRef | None
    best_by_risk: dict[int, OfferRef | None] = field(default_factory=dict)
    savings_month_eur: dict[int, Decimal | None] = field(default_factory=dict)
    all_offers: list[OfferRef] = field(default_factory=list)


def cheapest(
    offers: list[PublishedPrice],
    *,
    fx: FxTable,
    catalog: Catalog,
    feasibility: FeasibilityFile,
    residence: str = "SE",
    profile: str = "se_resident_co_citizen",
    fx_fee_pct: float = 2.0,
) -> list[CheapestRow]:
    raise NotImplementedError

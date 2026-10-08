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

Details:
  * money is Decimal, quantized to 0.01 EUR (ROUND_HALF_UP) after the FX conversion and after the
    fee, and offers are compared on the quantized cost: sub-cent differences are ties;
  * after the documented tie-breaks: monthly before annual billing, then obs_key, so the result
    never depends on input order;
  * offers without an FX rate for their currency cannot be costed and are left out; rows for
    plans not in the catalog are left out (validate() already rejects them);
  * home = cheapest residence-country offer on the preferred channel;
  * rows: one per (service, plan) with at least one costed offer, sorted by service_id, then plan
    rank, then catalog order; `all_offers` is sorted cheapest first with the same tie-breaks.
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass, field
from decimal import ROUND_HALF_UP, Decimal

from haggler.feasibility import verdict
from haggler.models.catalog import Catalog
from haggler.models.curated import FeasibilityFile
from haggler.models.fx import FxTable
from haggler.models.observations import PublishedPrice

RISK_LEVELS = (0, 1, 2, 3)
CANDIDATE_STATUSES = frozenset({"accepted", "carried_forward"})
CENT = Decimal("0.01")

_CHANNEL_ORDER = {"web": 0, "ios_iap": 1}
_KIND_ORDER = {"official": 0, "secondary": 1}
_PERIOD_ORDER = {"month": 0, "quarter": 1, "year": 2}


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


def _money(value: Decimal) -> Decimal:
    return value.quantize(CENT, rounding=ROUND_HALF_UP)


def _order_key(ref: OfferRef, source_kind: str, period: str) -> tuple:
    return (
        ref.cost_eur_with_fee,
        _CHANNEL_ORDER.get(ref.channel, 9),
        _KIND_ORDER.get(source_kind, 9),
        ref.country,
        _PERIOD_ORDER.get(period, 9),
        ref.obs_key,
    )


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
    try:
        home_currency = catalog.country(residence).currency
    except KeyError:
        raise ValueError(f"residence country {residence!r} is not in the catalog") from None
    fee_factor = 1 + Decimal(str(fx_fee_pct)) / 100
    # (service, plan) -> (rank, catalog position) for the row order.
    plan_order = {
        (svc.id, plan.id): (plan.rank, pos)
        for svc in catalog.services
        for pos, plan in enumerate(svc.plans)
    }

    groups: dict[tuple[str, str], list[tuple[tuple, OfferRef]]] = defaultdict(list)
    for offer in offers:
        group = (offer.service_id, offer.plan_id)
        if offer.status not in CANDIDATE_STATUSES or group not in plan_order:
            continue
        per_eur = fx.per_eur(offer.currency)
        if not per_eur:
            continue
        eur = offer.monthly_equiv / per_eur
        with_fee = eur if offer.currency == home_currency else eur * fee_factor
        v = verdict(
            offer.service_id,
            offer.country,
            residence_country=residence,
            profile=profile,
            feasibility=feasibility,
        )
        ref = OfferRef(
            obs_key=offer.obs_key,
            service_id=offer.service_id,
            plan_id=offer.plan_id,
            country=offer.country,
            channel=offer.channel,
            currency=offer.currency,
            amount=offer.amount,
            monthly_equiv=offer.monthly_equiv,
            cost_eur=_money(eur),
            cost_eur_with_fee=_money(with_fee),
            label=v.label,
            rank=v.rank,
            eu_badge=v.eu_badge,
            source_url=offer.source_url,
        )
        groups[group].append((_order_key(ref, offer.source_kind, offer.billing_period), ref))

    rows: list[CheapestRow] = []
    for group in sorted(groups, key=lambda g: (g[0], plan_order[g])):
        refs = [ref for _, ref in sorted(groups[group], key=lambda item: item[0])]
        home_offers = [r for r in refs if r.country == residence]
        # min() keeps the first (cheapest) offer among those on the preferred channel.
        home = (
            min(home_offers, key=lambda r: _CHANNEL_ORDER.get(r.channel, 9))
            if home_offers
            else None
        )
        row = CheapestRow(service_id=group[0], plan_id=group[1], home=home, all_offers=refs)
        for k in RISK_LEVELS:
            best = next((r for r in refs if r.rank <= k), None)
            row.best_by_risk[k] = best
            row.savings_month_eur[k] = (
                home.cost_eur_with_fee - best.cost_eur_with_fee if home and best else None
            )
        rows.append(row)
    return rows

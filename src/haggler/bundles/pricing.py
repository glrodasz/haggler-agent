"""Bundle costs and savings (the cost fields of bundles.json v2). CONTRACT.

Pure: bundles.yaml values, the day's published prices and the FX table in; Decimal amounts out.

Regular price: today's published row of the plan an observed bundle points to (price.source
observed: the bundle's own country, web before App Store, monthly before yearly billing), else the
YAML amount. A yearly price counts /12 per month. The intro price never enters monthly_eur nor
saving_pct.

monthly_eur          the cost the saving is measured on, per month in EUR: the marginal amount
                     (amount - base_plan_amount) when the bundle is sold on top of a base plan the
                     subscriber pays anyway (saving_basis "marginal"), else the full regular price
                     (saving_basis "full").
full_monthly_eur     the full regular price per month in EUR (base plan included).
monthly_eur_with_fee monthly_eur plus the card FX fee when the price currency is not the
                     residence's currency (the rule and fee of haggler.compare.cheapest); else
                     monthly_eur.
first_year_monthly_eur  (intro x n + regular x (12 - n)) / 12 on the same basis, only for a
                     verified bundle with a current intro (intro_amount and intro_months, a monthly
                     price, intro_valid_until not past); else None.
standalone_sum_eur   the cheapest standalone monthly EUR price (status accepted|carried_forward, the
                     component's plan when given, else the cheapest plan of the service) of every
                     included component and of the N cheapest options of each choose_n group,
                     summed from the per-component figures rounded to cents (the ones the card
                     shows, so they add up). It is a lower bound, so the saving is never
                     overstated. Counted components without a price are listed in
                     standalone_missing and the sum is then partial.
saving_pct           (standalone_sum_eur - monthly_eur) / standalone_sum_eur x 100 from the rounded
                     figures the site shows; None without a sum. It never includes the FX fee,
                     intro prices, discounts or free months.
optional_savings     discount_pct / discount_abs / free_months components: what the subscriber
                     saves per month IF they also subscribe to that service (its standalone price x
                     pct / 100, or the amount converted and capped at that price; free months ->
                     None). Never part of saving_pct.

EUR amounts are converted unrounded and quantized to cents (ROUND_HALF_UP) once, at the end
(the standalone sum adds the components' rounded figures).
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import date
from decimal import ROUND_HALF_UP, Decimal

from haggler.compare import CANDIDATE_STATUSES
from haggler.models.curated import Bundle, BundleComponent
from haggler.models.fx import FxTable
from haggler.models.observations import PublishedPrice

CENT = Decimal("0.01")
MONTHS = 12
INCLUDED = "included"
CHOOSE_N = "choose_n"
OPTIONAL_INCLUSIONS = ("discount_pct", "discount_abs", "free_months")

_CHANNEL_ORDER = {"web": 0, "ios_iap": 1}
_PERIOD_ORDER = {"month": 0, "quarter": 1, "year": 2}


def money(value: Decimal | None) -> Decimal | None:
    if value is None:
        return None
    return value.quantize(CENT, rounding=ROUND_HALF_UP)


def to_eur(amount: Decimal | None, currency: str, fx: FxTable | None) -> Decimal | None:
    """Unrounded EUR value, computed like haggler.compare (rounded once, at the end)."""
    if amount is None or fx is None:
        return None
    per = fx.per_eur(currency)
    if not per:
        return None
    return amount / per


def _offer_order(p: PublishedPrice) -> tuple:
    return (_CHANNEL_ORDER.get(p.channel, 9), _PERIOD_ORDER.get(p.billing_period, 9), p.obs_key)


def observed_price(bundle: Bundle, rows: Sequence[PublishedPrice]) -> PublishedPrice | None:
    """Today's published price of the plan an "observed" bundle points to (e.g. Apple One SE),
    in the bundle's own country: web before App Store, monthly before yearly billing."""
    matches = [
        p
        for p in rows
        if p.service_id == bundle.price.observed_service_id
        and p.plan_id == bundle.price.observed_plan_id
        and p.country == bundle.country
        and p.status in CANDIDATE_STATUSES
    ]
    return min(matches, key=_offer_order) if matches else None


def component_eur(
    comp: BundleComponent, country: str, rows: Sequence[PublishedPrice], fx: FxTable | None
) -> Decimal | None:
    """Cheapest monthly EUR price of a component bought on its own in the bundle's country.
    Without a plan id, the cheapest plan of the service: a lower bound, so the saving shown is
    never overstated."""
    costs = [
        eur
        for p in rows
        if p.service_id == comp.service_id
        and p.country == country
        and (comp.plan_id is None or p.plan_id == comp.plan_id)
        and p.status in CANDIDATE_STATUSES
        and (eur := to_eur(p.monthly_equiv, p.currency, fx)) is not None
    ]
    return min(costs) if costs else None


def counted_components(
    bundle: Bundle, costs: Sequence[Decimal | None] | None = None
) -> list[BundleComponent]:
    """The components a subscriber gets for the price, in YAML order: every included one and,
    of each choose_n group, the N cheapest options (`costs` aligned with bundle.components;
    options without a price come after the priced ones, then in YAML order)."""
    return [bundle.components[i] for i in _counted_indices(bundle, costs)]


def _counted_indices(bundle: Bundle, costs: Sequence[Decimal | None] | None) -> list[int]:
    costs = list(costs) if costs is not None else [None] * len(bundle.components)
    chosen: set[int] = set()
    groups: dict[str, list[int]] = {}
    for i, comp in enumerate(bundle.components):
        if comp.inclusion == INCLUDED:
            chosen.add(i)
        elif comp.inclusion == CHOOSE_N:
            groups.setdefault(comp.choice_group_id or f"#{i}", []).append(i)
    for members in groups.values():
        n = next((m for i in members if (m := bundle.components[i].choose_n)), 1)
        ordered = sorted(members, key=lambda i: (costs[i] is None, costs[i] or 0, i))
        chosen.update(ordered[:n])
    return sorted(chosen)


def _per_month(amount: Decimal, period: str) -> Decimal:
    return amount / MONTHS if period == "year" else amount


@dataclass(frozen=True)
class RegularPrice:
    """The regular price the bundle's costs come from (the YAML's, or today's observed row)."""

    amount: Decimal | None
    currency: str
    period: str
    # The date of the price dataset an observed price was read from (None for a curated price).
    observed_on: date | None = None


def regular_price(
    bundle: Bundle, rows: Sequence[PublishedPrice], *, as_of: date | None = None
) -> RegularPrice:
    p = bundle.price
    if p.source != "observed":
        return RegularPrice(p.amount, p.currency, p.period)
    obs = observed_price(bundle, rows)
    if obs is None:
        return RegularPrice(None, p.currency, p.period)
    if obs.billing_period == "year":
        return RegularPrice(obs.amount, obs.currency, "year", as_of)
    # Monthly billing, or a quarter expressed per month.
    return RegularPrice(obs.monthly_equiv, obs.currency, "month", as_of)


@dataclass(frozen=True)
class OptionalSaving:
    service_id: str
    inclusion: str
    value: Decimal | None
    saving_month_eur: Decimal | None


@dataclass(frozen=True)
class BundlePricing:
    regular: RegularPrice
    # Cost on top of the base plan, per price period (the YAML's, or amount - base_plan_amount).
    marginal_amount: Decimal | None
    saving_basis: str  # "full" | "marginal"
    monthly_eur: Decimal | None
    full_monthly_eur: Decimal | None
    monthly_eur_with_fee: Decimal | None
    first_year_monthly_eur: Decimal | None
    standalone_sum_eur: Decimal | None
    standalone_missing: tuple[str, ...]
    saving_pct: Decimal | None
    # Standalone monthly EUR price of each component (aligned with bundle.components), rounded.
    component_eur: tuple[Decimal | None, ...]
    # The components counted in the standalone sum (included + N cheapest of each group).
    counted: tuple[BundleComponent, ...]
    optional_savings: tuple[OptionalSaving, ...]


def _marginal(bundle: Bundle, amount: Decimal | None) -> Decimal | None:
    p = bundle.price
    if p.marginal_amount is not None:
        return p.marginal_amount
    if p.base_plan_amount is not None and amount is not None:
        return amount - p.base_plan_amount
    return None


def _first_year(
    bundle: Bundle, regular: RegularPrice, marginal: Decimal | None, *, today: date
) -> Decimal | None:
    """First-year monthly cost on the saving's basis, in the price currency (None when the
    intro does not apply)."""
    p = bundle.price
    if (
        regular.amount is None
        or p.intro_amount is None
        or not p.intro_months
        or regular.period != "month"
        or regular.currency != p.currency
        or (p.intro_valid_until is not None and p.intro_valid_until < today)
    ):
        return None
    base = regular.amount - marginal if marginal is not None else Decimal(0)
    n = min(p.intro_months, MONTHS)
    regular_cost = marginal if marginal is not None else regular.amount
    intro_cost = p.intro_amount - base
    return (intro_cost * n + regular_cost * (MONTHS - n)) / MONTHS


def _optional_savings(
    bundle: Bundle, costs: Sequence[Decimal | None], fx: FxTable | None
) -> tuple[OptionalSaving, ...]:
    out: list[OptionalSaving] = []
    for comp, cost in zip(bundle.components, costs, strict=True):
        if comp.inclusion not in OPTIONAL_INCLUSIONS:
            continue
        saving: Decimal | None = None
        if comp.inclusion == "discount_pct" and cost is not None and comp.value is not None:
            saving = cost * comp.value / 100
        elif comp.inclusion == "discount_abs":
            saving = to_eur(comp.value, bundle.price.currency, fx)
            if saving is not None and cost is not None:
                saving = min(saving, cost)  # never more than the service costs on its own
        out.append(
            OptionalSaving(
                service_id=comp.service_id,
                inclusion=comp.inclusion,
                value=comp.value,
                saving_month_eur=money(saving),
            )
        )
    return tuple(out)


def price_bundle(
    bundle: Bundle,
    rows: Sequence[PublishedPrice],
    fx: FxTable | None,
    *,
    residence_currency: str,
    fx_fee_pct: float,
    today: date,
    as_of: date | None = None,
    verified: bool = False,
) -> BundlePricing:
    """`verified`: the bundle's display_status is "verified" (haggler.bundles.status), the only
    case in which the intro price is used (first_year_monthly_eur)."""
    regular = regular_price(bundle, rows, as_of=as_of)
    marginal = _marginal(bundle, regular.amount)
    basis_amount = marginal if marginal is not None else regular.amount

    def eur(amount: Decimal | None) -> Decimal | None:
        if amount is None:
            return None
        return to_eur(_per_month(amount, regular.period), regular.currency, fx)

    monthly = eur(basis_amount)
    fee_factor = 1 + Decimal(str(fx_fee_pct)) / 100
    foreign = monthly is not None and regular.currency != residence_currency
    with_fee = monthly * fee_factor if foreign else monthly  # type: ignore[operator]
    first_year = None
    if verified:
        intro = _first_year(bundle, regular, marginal, today=today)
        first_year = to_eur(intro, regular.currency, fx)

    costs = [component_eur(c, bundle.country, rows, fx) for c in bundle.components]
    counted = _counted_indices(bundle, costs)
    # The rounded per-component figures the card lists, so the sum shown is their sum.
    parts = [cents for i in counted if (cents := money(costs[i])) is not None]
    missing = [bundle.components[i].service_id for i in counted if costs[i] is None]
    standalone = sum(parts, Decimal(0)) if parts else None
    monthly_rounded = money(monthly)
    saving_pct = None
    if standalone and monthly_rounded is not None:
        # From the rounded figures the site shows, so a reader can recompute it.
        saving_pct = money((standalone - monthly_rounded) / standalone * 100)

    return BundlePricing(
        regular=regular,
        marginal_amount=marginal,
        saving_basis="marginal" if marginal is not None else "full",
        monthly_eur=monthly_rounded,
        full_monthly_eur=money(eur(regular.amount)),
        monthly_eur_with_fee=money(with_fee),
        first_year_monthly_eur=money(first_year),
        standalone_sum_eur=standalone,
        standalone_missing=tuple(dict.fromkeys(missing)),
        saving_pct=saving_pct,
        component_eur=tuple(money(c) for c in costs),
        counted=tuple(bundle.components[i] for i in counted),
        optional_savings=_optional_savings(bundle, costs, fx),
    )

"""Microsoft 365 prices from the Microsoft Store catalog API (DisplayCatalog v7).

displaycatalog.mp.microsoft.com/v7.0/products?bigIds=...&market={cc}&languages=en-US returns the
market's catalog entries as JSON. Each product's DisplaySkuAvailabilities pair a SKU with
availabilities that carry OrderManagementData.Price {CurrencyCode, ListPrice, MSRP,
RecurrencePrice, TaxType, DiscountDuration} and the Actions allowed on them.

What the live API serves (probe of 2026-10-08, SE and US):
  * every SKU titles itself just "Microsoft 365 Personal": no period in titles or properties;
  * the purchasable subscription SKUs (007N, 007P, 007Q, 007R) have one "Purchase"
    availability with ListPrice 0.0 (a free first month, DiscountDuration {Month, 1}, or no
    charge at checkout) and the real price in RecurrencePrice: SE Personal 119.99 / 1199.99,
    Family 155.99 / 1559.0 SEK; US Personal 9.99 / 99.99 USD;
  * availabilities with RecurrencePrice 0 are one-off or multi-year purchases (US SKU 00HZ:
    ListPrice 190 / 165.76 / 209.99) or free placeholders (00HQ), not subscriptions;
  * "Renew", "Redeem" and "Tokenize" availabilities are for existing subscribers or keys.

So we keep availabilities whose Actions include "Purchase" and take RecurrencePrice when it is
present and > 0 (ListPrice is only the first-period charge), skip those whose RecurrencePrice is
0, and fall back to ListPrice > 0 for payloads without a RecurrencePrice. Prices are grouped per
SKU. A SKU's period comes from its title or period-like properties when stated, else from the
8-12.5x ratio between the plan's SKUs (1199.99 is ~10x 119.99 -> annual), else from the SKU
id (007N month, 007R year: the only ids whose period was the same for Personal and Family,
007P/007Q swap between products; params.sku_period_hints adds or overrides ids), else from a
SKU at the same price whose period is known, else params.default_period ("year"). One price
per (plan, period) is kept (the lowest, noting any other). TaxType
"TaxesNotIncluded" adds the flag taxes_not_included. A market where the products are not sold
answers "Products": [] -> not_available.
"""

from __future__ import annotations

import json
import re
from collections import Counter
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal, InvalidOperation
from typing import Any, ClassVar

from haggler.fetch import RawResponse
from haggler.models.catalog import Catalog, ServiceDef, SourceDef
from haggler.models.observations import BillingPeriod, ObservationDraft
from haggler.models.run import Task
from haggler.sources.appstore_iap import infer_periods, name_period
from haggler.sources.base import ParseResult, PerCountryAdapter, draft, make_task_id

DEFAULT_URL = (
    "https://displaycatalog.mp.microsoft.com/v7.0/products"
    "?bigIds={big_ids}&market={cc}&languages=en-US"
)
_SKIP_SKU = re.compile(r"\b(trial|classic|prueba|test)\b", re.IGNORECASE)
_PERIOD_KEY = re.compile(r"duration|period|recurr|billing|renew|interval|frequen", re.I)
_ISO_DURATION = re.compile(r"P(\d+)([DWMY])", re.IGNORECASE)
# SKU ids whose period was identical for Personal and Family in every probed market.
DEFAULT_SKU_PERIOD_HINTS: dict[str, BillingPeriod] = {"007N": "month", "007R": "year"}
FLAG_TAXES_NOT_INCLUDED = "taxes_not_included"
_UNIT_MONTHS = {
    "day": Decimal(1) / 30,
    "week": Decimal(7) / 30,
    "month": Decimal(1),
    "year": Decimal(12),
}


def _get(d: Any, key: str) -> Any:
    """Case-insensitive dict lookup (the API is PascalCase, some mirrors are camelCase)."""
    if not isinstance(d, dict):
        return None
    if key in d:
        return d[key]
    low = key.casefold()
    return next((v for k, v in d.items() if str(k).casefold() == low), None)


def _list(value: Any) -> list[Any]:
    if isinstance(value, list):
        return value
    return [] if value is None else [value]


def _localized(obj: Any, field: str) -> str:
    """`field` of the en-US LocalizedProperties entry (else the first one that has it)."""
    props = [p for p in _list(_get(obj, "LocalizedProperties")) if isinstance(p, dict)]
    props.sort(key=lambda p: str(_get(p, "Language") or "").casefold() != "en-us")
    for p in props:
        value = _get(p, field)
        if isinstance(value, str) and value.strip():
            return " ".join(value.split())
    return ""


def _decimal(value: Any) -> Decimal | None:
    if isinstance(value, bool) or value is None:
        return None
    try:
        amount = Decimal(str(value))
    except InvalidOperation:
        return None
    if not amount.is_finite():
        return None
    # Stable text across runs: 1049.0 -> 1049, 12.5 -> 12.50.
    if amount == amount.to_integral_value():
        return amount.quantize(Decimal(1))
    return amount.quantize(Decimal("0.01"))


def _months_to_period(months: Decimal) -> BillingPeriod | None:
    if Decimal("0.9") <= months <= Decimal("1.1"):
        return "month"
    if Decimal("2.9") <= months <= Decimal("3.1"):
        return "quarter"
    if Decimal("11.8") <= months <= Decimal("12.2"):
        return "year"
    return None


def _period_from_value(key: str, value: Any) -> BillingPeriod | None:
    if isinstance(value, str):
        if m := _ISO_DURATION.fullmatch(value.strip()):
            unit = {"D": "day", "W": "week", "M": "month", "Y": "year"}[m.group(2).upper()]
            return _months_to_period(int(m.group(1)) * _UNIT_MONTHS[unit])
        # Short enum-like values only ("Annual", "Monthly"); long text may mention both.
        return name_period(value) if len(value) <= 24 else None
    if isinstance(value, int | Decimal) and not isinstance(value, bool):
        k = key.casefold()
        for unit in ("day", "week", "month", "year"):
            if unit in k:
                return _months_to_period(Decimal(value) * _UNIT_MONTHS[unit])
    return None


def _period_from_props(obj: Any, depth: int = 0) -> BillingPeriod | None:
    """Billing period from period-like properties ("RecurrencePolicy": {"BillingPeriod": "P1M"},
    {"DurationUnit": "Year", "DurationValue": 1}, "DurationInDays": 365 ...)."""
    if depth > 4 or not isinstance(obj, dict):
        return None
    unit = next(
        (str(v).casefold().rstrip("s") for k, v in obj.items() if "unit" in str(k).casefold()),
        None,
    )
    if unit in _UNIT_MONTHS:
        count = next(
            (
                v
                for k, v in obj.items()
                if re.search(r"value|count|length|quantity|duration", str(k), re.I)
                and isinstance(v, int | Decimal)
                and not isinstance(v, bool)
            ),
            1,
        )
        if period := _months_to_period(Decimal(count) * _UNIT_MONTHS[unit]):
            return period
    for k, v in obj.items():
        key = str(k)
        if isinstance(v, dict):
            if period := _period_from_props(v, depth + 1):
                return period
        elif _PERIOD_KEY.search(key) and (period := _period_from_value(key, v)):
            return period
    return None


def _when(value: Any) -> datetime | None:
    if not isinstance(value, str):
        return None
    try:
        return datetime.fromisoformat(value.strip().replace("Z", "+00:00"))
    except ValueError:
        return None


def _catalog_clock(products: list[dict[str, Any]]) -> datetime | None:
    """Latest LastModifiedDate in the payload: a lower bound for "now" when the fetch time is
    unknown (replays), enough to drop availabilities whose EndDate has clearly passed."""
    stamps = [
        when
        for p in products
        for d in [p, *_list(_get(p, "DisplaySkuAvailabilities"))]
        for obj in [d, _get(d, "Sku"), *_list(_get(d, "Availabilities"))]
        if (when := _when(_get(obj, "LastModifiedDate"))) and when.tzinfo
    ]
    return max(stamps, default=None)


def _outside(at: datetime, start: datetime | None, end: datetime | None) -> bool:
    """True when `at` falls outside an availability's [StartDate, EndDate] window."""
    if start and start.tzinfo and start > at:
        return True
    return bool(end and end.tzinfo and end < at)


@dataclass
class _Offer:
    plan_id: str
    sku_id: str
    title: str
    amount: Decimal
    currency: str
    period: BillingPeriod | None
    how: str
    msrp: Decimal | None
    tax_type: str
    price_field: str  # "RecurrencePrice" or "ListPrice"
    first_period: str = ""  # "first 1 month free", "first period 49.99" ...


def _first_period(price: Any, list_price: Decimal | None, amount: Decimal) -> str:
    """What the first period costs when it differs from the recurring price."""
    if list_price is None or list_price == amount:
        return ""
    dd = _get(price, "DiscountDuration")
    units, unit = _get(dd, "Units"), str(_get(dd, "UnitType") or "").casefold()
    if not (units and unit):
        return f"ListPrice {list_price}"
    return f"first {units} {unit} {'free' if list_price == 0 else f'at {list_price}'}"


class MsDisplayCatalogAdapter(PerCountryAdapter):
    id: ClassVar[str] = "ms_displaycatalog"

    def plan(self, svc: ServiceDef, src: SourceDef, cat: Catalog) -> list[Task]:
        big_ids = src.params.get("big_ids") or {}
        params = {"url": DEFAULT_URL, **src.params, "accept": "application/json"}
        if big_ids:
            return super().plan(svc, src.model_copy(update={"params": params}), cat)
        return [
            Task(
                task_id=make_task_id(self.id, svc.id, c.cc),
                adapter_id=self.id,
                service_ids=[svc.id],
                countries=[c.cc],
                channel=src.channel,
                kind=src.kind,
                url="",
                host="",
                params={**params, "not_configured": ["big_ids"]},
            )
            for c in cat.countries
            if src.applies_to(c.cc)
        ]

    def extra_vars(self, src: SourceDef) -> dict[str, object]:
        ids = dict.fromkeys(str(v) for v in (src.params.get("big_ids") or {}).values())
        return {"big_ids": ",".join(ids)}

    def parse(self, task: Task, resp: RawResponse, cat: Catalog) -> ParseResult:
        svc = cat.service(task.service_ids[0])
        country = cat.country(task.countries[0])
        expected = list(country.expected_currencies(task.channel))
        if resp.status_code in (401, 403, 429):
            return ParseResult(status="blocked", notes=[f"HTTP {resp.status_code}"])
        if resp.status_code in (404, 410):
            return ParseResult(status="not_available", notes=[f"HTTP {resp.status_code}"])
        try:
            data = json.loads(resp.text, parse_float=Decimal)
        except ValueError:
            blocked = re.search(r"captcha|access denied|blocked", resp.text[:5000], re.I)
            return ParseResult(
                status="blocked" if blocked else "no_prices",
                notes=[f"not JSON (HTTP {resp.status_code}, {len(resp.text)} chars)"],
            )
        if resp.status_code >= 400:
            return ParseResult(status="no_prices", notes=[f"HTTP {resp.status_code}"])

        # bigIds queries answer {"Products": [...]}; /products/{id} answers {"Product": {...}}.
        raw_products = _list(_get(data, "Products")) or _list(_get(data, "Product"))
        products = [p for p in raw_products if isinstance(p, dict)]
        if not products:
            return ParseResult(
                status="not_available", notes=[f"no products for market {country.cc}"]
            )
        notes: list[str] = []
        offers = self._offers(task, products, resp.fetched_at or _catalog_clock(products), notes)
        foreign = Counter(o.currency for o in offers if o.currency not in expected)
        offers = [o for o in offers if o.currency in expected]
        if foreign:
            seen = ", ".join(f"{c}x{n}" for c, n in sorted(foreign.items()))
            notes.append(f"ignored prices in {seen}; expected {', '.join(expected)}")
        for tax in sorted({o.tax_type for o in offers if o.tax_type}):
            notes.append(f"TaxType={tax}")

        observations = self._drafts(task, svc, offers, notes)
        found = {d.plan_id for d in observations}
        if svc.default_plan in found:
            status = "ok"
        elif observations:
            status = "partial"
        elif foreign:
            status = "wrong_country"
        else:
            status = "not_available"
            notes.append("no purchasable availability with a price")
        return ParseResult(
            observations=observations,
            status=status,
            missing_plans=[p.id for p in svc.plans if p.id not in found],
            notes=notes,
        )

    def _offers(
        self,
        task: Task,
        products: list[dict[str, Any]],
        fetched_at: datetime | None,
        notes: list[str],
    ) -> list[_Offer]:
        plan_by_big_id = {
            str(big).upper(): plan for plan, big in (task.params.get("big_ids") or {}).items()
        }
        skip = [_SKIP_SKU] + [
            re.compile(p, re.IGNORECASE) for p in task.params.get("exclude_patterns") or []
        ]
        out: list[_Offer] = []
        for product in products:
            product_id = str(_get(product, "ProductId") or "").upper()
            plan_id = plan_by_big_id.get(product_id)
            if plan_id is None:
                notes.append(f"unmapped product {product_id}")
                continue
            product_title = _localized(product, "ProductTitle")
            for dsa in _list(_get(product, "DisplaySkuAvailabilities")):
                sku = _get(dsa, "Sku") or {}
                sku_id = str(_get(sku, "SkuId") or "")
                title = _localized(sku, "SkuTitle") or product_title
                description = _localized(sku, "SkuDescription")
                sku_props = _get(sku, "Properties") or {}
                trial = _get(sku_props, "IsTrial") is True or (
                    str(_get(sku, "SkuType") or "").casefold() == "trial"
                )
                if trial or any(rx.search(title) for rx in skip):
                    notes.append(f"skipped SKU {sku_id} {title!r}")
                    continue
                period, how = self._sku_period(title, description, sku)
                for av in _list(_get(dsa, "Availabilities")):
                    offer = self._offer(av, plan_id, sku_id, title, period, how, fetched_at)
                    if offer:
                        out.append(offer)
        return out

    @staticmethod
    def _sku_period(title: str, description: str, sku: Any) -> tuple[BillingPeriod | None, str]:
        if period := name_period(title):
            return period, "SKU title"
        for key in ("Properties", "RecurrencePolicy", "SubscriptionPolicy"):
            if period := _period_from_props(_get(sku, key)):
                return period, f"SKU {key}"
        if period := name_period(description):
            return period, "SKU description"
        return None, ""

    @staticmethod
    def _offer(
        av: Any,
        plan_id: str,
        sku_id: str,
        title: str,
        period: BillingPeriod | None,
        how: str,
        fetched_at: datetime | None,
    ) -> _Offer | None:
        actions = {str(a).casefold() for a in _list(_get(av, "Actions"))}
        if "purchase" not in actions:
            return None
        price = _get(_get(av, "OrderManagementData"), "Price") or {}
        currency = str(_get(price, "CurrencyCode") or "").upper()
        list_price = _decimal(_get(price, "ListPrice"))
        recurrence = _decimal(_get(price, "RecurrencePrice"))
        if recurrence is not None:
            # A subscription renews at RecurrencePrice; 0 means a one-off or multi-year
            # purchase (US 00HZ: ListPrice 190) or a free placeholder, not a subscription.
            if recurrence <= 0:
                return None
            amount, field = recurrence, "RecurrencePrice"
        elif list_price is not None and list_price > 0:
            amount, field = list_price, "ListPrice"
        else:
            return None
        if not re.fullmatch(r"[A-Z]{3}", currency):
            return None
        conditions = _get(av, "Conditions") or {}
        start, end = _when(_get(conditions, "StartDate")), _when(_get(conditions, "EndDate"))
        if fetched_at and fetched_at.tzinfo and _outside(fetched_at, start, end):
            return None
        if period is None:
            period = _period_from_props(_get(av, "Properties"))
            how = "availability Properties" if period else ""
        return _Offer(
            plan_id=plan_id,
            sku_id=sku_id,
            title=title,
            amount=amount,
            currency=currency,
            period=period,
            how=how,
            msrp=_decimal(_get(price, "MSRP")),
            tax_type=str(_get(price, "TaxType") or ""),
            price_field=field,
            first_period=_first_period(price, list_price, amount) if field != "ListPrice" else "",
        )

    @staticmethod
    def _hints(task: Task, notes: list[str]) -> dict[str, BillingPeriod]:
        hints: dict[str, BillingPeriod] = dict(DEFAULT_SKU_PERIOD_HINTS)
        for sku, period in (task.params.get("sku_period_hints") or {}).items():
            if period in ("month", "quarter", "year"):
                hints[str(sku).upper()] = period
            else:
                notes.append(f"ignored sku_period_hints[{sku}]={period!r}")
        return hints

    def _drafts(
        self, task: Task, svc: ServiceDef, offers: list[_Offer], notes: list[str]
    ) -> list[ObservationDraft]:
        default_period: BillingPeriod = task.params.get("default_period") or "year"
        if default_period not in ("month", "quarter", "year"):
            notes.append(f"invalid params.default_period {default_period!r}; using year")
            default_period = "year"
        hints = self._hints(task, notes)
        best: dict[tuple[str, BillingPeriod], _Offer] = {}
        seen: dict[tuple[str, BillingPeriod], set[Decimal]] = {}
        for plan in svc.plans:
            # One price per SKU: the lowest of its purchasable availabilities.
            per_sku: dict[str, _Offer] = {}
            for o in (o for o in offers if o.plan_id == plan.id):
                prev = per_sku.get(o.sku_id)
                if prev is not None and prev.amount != o.amount:
                    notes.append(f"{plan.id}: SKU {o.sku_id} at {prev.amount} and {o.amount}")
                if prev is None or o.amount < prev.amount:
                    per_sku[o.sku_id] = o
            skus = list(per_sku.values())
            known = [(o.amount, o.period) for o in skus if o.period]
            unknown = [o for o in skus if o.period is None]
            inferred = infer_periods([o.amount for o in unknown], known, default=default_period)
            undecided: list[_Offer] = []
            for o, (period, reason) in zip(unknown, inferred, strict=True):
                if reason != "default":
                    o.period, o.how = period, f"ratio: {reason}"
                elif (hint := hints.get(o.sku_id.upper())) is not None:
                    o.period, o.how = hint, f"SKU id {o.sku_id}"
                else:
                    undecided.append(o)
            for o in undecided:
                # Same price as a SKU whose period is known (007N and 007Q both 119.99).
                twin = next((t for t in skus if t.period and t.amount == o.amount), None)
                if twin is not None:
                    o.period, o.how = twin.period, f"same price as SKU {twin.sku_id}"
                else:
                    o.period, o.how = default_period, "params.default_period"
                    notes.append(f"{plan.id}: SKU {o.sku_id} period assumed {default_period}")
            for o in skus:
                assert o.period is not None
                key = (plan.id, o.period)
                seen.setdefault(key, set()).add(o.amount)
                if key not in best or o.amount < best[key].amount:
                    best[key] = o

        order = {p.id: i for i, p in enumerate(svc.plans)}
        rank = {"month": 0, "quarter": 1, "year": 2}
        out: list[ObservationDraft] = []
        for (plan_id, period), o in sorted(
            best.items(), key=lambda kv: (order[kv[0][0]], rank[kv[0][1]])
        ):
            if len(seen[(plan_id, period)]) > 1:
                amounts = ", ".join(str(a) for a in sorted(seen[(plan_id, period)]))
                notes.append(f"{plan_id}/{period}: prices {amounts}; kept the lowest")
            if o.price_field == "ListPrice" and o.msrp is not None and o.msrp > o.amount:
                notes.append(f"{plan_id}/{period}: ListPrice {o.amount} below MSRP {o.msrp}")
            extra = f"; {o.first_period}" if o.first_period else ""
            tax = f"; {o.tax_type}" if o.tax_type else ""
            out.append(
                draft(
                    task,
                    plan_id=plan_id,
                    label=o.title or plan_id,
                    amount=o.amount,
                    currency=o.currency,
                    period=period,
                    evidence=(
                        f"{o.title}: {o.price_field} {o.amount} {o.currency} "
                        f"(SKU {o.sku_id}; period from {o.how or 'n/a'}{extra}{tax})"
                    ),
                    flags=[FLAG_TAXES_NOT_INCLUDED]
                    if o.tax_type.casefold() == "taxesnotincluded"
                    else [],
                )
            )
        return out

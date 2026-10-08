"""Microsoft 365 prices from the Microsoft Store catalog API (DisplayCatalog v7).

displaycatalog.mp.microsoft.com/v7.0/products?bigIds=...&market={cc}&languages=en-US returns the
market's catalog entries as JSON. Each product's DisplaySkuAvailabilities pair a SKU (titles like
"Microsoft 365 Family - 12-month subscription") with availabilities that carry
OrderManagementData.Price {CurrencyCode, ListPrice, MSRP} and the Actions allowed on them.

We keep availabilities whose Actions include "Purchase" with ListPrice > 0 (inside their
Conditions date window when the fetch time is known), map ProductId -> plan through
params.big_ids, and skip trial / "Classic" SKUs (Classic plans are only offered to existing
subscribers). The billing period comes from the SKU title, then period-like SKU properties, then
the SKU description; failing that, the 8-12.5x price ratio between SKUs of the same plan, and
finally params.default_period ("year": the Store sells Microsoft 365 mainly as annual
subscriptions). The lowest price per (plan, period) wins. A market where the products are not
sold answers "Products": [] -> not_available.
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
        amount = _decimal(_get(price, "ListPrice"))
        currency = str(_get(price, "CurrencyCode") or "").upper()
        if amount is None or amount <= 0 or not re.fullmatch(r"[A-Z]{3}", currency):
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
        )

    def _drafts(
        self, task: Task, svc: ServiceDef, offers: list[_Offer], notes: list[str]
    ) -> list[ObservationDraft]:
        default_period: BillingPeriod = task.params.get("default_period") or "year"
        if default_period not in ("month", "quarter", "year"):
            notes.append(f"invalid params.default_period {default_period!r}; using year")
            default_period = "year"
        best: dict[tuple[str, BillingPeriod], _Offer] = {}
        seen: dict[tuple[str, BillingPeriod], set[Decimal]] = {}
        for plan in svc.plans:
            mine = [o for o in offers if o.plan_id == plan.id]
            known = [(o.amount, o.period) for o in mine if o.period]
            unknown = [o for o in mine if o.period is None]
            amounts = [o.amount for o in unknown]
            for o, (period, reason) in zip(
                unknown, infer_periods(amounts, known, default=default_period), strict=True
            ):
                o.period = period
                o.how = "params.default_period" if reason == "default" else f"ratio: {reason}"
                if reason == "default":
                    notes.append(f"{plan.id}: SKU {o.sku_id} period assumed {period}")
            for o in mine:
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
            if o.msrp is not None and o.msrp > o.amount:
                notes.append(f"{plan_id}/{period}: ListPrice {o.amount} below MSRP {o.msrp}")
            out.append(
                draft(
                    task,
                    plan_id=plan_id,
                    label=o.title or plan_id,
                    amount=o.amount,
                    currency=o.currency,
                    period=period,
                    evidence=(
                        f"{o.title}: ListPrice {o.amount} {o.currency} "
                        f"(SKU {o.sku_id}; period from {o.how or 'n/a'})"
                    ),
                )
            )
        return out

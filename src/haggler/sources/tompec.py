"""Secondary Netflix prices from the tompec/netflix-prices dataset (CC BY 4.0, by thomas.io).

One JSON file covers every country, so this adapter plans a single task. Format of latest.json:
a list of {country_code, country, currency, plans: [{name, price, price_usd}]}, where plan names
are our Netflix plan ids and `price: null` means the plan is not offered there. The dataset is
updated roughly monthly; its rows are only used to cross-check the official help-center prices
(source kind "secondary", set on the task from catalog/services.yaml).
"""

from __future__ import annotations

import json
import re
from decimal import Decimal, InvalidOperation
from typing import Any, ClassVar

from haggler.fetch import RawResponse
from haggler.models.catalog import Catalog, ServiceDef, SourceDef
from haggler.models.observations import ObservationDraft
from haggler.models.run import Task
from haggler.sources.base import ParseResult, draft, host_of, make_task_id


def _amount(value: Any) -> Decimal | None:
    """JSON number -> Decimal, written like page prices ("169", "14.90") so values compare and
    serialize identically whichever source produced them."""
    if value is None or isinstance(value, bool):
        return None
    try:
        d = Decimal(str(value).strip())
    except InvalidOperation:
        return None
    if not d.is_finite() or d <= 0:
        return None
    if d == d.to_integral_value():
        return d.quantize(Decimal(1))
    exponent = d.as_tuple().exponent
    if isinstance(exponent, int) and exponent >= -2:
        return d.quantize(Decimal("0.01"))
    return d


def _rows(data: Any) -> list[dict[str, Any]]:
    """The country list, tolerating a future {"data": [...]}-style wrapper."""
    if isinstance(data, dict):
        data = next((v for v in data.values() if isinstance(v, list)), [])
    return [r for r in data if isinstance(r, dict)] if isinstance(data, list) else []


class TompecNetflixAdapter:
    id: ClassVar[str] = "tompec_netflix"

    def plan(self, svc: ServiceDef, src: SourceDef, cat: Catalog) -> list[Task]:
        countries = [c.cc for c in cat.countries if src.applies_to(c.cc)]
        if not countries:
            return []
        url = src.params["url"]
        return [
            Task(
                task_id=make_task_id(self.id, svc.id, "all"),
                adapter_id=self.id,
                service_ids=[svc.id],
                countries=countries,
                channel=src.channel,
                kind=src.kind,
                url=url,
                host=host_of(url),
                params={**src.params, "accept": "application/json"},
            )
        ]

    def parse(self, task: Task, resp: RawResponse, cat: Catalog) -> ParseResult:
        svc = cat.service(task.service_ids[0])
        try:
            data = json.loads(resp.text, parse_float=Decimal)
        except json.JSONDecodeError as exc:
            return ParseResult(status="no_prices", notes=[f"tompec: response is not JSON ({exc})"])
        by_cc: dict[str, dict[str, Any]] = {}
        for row in _rows(data):
            by_cc.setdefault(str(row.get("country_code") or "").upper(), row)
        if not by_cc:
            return ParseResult(status="no_prices", notes=["tompec: no country rows in JSON"])

        plan_ids = {p.id for p in svc.plans}
        overrides = task.params.get("currency_overrides") or {}
        observations: list[ObservationDraft] = []
        notes: list[str] = []
        for cc in task.countries:
            row = by_cc.get(cc)
            if row is None:
                notes.append(f"{cc}: not in dataset")
                continue
            currency = str(row.get("currency") or "").strip().upper()
            if not re.fullmatch(r"[A-Z]{3}", currency):
                notes.append(f"{cc}: bad currency {row.get('currency')!r}")
                continue
            pinned = overrides.get(cc)
            expected = (
                ([pinned] if isinstance(pinned, str) else list(pinned))
                if pinned
                else cat.country(cc).expected_currencies(task.channel)
            )
            if currency not in expected:
                notes.append(f"{cc}: dataset currency {currency}, catalog expects {expected}")
            seen: set[str] = set()
            for plan in row.get("plans") or []:
                if not isinstance(plan, dict):
                    continue
                name = str(plan.get("name") or "")
                if name not in plan_ids:
                    notes.append(f"{cc}: unknown plan {name!r}")
                    continue
                amount = _amount(plan.get("price"))
                if amount is None or name in seen:
                    continue  # null = not offered in this country
                seen.add(name)
                observations.append(
                    draft(
                        task,
                        plan_id=name,
                        label=name,
                        amount=amount,
                        currency=currency,
                        country=cc,
                        evidence=f"tompec latest.json: {cc} {name} {amount} {currency}",
                    )
                )
            if svc.default_plan not in seen:
                notes.append(f"{cc}: no {svc.default_plan} price")
        return ParseResult(
            observations=observations,
            status="ok" if observations else "no_prices",
            notes=notes,
        )

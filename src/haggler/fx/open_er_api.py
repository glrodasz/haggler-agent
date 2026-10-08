"""open.er-api.com (ExchangeRate-API "open access") latest rates.

WHY last among live sources: broad coverage (PKR, VND, NGN ...) with no key, but rates are a
commercial blend refreshed once a day and the terms REQUIRE attribution on pages that show them
(see ATTRIBUTION). Response of GET /v6/latest/EUR:
  {"result": "success", "base_code": "EUR", "time_last_update_unix": 1759881601,
   "time_last_update_utc": "Wed, 08 Oct 2025 00:00:01 +0000", "rates": {"EUR": 1, "USD": 1.16}}
Errors come back as {"result": "error", "error-type": "unsupported-code"}.
"""

from __future__ import annotations

from datetime import UTC, date, datetime
from decimal import Decimal, InvalidOperation
from typing import Any

import httpx

OPEN_ER_API_URL = "https://open.er-api.com/v6/latest/{base}"
DEFAULT_TIMEOUT = httpx.Timeout(20.0, connect=10.0)
ATTRIBUTION = {
    "name": "Rates By Exchange Rate API",
    "url": "https://www.exchangerate-api.com",
    "license": "Open access terms: attribution required",
}


def parse_latest(payload: Any, *, base: str = "EUR") -> tuple[date, dict[str, Decimal]]:
    """(update date in UTC, {currency: units per `base`}); rebases if base_code differs."""
    if not isinstance(payload, dict):
        raise ValueError(f"unexpected open.er-api payload: {type(payload).__name__}")
    if payload.get("result") != "success":
        raise ValueError(f"open.er-api error: {payload.get('error-type') or payload.get('result')}")
    base = base.upper()
    src_base = str(payload.get("base_code") or "").upper()
    raw_rates = payload.get("rates")
    if not src_base or not isinstance(raw_rates, dict):
        raise ValueError("open.er-api payload without base_code/rates")
    rates: dict[str, Decimal] = {}
    for cur, value in raw_rates.items():
        try:
            rate = value if isinstance(value, Decimal) else Decimal(str(value))
        except (InvalidOperation, ValueError):
            continue
        if rate.is_finite() and rate > 0:
            rates[str(cur).upper()] = rate
    if src_base != base:
        pivot = rates.get(base)
        if pivot is None:
            raise ValueError(f"open.er-api payload cannot be rebased to {base}")
        rates = {cur: rate / pivot for cur, rate in rates.items()}
    rates.pop(base, None)
    updated = payload.get("time_last_update_unix")
    try:
        day = datetime.fromtimestamp(int(updated), UTC).date()
    except (TypeError, ValueError, OverflowError) as exc:
        raise ValueError("open.er-api payload without time_last_update_unix") from exc
    return day, rates


async def fetch_open_er_api(
    client: httpx.AsyncClient,
    *,
    base: str = "EUR",
    url_template: str = OPEN_ER_API_URL,
    headers: dict[str, str] | None = None,
    timeout: httpx.Timeout = DEFAULT_TIMEOUT,
) -> tuple[date, dict[str, Decimal]]:
    """Raises httpx.HTTPError on transport/status errors, ValueError on an error payload."""
    resp = await client.get(
        url_template.format(base=base.upper()),
        headers={"Accept": "application/json", **(headers or {})},
        timeout=timeout,
        follow_redirects=True,
    )
    resp.raise_for_status()
    return parse_latest(resp.json(parse_float=Decimal, parse_int=Decimal), base=base)

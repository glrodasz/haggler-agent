"""Frankfurter v2 API client (https://api.frankfurter.dev/v2, open source, no key).

Verified against the project's README and lib/public/v2/openapi.json (v2.1.1) plus its source:
- GET /v2/rates?base=EUR&quotes=COP,ARS[&providers=BANREP] -> JSON array of
  {"date": "YYYY-MM-DD", "base": "EUR", "quote": "USD", "rate": 1.089}. Without date params it
  returns the latest rates; carried-forward quotes keep their own (older) "date", which is
  what we record as each rate's as_of. An identity row (quote == base) is included unless
  `quotes` filters it out. Errors: 422 {"status","message"} (bad params), 404 (no data),
  503 (busy; may carry Retry-After).
- A SINGLE `providers=` value returns that provider's own view: the server rebases only through
  the provider's own rows (BaseConversion), so a provider that publishes only USD->COP
  (BANREP) yields NO record for base=EUR. Callers ask again with base=USD and bridge to EUR.
"""

from __future__ import annotations

from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from datetime import date
from decimal import Decimal, InvalidOperation
from typing import Any

import httpx

DEFAULT_BASE_URL = "https://api.frankfurter.dev"
DEFAULT_TIMEOUT = httpx.Timeout(20.0, connect=10.0)
ATTRIBUTION = {
    "name": "Frankfurter (central bank exchange rates)",
    "url": "https://frankfurter.dev",
    "license": "MIT (API); data from the cited central banks",
}


@dataclass(frozen=True)
class FrankfurterRate:
    base: str
    quote: str
    rate: Decimal
    date: date


def _decimal(value: Any) -> Decimal | None:
    try:
        out = value if isinstance(value, Decimal) else Decimal(str(value))
    except (InvalidOperation, ValueError, TypeError):
        return None
    return out if out.is_finite() and out > 0 else None


def parse_rates(payload: Any, *, base: str = "EUR") -> dict[str, FrankfurterRate]:
    """Records for `base` keyed by quote (identity row dropped, newest date wins).

    Accepts the v2 array shape; the v1 {"base","date","rates":{...}} object is tolerated so a
    misrouted/self-hosted v1 endpoint degrades gracefully instead of crashing the FX step.
    """
    base = base.upper()
    if isinstance(payload, dict) and isinstance(payload.get("rates"), dict):
        payload = [
            {"date": payload.get("date"), "base": payload.get("base"), "quote": q, "rate": r}
            for q, r in payload["rates"].items()
        ]
    if isinstance(payload, dict) and {"base", "quote", "rate"} <= payload.keys():
        payload = [payload]  # /v2/rate/{base}/{quote} returns a single object
    if not isinstance(payload, list):
        raise ValueError(f"unexpected Frankfurter payload: {type(payload).__name__}")
    out: dict[str, FrankfurterRate] = {}
    for rec in payload:
        if not isinstance(rec, dict):
            continue
        rec_base = str(rec.get("base") or "").upper()
        quote = str(rec.get("quote") or "").upper()
        rate = _decimal(rec.get("rate"))
        try:
            day = date.fromisoformat(str(rec.get("date")))
        except ValueError:
            continue
        if rec_base != base or not quote or quote == base or rate is None:
            continue
        prev = out.get(quote)
        if prev is None or day > prev.date:
            out[quote] = FrankfurterRate(base=base, quote=quote, rate=rate, date=day)
    return out


async def fetch_rates(
    client: httpx.AsyncClient,
    quotes: Iterable[str],
    *,
    base: str = "EUR",
    providers: Sequence[str] | str | None = None,
    base_url: str = DEFAULT_BASE_URL,
    headers: dict[str, str] | None = None,
    timeout: httpx.Timeout = DEFAULT_TIMEOUT,
) -> dict[str, FrankfurterRate]:
    """Latest rates for `quotes` against `base`; {} when the API has no data (404 / []).

    Raises httpx.HTTPError on transport/status errors, ValueError on a malformed payload.
    """
    wanted = sorted({q.upper() for q in quotes} - {base.upper()})
    if not wanted:
        return {}
    rates = await _get_rates(
        client,
        {"base": base.upper(), "quotes": ",".join(wanted)},
        base=base,
        providers=providers,
        base_url=base_url,
        headers=headers,
        timeout=timeout,
    )
    return {q: r for q, r in rates.items() if q in wanted}


async def fetch_all_rates(
    client: httpx.AsyncClient,
    *,
    base: str = "EUR",
    base_url: str = DEFAULT_BASE_URL,
    headers: dict[str, str] | None = None,
    timeout: httpx.Timeout = DEFAULT_TIMEOUT,
) -> dict[str, FrankfurterRate]:
    """Latest blended rate of every currency Frankfurter publishes against `base`."""
    return await _get_rates(
        client,
        {"base": base.upper()},
        base=base,
        providers=None,
        base_url=base_url,
        headers=headers,
        timeout=timeout,
    )


async def _get_rates(
    client: httpx.AsyncClient,
    params: dict[str, str],
    *,
    base: str,
    providers: Sequence[str] | str | None,
    base_url: str,
    headers: dict[str, str] | None,
    timeout: httpx.Timeout,
) -> dict[str, FrankfurterRate]:
    if providers:
        names = [providers] if isinstance(providers, str) else list(providers)
        params["providers"] = ",".join(p.upper() for p in names)
    resp = await client.get(
        f"{base_url.rstrip('/')}/v2/rates",
        params=params,
        headers={"Accept": "application/json", **(headers or {})},
        timeout=timeout,
        follow_redirects=True,
    )
    if resp.status_code == 404:
        return {}
    resp.raise_for_status()
    payload = resp.json(parse_float=Decimal, parse_int=Decimal)
    return parse_rates(payload, base=base)

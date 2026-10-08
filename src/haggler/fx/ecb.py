"""ECB euro foreign exchange reference rates (the daily eurofxref XML).

WHY first among unpinned sources: it is the official EUR reference, published around 16:00 CET
on TARGET business days for ~30 currencies, with no API key and a stable format since 2002.
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal, InvalidOperation
from xml.etree import ElementTree

import httpx

ECB_DAILY_URL = "https://www.ecb.europa.eu/stats/eurofxref/eurofxref-daily.xml"
DEFAULT_TIMEOUT = httpx.Timeout(20.0, connect=10.0)
ATTRIBUTION = {
    "name": "European Central Bank (euro foreign exchange reference rates)",
    "url": "https://www.ecb.europa.eu/stats/policy_and_exchange_rates/euro_reference_exchange_rates/html/index.en.html",
    "license": "ECB reuse policy: free reuse with source acknowledged",
}


def _local(tag: str) -> str:
    return tag.rsplit("}", 1)[-1]


def parse_ecb_daily(xml: str | bytes) -> tuple[date, dict[str, Decimal]]:
    """Parse the gesmes envelope; returns (rate date, {currency: units per EUR}).

    Namespace-agnostic on purpose: only the nested <Cube time=...><Cube currency rate/> shape
    matters. With the 90-day/historic files the first (newest) day is returned.
    """
    raw = xml.encode() if isinstance(xml, str) else xml
    head = raw[:2048].lower()
    if b"<!doctype" in head or b"<!entity" in head:
        raise ValueError("ECB XML with DTD/entities refused")
    root = ElementTree.fromstring(raw)
    for el in root.iter():
        when = el.get("time")
        if _local(el.tag) != "Cube" or not when:
            continue
        day = date.fromisoformat(when.strip())
        rates: dict[str, Decimal] = {}
        for child in el:
            cur = (child.get("currency") or "").strip().upper()
            try:
                rate = Decimal((child.get("rate") or "").strip())
            except InvalidOperation:
                continue
            if len(cur) == 3 and rate.is_finite() and rate > 0 and cur != "EUR":
                rates[cur] = rate
        if not rates:
            raise ValueError(f"ECB XML has no rates for {day}")
        return day, rates
    raise ValueError("ECB XML has no <Cube time=...> element")


async def fetch_ecb(
    client: httpx.AsyncClient,
    *,
    url: str = ECB_DAILY_URL,
    headers: dict[str, str] | None = None,
    timeout: httpx.Timeout = DEFAULT_TIMEOUT,
) -> tuple[date, dict[str, Decimal]]:
    """Raises httpx.HTTPError on transport/status errors, ValueError on a malformed file."""
    resp = await client.get(
        url,
        headers={"Accept": "application/xml,text/xml;q=0.9,*/*;q=0.5", **(headers or {})},
        timeout=timeout,
        follow_redirects=True,
    )
    resp.raise_for_status()
    return parse_ecb_daily(resp.content)

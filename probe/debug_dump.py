"""TEMPORARY: print real-page snippets to the CI log so adapters can be tuned offline."""
# ruff: noqa: E501

import asyncio
import json
import re

import httpx

from haggler.config import Settings
from haggler.fetch.client import HttpFetcher
from haggler.sources.spotify import page_lines


def around(lines, pattern, ctx=3, limit=120):
    rx = re.compile(pattern, re.IGNORECASE)
    keep = set()
    for i, ln in enumerate(lines):
        if rx.search(ln):
            keep.update(range(max(0, i - ctx), min(len(lines), i + ctx + 1)))
    out, prev = [], -2
    for i in sorted(keep)[:limit]:
        if i != prev + 1:
            out.append("…")
        out.append(f"{i:5d}| {lines[i][:220]}")
        prev = i
    return "\n".join(out)


def ms_summary(data):
    out = []
    for p in data.get("Products", []):
        out.append(
            f"PRODUCT {p.get('ProductId')} {p.get('LocalizedProperties', [{}])[0].get('ProductTitle')}"
        )
        for dsa in p.get("DisplaySkuAvailabilities", []):
            sku = dsa.get("Sku", {})
            lp = (sku.get("LocalizedProperties") or [{}])[0]
            props = sku.get("Properties", {})
            out.append(
                f"  SKU {sku.get('SkuId')} title={lp.get('SkuTitle')!r} type={sku.get('SkuType')} "
                f"props={ {k: props.get(k) for k in ('IsTrial', 'SkuDisplayRank', 'RecurrenceDuration', 'BundledSkus', 'IsRepurchasable', 'FulfillmentType') if k in props} }"
            )
            for av in dsa.get("Availabilities", []):
                omd = av.get("OrderManagementData", {})
                cond = av.get("Conditions", {})
                ap = av.get("Properties", {})
                out.append(
                    f"    AV {av.get('AvailabilityId')} actions={av.get('Actions')} price={omd.get('Price')} "
                    f"remed={av.get('RemediationRequired')} cond_end={cond.get('EndDate')} client={cond.get('ClientConditions')} "
                    f"props={ {k: ap.get(k) for k in list(ap)[:8]} } dur={av.get('LicensingData', {}).get('SatisfyingEntitlementKeys', '')!s:.0}"
                )
    return "\n".join(out[:200])


async def main():
    s = Settings()
    f = HttpFetcher(s)
    try:
        r = await f.fetch("https://support.apple.com/en-us/108047")
        lines = page_lines(r.text)
        print("==== ICLOUD euro / missing countries")
        print(
            around(
                lines,
                r"\bEUR\b|euro|Finland|Germany|Lithuania|Spain|Ireland|Netherlands|Portugal|Estonia|Latvia",
                4,
                160,
            )
        )
        for page in ("apple-one", "apple-music"):
            r = await f.fetch(f"https://www.apple.com/se/{page}/")
            lines = page_lines(r.text)
            print(f"==== APPLE SE {page}: {len(lines)} lines")
            print(around(lines, r"\d\s*kr|individ|personlig|familj|premier|student", 2, 200))
        for cc in ("SE", "US"):
            url = f"https://displaycatalog.mp.microsoft.com/v7.0/products?bigIds=CFQ7TTC0K5BF,CFQ7TTC0K5DM&market={cc}&languages=en-US"
            r = await f.fetch(url, accept="application/json")
            print(f"==== MS {cc}")
            print(ms_summary(json.loads(r.text)))
        async with httpx.AsyncClient(
            headers={"User-Agent": s.user_agent}, follow_redirects=True
        ) as c:
            for u in (
                "https://www.disneyplus.com/robots.txt",
                "https://help.disneyplus.com/robots.txt",
            ):
                rr = await c.get(u)
                print(f"==== {u} {rr.status_code}\n{rr.text[:2500]}")
        r = await f.fetch("https://apps.apple.com/se/app/id1446075923")
        lines = page_lines(r.text)
        print("==== DISNEY+ App Store SE")
        print(around(lines, r"\d\s*kr|in-app|köp inuti", 1, 80))
        r = await f.fetch("https://apps.apple.com/co/app/id1446075923")
        print("==== DISNEY+ App Store CO")
        print(around(page_lines(r.text), r"\$\s?\d|compras", 1, 80))
    finally:
        await f.aclose()


asyncio.run(main())

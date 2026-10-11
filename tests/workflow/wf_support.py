"""Shared helpers for the workflow tests: a small catalog subset, canned pages and Deps wiring.

Pages come from tests/sources/fixtures (the adapters' own fixtures) plus two prose pages under
tests/workflow/fixtures/pages that no parser can read, so the LLM fallback has work to do.
"""

from __future__ import annotations

import dataclasses
import json
from collections.abc import Callable
from datetime import UTC, date, datetime
from decimal import Decimal
from pathlib import Path
from typing import Any

from haggler.config import Settings
from haggler.fetch import RawResponse
from haggler.fx.service import StaticFxService
from haggler.jobs.daily import DailyOptions, run_daily
from haggler.models.catalog import Catalog
from haggler.models.fx import FxRate, FxTable
from haggler.models.run import RunReport
from haggler.pipeline.deps import Deps
from haggler.store import MemoryStore

TESTS = Path(__file__).resolve().parent.parent
SOURCE_FIXTURES = TESTS / "sources" / "fixtures"
PAGES = Path(__file__).resolve().parent / "fixtures" / "pages"
REPLAY_DIR = Path(__file__).resolve().parent / "fixtures" / "replay"

SERVICES = ("netflix", "spotify", "icloud_plus", "youtube_premium", "apple_one")
COUNTRIES = ("SE", "CO", "US")

NETFLIX = "https://help.netflix.com/en/node/24926/{cc}"
TOMPEC = "https://raw.githubusercontent.com/tompec/netflix-prices/main/data/latest.json"
SPOTIFY = "https://www.spotify.com/{cc}/premium/"
ICLOUD = "https://support.apple.com/en-us/108047"
YOUTUBE = "https://apps.apple.com/{cc}/app/id544007664"
APPLE_ONE = {
    "SE": "https://www.apple.com/se/apple-one/",
    "CO": "https://www.apple.com/co/apple-one/",
    "US": "https://www.apple.com/apple-one/",
}


def source(*parts: str) -> str:
    return SOURCE_FIXTURES.joinpath(*parts).read_text(encoding="utf-8")


def page(name: str) -> str:
    return PAGES.joinpath(name).read_text(encoding="utf-8")


def subset_catalog(full: Catalog) -> Catalog:
    return Catalog(
        services=[full.service(s) for s in SERVICES],
        countries=[full.country(c) for c in COUNTRIES],
        fx_pins={k: v for k, v in full.fx_pins.items() if k == "COP"},
    )


def fx_table(as_of: date = date(2026, 10, 7)) -> FxTable:
    rates = {"SEK": "11.20", "COP": "4700", "USD": "1.08"}
    return FxTable(
        as_of=as_of,
        rates={
            c: FxRate(currency=c, per_eur=Decimal(v), source="ecb", as_of=as_of)
            for c, v in rates.items()
        },
    )


def routes() -> dict[str, Any]:
    """Every URL the subset plans, mapped to a page (YouTube CO is left out: HTTP 404)."""
    r: dict[str, Any] = {
        NETFLIX.format(cc="se"): source("netflix_help", "se.html"),
        NETFLIX.format(cc="co"): source("netflix_help", "co.html"),
        NETFLIX.format(cc="us"): page("netflix_us_prose.html"),
        TOMPEC: source("tompec_netflix", "latest.json"),
        SPOTIFY.format(cc="se"): source("spotify_premium", "se.html"),
        SPOTIFY.format(cc="co"): source("spotify_premium", "co.html"),
        SPOTIFY.format(cc="us"): page("spotify_us_prose.html"),
        ICLOUD: source("icloud_support", "108047_en_us.html"),
        YOUTUBE.format(cc="se"): source("appstore_iap", "se_youtube_premium.html"),
        YOUTUBE.format(cc="us"): source("appstore_iap", "us_youtube_premium.html"),
        APPLE_ONE["SE"]: source("apple_marketing", "se_apple_one.html"),
        APPLE_ONE["CO"]: source("apple_marketing", "co_apple_one.html"),
        APPLE_ONE["US"]: source("apple_marketing", "us_apple_one.html"),
    }
    return r


class RecordingStore(MemoryStore):
    """MemoryStore that remembers the order of writes (run.json must come last)."""

    def __init__(self) -> None:
        super().__init__()
        self.writes: list[str] = []

    def write_bytes(self, key: str, data: bytes, *, content_type: str = "application/json") -> str:
        self.writes.append(key)
        return super().write_bytes(key, data, content_type=content_type)


class Clock:
    """Mutable `now` so consecutive runs can happen on different days."""

    def __init__(self, day: date = date(2026, 10, 8)) -> None:
        self.day = day

    def __call__(self) -> datetime:
        return datetime(self.day.year, self.day.month, self.day.day, 4, 17, tzinfo=UTC)


def make_deps(
    catalog: Catalog,
    fetcher: Any,
    *,
    model: Any = None,
    store: Any = None,
    clock: Callable[[], datetime] | None = None,
    **settings: Any,
) -> Deps:
    base = Settings()
    overrides: dict[str, Any] = {
        "store": "memory",
        "llm_concurrency": 2,
        "max_llm_calls": 50,
        "max_llm_jobs": 60,
        "max_adjudications": 15,
        "fetch_concurrency": 8,
        "quarantine_change_pct": 30.0,
    }
    overrides.update(settings)
    return Deps(
        settings=dataclasses.replace(base, **overrides),
        catalog=catalog,
        fetcher=fetcher,
        store=store if store is not None else RecordingStore(),
        fx=StaticFxService(fx_table()),
        model=model,
        now=clock or Clock(),
        fetch_retry_delay=0.01,
        fetch_timeout_s=10.0,
    )


async def run(deps: Deps, **options: Any) -> RunReport:
    report = await run_daily(DailyOptions(**options), deps)
    assert report is not None, "workflow produced no report"
    return report


def job_of(request_text: str) -> dict[str, Any]:
    """The LlmJob JSON a ScriptedLlm request carries (the agent sees only its node_input)."""
    start = request_text.find("{")
    return json.loads(request_text[start:]) if start >= 0 else {}


def item(plan_id: str, label: str, price: str, currency: str, evidence: str, period="month"):
    return {
        "plan_id": plan_id,
        "plan_label_raw": label,
        "price_text": price,
        "currency": currency,
        "billing_period": period,
        "evidence_snippet": evidence,
    }


NETFLIX_US_ITEMS = [
    item(
        "standard_with_ads",
        "most affordable way to watch",
        "US$8.99",
        "USD",
        "Our most affordable way to watch, which includes some ad breaks, is US$8.99 every month.",
    ),
    item(
        "standard",
        "ad-free option",
        "US$19.99",
        "USD",
        "The ad-free option that most households pick streams on two screens at once for "
        "US$19.99 every month.",
    ),
    item(
        "premium",
        "top tier",
        "US$26.99",
        "USD",
        "The top tier adds Ultra HD and four screens for US$26.99 every month.",
    ),
]


def extraction(items: list[dict[str, Any]], status: str = "ok") -> dict[str, Any]:
    return {"page_status": status, "items": items, "notes": ""}


def html_response(url: str, text: str, *, final_url: str | None = None) -> RawResponse:
    return RawResponse(
        url=url,
        final_url=final_url or url,
        status_code=200,
        text=text,
        fetched_at=datetime(2026, 10, 8, 4, 17, tzinfo=UTC),
    )

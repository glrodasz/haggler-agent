"""FX table builder (EUR base).

Priority per currency:
  1. pinned provider via Frankfurter v2 (`Catalog.fx_pins`, e.g. COP -> BANREP) when pinned;
  2. ECB euro reference rates (haggler.fx.ecb) for the ~30 currencies the ECB publishes;
  3. Frankfurter v2 blended rate (haggler.fx.frankfurter);
  4. open.er-api.com (haggler.fx.open_er_api), fetched with base EUR;
  5. the previous run's rate, marked stale=True (source="previous").
A rate older than 5 business days is marked stale, and a stale rate only stands if no later
source has a fresh one. EUR itself is implicit (never stored). Network errors in one provider
never raise out of build_table: they are logged, collected in `errors` and we fall through.
"""

from __future__ import annotations

import logging
from collections.abc import Awaitable
from datetime import date, timedelta
from decimal import Decimal, localcontext
from typing import Any, Protocol, TypeVar

import httpx

from haggler.fx.ecb import fetch_ecb
from haggler.fx.frankfurter import DEFAULT_BASE_URL, FrankfurterRate, fetch_rates
from haggler.fx.open_er_api import fetch_open_er_api
from haggler.models.fx import FxRate, FxTable

log = logging.getLogger(__name__)

STALE_AFTER_BUSINESS_DAYS = 5
# Significant digits kept for rates we derive (bridged or rebased); published ones are kept as is.
_DERIVED_DIGITS = 10

T = TypeVar("T")


class FxService(Protocol):
    async def build_table(
        self, currencies: set[str], *, as_of: date, previous: FxTable | None
    ) -> FxTable: ...


class StaticFxService:
    """Fixed table (tests / offline replay)."""

    def __init__(self, table: FxTable) -> None:
        self.table = table

    async def build_table(
        self, currencies: set[str], *, as_of: date, previous: FxTable | None
    ) -> FxTable:
        return self.table


def business_days_between(start: date, end: date) -> int:
    """Weekdays in (start, end]; 0 when start >= end. Holidays are ignored on purpose."""
    if start >= end:
        return 0
    full_weeks, extra = divmod((end - start).days, 7)
    count = full_weeks * 5
    for i in range(1, extra + 1):
        if (start + timedelta(days=i)).weekday() < 5:
            count += 1
    return count


def _derived(value: Decimal) -> Decimal:
    with localcontext() as ctx:
        ctx.prec = _DERIVED_DIGITS
        return +value


class _Picker:
    """Keeps the best rate per currency: the first fresh one wins; among stale ones the newest."""

    def __init__(self, wanted: list[str], as_of: date) -> None:
        self.wanted = wanted
        self.as_of = as_of
        self.best: dict[str, FxRate] = {}

    def offer(self, rate: FxRate) -> None:
        if rate.currency not in self.wanted:
            return
        too_old = business_days_between(rate.as_of, self.as_of) > STALE_AFTER_BUSINESS_DAYS
        if too_old and not rate.stale:
            rate = rate.model_copy(update={"stale": True})
        cur = self.best.get(rate.currency)
        if cur is None or (cur.stale and (not rate.stale or rate.as_of > cur.as_of)):
            self.best[rate.currency] = rate

    def need(self) -> list[str]:
        return [c for c in self.wanted if c not in self.best or self.best[c].stale]


class DefaultFxService:
    """Production FxService: official/pinned sources first, never failing the run."""

    def __init__(
        self,
        client: httpx.AsyncClient,
        pins: dict[str, str],
        *,
        frankfurter_base: str = DEFAULT_BASE_URL,
        user_agent: str | None = None,
    ) -> None:
        self.client = client
        self.pins = {cur.upper(): provider.upper() for cur, provider in pins.items()}
        self.frankfurter_base = frankfurter_base
        self.headers = {"User-Agent": user_agent} if user_agent else {}
        # Human-readable provider failures of the last build_table (for the run report).
        self.errors: list[str] = []
        self._memo: dict[str, Any] = {}

    async def build_table(
        self, currencies: set[str], *, as_of: date, previous: FxTable | None
    ) -> FxTable:
        self.errors = []
        self._memo = {}
        wanted = sorted({c.upper() for c in currencies} - {"EUR"})
        picker = _Picker(wanted, as_of)

        await self._pinned(picker)
        await self._ecb_tier(picker)
        await self._blended_tier(picker)
        await self._open_er_tier(picker)
        if previous is not None:
            for cur in picker.need():
                old = previous.rates.get(cur)
                if old is not None:
                    picker.offer(old.model_copy(update={"source": "previous", "stale": True}))

        missing = [c for c in wanted if c not in picker.best]
        if missing:
            self._error(f"fx: no rate at all for {','.join(missing)}")
        stale = sorted(c for c, r in picker.best.items() if r.stale)
        if stale:
            log.warning("fx: stale rates for %s", ",".join(stale))
        return FxTable(as_of=as_of, rates={c: picker.best[c] for c in sorted(picker.best)})

    # ---- tiers

    async def _pinned(self, picker: _Picker) -> None:
        by_provider: dict[str, list[str]] = {}
        for cur in picker.need():
            if cur in self.pins:
                by_provider.setdefault(self.pins[cur], []).append(cur)
        for provider, quotes in sorted(by_provider.items()):
            got = await self._safe(
                f"frankfurter[{provider}] EUR",
                fetch_rates(
                    self.client,
                    quotes,
                    base="EUR",
                    providers=provider,
                    base_url=self.frankfurter_base,
                    headers=self.headers,
                ),
            )
            for quote, r in (got or {}).items():
                picker.offer(
                    FxRate(
                        currency=quote,
                        per_eur=r.rate,
                        source="frankfurter",
                        provider=provider,
                        as_of=r.date,
                    )
                )
            # Only a successful-but-incomplete answer means "this provider has no EUR row";
            # an error means the API is unreachable, so later tiers take over.
            missing = [q for q in quotes if got is not None and q not in got]
            if missing:
                await self._pinned_via_usd(picker, provider, missing)

    async def _pinned_via_usd(self, picker: _Picker, provider: str, quotes: list[str]) -> None:
        """For providers publishing only against USD (BANREP: USD->COP), bridge USD->EUR."""
        got = await self._safe(
            f"frankfurter[{provider}] USD",
            fetch_rates(
                self.client,
                quotes,
                base="USD",
                providers=provider,
                base_url=self.frankfurter_base,
                headers=self.headers,
            ),
        )
        if not got:
            return
        bridge = await self._usd_per_eur()
        if bridge is None:
            self._error(f"fx: no USD/EUR bridge for {provider} rates")
            return
        usd_per_eur, bridge_date = bridge
        for quote, r in got.items():
            picker.offer(
                FxRate(
                    currency=quote,
                    per_eur=_derived(r.rate * usd_per_eur),
                    source="frankfurter",
                    provider=provider,
                    as_of=min(r.date, bridge_date),
                )
            )

    async def _ecb_tier(self, picker: _Picker) -> None:
        if not picker.need():
            return
        ecb = await self._ecb()
        if ecb is None:
            return
        day, rates = ecb
        for cur in picker.need():
            if cur in rates:
                picker.offer(
                    FxRate(
                        currency=cur, per_eur=rates[cur], source="ecb", provider="ECB", as_of=day
                    )
                )

    async def _blended_tier(self, picker: _Picker) -> None:
        need = picker.need()
        if not need:
            return
        got = await self._blended(need)
        for quote, r in got.items():
            picker.offer(FxRate(currency=quote, per_eur=r.rate, source="frankfurter", as_of=r.date))

    async def _open_er_tier(self, picker: _Picker) -> None:
        need = picker.need()
        if not need:
            return
        latest = await self._open_er()
        if latest is None:
            return
        day, rates = latest
        for cur in need:
            if cur in rates:
                picker.offer(
                    FxRate(
                        currency=cur, per_eur=_derived(rates[cur]), source="open_er_api", as_of=day
                    )
                )

    # ---- memoized provider calls (one request per provider per build)

    async def _ecb(self) -> tuple[date, dict[str, Decimal]] | None:
        if "ecb" not in self._memo:
            self._memo["ecb"] = await self._safe(
                "ecb", fetch_ecb(self.client, headers=self.headers)
            )
        return self._memo["ecb"]

    async def _open_er(self) -> tuple[date, dict[str, Decimal]] | None:
        if "open_er" not in self._memo:
            self._memo["open_er"] = await self._safe(
                "open_er_api", fetch_open_er_api(self.client, headers=self.headers)
            )
        return self._memo["open_er"]

    async def _blended(self, quotes: list[str]) -> dict[str, FrankfurterRate]:
        got = await self._safe(
            "frankfurter blended",
            fetch_rates(
                self.client,
                quotes,
                base="EUR",
                base_url=self.frankfurter_base,
                headers=self.headers,
            ),
        )
        return got or {}

    async def _usd_per_eur(self) -> tuple[Decimal, date] | None:
        """USD per EUR for bridging: ECB, else Frankfurter blended, else open.er-api."""
        ecb = await self._ecb()
        if ecb and "USD" in ecb[1]:
            return ecb[1]["USD"], ecb[0]
        blended = await self._blended(["USD"])
        if "USD" in blended:
            return blended["USD"].rate, blended["USD"].date
        latest = await self._open_er()
        if latest and "USD" in latest[1]:
            return latest[1]["USD"], latest[0]
        return None

    # ---- error handling

    def _error(self, message: str) -> None:
        log.warning(message)
        self.errors.append(message)

    async def _safe(self, name: str, call: Awaitable[T]) -> T | None:
        try:
            return await call
        except Exception as exc:  # any provider failure falls through to the next tier
            detail = str(exc).splitlines()[0][:200] if str(exc) else ""
            self._error(f"fx: {name} failed: {type(exc).__name__} {detail}".rstrip())
            return None

"""FX table builder (EUR base). CONTRACT — implementation lives in this module.

Priority per currency:
  1. pinned provider via Frankfurter v2 (`Catalog.fx_pins`, e.g. COP -> BANREP) when pinned;
  2. ECB euro reference rates (haggler.fx.ecb) for the ~29 currencies the ECB publishes;
  3. Frankfurter v2 blended rate (haggler.fx.frankfurter);
  4. open.er-api.com (haggler.fx.open_er_api), USD-based, converted to EUR base;
  5. the previous run's rate, marked stale=True (source="previous").
A rate older than 5 business days is marked stale. EUR itself is implicit (never stored).
Network errors in one provider must never raise out of build_table: fall through to the next.
"""

from __future__ import annotations

from datetime import date
from typing import Protocol

from haggler.models.fx import FxTable


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

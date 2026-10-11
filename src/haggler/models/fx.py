"""Exchange rates (EUR base)."""

from __future__ import annotations

from datetime import date
from decimal import Decimal

from pydantic import BaseModel, ConfigDict, Field


class FxRate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    currency: str
    # Units of `currency` per 1 EUR.
    per_eur: Decimal
    source: str  # ecb | frankfurter | open_er_api | previous
    provider: str | None = None  # e.g. BANREP when pinned through Frankfurter
    as_of: date
    stale: bool = False


class FxTable(BaseModel):
    model_config = ConfigDict(extra="forbid")

    base: str = "EUR"
    as_of: date
    rates: dict[str, FxRate] = Field(default_factory=dict)

    def per_eur(self, currency: str) -> Decimal | None:
        if currency == "EUR":
            return Decimal(1)
        rate = self.rates.get(currency)
        return rate.per_eur if rate else None

    def to_eur(self, amount: Decimal, currency: str) -> Decimal | None:
        per = self.per_eur(currency)
        if per is None or per == 0:
            return None
        return (amount / per).quantize(Decimal("0.0001"))

    def convert(self, amount: Decimal, src: str, dst: str) -> Decimal | None:
        eur = self.to_eur(amount, src)
        per = self.per_eur(dst)
        if eur is None or per is None:
            return None
        return (eur * per).quantize(Decimal("0.01"))

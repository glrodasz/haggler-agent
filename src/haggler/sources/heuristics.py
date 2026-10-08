"""Shared text heuristics for marketing/help pages: pair plan labels with nearby prices."""

from __future__ import annotations

import re
from dataclasses import dataclass

from haggler.models.catalog import ServiceDef
from haggler.models.observations import BillingPeriod
from haggler.money import Money, find_prices
from haggler.sources.base import match_plan

_MONTH = re.compile(
    r"(/\s*(mo|mth|month|mes|mån|månad|md|mnd|mois|monat|mese|ay|mies|měs|hó|lună|mês|maand)\b"
    r"|per\s+month|a\s+month|al\s+mes|por\s+mes|i\s+månaden|pr\.?\s*md|par\s+mois|pro\s+monat"
    r"|monthly|mensual|mensal|månadsvis|aylık|miesięcznie|/m\b)",
    re.IGNORECASE,
)
_YEAR = re.compile(
    r"(/\s*(yr|year|año|ano|år|an|jahr|anno|yıl|rok|év|jaar)\b|per\s+year|a\s+year|al\s+año"
    r"|por\s+año|om\s+året|par\s+an|pro\s+jahr|annual|yearly|anual|årlig|yıllık|rocznie)",
    re.IGNORECASE,
)
# Text that marks an introductory / trial price we must not record as the regular price.
_INTRO = re.compile(
    r"(free\s+for|gratis|0\s*kr\s+i|for\s+\d+\s+months?|first\s+\d+\s+months?|primer(o|os)\s+\d+"
    r"|trial|prueba|provperiod|then|after|därefter|luego|después|danach|ensuite)",
    re.IGNORECASE,
)


def detect_period(text: str) -> BillingPeriod | None:
    if _YEAR.search(text):
        return "year"
    if _MONTH.search(text):
        return "month"
    return None


@dataclass
class PlanPrice:
    plan_id: str
    label: str
    money: Money
    period: BillingPeriod
    evidence: str
    intro_context: bool


def plan_price_pairs(
    lines: list[str],
    svc: ServiceDef,
    expected: list[str],
    *,
    window: int = 6,
    default_period: BillingPeriod = "month",
) -> list[PlanPrice]:
    """For each plan label found in `lines`, the first regular price within `window` lines.

    A price is skipped when it is zero, in an unexpected currency, or appears in a line that reads
    like an introductory offer AND a later price exists in the same window (e.g. "0 kr for 1 month,
    then 129 kr/month" keeps 129).
    """
    found: dict[str, PlanPrice] = {}
    for i, line in enumerate(lines):
        plan_id = match_plan(svc, line)
        if plan_id is None or plan_id in found:
            continue
        candidates: list[PlanPrice] = []
        for j in range(i, min(len(lines), i + window)):
            if j > i and match_plan(svc, lines[j]) not in (None, plan_id):
                break  # reached the next plan's card
            for money in find_prices(lines[j], expected):
                if money.amount <= 0 or (expected and money.currency not in expected):
                    continue
                tail = lines[j][money.end : money.end + 40]
                period = detect_period(tail) or detect_period(lines[j]) or default_period
                candidates.append(
                    PlanPrice(
                        plan_id=plan_id,
                        label=line,
                        money=money,
                        period=period,
                        evidence=" ".join(lines[i : j + 1])[-240:],
                        intro_context=bool(_INTRO.search(lines[j][: money.start])),
                    )
                )
        if not candidates:
            continue
        regular = [c for c in candidates if not c.intro_context] or candidates
        monthly = [c for c in regular if c.period == "month"]
        found[plan_id] = (monthly or regular)[0]
    return list(found.values())

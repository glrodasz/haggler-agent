"""Shared text heuristics for marketing/help pages: what a price in a line *is*, and plan labels
paired with nearby prices.

Every price found in a line gets a role and a billing period:
  * period: a label right before the price ("Monthly: $10.99 · Annual: $109" -> 10.99 month,
    109 year), else the first explicit marker between the price and the next price or its label
    ("109 kr/mån eller 1 090 kr/år" -> 109 month, 1090 year); a lone price may also take one
    stated before it ("Monthly 129 kr"); None when nothing is stated. A short next line lends
    its marker ("<div>/år</div>") unless it holds a price or a label of its own.
  * role:
      - "savings": the amount saved, not a price ("Spara 124 kr/mån", "Save over $8/mo.");
      - "compare": a struck-through or comparison price ("Inte 339 kr/mån", "Not $29.95",
        "statt 9,99 €", "au lieu de", "en lugar de", "antes", "regular price", "$27.95
        separately");
      - "intro": an introductory offer on either side of the price ("3 months for $2.99",
        "$2.99/mo. for 3 months", "0 kr i 1 månad"); "then/därefter/luego $X" marks the
        regular price that follows an offer;
      - "regular": anything else.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Literal

from haggler.models.catalog import ServiceDef
from haggler.models.observations import BillingPeriod
from haggler.money import Money, find_prices
from haggler.sources.base import match_plan

PriceRole = Literal["regular", "intro", "compare", "savings"]

# ---- billing period markers ----------------------------------------------------------------

_MONTH_WORD = (
    r"(?:month|måna?d(?:en)?|måned(?:en)?|maand|mês|mes|monat|mois|mese|miesiąc|měsíc|hónap|ay"
    r"|bulan|tháng|mnd)"
)
_YEAR_WORD = r"(?:year|år(?:et)?|jahr|an|año|ano|anno|jaar|rok|yıl|év)"
_PER = r"(?:per|pr\.?|a|al|por|par|pro|i|om|w|na|za|each)"
_MONTH = re.compile(
    rf"(/\s*(mo|mth|month|mes|mån|månad|md|mnd|mois|monat|mese|ay|miesiąc|mies|měs|hó|lună|mês|maand"
    rf"|kk|kuukausi|mėn|mēn|kuu)(?![^\W\d_])"
    rf"|\b{_PER}\s+{_MONTH_WORD}\b|al\s+mes|i\s+månaden|pr\.?\s*md|par\s+mois"
    rf"|monthly|mensual|mensal|månadsvis|aylık|miesięcznie|kuukaudessa|/m\b)",
    re.IGNORECASE,
)
_YEAR = re.compile(
    rf"(/\s*(yr|year|año|ano|år|an|jahr|anno|yıl|rok|év|jaar|v|vuosi|metus|metai|gadā|gads"
    rf"|aasta)(?![^\W\d_])"
    rf"|\b{_PER}\s+{_YEAR_WORD}\b|al\s+año|om\s+året|i\s+året|par\s+an|all['’]anno|per\s+annum"
    rf"|annual|yearly|anual|årlig|årsvis|yıllık|rocznie|vuodessa)",
    re.IGNORECASE,
)
_QUARTER = re.compile(
    r"(/\s*(quarter|qtr|kvartal|trimestre)(?![^\W\d_])|\bquarterly\b|\btrimestral\w*"
    r"|\bkvartals\w*|\bper\s+quarter\b|\bvierteljährlich\b)",
    re.IGNORECASE,
)


_PERIOD_MARKERS: tuple[tuple[BillingPeriod, re.Pattern[str]], ...] = (
    ("month", _MONTH),
    ("year", _YEAR),
    ("quarter", _QUARTER),
)


def period_marker(text: str) -> BillingPeriod | None:
    """The billing period whose marker appears first in `text` (None when there is none).

    Earliest wins: "/mo. or $109/yr." is monthly for the price it follows.
    """
    best: tuple[int, BillingPeriod] | None = None
    for period, rx in _PERIOD_MARKERS:
        m = rx.search(text)
        if m and (best is None or m.start() < best[0]):
            best = (m.start(), period)
    return best[1] if best else None


def detect_period(text: str) -> BillingPeriod | None:
    """Billing period stated in `text` (the first marker wins)."""
    return period_marker(text)


def price_period(tail: str) -> BillingPeriod | None:
    """Billing period stated right after a price (pass only the text up to the next price)."""
    return period_marker(tail)


# A period label written before its price ("Monthly: $10.99 · Annual: $109", "Yıllık: ₺399").
_LABEL = (
    r"(?<![^\W\d_])(?:monthly|mensual|mensal|månadsvis|aylık|miesięcznie|annual(?:ly)?|yearly"
    r"|anual|årlig|årsvis|yıllık|rocznie|quarterly|trimestral)\s*:"
)
_LABEL_END = re.compile(_LABEL + r"\s*$", re.IGNORECASE)
_LABEL_ANY = re.compile(_LABEL, re.IGNORECASE)


def label_period(before: str) -> BillingPeriod | None:
    """Period of a label that ends `before`, the text since the previous price ("Annual: ")."""
    m = _LABEL_END.search(before[-40:])
    return period_marker(m.group()) if m else None


def price_tail(text: str, end: int, nxt: int, limit: int) -> str:
    """The text after a price that may state its period: from `end` up to the next price at
    `nxt`, without that price's own label ("$10.99 · Annual: $109" -> " · "), at most `limit`
    characters."""
    tail = text[end:nxt]
    if m := _LABEL_END.search(tail):
        tail = tail[: m.start()]
    return tail[:limit]


def lends_period(next_text: str, expected: list[str]) -> bool:
    """A short next line may hold the period split off a price by markup ("<div>/år</div>"), but
    not one with a price or a label of its own ("Monthly: $10.99", "Annual:")."""
    return (
        0 < len(next_text) <= 20
        and not _LABEL_ANY.search(next_text)
        and not find_prices(next_text, expected)
    )


# ---- introductory offers -------------------------------------------------------------------

_N = (
    r"(?:\d+|one|two|three|six|twelve|en|ett|två|tre|sex|un|uno|una|dos|tres|seis|ein|einen"
    r"|eine|zwei|drei|een|twee|drie|jeden|dwa|trzy|bir|iki|üç)"
)
_UNIT = (
    r"(?:months?|mo|månad(?:er)?|måneder?|måned|mes(?:es)?|mês|meses|mois|monat(?:e|en)?"
    r"|maand(?:en)?|kuukau\w*|miesi\w*|měsíc\w*|hónap\w*|lun[aiă]\w*|ay|bulan|tháng"
    r"|weeks?|veckor|vecka|semanas?|days?|dagar|días?|dias)"
)
_LEAD = r"(?:the\s+first\s+|de\s+första\s+|los\s+primeros\s+|las\s+primeras\s+|the\s+next\s+)?"
# "$2.99/mo. for 3 months", " i 1 månad", "/mes durante 3 meses" right after the price. The unit
# may be glued to the next sentence by markup ("i 6 månaderIngen bindningstid": an upper-case
# letter), never to more of a lower-case word.
_INTRO_AFTER = re.compile(
    rf"^\s*(?:/\s*[^\s\d/]{{1,8}}\.?)?[\s,]*(?:[^\W\d_]+[^\w/]+){{0,2}}?"
    rf"(?:for|för|i|in|por|para|durante|pendant|für|voor|per|na|za|po|için|selama|trong|på"
    rf"|under|x)\s+{_LEAD}{_N}\s+{_UNIT}(?-i:(?![a-zß-öø-ÿ0-9_]))",
    re.IGNORECASE,
)
# "3 months for ", "Try 1 month of Premium for ", "första månaden för " right before the price.
_INTRO_BEFORE = re.compile(
    rf"(?:{_N}\s+{_UNIT}|first\s+{_UNIT}|första\s+{_UNIT}|primer\s+mes)"
    rf"(?:\s+(?:of|av|de|von|med)\s+[\w+ ]{{1,25}}?)?"
    r"\s+(?:for|för|por|à|pour|für|voor|za|na|på|med|at)\s*[:\-–]?\s*$",
    re.IGNORECASE,
)
# A price right after these words is the regular one ("Free for 1 month, then $10.99/mo.").
_THEN = re.compile(
    r"(?<!\w)(then|after(wards)?|thereafter|därefter|sedan|luego|despu[ée]s|danach|anschließend"
    r"|ensuite|puis|derefter|deretter|sonra|potem|depois|daarna|poi|jälkeen|potom|apoi)"
    r"\W*(\w+\W+){0,2}$",
    re.IGNORECASE,
)
# Offer words in the clause before a price ("Free trial, then...": the clause is cut at ". ").
_INTRO_WORDS = re.compile(
    r"free|gratis|gratuit|kostenlos|ücretsiz|trial|prueba|provperiod|prøve|essai|first|primer"
    r"|första|intro|special|oferta|erbjudande|offer|angebot|promo",
    re.IGNORECASE,
)
_CLAUSE = re.compile(r"[.!?;•|]\s")


def follows_then(before: str) -> bool:
    """True when `before`, the text before a price, ends in "then/därefter/luego ...": the price
    is the regular one that follows an offer ("Free for 1 month, then $10.99/mo.")."""
    return bool(_THEN.search(before[-80:]))


def is_intro(before: str, after: str, *, words: bool = False) -> bool:
    """True for introductory prices: "0 kr i 1 månad", "3 months for €0", "€5 for 3 months".

    `before`/`after` are the text around the price. "then/därefter/luego ..." right before a
    price marks it as the regular price. With words=True an offer word ("free", "trial",
    "erbjudande") earlier in the same clause also counts.
    """
    prefix = before[-80:]
    if _THEN.search(prefix):
        return False
    if _INTRO_AFTER.match(after) or _INTRO_BEFORE.search(prefix):
        return True
    return words and bool(_INTRO_WORDS.search(_CLAUSE.split(prefix)[-1]))


# ---- comparison and savings ----------------------------------------------------------------

# Marker immediately before a struck-through / comparison price ("Inte 339 kr", "Not $29.95").
_COMPARE_BEFORE = re.compile(
    r"(?<!\w)(?:not|inte|ikke|ej|ei|nicht|nie|non|não|niet|no|statt|anstatt|instead\s+of"
    r"|i\s+st[äa]llet\s+för|ist[äa]llet\s+för|i\s+stedet\s+for|au\s+lieu\s+de|en\s+lugar\s+de"
    r"|en\s+vez\s+de|em\s+vez\s+de|ao\s+inv[ée]s\s+de|invece\s+di|zamiast|antes|was|previously"
    r"|originally|regular(?:ly)?\s+pric(?:e|ed)(?:\s+(?:of|at))?|precio\s+regular(?:\s+de)?"
    r"|tidigare|vorher|ehemals|value\s+of|valor\s+de|valued\s+at|worth|total\s+value(?:\s+of)?)"
    r"\s*[:\-–]?\s*$",
    re.IGNORECASE,
)
# Marker right after a comparison price ("$27.95/mo. separately", "79,99 TL yerine").
_COMPARE_AFTER = re.compile(
    r"^\s*(?:/\s*[^\s\d/]{1,8}\.?\s*)?(?:\(?\s*)?(?:when\s+(?:bought|purchased)\s+)?"
    r"(?:separately|por\s+separado|separat\w*|einzeln|individually|yerine|in\s+total\s+value)\b",
    re.IGNORECASE,
)
_SAVE_FILLER = (
    r"(?:over|up\s+to|upp\s+till|upp\s+mot|över|mer\s+än|more\s+than|nearly|almost|about"
    r"|around|hasta|más\s+de|bis\s+zu|über|plus\s+de|jusqu.à|até|oltre|op\s+til|opp\s+til|yli"
    r"|sie|du|ca\.?|cirka|ungefär|of|de)"
)
# The amount saved ("Spara 124 kr/mån", "Save over $8/mo.", "Ahorra hasta $ 10.000").
_SAVINGS_BEFORE = re.compile(
    rf"(?<!\w)(?:save|saves|saving|savings|you\s+save|spara|sparar|spar|ahorra\w*|ahórra\w*"
    rf"|economiz\w*|économis\w*|sparen|sparst|risparmi\w*|oszczędź\w*|ušetř\w*"
    rf"|tasarruf\s+edin|säästä\w*)\s+(?:{_SAVE_FILLER}\s+){{0,2}}$",
    re.IGNORECASE,
)
_SAVINGS_AFTER = re.compile(
    r"^\s*(?:/\s*[^\s\d/]{1,8}\.?\s*)?(?:i\s+|in\s+|en\s+|of\s+)?"
    r"(?:savings?|besparing\w*|ahorro|d.économies?|ersparnis|risparmio|sparen)\b",
    re.IGNORECASE,
)


def is_comparison(before: str, after: str) -> bool:
    return bool(_COMPARE_BEFORE.search(before[-60:]) or _COMPARE_AFTER.match(after))


def is_savings(before: str, after: str) -> bool:
    return bool(_SAVINGS_BEFORE.search(before[-60:]) or _SAVINGS_AFTER.match(after))


@dataclass(frozen=True)
class PriceInfo:
    money: Money
    period: BillingPeriod | None  # explicit marker (None = not stated)
    role: PriceRole


def classify_prices(
    text: str,
    expected: list[str],
    *,
    next_text: str = "",
    intro_words: bool = False,
    currency_words: bool = False,
) -> list[PriceInfo]:
    """Every price in `text` with its stated period and role (see the module docstring).

    `next_text` is the following line: a short one holds a period split off by markup
    ("<span>1 090 kr</span><div>/år</div>"). currency_words=True also reads amounts followed by
    a currency word ("19900 pesos", see haggler.money.find_prices).
    """
    found = find_prices(text, expected, words=currency_words)
    out: list[PriceInfo] = []
    prev_end = 0
    for k, m in enumerate(found):
        nxt = found[k + 1].start if k + 1 < len(found) else len(text)
        before, after = text[prev_end : m.start], text[m.end : nxt]
        tail = price_tail(text, m.end, nxt, 40)
        if not tail.strip() and k + 1 == len(found) and lends_period(next_text, expected):
            tail = next_text
        period = label_period(before) or period_marker(tail)
        if period is None and len(found) == 1:
            period = period_marker(text[: m.start])  # "Monthly 129 kr", "Årsvis 1 290 kr"
        role: PriceRole
        if is_savings(before, after):
            role = "savings"
        elif is_comparison(before, after):
            role = "compare"
        elif is_intro(before, after, words=intro_words) or is_intro(text[: m.start], ""):
            # The second check sees an offer ("3 months for") in the whole line, in case an
            # earlier match took its count; offer words stay limited to `before`.
            role = "intro"
        else:
            role = "regular"
        out.append(PriceInfo(m, period, role))
        prev_end = m.end
    return out


# ---- plan label / price pairing ------------------------------------------------------------


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
    intro_words: bool = True,
) -> list[PlanPrice]:
    """For each plan label found in `lines`, the first regular price within `window` lines.

    The window ends at the next plan's label. Zero prices, prices in an unexpected currency,
    savings amounts, comparison prices and intro offers are never taken; a monthly price wins
    over other periods ("109 kr/mån eller 1 090 kr/år" -> 109). A plan whose window holds only
    intro offers gets no price at all (the pipeline's LLM fallback sees the page instead).
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
            nxt = lines[j + 1] if j + 1 < len(lines) else ""
            for info in classify_prices(lines[j], expected, next_text=nxt, intro_words=intro_words):
                money = info.money
                if money.amount <= 0 or (expected and money.currency not in expected):
                    continue
                if info.role in ("savings", "compare"):
                    continue
                candidates.append(
                    PlanPrice(
                        plan_id=plan_id,
                        label=line,
                        money=money,
                        period=info.period or default_period,
                        evidence=" ".join(lines[i : j + 1])[-240:],
                        intro_context=info.role == "intro",
                    )
                )
        regular = [c for c in candidates if not c.intro_context]
        if not regular:
            continue
        monthly = [c for c in regular if c.period == "month"]
        found[plan_id] = (monthly or regular)[0]
    return list(found.values())

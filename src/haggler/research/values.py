"""Value-vs-quote check: does a cited excerpt really state the value a fact claims?

A quote found on an official page proves only that the page says *something*; `check_value`
proves that it says THIS value. It runs where a bundle value meets a quote: the research agent's
bundle facts (value hook of verify_subject), the watcher's price hints and, offline, the anchors
of bundles.yaml (haggler.catalog.integrity_errors, anchor text alone, no page context).

Kinds (what H8's bundle fields map to):
  * money / intro_money / base_money (bundle_price / bundle_intro_price / bundle_base_price):
    a value like "21900 COP/month" (haggler.money.parse_money_code), or a Decimal with the
    `currency` and `period` arguments. The prices of the quote, plus its context lines
    (Page.locate: 2 lines before, 1 after) when given, are read with
    haggler.sources.heuristics.classify_prices (amounts, currencies, periods) and given a role
    (see "Roles" below). Inside the quote's span there must be:
      - the same amount, in the bundle's currency. A bare "$" is read as the bundle's currency
        only when that currency is written "$" (COP, MXN, ARS, CLP, USD ...: find_prices'
        `expected`), never for a SEK or BRL bundle, and an ISO code or a nationality written
        next to it wins ("$ 399 MXN", "MXN $399" and "$ 399 pesos mexicanos" are never COP).
        With `url` and `provider`, the page itself must be on the provider's domains for the
        bundle's country (a Mexican "$ 399" page never confirms a Colombian price);
      - the same period, when the quote states one ("/año" never confirms a monthly price);
      - the role "regular" ("intro" for intro_money);
      - when the provider sells several tiers in the country (several bundles with the same
        provider and country), the bundle's own tier named for the amount (its aliases or
        name; the base plan's name for base_money). The text is cut into clauses (line breaks,
        sentence ends, ";", ", " and the conjunctions "y / e / o / ou / och / eller / and /
        or"); the tier named in the amount's own clause decides, before or after the amount
        ("215 kr i månaden för det personliga abonnemanget och 295 kr ... för
        familjeabonnemanget"); a clause without a tier name takes the nearest earlier clause
        that has one (a card's heading two lines up), never a later one (the next card's
        heading); a clause naming two tiers confirms neither. A name inherited by another tier
        ("todos los beneficios de Meli+ Esencial", "everything in Individual") is not a tier
        name.
    Then plausibility, with an FX table: a monthly equivalent outside 0.5-300 EUR is
    "implausible" (an intro price may be 0); without one the check is skipped and the reason
    says so. A change of more than 50 % of the monthly equivalent against `current` (the YAML
    value: a Decimal in the bundle's period, or the same "21900 COP/month" form) sets
    big_change; the status stays ok.
  * int / pct: the number is a number token of the quote, not part of a larger number ("2" is
    not in "$ 21.900", "12" not in "31/12/2026", "7" not in "24/7") nor of a date, and without
    a leading zero ("06"); 2-12 also count as words (es/pt/sv/en: "dos", "seis", "sex", "six";
    Spanish "once" is left out, it is also English).
  * date: haggler.dates.find_dates finds that date in the quote (the year is required).
  * component: "service_id", "service_id.plan_id" or "service_id:N" (a discount or a count of
    free months). The catalog name of the service or one of its aliases appears in the quote
    (case and accents ignored, whole words); "N" must be a number token of the service's own
    clause, or of the nearest earlier clause with a number ("30% OFF en HBO Max, Universal+ y
    ViX Premium": 30 for all three), or of the next clause with a number when that clause
    names no other service; "20% OFF en Disney+ y 30% OFF en HBO Max" gives Disney+ 20 only. A
    plan is checked only when the text after the service's name names one: naming ANOTHER
    plan of the service fails, naming none passes (plan aliases and labels are incomplete
    across languages).
  * url: a URL has nothing to find in a quote: ok only when it is https on the provider's
    domains for the bundle's country (haggler.catalog.on_domains).

Roles. classify_prices' own roles are recomputed here with the same predicates
(haggler.sources.heuristics.is_savings / is_comparison / is_intro), with three refinements that
only apply to value checks (the daily price pipeline keeps its own rules):
  * a line break ends what may describe a price: an introductory duration on the NEXT line
    ("$ 19.900/mes" / "por 2 meses", or "295 kr/mån" / "Prova gratis i 1 månad") cannot be
    told apart from the next item's, so the price is an "offer" that confirms neither a
    regular nor an intro value; labels on the previous line ("Antes" / "$ 24.900") still
    count;
  * an introductory price is structural, never an offer word alone: a duration tied to the
    price (classify_prices' patterns, plus "los primeros 2 meses", "(2 meses)", "de första 6
    månaderna", "i 6 mån", "nos 2 primeiros meses" after it and "2 meses a" before it).
    An offer word earlier in the price's own line and clause ("Precio especial: $ 19.900",
    "Oferta", "Prova gratis") without one makes the price an "offer" too ("envíos gratis",
    "frete grátis", "free shipping" and "ad-free" are not offer words);
  * the previous-price rule below.

Decision ("precio anterior" next to an introductory price): pages state an introductory offer
together with the price that follows it, labeled as the previous or ordinary price ("Precio
actual con 9 por ciento de descuento: 19900 pesos por mes, por 2 meses. Precio anterior:
$ 21.900 pesos por mes."; Telia: "Ordinarie pris: 269 kr/mån.Pris: 199 kr/mån.Rabatten gäller i
6 månader"). A price labeled previous ("precio anterior", "antes", "was", "tidigare", the
"De R$ 29,90 por R$ 19,90" idiom ...) or ordinary ("ordinarie pris", "precio regular / normal",
"regular price", "full price" ...) is the REGULAR price, what the subscriber pays once the offer
ends, only when the price right before or after it, on the same line, is a lower introductory
price (its duration stated as above) of the same tier, and no other regular price of that line
("luego $ X", "ahora $ X") competes with it. Otherwise a previous price is a "compare"
(struck-through) price, and so is an ordinary price next to any lower price of the text; an
ordinary price with no lower price around ("Ordinarie pris: 269 kr/mån.") is simply the regular
price. Comparison labels that
name another product ("not", "instead of", "separately", "value of") stay "compare" either way.
"""

from __future__ import annotations

import bisect
import re
import unicodedata
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from datetime import date
from decimal import Decimal, InvalidOperation
from typing import TYPE_CHECKING, Literal

from haggler.dates import find_dates
from haggler.money import ISO_CODES, format_money_code, parse_money_code, parse_number
from haggler.sources.heuristics import (
    PriceInfo,
    classify_prices,
    follows_then,
    is_comparison,
    is_intro,
    is_savings,
)

if TYPE_CHECKING:
    from haggler.models.catalog import Catalog, ServiceDef
    from haggler.models.curated import Bundle, Provider
    from haggler.models.fx import FxTable

ValueKind = Literal["money", "intro_money", "base_money", "int", "pct", "date", "component", "url"]
ValueStatus = Literal["ok", "value_not_in_quote", "implausible"]
MONEY_KINDS: tuple[ValueKind, ...] = ("money", "intro_money", "base_money")

# Monthly equivalent, in EUR, that a bundle price may have.
PLAUSIBLE_MONTHLY_EUR = (Decimal("0.5"), Decimal("300"))
# A change against the current value above this fraction is flagged for review.
BIG_CHANGE = Decimal("0.5")
_MONTHS_PER = {"month": 1, "quarter": 3, "year": 12}


@dataclass(frozen=True)
class ValueCheck:
    """Outcome of check_value. `reason` is English, for logs and review output."""

    status: ValueStatus
    reason: str = ""
    big_change: bool = False

    @property
    def ok(self) -> bool:
        return self.status == "ok"


def _ok(reason: str = "", *, big_change: bool = False) -> ValueCheck:
    return ValueCheck("ok", reason, big_change)


def _missing(reason: str) -> ValueCheck:
    return ValueCheck("value_not_in_quote", reason)


# ---- text folding with offsets --------------------------------------------------------------

_DASHES = re.compile(r"[‐-―−]")


def _fold_char(ch: str) -> str:
    """NFKC, case-folded, without accents; dashes and the typographic apostrophe made ASCII."""
    folded = unicodedata.normalize("NFKC", ch).casefold()
    folded = "".join(
        c for c in unicodedata.normalize("NFD", folded) if not unicodedata.combining(c)
    )
    return _DASHES.sub("-", folded).replace("’", "'")


def _fold_map(text: str) -> tuple[str, list[int]]:
    """`text` folded (see _fold_char) with whitespace runs collapsed to one space, and for every
    folded character the index of the character of `text` it comes from."""
    out: list[str] = []
    index: list[int] = []
    space = True  # drops leading whitespace
    for i, ch in enumerate(text):
        if ch.isspace() or ch == "\u200b":
            if not space:
                out.append(" ")
                index.append(i)
            space = True
            continue
        for c in _fold_char(ch):
            out.append(c)
            index.append(i)
        space = False
    if out and out[-1] == " ":
        out.pop()
        index.pop()
    return "".join(out), index


def _fold(text: str) -> str:
    return _fold_map(text)[0]


def _name_pattern(name: str) -> re.Pattern[str] | None:
    folded = _fold(name)
    if not folded:
        return None
    body = re.escape(folded).replace(r"\ ", r"\s+")
    # Whole words: no letter or digit glued to an alphanumeric end ("Max" is not in "Maxad").
    before = r"(?<![^\W_])" if folded[0].isalnum() else ""
    after = r"(?![^\W_])" if folded[-1].isalnum() else ""
    return re.compile(before + body + after)


def _mentions(folded: str, names: Iterable[str]) -> list[tuple[int, int, str]]:
    """(start, end, folded name) in `folded` for every mention of one of `names`; the longest
    name wins an overlap."""
    hits: list[tuple[int, int, str]] = []
    for name in dict.fromkeys(names):
        if (rx := _name_pattern(name)) is not None:
            hits.extend((m.start(), m.end(), _fold(name)) for m in rx.finditer(folded))
    hits.sort(key=lambda h: (h[0] - h[1], h[0]))
    kept: list[tuple[int, int, str]] = []
    for s, e, name in hits:
        if all(e <= ks or s >= ke for ks, ke, _ in kept):
            kept.append((s, e, name))
    return sorted(kept)


# ---- clauses --------------------------------------------------------------------------------

# Where a clause ends, for pairing a tier name or a discount with its amount: a line break, a
# sentence end ("." before a space, the end or a capital: "kr/mån.Pris"), "!", "?", ";", "•",
# "|", a comma before a space, and the conjunctions (es / pt / sv / en).
_CLAUSE_BREAK = re.compile(
    r"\n|[!?;•|]|\.(?=\s|$|[A-ZÀ-ÖØ-Þ])|,(?=\s)|(?i:\s(?:y|e|o|ou|och|eller|samt|and|or)(?=\s))"
)


def _clause_starts(text: str) -> list[int]:
    """The start offset of every clause of `text` (see _CLAUSE_BREAK), in order."""
    return [0, *(m.end() for m in _CLAUSE_BREAK.finditer(text))]


def _clause_of(starts: list[int], pos: int) -> int:
    return bisect.bisect_right(starts, pos) - 1


# ---- numbers --------------------------------------------------------------------------------

# A number not glued to a larger one: no digit, decimal separator or "digit/" "digit-" before
# it, no digit or "/digit" "-digit" ".digit" after it ("31/12/2026", "24/7", "01-8000-30").
_NUMBER_TOKEN = re.compile(r"(?<![\d.,])(?<!\d[/\-])\d+(?:[.,]\d+)*(?!\d|[.,/\-]\d)")
# Folded number words (es / pt / sv / en). 1 is left out: "un", "en", "ett" are also articles
# and prepositions; Spanish "once" (11) is too, it is also English ("billed once a year").
_NUMBER_WORDS: dict[int, tuple[str, ...]] = {
    2: ("dos", "dois", "duas", "tva", "two"),
    3: ("tres", "tre", "three"),
    4: ("cuatro", "quatro", "fyra", "four"),
    5: ("cinco", "fem", "five"),
    6: ("seis", "sex", "six"),
    7: ("siete", "sete", "sju", "seven"),
    8: ("ocho", "oito", "atta", "eight"),
    9: ("nueve", "nove", "nio", "nine"),
    10: ("diez", "dez", "tio", "ten"),
    11: ("onze", "elva", "eleven"),
    12: ("doce", "doze", "tolv", "twelve"),
}


def _decimal(value: object) -> Decimal | None:
    """A finite Decimal from a number or numeric text ("30", "30 %", "21.900"); None otherwise."""
    if isinstance(value, bool):
        return None
    if isinstance(value, Decimal | int):
        d: Decimal | None = Decimal(value)
    else:
        text = str(value).strip().rstrip("%").strip()
        try:
            d = Decimal(text)
        except InvalidOperation:
            d = parse_number(text)
    return d if d is not None and d.is_finite() else None


def _blank_dates(text: str) -> str:
    """`text` with every dated mention (find_dates) replaced by spaces; offsets are kept."""
    for d in find_dates(text):
        text = text[: d.start] + " " * (d.end - d.start) + text[d.end :]
    return text


def _has_number(text: str, number: Decimal) -> bool:
    """`number` is a number token or a number word of `text`, outside its dates (see the module
    docstring)."""
    return number in _numbers_in(_blank_dates(text))


# ---- money ----------------------------------------------------------------------------------

# Labels that mark the price after them as the previous one or the ordinary one (module
# docstring, "Decision").
_PREVIOUS_BEFORE = re.compile(
    r"(?<!\w)(?:precio\s+(?:anterior|original)(?:\s+de)?|pre[çc]o\s+(?:anterior|original)"
    r"(?:\s+de)?|antes|original\s+price|was|previously|originally|tidigare(?:\s+pris)?|förut"
    r"|vorher|ehemals)\s*[:\-–]?\s*$",
    re.IGNORECASE,
)
_ORDINARY_BEFORE = re.compile(
    r"(?<!\w)(?:precio\s+(?:regular|normal|habitual|de\s+lista)(?:\s+de)?"
    r"|pre[çc]o\s+(?:normal|regular|cheio|de\s+tabela)(?:\s+de)?|ordinarie(?:\s+pris)?"
    r"|ord\.?\s*pris|normalpris|normalt\s+pris|ordinary\s+price|regular(?:ly)?\s+pric(?:e|ed)"
    r"(?:\s+(?:of|at))?|usual(?:ly)?\s+price|full\s+price)\s*[:\-–]?\s*$",
    re.IGNORECASE,
)
# "De R$ 29,90 por R$ 19,90", "De $ 24.900 a $ 19.900" (pt / es): the first price is the old one.
_DE_BEFORE = re.compile(r"(?<![^\W\d_])de\s*:?\s*$", re.IGNORECASE)
_DE_BETWEEN = re.compile(r"^\s*(?:/\s*[^\s\d/]{1,8}\.?)?\s*(?:por|a|para|pra)\s*$", re.IGNORECASE)

# Introductory durations classify_prices does not read (module docstring, "Roles").
_V_N = (
    r"(?:\d{1,2}|un|una|uno|um|uma|en|ett|one|dos|dois|duas|två|two|tres|três|tre|three"
    r"|cuatro|quatro|fyra|four|cinco|fem|five|seis|sex|six|doce|doze|tolv|twelve)"
)
_V_UNIT = (
    r"(?:months?|mes(?:es)?|mês|mån(?:ad(?:er(?:na)?|en)?)?\.?|weeks?|semanas?|veckor(?:na)?"
    r"|veckan?|days?|d[ií]as?|dagar(?:na)?)(?![^\W\d_])"
)
_FIRST = r"(?:primeros?|primeras?|primeiros?|primeiras?|first|första)"
_COUNT = rf"(?:{_FIRST}\s+)?{_V_N}(?:\s+{_FIRST})?\s+{_V_UNIT}"
_FIRST_COUNT = rf"(?:{_FIRST}\s+{_V_N}|{_V_N}\s+{_FIRST})\s+{_V_UNIT}"
_PREP = r"(?:durante|por|para|for|in|i|under|nos|nas|x|during)"
_ARTICLE = r"(?:los|las|the|de|os|as)"
# Right after the price: "/mes los primeros 2 meses", " (2 meses)", "/mån i 6 mån".
_INTRO_TAIL = re.compile(
    rf"^\s*(?:/\s*[^\s\d/]{{1,8}}\.?)?\s*(?:\(\s*(?:{_ARTICLE}\s+)?{_COUNT}\s*\)"
    rf"|,?\s*(?:{_PREP}\s+)?{_ARTICLE}\s+{_FIRST_COUNT}|,?\s*{_PREP}\s+(?:{_ARTICLE}\s+)?{_COUNT})",
    re.IGNORECASE,
)
# Right before the price: "2 meses a ", "los primeros 2 meses: ", "6 mån för ".
_INTRO_LEAD = re.compile(
    rf"(?<![^\W\d_])(?:{_COUNT}\s+(?:a|por|para|for|för|at|à)(?:\s+(?:solo|sólo|só|apenas|only|bara))?"
    rf"|{_FIRST_COUNT})\s*[:\-–]?\s*$",
    re.IGNORECASE,
)
# Offer words in the price's own line and clause (an offer without a stated duration).
_OFFER_WORD = re.compile(
    r"(?<![\w-])(?:free|gratis|grátis|gratuit\w*|trial|prueba|teste|provperiod\w*|prova"
    r"|first|primer|primeir[oa]|första|intro\w*|special|especial|oferta\w*|erbjudande\w*"
    r"|offer\w*|promo\w*|kampanj\w*)(?![\w-])",
    re.IGNORECASE,
)
# ... except where the word is about something else ("envíos gratis", "free shipping").
_NOT_OFFER_BEFORE = re.compile(
    r"(?<![^\W\d_])(?:env[ií]os?|frete|entrega|despacho|shipping|delivery|frakt|leverans|ads?)"
    r"[\s-]*$",
    re.IGNORECASE,
)
_NOT_OFFER_AFTER = re.compile(r"^[\s-]*(?:shipping|delivery|frakt|leverans)", re.IGNORECASE)
_SENTENCE = re.compile(r"[.!?;•|]\s")
# A sentence end after a price: "." before a capital ("kr/mån. Prova", "kr/mån.Rabatten", but not
# "/mo. for 3 months"), "!", "?", ";", "•", "|".
_SENTENCE_END = re.compile(r"\.(?=\s*[A-ZÀ-ÖØ-Þ])|[!?;•|]")
# The start of a sentence that speaks of the price just stated ("Rabatten gäller i 6 månader",
# "El descuento aplica por 2 meses", "Durante 3 meses."): a duration in it is that price's.
_BACK_REFERENCE = re.compile(
    rf"^\s*(?:(?:rabatt\w*|erbjudandet|kampanj\w*|priset|(?:el|este|o|the|this)\s+(?:descuento"
    rf"|desconto|precio|pre[çc]o|discount|offer|price)|(?:la|esta|a|esa)\s+(?:oferta|promoci[óo]n"
    rf"|promo[çc][ãa]o)|descuento|desconto|discount|v[áa]lid[oa]?|g[äa]ller|aplica|applies)"
    rf"(?![^\W\d_])|{_PREP}\s+(?:{_ARTICLE}\s+)?{_COUNT})",
    re.IGNORECASE,
)

# An ISO code written right after (or before) an ambiguous "$" amount: "$ 399 MXN", "MXN $399";
# or the nationality after "pesos" ("19900 pesos mexicanos", "$ 399 pesos mexicanos").
_CODE_AFTER = re.compile(r"^\s*(?:/\s*[^\W\d_]{1,8}\.?\s*)?\(?([A-Z]{3})(?![A-Za-z])")
_CODE_BEFORE = re.compile(r"(?<![A-Za-z])([A-Z]{3})\s*$")
_PESOS_OF = re.compile(
    r"^\s*(?:pesos?\s+)?(mexicanos|colombianos|argentinos|chilenos)\b", re.IGNORECASE
)
_PESOS_CODES = {"mexicanos": "MXN", "colombianos": "COP", "argentinos": "ARS", "chilenos": "CLP"}
_BARE_DOLLAR = re.compile(r"(?<![A-Za-z])\$")
_CURRENCY_WORD = re.compile(r"(?:pesos?|kronor|krona|reais|soles)$", re.IGNORECASE)

# A name inherited by another tier ("todos los beneficios de Meli+ Esencial", folded text).
_INHERITED = re.compile(
    r"(?:beneficios|ventajas|vantagens|formaner|fordelar|benefits|perks|everything|todo\s+lo"
    r"|tudo|allt)\s+(?:(?:de|del|do|da|dos|das|of|in|i|fran|med)\s+)?"
    r"(?:(?:los|las|el|la|o|a|the)\s+)?(?:(?:planes?|plano|abonnemanget)\s+)?$"
)


Role = Literal["regular", "intro", "compare", "savings", "offer"]
_ROLE_NAMES: dict[str, str] = {
    "regular": "the regular price",
    "intro": "an introductory price",
    "compare": "a comparison (struck-through) price",
    "savings": "an amount saved",
    "offer": "an offer price the quote ties to no duration of its own",
}


@dataclass(frozen=True)
class _Price:
    info: PriceInfo
    role: Role
    currency: str | None
    # A previous / ordinary price made regular: the index of the intro price next to it.
    partner: int | None = None


def _money_value(
    value: object, currency: str, period: str | None
) -> tuple[Decimal, str, str | None] | None:
    if isinstance(value, str) and (parsed := parse_money_code(value)) is not None:
        amount, cur, per = parsed
        return amount, cur, per or period
    amount = _decimal(value)
    if amount is None:
        return None
    return amount, currency, period


def _context_text(quote: str, context_lines: Sequence[str]) -> tuple[str, tuple[int, int]]:
    """The text to classify and the span of the quote inside it. The context lines are joined
    one per line; a quote that cannot be found in them is checked on its own."""
    quote = unicodedata.normalize("NFC", quote)
    if context_lines:
        text = unicodedata.normalize("NFC", "\n".join(context_lines))
        folded, index = _fold_map(text)
        needle = _fold(quote)
        at = folded.find(needle) if needle else -1
        if at >= 0:
            return text, (index[at], index[at + len(needle) - 1] + 1)
    return quote, (0, len(quote))


def _explicit_currency(text: str, info: PriceInfo) -> str | None:
    """The currency written next to an amount whose marker is ambiguous (a bare "$" or a currency
    word): an ISO code ("$ 399 MXN", "MXN $399") or "pesos mexicanos"."""
    raw, start, end = info.money.raw, info.money.start, info.money.end
    if not (_BARE_DOLLAR.search(raw) or _CURRENCY_WORD.search(raw)):
        return None
    after = text[end : end + 24]
    if m := _PESOS_OF.match(after):
        return _PESOS_CODES[m.group(1).lower()]
    for m in (_CODE_AFTER.match(after), _CODE_BEFORE.search(text[max(0, start - 8) : start])):
        if m and m.group(1) in ISO_CODES:
            return m.group(1)
    return None


def _offer_word(before: str) -> bool:
    """An offer word in the last clause of the price's own line, not about shipping or ads."""
    clause = _SENTENCE.split(before.rsplit("\n", 1)[-1])[-1]
    return any(
        not _NOT_OFFER_BEFORE.search(clause[: m.start()])
        and not _NOT_OFFER_AFTER.match(clause[m.end() :])
        for m in _OFFER_WORD.finditer(clause)
    )


def _base_role(text: str, start: int, before: str, after: str) -> Role:
    """The role of the price at `start`, with `before` the text since the previous price and
    `after` the text up to the next one (module docstring, "Roles")."""
    if is_savings(before, after):
        return "savings"
    if is_comparison(before, after):
        return "compare"
    then = follows_then(before)

    def timed(tail: str) -> bool:
        """A duration right after the price, in `tail`."""
        return is_intro(before, tail) or (not then and bool(_INTRO_TAIL.match(tail)))

    line_after = after.split("\n", 1)[0]
    cut = _SENTENCE_END.search(line_after)
    sentence_after = line_after[: cut.start()] if cut else line_after
    if (
        timed(sentence_after)
        or is_intro(text[:start], "")
        or (not then and _INTRO_LEAD.search(before[-80:]))
    ):
        return "intro"
    if cut and timed(line_after) and _BACK_REFERENCE.match(line_after[cut.end() :]):
        return "intro"  # "199 kr/mån.Rabatten gäller i 6 månader"
    if timed(after) or (not then and _offer_word(before)):
        # A duration in the next sentence or on the next line may be another offer's ("295
        # kr/mån. Prova gratis i 1 månad"), an offer word has none: neither regular nor intro.
        return "offer"
    return "regular"


def _label(text: str, prices: Sequence[_Price], k: int, before: str) -> str | None:
    """Whether a label marks the k-th price as the "previous" or the "ordinary" one (module
    docstring, "Decision"); None when none does."""
    if _PREVIOUS_BEFORE.search(before[-60:]):
        return "previous"
    if _ORDINARY_BEFORE.search(before[-60:]):
        return "ordinary"
    if _DE_BEFORE.search(before[-20:]) and k + 1 < len(prices):
        p, nxt = prices[k].info.money, prices[k + 1].info.money
        if _DE_BETWEEN.match(text[p.end : nxt.start]) and nxt.amount < p.amount:
            return "previous"
    return None


def _same_period(a: str | None, b: str | None) -> bool:
    return a is None or b is None or a == b


def _prices(text: str, currency: str) -> list[_Price]:
    """Every price of `text` with its role (module docstring, "Roles" and "Decision") and its
    currency (an ISO code written next to a "$" amount wins)."""
    infos = classify_prices(text, [currency], currency_words=True)
    base: list[_Price] = []
    befores: list[str] = []
    afters: list[str] = []
    prev_end = 0
    for k, info in enumerate(infos):
        start, end = info.money.start, info.money.end
        nxt = infos[k + 1].money.start if k + 1 < len(infos) else len(text)
        before, after = text[prev_end:start], text[end:nxt]
        cur = _explicit_currency(text, info) or info.money.currency
        base.append(_Price(info, _base_role(text, start, before, after), cur))
        befores.append(before)
        afters.append(after)
        prev_end = end
    labels = [_label(text, base, k, befores[k]) for k in range(len(base))]
    lines = [text.count("\n", 0, p.info.money.start) for p in base]
    out: list[_Price] = []
    for k, p in enumerate(base):
        if labels[k] is None or p.role not in ("regular", "compare"):
            out.append(p)
            continue
        amount = p.info.money.amount
        others = [j for j, q in enumerate(base) if j != k and q.currency == p.currency]
        line = [j for j in others if lines[j] == lines[k]]
        intro_next_to_it = next(
            (
                j
                for j in (k - 1, k + 1)
                if j in line and base[j].role == "intro" and base[j].info.money.amount < amount
            ),
            None,
        )
        competing = any(base[j].role == "regular" and labels[j] is None for j in line)
        lower = any(
            base[j].info.money.amount < amount and _same_period(base[j].info.period, p.info.period)
            for j in others
        )
        if intro_next_to_it is not None and not competing:
            # The price that follows a timed offer.
            out.append(_Price(p.info, "regular", p.currency, intro_next_to_it))
        elif labels[k] == "ordinary" and not lower and not is_comparison("", afters[k]):
            out.append(_Price(p.info, "regular", p.currency))  # nothing cheaper around
        else:
            out.append(_Price(p.info, "compare", p.currency))
    return out


def _describe(prices: Sequence[_Price]) -> str:
    return ", ".join(
        f"{p.info.money.amount} {p.currency or '?'}"
        f"{'/' + p.info.period if p.info.period else ''} {p.role}"
        for p in prices
    )


def _tier_names(b: Bundle, kind: ValueKind) -> list[str]:
    if kind == "base_money" and b.price.base_plan_name:
        return [b.price.base_plan_name]
    return list(dict.fromkeys([*b.aliases, b.name]))


def _tier_error(
    text: str,
    price: _Price,
    kind: ValueKind,
    bundle: Bundle,
    siblings: Sequence[Bundle],
) -> str | None:
    """None when the tier named for the amount is the bundle's own (see the module docstring)."""
    own = {_fold(n) for n in _tier_names(bundle, kind)}
    owner: dict[str, tuple[str, str]] = {}  # folded name -> (bundle_id, name as written)
    for s in siblings:
        for n in _tier_names(s, kind):
            if _fold(n) not in own:
                owner.setdefault(_fold(n), (s.bundle_id, n))
    if not owner:
        return None  # the siblings share the name (one base plan under several bundles)
    for n in _tier_names(bundle, kind):
        owner.setdefault(_fold(n), (bundle.bundle_id, n))
    folded, index = _fold_map(text)
    starts = _clause_starts(text)
    named: dict[int, dict[str, str]] = {}  # clause -> {bundle_id: name}
    for s, _e, name in _mentions(folded, owner):
        if _INHERITED.search(folded[max(0, s - 50) : s]):
            continue
        tier, printed = owner[name]
        named.setdefault(_clause_of(starts, index[s]), {}).setdefault(tier, printed)
    if not named:
        return f"the quote names no tier (expected one of {_tier_names(bundle, kind)})"
    k = _clause_of(starts, price.info.money.start)
    tiers = next((named[j] for j in range(k, -1, -1) if j in named), None)
    if tiers is None:
        return "the quote names no tier in or before the amount's clause"
    if len(tiers) > 1:
        names = ", ".join(sorted(tiers.values()))
        return f"the clause that names the amount's tier names several ({names})"
    ((tier, printed),) = tiers.items()
    if tier == bundle.bundle_id:
        return None
    return f"the tier named next to the amount is {printed!r} ({tier})"


def _plausibility(
    amount: Decimal, currency: str, period: str | None, kind: ValueKind, fx: FxTable | None
) -> tuple[ValueCheck | None, str]:
    if fx is None:
        return None, "plausibility not checked (no FX table)"
    monthly = amount / _MONTHS_PER.get(period or "month", 1)
    eur = fx.to_eur(monthly, currency)
    if eur is None:
        return None, f"plausibility not checked (no FX rate for {currency})"
    lo, hi = PLAUSIBLE_MONTHLY_EUR
    if kind == "intro_money":
        lo = Decimal(0)  # an introductory offer may be free
    if not lo <= eur <= hi:
        return (
            ValueCheck(
                "implausible",
                f"{format_money_code(amount, currency, period)} is {eur} EUR/month "
                f"(plausible: {lo}-{hi})",
            ),
            "",
        )
    return None, f"{eur} EUR/month"


def _big_change(
    amount: Decimal, period: str | None, current: object, currency: str, current_period: str | None
) -> tuple[bool, str]:
    """Whether the monthly equivalent changes by more than BIG_CHANGE against `current`, and a
    note when `current` cannot be compared."""
    if current is None:
        return False, ""
    parsed = _money_value(current, currency, current_period)
    if parsed is None:
        return False, f"; change not measured: current value {current!r} is not an amount"
    old, old_currency, old_period = parsed
    if old_currency != currency:
        return False, f"; change not measured: current value {current!r} is not in {currency}"
    new_m = amount / _MONTHS_PER.get(period or "month", 1)
    old_m = old / _MONTHS_PER.get(old_period or "month", 1)
    if old_m == 0:
        return new_m != 0, ""
    return abs(new_m - old_m) / abs(old_m) > BIG_CHANGE, ""


def _check_money(
    kind: ValueKind,
    value: object,
    quote: str,
    *,
    context_lines: Sequence[str],
    country: str,
    currency: str,
    period: str | None,
    provider: Provider | None,
    bundle: Bundle | None,
    bundles: Iterable[Bundle] | None,
    fx: FxTable | None,
    current: object,
    url: str | None,
) -> ValueCheck:
    parsed = _money_value(value, currency, period)
    if parsed is None:
        return _missing(f"value {value!r} is not an amount like '21900 COP/month'")
    amount, cur, per = parsed
    if cur != currency:
        return _missing(f"value currency {cur} is not the bundle's {currency}")
    if url is not None and provider is not None:
        from haggler.catalog import on_domains

        if not on_domains(url, provider.domains.get(country, [])):
            return _missing(f"page {url} is not on a {country} domain of provider {provider.id}")
    text, (qs, qe) = _context_text(quote, context_lines)
    prices = _prices(text, currency)
    in_quote = [p for p in prices if qs <= p.info.money.start and p.info.money.end <= qe]
    if not in_quote:
        return _missing(f"the quote states no price in {currency}")
    same = [p for p in in_quote if p.info.money.amount == amount]
    if not same:
        return _missing(
            f"the quote does not state {format_money_code(amount, currency)} "
            f"(it states: {_describe(in_quote)})"
        )
    want = "intro" if kind == "intro_money" else "regular"
    siblings: list[Bundle] = []
    if bundle is not None:
        if bundles is None:
            from haggler.catalog import default_bundles

            bundles = default_bundles().bundles
        siblings = [
            b
            for b in bundles
            if b.provider_id == bundle.provider_id
            and b.country == country
            and b.bundle_id != bundle.bundle_id
        ]
    why: list[str] = []
    for p in same:
        if p.currency != currency:
            why.append(f"{p.info.money.raw!r} is {p.currency or 'an unknown currency'}")
        elif p.role != want:
            why.append(f"{p.info.money.raw!r} is {_ROLE_NAMES[p.role]}, not {_ROLE_NAMES[want]}")
        elif per is not None and p.info.period is not None and p.info.period != per:
            why.append(f"{p.info.money.raw!r} is per {p.info.period}, not per {per}")
        elif bundle is not None and (err := _tier_error(text, p, kind, bundle, siblings)):
            why.append(err)
        elif (
            bundle is not None
            and p.partner is not None
            and (err := _tier_error(text, prices[p.partner], kind, bundle, siblings))
        ):
            # A previous price is the regular one only after its own tier's offer.
            why.append(f"{p.info.money.raw!r} follows another tier's offer: {err}")
        else:
            implausible, note = _plausibility(amount, currency, per, kind, fx)
            if implausible is not None:
                return implausible
            big, change_note = _big_change(amount, per, current, currency, period)
            return _ok(f"{p.info.money.raw!r} {p.role}; {note}{change_note}", big_change=big)
    return _missing("; ".join(why))


# ---- components -----------------------------------------------------------------------------


def _plan_names(svc: ServiceDef) -> dict[str, list[str]]:
    return {p.id: [*p.aliases, p.label_es, p.label_en] for p in svc.plans}


def _named_plan(svc: ServiceDef, folded_segment: str) -> str | None:
    best: tuple[int, str] | None = None
    for plan_id, names in _plan_names(svc).items():
        for s, e, _name in _mentions(folded_segment, names):
            if best is None or e - s > best[0]:
                best = (e - s, plan_id)
    return best[1] if best else None


def _numbers_in(text: str) -> set[Decimal]:
    """The numbers of `text` (see _has_number): tokens without a leading zero, number words."""
    found = {
        n
        for m in _NUMBER_TOKEN.finditer(text)
        if not (len(m.group()) > 1 and m.group()[0] == "0" and m.group()[1].isdigit())
        and (n := parse_number(m.group())) is not None
    }
    folded = _fold(text)
    found.update(Decimal(n) for n, words in _NUMBER_WORDS.items() if _mentions(folded, words))
    return found


def _number_for(quote: str, number: Decimal, own: list[int], others: list[int]) -> bool:
    """`number` belongs to a mention of the service (raw offsets `own`; `others` are the other
    services' mentions): it is a number of the mention's clause, else of the nearest earlier
    clause with a number, else of the next clause with a number when that one names no other
    service."""
    starts = _clause_starts(quote)
    text = _blank_dates(quote)
    numbers = [
        _numbers_in(text[s:e]) for s, e in zip(starts, [*starts[1:], len(text)], strict=True)
    ]
    other_clauses = {_clause_of(starts, o) for o in others}
    for pos in own:
        k = _clause_of(starts, pos)
        j = next((j for j in range(k, -1, -1) if numbers[j]), None)
        if j is None:
            j = next((j for j in range(k + 1, len(numbers)) if numbers[j]), None)
            if j is None or j in other_clauses:
                continue
        if number in numbers[j]:
            return True
    return False


def _check_component(value: object, quote: str, catalog: Catalog) -> ValueCheck:
    text = str(value).strip()
    service_id, _, number = text.partition(":")
    service_id, _, plan_id = service_id.partition(".")
    try:
        svc = catalog.service(service_id)
    except KeyError:
        return _missing(f"unknown service {service_id!r}")
    if plan_id and svc.plan(plan_id) is None:
        return _missing(f"unknown plan {service_id}.{plan_id}")
    folded, index = _fold_map(quote)
    own = [svc.name, *svc.aliases]
    spans = _mentions(folded, own)
    if not spans:
        names = " / ".join(dict.fromkeys(own))
        return _missing(f"the quote does not name {names}")
    others = [n for s in catalog.services if s.id != svc.id for n in (s.name, *s.aliases)]
    other_spans = _mentions(folded, others)
    if number:
        n = _decimal(number)
        if n is None:
            return _missing(f"value {text!r}: {number!r} is not a number")
        if not _number_for(
            quote, n, [index[s] for s, _e, _ in spans], [index[s] for s, _e, _ in other_spans]
        ):
            return _missing(f"the quote does not give {svc.name} the number {number}")
    if not plan_id:
        return _ok(f"names {svc.name}")
    named: set[str | None] = set()
    for _s, e, _name in spans:
        nxt = min((os for os, _oe, _n in other_spans if os >= e), default=len(folded))
        named.add(_named_plan(svc, folded[e:nxt]))
    if plan_id in named:
        return _ok(f"names {svc.name} {plan_id}")
    if named == {None}:
        return _ok(f"names {svc.name}; the plan is not named")
    found = sorted(p for p in named if p)
    return _missing(f"the quote names {svc.name} {'/'.join(found)}, not {plan_id}")


# ---- entry point ----------------------------------------------------------------------------


def check_value(
    kind: ValueKind,
    value: object,
    quote: str,
    *,
    context_lines: Sequence[str] = (),
    country: str,
    currency: str,
    period: str | None = None,
    provider: Provider | None = None,
    bundle: Bundle | None = None,
    bundles: Iterable[Bundle] | None = None,
    catalog: Catalog | None = None,
    fx: FxTable | None = None,
    current: object = None,
    url: str | None = None,
) -> ValueCheck:
    """Whether `quote` states `value` (see the module docstring for each kind).

    `bundles` are the curated bundles the tier rule looks for the bundle's siblings in (same
    provider and country); None loads data/curated/bundles.yaml (haggler.catalog.
    default_bundles), the way `catalog` defaults to the service catalog. `url` is the page the
    quote comes from (money kinds; the url kind checks `value` itself); `current` is the YAML
    value a researched one would replace.
    """
    if kind in MONEY_KINDS:
        return _check_money(
            kind,
            value,
            quote,
            context_lines=context_lines,
            country=country,
            currency=currency,
            period=period,
            provider=provider,
            bundle=bundle,
            bundles=bundles,
            fx=fx,
            current=current,
            url=url,
        )
    if kind in ("int", "pct"):
        number = _decimal(value)
        if number is None:
            return _missing(f"value {value!r} is not a number")
        if _has_number(quote, number):
            return _ok(f"states {value}")
        return _missing(f"the quote does not state the number {value}")
    if kind == "date":
        try:
            wanted = value if isinstance(value, date) else date.fromisoformat(str(value).strip())
        except ValueError:
            return _missing(f"value {value!r} is not an ISO date")
        found = [d.value for d in find_dates(quote)]
        if wanted in found:
            return _ok(f"states {wanted.isoformat()}")
        listed = ", ".join(d.isoformat() for d in found) or "no date with a year"
        return _missing(f"the quote does not state {wanted.isoformat()} (it states: {listed})")
    if kind == "component":
        if catalog is None:
            from haggler.catalog import default_catalog

            catalog = default_catalog()
        return _check_component(value, quote, catalog)
    if kind == "url":
        if provider is None:
            return _missing("no provider to check the url's domain against")
        from haggler.catalog import on_domains

        if on_domains(str(value), provider.domains.get(country, [])):
            return _ok(f"on a {country} domain of provider {provider.id}")
        return _missing(f"{value} is not https on a {country} domain of provider {provider.id}")
    raise ValueError(f"unknown value kind {kind!r}")

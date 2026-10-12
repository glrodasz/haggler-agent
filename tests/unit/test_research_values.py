"""haggler.research.values.check_value: a quote states THE value, not just some value.

The quotes are real lines from the bundles probe (H0, 2026-10-09) where possible; the bundles
are small synthetic rows so the cases do not change when bundles.yaml is edited.
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal
from typing import Any

import pytest

from haggler.catalog import load_catalog
from haggler.models.curated import Bundle, BundlesFile
from haggler.models.fx import FxRate, FxTable
from haggler.money import format_money_code, parse_money_code
from haggler.research.evidence import Page
from haggler.research.values import ValueCheck, check_value

MELI_LINE = (
    "Precio actual con 9 por ciento de descuento: 19900 pesos por mes, por 2 meses. "
    "Precio anterior: $ 21.900 pesos por mes."
)
# Page.locate context: 2 lines before the quote, 1 after.
MELI_CONTEXT = ["Meli+ Total", "$ 19.900/mes por 2 meses", MELI_LINE, "Suscribirme"]
APPLE_SE = (
    "Det personliga abonnemanget kostar 215 kr i månaden och familjeabonnemanget 295 kr i månaden."
)
APPLE_CO = (
    "El plan Individual cuesta $45.900 al mes, el plan Familiar $62.900 al mes y el plan "
    "Premium $86.900 al mes."
)
# The real visible line: markup glues the next sentence to "månader".
TELIA_PLAY = (
    "Ordinarie pris: 269 kr/mån.Pris: 199 kr/mån.Rabatten gäller i 6 månaderIngen bindningstid"
)
TELIA_PLAY_CONTEXT = [
    "Streaming Mer",
    "Spara 58 kr/mån jämfört med att köpa tjänsterna separat",
    TELIA_PLAY,
    "Köp",
]
MELI_COMPONENTS = "30% OFF en HBO Max, Universal+, Paramount+ y ViX Premium."
TELIA_COMPONENTS = "Netflix Standard, HBO Max Basic med reklam, Disney+ Standard med reklam"
# The Meli+ Esencial description on /suscripciones/melimas: it is Meli+ Total that has Disney+.
MELI_ESENCIAL_PAGE = (
    "Meli+ Total: incluye Disney+ Estándar con anuncios y beneficios en otras plataformas de "
    "entretenimiento, además de todos los beneficios de Meli+ Esencial."
)
MELI_MEGA_COMPONENTS = "Entretenimento incluido: Disney+, Netflix, HBO Max, Apple TV"


PROVIDERS = [
    {
        "id": "meli",
        "name": "Mercado Libre",
        "domains": {
            "CO": ["mercadolibre.com.co"],
            "MX": ["mercadolibre.com.mx"],
            "BR": ["mercadolivre.com.br"],
        },
    },
    {"id": "telia", "name": "Telia", "domains": {"SE": ["telia.se"]}},
    {"id": "google", "name": "Google", "domains": {"CO": ["gemini.google/co/"]}},
    {
        "id": "apple",
        "name": "Apple",
        "domains": {"SE": ["www.apple.com/se/"], "CO": ["www.apple.com/co/"]},
    },
]
# (bundle_id, provider, country, name, aliases, amount, currency): two Meli+ tiers in CO (one in
# MX, one in BR), Apple One Individual / Familj in SE, three Apple One tiers in CO, a Telia
# bundle sold on top of a base plan and three Google AI tiers in CO.
TIERS = [
    ("meli_co_total", "meli", "CO", "Meli+ Total", ["Meli+ Total"], "21900", "COP"),
    ("meli_co_esencial", "meli", "CO", "Meli+ Esencial", ["Meli+ Esencial"], "9900", "COP"),
    ("meli_mx_mega", "meli", "MX", "Meli+ Mega", ["Meli+ Mega"], "399", "MXN"),
    ("meli_br_mega", "meli", "BR", "Meli+ Mega", ["Meli+ Mega", "Plano Mega"], "74.90", "BRL"),
    (
        "telia_se_plus",
        "telia",
        "SE",
        "Telia Obegränsad Plus + Netflix, HBO Max y Disney+",
        [],
        "699",
        "SEK",
    ),
    ("google_co_plus", "google", "CO", "Google AI Plus", ["AI Plus"], "19900", "COP"),
    ("google_co_pro", "google", "CO", "Google AI Pro", ["AI Pro"], "79000", "COP"),
    ("google_co_ultra", "google", "CO", "Google AI Ultra", ["AI Ultra"], "449000", "COP"),
    (
        "apple_se_individual",
        "apple",
        "SE",
        "Apple One Individual",
        ["Personligt", "personliga abonnemanget"],
        "215",
        "SEK",
    ),
    (
        "apple_se_family",
        "apple",
        "SE",
        "Apple One Familj",
        ["Familj", "familjeabonnemanget"],
        "295",
        "SEK",
    ),
    ("apple_co_individual", "apple", "CO", "Apple One Individual", ["Individual"], "45900", "COP"),
    ("apple_co_family", "apple", "CO", "Apple One Familiar", ["Familiar"], "62900", "COP"),
    ("apple_co_premium", "apple", "CO", "Apple One Premium", ["Premium"], "86900", "COP"),
]
# Price fields besides amount and currency.
PRICE_EXTRA: dict[str, dict[str, str]] = {
    "telia_se_plus": {
        "base_plan_name": "Telia Obegränsad Plus",
        "base_plan_amount": "569",
        "marginal_amount": "130",
    },
}
OFFICIAL_URL = {
    ("meli", "CO"): "https://www.mercadolibre.com.co/suscripciones/planes",
    ("meli", "MX"): "https://www.mercadolibre.com.mx/suscripciones/planes",
    ("meli", "BR"): "https://www.mercadolivre.com.br/assinaturas/melimais",
    ("telia", "SE"): "https://www.telia.se/mobilabonnemang/mobil-obegraensad-plus",
    ("google", "CO"): "https://gemini.google/co/subscriptions/",
    ("apple", "SE"): "https://www.apple.com/se/apple-one/",
    ("apple", "CO"): "https://www.apple.com/co/apple-one/",
}
BUNDLES = BundlesFile.model_validate(
    {
        "providers": PROVIDERS,
        "bundles": [
            {
                "bundle_id": bundle_id,
                "provider_id": provider,
                "provider_type": "platform",
                "country": country,
                "name": name,
                "tier": bundle_id.rsplit("_", 1)[-1],
                "aliases": aliases,
                "official_url": OFFICIAL_URL[provider, country],
                "price": {
                    "amount": amount,
                    "currency": currency,
                    "tax_included": True,
                    **PRICE_EXTRA.get(bundle_id, {}),
                },
                "components": [],
                "last_verified": "2026-10-09",
            }
            for bundle_id, provider, country, name, aliases, amount, currency in TIERS
        ],
    }
)
ROWS: dict[str, Bundle] = {b.bundle_id: b for b in BUNDLES.bundles}
FX = FxTable(
    as_of=date(2026, 10, 9),
    rates={
        cur: FxRate(currency=cur, per_eur=Decimal(per), source="ecb", as_of=date(2026, 10, 9))
        for cur, per in {"COP": "4500", "MXN": "21", "SEK": "11"}.items()
    },
)


@pytest.fixture(scope="module")
def cat():
    return load_catalog()


def _check(
    kind: str, value: Any, quote: str, bundle_id: str | None = None, **kw: Any
) -> ValueCheck:
    b = ROWS[bundle_id] if bundle_id else None
    defaults: dict[str, Any] = {
        "country": b.country if b else "SE",
        "currency": b.price.currency if b else "SEK",
        "period": "month",
        "provider": BUNDLES.provider(b.provider_id) if b else None,
        "bundle": b,
        "bundles": BUNDLES.bundles,
    }
    return check_value(kind, value, quote, **{**defaults, **kw})  # type: ignore[arg-type]


def _ok(result: ValueCheck) -> bool:
    return result.status == "ok"


# ----------------------------------------------------------- intro vs regular price


def test_meli_total_previous_price_next_to_the_intro_is_the_regular_price() -> None:
    """A "precio anterior" next to a discounted price that states its duration ("por 2 meses")
    is what the subscriber pays after the offer: the regular price, never a comparison one."""
    regular = _check(
        "money", "21900 COP/month", MELI_LINE, "meli_co_total", context_lines=MELI_CONTEXT
    )
    assert _ok(regular), regular
    intro = _check(
        "intro_money", "19900 COP/month", MELI_LINE, "meli_co_total", context_lines=MELI_CONTEXT
    )
    assert _ok(intro), intro
    # Each amount only confirms its own role.
    wrong = _check(
        "money", "19900 COP/month", MELI_LINE, "meli_co_total", context_lines=MELI_CONTEXT
    )
    assert wrong.status == "value_not_in_quote" and "introductory" in wrong.reason
    wrong = _check(
        "intro_money", "21900 COP/month", MELI_LINE, "meli_co_total", context_lines=MELI_CONTEXT
    )
    assert wrong.status == "value_not_in_quote" and "regular" in wrong.reason


def test_previous_price_without_a_stated_duration_is_a_comparison_price() -> None:
    quote = "Meli+ Total. Precio actual: $ 19.900/mes. Precio anterior: $ 21.900/mes."
    result = _check("money", "21900 COP/month", quote, "meli_co_total")
    assert result.status == "value_not_in_quote" and "comparison" in result.reason
    assert _ok(_check("money", "19900 COP/month", quote, "meli_co_total"))


def test_antes_before_an_intro_offer_is_the_regular_price() -> None:
    quote = "Meli+ Total: antes $ 21.900/mes, ahora $ 19.900/mes por 2 meses"
    assert _ok(_check("money", "21900 COP/month", quote, "meli_co_total"))
    assert _ok(_check("intro_money", "19900 COP/month", quote, "meli_co_total"))


def test_telia_play_ordinarie_pris_is_regular_when_the_discount_has_a_duration() -> None:
    """The real line: "6 månaderIngen bindningstid" (glued by markup) is still a duration."""
    for context in ([], TELIA_PLAY_CONTEXT):
        assert _ok(_check("money", "269 SEK/month", TELIA_PLAY, context_lines=context))
        assert _ok(_check("intro_money", "199 SEK/month", TELIA_PLAY, context_lines=context))
        wrong = _check("money", "199 SEK/month", TELIA_PLAY, context_lines=context)
        assert wrong.status == "value_not_in_quote" and "introductory" in wrong.reason
        wrong = _check("intro_money", "269 SEK/month", TELIA_PLAY, context_lines=context)
        assert wrong.status == "value_not_in_quote"
    for regular, intro in (("319", "249"), ("499", "399")):
        line = TELIA_PLAY.replace("269", regular).replace("199", intro)
        assert _ok(_check("money", f"{regular} SEK/month", line))
        assert not _ok(_check("money", f"{intro} SEK/month", line))
    # Without "Rabatten gäller i 6 månader" the ordinary price is a struck-through one.
    permanent = _check("money", "269 SEK/month", "Ordinarie pris: 269 kr/mån. Pris: 199 kr/mån.")
    assert permanent.status == "value_not_in_quote" and "comparison" in permanent.reason


def test_savings_and_comparison_amounts_never_confirm_a_price() -> None:
    savings = _check(
        "money", "58 SEK/month", "Spara 58 kr/mån jämfört med att köpa tjänsterna separat"
    )
    assert savings.status == "value_not_in_quote" and "saved" in savings.reason
    compare = _check("money", "339 SEK/month", "Telia Play Mer 269 kr/mån. Inte 339 kr/mån.")
    assert compare.status == "value_not_in_quote" and "comparison" in compare.reason


def test_previous_price_next_to_an_untimed_offer_is_a_comparison_price() -> None:
    """The duration half of the decision: an intro by offer word alone ("precio especial") does
    not turn the previous price into the regular one."""
    quote = "Meli+ Total precio especial: $ 19.900/mes. Precio anterior: $ 21.900/mes."
    result = _check("money", "21900 COP/month", quote, "meli_co_total")
    assert result.status == "value_not_in_quote" and "comparison" in result.reason


@pytest.mark.parametrize(
    ("value", "quote", "context"),
    [
        # The intro belongs to another tier's line: Esencial's own line has a current price.
        (
            "12900 COP/month",
            "Meli+ Esencial: precio anterior $ 12.900/mes, ahora $ 9.900/mes",
            ["Meli+ Total", "$ 19.900/mes por 2 meses"],
        ),
        # The other tier's intro sits in a context line, not next to the labeled price.
        (
            "24900 COP/month",
            "Meli+ Total: antes $ 24.900/mes, ahora $ 21.900/mes",
            ["Meli+ Esencial: $ 4.900/mes por 3 meses"],
        ),
        ("29900 COP/month", "Meli+ Total: antes $ 29.900/mes, ahora $ 21.900/mes.", []),
        (
            "29900 COP/month",
            "Meli+ Total: antes $ 29.900/mes, ahora $ 21.900/mes. Meli+ Esencial: $ 0 por 1 mes",
            [],
        ),
        # A stated "luego" price is the one paid after the offer, not the "antes" one.
        (
            "29900 COP/month",
            "Meli+ Total: antes $ 29.900, ahora $ 19.900/mes por 2 meses, luego $ 24.900/mes",
            [],
        ),
    ],
)
def test_a_previous_price_is_regular_only_next_to_its_own_timed_intro(
    value: str, quote: str, context: list[str]
) -> None:
    bundle_id = "meli_co_esencial" if "Esencial: precio" in quote else "meli_co_total"
    lines = [*context, quote, "Suscribirme"] if context else []
    result = _check("money", value, quote, bundle_id, context_lines=lines)
    assert result.status == "value_not_in_quote" and "comparison" in result.reason, result


def test_a_previous_price_next_to_another_tiers_intro_is_not_regular() -> None:
    quote = "Meli+ Total: antes $ 29.900/mes. Meli+ Esencial: $ 0 por 1 mes"
    result = _check("money", "29900 COP/month", quote, "meli_co_total")
    assert result.status == "value_not_in_quote" and "another tier's offer" in result.reason
    assert "meli_co_esencial" in result.reason


def test_the_luego_price_is_the_regular_one() -> None:
    quote = "Meli+ Total: antes $ 29.900, ahora $ 19.900/mes por 2 meses, luego $ 24.900/mes"
    assert _ok(_check("money", "24900 COP/month", quote, "meli_co_total"))
    assert _ok(_check("intro_money", "19900 COP/month", quote, "meli_co_total"))


def test_an_ordinary_price_with_another_price_around_is_a_comparison_one() -> None:
    quote = "Telia Play Mer 269 kr/mån. Ordinarie pris 339 kr/mån. Prova gratis: 0 kr i 30 dagar"
    result = _check("money", "339 SEK/month", quote)
    assert result.status == "value_not_in_quote" and "comparison" in result.reason
    # ... and the lower price next to it is a discount without a duration: neither.
    result = _check("money", "269 SEK/month", quote)
    assert result.status == "value_not_in_quote" and "offer price" in result.reason


def test_the_doubled_product_page_line_never_confirms_the_discounted_price() -> None:
    """telia.se repeats each price as visible text and a screen-reader label, glued:
    "269 kr/mån" + "Ordinarie pris: 269 kr/mån" + "199 kr/mån" + "Pris: 199 kr/mån"."""
    line = "269 kr/månOrdinarie pris: 269 kr/mån199 kr/månPris: 199 kr/mån"
    result = _check("money", "199 SEK/month", line)
    assert result.status == "value_not_in_quote" and "offer price" in result.reason
    assert not _ok(_check("intro_money", "199 SEK/month", line))


@pytest.mark.parametrize(
    "quote",
    [
        "Telia Play: Ordinarie pris: 269 kr/mån.",
        "Telia Play Normalpris 269 kr/mån",
        "Telia Play full price 269 kr/mån",
    ],
)
def test_an_ordinary_price_alone_is_the_regular_price(quote: str) -> None:
    assert _ok(_check("money", "269 SEK/month", quote))


def test_an_ordinary_price_and_a_cheaper_one_on_the_next_line() -> None:
    lines = ["Telia Play Mer", "Ordinarie pris: 269 kr/mån.", "Pris: 199 kr/mån"]
    result = _check("money", "269 SEK/month", lines[1], context_lines=lines)
    assert result.status == "value_not_in_quote" and "comparison" in result.reason
    # Another tier's dearer price does not make it a struck-through one.
    lines = ["Telia Play Mer", "Ordinarie pris: 269 kr/mån.", "Telia Play Maxad 319 kr/mån"]
    assert _ok(_check("money", "269 SEK/month", lines[1], context_lines=lines))


def test_precio_regular_de_next_to_a_timed_intro_is_the_regular_price() -> None:
    quote = "Meli+ Total: $ 19.900/mes por 2 meses. Precio regular de $ 21.900/mes"
    assert _ok(_check("money", "21900 COP/month", quote, "meli_co_total"))


@pytest.mark.parametrize(
    ("value", "quote", "country", "currency"),
    [
        ("29.90 BRL/month", "Meli+ Mega: De R$ 29,90 por R$ 19,90/mês", "BR", "BRL"),
        ("24900 COP/month", "Meli+ Mega: De $ 24.900 a $ 19.900/mes", "CO", "COP"),
    ],
)
def test_de_x_por_y_marks_the_old_price(
    value: str, quote: str, country: str, currency: str
) -> None:
    result = _check("money", value, quote, country=country, currency=currency)
    assert result.status == "value_not_in_quote" and "comparison" in result.reason
    new = "19.90 BRL/month" if currency == "BRL" else "19900 COP/month"
    assert _ok(_check("money", new, quote, country=country, currency=currency))
    # With a timed intro the old price is the one paid after it.
    timed = "Meli+ Mega: De R$ 29,90 por R$ 19,90/mês nos 2 primeiros meses"
    if currency == "BRL":
        assert _ok(_check("money", value, timed, country=country, currency=currency))
        assert _ok(_check("intro_money", new, timed, country=country, currency=currency))


@pytest.mark.parametrize(
    ("quote", "amount", "currency"),
    [
        ("Meli+ Total: $ 19.900/mes los primeros 2 meses", "19900", "COP"),
        ("Meli+ Total: 2 meses a $ 19.900", "19900", "COP"),
        ("Meli+ Total: $ 19.900/mes (2 meses)", "19900", "COP"),
        ("Meli+ Total: $ 19.900 los 2 primeros meses", "19900", "COP"),
        ("Telia Play Mer 199 kr/mån de första 6 månaderna", "199", "SEK"),
        ("Telia Play Mer: 199 kr/mån i 6 mån", "199", "SEK"),
        ("Meli+ Total R$ 15,90 nos 2 primeiros meses", "15.90", "BRL"),
    ],
)
def test_more_intro_phrasings(quote: str, amount: str, currency: str) -> None:
    country = {"COP": "CO", "SEK": "SE", "BRL": "BR"}[currency]
    bundle_id = "meli_co_total" if currency == "COP" else None
    kw: dict[str, Any] = {} if bundle_id else {"country": country, "currency": currency}
    regular = _check("money", f"{amount} {currency}/month", quote, bundle_id, **kw)
    assert regular.status == "value_not_in_quote" and "introductory" in regular.reason
    assert _ok(_check("intro_money", f"{amount} {currency}/month", quote, bundle_id, **kw))


def test_offer_words_never_confirm_an_intro_price() -> None:
    quote = "Meli+ Total con envíos gratis: $ 21.900/mes"
    intro = _check("intro_money", "21900 COP/month", quote, "meli_co_total")
    assert intro.status == "value_not_in_quote" and "regular" in intro.reason
    # Free shipping is not an offer on the price: it is the regular one.
    assert _ok(_check("money", "21900 COP/month", quote, "meli_co_total"))
    # A real offer word without a duration confirms neither.
    offer = "Meli+ Total: oferta especial $ 21.900/mes"
    for kind in ("money", "intro_money"):
        result = _check(kind, "21900 COP/month", offer, "meli_co_total")
        assert result.status == "value_not_in_quote" and "offer price" in result.reason


def test_an_offer_word_on_a_previous_line_does_not_reach_the_price() -> None:
    """A line break ends a clause: "Envíos gratis" above a card's price is a benefit."""
    lines = [
        "Meli+ Esencial",
        "Envíos gratis en millones de productos",
        "$ 9.900/mes",
        "Suscribirme",
    ]
    regular = _check(
        "money", "9900 COP/month", "$ 9.900/mes", "meli_co_esencial", context_lines=lines
    )
    assert _ok(regular), regular
    intro = _check(
        "intro_money", "9900 COP/month", "$ 9.900/mes", "meli_co_esencial", context_lines=lines
    )
    assert intro.status == "value_not_in_quote"


@pytest.mark.parametrize(
    ("quote", "context"),
    [
        # The duration on the next line may be the price's or the next item's.
        ("$ 19.900/mes", ["Meli+ Total", "Plan", "$ 19.900/mes", "por 2 meses"]),
        # A new sentence about another offer (a free trial).
        ("Meli+ Total $ 19.900/mes. Prueba gratis por 1 mes.", []),
    ],
)
def test_a_duration_past_the_prices_sentence_confirms_neither_role(
    quote: str, context: list[str]
) -> None:
    for kind in ("money", "intro_money"):
        result = _check(kind, "19900 COP/month", quote, "meli_co_total", context_lines=context)
        assert result.status == "value_not_in_quote" and "offer price" in result.reason, result


def test_a_then_price_after_a_free_trial_is_regular() -> None:
    quote = "Apple One Familj: Prova gratis i 1 månad, sedan 295 kr/mån"
    assert _ok(_check("money", "295 SEK/month", quote, "apple_se_family"))


@pytest.mark.parametrize(
    ("value", "quote", "context", "kw"),
    [
        # The struck-through markup is lost: two prices, the second lower, nothing between.
        ("24900 COP/month", "$ 24.900", ["Meli+ Total", "$ 24.900", "$ 21.900/mes", "x"], {}),
        ("24900 COP/month", "Meli+ Total $ 24.900 $ 21.900/mes", [], {}),
        ("24900 COP/month", "Meli+ Total $ 24.900 12% OFF $ 21.900/mes", [], {}),
        ("24900 COP/month", "$ 24.900", ["Meli+ Total", "12% OFF", "$ 24.900", "$ 21.900/mes"], {}),
        (
            "339 SEK/month",
            "Apple One Familj 339 kr 295 kr/mån",
            [],
            {"bundle_id": "apple_se_family"},
        ),
        (
            "29.90 BRL/month",
            "Meli+ Mega R$ 29,90 R$ 19,90/mês",
            [],
            {"bundle_id": "meli_br_mega", "country": "BR", "currency": "BRL"},
        ),
    ],
)
def test_a_struck_through_price_read_by_position_is_a_comparison_price(
    value: str, quote: str, context: list[str], kw: dict[str, Any]
) -> None:
    bundle_id = kw.pop("bundle_id", "meli_co_total")
    result = _check("money", value, quote, bundle_id, context_lines=context, **kw)
    assert result.status == "value_not_in_quote" and "comparison" in result.reason, result


@pytest.mark.parametrize(
    ("value", "quote", "context", "kw"),
    [
        (
            "29900 COP/month",
            "Antes $ 29.900 Ahora $ 19.900/mes por 2 meses",
            [
                "Meli+ Total",
                "Antes $ 29.900 Ahora $ 19.900/mes por 2 meses",
                "Luego $ 21.900/mes",
                "Suscribirme",
            ],
            {},
        ),
        (
            "29900 COP/month",
            "Meli+ Total: precio anterior $ 29.900, hoy $ 19.900 por 2 meses.",
            [
                "Meli+ Total: precio anterior $ 29.900, hoy $ 19.900 por 2 meses.",
                "Después de 2 meses pagas $ 21.900 al mes.",
            ],
            {},
        ),
        (
            "29.90 BRL/month",
            "De R$ 29,90 por R$ 19,90 nos 3 primeiros meses",
            ["Meli+ Mega", "De R$ 29,90 por R$ 19,90 nos 3 primeiros meses", "Depois R$ 24,90/mês"],
            {"bundle_id": "meli_br_mega", "country": "BR", "currency": "BRL"},
        ),
    ],
)
def test_a_previous_price_is_not_regular_when_the_text_states_the_price_after_the_offer(
    value: str, quote: str, context: list[str], kw: dict[str, Any]
) -> None:
    bundle_id = kw.pop("bundle_id", "meli_co_total")
    result = _check("money", value, quote, bundle_id, context_lines=context, **kw)
    assert result.status == "value_not_in_quote" and "after the offer" in result.reason, result
    # The real Meli+ line still has none.
    assert _ok(
        _check("money", "21900 COP/month", MELI_LINE, "meli_co_total", context_lines=MELI_CONTEXT)
    )


@pytest.mark.parametrize(
    ("value", "quote", "bundle_id"),
    [
        ("215 SEK/month", "Spara 58/77/215 kr/mån jämfört med att köpa tjänsterna separat", None),
        ("12000 COP/month", "Meli+ Total: ahorro de $ 12.000 al mes", "meli_co_total"),
        ("12000 COP/month", "Meli+ Total. Ahorro mensual: $ 12.000", "meli_co_total"),
        ("12000 COP/month", "Meli+ Total: economia de $ 12.000 por mês", "meli_co_total"),
        ("12000 COP/month", "Meli+ Total: descuento de $ 12.000 al mes", "meli_co_total"),
        ("12000 COP/month", "Meli+ Total: $ 12.000 de descuento al mes", "meli_co_total"),
        ("12000 COP/month", "Meli+ Total: $ 12.000 OFF al mes", "meli_co_total"),
        ("12000 COP/month", "Meli+ Total: dto. $ 12.000/mes", "meli_co_total"),
        ("58 SEK/month", "Apple One Familj: Rabatt 58 kr/mån", "apple_se_family"),
        ("58 SEK/month", "Apple One Familj: 58 kr rabatt i månaden", "apple_se_family"),
        ("58 SEK/month", "Apple One Familj. Besparing: 58 kr/mån", "apple_se_family"),
        ("58 SEK/month", "Apple One Familj: 58 kr/mån billigare än separat", "apple_se_family"),
        ("9910 MXN/month", "Meli+ Mega $ 9910% OFF", "meli_mx_mega"),  # "$ 99" + "10% OFF"
        # Real telia.se lines.
        (
            "100 SEK/month",
            "Samlingsrabatt: Samla bredband och mobilabonnemang hos oss och få upp till 100 kr/mån "
            "i rabatt på ditt mobilabonnemang.",
            None,
        ),
        ("100 SEK/month", "100 kr/mån i samlingsrabatt om du har bredband från Telia", None),
        ("50 SEK/month", "Är du 18-27 år får du 50 kr rabatt/mån på mobilabonnemanget.", None),
    ],
)
def test_savings_and_discount_amounts_are_no_price(
    value: str, quote: str, bundle_id: str | None
) -> None:
    result = _check("money", value, quote, bundle_id)
    assert result.status == "value_not_in_quote" and "saved" in result.reason, result


@pytest.mark.parametrize(
    ("value", "quote", "bundle_id"),
    [
        ("80000 COP/month", "Meli+ Total. Valor total: $ 80.000/mes", "meli_co_total"),
        (
            "80000 COP/month",
            "Meli+ Total incluye servicios por un valor total de $ 80.000/mes",
            "meli_co_total",
        ),
        ("80000 COP/month", "Meli+ Total: beneficios valorizados en $ 80.000/mes", "meli_co_total"),
        ("80000 COP/month", "Meli+ Total: servicios que valen $ 80.000/mes", "meli_co_total"),
        ("80000 COP/month", "Meli+ Total: por separado $ 80.000/mes", "meli_co_total"),
        (
            "80000 COP/month",
            "Meli+ Total: comprados por separado cuestan $ 80.000/mes",
            "meli_co_total",
        ),
        (
            "80000 COP/month",
            "Meli+ Total: $ 80.000/mes si los compras separadamente",
            "meli_co_total",
        ),
        (
            "80000 COP/month",
            "Meli+ Total $ 21.900/mes en lugar de pagar $ 80.000/mes",
            "meli_co_total",
        ),
        (
            "80000 COP/month",
            "Meli+ Total $ 21.900/mes ao invés de pagar $ 80.000/mes",
            "meli_co_total",
        ),
        ("500 SEK/month", "Apple One Familj: Värde 500 kr/mån", "apple_se_family"),
        (
            "500 SEK/month",
            "Apple One Familj: tjänster till ett värde av 500 kr/mån",
            "apple_se_family",
        ),
        (
            "500 SEK/month",
            "Apple One Familj 295 kr/mån istället för att betala 500 kr/mån",
            "apple_se_family",
        ),
        (
            "500 SEK/month",
            "Apple One Familj 295 kr/mån instead of paying 500 kr/mån",
            "apple_se_family",
        ),
        ("500 SEK/month", "Apple One Familj 295 kr/mån vs 500 kr/mån", "apple_se_family"),
        ("99 SEK/month", "Fri frakt via telia.se (värde 99 kr)", None),  # real line
    ],
)
def test_value_and_sum_amounts_are_comparison_prices(
    value: str, quote: str, bundle_id: str | None
) -> None:
    result = _check("money", value, quote, bundle_id)
    assert result.status == "value_not_in_quote" and "comparison" in result.reason, result


@pytest.mark.parametrize(
    ("value", "quote"),
    [
        ("5000 COP/month", "Meli+ Total: ahorra $ 5.000 por 3 meses"),
        ("5000 COP/month", "Meli+ Total: descuento de $ 5.000 por 3 meses"),
        ("5000 COP/month", "Meli+ Total: $ 5.000 OFF por 3 meses"),
    ],
)
def test_a_discount_with_a_duration_is_no_intro_price(value: str, quote: str) -> None:
    result = _check("intro_money", value, quote, "meli_co_total")
    assert result.status == "value_not_in_quote" and "saved" in result.reason, result
    result = _check("intro_money", "70 SEK/month", "Apple One Familj: 70 kr rabatt i 6 månader")
    assert result.status == "value_not_in_quote" and "saved" in result.reason


@pytest.mark.parametrize(
    "quote",
    [
        "Meli+ Total: $ 19.900 el primer mes",
        "Meli+ Total por solo $ 19.900 el primer mes",
        "Meli+ Total: $ 19.900 tu primer mes",
        "Meli+ Total: $ 19.900 durante el primer mes",
        "Meli+ Total: $ 19.900 no primeiro mês",
        "Meli+ Total $ 19.900/mes hasta 3 meses",
    ],
)
def test_a_first_month_or_up_to_n_months_is_an_intro(quote: str) -> None:
    regular = _check("money", "19900 COP/month", quote, "meli_co_total")
    assert regular.status == "value_not_in_quote" and "introductory" in regular.reason, regular
    assert _ok(_check("intro_money", "19900 COP/month", quote, "meli_co_total"))
    swedish = _check("money", "199 SEK/month", "Apple One Familj 199 kr första månaden")
    assert swedish.status == "value_not_in_quote" and "introductory" in swedish.reason


@pytest.mark.parametrize(
    "quote",
    [
        "Meli+ Total $ 19.900/mes hasta el 31 de diciembre",
        "Meli+ Total $ 19.900/mes, precio promocional válido hasta el 31/12/2026",
        "Meli+ Total $ 19.900/mes precio promocional",
        "Meli+ Total $ 19.900/mes con 9% de descuento",
        "Meli+ Total con descuento: $ 19.900/mes",
        "Meli+ Total precio con descuento $ 19.900/mes",
        "Meli+ Total desde $ 19.900/mes",
        "Meli+ Total a partir de $ 19.900/mes",
        "Meli+ Total $ 19.900 - $ 29.900/mes",  # a range
        "Meli+ Total entre $ 19.900 y $ 29.900 al mes",
    ],
)
def test_an_offer_without_a_duration_confirms_neither_role(quote: str) -> None:
    for kind in ("money", "intro_money"):
        result = _check(kind, "19900 COP/month", quote, "meli_co_total")
        assert result.status == "value_not_in_quote" and "offer price" in result.reason, result


@pytest.mark.parametrize(
    ("value", "quote", "kw"),
    [
        # Free-shipping thresholds (real lines).
        (
            "19 BRL/month",
            "Frete grátis rápido a partir de R$ 19.",
            {"bundle_id": "meli_br_mega", "country": "BR", "currency": "BRL"},
        ),
        (
            "30000 COP/month",
            "Envíos gratis en millones de productos desde $ 30.000.",
            {
                "bundle_id": "meli_co_esencial",
                "context_lines": [
                    "Meli+ Esencial",
                    "$ 9.900/mes",
                    "Envíos gratis en millones de productos desde $ 30.000.",
                    "Meli+ Total",
                ],
            },
        ),
        # The intro amount restated on the next line's button (real BR lines).
        (
            "15.90 BRL/month",
            "Assinar por R$ 15,90",
            {
                "country": "BR",
                "currency": "BRL",
                "context_lines": [
                    "Meli+ Total",
                    "R$ 15,90 /mês por 2 meses",
                    "Assinar por R$ 15,90",
                    "Meli+ Mega",
                ],
            },
        ),
        (
            "199 SEK/month",
            "Köp för 199 kr/mån",
            {"context_lines": ["Telia Play Mer", "199 kr/mån i 6 mån", "Köp för 199 kr/mån", "x"]},
        ),
        ("295 SEK/month", "Apple One Familj 199/295 kr/mån", {"bundle_id": "apple_se_family"}),
    ],
)
def test_thresholds_restated_intros_and_lists_are_offers(
    value: str, quote: str, kw: dict[str, Any]
) -> None:
    bundle_id = kw.pop("bundle_id", None)
    result = _check("money", value, quote, bundle_id, **kw)
    assert result.status == "value_not_in_quote" and "offer price" in result.reason, result


@pytest.mark.parametrize(
    ("value", "quote", "bundle_id"),
    [
        (
            "10483 COP/month",
            "Plan Familiar $62.900 al mes ($10.483 por persona)",
            "apple_co_family",
        ),
        ("49 SEK/month", "Apple One Familj 295 kr/mån – bara 49 kr per person", "apple_se_family"),
        ("10483 COP/month", "Apple One Familiar: $ 10.483 por usuario al mes", "apple_co_family"),
        ("10483 COP/month", "Apple One Familiar: $ 10.483 por pessoa/mês", "apple_co_family"),
        ("49 SEK/month", "Apple One Familj: 49 kr/mån per familjemedlem", "apple_se_family"),
        (
            "49 SEK/month",
            "Apple One Familj: lägg till en extra användare för 49 kr/mån",
            "apple_se_family",
        ),
        (
            "10000 COP/month",
            "Apple One Familiar: cada miembro adicional $ 10.000/mes",
            "apple_co_family",
        ),
    ],
)
def test_a_price_per_person_is_not_the_bundles(value: str, quote: str, bundle_id: str) -> None:
    result = _check("money", value, quote, bundle_id)
    assert result.status == "value_not_in_quote" and "per person" in result.reason, result
    family = "Plan Familiar $62.900 al mes ($10.483 por persona)"
    assert _ok(_check("money", "62900 COP/month", family, "apple_co_family"))


@pytest.mark.parametrize(
    ("value", "quote", "bundle_id", "why"),
    [
        ("18403 COP/month", "Meli+ Total $ 18.403 + IVA al mes", "meli_co_total", "before tax"),
        (
            "18403 COP/month",
            "Meli+ Total: precio sin IVA $ 18.403/mes",
            "meli_co_total",
            "before tax",
        ),
        (
            "172 SEK/month",
            "Apple One Familj 172 kr/mån exkl. moms",
            "apple_se_family",
            "before tax",
        ),
        (
            "172 SEK/month",
            "Apple One Familj, pris exkl. moms: 172 kr/mån",
            "apple_se_family",
            "before tax",
        ),
        ("172 SEK/month", "Apple One Familj 172 kr/mån excl. VAT", "apple_se_family", "before tax"),
        (
            "3497 COP/month",
            "Meli+ Total $ 21.900/mes (IVA incluido $ 3.497)",
            "meli_co_total",
            "tax",
        ),
        ("59 SEK/month", "Apple One Familj 295 kr/mån varav moms 59 kr", "apple_se_family", "tax"),
        ("59 SEK/month", "Apple One Familj 295 kr/mån. Moms: 59 kr/mån", "apple_se_family", "tax"),
    ],
)
def test_prices_before_tax_and_tax_amounts_never_confirm_a_tax_included_price(
    value: str, quote: str, bundle_id: str, why: str
) -> None:
    result = _check("money", value, quote, bundle_id)
    assert result.status == "value_not_in_quote" and why in result.reason, result
    with_vat = "Meli+ Total $ 21.900/mes (IVA incluido $ 3.497)"
    assert _ok(_check("money", "21900 COP/month", with_vat, "meli_co_total"))
    # A bundle stored without tax takes the price before tax.
    b = ROWS["apple_se_family"]
    ex_vat = b.model_copy(update={"price": b.price.model_copy(update={"tax_included": False})})
    quote = "Apple One Familj 172 kr/mån exkl. moms"
    assert _ok(_check("money", "172 SEK/month", quote, "apple_se_family", bundle=ex_vat))


# ---------------------------------------------------------------- tier names (siblings)


@pytest.mark.parametrize(
    ("bundle_id", "amount", "ok"),
    [
        ("apple_se_individual", "215", True),
        ("apple_se_individual", "295", False),
        ("apple_se_family", "295", True),
        ("apple_se_family", "215", False),
    ],
)
def test_apple_se_sibling_tier_the_alias_decides(bundle_id: str, amount: str, ok: bool) -> None:
    result = _check("money", f"{amount} SEK/month", APPLE_SE, bundle_id)
    assert _ok(result) is ok, result
    if not ok:
        assert "tier named next to the amount" in result.reason


@pytest.mark.parametrize(
    "bundle_id", ["apple_co_individual", "apple_co_family", "apple_co_premium"]
)
@pytest.mark.parametrize("amount", ["45900", "62900", "86900"])
def test_apple_co_three_tiers_in_one_sentence(bundle_id: str, amount: str) -> None:
    result = _check("money", f"{amount} COP/month", APPLE_CO, bundle_id)
    assert _ok(result) is (ROWS[bundle_id].price.amount == Decimal(amount)), result


def test_several_tiers_need_the_tier_name_in_the_quote_or_its_context() -> None:
    alone = _check("money", "21900 COP/month", MELI_LINE, "meli_co_total")
    assert alone.status == "value_not_in_quote" and "names no tier" in alone.reason
    named = "Meli+ Total: $ 21.900/mes"
    assert _ok(_check("money", "21900 COP/month", named, "meli_co_total"))
    sibling = _check("money", "21900 COP/month", "Meli+ Esencial: $ 21.900/mes", "meli_co_total")
    assert sibling.status == "value_not_in_quote" and "meli_co_esencial" in sibling.reason


def test_a_single_tier_still_needs_its_name() -> None:
    """Meli+ Mega is the only MX bundle of the YAML, but the real MX page also lists Total
    ($ 99) and Esencial ($ 49.90): a price confirms Mega only next to its name."""
    alone = _check("money", "399 MXN/month", "$ 399/mes", "meli_mx_mega")
    assert alone.status == "value_not_in_quote" and "names no tier" in alone.reason
    assert _ok(_check("money", "399 MXN/month", "Meli+ Mega $ 399/mes", "meli_mx_mega"))
    card = ["Meli+ Esencial", "$ 49.90/mes", "Meli+ Mega", "$ 399/mes"]
    assert _ok(_check("money", "399 MXN/month", card[3], "meli_mx_mega", context_lines=card))
    for value, quote in (
        ("99 MXN/month", "Meli+ Total $ 99/mes"),
        ("49.90 MXN/month", "Meli+ Esencial $ 49.90/mes"),
        ("49.90 MXN/month", "$ 49.90/mes"),
    ):
        result = _check("money", value, quote, "meli_mx_mega")
        assert result.status == "value_not_in_quote" and "names no tier" in result.reason
    # BR: another tier's intro never confirms the only BR row; the page's own card label does.
    br = {"country": "BR", "currency": "BRL"}
    other = _check(
        "intro_money", "15.90 BRL/month", "Meli+ Total R$ 15,90/mês por 2 meses", "meli_br_mega"
    )
    assert other.status == "value_not_in_quote" and "names no tier" in other.reason
    card = ["Plano Mega", "Assinar por R$ 74,90"]
    assert _ok(
        _check("money", "74.90 BRL/month", card[1], "meli_br_mega", context_lines=card, **br)
    )


@pytest.mark.parametrize(
    ("bundle_id", "amount", "ok"),
    [
        ("apple_se_individual", "215", True),
        ("apple_se_individual", "295", False),
        ("apple_se_family", "295", True),
        ("apple_se_family", "215", False),
    ],
)
def test_a_tier_named_after_its_amount_decides(bundle_id: str, amount: str, ok: bool) -> None:
    quote = (
        "Apple One kostar 215 kr i månaden för det personliga abonnemanget och 295 kr i "
        "månaden för familjeabonnemanget."
    )
    result = _check("money", f"{amount} SEK/month", quote, bundle_id)
    assert _ok(result) is ok, result


@pytest.mark.parametrize(
    "bundle_id", ["apple_co_individual", "apple_co_family", "apple_co_premium"]
)
@pytest.mark.parametrize("amount", ["45900", "62900", "86900"])
def test_apple_co_tiers_named_after_their_amounts(bundle_id: str, amount: str) -> None:
    quote = (
        "$45.900 al mes con Individual, $62.900 al mes con Familiar o $86.900 al mes con Premium"
    )
    result = _check("money", f"{amount} COP/month", quote, bundle_id)
    assert _ok(result) is (ROWS[bundle_id].price.amount == Decimal(amount)), result


def test_the_next_cards_heading_is_not_the_amounts_tier() -> None:
    lines = [
        "Millones de productos",
        "Disney+ y HBO Max incluidos",
        "$ 21.900/mes",
        "Meli+ Esencial",
    ]
    result = _check(
        "money", "21900 COP/month", "$ 21.900/mes", "meli_co_esencial", context_lines=lines
    )
    assert result.status == "value_not_in_quote" and "names no tier" in result.reason


def test_a_clause_naming_two_tiers_confirms_neither() -> None:
    quote = "Individual $45.900 al mes Familiar $62.900 al mes"
    result = _check("money", "45900 COP/month", quote, "apple_co_individual")
    assert result.status == "value_not_in_quote" and "several" in result.reason


def test_an_inherited_tier_name_is_not_the_amounts_tier() -> None:
    lines = [
        "Meli+ Total",
        "Todos los beneficios de Meli+ Esencial y Disney+",
        "$ 21.900/mes",
        "Suscribirme",
    ]
    result = _check(
        "money", "21900 COP/month", "$ 21.900/mes", "meli_co_total", context_lines=lines
    )
    assert _ok(result), result


def test_without_bundles_the_curated_ones_are_the_siblings(monkeypatch) -> None:
    """bundles=None loads the curated bundles, so the tier rule is never silently off."""
    import haggler.catalog

    monkeypatch.setattr(haggler.catalog, "default_bundles", lambda: BUNDLES)
    result = check_value(
        "money",
        "295 SEK/month",
        APPLE_SE,
        country="SE",
        currency="SEK",
        period="month",
        provider=BUNDLES.provider("apple"),
        bundle=ROWS["apple_se_individual"],
    )
    assert result.status == "value_not_in_quote" and "familjeabonnemanget" in result.reason


@pytest.mark.parametrize(
    ("kind", "value", "quote", "ok"),
    [
        # The base plan's price never confirms the bundle's, nor the other way round.
        ("money", "569 SEK/month", "Telia Obegränsad Plus 569 kr/mån", False),
        ("base_money", "569 SEK/month", "Telia Obegränsad Plus 569 kr/mån", True),
        (
            "base_money",
            "699 SEK/month",
            "Obegränsad Plus med Netflix, Max och Disney+ 699 kr/mån",
            False,
        ),
        (
            "money",
            "569 SEK/month",
            "Obegränsad Plus 569 kr Obegränsad Plus + Netflix, Max, Disney+ 699 kr",
            False,
        ),
        (
            "base_money",
            "699 SEK/month",
            "Obegränsad Plus 569 kr Obegränsad Plus + Netflix, Max, Disney+ 699 kr",
            False,
        ),
        ("money", "349 SEK/month", "Telia Mobil 30 GB 349 kr/mån", False),
        ("base_money", "449 SEK/month", "Telia Obegränsad 449 kr/mån", False),
        # Real telia.se lines of other products.
        ("money", "319 SEK/month", "Ordinarie pris: 319 kr/mån.", False),
        (
            "money",
            "50 SEK/month",
            "till Netflix Premium (4 enheter kan streama samtidigt istället för 2) för endast 50 "
            "kr/mån, i samband med aktiveringen eller senare via Mitt Telia.",
            False,
        ),
        (
            "money",
            "699 SEK/month",
            "Telia Obegränsad Plus + Netflix, HBO Max y Disney+: 699 kr/mån",
            True,
        ),
    ],
)
def test_a_bundle_on_a_base_plan_needs_its_own_name(
    kind: str, value: str, quote: str, ok: bool
) -> None:
    result = _check(kind, value, quote, "telia_se_plus")
    assert _ok(result) is ok, result


@pytest.mark.parametrize(
    ("bundle_id", "value", "quote", "bundles_without"),
    [
        # The real Apple CO line, with a YAML that has no Premium row.
        ("apple_co_family", "86900 COP/month", APPLE_CO, "apple_co_premium"),
        ("meli_co_total", "9900 COP/month", "Meli+ Total $ 21.900/mes. Esencial $ 9.900/mes", None),
        (
            "meli_co_total",
            "9900 COP/month",
            "Meli+ Total cuesta $ 21.900 al mes. El plan básico cuesta $ 9.900 al mes.",
            None,
        ),
        (
            "meli_co_total",
            "9900 COP/month",
            "Meli+ Total: $ 21.900/mes, o si prefieres el plan más simple, $ 9.900/mes",
            None,
        ),
        (
            "apple_se_family",
            "215 SEK/month",
            "Familjeabonnemanget kostar 295 kr i månaden medan det personliga kostar 215 kr",
            None,
        ),
        (
            "apple_se_family",
            "215 SEK/month",
            "Apple One Familj 295 kr/mån. Individuell 215 kr/mån",
            None,
        ),
        (
            "meli_co_total",
            "9900 COP/month",
            "Meli+ Total\n$ 21.900/mes\n$ 9.900/mes\nMeli+ Esencial",
            None,
        ),
        # An inherited tier name in the amount's sentence.
        (
            "meli_co_total",
            "9900 COP/month",
            "Meli+ Total: todo lo de Meli+ Esencial, que cuesta $ 9.900/mes, más Disney+",
            None,
        ),
        (
            "apple_se_family",
            "215 SEK/month",
            "Apple One Familj: everything in Individual (215 kr/mån), plus family sharing",
            None,
        ),
        # A table row under one heading: the heading is not each column's.
        ("meli_co_total", "9900 COP/month", "Plan Meli+ Total\nPrecio $ 9.900 $ 21.900", None),
    ],
)
def test_a_tier_name_with_another_price_before_the_amount_is_not_its_tier(
    bundle_id: str, value: str, quote: str, bundles_without: str | None
) -> None:
    rows = [b for b in BUNDLES.bundles if b.bundle_id != bundles_without]
    result = _check("money", value, quote, bundle_id, bundles=rows)
    assert result.status == "value_not_in_quote" and "names no tier" in result.reason, result
    if bundles_without:  # each amount of the real line still confirms its own tier
        assert _ok(_check("money", "62900 COP/month", APPLE_CO, "apple_co_family", bundles=rows))


@pytest.mark.parametrize(
    ("kind", "value", "quote", "bundle_id", "kw"),
    [
        (
            "intro_money",
            "19900 COP/month",
            "$ 19.900/mes por 2 meses",
            "meli_co_total",
            {"context_lines": ["Meli+ Total", "$ 19.900/mes por 2 meses", MELI_LINE]},
        ),
        (
            "money",
            "9900 COP/month",
            "$ 9.900/mes",
            "meli_co_esencial",
            {
                "context_lines": [
                    "Meli+ Esencial",
                    "$ 9.900/mes",
                    "Envíos gratis en millones de productos desde $ 30.000.",
                ]
            },
        ),
        (
            "money",
            "399 MXN/month",
            "$ 399/mes",
            "meli_mx_mega",
            {"context_lines": ["Meli+ Mega", "$ 399/mes"]},
        ),
        (
            "money",
            "74.90 BRL/month",
            "Assinar por R$ 74,90",
            "meli_br_mega",
            {
                "country": "BR",
                "currency": "BRL",
                "context_lines": ["Plano Mega", "Assinar por R$ 74,90"],
            },
        ),
        (
            "money",
            "79000 COP/month",
            "COP 79,000 por mes",
            "google_co_pro",
            {"context_lines": ["Google AI Pro", "COP 79,000 por mes"]},
        ),
    ],
)
def test_real_probe_lines_with_their_card_heading_confirm(
    kind: str, value: str, quote: str, bundle_id: str, kw: dict[str, Any]
) -> None:
    """The H0 lines whose card heading names the tier (an alias must be the page's label:
    "Plano Mega" on the BR page)."""
    result = _check(kind, value, quote, bundle_id, **kw)
    assert _ok(result), result


def test_an_inherited_name_in_the_amounts_own_clause_is_its_tier() -> None:
    quote = "Disfruta todos los beneficios de Meli+ Esencial por $ 9.900/mes"
    lines = ["Meli+ Total $ 21.900/mes", "Suscribirme", quote, "Suscribirme"]
    result = _check("money", "9900 COP/month", quote, "meli_co_total", context_lines=lines)
    assert result.status == "value_not_in_quote" and "meli_co_esencial" in result.reason, result
    assert _ok(_check("money", "9900 COP/month", quote, "meli_co_esencial"))


def test_a_tier_named_in_a_comparison_is_not_the_amounts() -> None:
    """Real gemini.google/co line under the Ultra card: "5 veces más altos que los de AI Pro"."""
    quote = "COP 449,000/ mes: Límites de uso 5 veces más altos que los de AI Pro"
    lines = [
        "Google AI Ultra",
        "COP 449,000/ mes",
        quote,
        "COP 790,000 / mes: Límites de uso 20 veces más altos que los de AI Pro",
    ]
    pro = _check("money", "449000 COP/month", quote, "google_co_pro", context_lines=lines)
    assert pro.status == "value_not_in_quote" and "google_co_ultra" in pro.reason, pro
    ultra = _check("money", "449000 COP/month", quote, "google_co_ultra", context_lines=lines)
    assert _ok(ultra), ultra


# ------------------------------------------------------- card headings (the real planes page)

PLANES_URL = "https://www.mercadolibre.com.co/suscripciones/melimas/planes"
# Our fetch of the planes page (2026-10-11), consecutive visible lines: the Meli+ Esencial card,
# then the Meli+ Total card (the benefit lines between its "Envíos gratis" and "Disney+" ones
# left out). Markup doubles a line's text: the visible one and the accessible label.
PLANES_ESENCIAL = [
    "Plan Esencial",
    "-",
    "$ 9.900/mes",
    "Disney+ Estándar con anuncios.No incluido: Disney+ Estándar con anuncios.",
    MELI_COMPONENTS + "No incluido: " + MELI_COMPONENTS,
]
PLANES_TOTAL = [
    "Plan Total",
    "$ 21.900 9% OFF",
    "$ 19.900/mes por 2 meses",
    MELI_LINE,
    "Envíos gratis en millones de productos desde $ 30.000.Envíos gratis en millones de "
    "productos desde 30,000 pesos",
    "Disney+ Estándar con anuncios.Disney+ Estándar con anuncios.",
    MELI_COMPONENTS + MELI_COMPONENTS,
]
PLANES = [*PLANES_ESENCIAL, *PLANES_TOTAL]
TOTAL_PRICES = "\n".join(PLANES_TOTAL[:4])  # "Plan Total" ... "Precio anterior: $ 21.900 ..."
TOTAL_INTRO = "\n".join(PLANES_TOTAL[:3])  # "Plan Total" ... "$ 19.900/mes por 2 meses"
TOTAL, ESENCIAL = "meli_plus_co_total", "meli_plus_co_esencial"
# The two rows as bundles.yaml has them: the page's card labels are aliases.
PLANES_BUNDLES = BundlesFile.model_validate(
    {
        "providers": PROVIDERS,
        "bundles": [
            {
                "bundle_id": bundle_id,
                "provider_id": "meli",
                "provider_type": "marketplace",
                "country": "CO",
                "name": name,
                "tier": bundle_id.rsplit("_", 1)[-1],
                "aliases": [name, label],
                "official_url": PLANES_URL,
                "price": {"amount": amount, "currency": "COP", "tax_included": True, **intro},
                "components": [],
                "last_verified": "2026-10-11",
            }
            for bundle_id, name, label, amount, intro in (
                (
                    TOTAL,
                    "Meli+ Total",
                    "Plan Total",
                    "21900",
                    {"intro_amount": "19900", "intro_months": 2},
                ),
                (ESENCIAL, "Meli+ Esencial", "Plan Esencial", "9900", {}),
            )
        ],
    }
)


def _planes(kind: str, value: Any, quote: str, bundle_id: str, **kw: Any) -> ValueCheck:
    bundle = next(b for b in PLANES_BUNDLES.bundles if b.bundle_id == bundle_id)
    return check_value(
        kind,  # type: ignore[arg-type]
        value,
        quote,
        country="CO",
        currency="COP",
        period="month",
        provider=PLANES_BUNDLES.provider("meli"),
        bundle=bundle,
        bundles=PLANES_BUNDLES.bundles,
        **kw,
    )


def _window(quote: str) -> list[str]:
    """The context lines Page.locate gives the quote on the planes page: 2 before, 1 after."""
    page = Page(url=PLANES_URL, text="\n".join(PLANES), content_type="text/plain")
    hit = page.locate(quote)
    assert hit is not None, quote
    return hit[1]


@pytest.mark.parametrize("in_window", [False, True])
def test_the_real_total_card_confirms_its_regular_and_intro_prices(in_window: bool) -> None:
    """The line "Plan Total" heads its card: the amounts below it are Total's, past the struck
    "$ 21.900 9% OFF". The roles do not change: that struck price is a comparison one, the
    "Precio anterior" next to its own timed intro the regular one."""

    def check(kind: str, value: str, quote: str) -> ValueCheck:
        lines = _window(quote) if in_window else []
        return _planes(kind, value, quote, TOTAL, context_lines=lines)

    regular = check("money", "21900 COP/month", TOTAL_PRICES)
    assert _ok(regular) and "'$ 21.900' regular" in regular.reason, regular
    for quote in (TOTAL_PRICES, TOTAL_INTRO):
        intro = check("intro_money", "19900 COP/month", quote)
        assert _ok(intro), intro
    struck = check("money", "21900 COP/month", TOTAL_INTRO)
    assert struck.status == "value_not_in_quote" and "comparison" in struck.reason, struck
    assert "names no tier" not in struck.reason, struck
    wrong = check("money", "19900 COP/month", TOTAL_PRICES)
    assert wrong.status == "value_not_in_quote" and "introductory" in wrong.reason, wrong
    wrong = check("intro_money", "21900 COP/month", TOTAL_PRICES)
    assert wrong.status == "value_not_in_quote", wrong


@pytest.mark.parametrize(
    ("kind", "value", "quote", "owner"),
    [
        pytest.param(
            "money", "9900 COP/month", "\n".join(PLANES_ESENCIAL[:3]), ESENCIAL, id="esencial"
        ),
        pytest.param("money", "21900 COP/month", TOTAL_PRICES, TOTAL, id="total-regular"),
        pytest.param("intro_money", "19900 COP/month", TOTAL_PRICES, TOTAL, id="total-intro"),
        pytest.param("intro_money", "19900 COP/month", TOTAL_INTRO, TOTAL, id="total-intro-3"),
        # Both cards in one text: each amount is its own card's.
        pytest.param("money", "9900 COP/month", "\n".join(PLANES), ESENCIAL, id="page-esencial"),
        pytest.param("money", "21900 COP/month", "\n".join(PLANES), TOTAL, id="page-regular"),
        pytest.param("intro_money", "19900 COP/month", "\n".join(PLANES), TOTAL, id="page-intro"),
    ],
)
def test_one_cards_prices_never_confirm_the_other_card(
    kind: str, value: str, quote: str, owner: str
) -> None:
    assert _ok(_planes(kind, value, quote, owner))
    other = ESENCIAL if owner == TOTAL else TOTAL
    result = _planes(kind, value, quote, other)
    assert result.status == "value_not_in_quote" and owner in result.reason, result


@pytest.mark.parametrize(
    ("kind", "value"),
    [
        ("component", "disney_plus.standard_with_ads"),
        ("discount_pct", "hbo_max:30"),
        ("discount_pct", "paramount_plus:30"),
    ],
)
def test_one_cards_component_lines_never_confirm_the_other_card(kind: str, value: str, cat) -> None:
    total_card = "\n".join(PLANES_TOTAL)
    assert _ok(_planes(kind, value, total_card, TOTAL, catalog=cat))
    result = _planes(kind, value, total_card, ESENCIAL, catalog=cat)
    assert result.status == "value_not_in_quote" and TOTAL in result.reason, result
    for bundle_id in (TOTAL, ESENCIAL):  # the Esencial card lists them as not included
        result = _planes(kind, value, "\n".join(PLANES_ESENCIAL), bundle_id, catalog=cat)
        assert result.status == "value_not_in_quote" and "not included" in result.reason


@pytest.mark.parametrize(
    ("lines", "kind", "value", "owner"),
    [
        (
            ["Plan Total", "$ 21.900 9% OFF", "Plan Esencial", "$ 9.900/mes"],
            "money",
            "9900 COP/month",
            ESENCIAL,
        ),
        # Nothing between the two headings: the card still ends at the sibling's.
        (
            ["Plan Total", "Plan Esencial", "$ 19.900/mes por 2 meses"],
            "intro_money",
            "19900 COP/month",
            ESENCIAL,
        ),
        (
            ["Plan Esencial", "Plan Total", "$ 19.900/mes por 2 meses"],
            "intro_money",
            "19900 COP/month",
            TOTAL,
        ),
        # Under "Plan Esencial", "$ 9.900/mes" is Esencial's even if Total's heading follows.
        ([*PLANES_ESENCIAL[:3], "Plan Total"], "money", "9900 COP/month", ESENCIAL),
        ([*PLANES_ESENCIAL, *PLANES_TOTAL[:3]], "money", "9900 COP/month", ESENCIAL),
    ],
)
def test_a_sibling_heading_ends_a_card(lines: list[str], kind: str, value: str, owner: str) -> None:
    quote = "\n".join(lines)
    assert _ok(_planes(kind, value, quote, owner))
    other = ESENCIAL if owner == TOTAL else TOTAL
    result = _planes(kind, value, quote, other)
    assert result.status == "value_not_in_quote" and owner in result.reason, result


@pytest.mark.parametrize(
    ("lines", "why"),
    [
        (["Plan Esencial / Plan Total", *PLANES_TOTAL[1:3]], "names no tier"),
        (["Plan Total y Plan Esencial", *PLANES_TOTAL[1:3]], "names no tier"),
        (["Meli+ Total | Meli+ Esencial", *PLANES_TOTAL[1:3]], "names no tier"),
        # One clause naming two tiers, and no struck price below it: several.
        (["Plan Esencial / Plan Total", PLANES_TOTAL[2]], "several"),
    ],
)
def test_a_line_naming_two_tiers_heads_no_card(lines: list[str], why: str) -> None:
    for bundle_id in (TOTAL, ESENCIAL):
        result = _planes("intro_money", "19900 COP/month", "\n".join(lines), bundle_id)
        assert result.status == "value_not_in_quote" and why in result.reason, result


@pytest.mark.parametrize(
    "lines",
    [
        ["Plan Total $ 21.900 9% OFF", PLANES_TOTAL[2]],  # the heading's line states a price
        ["Plan Total desde US$ 5", *PLANES_TOTAL[1:3]],  # in another currency too
    ],
)
def test_a_heading_line_that_states_an_amount_keeps_the_clause_rules(lines: list[str]) -> None:
    """Below a tier name that shares its line with an amount, the earlier-clause rule decides:
    the struck price between them keeps the intro from the name."""
    result = _planes("intro_money", "19900 COP/month", "\n".join(lines), TOTAL)
    assert result.status == "value_not_in_quote" and "no other price in between" in result.reason
    # The tier named in the amount's own clause still decides.
    quote = "Plan Total: $ 19.900/mes por 2 meses"
    assert _ok(_planes("intro_money", "19900 COP/month", quote, TOTAL))


@pytest.mark.parametrize("heading", ["Planes", "Elige tu plan de Meli+"])
def test_a_generic_heading_is_no_tier_heading(heading: str) -> None:
    quote = "\n".join([heading, *PLANES_TOTAL[1:4]])
    for kind, value in (("money", "21900 COP/month"), ("intro_money", "19900 COP/month")):
        result = _planes(kind, value, quote, TOTAL)
        assert result.status == "value_not_in_quote" and "names no tier" in result.reason, result


@pytest.mark.parametrize(
    ("quote", "kind", "value", "bundle_id", "ok"),
    [
        ("$ 19.900/mes por 2 meses", "intro_money", "19900 COP/month", TOTAL, True),
        ("$ 19.900/mes por 2 meses", "intro_money", "19900 COP/month", ESENCIAL, False),
        ("$ 9.900/mes", "money", "9900 COP/month", ESENCIAL, True),
        ("$ 9.900/mes", "money", "9900 COP/month", TOTAL, False),
        # Three lines below "Plan Total", the real line's window does not reach its heading.
        (MELI_LINE, "money", "21900 COP/month", TOTAL, False),
        (MELI_LINE, "intro_money", "19900 COP/month", TOTAL, False),
    ],
)
def test_a_card_is_read_in_the_quotes_context_window(
    quote: str, kind: str, value: str, bundle_id: str, ok: bool
) -> None:
    lines = _window(quote)
    result = _planes(kind, value, quote, bundle_id, context_lines=lines)
    assert _ok(result) is ok, (lines, result)
    if quote == MELI_LINE:
        assert "Plan Total" not in lines and "names no tier" in result.reason, result


@pytest.mark.parametrize(
    ("value", "lines"),
    [
        ("21900 COP/month", ["Meli+ Total", "$ 24.900", "$ 21.900/mes"]),
        ("21900 COP/month", ["Plan Total", "$ 24.900 12% OFF", "$ 21.900/mes"]),
        # The real card without its duration: a discount the card states no end for.
        ("19900 COP/month", ["Plan Total", "$ 21.900 9% OFF", "$ 19.900/mes"]),
    ],
)
def test_under_a_card_only_an_intro_follows_a_price_struck_through_by_position(
    value: str, lines: list[str]
) -> None:
    result = _planes("money", value, "\n".join(lines), TOTAL)
    assert result.status == "value_not_in_quote" and "struck through by position" in result.reason
    # The intro's own struck price does not separate them, on its line or the one above.
    for card in (
        ["Plan Total", "$ 21.900 9% OFF $ 19.900/mes por 2 meses"],
        ["Plan Total", "$ 24.900", "$ 19.900/mes por 2 meses"],
    ):
        assert _ok(_planes("intro_money", "19900 COP/month", "\n".join(card), TOTAL)), card


@pytest.mark.parametrize(
    ("quote", "bundle_id"),
    [
        ("Plan Total\nEnvíos gratis desde $ 30.000\n$ 21.900/mes", TOTAL),
        ("Plan Esencial\nEnvíos gratis desde $ 30.000\n$ 9.900/mes", ESENCIAL),
    ],
)
def test_under_a_card_a_threshold_above_a_regular_price_reads_as_struck(
    quote: str, bundle_id: str
) -> None:
    """Errs toward rejecting: the threshold right above a lower price is struck by position."""
    value = "21900 COP/month" if bundle_id == TOTAL else "9900 COP/month"
    result = _planes("money", value, quote, bundle_id)
    assert result.status == "value_not_in_quote" and "struck through by position" in result.reason


@pytest.mark.parametrize(
    ("lines", "bundle_id", "value"),
    [
        # A price with words before it on its line is no intro's own struck price: a threshold,
        # an add-on (whose intro the next line may be).
        (
            ["Plan Total", "Envíos gratis desde $ 30.000", "$ 19.900/mes por 2 meses"],
            TOTAL,
            "19900",
        ),
        (
            ["Plan Esencial", "Agrega Disney+ por $ 8.000/mes", "$ 4.900/mes por 2 meses"],
            ESENCIAL,
            "4900",
        ),
        # Struck by another price, not by the intro: two discounts the card does not tell apart.
        (["Plan Total", "$ 24.900", "$ 21.900/mes", "$ 19.900/mes por 2 meses"], TOTAL, "19900"),
        # A struck price of another intro amount.
        (
            ["Plan Total", "$ 49.900 20% OFF", "$ 39.900/mes por 3 meses", PLANES_TOTAL[2]],
            TOTAL,
            "19900",
        ),
    ],
)
def test_under_a_card_only_the_intros_own_struck_price_is_waived(
    lines: list[str], bundle_id: str, value: str
) -> None:
    result = _planes("intro_money", f"{value} COP/month", "\n".join(lines), bundle_id)
    assert result.status == "value_not_in_quote", result
    assert "another price lies between them" in result.reason, result


@pytest.mark.parametrize(
    ("kind", "value", "lines", "bundle_id"),
    [
        # Two regular prices under one heading, one feature line between them.
        (
            "money",
            "9900 COP/month",
            ["Meli+ Total", "$ 21.900/mes", "Envíos gratis", "$ 9.900/mes", "Meli+ Esencial"],
            TOTAL,
        ),
        # An add-on, a payment variant, a footer below the last card.
        (
            "money",
            "8000 COP/month",
            [
                "Plan Total",
                "$ 21.900/mes",
                "Agrega Disney+ Premium sin anuncios por $ 8.000/mes",
            ],
            TOTAL,
        ),
        (
            "money",
            "14900 COP/month",
            ["Plan Total", "$ 21.900/mes", "Agrega Disney+ Premium por $ 14.900/mes"],
            TOTAL,
        ),
        (
            "money",
            "17900 COP/month",
            ["Plan Total", "$ 21.900/mes", "Con Mercado Pago: $ 17.900/mes"],
            TOTAL,
        ),
        (
            "money",
            "15900 COP/month",
            [
                "Plan Esencial",
                "$ 9.900/mes",
                "Plan Total",
                "$ 21.900/mes",
                "Todos los planes incluyen envíos gratis.",
                "Tarjeta de crédito Mercado Pago: cuota de manejo $ 15.900/mes",
            ],
            TOTAL,
        ),
        # The next card has no text heading.
        (
            "money",
            "9900 COP/month",
            [
                "Plan Total",
                "$ 21.900/mes",
                "Envíos gratis en millones de productos",
                "Disney+ Estándar con anuncios",
                "$ 9.900/mes",
                "Envíos gratis en millones de productos",
            ],
            TOTAL,
        ),
        # A column-major comparison table: each header cell is a line naming one tier.
        (
            "intro_money",
            "4900 COP/month",
            [
                "Compara los planes",
                "Plan Esencial",
                "Plan Total",
                "Precio",
                "$ 9.900/mes",
                "$ 21.900/mes",
                "Precio de lanzamiento",
                "$ 4.900/mes por 2 meses",
                "$ 19.900/mes por 2 meses",
            ],
            TOTAL,
        ),
        # The real CTA line "Elegir Plan Total" names one tier and no amount.
        (
            "money",
            "29900 COP/month",
            [
                "Elegir Plan Total",
                "Disney+, Suscripciones",
                "Disney+ Premium",
                "$ 38.900/mes",
                "HBO Max, Suscripciones",
                "HBO Max Platino",
                "$ 29.900/mes",
            ],
            TOTAL,
        ),
        # A tier missing from the rows does not end the card: the heading's price still does.
        (
            "money",
            "39900 COP/month",
            ["Plan Total", "$ 21.900/mes", "Plan Mega", "$ 39.900/mes"],
            TOTAL,
        ),
        (
            "intro_money",
            "29900 COP/month",
            ["Plan Total", "$ 21.900/mes", "Plan Mega", "$ 29.900/mes por 3 meses"],
            TOTAL,
        ),
        # Total's real intro lines below Esencial's card, after a generic or drifted heading.
        (
            "intro_money",
            "19900 COP/month",
            ["Plan Esencial", "$ 9.900/mes", "Planes", "$ 21.900 9% OFF", PLANES_TOTAL[2]],
            ESENCIAL,
        ),
        (
            "intro_money",
            "19900 COP/month",
            ["Plan Esencial", "-", "$ 9.900/mes", "Total", "$ 21.900 9% OFF", PLANES_TOTAL[2]],
            ESENCIAL,
        ),
    ],
)
def test_under_a_card_another_price_separates_the_heading_from_the_amount(
    kind: str, value: str, lines: list[str], bundle_id: str
) -> None:
    """Only a price struck through by position next to an intro is the same offer's: any other
    price between a card heading and the amount keeps the amount from the heading's tier."""
    result = _planes(kind, value, "\n".join(lines), bundle_id)
    assert result.status == "value_not_in_quote", result
    assert "another price lies between them" in result.reason, result


def test_a_watched_line_under_a_card_is_not_the_cards_price() -> None:
    """The watcher's per-line call: an add-on's price below the card's own is no price hint."""
    result = _planes(
        "money",
        Decimal("7900"),
        "Paramount+ $ 7.900/mes",
        TOTAL,
        context_lines=["Plan Total", "$ 21.900/mes", "Paramount+ $ 7.900/mes"],
        current=Decimal("21900"),
    )
    assert result.status == "value_not_in_quote" and not result.big_change, result
    assert "another price lies between them" in result.reason, result


# Rows as bundles.yaml has them where it lists one tier of a page that shows several: no Google
# AI Plus / Ultra, no Apple One Premium (CO) or Premier (SE), no Meli+ Total in MX or BR, Telia
# and Go3 plans besides the bundle's.
UNLISTED = BundlesFile.model_validate(
    {
        "providers": [
            *PROVIDERS,
            {"id": "go3", "name": "Go3", "domains": {"LT": ["go3.lt"]}},
        ],
        "bundles": [
            {
                "bundle_id": bundle_id,
                "provider_id": provider,
                "provider_type": "platform",
                "country": country,
                "name": name,
                "tier": bundle_id.rsplit("_", 1)[-1],
                "aliases": aliases,
                "official_url": url,
                "price": {"amount": amount, "currency": currency, "tax_included": True, **extra},
                "components": [],
                "last_verified": "2026-10-11",
            }
            for bundle_id, provider, country, name, aliases, amount, currency, url, extra in (
                (
                    "google_ai_pro_co",
                    "google",
                    "CO",
                    "Google AI Pro",
                    ["Google AI Pro"],
                    "79000",
                    "COP",
                    OFFICIAL_URL["google", "CO"],
                    {},
                ),
                (
                    "apple_one_individual_co",
                    "apple",
                    "CO",
                    "Apple One Individual",
                    ["plan Individual", "Individual"],
                    "45900",
                    "COP",
                    OFFICIAL_URL["apple", "CO"],
                    {},
                ),
                (
                    "apple_one_family_co",
                    "apple",
                    "CO",
                    "Apple One Familiar",
                    ["plan Familiar", "Familiar"],
                    "62900",
                    "COP",
                    OFFICIAL_URL["apple", "CO"],
                    {},
                ),
                (
                    "apple_one_individual_se",
                    "apple",
                    "SE",
                    "Apple One Individual",
                    ["Personligt", "personliga abonnemanget"],
                    "215",
                    "SEK",
                    OFFICIAL_URL["apple", "SE"],
                    {},
                ),
                (
                    "apple_one_family_se",
                    "apple",
                    "SE",
                    "Apple One Familj",
                    ["Familj", "familjeabonnemanget"],
                    "295",
                    "SEK",
                    OFFICIAL_URL["apple", "SE"],
                    {},
                ),
                (
                    "meli_plus_mx_mega",
                    "meli",
                    "MX",
                    "Meli+ Mega (con anuncios)",
                    ["Meli+ Mega", "Plan Mega"],
                    "399",
                    "MXN",
                    OFFICIAL_URL["meli", "MX"],
                    {},
                ),
                (
                    "meli_plus_br_mega",
                    "meli",
                    "BR",
                    "Meli+ Mega",
                    ["Meli+ Mega", "Plano Mega"],
                    "74.90",
                    "BRL",
                    OFFICIAL_URL["meli", "BR"],
                    {},
                ),
                (
                    "telia_se_obegransad_streaming",
                    "telia",
                    "SE",
                    "Telia Obegränsad Plus + Netflix, HBO Max y Disney+",
                    ["Obegränsad Plus med Netflix", "HBO Max och Disney+"],
                    "699",
                    "SEK",
                    OFFICIAL_URL["telia", "SE"],
                    {"base_plan_name": "Telia Obegränsad Plus", "base_plan_amount": "569"},
                ),
                (
                    "go3_lt_filmai_netflix",
                    "go3",
                    "LT",
                    "Go3 Filmai + Netflix",
                    [],
                    "17.99",
                    "EUR",
                    "https://go3.lt/",
                    {},
                ),
            )
        ],
    }
)
GOOGLE_CO_PAGE = [
    "Google AI Pro",
    "COP 79,000 por mes",
    "Google AI Ultra",
    "COP 449,000/ mes",
    "COP 449,000/ mes: Límites de uso 5 veces más altos que los de AI Pro",
]
APPLE_CO_PAGE = [
    "Individual",
    "$ 45.900/mes",
    "Familiar",
    "$ 62.900/mes",
    "Premium",
    "$ 86.900/mes",
    "Prueba gratis",
]


@pytest.mark.parametrize(
    ("bundle_id", "kind", "value", "quote", "context"),
    [
        (
            "google_ai_pro_co",
            "money",
            "449000 COP/month",
            "Google AI Ultra\nCOP 449,000/ mes",
            GOOGLE_CO_PAGE,
        ),
        ("google_ai_pro_co", "money", "449000 COP/month", "\n".join(GOOGLE_CO_PAGE[:4]), []),
        ("apple_one_family_co", "money", "86900 COP/month", "Premium\n$ 86.900/mes", APPLE_CO_PAGE),
        (
            "meli_plus_mx_mega",
            "money",
            "149 MXN/month",
            "Plan Mega\n$ 399/mes\nPlan Total\n$ 149/mes",
            [],
        ),
        (
            "meli_plus_br_mega",
            "money",
            "24.90 BRL/month",
            "Plano Mega\nR$ 74,90/mês\nPlano Total\nR$ 24,90/mês",
            [],
        ),
        (
            "apple_one_family_se",
            "money",
            "379 SEK/month",
            "Familj\n295 kr/mån\nPremier\n379 kr/mån",
            [],
        ),
        (
            "apple_one_family_se",
            "money",
            "215 SEK/month",
            "Familj\n295 kr/mån\nIndividuell\n215 kr/mån",
            [],
        ),
        (
            "telia_se_obegransad_streaming",
            "base_money",
            "449 SEK/month",
            "Telia Obegränsad Plus\n569 kr/mån\nTelia Obegränsad\n449 kr/mån",
            [],
        ),
        (
            "go3_lt_filmai_netflix",
            "money",
            "15.99 EUR/month",
            "Go3 Filmai + Netflix\n17,99 € / mėn.\nGo3 Filmai + HBO Max\n15,99 € / mėn.",
            [],
        ),
    ],
)
def test_a_card_does_not_take_the_price_of_a_tier_the_rows_do_not_list(
    bundle_id: str, kind: str, value: str, quote: str, context: list[str]
) -> None:
    """A tier missing from the rows ends no card, but the card's own price before its price
    still keeps that price from the heading."""
    b = next(r for r in UNLISTED.bundles if r.bundle_id == bundle_id)
    result = check_value(
        kind,  # type: ignore[arg-type]
        value,
        quote,
        context_lines=context,
        country=b.country,
        currency=b.price.currency,
        period="month",
        provider=UNLISTED.provider(b.provider_id),
        bundle=b,
        bundles=UNLISTED.bundles,
        fx=FX,
    )
    assert result.status == "value_not_in_quote", result
    assert "another price lies between them" in result.reason, result


# ------------------------------------------------------------- currency and country


def test_a_mexican_dollar_is_never_read_as_cop() -> None:
    quote = "Meli+ Total $ 399/mes"
    # The ISO code next to the "$" wins.
    coded = _check("money", "399 COP/month", "Meli+ Total $ 399 MXN/mes", "meli_co_total")
    assert coded.status == "value_not_in_quote" and "MXN" in coded.reason
    # A page of the provider's Mexican site never confirms a Colombian price.
    page = _check(
        "money",
        "399 COP/month",
        quote,
        "meli_co_total",
        url="https://www.mercadolibre.com.mx/suscripciones/planes",
    )
    assert page.status == "value_not_in_quote" and "CO domain" in page.reason
    assert _ok(
        _check(
            "money",
            "399 COP/month",
            quote,
            "meli_co_total",
            url="https://www.mercadolibre.com.co/suscripciones/planes",
        )
    )
    # And with an FX table 399 COP a month is not a bundle price.
    assert _check("money", "399 COP/month", quote, "meli_co_total", fx=FX).status == "implausible"


@pytest.mark.parametrize(
    "quote",
    [
        "Meli+ Total MXN $ 399/mes",
        "Meli+ Total $ 399 pesos mexicanos al mes",
        "Meli+ Total: 399 pesos mexicanos por mes",
    ],
)
def test_a_code_or_nationality_next_to_the_amount_wins(quote: str) -> None:
    result = _check("money", "399 COP/month", quote, "meli_co_total")
    assert result.status == "value_not_in_quote" and "MXN" in result.reason, result


def test_a_dollar_is_the_bundles_currency_only_where_that_currency_uses_it() -> None:
    assert _ok(_check("money", "399 MXN/month", "Meli+ Mega $ 399/mes", "meli_mx_mega"))
    swedish = _check("money", "399 SEK/month", "Apple One Familj $ 399/mån", "apple_se_family")
    assert swedish.status == "value_not_in_quote" and "unknown currency" in swedish.reason


def test_value_currency_must_be_the_bundles() -> None:
    result = _check("money", "21900 USD/month", "Meli+ Total: $ 21.900/mes", "meli_co_total")
    assert result.status == "value_not_in_quote" and "is not the bundle's COP" in result.reason


def test_a_value_that_is_not_a_finite_amount_is_not_in_the_quote() -> None:
    for value in ("Infinity", Decimal("NaN"), "nan"):
        result = _check("money", value, "Meli+ Total: $ 21.900/mes", "meli_co_total")
        assert result.status == "value_not_in_quote" and "not an amount" in result.reason


def test_a_value_as_decimal_takes_currency_and_period_from_the_arguments() -> None:
    assert _ok(_check("money", Decimal("21900"), "Meli+ Total: $ 21.900/mes", "meli_co_total"))
    assert _ok(_check("money", 21900, "Meli+ Total: COP 21.900 por mes", "meli_co_total"))


@pytest.mark.parametrize(
    ("quote", "context"),
    [
        ("Meli+ Mega $ 19.99 dólares al mes", []),
        ("Meli+ Mega (USD): $ 19.99 al mes", []),
        ("$ 19.99 al mes", ["Precios en dólares (USD)", "Meli+ Mega", "$ 19.99 al mes", ""]),
        ("$ 19.99 al mes", ["Todos los precios en USD", "Meli+ Mega", "$ 19.99 al mes", ""]),
    ],
)
def test_a_dollar_written_as_usd_is_never_the_bundles_pesos(quote: str, context: list[str]) -> None:
    result = _check(
        "money",
        "19.99 MXN/month",
        quote,
        "meli_mx_mega",
        context_lines=context,
        url="https://www.mercadolibre.com.mx/suscripciones/planes",
        fx=FX,
    )
    assert result.status == "value_not_in_quote" and "USD" in result.reason, result


@pytest.mark.parametrize(
    "quote",
    ["Apple One Familj 295 kr/md (danske kroner)", "Apple One Familj 295 kr. DKK pr. måned"],
)
def test_kronor_of_another_country_are_not_sek(quote: str) -> None:
    result = _check("money", "295 SEK/month", quote, "apple_se_family")
    assert result.status == "value_not_in_quote" and "DKK" in result.reason, result


# ------------------------------------------------------------------------ period


@pytest.mark.parametrize(
    ("value", "quote", "ok"),
    [
        ("21900 COP/month", "Meli+ Total $ 21.900 al año", False),
        ("21900 COP/year", "Meli+ Total $ 21.900/mes", False),
        ("21900 COP/year", "Meli+ Total $ 21.900 al año", True),
        # A quote without a period does not contradict the value's.
        ("21900 COP/month", "Meli+ Total: $ 21.900", True),
    ],
)
def test_period_must_match_when_the_quote_states_one(value: str, quote: str, ok: bool) -> None:
    result = _check("money", value, quote, "meli_co_total")
    assert _ok(result) is ok, result
    if not ok:
        assert "per " in result.reason


@pytest.mark.parametrize(
    ("value", "quote", "bundle_id"),
    [
        ("5000 COP/month", "Meli+ Total: $ 5.000 a la semana", "meli_co_total"),
        ("5000 COP/month", "Meli+ Total: $ 5.000/semana", "meli_co_total"),
        ("5000 COP/month", "Meli+ Total: $ 5.000/día", "meli_co_total"),
        ("99000 COP/month", "Meli+ Total semestral $ 99.000", "meli_co_total"),
        ("99000 COP/month", "Meli+ Total $ 99.000/6 meses", "meli_co_total"),
        ("99000 COP/month", "Meli+ Total $ 99.000 cada 6 meses", "meli_co_total"),
        ("99000 COP/month", "Meli+ Total $ 99.000 por semestre", "meli_co_total"),
        ("40000 COP/month", "Meli+ Total $ 40.000 bimestral", "meli_co_total"),
        ("65700 COP/month", "Meli+ Total $ 65.700 al trimestre", "meli_co_total"),
        ("50 SEK/month", "Apple One Familj 50 kr/vecka", "apple_se_family"),
        ("50 SEK/month", "Apple One Familj 50 kr i veckan", "apple_se_family"),
        ("10 SEK/month", "Apple One Familj bara 10 kr per dag", "apple_se_family"),
        ("990 SEK/month", "Apple One Familj 990 kr/halvår", "apple_se_family"),
        ("885 SEK/month", "Apple One Familj 885 kr per kvartal", "apple_se_family"),
    ],
)
def test_a_rate_the_period_markers_miss_is_a_period_mismatch(
    value: str, quote: str, bundle_id: str
) -> None:
    result = _check("money", value, quote, bundle_id)
    assert result.status == "value_not_in_quote" and "per month" in result.reason, result


def test_a_quarterly_rate_confirms_a_quarterly_value() -> None:
    quote = "Apple One Familj 885 kr per kvartal"
    assert _ok(_check("money", "885 SEK/quarter", quote, "apple_se_family", period="quarter"))


@pytest.mark.parametrize(
    ("value", "quote", "context", "bundle_id"),
    [
        ("16583 COP/month", "Meli+ Total $ 16.583/mes facturado anualmente", [], "meli_co_total"),
        ("16583 COP/month", "Meli+ Total $ 16.583/mes en el plan anual", [], "meli_co_total"),
        (
            "16583 COP/month",
            "Meli+ Total plan anual: $ 199.000 (equivale a $ 16.583/mes)",
            [],
            "meli_co_total",
        ),
        (
            "16583 COP/month",
            "Meli+ Total plan mensual $ 21.900 | plan anual 12x de $ 16.583",
            [],
            "meli_co_total",
        ),
        ("16583 COP/month", "Meli+ Total: 12 cuotas de $ 16.583", [], "meli_co_total"),
        (
            "16583 COP/month",
            "$ 16.583/mes",
            ["Meli+ Total anual", "Paga una vez", "$ 16.583/mes", "Suscribirme"],
            "meli_co_total",
        ),
        ("179 SEK/month", "Apple One Familj 179 kr/mån vid årsbetalning", [], "apple_se_family"),
        ("179 SEK/month", "Apple One Familj 179 kr/mån, betalas årsvis", [], "apple_se_family"),
        ("179 SEK/month", "Apple One Familj 179 kr/mo billed annually", [], "apple_se_family"),
    ],
)
def test_the_monthly_equivalent_of_an_annual_plan_is_no_monthly_price(
    value: str, quote: str, context: list[str], bundle_id: str
) -> None:
    result = _check("money", value, quote, bundle_id, context_lines=context)
    assert result.status == "value_not_in_quote" and "annual" in result.reason, result


# --------------------------------------------------------- plausibility, big change


@pytest.mark.parametrize(
    ("value", "quote", "status"),
    [
        ("4999 SEK/month", "Apple One Familj 4 999 kr/mån", "implausible"),  # ~454 EUR
        ("3 SEK/month", "Apple One Familj 3 kr/mån", "implausible"),  # ~0.27 EUR
        ("2990 SEK/year", "Apple One Familj 2 990 kr/år", "ok"),  # ~22.65 EUR a month
        ("3990 SEK/year", "Apple One Familj 3 990 kr/år", "ok"),  # ~30 EUR (362 if not / 12)
        ("3301 SEK/month", "Apple One Familj 3 301 kr/mån", "implausible"),  # ~300.09 EUR
        ("3300 SEK/month", "Apple One Familj 3 300 kr/mån", "ok"),  # 300 EUR
        ("295 SEK/month", "Apple One Familj 295 kr/mån", "ok"),
    ],
)
def test_plausibility_with_an_fx_table(value: str, quote: str, status: str) -> None:
    period = parse_money_code(value)[2]
    result = _check("money", value, quote, "apple_se_family", fx=FX, period=period)
    assert result.status == status, result
    if status == "implausible":
        assert "EUR/month" in result.reason


def test_a_free_intro_is_plausible() -> None:
    result = _check(
        "intro_money",
        "0 SEK/month",
        "Apple One Familj: 0 kr i 1 månad, sedan 295 kr/mån",
        "apple_se_family",
        fx=FX,
    )
    assert _ok(result), result


def test_plausibility_is_skipped_without_fx_and_says_so() -> None:
    result = _check("money", "4999 SEK/month", "Apple One Familj 4 999 kr/mån", "apple_se_family")
    assert _ok(result) and "no FX table" in result.reason
    no_rate = FxTable(as_of=date(2026, 10, 9))
    result = _check(
        "money", "295 SEK/month", "Apple One Familj 295 kr/mån", "apple_se_family", fx=no_rate
    )
    assert _ok(result) and "no FX rate for SEK" in result.reason


@pytest.mark.parametrize(
    ("current", "big"),
    [
        ("24900", False),
        ("9900", True),
        (None, False),
        ("9900 COP/month", True),  # the compact form of research facts
        (Decimal("14700"), False),  # +49 %
        (Decimal("14400"), True),  # +52 %
        ("262800 COP/year", False),  # the same price, per year
    ],
)
def test_a_change_over_half_is_flagged_but_stays_ok(current: Any, big: bool) -> None:
    result = _check(
        "money", "21900 COP/month", "Meli+ Total: $ 21.900/mes", "meli_co_total", current=current
    )
    assert _ok(result) and result.big_change is big, result


def test_a_change_is_measured_on_the_monthly_equivalent() -> None:
    quote = "Apple One Familj 1 290 kr/år"
    yearly = _check(
        "money", "1290 SEK/year", quote, "apple_se_family", current=Decimal("129"), period="month"
    )
    assert _ok(yearly) and not yearly.big_change, yearly  # 107.5 vs 129 a month: -17 %


def test_a_current_value_that_cannot_be_compared_is_said() -> None:
    result = _check(
        "money", "21900 COP/month", "Meli+ Total: $ 21.900/mes", "meli_co_total", current="n/a"
    )
    assert _ok(result) and not result.big_change and "change not measured" in result.reason
    result = _check(
        "money",
        "21900 COP/month",
        "Meli+ Total: $ 21.900/mes",
        "meli_co_total",
        current="20 USD/month",
    )
    assert _ok(result) and "not in COP" in result.reason


@pytest.mark.parametrize(
    "value", ["1e5000", "1E+999999", "9e999999999", "1e-999999999", Decimal("1e5000"), "-5"]
)
def test_extreme_values_are_not_amounts_and_never_raise(value: Any) -> None:
    quote = "Meli+ Total: $ 21.900/mes"
    result = _check("money", value, quote, "meli_co_total", fx=FX)
    assert result.status == "value_not_in_quote"
    for kind, v in (("pct", value), ("int", value), ("component", f"hbo_max:{value}")):
        assert _check(kind, v, "30% OFF en HBO Max").status == "value_not_in_quote"
    current = _check("money", "21900 COP/month", quote, "meli_co_total", current=value)
    assert _ok(current) and not current.big_change and "change not measured" in current.reason


def test_a_page_amount_out_of_bounds_is_no_price() -> None:
    """A 26-digit run before "kr" on a page line (the watcher's hint scan passes it in)."""
    from haggler.money import find_prices

    line = "Telia Play Mer " + "9" * 26 + " kr/mån"
    (money,) = find_prices(line, ["SEK"], words=True)
    result = check_value(
        "money", money.amount, line, country="SE", currency="SEK", period="month", fx=FX
    )
    assert result.status == "value_not_in_quote"
    result = check_value(
        "money", Decimal("269"), line, country="SE", currency="SEK", period="month", fx=FX
    )
    assert result.status == "value_not_in_quote" and "no price" in result.reason


# ------------------------------------------------------------------ quote and context


def test_a_price_in_the_context_but_outside_the_quote_does_not_count() -> None:
    context = ["Meli+ Total", "$ 21.900/mes", "Envío gratis y más beneficios en Mercado Libre"]
    result = _check(
        "money",
        "21900 COP/month",
        "Envío gratis y más beneficios en Mercado Libre",
        "meli_co_total",
        context_lines=context,
    )
    assert result.status == "value_not_in_quote" and "states no price" in result.reason


def test_a_quote_missing_from_its_context_is_checked_alone() -> None:
    result = _check(
        "money",
        "21900 COP/month",
        "Meli+ Total: $ 21.900/mes",
        "meli_co_total",
        context_lines=["otra cosa", "nada que ver"],
    )
    assert _ok(result), result


def test_the_quote_is_found_in_its_context_despite_case_and_spacing() -> None:
    quote = "precio ANTERIOR:   $ 21.900 pesos por mes."
    assert _ok(
        _check("money", "21900 COP/month", quote, "meli_co_total", context_lines=MELI_CONTEXT)
    )


def test_a_long_text_is_checked_fast_or_not_at_all() -> None:
    import time

    from haggler.research.values import MAX_CHECK_CHARS

    text = ("Meli+ Esencial $ 21.900/mes. " * 400)[: MAX_CHECK_CHARS - 10]
    started = time.perf_counter()
    result = _check("money", "21900 COP/month", text, "meli_co_total")
    assert time.perf_counter() - started < 5
    assert result.status == "value_not_in_quote" and "meli_co_esencial" in result.reason
    too_long = _check("money", "21900 COP/month", text * 2, "meli_co_total")
    assert too_long.status == "value_not_in_quote" and "too long" in too_long.reason


# ------------------------------------------------------------------- int and pct


@pytest.mark.parametrize(
    ("kind", "value", "quote", "ok"),
    [
        ("pct", "30", "30% OFF en HBO Max", True),
        ("pct", "30%", "30 % OFF en HBO Max", True),
        ("pct", Decimal("9"), MELI_LINE, True),
        ("pct", "3", "30% OFF en HBO Max", False),  # part of a larger number
        ("pct", "30", "y ViX Premium.30% OFF en HBO Max", True),  # a glued sentence end
        ("pct", "30", "Total: 0.30% OFF", False),  # a decimal
        ("pct", "30", "Total: .30% OFF", False),
        ("int", 2, MELI_LINE, True),
        ("int", 2, "Precio anterior: $ 21.900", False),
        ("int", 6, TELIA_PLAY, True),
        ("int", 6, "El descuento aplica por seis meses", True),
        ("int", 6, "Rabatten gäller i sex månader", True),
        ("int", 12, "Precio especial por doce meses", True),
        ("int", 1, "por un mes", False),  # "un" is also an article
        ("int", 3, "Válido hasta el 31/12/2026", False),
        ("int", 12, "Válido hasta el 31/12/2026", False),  # a date part
        ("int", 12, "Oferta hasta el 12 de diciembre de 2026", False),
        ("int", 6, "Rabatten gäller t.o.m. 2026-06-30", False),
        ("pct", 30, "Llama al 01-8000-30-30", False),  # a phone number
        ("int", 7, "Soporte 24/7", False),
        ("int", 6, "Código 06", False),  # a leading zero
        ("int", 900, "Precio anterior: $ 21.900", False),
        ("int", 11, "Billed once a year", False),
        ("int", 3, "Prueba de 3-meses", True),
        ("int", 30, "30-day free trial", True),
        ("int", "x", "por 2 meses", False),
        ("pct", "nan", "30% OFF", False),
        ("pct", "Infinity", "30% OFF", False),
        ("int", "sNaN", "30% OFF", False),
    ],
)
def test_numbers_are_whole_tokens(kind: str, value: Any, quote: str, ok: bool) -> None:
    assert _ok(_check(kind, value, quote)) is ok


@pytest.mark.parametrize(
    ("kind", "value", "quote", "ok"),
    [
        # The real Meli+ line: 9 is the percentage, 2 the months.
        ("int", 9, MELI_LINE, False),
        ("months", 9, MELI_LINE, False),
        ("pct", 9, MELI_LINE, True),
        ("months", 2, MELI_LINE, True),
        ("pct", 2, MELI_LINE, False),
        ("months", 6, TELIA_PLAY, True),  # "6 månaderIngen"
        ("months", 6, "Rabatten gäller i sex månader", True),
        ("months", 2, "Meli+ Total: $ 19.900 los 2 primeros meses", True),
        ("months", 3, "Prueba de 3-meses", True),
        ("months", 10, "Desconto por dez meses", True),
        ("months", 12, "Meses: 12", True),
        ("months", 4, "Netflix Premium 4K HDR por 2 meses", False),
        ("int", 30, MELI_COMPONENTS, False),  # a percentage
        ("int", 58, "Spara 58 kr/mån jämfört med att köpa tjänsterna separat", False),  # money
        ("int", 10, "Promoção válida até dez/2026", False),  # December
        ("int", 10, "Oferta válida até Dez. 2026 para novos assinantes", False),
        ("int", 12, "Válido hasta las 12:00", False),
        ("int", 4, "Netflix Premium 4K HDR por 2 meses", False),
        ("int", 6, "6% de descuento por 2 meses", False),
        ("int", 5, "Hasta 5 perfiles", True),
        ("pct", 30, "30 por ciento de descuento", True),
        ("pct", 30, "30 procent rabatt", True),
    ],
)
def test_numbers_carry_their_unit(kind: str, value: Any, quote: str, ok: bool) -> None:
    result = _check(kind, value, quote)
    assert _ok(result) is ok, result


# ------------------------------------------------------------------------- dates


@pytest.mark.parametrize(
    ("value", "quote", "ok"),
    [
        ("2026-12-31", "Oferta válida hasta el 31 de diciembre de 2026", True),
        (date(2026, 12, 31), "Oferta válida até 31 de dezembro de 2026", True),
        ("2026-04-01", "Erbjudandet gäller fr.o.m. 1 april 2026", True),
        ("2026-12-31", "Offer valid until December 31, 2026", True),
        ("2026-12-31", "Hasta el 31/12/2026", True),
        ("2026-12-31", "Oferta válida hasta el 31 de diciembre", False),  # no year
        ("2026-12-30", "Oferta válida hasta el 31 de diciembre de 2026", False),
        ("31/12/2026", "Hasta el 31/12/2026", False),  # the value is ISO
    ],
)
def test_dates_need_the_year(value: Any, quote: str, ok: bool) -> None:
    result = _check("date", value, quote)
    assert _ok(result) is ok, result


@pytest.mark.parametrize(
    ("kind", "value", "quote", "ok"),
    [
        ("date_until", "2026-11-01", "Nuevos precios a partir del 1 de noviembre de 2026", False),
        ("date_until", "2026-04-01", "Priset gäller fr.o.m. 1 april 2026", False),
        ("date_until", "2026-12-31", "Oferta válida hasta el 31 de diciembre de 2026", True),
        ("date_until", "2027-03-31", "Rabatten gäller t.o.m. 31 mars 2027", True),
        ("date_until", "2026-12-31", "Precio especial 31/12/2026", True),  # says neither
        ("date_from", "2026-04-01", "Priset gäller fr.o.m. 1 april 2026", True),
        ("date_from", "2026-11-01", "Nuevos precios a partir del 1 de noviembre de 2026", True),
        ("date_from", "2026-12-31", "Oferta válida hasta el 31 de diciembre de 2026", False),
        ("date", "2026-11-01", "Nuevos precios a partir del 1 de noviembre de 2026", True),
        ("date_until", "2027-01-01", "Hasta el 1ro. de enero de 2027", True),
    ],
)
def test_dates_with_a_direction(kind: str, value: str, quote: str, ok: bool) -> None:
    result = _check(kind, value, quote)
    assert _ok(result) is ok, result


# -------------------------------------------------------------------- components


@pytest.mark.parametrize(
    ("value", "quote", "ok"),
    [
        ("hbo_max:30", MELI_COMPONENTS, True),
        ("paramount_plus:30", MELI_COMPONENTS, True),
        ("vix:30", MELI_COMPONENTS, True),
        # The real planes page prints the line twice, glued: "...ViX Premium.30% OFF en ...".
        ("vix:30", MELI_COMPONENTS + MELI_COMPONENTS, True),
        ("hbo_max:30", MELI_COMPONENTS + MELI_COMPONENTS, True),
        ("paramount_plus:30", MELI_COMPONENTS + MELI_COMPONENTS, True),
        ("vix:3", MELI_COMPONENTS + MELI_COMPONENTS, False),
        ("hbo_max:20", MELI_COMPONENTS, False),  # the discount's number
        ("netflix", MELI_COMPONENTS, False),
        ("disney_plus.standard_with_ads", "Disney+ Estándar con anuncios.", True),
        ("DISNEY_PLUS", "Disney+ Estándar con anuncios.", False),  # ids are exact
        ("disney_plus", "DISNEY+ ESTANDAR CON ANUNCIOS", True),  # case
        ("disney_plus.standard", "DISNEY+ ESTANDAR CON ANUNCIOS", False),  # with ads
        ("disney_plus.standard_with_ads", "Disney+ PADRAO", False),  # accents: "padrão"
        ("hbo_max", "Incluye SuperMax Básico", False),  # a whole word on the left too
        ("disney_plus:20", "20% OFF en Disney+ y 30% OFF en HBO Max", True),
        ("disney_plus:30", "20% OFF en Disney+ y 30% OFF en HBO Max", False),  # HBO Max's
        ("hbo_max:20", "20% OFF en Disney+ y 30% OFF en HBO Max", False),
        ("hbo_max:30", "Disney+ con 20% OFF y HBO Max con 30% OFF", True),
        ("hbo_max:20", "Disney+ con 20% OFF y HBO Max con 30% OFF", False),
        ("disney_plus:30", "Disney+ incluido, 30% OFF en HBO Max", False),
        ("hbo_max:3", "3 meses gratis de HBO Max", True),
        ("netflix.standard", TELIA_COMPONENTS, True),
        ("disney_plus.standard_with_ads", TELIA_COMPONENTS, True),
        ("disney_plus.standard", TELIA_COMPONENTS, False),  # names another plan
        ("hbo_max.basic_with_ads", TELIA_COMPONENTS, True),  # the plan is not named: passes
        ("hbo_max", "Telia Play Maxad 319 kr/mån", False),  # "Max" is a whole word
        ("hbo_max", "Incluye Max Básico con anuncios", True),  # an alias
        ("nope", MELI_COMPONENTS, False),
        ("netflix.nope", TELIA_COMPONENTS, False),
    ],
)
def test_components(value: str, quote: str, ok: bool, cat) -> None:
    result = _check("component", value, quote, catalog=cat)
    assert _ok(result) is ok, result


def test_component_uses_the_default_catalog_when_none_is_given() -> None:
    assert _ok(_check("component", "hbo_max:30", MELI_COMPONENTS))


@pytest.mark.parametrize(
    ("value", "quote", "bundle_id", "why"),
    [
        # A discount is no inclusion (the real Meli+ CO and MX lines).
        ("hbo_max", MELI_COMPONENTS, None, "discount"),
        ("vix", MELI_COMPONENTS, None, "discount"),
        (
            "apple_tv.standard",
            "30% OFF en HBO Max Estándar, Apple TV, Universal+, Paramount+ y ViX Premium.",
            None,
            "discount",
        ),
        ("hbo_max", "3 meses gratis de HBO Max", None, "free months"),
        # Another tier's line (the real Meli+ Esencial description).
        ("disney_plus.standard_with_ads", MELI_ESENCIAL_PAGE, "meli_co_esencial", "Meli+ Total"),
        (
            "disney_plus",
            "Meli+ Total incluye Disney+. Meli+ Esencial incluye envíos gratis.",
            "meli_co_esencial",
            "Meli+ Total",
        ),
        # Negations.
        ("disney_plus", "Disney+ no está incluido en Meli+ Esencial", None, "not included"),
        ("disney_plus", "Disney+ ingår inte i abonnemanget", None, "not included"),
        ("disney_plus", "Disney+ is not included in this plan", None, "not included"),
        ("disney_plus", "Meli+ Esencial: sin Disney+", None, "not included"),
        ("netflix", "Sin Netflix", None, "not included"),
        ("netflix", "Netflix not included", None, "not included"),
        ("netflix", "No incluye: Disney+, Netflix y HBO Max", None, "not included"),
        ("netflix", "Netflix y Disney+ no están incluidos", None, "not included"),
        # The real Esencial card: the struck item and its "No incluido" label in one line.
        (
            "disney_plus.standard_with_ads",
            "No incluido: Disney+ Estándar con anuncios.",
            None,
            "not",
        ),
        (
            "disney_plus.standard_with_ads",
            "Disney+ Estándar con anuncios.No incluido: Disney+ Estándar con anuncios.",
            None,
            "not included",
        ),
        ("hbo_max:30", MELI_COMPONENTS + "No incluido: " + MELI_COMPONENTS, None, "not included"),
    ],
)
def test_a_component_line_that_excludes_discounts_or_names_another_tier_fails(
    value: str, quote: str, bundle_id: str | None, why: str, cat
) -> None:
    result = _check("component", value, quote, bundle_id, catalog=cat)
    assert result.status == "value_not_in_quote" and why in result.reason, result


@pytest.mark.parametrize(
    ("value", "quote", "bundle_id"),
    [
        ("disney_plus.standard_with_ads", MELI_ESENCIAL_PAGE, "meli_co_total"),
        ("disney_plus.standard_with_ads", "Disney+ Estándar con anuncios.", "meli_co_esencial"),
        ("netflix", MELI_MEGA_COMPONENTS, "meli_mx_mega"),
        ("apple_tv", MELI_MEGA_COMPONENTS, "meli_mx_mega"),
        ("hbo_max", MELI_MEGA_COMPONENTS, "meli_mx_mega"),
        ("hbo_max", "Obegränsad Plus med Netflix, Max och Disney+", "telia_se_plus"),
        ("netflix.standard", "Telco Plus med Netflix Standard 699 kr/mån", None),
        ("disney_plus", "Netflix Estándar sin anuncios, Disney+ y HBO Max", None),
        ("hbo_max", "HBO Max, no incluye Netflix", None),  # the negation is Netflix's
    ],
)
def test_real_inclusion_lines_still_confirm(
    value: str, quote: str, bundle_id: str | None, cat
) -> None:
    result = _check("component", value, quote, bundle_id, catalog=cat)
    assert _ok(result), result


@pytest.mark.parametrize(
    ("value", "quote", "ok"),
    [
        ("hbo_max.premium", TELIA_COMPONENTS, False),  # "Basic med reklam"
        ("hbo_max.standard", TELIA_COMPONENTS, False),
        ("netflix.standard", "Netflix Standard med reklam, HBO Max Basic med reklam", False),
        (
            "netflix.standard_with_ads",
            "Netflix Standard med reklam, HBO Max Basic med reklam",
            True,
        ),
        ("netflix.standard", "Netflix Estándar con anuncios incluido", False),
        ("hbo_max.premium", "Incluye el plan Básico con anuncios de HBO Max", False),
        ("hbo_max.basic_with_ads", "Incluye el plan Básico con anuncios de HBO Max", True),
        ("disney_plus.standard", "Incluye Estándar con anuncios de Disney+", False),
        ("hbo_max.premium", "HBO Max con anuncios", False),  # an ads marker alone
        ("hbo_max.basic_with_ads", "HBO Max con anuncios", True),
        ("paramount_plus.standard", "Paramount+ Básico", False),  # a plan it does not have
    ],
)
def test_component_plans(value: str, quote: str, ok: bool, cat) -> None:
    result = _check("component", value, quote, catalog=cat)
    assert _ok(result) is ok, result


@pytest.mark.parametrize(
    ("value", "quote", "ok"),
    [
        (
            "hbo_max:30",
            "30% OFF en Disney+. HBO Max incluido sin costo.",
            False,
        ),  # another sentence
        ("hbo_max:30", "Disney+ con 30% OFF, además HBO Max", False),  # Disney+'s number
        ("paramount_plus:30", MELI_COMPONENTS, True),  # bare list items
        ("hbo_max:30", "HBO Max, con 30% OFF", True),
        # "Max" is a common word: as written only, and never before a number.
        ("hbo_max", "Dela abonnemanget med max 5 personer i din familj", False),
        ("hbo_max", "Netflix Premium: máx. 4 pantallas a la vez", False),
        ("hbo_max:30", "30% OFF en Disney+, máx. 2 cuentas", False),
        ("hbo_max", "Telia Obegränsad Max 50 GB", False),
    ],
)
def test_component_numbers_and_short_aliases(value: str, quote: str, ok: bool, cat) -> None:
    result = _check("component", value, quote, catalog=cat)
    assert _ok(result) is ok, result


@pytest.mark.parametrize(
    ("value", "quote", "ok"),
    [
        ("hbo_max:30", MELI_COMPONENTS, True),
        ("vix:30", MELI_COMPONENTS, True),
        # The real planes page prints the line twice, glued: "...ViX Premium.30% OFF en ...".
        ("vix:30", MELI_COMPONENTS + MELI_COMPONENTS, True),
        ("hbo_max:30", MELI_COMPONENTS + MELI_COMPONENTS, True),
        ("paramount_plus:30", MELI_COMPONENTS + MELI_COMPONENTS, True),
        ("vix:3", MELI_COMPONENTS + MELI_COMPONENTS, False),
        ("hbo_max:30", "30 por ciento de descuento en HBO Max", True),
        ("disney_plus:20", "20% OFF en Disney+ y 30% OFF en HBO Max", True),
        ("hbo_max:20", MELI_COMPONENTS, False),
        ("hbo_max:3", "3 meses gratis de HBO Max", False),  # free months
        ("hbo_max:2", "Incluye HBO Max para 2 dispositivos a la vez.", False),  # a count
        ("hbo_max:5", "Ahorra US$ 5 en HBO Max cada mes.", False),  # money
        ("hbo_max", MELI_COMPONENTS, False),  # a discount needs its percentage
    ],
)
def test_discount_pct_is_a_component_whose_number_is_a_percentage(
    value: str, quote: str, ok: bool, cat
) -> None:
    result = _check("discount_pct", value, quote, catalog=cat)
    assert _ok(result) is ok, result


# --------------------------------------------------------------------------- urls


@pytest.mark.parametrize(
    ("url", "ok"),
    [
        ("https://www.mercadolibre.com.co/l/precios.pdf", True),
        ("https://www.mercadolibre.com.mx/l/precios.pdf", False),
        ("http://www.mercadolibre.com.co/l/precios.pdf", False),
    ],
)
def test_url_is_on_the_providers_domains_for_the_country(url: str, ok: bool) -> None:
    assert _ok(_check("url", url, "", "meli_co_total")) is ok


def test_url_without_a_provider_is_not_confirmed() -> None:
    result = _check("url", "https://www.mercadolibre.com.co/x", "")
    assert result.status == "value_not_in_quote" and "no provider" in result.reason


def test_unknown_kind_is_a_programming_error() -> None:
    with pytest.raises(ValueError, match="unknown value kind"):
        _check("bool", "true", "sí")


# ------------------------------------------------------------- money code helpers


@pytest.mark.parametrize(
    ("text", "parsed"),
    [
        ("21900 COP/month", (Decimal("21900"), "COP", "month")),
        ("74.90 BRL/month", (Decimal("74.90"), "BRL", "month")),
        ("1290 SEK/year", (Decimal("1290"), "SEK", "year")),
        ("21900 COP", (Decimal("21900"), "COP", None)),
        ("21.900 COP/month", (Decimal("21.900"), "COP", "month")),
        ("74,90 BRL/month", None),
        ("21900 cop/month", None),
        ("21900 COP/week", None),
        ("$ 21.900", None),
    ],
)
def test_parse_money_code(text: str, parsed: tuple | None) -> None:
    assert parse_money_code(text) == parsed


def test_format_money_code_round_trips() -> None:
    assert format_money_code(Decimal("21900.00"), "COP", "month") == "21900 COP/month"
    assert format_money_code(Decimal("74.90"), "BRL") == "74.90 BRL"
    assert parse_money_code(format_money_code(Decimal("4.5"), "EUR", "month")) == (
        Decimal("4.5"),
        "EUR",
        "month",
    )

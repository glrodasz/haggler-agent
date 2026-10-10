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
TELIA_PLAY = "Ordinarie pris: 269 kr/mån.Pris: 199 kr/mån.Rabatten gäller i 6 månader"
MELI_COMPONENTS = "30% OFF en HBO Max, Universal+, Paramount+ y ViX Premium."
TELIA_COMPONENTS = "Netflix Standard, HBO Max Basic med reklam, Disney+ Standard med reklam"


PROVIDERS = [
    {
        "id": "meli",
        "name": "Mercado Libre",
        "domains": {"CO": ["mercadolibre.com.co"], "MX": ["mercadolibre.com.mx"]},
    },
    {
        "id": "apple",
        "name": "Apple",
        "domains": {"SE": ["www.apple.com/se/"], "CO": ["www.apple.com/co/"]},
    },
]
# (bundle_id, provider, country, name, aliases, amount, currency): two Meli+ tiers in CO (one in
# MX), Apple One Individual / Familj in SE and three Apple One tiers in CO.
TIERS = [
    ("meli_co_total", "meli", "CO", "Meli+ Total", ["Meli+ Total"], "21900", "COP"),
    ("meli_co_esencial", "meli", "CO", "Meli+ Esencial", ["Meli+ Esencial"], "9900", "COP"),
    ("meli_mx_mega", "meli", "MX", "Meli+ Mega", ["Meli+ Mega"], "399", "MXN"),
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
OFFICIAL_URL = {
    ("meli", "CO"): "https://www.mercadolibre.com.co/suscripciones/planes",
    ("meli", "MX"): "https://www.mercadolibre.com.mx/suscripciones/planes",
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
                "price": {"amount": amount, "currency": currency, "tax_included": True},
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
    assert _ok(_check("money", "269 SEK/month", TELIA_PLAY))
    assert _ok(_check("intro_money", "199 SEK/month", TELIA_PLAY))
    assert not _ok(_check("money", "199 SEK/month", TELIA_PLAY))
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
    assert _ok(_check("money", "269 SEK/month", quote))


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


def test_a_single_tier_needs_no_name() -> None:
    """Meli+ Mega is the only MX bundle of the provider."""
    assert _ok(_check("money", "399 MXN/month", "$ 399/mes", "meli_mx_mega"))


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
    assert _ok(_check("money", "399 MXN/month", "$ 399/mes", "meli_mx_mega"))
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


# ------------------------------------------------------------------- int and pct


@pytest.mark.parametrize(
    ("kind", "value", "quote", "ok"),
    [
        ("pct", "30", "30% OFF en HBO Max", True),
        ("pct", "30%", "30 % OFF en HBO Max", True),
        ("pct", Decimal("9"), MELI_LINE, True),
        ("pct", "3", "30% OFF en HBO Max", False),  # part of a larger number
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


# -------------------------------------------------------------------- components


@pytest.mark.parametrize(
    ("value", "quote", "ok"),
    [
        ("hbo_max:30", MELI_COMPONENTS, True),
        ("paramount_plus:30", MELI_COMPONENTS, True),
        ("vix:30", MELI_COMPONENTS, True),
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

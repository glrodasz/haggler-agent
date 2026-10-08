import re
from decimal import Decimal
from pathlib import Path

import pytest

from haggler.fetch import RawResponse
from haggler.sources.spotify import (
    SpotifyPremiumAdapter,
    clean_price_lines,
    is_intro,
    json_documents,
    json_plan_hits,
    page_lines,
    pick_hits,
)

FIX = Path(__file__).parent / "fixtures" / "spotify_premium"
ADAPTER = SpotifyPremiumAdapter()


def _task(catalog, cc):
    svc = catalog.service("spotify")
    src = next(s for s in svc.sources if s.adapter == ADAPTER.id)
    return next(t for t in ADAPTER.plan(svc, src, catalog) if t.countries == [cc])


def _parse(catalog, cc, html, *, final_url=None, status=200):
    task = _task(catalog, cc)
    resp = RawResponse(url=task.url, final_url=final_url or task.url, status_code=status, text=html)
    return ADAPTER.parse(task, resp, catalog)


def _prices(result):
    return {o.plan_id: (o.amount, o.currency) for o in result.observations}


def _fixture(name):
    return (FIX / name).read_text(encoding="utf-8")


def _cards(*cards: tuple[str, list[str]]) -> str:
    body = "".join(
        f'<div class="PlanCard"><span>Premium</span><h3>{name}</h3>'
        + "".join(f"<p>{p}</p>" for p in prices)
        + "<ul><li>Cancel anytime</li></ul></div>"
        for name, prices in cards
    )
    return f"<html><body><main><section id='plans'>{body}</section></main></body></html>"


SE = {
    "individual": (Decimal("129"), "SEK"),
    "duo": (Decimal("169"), "SEK"),
    "family": (Decimal("209"), "SEK"),
    "student": (Decimal("65"), "SEK"),
}


def test_plan_urls(catalog):
    task = _task(catalog, "SE")
    assert task.url == "https://www.spotify.com/se/premium/"
    assert task.host == "www.spotify.com"
    assert _task(catalog, "CO").url == "https://www.spotify.com/co/premium/"
    # Spotify's UK market is /uk/ (params.path_overrides {GB: uk}).
    assert _task(catalog, "GB").url == "https://www.spotify.com/uk/premium/"


@pytest.mark.parametrize(
    ("final_url", "status"),
    [
        ("https://www.spotify.com/uk/premium/", "ok"),
        ("https://www.spotify.com/uk-en/premium/", "ok"),
        ("https://www.spotify.com/gb/premium/", "ok"),
        ("https://www.spotify.com/us/premium/", "wrong_country"),
    ],
)
def test_uk_market_segment_is_not_wrong_country(catalog, final_url, status):
    html = _cards(("Individual", ["£11.99/month"]))
    result = _parse(catalog, "GB", html, final_url=final_url)
    assert result.status == status
    if status == "ok":
        assert _prices(result) == {"individual": (Decimal("11.99"), "GBP")}
    else:
        assert "/uk/" in result.notes[0]


def test_localized_plan_names_come_from_the_catalog(catalog):
    # Formerly adapter-local EXTRA_ALIASES; now catalog aliases (Spanish, Polish, Turkish...).
    svc = catalog.service("spotify")
    aliases = {p.id: set(p.aliases) for p in svc.plans}
    assert {"familiar", "rodzinny", "keluarga"} <= aliases["family"]
    assert {"estudiante", "studencki", "mahasiswa"} <= aliases["student"]
    assert {"bireysel", "individuell"} <= aliases["individual"]
    html = _cards(("Premium Rodzinny", ["37,99 zł/mies."]), ("Premium Indywidualny", ["23,99 zł"]))
    assert _prices(_parse(catalog, "PL", html)) == {
        "family": (Decimal("37.99"), "PLN"),
        "individual": (Decimal("23.99"), "PLN"),
    }


def test_sweden_from_embedded_json(catalog):
    result = _parse(catalog, "SE", _fixture("se.html"))
    assert result.status == "ok"
    assert _prices(result) == SE
    assert result.notes[0] == "strategy=json"
    ind = next(o for o in result.observations if o.plan_id == "individual")
    assert "129 kr" in ind.evidence_snippet
    assert result.missing_plans == []


def test_sweden_cards_without_json_agree(catalog):
    html = re.sub(r'<script id="__NEXT_DATA__".*?</script>', "", _fixture("se.html"), flags=re.S)
    result = _parse(catalog, "SE", html)
    assert result.notes[0] == "strategy=cards"
    assert _prices(result) == SE  # the "0 kr i 1 månad" intro is never the price


def test_colombia_cards_with_spanish_labels_and_intro_offers(catalog):
    result = _parse(catalog, "CO", _fixture("co.html"))
    assert result.status == "ok"
    assert _prices(result) == {
        "individual": (Decimal("18500"), "COP"),
        "duo": (Decimal("24900"), "COP"),
        "family": (Decimal("30900"), "COP"),  # "Premium Familiar"
        "student": (Decimal("9900"), "COP"),  # after "3 meses por COP 9.900"
    }
    ind = next(o for o in result.observations if o.plan_id == "individual")
    assert "COP 18.500/mes" in ind.evidence_snippet


@pytest.mark.parametrize(
    ("final_url", "status"),
    [
        ("https://www.spotify.com/us/premium/", "wrong_country"),
        ("https://www.spotify.com/int/premium/", "wrong_country"),
        ("https://www.spotify.com/se-en/premium/", "ok"),
        ("https://www.spotify.com/se/premium/?ref=nav", "ok"),
    ],
)
def test_final_url_market(catalog, final_url, status):
    assert _parse(catalog, "SE", _fixture("se.html"), final_url=final_url).status == status


def test_prices_in_another_currency(catalog):
    html = _cards(
        ("Individual", ["US$0 for 1 month", "US$11.99/month after"]),
        ("Duo", ["US$16.99/month"]),
    )
    result = _parse(catalog, "CO", html)
    assert result.status == "wrong_country"
    assert result.observations == []


def test_dollar_cents_on_a_peso_page_is_not_cop(catalog):
    # "$11.99" resolves to COP on a CO page; zero-decimal COP with cents means a USD fallback.
    result = _parse(catalog, "CO", _cards(("Individual", ["$11.99/month"])))
    assert result.status == "wrong_country"


def test_blocked(catalog):
    result = _parse(catalog, "SE", _fixture("blocked.html"))
    assert result.status == "blocked"


def test_http_404_is_not_available(catalog):
    assert _parse(catalog, "PK", "<html><body>Not found</body></html>", status=404).status == (
        "not_available"
    )


def test_other_tiers_are_ignored(catalog):
    html = _cards(
        ("Lite", ["₹139/month"]),
        ("Standard", ["₹199/month"]),
        ("Platinum", ["₹299/month"]),
        ("Student", ["₹99/month"]),
    )
    result = _parse(catalog, "IN", html)
    assert result.status == "partial"
    assert _prices(result) == {"student": (Decimal("99"), "INR")}
    assert "individual" in result.missing_plans


def test_basic_individual_is_not_premium_individual(catalog):
    html = _cards(
        ("Basic Individual", ["$10.99/month"]),
        ("Individual", ["$0 for 1 month", "$11.99/month after"]),
    )
    assert _prices(_parse(catalog, "US", html)) == {"individual": (Decimal("11.99"), "USD")}


def test_non_zero_intro_and_yearly_prices(catalog):
    html = _cards(
        ("Individual", ["3 meses por 5,49 €", "Después, 10,99 €/mes"]),
        ("Familiar", ["179,88 €/año", "17,99 €/mes"]),
    )
    assert _prices(_parse(catalog, "ES", html)) == {
        "individual": (Decimal("10.99"), "EUR"),
        "family": (Decimal("17.99"), "EUR"),
    }


def test_no_prices(catalog):
    result = _parse(catalog, "SE", "<html><body><h1>Spotify Premium</h1></body></html>")
    assert result.status == "no_prices"


@pytest.mark.parametrize(
    ("before", "after", "intro"),
    [
        ("", " i 1 månad", True),
        ("Testa 3 månader för ", "", True),
        ("", " for 3 months", True),
        ("", "/mån i 3 månader", True),
        ("Try 1 month of Premium for ", "", True),
        ("", "/månad efteråt", False),
        ("Endast ", "/månad efter perioden.", False),
        ("Después, ", "/mes", False),
        ("", " i månaden", False),
    ],
)
def test_is_intro(before, after, intro):
    assert is_intro(before, after) is intro


def test_clean_price_lines_drops_yearly_with_its_marker():
    lines = ["109 kr/mån eller 1 090 kr/år", "0 kr i 1 månad", "1 390 kr", "/år"]
    cleaned = clean_price_lines(lines, ["SEK"])
    assert "1 090" not in cleaned[0] and "/år" not in cleaned[0] and "109 kr/mån" in cleaned[0]
    assert "0 kr" not in cleaned[1]
    assert "1 390" not in cleaned[2]


def test_page_lines_join_inline_text_and_skip_chrome():
    html = (
        "<html><body><header><a>Premium Family</a></header><main>"
        "<div><span>Premium</span><span>Duo</span></div>"
        "<p><b>Basic</b>: 129 kr<span>/mån</span></p>"
        "<script>var x = 1;</script></main><footer>Premium Student</footer></body></html>"
    )
    assert page_lines(html) == ["Premium Duo", "Basic: 129 kr/mån"]


def test_json_documents_reads_state_assignments():
    html = (
        '<script>window.__STATE__ = {"plans": [{"name": "Duo"}]};</script>'
        '<script type="application/ld+json">{"@type": "Product"}</script>'
        "<script>console.log(1)</script>"
    )
    docs = dict(json_documents(html))
    assert docs["window.__STATE__"] == {"plans": [{"name": "Duo"}]}
    assert docs["application/ld+json"] == {"@type": "Product"}


def test_json_plan_hits_shapes(catalog):
    svc = catalog.service("spotify")
    docs = [
        (
            "ld",
            {
                "@type": "Product",
                "offers": [
                    {
                        "@type": "Offer",
                        "name": "Premium Duo",
                        "price": "169.00",
                        "priceCurrency": "SEK",
                    }
                ],
            },
        ),
        (
            "state",
            {
                "plans": [
                    {
                        "planId": "premium-family",
                        "introPrice": {"amount": 0, "currency": "SEK"},
                        "price": {"amountInMinorUnits": 20900, "currency": "SEK", "period": "P1M"},
                    },
                    {
                        "title": "Premium Individual",
                        "prices": ["1 290 kr/år", "129 kr/mån"],
                        "upsell": {"title": "Premium Duo", "price": "99 kr/mån"},
                    },
                    {"title": "Premium Student", "trialPrice": "32 kr/mån", "price": "65 kr/mån"},
                ]
            },
        ),
    ]
    hits = json_plan_hits(docs, svc, ["SEK"], id_matcher=lambda v: v.split("-")[-1])
    best, foreign, _ = pick_hits(hits, ["SEK"])
    assert {k: v.amount for k, v in best.items()} == {
        "duo": Decimal("169.00"),  # the nested "upsell" Duo object is not Individual's price
        "family": Decimal("209"),
        "individual": Decimal("129"),
        "student": Decimal("65"),
    }
    assert foreign == []

"""The bundle watcher (haggler.bundles.watch): anchors, pages, versioned PDFs, hints and pauses,
offline over canned pages (HTML strings and PDFs built in the test)."""

from __future__ import annotations

import hashlib
import logging
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from typing import Any

import pytest
import yaml

from conftest import FixtureFetcher
from haggler.bundles.status import bundle_state
from haggler.bundles.watch import (
    PAUSE_DAYS,
    edition_key,
    page_links,
    pages_over_budget,
    summary,
    watch_bundles,
)
from haggler.catalog import load_catalog, load_feasibility, load_payment_instruments
from haggler.fetch import RawResponse
from haggler.fetch.errors import BlockedError, FetchError, RobotsDisallowedError
from haggler.models.bundles import WatchFile, anchor_hash
from haggler.models.curated import Anchor, BundlesFile
from haggler.models.fx import FxRate, FxTable
from haggler.research.evidence import PageCache
from haggler.text import normalize_for_match
from pdf_support import build_pdf

TODAY = date(2026, 10, 12)
CATALOG = load_catalog()

ACME = "https://www.acme.com.co/planes"
PRICE = (
    "Precio actual con 9 por ciento de descuento: 19900 pesos por mes, por 2 meses. "
    "Precio anterior: $ 21.900 pesos por mes."
)
DISNEY = "Disney+ Estándar con anuncios."
HBO = "30% OFF en HBO Max, Universal+, Paramount+ y ViX Premium."

INDEX = "https://www.telco.se/om/priser"
PDF18 = "https://www.telco.se/priser/TSP-4667_18_0701_Prislista.pdf"
PDF19 = "https://www.telco.se/priser/TSP-4667_19_0102_Prislista.pdf"
PATTERN = r"TSP-4667_(?P<edition>\d+)_\d{4}_Prislista\.pdf"
TELCO_PRICE = "Telco Plus med Netflix Standard 699 kr/mån"
VALIDITY = "Prislistan gäller från och med den 1 juli 2026."


def providers(**access: str) -> list[dict[str, Any]]:
    return [
        {
            "id": "acme",
            "name": "Acme",
            "domains": {"CO": ["acme.com.co"]},
            "automated_access": access.get("acme", "allowed"),
        },
        {
            "id": "telco",
            "name": "Telco",
            "domains": {"SE": ["telco.se"]},
            "automated_access": access.get("telco", "allowed"),
        },
    ]


def acme(**overrides: Any) -> dict[str, Any]:
    data: dict[str, Any] = {
        "bundle_id": "acme_total",
        "provider_id": "acme",
        "provider_type": "marketplace",
        "country": "CO",
        "name": "Acme Total",
        "tier": "total",
        "aliases": ["Acme Total"],
        "official_url": ACME,
        "price": {
            "amount": "21900",
            "currency": "COP",
            "intro_amount": "19900",
            "intro_months": 2,
        },
        "components": [
            {"service_id": "disney_plus", "plan_id": "standard_with_ads", "inclusion": "included"},
            {"service_id": "hbo_max", "inclusion": "discount_pct", "value": "30"},
        ],
        "watch": {
            "mode": "auto",
            "pages": [{"url": ACME}],
            "anchors": [
                {"fact": "price", "url": ACME, "text": PRICE},
                {"fact": "intro", "url": ACME, "text": PRICE},
                {"fact": "component", "service_id": "disney_plus", "url": ACME, "text": DISNEY},
                {"fact": "component", "service_id": "hbo_max", "url": ACME, "text": HBO},
            ],
        },
        "last_verified": "2026-10-01",
    }
    data.update(overrides)
    return data


def telco(*, validity: bool = False, **overrides: Any) -> dict[str, Any]:
    anchors = [
        {"fact": "price", "url": PDF18, "text": TELCO_PRICE},
        {"fact": "component", "service_id": "netflix", "url": PDF18, "text": TELCO_PRICE},
    ]
    if validity:
        anchors.append({"fact": "validity", "url": PDF18, "text": VALIDITY})
    data: dict[str, Any] = {
        "bundle_id": "telco_plus",
        "provider_id": "telco",
        "provider_type": "telco_mobile",
        "country": "SE",
        "name": "Telco Plus",
        "tier": "plus",
        "official_url": "https://www.telco.se/mobil",
        "price": {"amount": "699", "currency": "SEK"},
        "components": [{"service_id": "netflix", "plan_id": "standard", "inclusion": "included"}],
        "valid_from": "2026-07-01",
        "watch": {
            "mode": "auto",
            "pages": [{"url": PDF18, "kind": "pdf", "index_url": INDEX, "link_pattern": PATTERN}],
            "anchors": anchors,
        },
        "last_verified": "2026-10-01",
    }
    data.update(overrides)
    return data


def bundles(*rows: dict[str, Any], **access: str) -> BundlesFile:
    return BundlesFile.model_validate({"providers": providers(**access), "bundles": list(rows)})


def acme_page(price: str = PRICE, *, extra: str = "") -> str:
    return (
        f"<html><body><main><h2>Acme Total</h2><p>{price}</p>"
        f"<ul><li>{DISNEY}</li><li>{HBO}</li></ul>{extra}</main></body></html>"
    )


def html(url: str, text: str, *, final_url: str | None = None) -> RawResponse:
    return RawResponse(url=url, final_url=final_url or url, status_code=200, text=text)


def pdf(url: str, lines: list[str]) -> RawResponse:
    body = build_pdf([lines])
    return RawResponse(
        url=url,
        final_url=url,
        status_code=200,
        text=body.decode("utf-8", "replace"),
        content_type="application/pdf",
        body=body,
    )


# Mercado Libre sends some pages to its account verification (H0): a login wall, not content.
VERIFY_URL = (
    "https://www.acme.com.co/gz/account-verification?go=https%3A%2F%2Fwww.acme.com.co%2Fplanes"
)
VERIFY_PAGE = (
    "<html><body><main><h1>Verifica tu cuenta</h1>"
    "<p>Para continuar, ingresa a tu cuenta.</p></main></body></html>"
)
CHALLENGE_PAGE = (
    "<html><head><title>Just a moment...</title></head><body>"
    "<p>Checking your browser before accessing acme.com.co.</p></body></html>"
)


def index_page(*links: str) -> str:
    items = "".join(f'<li><a href="{href}">Prislista</a></li>' for href in links)
    return f"<html><body><main><h1>Priser</h1><ul>{items}</ul></main></body></html>"


async def watch(
    bf: BundlesFile,
    routes: dict[str, Any],
    *,
    previous: WatchFile | None = None,
    today: date = TODAY,
    max_pages: int = 40,
    fx: FxTable | None = None,
) -> tuple[WatchFile, FixtureFetcher]:
    fetcher = FixtureFetcher(routes)
    out = await watch_bundles(
        bf,
        pages=PageCache(fetcher, timeout_s=30),
        previous=previous,
        today=today,
        now=datetime(today.year, today.month, today.day, 5, 23, tzinfo=UTC),
        run_id=f"r-{today.isoformat()}",
        catalog=CATALOG,
        fx=fx,
        max_pages=max_pages,
    )
    return out, fetcher


def anchors_by_fact(state: Any) -> dict[str, Any]:
    return {(a.fact if a.fact != "component" else a.service_id): a for a in state.anchors}


# ------------------------------------------------------------------------- anchors


async def test_anchors_on_a_page_that_loaded_are_verified() -> None:
    bf = bundles(acme())
    out, fetcher = await watch(bf, {ACME: acme_page()})
    assert fetcher.calls == [ACME]
    (state,) = out.bundles
    assert state.outcome == "ok" and state.checked_at == TODAY
    for a in state.anchors:
        assert (a.status, a.via, a.last_verified_at, a.misses) == ("verified", "lines", TODAY, 0)
    (page,) = state.pages
    assert (page.status, page.last_ok_at, page.unreadable_runs, page.paused_until) == (
        "ok",
        TODAY,
        0,
        None,
    )
    # The page's amounts are the YAML ones: nothing to propose.
    assert state.hints == []
    b = bf.bundles[0]
    st = bundle_state(b, today=TODAY, watch=state)
    assert st.display_status == "verified" and st.reasons_es == ()
    assert st.confidence == "high" and st.verified_at == TODAY
    assert st.intro_confirmed and st.confirmed_components == {"disney_plus", "hbo_max"}


async def test_a_missing_price_anchor_is_a_change_and_the_new_amount_a_hint() -> None:
    bf = bundles(acme())
    first, _ = await watch(bf, {ACME: acme_page()})
    changed = acme_page(PRICE.replace("21.900", "23.900"))
    later = TODAY + timedelta(days=7)
    out, _ = await watch(bf, {ACME: changed}, previous=first, today=later)
    (state,) = out.bundles
    a = anchors_by_fact(state)
    assert state.outcome == "changed"
    assert (a["price"].status, a["price"].via, a["price"].misses) == ("missing", None, 1)
    assert a["price"].last_verified_at == TODAY  # carried: the last day it was verified
    assert a["disney_plus"].status == "verified"
    (hint,) = state.hints
    assert (hint.role, hint.amount, hint.currency, hint.period) == (
        "regular",
        Decimal("23900"),
        "COP",
        "month",
    )
    assert "23.900" in hint.line and hint.url == ACME and len(hint.line) <= 200
    st = bundle_state(bf.bundles[0], today=later, watch=state)
    assert st.display_status == "needs_review"
    assert st.reasons_es[0] == "El precio ya no aparece en acme.com.co."
    assert "Último valor confirmado 21.900 COP el 12 de octubre de 2026" in st.reasons_es[1]
    # The hint is a proposal from the day it was first found, carried while it is found again.
    assert hint.first_seen == later
    again, _ = await watch(bf, {ACME: changed}, previous=out, today=later + timedelta(days=7))
    assert [h.first_seen for h in again.bundles[0].hints] == [later]
    again, _ = await watch(bf, {ACME: changed}, previous=out, today=later + timedelta(days=7))
    assert anchors_by_fact(again.bundles[0])["price"].misses == 2


async def test_an_excerpt_that_no_longer_states_the_value_is_missing_with_the_reason() -> None:
    text = "Acme Total cuesta $ 21.900 por mes"
    row = acme(
        price={"amount": "21900", "currency": "COP"},
        watch={
            "mode": "auto",
            "pages": [{"url": ACME}],
            "anchors": [
                {"fact": "price", "url": ACME, "text": text},
            ],
        },
    )
    page = f"<html><body><p>{text} durante los primeros 2 meses</p></body></html>"
    out, _ = await watch(bundles(row), {ACME: page})
    (a,) = out.bundles[0].anchors
    assert (a.status, a.via) == ("missing", "lines")
    assert a.detail.startswith("value_not_in_quote:") and "introductory" in a.detail
    assert out.bundles[0].outcome == "changed"
    st = bundle_state(bundles(row).bundles[0], today=TODAY, watch=out.bundles[0])
    assert (
        st.reasons_es[0] == "El precio sigue en acme.com.co, pero ya no indica el valor registrado."
    )


@pytest.mark.parametrize(
    "response",
    [
        BlockedError("bot wall", status=403, url=ACME),
        RobotsDisallowedError("robots", url=ACME),
        FetchError("server error", status=500, url=ACME),
        FetchError("gone", status=404, url=ACME),
        html(ACME, acme_page(), final_url="https://www.acme.com.mx/planes"),
        # Walls that answer 200 on the provider's own domain (HttpFetcher only raises
        # BlockedError for a 4xx/503 wall).
        html(ACME, VERIFY_PAGE, final_url=VERIFY_URL),
        html(ACME, CHALLENGE_PAGE),
        RawResponse(
            url=ACME,
            final_url=ACME,
            status_code=200,
            text=acme_page(),
            headers={"cf-mitigated": "challenge"},
        ),
    ],
    ids=[
        "blocked",
        "robots",
        "failed",
        "not_found",
        "wrong_country",
        "account_verification_200",
        "challenge_200",
        "challenge_header_200",
    ],
)
async def test_an_unreadable_page_never_counts_as_a_change(response: Any) -> None:
    bf = bundles(acme())
    first, _ = await watch(bf, {ACME: acme_page()})
    later = TODAY + timedelta(days=7)
    out, _ = await watch(bf, {ACME: response}, previous=first, today=later)
    (state,) = out.bundles
    assert state.outcome == "unreadable"
    for a in state.anchors:
        assert a.status == "unreadable" and a.misses == 0 and a.last_verified_at == TODAY
    (page,) = state.pages
    assert page.unreadable_runs == 1 and page.last_ok_at == TODAY and page.paused_until is None
    # Verified a week ago: still within the 15-day window, so the bundle stays verified.
    assert bundle_state(bf.bundles[0], today=later, watch=state).display_status == "verified"
    # Past the window it is unverified, with what the watcher found, never needs_review.
    late = later + timedelta(days=9)
    st = bundle_state(bf.bundles[0], today=late, watch=state)
    assert st.display_status == "unverified"
    assert any("acme.com" in r for r in st.reasons_es)
    assert any(r.startswith("Último valor confirmado 21.900 COP") for r in st.reasons_es)


async def test_a_wall_that_answers_200_is_a_blocked_page() -> None:
    bf = bundles(acme())
    out, _ = await watch(bf, {ACME: html(ACME, VERIFY_PAGE, final_url=VERIFY_URL)})
    (page,) = out.bundles[0].pages
    assert (page.status, page.final_url) == ("blocked", VERIFY_URL)
    assert page.detail.startswith("login wall at ")
    out, _ = await watch(bf, {ACME: html(ACME, CHALLENGE_PAGE)})
    (page,) = out.bundles[0].pages
    assert page.status == "blocked" and page.detail.startswith("cloudflare wall at ")
    # A page that is long enough is content even when it names a captcha somewhere.
    long_page = acme_page(extra="<p>" + "Formulario protegido por captcha. " * 20 + "</p>")
    out, _ = await watch(bf, {ACME: long_page})
    assert out.bundles[0].pages[0].status == "ok" and out.bundles[0].outcome == "ok"


@pytest.mark.parametrize("third", ["blocked", "over_budget", "paused"])
async def test_a_change_is_not_undone_by_the_runs_that_cannot_read_the_page(third: str) -> None:
    """verified -> missing -> not read: the old verification never counts again (needs_review
    stays, with the last confirmed value), until a read verifies the anchor again."""
    bf = bundles(acme())
    b = bf.bundles[0]
    first, _ = await watch(bf, {ACME: acme_page()})
    week = TODAY + timedelta(days=7)
    changed = acme_page(PRICE.replace("21.900", "23.900"))
    second, _ = await watch(bf, {ACME: changed}, previous=first, today=week)
    assert bundle_state(b, today=week, watch=second.bundles[0]).display_status == "needs_review"
    if third == "paused":  # a page that had paused right after the change
        page = second.bundles[0].pages[0].model_copy(update={"paused_until": week + timedelta(28)})
        second.bundles[0].pages[0] = page
    later = week + timedelta(days=7)
    routes = {ACME: BlockedError("bot wall", status=403, url=ACME)} if third == "blocked" else {}
    out, fetcher = await watch(
        bf,
        routes or {ACME: acme_page()},
        previous=second,
        today=later,
        max_pages=0 if third == "over_budget" else 40,
    )
    assert fetcher.calls == ([ACME] if third == "blocked" else [])
    (state,) = out.bundles
    price = anchors_by_fact(state)["price"]
    assert price.status != "verified" and price.last_verified_at == TODAY
    assert (price.unconfirmed, price.unconfirmed_on) == ("missing", week)
    st = bundle_state(b, today=later, watch=state)
    assert (st.display_status, st.confidence, st.verified_at) == ("needs_review", "low", None)
    # A paused page carries its anchors as they were: the miss itself.
    assert st.reasons_es[0] == (
        "El precio ya no aparece en acme.com.co."
        if third == "paused"
        else "El precio no aparecía en acme.com.co la última vez que se pudo leer "
        "(19 de octubre de 2026)."
    )
    assert any(
        r.startswith("Último valor confirmado 21.900 COP el 12 de oct") for r in st.reasons_es
    )
    assert not st.intro_confirmed  # no first-year cost from the old intro either
    if third == "paused":
        return
    # The page shows the curated price again: verified, and the earlier miss is cleared.
    back = later + timedelta(days=7)
    again, _ = await watch(bf, {ACME: acme_page()}, previous=out, today=back)
    price = anchors_by_fact(again.bundles[0])["price"]
    assert (price.status, price.unconfirmed, price.last_verified_at) == ("verified", None, back)
    assert bundle_state(b, today=back, watch=again.bundles[0]).display_status == "verified"


async def test_an_embedded_only_anchor_stays_unconfirmed_while_the_page_is_unreadable() -> None:
    script = (
        f'<script type="application/ld+json">{{"offers": {{"description": "{PRICE}"}}}}</script>'
    )
    embedded = (
        f"<html><head>{script}</head><body><h2>Acme Total</h2>"
        f"<ul><li>{DISNEY}</li><li>{HBO}</li></ul></body></html>"
    )
    bf = bundles(acme())
    first, _ = await watch(bf, {ACME: acme_page()})
    week = TODAY + timedelta(days=7)
    second, _ = await watch(bf, {ACME: embedded}, previous=first, today=week)
    later = week + timedelta(days=1)
    blocked = {ACME: BlockedError("bot wall", status=403, url=ACME)}
    out, _ = await watch(bf, blocked, previous=second, today=later)
    price = anchors_by_fact(out.bundles[0])["price"]
    assert (price.status, price.unconfirmed) == ("unreadable", "embedded_only")
    st = bundle_state(bf.bundles[0], today=later, watch=out.bundles[0])
    assert st.display_status == "unverified"
    assert "El precio solo aparecía en datos embebidos de la página" in " ".join(st.reasons_es)


async def test_wrong_country_by_host_and_by_currency() -> None:
    bf = bundles(acme())
    elsewhere = html(ACME, acme_page(), final_url="https://www.acme.com.mx/planes")
    out, _ = await watch(bf, {ACME: elsewhere})
    (page,) = out.bundles[0].pages
    assert page.status == "wrong_country" and "acme.com.mx" in page.detail
    usd = "<html><body><p>Acme Total: USD 9.99 per month</p><p>Disney+ Estándar con anuncios.</p>"
    out, _ = await watch(bf, {ACME: usd + "</body></html>"})
    (page,) = out.bundles[0].pages
    assert page.status == "wrong_country" and page.detail == "prices in USD, none in COP"
    assert {a.status for a in out.bundles[0].anchors} == {"unreadable"}
    st = bundle_state(bf.bundles[0], today=TODAY, watch=out.bundles[0])
    assert "acme.com.co mostró la página de otro país" in " ".join(st.reasons_es)


TELCO_WEB = "https://www.telco.se/mobil"


def telco_web() -> dict[str, Any]:
    return telco(
        watch={
            "mode": "auto",
            "pages": [{"url": TELCO_WEB}],
            "anchors": [
                {"fact": "price", "url": TELCO_WEB, "text": TELCO_PRICE},
                {
                    "fact": "component",
                    "service_id": "netflix",
                    "url": TELCO_WEB,
                    "text": TELCO_PRICE,
                },
            ],
        }
    )


@pytest.mark.parametrize(
    ("row", "url", "price", "fx"),
    [
        # "$" resolves to COP on a Colombian page, but COP has no decimals.
        (acme, ACME, "$9.99", None),
        # A whole "$" amount: with the FX table, 12 COP is far under any plausible price.
        (
            acme,
            ACME,
            "$ 12",
            FxTable(
                as_of=TODAY,
                rates={
                    "COP": FxRate(
                        currency="COP", per_eur=Decimal("4500"), source="ecb", as_of=TODAY
                    )
                },
            ),
        ),
        # "$" can never be SEK.
        (telco_web, TELCO_WEB, "$9.99", None),
    ],
    ids=["co_decimals", "co_fx", "se"],
)
async def test_a_page_priced_only_in_bare_dollars_is_another_countrys(
    row: Any, url: str, price: str, fx: FxTable | None
) -> None:
    """one.google.com prices by IP: a page that shows only "$9.99" is unreadable, never a
    change of the bundle's price."""
    page = (
        f"<html><body><h2>Acme Total / Telco Plus</h2><p>Plan: {price} per month</p>"
        f"<ul><li>{DISNEY}</li></ul></body></html>"
    )
    bf = bundles(row())
    out, _ = await watch(bf, {url: page}, fx=fx)
    (state,) = out.bundles
    (check,) = state.pages
    currency = bf.bundles[0].price.currency
    assert (check.status, check.detail) == ("wrong_country", f"prices in $, none in {currency}")
    assert state.outcome == "unreadable"
    assert {a.status for a in state.anchors} == {"unreadable"}
    # The same "$" next to a plausible amount of the country's currency is read as usual.
    if row is acme:
        out, _ = await watch(bf, {url: page.replace("</ul>", f"</ul><p>{PRICE}</p>")}, fx=fx)
        assert out.bundles[0].pages[0].status == "ok"


async def test_three_unreadable_runs_pause_the_page_for_28_days() -> None:
    bf = bundles(acme())
    blocked = {ACME: BlockedError("bot wall", status=403, url=ACME)}
    prev = None
    days = [TODAY + timedelta(days=7 * i) for i in range(3)]
    for n, day in enumerate(days, 1):
        prev, _ = await watch(bf, blocked, previous=prev, today=day)
        assert prev.bundles[0].pages[0].unreadable_runs == n
    page = prev.bundles[0].pages[0]
    assert page.paused_until == days[-1] + timedelta(days=PAUSE_DAYS)
    # While paused: not fetched, the check is carried as it was.
    during = days[-1] + timedelta(days=7)
    out, fetcher = await watch(bf, {ACME: acme_page()}, previous=prev, today=during)
    assert fetcher.calls == []
    assert out.bundles[0].pages[0] == page and out.bundles[0].outcome == "unreadable"
    st = bundle_state(bf.bundles[0], today=during, watch=out.bundles[0])
    assert any("en pausa hasta el" in r for r in st.reasons_es)
    # Once the pause is over the page is read again.
    after = page.paused_until
    out, fetcher = await watch(bf, {ACME: acme_page()}, previous=out, today=after)
    assert fetcher.calls == [ACME] and out.bundles[0].outcome == "ok"
    assert out.bundles[0].pages[0].unreadable_runs == 0


async def test_embedded_only_never_confirms() -> None:
    script = (
        f'<script type="application/ld+json">{{"offers": {{"description": "{PRICE}"}}}}</script>'
    )
    page = (
        f"<html><head>{script}</head><body><h2>Acme Total</h2>"
        f"<ul><li>{DISNEY}</li><li>{HBO}</li></ul></body></html>"
    )
    bf = bundles(acme())
    out, _ = await watch(bf, {ACME: page})
    a = anchors_by_fact(out.bundles[0])
    assert (a["price"].status, a["price"].via, a["price"].last_verified_at) == (
        "embedded_only",
        "embedded",
        None,
    )
    assert out.bundles[0].outcome == "ok"  # not missing: the page has it, embedded
    st = bundle_state(bf.bundles[0], today=TODAY, watch=out.bundles[0])
    assert st.display_status == "unverified"
    assert "El precio solo aparece en datos embebidos" in " ".join(st.reasons_es)


# ------------------------------------------------------------------------- versioned PDFs


async def test_a_newer_edition_on_the_index_is_a_new_document() -> None:
    bf = bundles(telco())
    new_doc = ["Telco Prislista 19", "Telco Plus med Netflix Standard 749 kr/mån"]
    routes = {
        INDEX: index_page(
            "/priser/TSP-4667_18_0701_Prislista.pdf",
            PDF19,
            # Another site's file is never an edition of this list.
            "https://evil.example/priser/TSP-4667_99_0101_Prislista.pdf",
        ),
        PDF19: pdf(PDF19, new_doc),
    }
    out, fetcher = await watch(bf, routes)
    assert fetcher.calls == [INDEX, PDF19]  # the superseded document is not fetched
    (state,) = out.bundles
    assert state.outcome == "new_document"
    index, doc = state.pages
    assert (index.kind, index.status) == ("index", "ok")
    assert (doc.status, doc.edition, doc.newest_edition, doc.newest_url) == (
        "superseded",
        "18",
        "19",
        PDF19,
    )
    assert doc.newest_status == "ok"
    for a in state.anchors:
        assert (a.status, a.edition) == ("outdated_document", "18")
    # The new list still names the component on the same line, but at another price: the
    # excerpt (699 kr) is not in it; the new amount is a hint.
    assert doc.newest_anchors == []
    (hint,) = state.hints
    assert (hint.amount, hint.role, hint.url) == (Decimal("749"), "regular", PDF19)
    st = bundle_state(bf.bundles[0], today=TODAY, watch=state)
    assert st.display_status == "needs_review"
    assert st.reasons_es == (
        "Hay una edición más nueva de la lista de precios (19); los valores son de la "
        "edición 18 hasta revisarlos.",
    )


async def test_the_newest_edition_on_the_index_is_current() -> None:
    bf = bundles(telco())
    routes = {
        INDEX: index_page(
            "TSP-4667_17_0101_Prislista.pdf", "/priser/TSP-4667_18_0701_Prislista.pdf"
        ),
        PDF18: pdf(PDF18, ["Telco Prislista 18", TELCO_PRICE]),
    }
    out, fetcher = await watch(bf, routes)
    assert fetcher.calls == [INDEX, PDF18]
    (state,) = out.bundles
    assert state.outcome == "ok"
    doc = state.pages[1]
    assert (doc.status, doc.edition_check, doc.newest_edition) == ("ok", "index", "18")
    assert {a.status for a in state.anchors} == {"verified"}
    assert all(a.edition == "18" for a in state.anchors)
    st = bundle_state(bf.bundles[0], today=TODAY, watch=state)
    assert (st.display_status, st.confidence) == ("verified", "high")


async def test_without_a_readable_index_a_pdf_needs_a_validity_anchor() -> None:
    blocked = {INDEX: BlockedError("bot wall", status=403, url=INDEX)}
    lines = ["Telco Prislista 18", TELCO_PRICE, VALIDITY]
    with_validity = bundles(telco(validity=True))
    out, _ = await watch(with_validity, {**blocked, PDF18: pdf(PDF18, lines)})
    (state,) = out.bundles
    doc = state.pages[1]
    assert (doc.status, doc.edition_check) == ("ok", "validity")
    st = bundle_state(with_validity.bundles[0], today=TODAY, watch=state)
    # Current only by its own validity line: confidence at most medium.
    assert (st.display_status, st.confidence) == ("verified", "medium")

    without = bundles(telco())
    out, _ = await watch(without, {**blocked, PDF18: pdf(PDF18, lines)})
    (state,) = out.bundles
    assert state.pages[1].edition_check == "unchecked"
    for a in state.anchors:
        assert a.status == "verified" and a.last_verified_at is None
    st = bundle_state(without.bundles[0], today=TODAY, watch=state)
    assert st.display_status == "unverified"
    assert "Según la lista 18; no se pudo comprobar si hay otra más nueva." in st.reasons_es


@pytest.mark.parametrize(
    "index",
    [BlockedError("bot wall", status=403, url=INDEX), index_page("/om/annat.pdf")],
    ids=["index_blocked", "no_matching_link"],
)
async def test_a_newer_edition_found_earlier_is_not_undone_by_the_index(index: Any) -> None:
    bf = bundles(telco(validity=True))
    b = bf.bundles[0]
    doc18 = pdf(PDF18, ["Telco Prislista 18", TELCO_PRICE, VALIDITY])
    doc19 = pdf(PDF19, ["Telco Prislista 19", "Telco Plus med Netflix Standard 749 kr/mån"])
    first, _ = await watch(bf, {INDEX: index_page(PDF18), PDF18: doc18})
    assert bundle_state(b, today=TODAY, watch=first.bundles[0]).display_status == "verified"
    week = TODAY + timedelta(days=7)
    routes = {INDEX: index_page(PDF18, PDF19), PDF18: doc18, PDF19: doc19}
    second, _ = await watch(bf, routes, previous=first, today=week)
    assert second.bundles[0].outcome == "new_document"
    # The index cannot be read (or lists nothing) the week after: edition 19 still replaces 18.
    later = week + timedelta(days=7)
    out, fetcher = await watch(
        bf, {INDEX: index, PDF18: doc18, PDF19: doc19}, previous=second, today=later
    )
    assert fetcher.calls == [INDEX, PDF19]  # the superseded document is not read as current
    (state,) = out.bundles
    doc = state.pages[1]
    assert (doc.status, doc.edition, doc.newest_edition, doc.newest_url) == (
        "superseded",
        "18",
        "19",
        PDF19,
    )
    assert "found by an earlier run" in doc.detail
    assert state.outcome == "new_document"
    assert {a.status for a in state.anchors} == {"outdated_document"}
    st = bundle_state(b, today=later, watch=state)
    assert st.display_status == "needs_review"
    assert st.reasons_es[0].startswith("Hay una edición más nueva de la lista de precios (19)")


async def test_without_this_runs_index_the_confidence_is_at_most_medium() -> None:
    """Verified through the index, then the index cannot be read: within the 15 days the bundle
    stays verified, but at most medium and saying why (never high without evidence)."""
    blocked = BlockedError("bot wall", status=403, url=INDEX)
    lines = ["Telco Prislista 18", TELCO_PRICE, VALIDITY]
    for validity, pdf_ok, reason in [
        (False, True, "Según la lista 18; no se pudo comprobar si hay otra más nueva."),
        (
            True,
            True,
            "Vigente según la propia lista 18 (desde el 1 de julio de 2026); no se pudo comprobar "
            "si hay otra más nueva.",
        ),
        (False, False, "Según la lista 18; no se pudo comprobar si hay otra más nueva."),
    ]:
        bf = bundles(telco(validity=validity))
        b = bf.bundles[0]
        first, _ = await watch(bf, {INDEX: index_page(PDF18), PDF18: pdf(PDF18, lines)})
        st = bundle_state(b, today=TODAY, watch=first.bundles[0])
        assert (st.display_status, st.confidence, st.reasons_es) == ("verified", "high", ())
        week = TODAY + timedelta(days=7)
        doc = pdf(PDF18, lines) if pdf_ok else BlockedError("bot wall", status=403, url=PDF18)
        out, _ = await watch(bf, {INDEX: blocked, PDF18: doc}, previous=first, today=week)
        st = bundle_state(b, today=week, watch=out.bundles[0])
        assert (st.display_status, st.confidence) == ("verified", "medium"), (validity, pdf_ok)
        assert st.reasons_es == (reason,)
        # Past the window, unverified with the same reason.
        if not validity:
            late = TODAY + timedelta(days=16)
            st = bundle_state(b, today=late, watch=out.bundles[0])
            assert st.display_status == "unverified" and reason in st.reasons_es


async def test_an_index_read_alone_is_an_unreadable_outcome() -> None:
    # Only the index page loaded; the document every anchor quotes did not.
    bf = bundles(telco())
    robots = RobotsDisallowedError("robots", url=PDF18)
    out, _ = await watch(bf, {INDEX: index_page(PDF18), PDF18: robots})
    (state,) = out.bundles
    assert [p.status for p in state.pages] == ["ok", "robots_disallowed"]
    assert state.outcome == "unreadable"
    assert {a.status for a in state.anchors} == {"unreadable"}
    assert summary(out)["outcomes"] == {"unreadable": 1}


async def test_the_same_edition_at_another_url_is_read_where_the_index_links_it() -> None:
    moved = "https://www.telco.se/assets/m/9f0c/TSP-4667_18_0701_Prislista.pdf"
    routes = {
        INDEX: index_page(moved),
        PDF18: FetchError("gone", status=404, url=PDF18),
        moved: pdf(moved, ["Telco Prislista 18", TELCO_PRICE]),
    }
    out, fetcher = await watch(bundles(telco()), routes)
    assert fetcher.calls == [INDEX, moved]
    (state,) = out.bundles
    doc = state.pages[1]
    assert (doc.url, doc.final_url, doc.status, doc.edition_check) == (PDF18, moved, "ok", "index")
    assert {a.status for a in state.anchors} == {"verified"}


async def test_the_newest_edition_left_out_by_the_budget_is_reported() -> None:
    routes = {INDEX: index_page(PDF18, PDF19), PDF19: pdf(PDF19, ["Telco Prislista 19"])}
    out, fetcher = await watch(bundles(telco()), routes, max_pages=1)
    assert fetcher.calls == [INDEX]
    (state,) = out.bundles
    doc = state.pages[1]
    assert (doc.status, doc.newest_status) == ("superseded", "over_budget")
    assert pages_over_budget(state, TODAY) == [PDF19]
    assert pages_over_budget(state, TODAY + timedelta(days=1)) == []
    assert summary(out)["pages_over_budget"] == 1


def test_editions_compare_by_their_numbers() -> None:
    assert edition_key("9") < edition_key("10") < edition_key("10_0102")
    assert max(["18_0701", "19_0102", "2_1231"], key=edition_key) == "19_0102"


def test_index_links_resolve_relative_and_escaped_urls() -> None:
    from haggler.research.evidence import Page

    text = (
        '<a href="a.pdf">a</a><a href="/b.pdf">b</a><a href="mailto:x@y.se">m</a>'
        '<script>{"u": "https:\\/\\/www.telco.se\\/c.pdf"}</script>'
    )
    page = Page(url=INDEX, final_url="https://www.telco.se/om/priser/", text=text)
    assert page_links(page) == [
        "https://www.telco.se/om/priser/a.pdf",
        "https://www.telco.se/b.pdf",
        "https://www.telco.se/c.pdf",
    ]


# ------------------------------------------------------------------------- hints


async def test_hints_only_for_the_bundles_own_tier() -> None:
    esencial = acme(
        bundle_id="acme_esencial",
        name="Acme Esencial",
        tier="esencial",
        aliases=["Acme Esencial"],
        price={"amount": "9900", "currency": "COP"},
        components=[{"service_id": "hbo_max", "inclusion": "discount_pct", "value": "30"}],
        watch={"mode": "auto", "pages": [{"url": ACME}]},
    )
    total = acme(watch={"mode": "auto", "pages": [{"url": ACME}]})
    page = (
        "<html><body><h2>Acme Total</h2><p>Acme Total: $ 23.900 por mes</p>"
        "<h2>Acme Esencial</h2><p>Acme Esencial: $ 9.900 por mes</p></body></html>"
    )
    out, fetcher = await watch(bundles(total, esencial), {ACME: page})
    assert fetcher.calls == [ACME]  # one fetch for both bundles
    hints = {s.bundle_id: [(h.role, h.amount) for h in s.hints] for s in out.bundles}
    assert hints == {"acme_total": [("regular", Decimal("23900"))], "acme_esencial": []}


async def test_hints_in_a_pdf_need_the_bundles_name_on_the_line() -> None:
    lines = [
        "Telco Prislista 18",
        TELCO_PRICE,
        "Telco Bas 299 kr/mån",
        "Telco Plus (ny) 749 kr/mån",
    ]
    routes = {INDEX: index_page(PDF18), PDF18: pdf(PDF18, lines)}
    out, _ = await watch(bundles(telco()), routes)
    # "Telco Bas" is another row of the list, even right under the bundle's own row.
    assert [h.amount for h in out.bundles[0].hints] == [Decimal("749")]


# ------------------------------------------------------------------------- scope and budget


def test_anchor_hash_is_stable_and_specific() -> None:
    a = Anchor(fact="price", url=ACME, text=PRICE)
    seed = f"price|{normalize_for_match(PRICE)}|{ACME}"
    assert anchor_hash(a) == hashlib.sha256(seed.encode()).hexdigest()[:12]
    spaced = Anchor(
        fact="price", url=ACME + "#precio", text="  " + PRICE.upper().replace(" ", "  ")
    )
    assert anchor_hash(spaced) == anchor_hash(a)
    assert anchor_hash(Anchor(fact="intro", url=ACME, text=PRICE)) != anchor_hash(a)
    assert anchor_hash(Anchor(fact="price", url=ACME + "/x", text=PRICE)) != anchor_hash(a)
    # One excerpt may confirm several components: each keeps its own state.
    one = Anchor(fact="component", service_id="netflix", url=PDF18, text=TELCO_PRICE)
    two = Anchor(fact="component", service_id="hbo_max", url=PDF18, text=TELCO_PRICE)
    assert anchor_hash(one) != anchor_hash(two)


async def test_an_edited_anchor_does_not_inherit_the_old_state() -> None:
    bf = bundles(acme())
    first, _ = await watch(bf, {ACME: acme_page()})
    edited = acme()
    edited["watch"]["anchors"][2]["text"] = "Incluye Disney+ Estándar con anuncios."
    out, _ = await watch(bundles(edited), {ACME: acme_page()}, previous=first)
    by = anchors_by_fact(out.bundles[0])
    assert by["disney_plus"].status == "missing" and by["disney_plus"].misses == 1


async def test_the_page_budget_is_reported_never_silent(caplog: pytest.LogCaptureFixture) -> None:
    rows = [
        acme(
            bundle_id=f"acme_{i}",
            name=f"Acme {i}",
            aliases=[f"Acme {i}"],
            official_url=f"{ACME}/{i}",
            watch={
                "mode": "auto",
                "pages": [{"url": f"{ACME}/{i}"}],
                "anchors": [
                    {
                        "fact": "component",
                        "service_id": "disney_plus",
                        "url": f"{ACME}/{i}",
                        "text": DISNEY,
                    },
                ],
            },
        )
        for i in range(3)
    ]
    routes = {f"{ACME}/{i}": acme_page() for i in range(3)}
    with caplog.at_level(logging.WARNING, logger="haggler.bundles.watch"):
        out, fetcher = await watch(bundles(*rows), routes, max_pages=2)
    assert fetcher.calls == [f"{ACME}/0", f"{ACME}/1"]
    last = out.bundles[2]
    assert last.pages[0].status == "over_budget"
    assert last.anchors[0].status == "not_checked" and last.outcome == "unreadable"
    assert "page budget of 2 spent" in caplog.text and f"{ACME}/2" in caplog.text


async def test_forbidden_provider_manual_and_off_bundles_are_not_fetched() -> None:
    bf = bundles(acme())
    first, _ = await watch(bf, {ACME: acme_page()})
    out, fetcher = await watch(
        bundles(acme(), acme="forbidden"), {ACME: acme_page()}, previous=first
    )
    assert fetcher.calls == []
    (state,) = out.bundles
    assert state.outcome == "off" and "forbid automated access" in state.detail
    # The previous state is carried, and no longer counts for the display state.
    assert state.anchors == first.bundles[0].anchors and state.checked_at == TODAY
    b = bundles(acme(), acme="forbidden").bundles[0]
    assert bundle_state(b, today=TODAY, watch=state).display_status == "unverified"

    manual = acme(watch={**acme()["watch"], "mode": "manual"})
    out, fetcher = await watch(bundles(manual), {ACME: acme_page()})
    assert fetcher.calls == [] and out.bundles[0].outcome == "manual"
    assert {a.status for a in out.bundles[0].anchors} == {"not_checked"}


async def test_an_expired_bundle_is_not_fetched() -> None:
    bf = bundles(acme())
    first, _ = await watch(bf, {ACME: acme_page()})
    ended = bundles(acme(valid_until="2026-10-01"))
    out, fetcher = await watch(ended, {ACME: acme_page()}, previous=first)
    assert fetcher.calls == [] and out.bundles == first.bundles
    out, _ = await watch(ended, {ACME: acme_page()})
    assert out.bundles == []


async def test_an_observed_bundle_without_todays_row_says_only_what_is_missing() -> None:
    row = acme(
        price={
            "currency": "COP",
            "source": "observed",
            "observed_service_id": "disney_plus",
            "observed_plan_id": "standard_with_ads",
        },
        components=[
            {"service_id": "disney_plus", "plan_id": "standard_with_ads", "inclusion": "included"}
        ],
        watch={
            "mode": "auto",
            "pages": [{"url": ACME}],
            "anchors": [
                {"fact": "component", "service_id": "disney_plus", "url": ACME, "text": DISNEY}
            ],
        },
        verified_by="official_fetch",
    )
    bf = bundles(row)
    out, _ = await watch(bf, {ACME: acme_page()})
    st = bundle_state(bf.bundles[0], today=TODAY, watch=out.bundles[0], price_missing=True)
    assert st.display_status == "unverified" and st.confirmed_components == {"disney_plus"}
    # Not "the watcher has not read / confirmed them": it has; only the daily row is missing.
    assert st.reasons_es == (
        "Componentes copiados de la página oficial.",
        "Aún no hay un precio publicado de este plan en los datos diarios.",
    )
    # Before the watcher read anything, the reasons say so.
    st = bundle_state(bf.bundles[0], today=TODAY, price_missing=True)
    assert st.reasons_es[0].endswith("pero el vigilante aún no los ha confirmado.")
    assert st.reasons_es[-1] == "El vigilante aún no ha leído sus páginas oficiales."


async def test_reasons_name_components_by_their_service_names() -> None:
    bf = bundles(acme())
    page = acme_page().replace(f"<li>{DISNEY}</li>", "")
    out, _ = await watch(bf, {ACME: page})
    names = {s.id: s.name for s in CATALOG.services}
    st = bundle_state(bf.bundles[0], today=TODAY, watch=out.bundles[0], service_names=names)
    assert st.reasons_es[0] == f"El componente {names['disney_plus']} ya no aparece en acme.com.co."
    plain = bundle_state(bf.bundles[0], today=TODAY, watch=out.bundles[0])
    assert plain.reasons_es[0] == "El componente disney_plus ya no aparece en acme.com.co."


async def test_a_component_anchor_naming_a_sibling_tier_does_not_confirm() -> None:
    # The component check gets the bundle and its siblings, as the offline check does: a line
    # about another tier of the same provider never confirms this bundle's component.
    sibling_line = "Acme Total: 30% OFF en HBO Max."
    esencial = acme(
        bundle_id="acme_esencial",
        name="Acme Esencial",
        tier="esencial",
        aliases=["Acme Esencial"],
        price={"amount": "9900", "currency": "COP"},
        components=[{"service_id": "hbo_max", "inclusion": "discount_pct", "value": "30"}],
        watch={
            "mode": "auto",
            "pages": [{"url": ACME}],
            "anchors": [
                {"fact": "component", "service_id": "hbo_max", "url": ACME, "text": sibling_line}
            ],
        },
    )
    total = acme(watch={"mode": "auto", "pages": [{"url": ACME}]})
    page = f"<html><body><h2>Acme Total</h2><p>{sibling_line}</p></body></html>"
    out, _ = await watch(bundles(total, esencial), {ACME: page})
    state = next(s for s in out.bundles if s.bundle_id == "acme_esencial")
    assert anchors_by_fact(state)["hbo_max"].status == "missing"


# ----------------------------------------------- a new edition, reviewed and pasted


TELCO_NETFLIX = "Netflix Standard ingår i Telco Plus utan extra kostnad."


def _paste_editions(row: dict[str, Any], snippets: list[Any]) -> dict[str, Any]:
    """Paste snippets the way their notes say: a page entry with the index_url and
    link_pattern of a versioned page replaces that page (and its anchors, which the snippet
    carries to the new url when the new edition still has them); a price anchor replaces the
    one that states the old price."""
    out = {**row, "price": dict(row["price"]), "watch": dict(row["watch"])}
    pages = list(out["watch"]["pages"])
    anchors = list(out["watch"]["anchors"])
    for s in snippets:
        frag = yaml.safe_load(s.text())
        out["price"] |= frag.get("price", {})
        for page in frag.get("watch", {}).get("pages", []):
            old = [p for p in pages if p.get("link_pattern") == page.get("link_pattern")]
            for p in old:
                if p["url"] != page["url"]:
                    anchors = [a for a in anchors if a["url"] != p["url"]]
            pages = [p for p in pages if p not in old] + [page]
        for anchor in frag.get("watch", {}).get("anchors", []):
            anchors = [
                a
                for a in anchors
                if (a["fact"], a.get("service_id")) != (anchor["fact"], anchor.get("service_id"))
            ] + [anchor]
    out["watch"] |= {"pages": pages, "anchors": anchors}
    return out


async def test_a_superseded_price_list_is_reviewed_and_pasted_back_to_verified() -> None:
    from haggler.bundles.proposals import bundle_proposals, edition_snippets, snippet
    from haggler.bundles.review import report_lines, review_bundle
    from haggler.catalog import integrity_errors
    from haggler.research.evaluate import EvalContext
    from haggler.research.review import ReviewIndex

    row = telco(price={"amount": "699", "currency": "SEK", "tax_included": True})
    row["watch"]["anchors"] = [
        {"fact": "price", "url": PDF18, "text": TELCO_PRICE},
        {"fact": "component", "service_id": "netflix", "url": PDF18, "text": TELCO_NETFLIX},
    ]
    bf = bundles(row)
    assert [e for e in integrity_errors(CATALOG, bundles=bf) if "telco_plus" in e] == []
    doc18 = pdf(PDF18, ["Telco Prislista 18", TELCO_PRICE, TELCO_NETFLIX])
    doc19 = pdf(PDF19, ["Telco Prislista 19", "Telco Plus med Netflix Standard 729 kr/mån",
                        TELCO_NETFLIX])  # fmt: skip
    first, _ = await watch(bf, {INDEX: index_page(PDF18), PDF18: doc18})
    week = TODAY + timedelta(days=7)
    out, _ = await watch(bf, {INDEX: index_page(PDF19), PDF19: doc19}, previous=first, today=week)
    (state,) = out.bundles
    assert state.outcome == "new_document"

    # The review names the newest edition's URL and which anchors it still has.
    b = bf.bundles[0]
    r = review_bundle(b, watch=state, facts=None, review=ReviewIndex(), today=week)
    ctx = EvalContext.build(
        catalog=CATALOG,
        feasibility=load_feasibility(),
        instruments=load_payment_instruments(),
        today=week,
    )
    text = "\n".join(report_lines(r, ctx, [], bundles=bf))
    assert f"edición 18, la más nueva 19 ({PDF19})" in text
    lines = {ln.strip().split(":")[0]: ln for ln in text.splitlines() if "ancla " in ln}
    assert lines["ancla price"].endswith("; no está en la edición 19")
    assert lines["ancla component netflix"].endswith("; la edición 19 la contiene")

    # The edition snippet moves the page (still versioned) and the anchor edition 19 still has.
    (edition,) = edition_snippets(b, state)
    frag = yaml.safe_load(edition.text())
    assert frag["watch"]["pages"] == [
        {"url": PDF19, "kind": "pdf", "index_url": INDEX, "link_pattern": PATTERN}
    ]
    assert frag["watch"]["anchors"] == [
        {"fact": "component", "service_id": "netflix", "url": PDF19, "text": TELCO_NETFLIX}
    ]
    assert any("no contiene estas anclas de la anterior: price" in n for n in edition.notes)
    # The price hint read in edition 19: its page replaces the versioned entry, never a new one.
    (p,) = bundle_proposals(b, watch=state, facts=None, review=None, today=week)
    assert (p.field, p.proposed, p.url) == ("bundle_price", "729 SEK/month", PDF19)
    price = snippet(b, p, bundles=bf, catalog=CATALOG)
    assert yaml.safe_load(price.text())["watch"]["pages"] == frag["watch"]["pages"]
    assert not any("aún no está en watch.pages" in n for n in price.notes)
    assert "reemplaza esa entrada de watch.pages" in " ".join(price.notes)

    edited = bundles(_paste_editions(row, [edition, price]))
    assert [e for e in integrity_errors(CATALOG, bundles=edited) if "telco_plus" in e] == []
    later = week + timedelta(days=7)
    out, _ = await watch(
        edited, {INDEX: index_page(PDF19), PDF19: doc19}, previous=out, today=later
    )
    (state,) = out.bundles
    assert state.outcome == "ok" and {a.status for a in state.anchors} == {"verified"}
    assert state.pages[1].edition == "19" and state.pages[1].edition_check == "index"
    assert bundle_state(edited.bundles[0], today=later, watch=state).display_status == "verified"

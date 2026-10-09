"""haggler probe --mode bundles: targets parsing and the readability report, without network."""

from __future__ import annotations

import asyncio
import io
import json
import re
from datetime import UTC, datetime
from pathlib import Path

import pytest

from conftest import FixtureFetcher
from haggler import probe_bundles
from haggler.fetch import RawResponse
from haggler.fetch.errors import (
    BlockedError,
    FetchError,
    RobotsDisallowedError,
    TransientFetchError,
)
from haggler.probe import run_probe
from haggler.probe_bundles import (
    LINE_CHARS,
    MAX_AMOUNT_LINES,
    MAX_EMBEDDED_HITS,
    MAX_EMBEDDED_SNIPPETS,
    MAX_SCRIPT_IDS,
    PATH_CHARS,
    QUOTE_BUDGET,
    READABILITY,
    _code,
    parse_bundle_targets,
    probe_bundle_targets,
    quoted_chars,
)

NOW = datetime(2026, 10, 9, 6, 0, tzinfo=UTC)
MELI = "https://www.mercadolibre.com.co/suscripciones/melimas/planes"
TELIA = "https://www.telia.se/privat/mobilt/abonnemang"
GO3 = "https://static3.go3.tv/file/go3/assets/legal/go3_lt_pricelist.pdf"
APPLE_SE = "https://www.apple.com/se/apple-one/"

MELI_PAGE = """<!doctype html><html><head><title>Meli+ | Planes</title></head><body>
<nav>Categorías</nav><h1>Elige tu Meli+</h1>
<div class="plan"><h2>Meli+ Total</h2><p>$ 24.900 / mes</p>
<ul><li>Disney+ Estándar con anuncios incluido</li><li>30 % de descuento en HBO Max</li>
<li>Paramount+ y ViX con descuento</li></ul></div>
<div class="plan"><h2>Meli+ Esencial</h2><p>$&nbsp;9.900 / mes</p><p>Envíos gratis</p></div>
<footer><p>Términos y condiciones de Mercado Libre Colombia, válidos hasta agotar existencias.
Las suscripciones se renuevan cada mes salvo que se cancelen desde la cuenta.</p>
<p>Los beneficios de streaming se activan desde la sección Suscripciones de tu cuenta, con el
mismo correo electrónico; cada servicio aplica sus propias condiciones de uso y de país.</p>
<p>Ayuda · Privacidad · Accesibilidad · Contacto · Trabaja con nosotros</p></footer>
</body></html>"""

TELIA_PAGE = """<html><head><title>Mobilabonnemang | Telia</title></head><body>
<h1>Obegränsad Plus</h1><p>569 kr/mån</p>
<p>Lägg till Netflix, HBO Max och Disney+ för 130 kr/mån</p>
<p>Totalt 699 kr/mån med alla tre tjänsterna. Bindningstid 24 månader, sedan löpande.</p>
<p>Priserna gäller för privatpersoner och inkluderar moms. Kreditprövning krävs.</p>
</body></html>"""

SPA_PAGE = """<html><head><title>Meli+</title>
<script id="__NEXT_DATA__" type="application/json">{"props": {"pageProps": {"plans": [
  {"name": "Meli+ Total", "price": {"amount": 24900, "currency": "COP"},
   "label": "$ 24.900 / mes", "active": true},
  {"name": "Meli+ Esencial", "price": {"amount": 9900, "currency": "COP"}}
]}}}</script>
<script>window.__PRELOADED_STATE__ = {"offer": {"priceText": "$ 21.900"}};</script>
</head><body><div id="root"></div><noscript>Activa JavaScript</noscript></body></html>"""


def html(url: str, text: str, *, final_url: str | None = None, **kw) -> RawResponse:
    return RawResponse(url=url, final_url=final_url or url, status_code=200, text=text, **kw)


def report(text: str, routes: dict, catalog, bundles, tmp_path: Path, **kw) -> list[dict]:
    targets = parse_bundle_targets(text, catalog, bundles)
    fetcher = kw.pop("fetcher", None) or FixtureFetcher(routes)
    return asyncio.run(probe_bundle_targets(targets, fetcher, catalog, bundles, tmp_path, **kw))


def row_for(text: str, routes: dict, catalog, bundles, tmp_path: Path) -> dict:
    (row,) = report(text, routes, catalog, bundles, tmp_path)
    return row


# --------------------------------------------------------------------------- targets


def test_parse_bundle_targets(catalog, bundles) -> None:
    text = f"""
# a comment line

meli_plus_co_total co {MELI}   # inline comment
apple_one_co CO https://www.apple.com/co/apple-one/#plans
meli_plus_co_total CO {MELI}
telia_se_obegransad_streaming SE {TELIA}
"""
    targets = parse_bundle_targets(text, catalog, bundles)
    assert [(t.label, t.country, t.url, t.line) for t in targets] == [
        ("meli_plus_co_total", "CO", MELI, 4),
        # A free label for a page not in bundles.yaml yet; the URL fragment is not a comment.
        ("apple_one_co", "CO", "https://www.apple.com/co/apple-one/#plans", 5),
        ("telia_se_obegransad_streaming", "SE", TELIA, 7),
    ]


def test_parse_bundle_targets_reports_every_bad_line(catalog, bundles) -> None:
    text = "\n".join(
        [
            f"meli_plus_co_total XX {MELI}",
            "meli_plus_co_total CO http://www.mercadolibre.com.co/x",
            "meli_plus_co_total CO",
            f"meli_plus_co_total CO {MELI} extra",
            f"Meli-Plus CO {MELI}",
            "local_page SE https://localhost/x",
            "lan_page SE https://10.0.0.1/x",
            f"meli_plus_co_total SE {MELI}",
            "creds SE https://user:pw@www.telia.se/x",
            "ftp_page SE ftp://www.telia.se/x",
            "bad_ipv6 SE https://[::1",
        ]
    )
    with pytest.raises(ValueError) as err:
        parse_bundle_targets(text, catalog, bundles)
    message = str(err.value)
    for expected in (
        "línea 1: país desconocido 'XX'",
        "línea 2: la URL debe ser https://",
        "línea 3: faltan campos",
        "línea 4: sobran campos",
        "línea 5: etiqueta inválida 'Meli-Plus'",
        "línea 6: el host 'localhost' no es público",
        "línea 7: el host '10.0.0.1' no es público",
        "línea 8: el paquete meli_plus_co_total es de CO, no de SE",
        "línea 9: la URL no puede llevar usuario ni contraseña",
        "línea 10: la URL debe ser https://",
        # urlsplit raises on it: still reported with its line, next to the others.
        "línea 11: URL inválida 'https://[::1'",
    ):
        assert expected in message


def test_parse_bundle_targets_needs_at_least_one(catalog, bundles) -> None:
    with pytest.raises(ValueError, match="ningún target"):
        parse_bundle_targets("# only comments\n\n", catalog, bundles)


def test_parse_bundle_targets_semicolons_like_the_workflow_override(catalog, bundles) -> None:
    override = f" meli_plus_co_total CO {MELI};  apple_one_co CO https://www.apple.com/co/x ;"
    # Read as is: a ";" next to whitespace separates targets, a ";" inside a URL does not.
    direct = parse_bundle_targets(
        override + "\nsess CO https://www.example.co/a;jsessionid=1", catalog, bundles
    )
    assert [(t.label, t.url, t.line) for t in direct] == [
        ("meli_plus_co_total", MELI, 1),
        ("apple_one_co", "https://www.apple.com/co/x", 1),
        ("sess", "https://www.example.co/a;jsessionid=1", 2),
    ]
    # What probe.yml writes: tr ';' '\n' | sed 's/^[[:space:]]*//'.
    lines = "\n".join(ln.lstrip() for ln in (override + "\n").replace(";", "\n").splitlines())
    via_workflow = parse_bundle_targets(lines, catalog, bundles)
    assert [(t.label, t.url) for t in via_workflow] == [(t.label, t.url) for t in direct[:2]]


def test_repo_bundle_targets_file_is_valid(catalog, bundles) -> None:
    path = Path(__file__).parents[2] / "probe" / "bundle_targets.txt"
    targets = parse_bundle_targets(path.read_text(encoding="utf-8"), catalog, bundles)
    known = {b.bundle_id: b for b in bundles.bundles}
    assert targets
    for t in targets:
        if t.label in known:
            assert t.country == known[t.label].country


# --------------------------------------------------------------------------- report


def test_visible_prices_in_cop_with_component_lines(catalog, bundles, tmp_path: Path) -> None:
    row = row_for(f"meli_plus_co_total CO {MELI}", {MELI: MELI_PAGE}, catalog, bundles, tmp_path)
    assert (row["status"], row["readability"], row["suggested_mode"]) == ("ok", "readable", "auto")
    assert row["mode_reason"] == "importes en COP en el texto visible"
    assert row["currency"] == "COP"
    assert (row["known_bundle"], row["bundle_name"]) == (True, "Meli+ Total")
    assert row["amount_lines"] == ["$ 24.900 / mes", "$ 9.900 / mes"]
    assert row["amount_lines_total"] == 2
    assert row["other_currencies"] == {}
    # Lines naming the bundle or its services (catalog names, "ViX Premium" -> "ViX").
    assert row["component_lines"] == [
        "Meli+ Total",
        "Disney+ Estándar con anuncios incluido",
        "30 % de descuento en HBO Max",
        "Paramount+ y ViX con descuento",
    ]
    assert row["page_title"] == "Meli+ | Planes"
    assert (row["http_status"], row["final_host"], row["same_site"]) == (
        200,
        "www.mercadolibre.com.co",
        True,
    )
    assert row["thin_page"] is False and row["wall"] is None
    # The full page stays under <out>/raw/bundles/ for local debugging only.
    assert row["raw_file"] == "raw/bundles/01_meli_plus_co_total_CO.html"
    assert (tmp_path / row["raw_file"]).read_text(encoding="utf-8") == MELI_PAGE


def test_visible_prices_in_sek(catalog, bundles, tmp_path: Path) -> None:
    row = row_for(
        f"telia_se_obegransad_streaming SE {TELIA}", {TELIA: TELIA_PAGE}, catalog, bundles, tmp_path
    )
    assert row["readability"] == "readable"
    assert row["amount_lines"][:2] == [
        "569 kr/mån",
        "Lägg till Netflix, HBO Max och Disney+ för 130 kr/mån",
    ]
    # Lines already quoted as amount lines are not quoted again as component lines.
    assert row["component_lines"] == []


def test_spa_with_prices_only_in_embedded_json(catalog, bundles, tmp_path: Path) -> None:
    row = row_for(f"meli_plus_co_total CO {MELI}", {MELI: SPA_PAGE}, catalog, bundles, tmp_path)
    assert row["readability"] == "embedded_only"
    assert row["suggested_mode"] == "manual"
    assert "datos embebidos" in row["mode_reason"]
    assert row["amount_lines"] == []
    assert row["json_script_ids"] == ["__NEXT_DATA__", "window.__PRELOADED_STATE__"]
    assert row["embedded_amount_snippets"] == [
        "__NEXT_DATA__ props.pageProps.plans[0].price.amount: 24900",
        "__NEXT_DATA__ props.pageProps.plans[0].label: $ 24.900 / mes",
        "__NEXT_DATA__ props.pageProps.plans[1].price.amount: 9900",
        "window.__PRELOADED_STATE__ offer.priceText: $ 21.900",
    ]
    assert row["embedded_amount_total"] == 4
    assert row["thin_page"] is True
    assert any("probablemente se pinta con JavaScript" in n for n in row["notes"])


def test_embedded_javascript_literal_falls_back_to_text_windows(
    catalog, bundles, tmp_path: Path
) -> None:
    page = (
        "<html><body><div id=app></div><script>window.__INITIAL_STATE__ = {plans: [{precio: 24900,"
        " texto: '$ 24.900 al mes'}]};</script></body></html>"
    )
    row = row_for(f"meli_plus_co_total CO {MELI}", {MELI: page}, catalog, bundles, tmp_path)
    assert row["readability"] == "embedded_only"
    snippets = row["embedded_amount_snippets"]
    assert snippets and all(s.startswith("window.__INITIAL_STATE__: ") for s in snippets)
    assert any("precio: 24900" in s for s in snippets)


def test_embedded_hits_are_counted_up_to_a_cap(catalog, bundles, tmp_path: Path) -> None:
    data = json.dumps(
        {"items": [{"label": f"$ {i}.900"} for i in range(10, MAX_EMBEDDED_HITS + 60)]}
    )
    page = f'<html><script type="application/ld+json">{data}</script><body></body></html>'
    row = row_for(f"meli_plus_co_total CO {MELI}", {MELI: page}, catalog, bundles, tmp_path)
    assert row["embedded_amount_total"] == MAX_EMBEDDED_HITS
    assert len(row["embedded_amount_snippets"]) == MAX_EMBEDDED_SNIPPETS
    assert row["embedded_amount_snippets"][0] == "application/ld+json items[0].label: $ 10.900"
    md = probe_bundles.render_bundles_md([row], generated_at=NOW)
    assert f"Importes en datos embebidos ({MAX_EMBEDDED_SNIPPETS} de {MAX_EMBEDDED_HITS}+)" in md


def test_blocked_robots_and_failed(catalog, bundles, tmp_path: Path) -> None:
    text = f"""
meli_plus_co_total CO {MELI}
telia_se_obegransad_streaming SE {TELIA}
apple_one_individual_se SE {APPLE_SE}
"""
    routes = {
        MELI: BlockedError("bot wall (cloudflare) HTTP 403", status=403, url=MELI),
        TELIA: RobotsDisallowedError("robots.txt disallows this URL", url=TELIA),
        # APPLE_SE has no route: FixtureFetcher raises FetchError(404).
    }
    meli, telia, apple = report(text, routes, catalog, bundles, tmp_path)
    assert (meli["status"], meli["readability"], meli["http_status"]) == ("blocked", "blocked", 403)
    assert meli["mode_reason"].endswith("no se evade; comprobación manual")
    assert (telia["status"], telia["readability"]) == ("robots_disallowed", "robots_disallowed")
    assert "se respeta" in telia["mode_reason"]
    assert (apple["status"], apple["readability"]) == ("failed", "failed")
    assert apple["http_status"] == 404
    for row in (meli, telia, apple):
        assert row["suggested_mode"] == "manual"
        assert row["raw_file"] is None and row["unexpected"] is False
    assert not (tmp_path / "raw" / "bundles").exists()


def test_walls_on_a_200_page(catalog, bundles, tmp_path: Path) -> None:
    login = "https://www.mercadolibre.com.co/jms/mco/lgz/login?go=planes"
    challenge = "<html><head><title>Just a moment...</title></head><body>Checking</body></html>"
    text = f"meli_plus_co_total CO {MELI}\napple_one_individual_se SE {APPLE_SE}"
    routes = {
        MELI: html(MELI, "<html><body><form>Ingresa</form></body></html>", final_url=login),
        APPLE_SE: html(APPLE_SE, challenge),
    }
    meli, apple = report(text, routes, catalog, bundles, tmp_path)
    assert (meli["status"], meli["wall"], meli["readability"]) == ("ok", "login", "blocked")
    assert meli["same_site"] is True
    assert (apple["wall"], apple["readability"]) == ("cloudflare", "blocked")
    assert "muro (cloudflare): no se evade" in apple["mode_reason"]


def test_a_wall_decides_even_with_amounts_or_on_another_host(
    catalog, bundles, tmp_path: Path
) -> None:
    same_site_login = "https://www.mercadolibre.com.co/jms/mco/lgz/login?go=planes"
    central_login = "https://www.mercadolibre.com/jms/mco/lgz/msl/login?go=planes"
    sso = "https://accounts.google.com/AccountChooser?continue=https://one.google.com/"
    one = "https://one.google.com/about/plans"
    login_page = "<html><body><h1>Ingresa</h1><p>Suscríbete a Meli+ Total desde $ 24.900 / mes</p>"
    text = (
        f"meli_plus_co_total CO {MELI}\nmeli_plus_co_esencial CO {MELI}?x=1\n"
        f"google_one CO {one}\ntelia_se_obegransad_streaming SE {TELIA}"
    )
    routes = {
        MELI: html(MELI, login_page, final_url=same_site_login),
        f"{MELI}?x=1": html(f"{MELI}?x=1", login_page, final_url=central_login),
        one: html(one, "<html><body><p>Elige una cuenta</p></body></html>", final_url=sso),
        TELIA: html(TELIA, TELIA_PAGE, headers={"cf-mitigated": "challenge"}),
    }
    same, central, google, telia = report(text, routes, catalog, bundles, tmp_path)
    for row, wall in (
        (same, "login"),
        (central, "login"),
        (google, "login"),
        (telia, "cloudflare"),
    ):
        assert (row["wall"], row["readability"], row["suggested_mode"]) == (
            wall,
            "blocked",
            "manual",
        )
        assert row["mode_reason"] == f"muro ({wall}): no se evade; comprobación manual"
    # A sign-in page on another site is a login wall, not a geo redirect; a sign-in host
    # (accounts.google.com) is one whatever its path.
    assert (central["same_site"], google["same_site"]) == (False, True)
    # The amounts behind the wall stay in the row for the human check, but decide nothing.
    assert same["amount_lines"] == ["Suscríbete a Meli+ Total desde $ 24.900 / mes"]
    assert telia["amount_lines_total"] > 0
    for row in (same, central, telia):
        assert any("detrás del muro" in n for n in row["notes"])


def test_scripts_on_a_normal_page_are_not_a_wall(catalog, bundles, tmp_path: Path) -> None:
    scripts = (
        '<script src="https://www.google.com/recaptcha/api.js"></script>'
        '<script src="https://js.datadome.co/tags.js"></script>'
        '<script src="/cdn-cgi/challenge-platform/scripts/jsd/main.js"></script>'
        "<script>window._pxAppId = 'PXabc';</script>"
    )
    spa = SPA_PAGE.replace("</head>", scripts + "</head>")
    data = json.dumps({"plans": [{"name": "Meli+ Total", "price": 24900}]})
    full = MELI_PAGE.replace("$ 24.900 / mes", "Precio al elegir").replace("$&nbsp;9.900 / mes", "")
    full = full.replace("</head>", f'<script id="__NEXT_DATA__" type="application/json">{data}'
                        "</script></head>")  # fmt: skip
    challenge = "<html><head><title>Meli+</title></head><body><p>Press &amp; Hold</p></body></html>"
    other = f"{MELI}?b=1"
    third = f"{MELI}?c=1"
    text = f"meli_plus_co_total CO {MELI}\nmeli_plus_co_total CO {other}\nmeli_x CO {third}"
    routes = {
        MELI: html(MELI, spa),
        other: html(other, full, headers={"x-datadome": "protected", "server": "nginx"}),
        third: html(third, challenge),
    }
    spa_row, full_row, wall_row = report(text, routes, catalog, bundles, tmp_path)
    assert spa_row["thin_page"] is True and full_row["thin_page"] is False
    for row in (spa_row, full_row):
        assert (row["wall"], row["readability"]) == (None, "embedded_only")
        assert row["embedded_amount_total"] > 0
    # A challenge in the visible text of a thin page is still a wall.
    assert (wall_row["wall"], wall_row["readability"]) == ("perimeterx", "blocked")


def test_a_foreign_dollar_price_is_not_read_as_cop(catalog, bundles, tmp_path: Path) -> None:
    co = "https://www.apple.com/co/apple-one/"
    us_page = "<html><body><h1>Apple One</h1><p>$19.95/mo</p><p>$25.95/mo</p></body></html>"
    co_page = "<html><body><h1>Apple One</h1><p>$ 19.900 al mes</p></body></html>"
    text = f"apple_one_co CO {co}\napple_one_co CO {co}?ip=1\napple_one_co CO {co}?kept=1"
    routes = {
        # Geo redirect on the same site: /co/ is gone from the path.
        co: html(co, co_page, final_url="https://www.apple.com/apple-one/"),
        # No redirect, but the page shows the runner's US price (a site that localises by IP).
        f"{co}?ip=1": html(f"{co}?ip=1", us_page),
        f"{co}?kept=1": html(f"{co}?kept=1", co_page, final_url=co + "plans/"),
    }
    redirected, by_ip, kept = report(text, routes, catalog, bundles, tmp_path)
    assert (redirected["same_site"], redirected["lost_country_segment"]) == (True, "co")
    assert (redirected["readability"], redirected["suggested_mode"]) == ("wrong_country", "manual")
    assert redirected["mode_reason"] == "la redirección quitó el segmento de país /co/ de la URL"
    # A bare "$" amount far too small for COP is not counted as COP.
    assert (by_ip["readability"], by_ip["suggested_mode"]) == ("wrong_country", "manual")
    assert (by_ip["amount_lines"], by_ip["other_currencies"]) == ([], {"?": 2})
    assert any('2 importe(s) con "$" por debajo de 1000' in n for n in by_ip["notes"])
    # A redirect that keeps the country segment, with a COP-sized "$" amount, stays readable.
    assert (kept["lost_country_segment"], kept["readability"]) == (None, "readable")
    assert kept["amount_lines"] == ["$ 19.900 al mes"]


def test_redirect_to_another_site_is_wrong_country(catalog, bundles, tmp_path: Path) -> None:
    routes = {TELIA: html(TELIA, TELIA_PAGE, final_url="https://www.telia.com/en/")}
    row = row_for(f"telia_se_obegransad_streaming SE {TELIA}", routes, catalog, bundles, tmp_path)
    assert (row["same_site"], row["final_host"]) == (False, "www.telia.com")
    assert row["readability"] == "wrong_country"
    assert row["mode_reason"] == "el host final www.telia.com no es el sitio pedido (telia.se)"
    assert "redirigida: la URL final no es la pedida" in row["notes"]


def test_amounts_only_in_another_currency_is_wrong_country(
    catalog, bundles, tmp_path: Path
) -> None:
    page = "<html><body><h1>Apple One</h1><p>US$ 19.95/mo</p><p>US$ 25.95/mo</p></body></html>"
    url = "https://www.apple.com/co/apple-one/"
    row = row_for(f"apple_one_co CO {url}", {url: page}, catalog, bundles, tmp_path)
    assert row["same_site"] is True
    assert row["readability"] == "wrong_country"
    assert row["other_currencies"] == {"USD": 2}
    assert row["mode_reason"] == "solo hay importes en otras monedas (USD), ninguno en COP"
    assert row["known_bundle"] is False and row["component_lines"] == []


def test_no_amounts(catalog, bundles, tmp_path: Path) -> None:
    page = "<html><body>" + "<p>Beneficios de la suscripción sin precio visible.</p>" * 20
    row = row_for(f"meli_terms CO {MELI}", {MELI: page + "</body></html>"}, catalog, bundles,
                  tmp_path)  # fmt: skip
    assert (row["readability"], row["thin_page"]) == ("no_amounts", False)
    assert row["mode_reason"] == "sin importes en COP en la página"


@pytest.mark.parametrize(
    ("content_type", "body"),
    [
        ("application/pdf", "%PDF-1.7\n%�� 17,99 € Netflix stream 4 0 obj"),
        ("application/octet-stream", "﻿%PDF-1.4\n\x00\x00 17,99 € Netflix"),
    ],
)
def test_pdf_is_pending_and_never_quoted(
    catalog, bundles, tmp_path: Path, content_type: str, body: str
) -> None:
    routes = {GO3: html(GO3, body, content_type=content_type)}
    row = row_for(f"go3_lt_filmai_netflix LT {GO3}", routes, catalog, bundles, tmp_path)
    assert (row["status"], row["readability"], row["pdf"]) == ("ok", "pdf_pending", True)
    assert row["suggested_mode"] == "manual" and "H4" in row["mode_reason"]
    assert (row["content_type"], row["http_status"], row["page_len"]) == (
        content_type,
        200,
        len(body),
    )
    assert row["page_title"] == "" and quoted_chars(row) == 0
    assert row["raw_file"] is None and not (tmp_path / "raw" / "bundles").exists()
    md = probe_bundles.render_bundles_md([row], generated_at=NOW)
    assert "17,99" not in md and "Netflix stream" not in md
    assert "| go3_lt_filmai_netflix | LT | static3.go3.tv | 200 |" in md


def test_long_amount_lines_are_not_quoted_again_as_component_lines(
    catalog, bundles, tmp_path: Path
) -> None:
    filler = "lorem ipsum dolor sit amet " * 7
    lines = "".join(
        f"<p>{filler}Meli+ Total con HBO Max por $ 1{i}.900 / mes {filler}</p>" for i in range(3)
    )
    page = f"<html><body>{lines}</body></html>"
    row = row_for(f"meli_plus_co_total CO {MELI}", {MELI: page}, catalog, bundles, tmp_path)
    assert row["amount_lines_total"] == 3
    assert all(len(line) <= LINE_CHARS and line.startswith("…") for line in row["amount_lines"])
    assert row["component_lines"] == []


def test_script_ids_are_clipped_and_counted_in_the_budget(catalog, bundles, tmp_path: Path):
    scripts = "".join(
        f'<script type="application/json" id="{i:02d}{"X" * 3000}">{{}}</script>' for i in range(25)
    )
    page = f"<html><head><title>Meli+</title>{scripts}</head><body><p>$ 24.900</p></body></html>"
    row = row_for(f"meli_plus_co_total CO {MELI}", {MELI: page}, catalog, bundles, tmp_path)
    ids = row["json_script_ids"]
    assert len(ids) == MAX_SCRIPT_IDS
    assert all(len(i) <= PATH_CHARS and i.endswith("…") for i in ids)
    assert quoted_chars(row) == len("Meli+") + len("$ 24.900") + sum(map(len, ids))
    assert quoted_chars(row) <= QUOTE_BUDGET


def test_quote_budget_is_respected(catalog, bundles, tmp_path: Path) -> None:
    filler = "lorem ipsum dolor " * 20
    lines = "".join(
        f"<p>{filler} Disney+ plan {i}: $ {i}.900 / mes {filler}</p>" for i in range(10, 50)
    )
    data = json.dumps({"plans": [{"price": i, "label": f"$ {i}.900 {filler}"} for i in range(30)]})
    page = (
        f"<html><head><title>{'Título larguísimo ' * 30}</title>"
        f'<script id="__NEXT_DATA__" type="application/json">{data}</script></head>'
        f"<body>{lines}<p>{filler} HBO Max {filler}</p></body></html>"
    )
    row = row_for(f"meli_plus_co_total CO {MELI}", {MELI: page}, catalog, bundles, tmp_path)
    assert row["amount_lines_total"] == 40
    assert 0 < len(row["amount_lines"]) <= MAX_AMOUNT_LINES
    assert quoted_chars(row) <= QUOTE_BUDGET
    assert len(row["page_title"]) <= 100
    for line in row["amount_lines"] + row["component_lines"] + row["embedded_amount_snippets"]:
        assert len(line) <= LINE_CHARS
    # Long lines are cut around the amount, and the cut is marked.
    assert row["amount_lines"][0].startswith("…") and "$ 10.900 / mes" in row["amount_lines"][0]
    md = probe_bundles.render_bundles_md([row], generated_at=NOW)
    assert filler * 2 not in md
    assert (tmp_path / row["raw_file"]).read_text(encoding="utf-8") == page


def test_transient_errors_are_retried_once(catalog, bundles, tmp_path: Path) -> None:
    class Flaky:
        def __init__(self) -> None:
            self.calls = 0

        async def fetch(self, url: str, *, accept: str = "text/html", egress: str = "default"):
            self.calls += 1
            assert (accept, egress) == ("text/html", "default")
            if self.calls == 1:
                raise TransientFetchError("HTTP 503", status=503, url=url)
            return html(url, MELI_PAGE)

        async def aclose(self) -> None:
            return None

    fetcher = Flaky()
    rows = report(f"meli_plus_co_total CO {MELI}", {}, catalog, bundles, tmp_path, fetcher=fetcher)
    assert rows[0]["readability"] == "readable"
    assert fetcher.calls == 2


def test_a_429_is_not_retried(catalog, bundles, tmp_path: Path) -> None:
    calls: list[str] = []

    class Busy:
        async def fetch(self, url: str, *, accept: str = "text/html", egress: str = "default"):
            calls.append(url)
            raise TransientFetchError("HTTP 429 (retry-after 120)", status=429, url=url)

        async def aclose(self) -> None:
            return None

    rows = report(f"meli_plus_co_total CO {MELI}", {}, catalog, bundles, tmp_path, fetcher=Busy())
    assert (rows[0]["status"], rows[0]["http_status"], rows[0]["readability"]) == (
        "failed",
        429,
        "failed",
    )
    assert calls == [MELI]


def test_deeply_nested_pages_are_findings_not_crashes(catalog, bundles, tmp_path: Path) -> None:
    deep_json = MELI_PAGE.replace(
        "</head>", '<script type="application/ld+json">' + "[" * 100_000 + "</script></head>"
    )
    deep_html = "<html><body>" + "<div>" * 3000 + "$ 24.900" + "</div>" * 3000 + "</body></html>"
    other = f"{MELI}?deep=1"
    text = f"meli_plus_co_total CO {MELI}\nmeli_plus_co_total CO {other}"
    routes = {MELI: html(MELI, deep_json), other: html(other, deep_html)}
    json_row, html_row = report(text, routes, catalog, bundles, tmp_path)
    # A JSON blob too deep to parse is scanned as text; the rest of the page is still read.
    assert (json_row["readability"], json_row["unexpected"]) == ("readable", False)
    assert json_row["amount_lines"] == ["$ 24.900 / mes", "$ 9.900 / mes"]
    # HTML too deep to walk: the row failed (manual), and the job does not.
    assert (html_row["status"], html_row["readability"], html_row["unexpected"]) == (
        "failed",
        "failed",
        False,
    )
    assert html_row["error"].startswith("RecursionError: ")


def test_analysis_crash_is_an_unexpected_row(catalog, bundles, tmp_path: Path, monkeypatch):
    def boom(text):
        raise KeyError("lexbor")

    monkeypatch.setattr(probe_bundles, "page_lines", boom)
    row = row_for(f"meli_plus_co_total CO {MELI}", {MELI: MELI_PAGE}, catalog, bundles, tmp_path)
    assert (row["status"], row["readability"], row["unexpected"]) == ("error", "failed", True)
    assert row["error"] == "KeyError: 'lexbor'"


# --------------------------------------------------------------------------- markdown


def test_code_spans() -> None:
    assert _code("$ 24.900 | *mes*") == "`$ 24.900 | *mes*`"
    # A line break would end the span: whitespace runs become one space.
    assert _code("a\n# [x](https://evil.example)\r\n\tb") == "`a # [x](https://evil.example) b`"
    assert _code("a `b` c") == "``a `b` c``"
    assert _code("`x`") == "`` `x` ``"


def test_markdown_matrix_escapes_pipes_and_quotes_lines_as_code(
    catalog, bundles, tmp_path: Path
) -> None:
    page = (
        "<html><head><title>Planes | Meli+ <b>2026</b></title></head><body>"
        "<p>Total | $ 24.900 / mes | *IVA* `incluido` &lt;b&gt;x&lt;/b&gt;</p></body></html>"
    )
    routes = {MELI: html(MELI, page, content_type="text/html|weird")}
    row = row_for(f"meli_plus_co_total CO {MELI}", routes, catalog, bundles, tmp_path)
    md = probe_bundles.render_bundles_md([row], generated_at=NOW)
    table = [ln for ln in md.splitlines() if ln.startswith("| ")]
    # Header + one row, each with exactly 9 unescaped pipes (8 columns).
    assert len(table) == 2
    assert all(len(re.findall(r"(?<!\\)\|", ln)) == 9 for ln in table)
    assert "text/html\\|weird" in table[1]
    # Quoted page text is a code span: rendered verbatim, never as Markdown or HTML.
    assert "  - ``Total | $ 24.900 / mes | *IVA* `incluido` <b>x</b>``" in md
    assert "- Título: `Planes | Meli+ <b>2026</b>`" in md  # <title> text is literal


def test_markdown_never_renders_vendor_text(catalog, bundles, tmp_path: Path) -> None:
    evil_key = "x\n# [Click me](https://evil.example) <img src=x>"
    data = json.dumps({evil_key: {"price": 24900}})
    page = (
        "<html><head><title>Meli+</title>"
        f'<script type="application/json" id="d">{data}</script>'
        '<script type="application/json" id="a\n# [Pwn](https://evil.example)">{}</script>'
        "</head><body><div id=root></div></body></html>"
    )
    final = "https://www.mercadolibre.com.co/p?x=[Click](https://evil.example)<img src=https://evil.example/a.png>"
    ctype = "text/html <img src=https://evil.example/b.png>"
    broken = f"{MELI}?b=1"
    routes = {
        MELI: html(MELI, page, final_url=final, content_type=ctype),
        broken: FetchError("unsupported URL: '[x](https://evil.example)<b>'", url=broken),
    }
    text = f"meli_plus_co_total CO {MELI}\nmeli_plus_co_total CO {broken}"
    rows = report(text, routes, catalog, bundles, tmp_path)
    md = probe_bundles.render_bundles_md(rows, generated_at=NOW)
    lines = md.splitlines()
    # No vendor text starts a line of its own (a heading, a list item, an HTML block).
    assert all(
        not ln.startswith("#") or ln.startswith(("# Probe de paquetes", "## Detalles", "### "))
        for ln in lines
    )
    assert "  - `d x # [Click me](https://evil.example) <img src=x>.price: 24900`" in lines
    assert "- Scripts JSON: `d`, `a # [Pwn](https://evil.example)`" in lines
    assert f"- URL: `{MELI}` -> `{final}`" in lines
    assert f"`{ctype}`" in next(ln for ln in lines if ln.startswith("| meli_plus_co_total"))
    assert any(ln.startswith("- Estado: ok, HTTP 200, `text/html <img") for ln in lines)
    # Reasons carry vendor error text: rendered literally, every link or tag character escaped.
    assert (
        "- Modo sugerido: **manual**: la descarga o el análisis falló (FetchError: unsupported "
        "URL: '\\[x\\](https://evil.example)\\<b\\>')"
    ) in lines
    # Every occurrence of the vendor's link text sits inside a code span or is escaped.
    for ln in lines:
        for hit in re.finditer(r"(?<!\\)\[(?:Click|Pwn|x)", ln):
            assert ln[: hit.start()].count("`") % 2 == 1, ln


def test_run_probe_bundles_mode(catalog, tmp_path: Path) -> None:
    targets = tmp_path / "bundle_targets.txt"
    targets.write_text(
        f"meli_plus_co_total CO {MELI}\ntelia_se_obegransad_streaming SE {TELIA}\n"
        f"go3_lt_filmai_netflix LT {GO3}\n",
        encoding="utf-8",
    )
    fetcher = FixtureFetcher(
        {
            MELI: MELI_PAGE,
            TELIA: BlockedError("bot wall (akamai) HTTP 403", status=403, url=TELIA),
            GO3: html(GO3, "%PDF-1.7 binary", content_type="application/pdf"),
        }
    )
    out = io.StringIO()
    out_dir = tmp_path / "probe-out"
    code = run_probe(targets, out_dir, "bundles", fetcher=fetcher, catalog=catalog, now=NOW,
                     out=out)  # fmt: skip
    assert code == 0
    summary = json.loads((out_dir / "summary.json").read_text(encoding="utf-8"))
    assert summary["mode"] == "bundles"
    assert summary["quote_budget"] == QUOTE_BUDGET
    assert summary["readability_totals"] == {"blocked": 1, "pdf_pending": 1, "readable": 1}
    assert summary["status_totals"] == {"blocked": 1, "ok": 2}
    assert set(summary["readability_totals"]) <= set(READABILITY)
    assert [r["label"] for r in summary["targets"]] == [
        "meli_plus_co_total",
        "telia_se_obegransad_streaming",
        "go3_lt_filmai_netflix",
    ]
    md = (out_dir / "summary.md").read_text(encoding="utf-8")
    assert out.getvalue() == md
    assert md.startswith("# Probe de paquetes (2026-10-09 06:00 UTC)")
    assert "**3 objetivos**: blocked 1, pdf_pending 1, readable 1" in md
    assert (
        "| Etiqueta | CC | Host | HTTP | Tipo | Legibilidad | Modo sugerido | Líneas con importe |"
    ) in md
    meli_row = "| meli_plus_co_total | CO | www.mercadolibre.com.co | 200 | `text/html` |"
    assert meli_row + " readable | auto | 2 |" in md
    assert "### 2. telia_se_obegransad_streaming (SE): blocked" in md
    # Raw pages stay in <out>/raw/; the summaries never carry the whole page.
    assert [p.name for p in (out_dir / "raw" / "bundles").iterdir()] == [
        "01_meli_plus_co_total_CO.html"
    ]
    assert "Términos y condiciones" not in md and "Términos y condiciones" not in json.dumps(
        summary, ensure_ascii=False
    )


def test_run_probe_bundles_exits_1_on_an_unexpected_error(catalog, tmp_path: Path) -> None:
    targets = tmp_path / "t.txt"
    targets.write_text(f"meli_plus_co_total CO {MELI}\n", encoding="utf-8")
    fetcher = FixtureFetcher({MELI: ValueError("bad url")})
    code = run_probe(targets, tmp_path / "o", "bundles", fetcher=fetcher, catalog=catalog,
                     out=io.StringIO())  # fmt: skip
    assert code == 1
    (row,) = json.loads((tmp_path / "o" / "summary.json").read_text())["targets"]
    assert (row["status"], row["unexpected"]) == ("error", True)
    assert row["error"] == "ValueError: bad url"


def test_run_probe_bundles_rejects_bad_targets_before_fetching(catalog, tmp_path: Path) -> None:
    targets = tmp_path / "t.txt"
    targets.write_text("meli_plus_co_total CO http://www.mercadolibre.com.co/\n", encoding="utf-8")
    fetcher = FixtureFetcher()
    with pytest.raises(ValueError, match="línea 1"):
        run_probe(targets, tmp_path / "o", "bundles", fetcher=fetcher, catalog=catalog)
    assert fetcher.calls == []

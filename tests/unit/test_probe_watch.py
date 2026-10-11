"""haggler probe --mode watch: the bundle watcher on canned pages, writing nothing (offline, with
FixtureFetcher and the bundle watcher's test helpers or a copy of data/curated).

The tests that read curated bundles use PINNED (a frozen bundles.yaml, wave 1 before the H10
Paso 3 edits) so a correct edit to the real data/curated/bundles.yaml does not break them; only
test_repo_watch_targets_are_wave_1 reads the live file."""

from __future__ import annotations

import hashlib
import io
import json
import re
import shutil
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Any

import pytest
import yaml
from test_bundle_watch import (
    ACME,
    CATALOG,
    DISNEY,
    INDEX,
    PDF18,
    PDF19,
    PRICE,
    TELCO_PRICE,
    VALIDITY,
    acme,
    acme_page,
    bundles,
    index_page,
    pdf,
    telco,
)

from conftest import FixtureFetcher
from haggler import probe_watch
from haggler.catalog import integrity_errors, load_bundles
from haggler.config import Settings, repo_root
from haggler.fetch.errors import BlockedError, FetchError
from haggler.models.curated import BundlesFile
from haggler.probe import run_probe
from haggler.probe_watch import (
    ANCHOR_TEXT_CHARS,
    QUOTE_BUDGET,
    OfflineData,
    in_memory_watch,
    parse_watch_targets,
    select_bundles,
)

PINNED = Path(__file__).parent / "fixtures" / "probe_watch" / "curated"
NOW = datetime(2026, 10, 12, 6, 0, tzinfo=UTC)
TODAY = NOW.date()
WAVE_1 = [
    "meli_plus_co_total",
    "meli_plus_co_esencial",
    "apple_one_individual_se",
    "apple_one_family_se",
    "telia_se_obegransad_streaming",
]

# The official Meli+ CO plans page as the bundles probe read it (H0), with an Esencial discount.
PLANES = "https://www.mercadolibre.com.co/suscripciones/melimas/planes"
PRICE_QUOTE = (
    "Precio actual con 9 por ciento de descuento: 19900 pesos por mes, por 2 meses. "
    "Precio anterior: $ 21.900 pesos por mes."
)
DISCOUNTS = "30% OFF en HBO Max, Universal+, Paramount+ y ViX Premium."
ESENCIAL_HBO = "Descuento del 30% en HBO Max."
FOOTER = "Términos y condiciones de Mercado Libre Colombia, válidos hasta agotar existencias."
PLANES_HTML = f"""<html><body><main>
<section><h2>Meli+ Total</h2>
<p>$ 19.900/mes por 2 meses</p>
<p>{PRICE_QUOTE}</p>
<ul><li>{DISNEY}</li><li>{DISCOUNTS}</li></ul>
</section>
<section><h2>Meli+ Esencial</h2><p>$ 9.900/mes</p><ul><li>{ESENCIAL_HBO}</li></ul></section>
<footer><p>{FOOTER}</p></footer>
</main></body></html>"""


def probe(
    bf: BundlesFile | None,
    routes: dict[str, Any],
    tmp_path: Path,
    targets: str = "",
    *,
    data_dir: Path | None = None,
    offline: OfflineData | None = None,
) -> tuple[dict[str, Any], str, FixtureFetcher]:
    path = tmp_path / "watch_targets.txt"
    path.write_text(targets, encoding="utf-8")
    fetcher = FixtureFetcher(routes)
    summary, md = probe_watch.run(
        path,
        tmp_path / "out",
        Settings(),
        catalog=CATALOG,
        now=NOW,
        fetcher=fetcher,
        data_dir=data_dir or tmp_path / "no-data",
        bundles=bf,
        offline=offline or (OfflineData() if bf is not None else None),
    )
    return summary, md, fetcher


def by_id(summary: dict[str, Any]) -> dict[str, dict[str, Any]]:
    return {r["bundle_id"]: r for r in summary["bundles"]}


def curated_copy(tmp_path: Path) -> Path:
    """A copy of data/curated with the pinned bundles.yaml (module docstring)."""
    data = tmp_path / "data"
    shutil.copytree(repo_root() / "data" / "curated", data / "curated")
    for pinned in PINNED.iterdir():
        shutil.copy(pinned, data / "curated" / pinned.name)
    return data


def tree_hashes(root: Path) -> dict[str, str]:
    return {
        str(p.relative_to(root)): hashlib.sha256(p.read_bytes()).hexdigest()
        for p in sorted(root.rglob("*"))
        if p.is_file()
    }


def manual(row: dict[str, Any]) -> dict[str, Any]:
    return {**row, "watch": {**row["watch"], "mode": "manual"}}


# --------------------------------------------------------------------------- targets


def test_parse_watch_targets(bundles_file: BundlesFile) -> None:
    text = (
        "# wave 1\n"
        "meli_plus_co_total  # the plans page\n"
        "meli_plus_co_esencial; apple_one_individual_se,apple_one_family_se\n"
        "\n"
        "meli_plus_co_total\n"
    )
    assert parse_watch_targets(text, bundles_file) == WAVE_1[:4]
    assert parse_watch_targets("# nothing\n\n", bundles_file) == []


def test_parse_watch_targets_reports_every_unknown_id(bundles_file: BundlesFile) -> None:
    with pytest.raises(ValueError) as err:
        parse_watch_targets(
            "meli_plus_co_total\nmeli_plus_co_totl\n\nnope; apple_one\n", bundles_file
        )
    message = str(err.value)
    assert "línea 2: paquete desconocido 'meli_plus_co_totl'" in message
    assert "línea 4: paquete desconocido 'nope'" in message
    assert "línea 4: paquete desconocido 'apple_one'" in message


def test_no_targets_means_every_bundle_with_watch_pages(bundles_file: BundlesFile) -> None:
    assert select_bundles([], bundles_file) == [
        b.bundle_id for b in bundles_file.bundles if b.watch.pages
    ]
    assert sorted(select_bundles([], bundles_file)) == sorted(WAVE_1)
    assert select_bundles(["apple_one_family_se"], bundles_file) == ["apple_one_family_se"]


def test_repo_watch_targets_are_wave_1() -> None:
    # The live bundles.yaml: parsing fails on an id it does not have.
    text = (repo_root() / "probe" / "watch_targets.txt").read_text(encoding="utf-8")
    assert set(WAVE_1) <= set(parse_watch_targets(text, load_bundles()))


@pytest.fixture(scope="module")
def bundles_file() -> BundlesFile:
    return load_bundles(PINNED)


def test_in_memory_watch_switches_only_the_targets(bundles_file: BundlesFile) -> None:
    before = bundles_file.model_dump(mode="json")
    watched, skipped = in_memory_watch(bundles_file, ["meli_plus_co_total"], TODAY)
    modes = {b.bundle_id: b.watch.mode for b in watched.bundles}
    assert modes["meli_plus_co_total"] == "auto"
    assert set(modes.values()) == {"auto", "off"}
    assert skipped == {}
    # The curated rows are untouched.
    assert bundles_file.model_dump(mode="json") == before


# --------------------------------------------------------------------------- the report


def test_a_manual_bundle_is_watched_as_auto_and_its_anchors_verified(tmp_path: Path) -> None:
    bf = bundles(manual(acme()))
    summary, md, fetcher = probe(bf, {ACME: acme_page()}, tmp_path)
    assert fetcher.calls == [ACME]
    assert bf.bundles[0].watch.mode == "manual"  # in memory only
    (row,) = summary["bundles"]
    assert (row["yaml_mode"], row["watched_as"], row["outcome"]) == ("manual", "auto", "ok")
    (page,) = row["pages"]
    assert (page["status"], page["http"], page["final_url"], page["wall"]) == (
        "ok",
        200,
        ACME,
        None,
    )
    assert page["raw_file"].startswith("raw/watch/01_www.acme.com.co_planes")
    assert [(a["fact"], a["service_id"], a["status"], a["via"]) for a in row["anchors"]] == [
        ("price", None, "verified", "lines"),
        ("intro", None, "verified", "lines"),
        ("component", "disney_plus", "verified", "lines"),
        ("component", "hbo_max", "verified", "lines"),
    ]
    # The YAML's anchor texts, clipped to ANCHOR_TEXT_CHARS (these fit).
    assert [a["text"] for a in row["anchors"][::2]] == [PRICE, DISNEY]
    assert row["state"] == {
        "display_status": "verified",
        "reasons_es": [],
        "confidence": "high",
        "price_basis": "curated",
        "notes": [],
    }
    assert row["hints"] == [] and row["candidates"] == [] and row["facts_without_candidate"] == []
    assert summary["outcome_totals"] == {"ok": 1}
    assert summary["display_totals"] == {"verified": 1}
    assert "| acme_total | CO | manual | ok | verified | high | 1/1 | 4/4 | 0 | 0 |" in md
    assert "- Modo en el YAML: manual (aquí, `auto` solo en memoria)" in md


def test_a_forbidden_provider_is_reported_and_never_fetched(tmp_path: Path) -> None:
    bf = bundles(acme(), acme="forbidden")
    summary, md, fetcher = probe(bf, {ACME: acme_page()}, tmp_path)
    assert fetcher.calls == []
    (row,) = summary["bundles"]
    assert (row["outcome"], row["watched_as"]) == ("skipped", None)
    assert "prohíben el acceso automático" in row["skipped"]
    assert row["pages"] == [] and row["state"]["display_status"] is None
    assert "- No se lee: los términos del proveedor prohíben el acceso automático" in md


def test_only_the_targets_are_fetched(tmp_path: Path) -> None:
    # telco is auto in its YAML: the probe still reads only what the targets name.
    bf = bundles(acme(), telco())
    summary, _md, fetcher = probe(bf, {ACME: acme_page()}, tmp_path, "acme_total\n")
    assert fetcher.calls == [ACME]
    assert [r["bundle_id"] for r in summary["bundles"]] == ["acme_total"]


def test_a_bundle_whose_pages_fail_is_a_finding(tmp_path: Path) -> None:
    bf = bundles(acme())
    routes = {ACME: BlockedError("bot wall (akamai) HTTP 403", status=403, url=ACME)}
    summary, md, _ = probe(bf, routes, tmp_path)
    (row,) = summary["bundles"]
    assert (row["outcome"], row["unexpected"]) == ("unreadable", False)
    assert [p["status"] for p in row["pages"]] == ["blocked"]
    assert {a["status"] for a in row["anchors"]} == {"unreadable"}
    assert row["state"]["display_status"] == "unverified"
    assert (
        "acme.com.co bloquea la lectura automática; no se confirma desde ahí."
        in (row["state"]["reasons_es"])
    )
    assert row["candidates"] == []
    assert row["facts_without_candidate"] == [
        "price",
        "intro",
        "component disney_plus",
        "component hbo_max",
    ]
    assert "**blocked**, HTTP 403" in md


def test_a_login_wall_that_answered_200_is_reported_as_a_wall(tmp_path: Path) -> None:
    verify = "https://www.acme.com.co/gz/account-verification?go=planes"
    page = "<html><body><main><h1>Verifica tu cuenta</h1><p>Ingresa.</p></main></body></html>"
    bf = bundles(acme())
    routes = {ACME: probe_html(ACME, page, final_url=verify)}
    summary, _md, _ = probe(bf, routes, tmp_path)
    (row,) = summary["bundles"]
    (p,) = row["pages"]
    assert (p["status"], p["wall"], p["final_url"]) == ("blocked", "login", verify)
    assert row["outcome"] == "unreadable"


def probe_html(url: str, text: str, *, final_url: str | None = None) -> Any:
    from haggler.fetch import RawResponse

    return RawResponse(url=url, final_url=final_url or url, status_code=200, text=text)


def test_hints_come_with_the_review_snippet_and_an_offline_checked_text(tmp_path: Path) -> None:
    data = curated_copy(tmp_path)
    summary, md, fetcher = probe(
        None,
        {PLANES: PLANES_HTML},
        tmp_path,
        "meli_plus_co_total; meli_plus_co_esencial",
        data_dir=data,
    )
    assert fetcher.calls == [PLANES]  # one fetch for both tiers
    rows = by_id(summary)
    total, esencial = rows["meli_plus_co_total"], rows["meli_plus_co_esencial"]
    assert total["yaml_mode"] == "manual" and total["outcome"] == "ok"
    hints = {h["field"]: h for h in total["hints"]}
    assert set(hints) == {"bundle_price", "bundle_intro_price"}
    price, intro = hints["bundle_price"], hints["bundle_intro_price"]
    assert (price["role"], price["value"], price["line"]) == (
        "regular",
        "21900 COP/month",
        PRICE_QUOTE,
    )
    # The line names no tier: the anchor text runs from the "Meli+ Total" heading.
    assert price["anchor_text"] == f"Meli+ Total\n$ 19.900/mes por 2 meses\n{PRICE_QUOTE}"
    assert intro["anchor_text"] == "Meli+ Total\n$ 19.900/mes por 2 meses"
    for h in (price, intro):
        assert h["page_check"]["status"] == "ok"
        assert h["anchor_check"]["status"] == "ok"
        assert h["proposal_id"].startswith("watch:meli_plus_co_total:") and not h["rejected"]
    # The snippet is the review CLI's (bundles.proposals.snippet), with the manual-mode note.
    head = "# data/curated/bundles.yaml, paquete meli_plus_co_total: price.amount"
    assert price["snippet"].startswith(head)
    assert "# nota: watch.mode es manual" in price["snippet"]
    pasted = yaml.safe_load(price["snippet"])
    assert pasted["price"] == {"amount": "21900"}
    assert pasted["watch"]["anchors"] == [
        {"fact": "price", "url": PLANES, "text": price["anchor_text"]}
    ]
    # The site would show it for review: the YAML no longer says what the page says.
    assert total["state"]["display_status"] == "needs_review"
    assert total["state"]["reasons_es"][0].startswith("Cambios pendientes de revisión")
    # Would-be anchors: the hint's text for the price, the page lines for the components.
    cands = [(c["fact"], c["service_id"], c["source"], c["text"]) for c in total["candidates"]]
    assert cands == [
        ("price", None, "hint", price["anchor_text"]),
        ("intro", None, "hint", intro["anchor_text"]),
        ("component", "disney_plus", "yaml", DISNEY),
        ("component", "hbo_max", "yaml", DISCOUNTS),
        ("component", "paramount_plus", "yaml", DISCOUNTS),
        ("component", "vix", "yaml", DISCOUNTS),
    ]
    # The hint's snippet pastes those.
    assert [c["snippet"] is None for c in total["candidates"][:3]] == [True, True, False]
    # Esencial: its own card only (Total's discount line is never offered to it).
    assert esencial["hints"] == []
    assert [(c["fact"], c["text"]) for c in esencial["candidates"]] == [
        ("price", "Meli+ Esencial\n$ 9.900/mes"),
        ("component", ESENCIAL_HBO),
    ]
    assert yaml.safe_load(esencial["candidates"][0]["snippet"])["watch"]["anchors"] == [
        {"fact": "price", "url": PLANES, "text": "Meli+ Esencial\n$ 9.900/mes"}
    ]
    assert "Meli+ Esencial" in md and "````" not in md
    # Only anchors, hints and would-be anchors are quoted: never the rest of the page.
    blob = json.dumps(summary, ensure_ascii=False)
    assert FOOTER not in blob and FOOTER not in md


def _paste(row: dict[str, Any], snippet_text: str) -> dict[str, Any]:
    """A snippet merged into a bundles.yaml row the way a reviewer pastes it."""
    fragment = yaml.safe_load(snippet_text)
    out = dict(row)
    for key, value in fragment.items():
        if key == "price":
            out["price"] = {**out["price"], **value}
        elif key == "watch":
            w = dict(out["watch"])
            for part in ("pages", "anchors"):
                w[part] = [*w.get(part, []), *value.get(part, [])]
            out["watch"] = w
        else:
            out[key] = value
    return out


def test_the_output_pasted_as_printed_verifies_on_the_next_run(tmp_path: Path) -> None:
    data = curated_copy(tmp_path)
    first, _md, _ = probe(
        None, {PLANES: PLANES_HTML}, tmp_path, "meli_plus_co_total; meli_plus_co_esencial",
        data_dir=data,
    )  # fmt: skip
    path = data / "curated" / "bundles.yaml"
    raw = yaml.safe_load(path.read_text(encoding="utf-8"))
    for row in raw["bundles"]:
        report = by_id(first).get(row["bundle_id"])
        if report is None:
            continue
        for h in report["hints"]:
            row.update(_paste(row, h["snippet"]))
        for c in report["candidates"]:
            if c["snippet"] is not None:
                row.update(_paste(row, c["snippet"]))
        if "intro_amount" in row["price"]:
            row["price"]["intro_months"] = 2  # its own field (bundle_intro_months)
        row["watch"]["mode"] = "auto"
    edited = BundlesFile.model_validate(raw)
    errors = [e for e in integrity_errors(CATALOG, bundles=edited) if "meli_plus_co" in e]
    assert errors == []
    (tmp_path / "2").mkdir()
    again, md, _ = probe(edited, {PLANES: PLANES_HTML}, tmp_path / "2", "", data_dir=data)
    rows = by_id(again)
    for bundle_id in ("meli_plus_co_total", "meli_plus_co_esencial"):
        row = rows[bundle_id]
        assert row["outcome"] == "ok"
        assert {a["status"] for a in row["anchors"]} == {"verified"}
        assert row["hints"] == [] and row["candidates"] == []
        assert (row["state"]["display_status"], row["state"]["confidence"]) == ("verified", "high")
    assert "todas las anclas que se comprueban están verificadas" in md


def test_an_observed_price_without_the_daily_row_is_assumed_and_said(tmp_path: Path) -> None:
    data = curated_copy(tmp_path)
    apple = "https://www.apple.com/se/apple-one/"
    page = (
        "<html><body><main><h1>Apple One</h1><p>Det personliga abonnemanget kostar 215 kr i "
        "månaden och familjeabonnemanget 295 kr i månaden.</p></main></body></html>"
    )
    summary, _md, _ = probe(None, {apple: page}, tmp_path, "apple_one_individual_se", data_dir=data)
    (row,) = summary["bundles"]
    assert row["state"]["price_basis"] == "observed_assumed"
    assert row["state"]["notes"][0].startswith("Precio observado sin fila diaria")
    # An observed price needs no price anchor: only the components are would-be anchors.
    assert {c["fact"] for c in row["candidates"]} <= {"component"}
    assert "price" not in row["facts_without_candidate"]


def test_a_superseded_price_list_gets_its_edition_snippet(tmp_path: Path) -> None:
    bf = bundles(telco())
    routes = {INDEX: index_page(PDF18, PDF19), PDF19: pdf(PDF19, [TELCO_PRICE, VALIDITY])}
    summary, md, fetcher = probe(bf, routes, tmp_path)
    assert fetcher.calls == [INDEX, PDF19]  # the curated edition is not fetched
    (row,) = summary["bundles"]
    assert row["outcome"] == "new_document"
    index, doc = row["pages"]
    assert (index["kind"], index["status"]) == ("index", "ok")
    assert (doc["status"], doc["edition"], doc["newest_edition"], doc["newest_url"]) == (
        "superseded",
        "18",
        "19",
        PDF19,
    )
    assert doc["newest_status"] == "ok"
    assert {a["status"] for a in row["anchors"]} == {"outdated_document"}
    (edition,) = row["edition_snippets"]
    fragment = yaml.safe_load(edition["snippet"])
    assert fragment["watch"]["pages"][0]["url"] == PDF19
    assert {a["url"] for a in fragment["watch"]["anchors"]} == {PDF19}
    # The newest document's lines stating the YAML values, at the new edition's URL.
    price = next(c for c in row["candidates"] if c["fact"] == "price")
    assert (price["url"], price["text"], price["newest_edition"]) == (PDF19, TELCO_PRICE, "19")
    assert "pega antes el snippet de la edición nueva" in price["snippet"]
    assert row["state"]["display_status"] == "needs_review"
    assert "#### Edición nueva de la lista" in md


# --------------------------------------------------------------------------- budget, markdown


def _quoted(row: dict[str, Any]) -> int:
    texts = [h["line"] for h in row["hints"]] + [h["anchor_text"] for h in row["hints"]]
    texts += [c["text"] for c in row["candidates"]]
    for e in row["edition_snippets"]:
        texts += [a["text"] for a in yaml.safe_load(e["snippet"])["watch"].get("anchors", [])]
    unique = {t for t in texts if t}
    return sum(len(a["text"]) for a in row["anchors"]) + sum(len(t) for t in unique)


def test_the_quote_budget_bounds_every_bundle(tmp_path: Path) -> None:
    data = curated_copy(tmp_path)
    summary, _md, _ = probe(None, {PLANES: PLANES_HTML}, tmp_path, "", data_dir=data)
    for row in summary["bundles"]:
        assert row["quoted_chars"] == _quoted(row) <= QUOTE_BUDGET
    assert summary["quote_budget"] == QUOTE_BUDGET


def test_what_does_not_fit_the_budget_is_dropped_whole(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(probe_watch, "QUOTE_BUDGET", 150)
    data = curated_copy(tmp_path)
    summary, md, _ = probe(
        None, {PLANES: PLANES_HTML}, tmp_path, "meli_plus_co_total", data_dir=data
    )
    (row,) = summary["bundles"]
    assert row["quoted_chars"] == _quoted(row) <= 150
    assert row["dropped_by_budget"] > 0
    # Kept texts are whole (a cut one would not pass validate-catalog).
    for h in row["hints"]:
        assert h["line"] in PLANES_HTML and h["snippet"] is not None
    assert all(c["text"].split("\n")[-1] in PLANES_HTML for c in row["candidates"])
    assert "elemento(s) no caben y se omiten" in md


def test_a_hint_dropped_by_the_budget_still_offers_its_anchor_text(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # A budget with room for the anchors, the intro hint and the price hint's anchor_text, but
    # not for the price hint's line as well: the hint is dropped, its text is still offered.
    data = curated_copy(tmp_path)
    full, _md, _ = probe(None, {PLANES: PLANES_HTML}, tmp_path, "meli_plus_co_total", data_dir=data)
    (row,) = full["bundles"]
    hints = {h["field"]: h for h in row["hints"]}
    intro, price = hints["bundle_intro_price"], hints["bundle_price"]
    budget = (
        sum(len(a["text"]) for a in row["anchors"])
        + len(intro["line"])
        + len(intro["anchor_text"])
        + len(price["anchor_text"])
    )
    assert price["line"] not in price["anchor_text"].split("\n")[:-1]  # the line costs extra
    monkeypatch.setattr(probe_watch, "QUOTE_BUDGET", budget)
    (tmp_path / "2").mkdir()
    summary, md, _ = probe(
        None, {PLANES: PLANES_HTML}, tmp_path / "2", "meli_plus_co_total", data_dir=data
    )
    (row,) = summary["bundles"]
    assert not row["unexpected"] and row["error"] is None
    assert [h["field"] for h in row["hints"]] == ["bundle_intro_price"]
    cand = next(c for c in row["candidates"] if c["fact"] == "price")
    assert (cand["source"], cand["hint"], cand["text"]) == ("hint", None, price["anchor_text"])
    pasted = yaml.safe_load(cand["snippet"])
    assert pasted["watch"]["anchors"] == [
        {"fact": "price", "url": PLANES, "text": price["anchor_text"]}
    ]
    assert "# nota: el ancla confirma 21900 COP/month" in cand["snippet"]
    assert row["quoted_chars"] == _quoted(row) <= budget
    assert row["dropped_by_budget"] > 0
    assert "- price (`21900 COP/month`, de una pista sin snippet en este informe) en " in md


def test_a_report_bug_costs_one_bundle_its_details_not_the_summary(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    def broken(*_a: Any, **_kw: Any) -> Any:
        raise RuntimeError("render bug")

    monkeypatch.setattr(probe_watch, "_hint_md", broken)
    data = curated_copy(tmp_path)
    targets = tmp_path / "t.txt"
    targets.write_text("meli_plus_co_total; meli_plus_co_esencial\n", encoding="utf-8")
    fetcher = FixtureFetcher({PLANES: PLANES_HTML})
    code = run_probe(targets, tmp_path / "o", "watch", fetcher=fetcher, catalog=CATALOG,
                     now=NOW, out=io.StringIO(), data_dir=data)  # fmt: skip
    assert code == 1
    rows = by_id(json.loads((tmp_path / "o" / "summary.json").read_text(encoding="utf-8")))
    total, esencial = rows["meli_plus_co_total"], rows["meli_plus_co_esencial"]
    assert (total["unexpected"], total["error"]) == (True, "RuntimeError: render bug")
    assert total["hints"] and not esencial["unexpected"]  # Esencial has no hint to render
    md = (tmp_path / "o" / "summary.md").read_text(encoding="utf-8")
    assert "### 1. meli_plus_co_total: el informe falló" in md
    assert "- Error: `RuntimeError: render bug`" in md
    assert "### 2. meli_plus_co_esencial (CO): ok" in md


def test_quote_budget_clips_anchor_texts_and_drops_copyable_ones_whole() -> None:
    budget = probe_watch._QuoteBudget(300)
    clipped = budget.clip("x" * 200, ANCHOR_TEXT_CHARS)
    assert len(clipped) == ANCHOR_TEXT_CHARS and clipped.endswith("…")
    assert budget.whole("a" * 100, "b" * 50)
    assert budget.whole("a" * 100)  # quoted already: free
    assert not budget.whole("c" * 31)  # 30 left: dropped whole, never cut
    assert (budget.used, budget.left, budget.dropped) == (270, 30, 1)


def _outside_fences(md: str) -> list[str]:
    out, fence = [], None
    for line in md.splitlines():
        m = re.match(r"^(`{3,})", line)
        if fence is None and m:
            fence = m.group(1)
        elif fence is not None and line == fence:
            fence = None
        elif fence is None:
            out.append(line)
    return out


def test_markdown_never_renders_vendor_text(tmp_path: Path) -> None:
    evil = "``` [Click](https://evil.example) <img src=x> | fin"
    changed = (
        PRICE.replace("21.900", "23.900") + " " + evil.replace("<", "&lt;").replace(">", "&gt;")
    )
    bf = bundles(acme(), telco())
    broken = FetchError("bad '[x](https://evil.example)<b>'", status=500, url=INDEX)
    routes = {ACME: acme_page(changed), INDEX: broken, PDF18: broken}
    summary, md, _ = probe(bf, routes, tmp_path)
    rows = by_id(summary)
    (hint,) = rows["acme_total"]["hints"]
    assert evil in hint["line"] and hint["snippet"] is not None
    # A snippet with a run of 3 backticks gets a longer fence.
    assert "````yaml" in md
    lines = _outside_fences(md)
    headings = ("# Probe del vigilante", "## Detalles", "### ", "#### ")
    assert all(not ln.startswith("#") or ln.startswith(headings) for ln in lines)
    # Every vendor link outside a fence sits in a code span, or is escaped.
    for ln in lines:
        for hit in re.finditer(r"(?<!\\)\[(?:Click|x)\]", ln):
            assert ln[: hit.start()].count("`") % 2 == 1, ln
    assert any(ln.startswith("  - Línea: ````Precio actual") for ln in lines)
    assert "; `bad '[x](https://evil.example)<b>'`" in md  # a fetch error's text
    # Table cells stay cells.
    table = [ln for ln in lines if ln.startswith("| ")]
    assert all(len(re.findall(r"(?<!\\)\|", ln)) == 11 for ln in table)


# --------------------------------------------------------------------------- run_probe


def test_run_probe_dispatches_the_watch_mode_and_writes_only_under_out(tmp_path: Path) -> None:
    data = curated_copy(tmp_path)
    repo_curated = repo_root() / "data" / "curated"
    before, repo_before = tree_hashes(data), tree_hashes(repo_curated)
    targets = tmp_path / "watch_targets.txt"
    targets.write_text("meli_plus_co_total\n", encoding="utf-8")
    out = io.StringIO()
    out_dir = tmp_path / "probe-out"
    fetcher = FixtureFetcher({PLANES: PLANES_HTML})
    code = run_probe(targets, out_dir, "watch", fetcher=fetcher, catalog=CATALOG, now=NOW,
                     out=out, data_dir=data)  # fmt: skip
    assert code == 0
    assert tree_hashes(data) == before and tree_hashes(repo_curated) == repo_before
    assert sorted(p.name for p in tmp_path.iterdir()) == ["data", "probe-out", "watch_targets.txt"]
    assert sorted(tree_hashes(out_dir)) == [
        "raw/watch/01_www.mercadolibre.com.co_suscripciones_melimas_planes.html",
        "summary.json",
        "summary.md",
    ]
    summary = json.loads((out_dir / "summary.json").read_text(encoding="utf-8"))
    assert (summary["mode"], summary["targets"], summary["error"]) == (
        "watch",
        ["meli_plus_co_total"],
        None,
    )
    md = (out_dir / "summary.md").read_text(encoding="utf-8")
    assert out.getvalue() == md
    assert md.startswith("# Probe del vigilante de paquetes (2026-10-12 06:00 UTC)")


def test_run_probe_watch_exits_1_when_the_watcher_crashes(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    async def broken(*_a: Any, **_kw: Any) -> Any:
        raise RuntimeError("watcher bug")

    monkeypatch.setattr(probe_watch, "watch_bundles", broken)
    data = curated_copy(tmp_path)
    targets = tmp_path / "t.txt"
    targets.write_text("", encoding="utf-8")
    code = run_probe(targets, tmp_path / "o", "watch", fetcher=FixtureFetcher(), catalog=CATALOG,
                     now=NOW, out=io.StringIO(), data_dir=data)  # fmt: skip
    assert code == 1
    summary = json.loads((tmp_path / "o" / "summary.json").read_text(encoding="utf-8"))
    assert summary["error"] == "RuntimeError: watcher bug"
    assert "El vigilante falló: `RuntimeError: watcher bug`" in (
        (tmp_path / "o" / "summary.md").read_text(encoding="utf-8")
    )


def test_run_probe_watch_rejects_unknown_ids_before_fetching(tmp_path: Path) -> None:
    data = curated_copy(tmp_path)
    targets = tmp_path / "t.txt"
    targets.write_text("meli_plus_co_totl\n", encoding="utf-8")
    fetcher = FixtureFetcher()
    with pytest.raises(ValueError, match="línea 1"):
        run_probe(targets, tmp_path / "o", "watch", fetcher=fetcher, catalog=CATALOG,
                  data_dir=data)  # fmt: skip
    assert fetcher.calls == []


def test_offline_data_is_read_when_the_checkout_has_it(tmp_path: Path) -> None:
    latest = tmp_path / "latest"
    latest.mkdir()
    (latest / "fx.json").write_text("{not json", encoding="utf-8")
    (latest / "run.json").write_text(json.dumps({"as_of": "2026-10-11"}), encoding="utf-8")
    got = probe_watch.load_offline_data(latest)
    assert got.fx is None and got.prices == [] and got.as_of == date(2026, 10, 11)
    assert got.notes and got.notes[0].startswith("data/latest/fx.json ignorado")
    assert probe_watch.load_offline_data(tmp_path / "missing") == OfflineData()

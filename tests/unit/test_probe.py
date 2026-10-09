"""haggler probe: targets parsing, planning and the three modes, without network."""

from __future__ import annotations

import dataclasses
import io
import json
import sys
import types
from datetime import UTC, date, datetime
from decimal import Decimal
from pathlib import Path

import pytest

from conftest import FixtureFetcher, fixture_text
from haggler.fetch import RawResponse
from haggler.fetch.errors import BlockedError, RobotsDisallowedError, TransientFetchError
from haggler.models.fx import FxRate, FxTable
from haggler.models.run import RunReport
from haggler.probe import (
    _daily_options,
    _limit_page_text,
    json_script_ids,
    merge_targets,
    page_lines,
    page_title,
    parse_targets,
    plan_tasks,
    price_snippets,
    run_probe,
)
from haggler.sources.registry import get_adapter

NOW = datetime(2026, 10, 8, 6, 0, tzinfo=UTC)
NETFLIX_SE = "https://help.netflix.com/en/node/24926/se"
NETFLIX_CO = "https://help.netflix.com/en/node/24926/co"
TOMPEC = "https://raw.githubusercontent.com/tompec/netflix-prices/main/data/latest.json"
SPOTIFY_SE = "https://www.spotify.com/se/premium/"
SPOTIFY_CO = "https://www.spotify.com/co/premium/"


def write_targets(tmp_path: Path, text: str) -> Path:
    path = tmp_path / "targets.txt"
    path.write_text(text, encoding="utf-8")
    return path


# --------------------------------------------------------------------------- targets


def test_parse_targets(catalog) -> None:
    text = """
# comment line
netflix SE co   # inline comment
spotify *

icloud_plus SE SE LT
"""
    targets = parse_targets(text, catalog)
    assert [(t.service_id, t.countries, t.line) for t in targets] == [
        ("netflix", ("SE", "CO"), 3),
        ("spotify", None, 4),
        ("icloud_plus", ("SE", "LT"), 6),
    ]


def test_parse_targets_reports_every_error_with_line_numbers(catalog) -> None:
    with pytest.raises(ValueError) as err:
        parse_targets("netflx SE\nnetflix XX SE\nspotify\n", catalog)
    message = str(err.value)
    assert "línea 1: servicio desconocido 'netflx'" in message
    assert "línea 2: países desconocidos XX" in message
    assert "línea 3: falta al menos un país" in message


def test_repo_targets_file_is_valid(catalog) -> None:
    repo_targets = Path(__file__).parents[2] / "probe" / "targets.txt"
    if not repo_targets.exists():
        pytest.skip("probe/targets.txt not present")
    assert parse_targets(repo_targets.read_text(encoding="utf-8"), catalog)


def test_merge_targets(catalog) -> None:
    targets = parse_targets("netflix SE\nnetflix CO\nspotify SE\nspotify *\nspotify LT\n", catalog)
    assert merge_targets(targets) == {"netflix": {"SE", "CO"}, "spotify": None}


def test_plan_tasks_filters_and_narrows(catalog) -> None:
    targets = parse_targets("netflix SE CO\nicloud_plus LT SE\napple_one AR\n", catalog)
    planned = {p.task.task_id: p.task for p in plan_tasks(targets, catalog)}
    assert sorted(planned) == [
        "apple_marketing:apple_one:AR",
        "icloud_support:icloud_plus:ALL",
        "netflix_help:netflix:CO",
        "netflix_help:netflix:SE",
        "tompec_netflix:netflix:all",
    ]
    # One-task-for-all-countries sources are narrowed to the targeted countries.
    assert planned["tompec_netflix:netflix:all"].countries == ["SE", "CO"]
    assert planned["icloud_support:icloud_plus:ALL"].countries == ["SE", "LT"]
    assert planned["apple_marketing:apple_one:AR"].params["not_configured"] == ["apple_path"]


def test_plan_tasks_survives_a_broken_adapter(catalog, monkeypatch) -> None:
    adapter = get_adapter("spotify_premium")

    def boom(*args, **kwargs):
        raise RuntimeError("plan exploded")

    monkeypatch.setattr(adapter, "plan", boom)
    planned = plan_tasks(parse_targets("spotify SE\nnetflix SE\n", catalog), catalog)
    broken = [p for p in planned if p.adapter is None]
    assert len(broken) == 1
    assert broken[0].task.task_id == "spotify_premium:spotify:plan"
    assert "plan exploded" in (broken[0].error or "")
    assert any(p.task.task_id == "netflix_help:netflix:SE" for p in planned)


# --------------------------------------------------------------------------- page diagnostics


PAGE = """<!doctype html><html><head><title>  Premium   plans </title>
<script id="__NEXT_DATA__" type="application/json">{"props": {}}</script>
<script type="application/ld+json">{"@type": "Product"}</script>
<script>window.__INITIAL_STATE__ = {"a": 1};</script>
<script src="/app.js"></script>
</head><body><nav>Menu</nav><div class="card"><h3>Premium <b>Individual</b></h3>
<p>129 kr/mån</p><p>No price here</p></div><ul><li>Duo</li><li>169&nbsp;kr</li></ul>
</body></html>"""


def test_page_helpers() -> None:
    assert page_title(PAGE) == "Premium plans"
    assert json_script_ids(PAGE) == [
        "__NEXT_DATA__",
        "application/ld+json",
        "window.__INITIAL_STATE__",
    ]
    lines = page_lines(PAGE)
    assert "Premium Individual" in lines
    assert "129 kr/mån" in lines
    assert price_snippets(PAGE, ["SEK"], is_json=False) == ["129 kr/mån", "169 kr"]


def test_price_snippets_for_json_and_limits() -> None:
    payload = json.dumps({"Products": [{"Price": {"ListPrice": 99.0, "Currency": "SEK"}}]})
    assert price_snippets(payload, [], is_json=True) == ['"ListPrice": 99.0,']
    many = "<body>" + "".join(f"<p>{i} kr</p>" for i in range(1, 20)) + "</body>"
    assert len(price_snippets(many, ["SEK"], is_json=False)) == 8


# --------------------------------------------------------------------------- adapters mode


class SequenceFetcher:
    """Returns/raises the queued values for a URL in order (the last one repeats)."""

    def __init__(self, routes: dict[str, list]) -> None:
        self.routes = routes
        self.calls: list[tuple[str, str]] = []

    async def fetch(self, url: str, *, accept: str = "text/html", egress: str = "default"):
        self.calls.append((url, accept))
        queue = self.routes[url]
        value = queue.pop(0) if len(queue) > 1 else queue[0]
        if isinstance(value, BaseException):
            raise value
        return value

    async def aclose(self) -> None:
        return None


def html_response(url: str, text: str, *, final_url: str | None = None) -> RawResponse:
    return RawResponse(url=url, final_url=final_url or url, status_code=200, text=text)


@pytest.fixture
def adapters_run(tmp_path: Path, catalog):
    targets = write_targets(tmp_path, "netflix SE CO\nspotify SE CO\napple_one AR\n")
    fetcher = FixtureFetcher(
        {
            NETFLIX_SE: fixture_text("netflix_help", "se.html"),
            TOMPEC: fixture_text("tompec_netflix", "latest.json"),
            SPOTIFY_SE: BlockedError("HTTP 403 (cloudflare)", status=403, url=SPOTIFY_SE),
            SPOTIFY_CO: RobotsDisallowedError("robots.txt disallows", url=SPOTIFY_CO),
            # NETFLIX_CO has no route: FixtureFetcher raises FetchError(404).
        }
    )
    out = io.StringIO()
    out_dir = tmp_path / "probe-out"
    code = run_probe(
        targets, out_dir, "adapters", fetcher=fetcher, catalog=catalog, now=NOW, out=out
    )
    summary = json.loads((out_dir / "summary.json").read_text())
    rows = {r["task_id"]: r for r in summary["tasks"]}
    return code, out.getvalue(), out_dir, summary, rows, fetcher


def test_adapters_mode_statuses(adapters_run) -> None:
    code, _, _, summary, rows, fetcher = adapters_run
    assert code == 0
    # Rows keep the planning order even though hosts are fetched in parallel.
    assert [r["task_id"] for r in summary["tasks"]] == [
        "netflix_help:netflix:SE",
        "netflix_help:netflix:CO",
        "tompec_netflix:netflix:all",
        "spotify_premium:spotify:SE",
        "spotify_premium:spotify:CO",
        "apple_marketing:apple_one:AR",
    ]
    assert {tid: r["status"] for tid, r in rows.items()} == {
        "netflix_help:netflix:SE": "ok",
        "netflix_help:netflix:CO": "failed",
        "tompec_netflix:netflix:all": "ok",
        "spotify_premium:spotify:SE": "blocked",
        "spotify_premium:spotify:CO": "robots_disallowed",
        "apple_marketing:apple_one:AR": "not_configured",
    }
    assert summary["totals"] == {
        "blocked": 1,
        "failed": 1,
        "not_configured": 1,
        "ok": 2,
        "robots_disallowed": 1,
    }
    # Not-configured tasks are never fetched; the JSON source is asked for JSON.
    assert all("apple.com" not in url for url in fetcher.calls)
    assert fetcher.calls.count(TOMPEC) == 1
    assert rows["netflix_help:netflix:CO"]["http_status"] == 404
    assert rows["spotify_premium:spotify:SE"]["http_status"] == 403


def test_adapters_mode_row_contents(adapters_run) -> None:
    _, _, out_dir, _, rows, _ = adapters_run
    se = rows["netflix_help:netflix:SE"]
    assert se["final_url"] == NETFLIX_SE
    assert se["http_status"] == 200
    assert se["observations"]
    obs = se["observations"][0]
    assert set(obs) >= {"plan", "amount", "currency", "period", "country"}
    assert {o["currency"] for o in se["observations"]} == {"SEK"}
    assert se["page_len"] > 0
    assert isinstance(se["page_title"], str)
    assert se["price_snippets"]
    assert len(se["price_snippets"]) <= 8
    assert se["raw_file"] == "raw/netflix_help__netflix__SE.html"
    assert (out_dir / se["raw_file"]).read_text(encoding="utf-8") == fixture_text(
        "netflix_help", "se.html"
    )

    tompec = rows["tompec_netflix:netflix:all"]
    assert tompec["countries"] == ["SE", "CO"]
    assert {o["country"] for o in tompec["observations"]} == {"SE", "CO"}
    assert tompec["raw_file"] == "raw/tompec_netflix__netflix__all.json"
    assert tompec["page_title"] == ""
    assert tompec["price_snippets"]

    nc = rows["apple_marketing:apple_one:AR"]
    assert nc["notes"] == ["campos del catálogo sin configurar: apple_path"]
    # GitHub artifact paths cannot contain ":".
    assert all(":" not in p.name for p in (out_dir / "raw").iterdir())


def test_adapters_mode_markdown(adapters_run) -> None:
    _, printed, out_dir, _, _, _ = adapters_run
    md = (out_dir / "summary.md").read_text(encoding="utf-8")
    assert printed == md
    assert md.startswith("# Probe de adapters (2026-10-08 06:00 UTC)")
    assert "**6 tareas**" in md
    assert "| Estado | Tarea | HTTP | Obs. | Precios | URL final |" in md
    assert "| ok | netflix_help:netflix:SE | 200 |" in md
    assert "2/2 países" in md  # multi-country task summarised by country count
    # Failures get a details section; ok rows do not.
    assert "### netflix_help:netflix:CO: failed" in md
    assert "### spotify_premium:spotify:SE: blocked" in md
    assert "### netflix_help:netflix:SE" not in md
    assert "Sin configurar (1): apple_marketing:apple_one:AR" in md


def test_parse_details_for_a_page_without_prices(tmp_path: Path, catalog) -> None:
    targets = write_targets(tmp_path, "spotify SE\n")
    page = PAGE.replace("Premium", "Something")
    fetcher = SequenceFetcher(
        {SPOTIFY_SE: [html_response(SPOTIFY_SE, page, final_url="https://www.spotify.com/us/")]}
    )
    out = io.StringIO()
    code = run_probe(targets, tmp_path / "o", "adapters", fetcher=fetcher, catalog=catalog, out=out)
    assert code == 0
    row = json.loads((tmp_path / "o" / "summary.json").read_text())["tasks"][0]
    assert row["status"] not in {"ok", "error"}
    assert row["final_url"] == "https://www.spotify.com/us/"
    md = out.getvalue()
    assert f"### spotify_premium:spotify:SE: {row['status']}" in md
    assert "Scripts JSON: __NEXT_DATA__, application/ld+json, window.__INITIAL_STATE__" in md
    assert "Líneas con precio:" in md
    assert "129 kr/mån" in md


def page_text_chars(row: dict) -> int:
    unmapped = sum(len(u["name"]) + len(u["price_text"]) for u in row["unmapped"])
    return len(row["page_title"]) + sum(len(s) for s in row["price_snippets"]) + unmapped


def test_summary_quotes_at_most_300_chars_of_page_text_per_task(tmp_path: Path, catalog) -> None:
    targets = write_targets(tmp_path, "spotify SE\n")
    lines = "".join(f"<p>{i} kr {'lorem ipsum ' * 15}</p>" for i in range(1, 20))
    page = f"<html><head><title>{'Title ' * 100}</title></head><body>{lines}</body></html>"
    fetcher = SequenceFetcher({SPOTIFY_SE: [html_response(SPOTIFY_SE, page)]})
    out = io.StringIO()
    run_probe(targets, tmp_path / "o", "adapters", fetcher=fetcher, catalog=catalog, out=out)
    (row,) = json.loads((tmp_path / "o" / "summary.json").read_text())["tasks"]
    assert row["page_title"].startswith("Title Title")
    assert row["price_snippets"]
    assert page_text_chars(row) <= 300
    assert "lorem ipsum " * 15 not in out.getvalue()
    assert "Title " * 30 not in out.getvalue()
    # The full page is kept for local debugging, under <out>/raw/ only.
    assert (tmp_path / "o" / row["raw_file"]).read_text(encoding="utf-8") == page
    assert row["raw_file"].startswith("raw/")


def test_page_text_budget_covers_unmapped_items() -> None:
    row = {
        "page_title": "T" * 50,
        "unmapped": [{"name": "N" * 100, "price_text": "P" * 40}] * 3,
        "price_snippets": ["S" * 160] * 8,
    }
    _limit_page_text(row)
    assert page_text_chars(row) == 300
    assert row["page_title"] == "T" * 50
    assert row["unmapped"][:2] == [{"name": "N" * 100, "price_text": "P" * 40}] * 1 + [
        {"name": "N" * 100, "price_text": "P" * 10}
    ]
    assert row["price_snippets"] == []


def test_transient_errors_are_retried_once(tmp_path: Path, catalog) -> None:
    targets = write_targets(tmp_path, "netflix SE\n")
    flaky = TransientFetchError("HTTP 503", status=503, url=NETFLIX_SE)
    fetcher = SequenceFetcher(
        {
            NETFLIX_SE: [flaky, html_response(NETFLIX_SE, fixture_text("netflix_help", "se.html"))],
            TOMPEC: [TransientFetchError("HTTP 429", status=429, url=TOMPEC)],
        }
    )
    code = run_probe(targets, tmp_path / "o", "adapters", fetcher=fetcher, catalog=catalog,
                     out=io.StringIO())  # fmt: skip
    assert code == 0
    rows = {
        r["task_id"]: r for r in json.loads((tmp_path / "o" / "summary.json").read_text())["tasks"]
    }
    assert rows["netflix_help:netflix:SE"]["status"] == "ok"
    assert rows["tompec_netflix:netflix:all"]["status"] == "failed"
    assert rows["tompec_netflix:netflix:all"]["http_status"] == 429
    assert [u for u, _ in fetcher.calls].count(TOMPEC) == 2
    assert ("https://raw.githubusercontent.com/tompec/netflix-prices/main/data/latest.json",
            "application/json") in fetcher.calls  # fmt: skip


def test_adapter_crash_is_reported_and_exits_1(tmp_path: Path, catalog, monkeypatch) -> None:
    adapter = get_adapter("netflix_help")

    def boom(task, resp, cat):
        raise KeyError("selector")

    monkeypatch.setattr(adapter, "parse", boom)
    targets = write_targets(tmp_path, "netflix SE\n")
    fetcher = FixtureFetcher(
        {
            NETFLIX_SE: "<html><body>x</body></html>",
            TOMPEC: fixture_text("tompec_netflix", "latest.json"),
        }
    )
    out = io.StringIO()
    code = run_probe(targets, tmp_path / "o", "adapters", fetcher=fetcher, catalog=catalog, out=out)
    assert code == 1
    rows = {
        r["task_id"]: r for r in json.loads((tmp_path / "o" / "summary.json").read_text())["tasks"]
    }
    crashed = rows["netflix_help:netflix:SE"]
    assert crashed["status"] == "error"
    assert crashed["unexpected"] is True
    assert crashed["error"] == "KeyError: 'selector'"
    assert crashed["raw_file"]  # the page is still saved for debugging
    assert rows["tompec_netflix:netflix:all"]["status"] == "ok"
    assert "### netflix_help:netflix:SE: error" in out.getvalue()


def test_invalid_mode_and_targets(tmp_path: Path, catalog) -> None:
    targets = write_targets(tmp_path, "nope SE\n")
    with pytest.raises(ValueError, match="modo desconocido"):
        run_probe(targets, tmp_path / "o", "everything", catalog=catalog)
    with pytest.raises(ValueError, match="servicio desconocido"):
        run_probe(targets, tmp_path / "o", "adapters", fetcher=FixtureFetcher(), catalog=catalog)


# --------------------------------------------------------------------------- fx mode


class FakeFx:
    def __init__(self) -> None:
        self.errors = ["open_er_api: HTTP 500"]
        self.calls: list[tuple[set[str], date]] = []

    async def build_table(self, currencies, *, as_of, previous):
        self.calls.append((set(currencies), as_of))
        old = date(2026, 10, 6)
        return FxTable(
            as_of=as_of,
            rates={
                "SEK": FxRate(currency="SEK", per_eur=Decimal("11.02"), source="ecb", as_of=as_of),
                "COP": FxRate(
                    currency="COP",
                    per_eur=Decimal("4512.3"),
                    source="frankfurter",
                    provider="BANREP",
                    as_of=as_of,
                ),
                "ARS": FxRate(
                    currency="ARS",
                    per_eur=Decimal("1500"),
                    source="previous",
                    as_of=old,
                    stale=True,
                ),
            },
        )


def test_fx_mode(tmp_path: Path, catalog) -> None:
    fx = FakeFx()
    out = io.StringIO()
    targets = write_targets(tmp_path, "netflix SE\n")
    code = run_probe(
        targets,
        tmp_path / "o",
        "fx",
        catalog=catalog,
        fx_service=fx,
        today=date(2026, 10, 8),
        out=out,
    )
    assert code == 0
    assert fx.calls == [(catalog.currencies(), date(2026, 10, 8))]
    summary = json.loads((tmp_path / "o" / "summary.json").read_text())
    rates = {r["currency"]: r for r in summary["rates"]}
    assert "EUR" not in rates
    assert rates["COP"] == {
        "currency": "COP",
        "per_eur": "4512.3",
        "source": "frankfurter",
        "provider": "BANREP",
        "as_of": "2026-10-08",
        "stale": False,
        "pinned": "BANREP",
    }
    assert rates["ARS"]["stale"] is True
    assert rates["USD"]["per_eur"] is None
    md = out.getvalue()
    assert md == (tmp_path / "o" / "summary.md").read_text()
    assert "| COP | 4512.3 | frankfurter | BANREP | 2026-10-08 | no | BANREP |" in md
    assert "| ARS | 1500 | previous | — | 2026-10-06 | sí | BCRA |" in md
    assert "sin tasa:" in md and "USD" in md
    assert "- open_er_api: HTTP 500" in md


# --------------------------------------------------------------------------- full mode


@pytest.fixture
def no_llm_credentials(monkeypatch) -> None:
    for name in (
        "GOOGLE_API_KEY",
        "GEMINI_API_KEY",
        "GOOGLE_GENAI_USE_ENTERPRISE",
        "GOOGLE_GENAI_USE_VERTEXAI",
    ):
        monkeypatch.delenv(name, raising=False)


def fake_daily(monkeypatch, **attrs) -> types.ModuleType:
    module = types.ModuleType("haggler.jobs.daily")
    for name, value in attrs.items():
        setattr(module, name, value)
    monkeypatch.setitem(sys.modules, "haggler.jobs.daily", module)
    return module


@dataclasses.dataclass
class FakeDailyOptions:
    """Mirrors haggler.jobs.daily.DailyOptions (the fields the probe sets)."""

    services: list[str] = dataclasses.field(default_factory=list)
    countries: list[str] = dataclasses.field(default_factory=list)
    adapters: list[str] = dataclasses.field(default_factory=list)
    dry_run: bool = False
    no_llm: bool = False
    store: str | None = None


def test_full_mode_calls_run_daily(
    tmp_path: Path, catalog, monkeypatch, no_llm_credentials
) -> None:
    seen: dict = {}

    async def run_daily(options, deps=None):
        import os

        seen.update(
            options=options,
            local_dir=os.environ["HAGGLER_LOCAL_DIR"],
            store=os.environ["HAGGLER_STORE"],
            llm=os.environ["HAGGLER_LLM"],
        )
        return RunReport(
            run_id="probe-run",
            run_date=date(2026, 10, 8),
            started_at=NOW,
            tasks_total=4,
            tasks_by_status={"ok": 4},
        )

    fake_daily(monkeypatch, DailyOptions=FakeDailyOptions, run_daily=run_daily)
    monkeypatch.setenv("HAGGLER_LOCAL_DIR", "/somewhere/else")
    # Targets with the same country set share one run.
    targets = write_targets(tmp_path, "netflix SE CO\nspotify CO\nspotify SE\n")
    out = io.StringIO()
    code = run_probe(
        targets, tmp_path / "o", "full", catalog=catalog, today=date(2026, 10, 8), out=out
    )
    assert code == 0
    assert seen["options"] == FakeDailyOptions(
        services=["netflix", "spotify"], countries=["CO", "SE"], no_llm=True, store="local"
    )
    # The store quotes vendor pages (raw pages, snapshots): it lives under <out>/raw/.
    assert seen["local_dir"] == str(tmp_path / "o" / "raw" / "store")
    assert (seen["store"], seen["llm"]) == ("local", "0")  # no credentials in the environment
    import os

    assert os.environ["HAGGLER_LOCAL_DIR"] == "/somewhere/else"  # environment restored
    summary = json.loads((tmp_path / "o" / "summary.json").read_text())
    (run,) = summary["runs"]
    assert run["report"]["run_id"] == "probe-run"
    assert (run["services"], run["countries"]) == (["netflix", "spotify"], ["CO", "SE"])
    assert summary["llm"] is False
    assert run["exit_code"] is None
    assert '"run_id": "probe-run"' in out.getvalue()
    assert "- LLM: no" in out.getvalue()


def test_full_mode_falls_back_to_main(
    tmp_path: Path, catalog, monkeypatch, no_llm_credentials
) -> None:
    """Without a usable run_daily, the CLI contract (main(argv)) runs and the report is read
    back from the store layout (latest/run.json -> runs/{as_of}/{run_id}/report.json)."""
    import os

    calls: list[list[str]] = []

    def main(argv):
        calls.append(list(argv))
        root = Path(os.environ["HAGGLER_LOCAL_DIR"])
        (root / "latest").mkdir(parents=True)
        (root / "latest" / "run.json").write_text(
            json.dumps({"schema_version": 1, "run_id": "r1", "as_of": "2026-10-08", "files": {}})
        )
        report_dir = root / "runs" / "2026-10-08" / "r1"
        report_dir.mkdir(parents=True)
        (report_dir / "report.json").write_text(json.dumps({"run_id": "r1", "tasks_total": 2}))
        return 1  # e.g. fewer than 50 % of tasks OK: not a probe failure

    async def run_daily(options, deps=None):  # no DailyOptions in the module -> use main()
        raise AssertionError("must not be called")

    fake_daily(monkeypatch, main=main, run_daily=run_daily)
    targets = write_targets(tmp_path, "netflix *\n")
    out = io.StringIO()
    code = run_probe(targets, tmp_path / "o", "full", catalog=catalog, out=out)
    assert code == 0
    assert calls == [["--store", "local", "--services", "netflix", "--no-llm"]]
    summary = json.loads((tmp_path / "o" / "summary.json").read_text())
    (run,) = summary["runs"]
    assert run["report"] == {"run_id": "r1", "tasks_total": 2}
    assert run["exit_code"] == 1
    assert run["countries"] is None
    assert "Código de salida del job: 1" in out.getvalue()


def test_full_mode_runs_once_per_country_set(
    tmp_path: Path, catalog, monkeypatch, no_llm_credentials
) -> None:
    """A "*" target must not lift the country filter of the other targets."""
    seen: list = []

    async def run_daily(options, deps=None):
        seen.append(options)
        return RunReport(
            run_id=f"run-{len(seen)}", run_date=date(2026, 10, 8), started_at=NOW, tasks_total=1
        )

    fake_daily(monkeypatch, DailyOptions=FakeDailyOptions, run_daily=run_daily)
    targets = write_targets(
        tmp_path, "icloud_plus *\nnetflix SE CO\nspotify CO SE\napple_music LT\nnetflix SE\n"
    )
    out = io.StringIO()
    code = run_probe(targets, tmp_path / "o", "full", catalog=catalog, out=out)
    assert code == 0
    assert [(o.services, o.countries) for o in seen] == [
        (["icloud_plus"], []),
        (["netflix", "spotify"], ["CO", "SE"]),
        (["apple_music"], ["LT"]),
    ]
    summary = json.loads((tmp_path / "o" / "summary.json").read_text())
    assert [(r["services"], r["countries"], r["report"]["run_id"]) for r in summary["runs"]] == [
        (["icloud_plus"], None, "run-1"),
        (["netflix", "spotify"], ["CO", "SE"], "run-2"),
        (["apple_music"], ["LT"], "run-3"),
    ]
    assert summary["services"] == ["icloud_plus", "netflix", "spotify", "apple_music"]
    md = out.getvalue()
    assert "## Corrida 1/3: icloud_plus (todos los países)" in md
    assert "## Corrida 2/3: netflix, spotify (CO, SE)" in md


def test_full_mode_main_fallback_runs_once_per_country_set(
    tmp_path: Path, catalog, monkeypatch, no_llm_credentials
) -> None:
    import os

    calls: list[list[str]] = []

    def main(argv):
        calls.append(list(argv))
        run_id = f"r{len(calls)}"
        root = Path(os.environ["HAGGLER_LOCAL_DIR"])
        (root / "latest").mkdir(parents=True, exist_ok=True)
        (root / "latest" / "run.json").write_text(json.dumps({"run_id": run_id, "as_of": "d"}))
        report_dir = root / "runs" / "d" / run_id
        report_dir.mkdir(parents=True)
        (report_dir / "report.json").write_text(json.dumps({"run_id": run_id}))
        return 0

    fake_daily(monkeypatch, main=main)
    targets = write_targets(tmp_path, "netflix *\nspotify SE CO\n")
    code = run_probe(targets, tmp_path / "o", "full", catalog=catalog, out=io.StringIO())
    assert code == 0
    assert calls == [
        ["--store", "local", "--services", "netflix", "--no-llm"],
        ["--store", "local", "--services", "spotify", "--countries", "CO,SE", "--no-llm"],
    ]
    summary = json.loads((tmp_path / "o" / "summary.json").read_text())
    assert [r["report"]["run_id"] for r in summary["runs"]] == ["r1", "r2"]


def test_full_mode_failure_exits_1(
    tmp_path: Path, catalog, monkeypatch, no_llm_credentials
) -> None:
    def run_daily(options, deps=None):
        raise RuntimeError("pipeline exploded")

    fake_daily(monkeypatch, DailyOptions=FakeDailyOptions, run_daily=run_daily)
    out = io.StringIO()
    code = run_probe(write_targets(tmp_path, "netflix SE\n"), tmp_path / "o", "full",
                     catalog=catalog, out=out)  # fmt: skip
    assert code == 1
    assert "RuntimeError: pipeline exploded" in out.getvalue()
    assert json.loads((tmp_path / "o" / "summary.json").read_text())["error"].startswith(
        "RuntimeError"
    )


def test_daily_options_only_when_filters_reach_the_job() -> None:
    values = {"services": ["netflix"], "countries": ["SE"], "no_llm": True, "store": "local"}
    with_filters = types.SimpleNamespace(DailyOptions=FakeDailyOptions)
    assert _daily_options(with_filters, values) == FakeDailyOptions(
        services=["netflix"], countries=["SE"], no_llm=True, store="local"
    )

    @dataclasses.dataclass
    class NoCountries:
        services: list[str] = dataclasses.field(default_factory=list)

    # Without a countries filter the job would run every country: use the CLI contract instead.
    assert _daily_options(types.SimpleNamespace(DailyOptions=NoCountries), values) is None
    assert _daily_options(types.SimpleNamespace(), values) is None


def test_unexpected_fetch_exception_is_a_row_not_a_crash(tmp_path: Path, catalog) -> None:
    targets = write_targets(tmp_path, "netflix SE\n")
    fetcher = FixtureFetcher(
        {NETFLIX_SE: ValueError("bad url"), TOMPEC: fixture_text("tompec_netflix", "latest.json")}
    )
    code = run_probe(
        targets, tmp_path / "o", "adapters", fetcher=fetcher, catalog=catalog, out=io.StringIO()
    )
    assert code == 1
    tasks = json.loads((tmp_path / "o" / "summary.json").read_text())["tasks"]
    rows = {r["task_id"]: r for r in tasks}
    assert rows["netflix_help:netflix:SE"]["status"] == "error"
    assert rows["netflix_help:netflix:SE"]["error"] == "ValueError: bad url"
    assert rows["tompec_netflix:netflix:all"]["status"] == "ok"


# ------------------------------------------------------------------ research mode


def test_research_targets_and_flash_models():
    from haggler.probe_research import flash_models, parse_research_targets

    t = parse_research_targets("netflix; spotify co us # comment\ninstrument revolut_eea nope; zz")
    assert t.services == ["netflix"] and t.pairs == ["spotify:CO", "spotify:US"]
    assert t.instruments == ["revolut_eea"]
    assert t.errors == ["unknown instrument nope", "unknown service zz"]
    names = [
        "models/gemini-2.5-flash",
        "models/gemini-3-flash-preview",
        "models/gemini-3.1-flash-lite",
        "models/gemini-3.1-flash",
        "models/gemini-3-pro-preview",
    ]
    assert flash_models(names) == ["gemini-3.1-flash", "gemini-3-flash-preview", "gemini-2.5-flash"]


def test_research_probe_without_llm(tmp_path, monkeypatch):
    from haggler import probe_research
    from haggler.config import Settings

    async def robots(settings):
        return "allowed"

    async def pages(settings):
        return [
            {
                "url": "https://help.netflix.com/legal/termsofuse",
                "subjects": ["netflix"],
                "status": "ok",
                "http": 200,
                "final_url": "https://help.netflix.com/legal/termsofuse",
                "content_type": "text/html",
                "text_chars": 5000,
                "detail": "",
            }
        ]

    monkeypatch.setattr(probe_research, "redirect_robots", robots)
    monkeypatch.setattr(probe_research, "known_pages", pages)
    monkeypatch.setenv("HAGGLER_LLM", "off")
    targets = tmp_path / "t.txt"
    targets.write_text("netflix\n", encoding="utf-8")
    summary, md = probe_research.run(targets, tmp_path / "out", Settings(), today=date(2026, 10, 9))
    assert summary["llm"] is False and summary["report"] is None and summary["gate"] is None
    assert "robots.txt de las redirecciones de grounding: allowed" in md
    assert "Páginas oficiales conocidas (1/1 legibles)" in md


def test_research_probe_markdown_reports_the_gate():
    from haggler.probe_research import render_md

    summary = {
        "llm": True,
        "model": "gemini-3.1-flash",
        "configured_model": "gemini-3-flash-preview",
        "flash_models": ["gemini-3.1-flash"],
        "models_error": None,
        "redirect_robots": "allowed",
        "targets": {"services": ["netflix"], "pairs": [], "instruments": [], "errors": []},
        "error": None,
        "official_verified_ratio": 0.5,
        "gate": "fail",
        "report": {
            "by_status": {"researched": 1},
            "claims_verified": 1,
            "claims_unverified": 1,
            "checks": {"official": {"verified": 1, "quote_not_found": 1}},
            "budget": {"calls": 1, "queries": 3},
            "errors": [],
        },
        "grounding": {
            "service_x": {"queries": ["a"], "domains": ["netflix.com"], "finish": ["STOP"]}
        },
        "subjects": [
            {
                "key": "service:netflix",
                "status": "researched",
                "verified": [
                    {
                        "field": "residence_clause",
                        "value": "must_reside",
                        "class": "official",
                        "host": "help.netflix.com",
                        "via": "cited",
                        "quote": "Only residents",
                    }
                ],
                "unverified": [],
                "open_questions": [],
            }
        ],
    }
    md = render_md(summary, date(2026, 10, 9))
    assert "no disponible" in md and "50% (compuerta 60%: fail)" in md
    assert "✅ `residence_clause=must_reside` [official] help.netflix.com" in md

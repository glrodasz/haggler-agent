"""`haggler probe`: fetch real vendor pages and report what each adapter extracts from them.

WHY: the dev container cannot reach the vendor sites, so parsers are written against synthetic
fixtures. The probe runs on GitHub-hosted runners (which can reach them), keeps every raw page
and prints a compact per-task report: status, final URL, prices found and, for failures, the page
title, the ids of embedded JSON scripts and the first price-looking lines. That is enough to tune
selectors from the CI log alone, without ever re-publishing the vendor's HTML.

Modes:
  adapters  plan the tasks of every source of the targeted services, fetch them with HttpFetcher
            (robots.txt, identifiable UA, per-host rate limit), parse, summarise;
  fx        build the FX table with DefaultFxService and print source/as_of/stale per currency;
  full      run the daily job (haggler.jobs.daily) against a LocalStore in <out>/raw/store, once
            per distinct country set of the targets, with the LLM only when credentials exist,
            and print the RunReports;
  research  one real feasibility research run (haggler.probe_research; its own target format);
  bundles   readability of the promotional bundles' official pages (haggler.probe_bundles; its
            own target format, "<label> <CC> <url>"), fetched with HttpFetcher, LLM off;
  watch     the bundle watcher on the real pages, each target watched as watch.mode auto in
            memory only, writing nothing (haggler.probe_watch; targets: bundle ids, none = every
            bundle with watch.pages), LLM off.

Targets file (adapters, fx, full): one target per non-comment line, "service_id CC [CC ...]"
("*" = all countries).

Output: everything that quotes vendor pages (raw pages, the full-mode store) goes under
<out>/raw/ and is for local debugging only. <out>/summary.{json,md} are the only files meant to
be published (CI artifact, job summary); in adapters mode they quote at most PAGE_TEXT_BUDGET
characters of page text per task (title, unmapped items, price lines), in bundles mode at most
haggler.probe_bundles.QUOTE_BUDGET per target, in watch mode at most
haggler.probe_watch.QUOTE_BUDGET per bundle (anchors, hint lines, anchor texts).
"""

from __future__ import annotations

import asyncio
import contextlib
import dataclasses
import importlib
import inspect
import json
import os
import re
import sys
import time
import traceback
from collections.abc import Iterator
from dataclasses import dataclass
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Any, Literal, TextIO

from selectolax.lexbor import LexborHTMLParser

from haggler.catalog import load_catalog
from haggler.config import Settings, llm_credentials_available
from haggler.fetch import Fetcher, RawResponse
from haggler.fetch.errors import (
    BlockedError,
    FetchError,
    RobotsDisallowedError,
    TransientFetchError,
)
from haggler.models.catalog import Catalog
from haggler.models.run import Task
from haggler.money import find_prices
from haggler.sources.base import Adapter, ParseResult
from haggler.sources.registry import get_adapter

Mode = Literal["adapters", "fx", "full", "research", "bundles", "watch"]
MODES: tuple[str, ...] = ("adapters", "fx", "full", "research", "bundles", "watch")
ALL_COUNTRIES = "*"
TASK_TIMEOUT_S = 90.0
TRANSIENT_ATTEMPTS = 2
MAX_SNIPPETS = 8
SNIPPET_CHARS = 160
# Verbatim page text a summary row may quote, in total, and how much of it the title may take.
PAGE_TEXT_BUDGET = 300
TITLE_CHARS = 100
# Statuses without a details section below the table (every other status gets one).
QUIET_STATUSES = frozenset({"ok", "not_configured"})

_DROP_TAGS = frozenset({"script", "style", "noscript", "template", "svg", "head", "iframe"})
_BLOCK_TAGS = frozenset(
    {
        "address", "article", "aside", "blockquote", "br", "dd", "details", "dialog", "div",
        "dl", "dt", "fieldset", "figcaption", "figure", "footer", "form", "h1", "h2", "h3",
        "h4", "h5", "h6", "header", "hr", "li", "main", "nav", "ol", "p", "pre", "section",
        "summary", "table", "tbody", "td", "tfoot", "th", "thead", "tr", "ul", "option",
        "button", "label",
    }
)  # fmt: skip
_WS = re.compile(r"\s+")
_STATE_ASSIGN = re.compile(r"(window\.__[A-Za-z0-9_]+__|window\.[A-Za-z_]*[Ss]tate)\s*=")
_JSON_PRICE_LINE = re.compile(r"price|amount|cost|precio|preis|pris", re.IGNORECASE)
_UNSAFE_NAME = re.compile(r"[^A-Za-z0-9_.-]")
_DIGIT = re.compile(r"\d")


# ============================================================================ targets


@dataclass(frozen=True)
class ProbeTarget:
    service_id: str
    # None = every catalog country.
    countries: tuple[str, ...] | None
    line: int = 0


def parse_targets(text: str, catalog: Catalog) -> list[ProbeTarget]:
    """Parse a targets file. Unknown services/countries are errors: typos must not pass silently."""
    services = {s.id for s in catalog.services}
    ccs = set(catalog.country_codes())
    targets: list[ProbeTarget] = []
    errors: list[str] = []
    for n, raw in enumerate(text.splitlines(), 1):
        line = raw.split("#", 1)[0].strip()
        if not line:
            continue
        service_id, *codes = line.split()
        if service_id not in services:
            errors.append(f"línea {n}: servicio desconocido {service_id!r}")
            continue
        if not codes:
            errors.append(f"línea {n}: falta al menos un país (o '*') para {service_id}")
            continue
        if ALL_COUNTRIES in codes:
            targets.append(ProbeTarget(service_id, None, n))
            continue
        upper = [c.upper() for c in codes]
        unknown = [c for c in upper if c not in ccs]
        if unknown:
            errors.append(f"línea {n}: países desconocidos {', '.join(unknown)}")
            continue
        targets.append(ProbeTarget(service_id, tuple(dict.fromkeys(upper)), n))
    if errors:
        raise ValueError("targets inválidos:\n  " + "\n  ".join(errors))
    return targets


def merge_targets(targets: list[ProbeTarget]) -> dict[str, set[str] | None]:
    """service_id -> wanted countries (None = all), in first-seen order."""
    merged: dict[str, set[str] | None] = {}
    for t in targets:
        if t.countries is None or (t.service_id in merged and merged[t.service_id] is None):
            merged[t.service_id] = None
        else:
            merged.setdefault(t.service_id, set()).update(t.countries)  # type: ignore[union-attr]
    return merged


@dataclass
class PlannedTask:
    task: Task
    adapter: Adapter | None
    error: str | None = None


def plan_tasks(targets: list[ProbeTarget], catalog: Catalog) -> list[PlannedTask]:
    """Tasks of every source of the targeted services, limited to the targeted countries.

    Multi-country tasks (iCloud's single support page, the tompec dataset) are narrowed to the
    targeted countries so their status and notes speak about those countries only.
    """
    planned: list[PlannedTask] = []
    seen: set[str] = set()
    for service_id, wanted in merge_targets(targets).items():
        svc = catalog.service(service_id)
        for src in svc.sources:
            try:
                adapter = get_adapter(src.adapter)
                tasks = adapter.plan(svc, src, catalog)
            except Exception as exc:  # a broken adapter must not hide the others
                placeholder = Task(
                    task_id=f"{src.adapter}:{svc.id}:plan",
                    adapter_id=src.adapter,
                    service_ids=[svc.id],
                    countries=sorted(wanted) if wanted else [],
                    channel=src.channel,
                    kind=src.kind,
                    url="",
                    host="",
                )
                planned.append(PlannedTask(placeholder, None, _exc_text(exc)))
                continue
            for task in tasks:
                keep = [c for c in task.countries if wanted is None or c in wanted]
                if not keep or task.task_id in seen:
                    continue
                seen.add(task.task_id)
                if keep != task.countries:
                    task = task.model_copy(update={"countries": keep})
                planned.append(PlannedTask(task, adapter))
    return planned


# ============================================================================ page diagnostics


def _render(node: Any, out: list[str]) -> None:
    child = node.child
    while child is not None:
        tag = child.tag
        if tag == "-text":
            out.append(child.text_content or "")
        elif tag not in _DROP_TAGS and not tag.startswith("-"):
            block = tag in _BLOCK_TAGS
            if block:
                out.append("\n")
            _render(child, out)
            if block:
                out.append("\n")
        child = child.next


def page_lines(html: str) -> list[str]:
    """Visible text, one line per block element (short lines such as "129 kr" are kept)."""
    tree = LexborHTMLParser(html)
    root = tree.body or tree.root
    if root is None:
        return []
    parts: list[str] = []
    _render(root, parts)
    lines = (_WS.sub(" ", ln).strip() for ln in "".join(parts).split("\n"))
    return [ln for ln in lines if ln]


def page_title(html: str) -> str:
    node = LexborHTMLParser(html).css_first("title")
    return _WS.sub(" ", node.text()).strip() if node else ""


def json_script_ids(html: str) -> list[str]:
    """Embedded data blobs worth parsing: JSON <script>s (by id, else by type) and inline
    `window.__STATE__ = ...` assignments."""
    out: list[str] = []
    for node in LexborHTMLParser(html).css("script"):
        attrs = node.attributes
        stype = (attrs.get("type") or "").lower()
        if "json" in stype:
            out.append(attrs.get("id") or attrs.get("data-target") or stype)
        elif not attrs.get("src"):
            out.extend(m.group(1) for m in _STATE_ASSIGN.finditer(node.text() or ""))
    return list(dict.fromkeys(out))


def price_snippets(text: str, expected: list[str], *, is_json: bool) -> list[str]:
    """First lines that contain a price (HTML) or a price-like key (JSON)."""
    out: list[str] = []
    if is_json:
        try:
            lines = json.dumps(json.loads(text), indent=1, ensure_ascii=False).splitlines()
        except ValueError:
            lines = text.splitlines()
        # Price-like key with a number on the line ("price": null is noise).
        candidates = (
            ln.strip() for ln in lines if _JSON_PRICE_LINE.search(ln) and _DIGIT.search(ln)
        )
    else:
        candidates = (ln for ln in page_lines(text) if find_prices(ln, expected or None))
    for line in candidates:
        if len(out) >= MAX_SNIPPETS:
            break
        out.append(line[:SNIPPET_CHARS])
    return out


# ============================================================================ adapters mode


def _exc_text(exc: BaseException) -> str:
    return f"{type(exc).__name__}: {exc}"


def _safe_name(task_id: str) -> str:
    # ":" is not allowed in GitHub Actions artifact paths.
    return _UNSAFE_NAME.sub("_", task_id.replace(":", "__"))


def _expected_currencies(task: Task, catalog: Catalog) -> list[str]:
    out: list[str] = []
    for cc in task.countries:
        with contextlib.suppress(KeyError):
            out.extend(catalog.country(cc).expected_currencies(task.channel))
    return list(dict.fromkeys(out))


def _base_row(task: Task) -> dict[str, Any]:
    return {
        "task_id": task.task_id,
        "adapter_id": task.adapter_id,
        "service_ids": list(task.service_ids),
        "countries": list(task.countries),
        "channel": task.channel,
        "url": task.url,
        "status": None,
        "http_status": None,
        "final_url": None,
        "content_type": None,
        "observations": [],
        "missing_plans": [],
        "unmapped": [],
        "llm_requests": 0,
        "notes": [],
        "page_title": "",
        "page_len": 0,
        "json_script_ids": [],
        "price_snippets": [],
        "raw_file": None,
        "error": None,
        "unexpected": False,
        "ms": 0,
    }


def _apply_result(row: dict[str, Any], result: ParseResult) -> None:
    row["status"] = result.status
    row["observations"] = [
        {
            "country": o.country,
            "service": o.service_id,
            "plan": o.plan_id,
            "label": o.plan_label_raw,
            "amount": str(o.amount),
            "currency": o.currency,
            "period": o.billing_period,
            "flags": list(o.flags),
        }
        for o in result.observations
    ]
    row["missing_plans"] = list(result.missing_plans)
    row["unmapped"] = [{"name": i.name, "price_text": i.price_text} for i in result.unmapped]
    row["llm_requests"] = len(result.llm_requests)
    row["notes"] = [*row["notes"], *result.notes]


async def _fetch(fetcher: Fetcher, task: Task) -> RawResponse:
    accept = str(task.params.get("accept") or "text/html")
    return await _fetch_url(fetcher, task.url, accept=accept, egress=task.egress)


async def _fetch_url(
    fetcher: Fetcher, url: str, *, accept: str = "text/html", egress: str = "default"
) -> RawResponse:
    """One fetch with a per-attempt timeout; transient failures (5xx, resets, timeouts) are
    retried once. A 429 is not: the server asked us to slow down, and its Retry-After is longer
    than the per-host interval a retry would wait. Walls, robots.txt and other permanent failures
    are never retried."""
    for attempt in range(1, TRANSIENT_ATTEMPTS + 1):
        try:
            return await asyncio.wait_for(
                fetcher.fetch(url, accept=accept, egress=egress), TASK_TIMEOUT_S
            )
        except TransientFetchError as exc:
            if attempt == TRANSIENT_ATTEMPTS or exc.status == 429:
                raise
    raise AssertionError("unreachable")  # pragma: no cover


async def probe_task(
    planned: PlannedTask, fetcher: Fetcher, catalog: Catalog, raw_dir: Path
) -> dict[str, Any]:
    task = planned.task
    row = _base_row(task)
    if planned.adapter is None:
        row.update(status="error", error=planned.error, unexpected=True)
        return row
    missing = task.params.get("not_configured")
    if missing or not task.url:
        row["status"] = "not_configured"
        row["notes"] = [f"campos del catálogo sin configurar: {', '.join(missing or ['url'])}"]
        return row

    started = time.monotonic()
    try:
        resp = await _fetch(fetcher, task)
    except Exception as exc:
        row.update(_fetch_failure(exc), ms=int((time.monotonic() - started) * 1000))
        return row
    row["ms"] = int((time.monotonic() - started) * 1000)
    _inspect_page(row, task, resp, catalog, raw_dir)
    try:
        result = planned.adapter.parse(task, resp, catalog)
    except Exception as exc:
        row.update(status="error", error=_exc_text(exc), unexpected=True)
        row["notes"].append(traceback.format_exc(limit=4).strip()[-1500:])
    else:
        _apply_result(row, result)
    _limit_page_text(row)
    return row


def _limit_page_text(row: dict[str, Any]) -> None:
    """Cap the page text a row quotes at PAGE_TEXT_BUDGET characters, spent in order on the
    title (at most TITLE_CHARS), the unmapped items and the price lines: summaries get
    published, vendor pages never do (the full page stays in <out>/raw/)."""
    left = PAGE_TEXT_BUDGET

    def take(text: str, cap: int = PAGE_TEXT_BUDGET) -> str:
        nonlocal left
        kept = text[: max(0, min(left, cap))]
        left -= len(kept)
        return kept

    row["page_title"] = take(row["page_title"], TITLE_CHARS)
    unmapped = []
    for item in row["unmapped"]:
        if left <= 0:
            break
        unmapped.append({"name": take(item["name"]), "price_text": take(item["price_text"])})
    row["unmapped"] = unmapped
    snippets = []
    for line in row["price_snippets"]:
        if left <= 0:
            break
        snippets.append(take(line))
    row["price_snippets"] = snippets


def _fetch_failure(exc: Exception) -> dict[str, Any]:
    """Row fields for a fetch that raised: walls, robots and HTTP errors are findings; anything
    else is a bug in our fetch layer and makes the probe exit non-zero."""
    if isinstance(exc, FetchError):
        if isinstance(exc, BlockedError):
            status = "blocked"
        elif isinstance(exc, RobotsDisallowedError):
            status = "robots_disallowed"
        else:
            status = "failed"
        return {"status": status, "http_status": exc.status, "error": _exc_text(exc)}
    if isinstance(exc, TimeoutError):
        return {"status": "failed", "error": f"timeout ({TASK_TIMEOUT_S:.0f} s)"}
    return {"status": "error", "error": _exc_text(exc), "unexpected": True}


def _inspect_page(
    row: dict[str, Any], task: Task, resp: RawResponse, catalog: Catalog, raw_dir: Path
) -> None:
    accept = str(task.params.get("accept") or "text/html")
    is_json = "json" in accept or "json" in (resp.content_type or "")
    raw_dir.mkdir(parents=True, exist_ok=True)
    raw_path = raw_dir / f"{_safe_name(task.task_id)}.{'json' if is_json else 'html'}"
    raw_path.write_text(resp.text, encoding="utf-8")
    row.update(
        http_status=resp.status_code,
        final_url=resp.final_url,
        content_type=resp.content_type,
        page_len=len(resp.text),
        raw_file=f"raw/{raw_path.name}",
    )
    try:
        if not is_json:
            row["page_title"] = page_title(resp.text)
            row["json_script_ids"] = json_script_ids(resp.text)
        row["price_snippets"] = price_snippets(
            resp.text, _expected_currencies(task, catalog), is_json=is_json
        )
    except Exception as exc:  # diagnostics are best effort
        row["notes"].append(f"diagnóstico de la página falló: {_exc_text(exc)}")


async def probe_adapters(
    planned: list[PlannedTask],
    fetcher: Fetcher,
    catalog: Catalog,
    out_dir: Path,
    *,
    concurrency: int = 8,
) -> list[dict[str, Any]]:
    """Run every planned task: hosts in parallel, each host's tasks one after another.

    One worker per host (instead of one coroutine per task) keeps the per-task timeout about the
    request itself, not about waiting in the fetcher's per-host queue behind other tasks.
    """
    sem = asyncio.Semaphore(max(1, concurrency))
    by_host: dict[str, list[int]] = {}
    for i, p in enumerate(planned):
        by_host.setdefault(p.task.host or f"#{i}", []).append(i)
    rows: dict[int, dict[str, Any]] = {}

    async def worker(indices: list[int]) -> None:
        async with sem:
            for i in indices:
                rows[i] = await probe_task(planned[i], fetcher, catalog, out_dir / "raw")

    await asyncio.gather(*(worker(ix) for ix in by_host.values()))
    return [rows[i] for i in range(len(planned))]


# ============================================================================ markdown


def _cell(value: Any) -> str:
    return str(value if value is not None else "—").replace("|", "\\|").replace("\n", " ")


def _obs_cell(row: dict[str, Any]) -> str:
    obs = row["observations"]
    if not obs:
        return "—"
    if len(row["countries"]) > 1:
        with_prices = len({o["country"] for o in obs})
        return f"{with_prices}/{len(row['countries'])} países"
    items = [
        f"{o['plan']} {o['amount']} {o['currency']}"
        + ("" if o["period"] == "month" else f"/{o['period']}")
        for o in obs
    ]
    shown = ", ".join(items[:6])
    return shown + (f" (+{len(items) - 6})" if len(items) > 6 else "")


def _totals(rows: list[dict[str, Any]]) -> dict[str, int]:
    totals: dict[str, int] = {}
    for row in rows:
        totals[row["status"]] = totals.get(row["status"], 0) + 1
    return dict(sorted(totals.items()))


def render_adapters_md(rows: list[dict[str, Any]], *, generated_at: datetime) -> str:
    totals = _totals(rows)
    lines = [
        f"# Probe de adapters ({generated_at:%Y-%m-%d %H:%M} UTC)",
        "",
        f"**{len(rows)} tareas**: " + ", ".join(f"{k} {v}" for k, v in totals.items()),
        "",
        "| Estado | Tarea | HTTP | Obs. | Precios | URL final |",
        "|---|---|---|---|---|---|",
    ]
    for row in rows:
        lines.append(
            "| "
            + " | ".join(
                _cell(v)
                for v in (
                    row["status"],
                    row["task_id"],
                    row["http_status"],
                    len(row["observations"]),
                    _obs_cell(row),
                    row["final_url"] or row["url"],
                )
            )
            + " |"
        )
    failures = [r for r in rows if r["status"] not in QUIET_STATUSES]
    if failures:
        lines += ["", "## Detalles", ""]
        for row in failures:
            lines += _failure_details(row)
    not_configured = [r["task_id"] for r in rows if r["status"] == "not_configured"]
    if not_configured:
        lines += ["", f"Sin configurar ({len(not_configured)}): " + ", ".join(not_configured)]
    return "\n".join(lines) + "\n"


def _failure_details(row: dict[str, Any]) -> list[str]:
    out = [f"### {row['task_id']}: {row['status']}", ""]
    out.append(f"- URL: {row['url']}" + (f" -> {row['final_url']}" if row["final_url"] else ""))
    if row["http_status"] is not None or row["page_len"]:
        out.append(
            f"- HTTP {row['http_status']}, {row['page_len']} caracteres, "
            f"título: {row['page_title']!r}"
        )
    if row["json_script_ids"]:
        out.append("- Scripts JSON: " + ", ".join(row["json_script_ids"][:12]))
    if row["observations"]:
        out.append("- Observaciones: " + _obs_cell(row))
    if row["missing_plans"]:
        out.append("- Planes faltantes: " + ", ".join(row["missing_plans"]))
    if row["unmapped"]:
        items = [f"{u['name']!r} = {u['price_text']!r}" for u in row["unmapped"][:10]]
        out.append("- Sin mapear: " + "; ".join(items))
    if row["error"]:
        out.append(f"- Error: {row['error']}")
    for note in row["notes"][:8]:
        out.append(f"- Nota: {note[:300]}")
    if row["price_snippets"]:
        out += ["- Líneas con precio:", "", "```", *row["price_snippets"], "```"]
    out.append("")
    return out


# ============================================================================ fx mode


async def probe_fx(
    catalog: Catalog,
    settings: Settings,
    *,
    today: date,
    fx_service: Any | None = None,
) -> tuple[dict[str, Any], str]:
    """(summary json, markdown) for the FX table built today from the live providers."""
    currencies = catalog.currencies()
    if fx_service is None:
        import httpx

        from haggler.fx.service import DefaultFxService

        async with httpx.AsyncClient(timeout=30.0, follow_redirects=True) as client:
            svc = DefaultFxService(client, catalog.fx_pins, user_agent=settings.user_agent)
            table = await svc.build_table(currencies, as_of=today, previous=None)
            errors = list(svc.errors)
    else:
        table = await fx_service.build_table(currencies, as_of=today, previous=None)
        errors = list(getattr(fx_service, "errors", []) or [])

    rows = []
    for cur in sorted(currencies - {"EUR"}):
        rate = table.rates.get(cur)
        rows.append(
            {
                "currency": cur,
                "per_eur": str(rate.per_eur) if rate else None,
                "source": rate.source if rate else None,
                "provider": rate.provider if rate else None,
                "as_of": rate.as_of.isoformat() if rate else None,
                "stale": rate.stale if rate else None,
                "pinned": catalog.fx_pins.get(cur),
            }
        )
    missing = [r["currency"] for r in rows if r["per_eur"] is None]
    lines = [
        f"# Probe FX ({today.isoformat()})",
        "",
        f"**{len(rows) - len(missing)}/{len(rows)} monedas con tasa**"
        + (f"; sin tasa: {', '.join(missing)}" if missing else ""),
        "",
        "| Moneda | Por EUR | Fuente | Proveedor | Fecha | Vieja | Fijada |",
        "|---|---|---|---|---|---|---|",
    ]
    for r in rows:
        stale = "—" if r["stale"] is None else ("sí" if r["stale"] else "no")
        cells = (r["currency"], r["per_eur"], r["source"], r["provider"], r["as_of"], stale)
        lines.append("| " + " | ".join(_cell(v) for v in (*cells, r["pinned"])) + " |")
    if errors:
        lines += ["", "## Errores de proveedores", "", *(f"- {e}" for e in errors)]
    summary = {
        "mode": "fx",
        "as_of": table.as_of.isoformat(),
        "rates": rows,
        "errors": errors,
        "table": table.model_dump(mode="json"),
    }
    return summary, "\n".join(lines) + "\n"


# ============================================================================ full mode


@contextlib.contextmanager
def _env(**values: str) -> Iterator[None]:
    old = {k: os.environ.get(k) for k in values}
    os.environ.update(values)
    try:
        yield
    finally:
        for k, v in old.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v


def _daily_options(daily: Any, values: dict[str, Any]) -> Any | None:
    """haggler.jobs.daily.DailyOptions for this probe, or None when the module has no such
    dataclass (its CLI contract, daily.main(argv), is then used instead)."""
    options_cls = getattr(daily, "DailyOptions", None)
    if options_cls is None or not dataclasses.is_dataclass(options_cls):
        return None
    names = {f.name for f in dataclasses.fields(options_cls)}
    if not {"services", "countries"} <= names:
        return None  # a probe must never turn into a full-catalog run
    return options_cls(**{k: v for k, v in values.items() if k in names})


def _report_from_store(store_dir: Path) -> Any | None:
    run = _read_json(store_dir / "latest" / "run.json")
    if isinstance(run, dict) and run.get("run_id") and run.get("as_of"):
        report = _read_json(store_dir / "runs" / str(run["as_of"]) / run["run_id"] / "report.json")
        return report if report is not None else run.get("report")
    return None


def _read_json(path: Path) -> Any | None:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


def country_sets(targets: list[ProbeTarget]) -> list[tuple[list[str], list[str] | None]]:
    """(services, countries) per distinct country set of the merged targets (None = all
    countries), in first-seen order. The daily job filters by services x countries, so one run
    per set covers exactly the targeted pairs: a "*" target never lifts another one's filter."""
    groups: dict[frozenset[str] | None, list[str]] = {}
    for service_id, wanted in merge_targets(targets).items():
        groups.setdefault(None if wanted is None else frozenset(wanted), []).append(service_id)
    return [(services, None if key is None else sorted(key)) for key, services in groups.items()]


def _daily_run(
    daily: Any, services: list[str], countries: list[str] | None, *, llm: bool, store_dir: Path
) -> dict[str, Any]:
    """One daily job run for these services x countries (None = all countries)."""
    report: Any = None
    exit_code: int | None = None
    latest_before = _read_json(store_dir / "latest" / "run.json")
    options = _daily_options(
        daily,
        {
            "services": services,
            "countries": countries or [],
            "no_llm": not llm,
            "store": "local",
        },
    )
    run_daily = getattr(daily, "run_daily", None)
    if options is not None and run_daily is not None:
        result = run_daily(options)
        report = asyncio.run(result) if inspect.isawaitable(result) else result
        if report is None:
            exit_code = 1  # the workflow produced no report
    else:
        argv = ["--store", "local", "--services", ",".join(services)]
        if countries:
            argv += ["--countries", ",".join(countries)]
        if not llm:
            argv.append("--no-llm")
        exit_code = int(daily.main(argv) or 0)
    # Only a run this call made: an earlier run's report must not be attributed to this one.
    if report is None and _read_json(store_dir / "latest" / "run.json") != latest_before:
        report = _report_from_store(store_dir)
    if hasattr(report, "model_dump"):
        report = report.model_dump(mode="json")
    return {"services": services, "countries": countries, "exit_code": exit_code, "report": report}


def probe_full(
    targets: list[ProbeTarget],
    out_dir: Path,
    settings: Settings,
    *,
    today: date,
) -> tuple[dict[str, Any], str]:
    """Run the daily job for the targeted services/countries into <out>/raw/store, once per
    distinct country set (see country_sets); later runs see the earlier ones as published."""
    sets = country_sets(targets)
    services = list(merge_targets(targets))
    # The store holds raw pages and snapshots with evidence: never part of the summary.
    store_dir = out_dir / "raw" / "store"
    llm = settings.llm_enabled and llm_credentials_available()

    daily = importlib.import_module("haggler.jobs.daily")
    runs: list[dict[str, Any]] = []
    # The job builds its own Settings/Store from the environment: point it at the store.
    with _env(HAGGLER_STORE="local", HAGGLER_LOCAL_DIR=str(store_dir), HAGGLER_LLM=str(int(llm))):
        for group, countries in sets:
            runs.append(_daily_run(daily, group, countries, llm=llm, store_dir=store_dir))
    summary = {
        "mode": "full",
        "llm": llm,
        "services": services,
        "store": str(store_dir),
        "runs": runs,
    }
    lines = [
        f"# Probe completo ({today.isoformat()})",
        "",
        f"- Servicios: {', '.join(services)}",
        f"- LLM: {'sí' if llm else 'no'}",
        f"- Store: {store_dir}",
        f"- Corridas del job: {len(runs)} (una por conjunto de países)",
    ]
    for n, run in enumerate(runs, 1):
        report = run["report"]
        where = ", ".join(run["countries"]) if run["countries"] else "todos los países"
        lines += ["", f"## Corrida {n}/{len(runs)}: {', '.join(run['services'])} ({where})", ""]
        if run["exit_code"] is not None:
            lines.append(f"- Código de salida del job: {run['exit_code']}")
        if isinstance(report, dict):
            lines += [
                f"- Tareas: {report.get('tasks_total')} {report.get('tasks_by_status')}",
                f"- Observaciones: {report.get('obs_by_status')}",
            ]
        lines += [
            "",
            "```json",
            json.dumps(report, indent=2, ensure_ascii=False, default=str),
            "```",
        ]
    return summary, "\n".join(lines) + "\n"


# ============================================================================ entry point


def _write_outputs(out_dir: Path, summary: dict[str, Any], md: str, out: TextIO) -> None:
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / "summary.json").write_text(
        json.dumps(summary, indent=2, ensure_ascii=False, default=str) + "\n", encoding="utf-8"
    )
    (out_dir / "summary.md").write_text(md, encoding="utf-8")
    out.write(md)
    out.flush()


def run_probe(
    targets_file: Path,
    out_dir: Path,
    mode: str = "adapters",
    *,
    fetcher: Fetcher | None = None,
    catalog: Catalog | None = None,
    settings: Settings | None = None,
    fx_service: Any | None = None,
    today: date | None = None,
    now: datetime | None = None,
    out: TextIO | None = None,
    data_dir: Path | None = None,
) -> int:
    """Run one probe mode; writes <out>/summary.{json,md} and prints the markdown.

    Returns 0 unless something unexpected happened (an adapter, the bundle page analysis or the
    watcher crashed, the daily job raised); fetch failures, bot walls and empty pages are
    findings, not errors. `data_dir` (watch mode): the data/ directory whose curated/ and
    latest/ it reads, never writes (default the repo's).
    """
    if mode not in MODES:
        raise ValueError(f"modo desconocido {mode!r} (opciones: {', '.join(MODES)})")
    out = out or sys.stdout
    catalog = catalog or load_catalog()
    settings = settings or Settings()
    now = now or datetime.now(UTC)
    today = today or now.date()
    out_dir = Path(out_dir)
    if mode == "research":
        from haggler import probe_research

        summary, md = probe_research.run(targets_file, out_dir, settings, today=today)
        _write_outputs(out_dir, summary, md, out)
        return 1 if summary.get("error") else 0
    if mode == "bundles":
        from haggler import probe_bundles

        summary, md = probe_bundles.run(
            targets_file, out_dir, settings, catalog=catalog, fetcher=fetcher, now=now
        )
        _write_outputs(out_dir, summary, md, out)
        return 1 if any(r["unexpected"] for r in summary["targets"]) else 0
    if mode == "watch":
        from haggler import probe_watch

        summary, md = probe_watch.run(
            targets_file,
            out_dir,
            settings,
            catalog=catalog,
            fetcher=fetcher,
            now=now,
            today=today,
            data_dir=data_dir,
        )
        _write_outputs(out_dir, summary, md, out)
        unexpected = summary["error"] or any(r["unexpected"] for r in summary["bundles"])
        return 1 if unexpected else 0
    targets = parse_targets(Path(targets_file).read_text(encoding="utf-8"), catalog)

    if mode == "fx":
        summary, md = asyncio.run(probe_fx(catalog, settings, today=today, fx_service=fx_service))
        _write_outputs(out_dir, summary, md, out)
        return 0
    if mode == "full":
        try:
            summary, md = probe_full(targets, out_dir, settings, today=today)
        except Exception as exc:
            md = (
                f"# Probe completo ({today.isoformat()})\n\nEl job falló: {_exc_text(exc)}\n\n"
                f"```\n{traceback.format_exc(limit=8)}```\n"
            )
            _write_outputs(out_dir, {"mode": "full", "error": _exc_text(exc)}, md, out)
            return 1
        _write_outputs(out_dir, summary, md, out)
        return 0

    planned = plan_tasks(targets, catalog)
    rows = asyncio.run(_adapters(planned, fetcher, catalog, settings, out_dir))
    summary = {
        "mode": "adapters",
        "generated_at": now.astimezone(UTC).isoformat(),
        "targets_file": str(targets_file),
        "totals": _totals(rows),
        "tasks": rows,
    }
    _write_outputs(out_dir, summary, render_adapters_md(rows, generated_at=now), out)
    return 1 if any(r["unexpected"] for r in rows) else 0


async def _adapters(
    planned: list[PlannedTask],
    fetcher: Fetcher | None,
    catalog: Catalog,
    settings: Settings,
    out_dir: Path,
) -> list[dict[str, Any]]:
    owned = fetcher is None
    if fetcher is None:
        from haggler.fetch.client import HttpFetcher

        fetcher = HttpFetcher(settings)
    try:
        return await probe_adapters(
            planned, fetcher, catalog, out_dir, concurrency=settings.fetch_concurrency
        )
    finally:
        if owned:
            await fetcher.aclose()

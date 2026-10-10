"""`haggler probe --mode research`: one real research run, measured (GitHub runners only).

Answers the questions the dev container cannot: which Gemini models the key can use, whether
the schema + google_search combination answers, which domains the agents cite, how many claims
our own fetch verifies per source class, whether robots.txt lets us follow the grounding
redirects, and what a run costs in search queries and tokens.

Targets (one per line or ';'-separated):
    netflix                   the service's general rules
    spotify CO [US ...]       pairs (service x country)
    instrument revolut_eea    a payment instrument

Gate: when fewer than GATE_RATIO of the official citations verify, the plan adds a tool-less
quote extraction pass over our own page text (see docs). The summary says which side we are on.

Output: <out>/summary.{json,md} quote claims (<= QUOTE_CHARS chars each), never whole pages; the
store with the fetched pages stays in <out>/raw/.
"""

from __future__ import annotations

import asyncio
import dataclasses
import json
import re
from datetime import date
from pathlib import Path
from typing import Any

from haggler.catalog import (
    load_catalog,
    load_feasibility,
    load_payment_instruments,
    load_profiles,
    synthesize_profiles,
)
from haggler.config import Settings, llm_credentials_available
from haggler.research.evidence import host_of

GATE_RATIO = 0.6
QUOTE_CHARS = 160
REDIRECT_PROBE_URL = "https://vertexaisearch.cloud.google.com/grounding-api-redirect/probe"
_FLASH = re.compile(r"^gemini-(\d+(?:\.\d+)?)-flash(?:-preview)?(?:-\d{2}-\d{4})?$")


@dataclasses.dataclass
class ResearchTargets:
    services: list[str] = dataclasses.field(default_factory=list)
    pairs: list[str] = dataclasses.field(default_factory=list)
    instruments: list[str] = dataclasses.field(default_factory=list)
    errors: list[str] = dataclasses.field(default_factory=list)

    def empty(self) -> bool:
        return not (self.services or self.pairs or self.instruments)


def parse_research_targets(text: str) -> ResearchTargets:
    cat = load_catalog()
    instruments = {i.id for i in load_payment_instruments().instruments}
    services = {s.id for s in cat.services}
    ccs = set(cat.country_codes())
    out = ResearchTargets()
    for raw in re.split(r"[;\n]", text):
        line = raw.split("#", 1)[0].strip()
        if not line:
            continue
        head, *rest = line.split()
        if head == "instrument":
            for iid in rest:
                (out.instruments if iid in instruments else out.errors).append(
                    iid if iid in instruments else f"unknown instrument {iid}"
                )
            continue
        if head not in services:
            out.errors.append(f"unknown service {head}")
            continue
        if not rest:
            out.services.append(head)
        for cc in (c.upper() for c in rest):
            if cc in ccs:
                out.pairs.append(f"{head}:{cc}")
            else:
                out.errors.append(f"unknown country {cc}")
    return out


def flash_models(names: list[str]) -> list[str]:
    """Gemini flash model ids (not lite), newest version first."""
    found: list[tuple[float, bool, str]] = []
    for name in names:
        bare = name.rsplit("/", 1)[-1]
        m = _FLASH.match(bare)
        if m:
            found.append((float(m.group(1)), "preview" not in bare, bare))
    return [n for _, _, n in sorted(found, key=lambda x: (-x[0], not x[1], x[2]))]


def list_models() -> tuple[list[str], str | None]:
    try:
        from google import genai

        client = genai.Client()
        names = [str(m.name) for m in client.models.list()]
        return names, None
    except Exception as exc:
        return [], f"{type(exc).__name__}: {str(exc)[:200]}"


async def redirect_robots(settings: Settings) -> str:
    """Whether robots.txt lets our user agent follow grounding redirects."""
    from haggler.fetch.client import HttpFetcher
    from haggler.fetch.errors import FetchError, RobotsDisallowedError

    fetcher = HttpFetcher(settings)
    try:
        await fetcher._check_robots(REDIRECT_PROBE_URL)
        return "allowed"
    except RobotsDisallowedError as exc:
        return f"disallowed ({exc})"
    except FetchError as exc:
        return f"unknown ({exc})"
    finally:
        await fetcher.aclose()


async def known_pages(settings: Settings) -> list[dict[str, Any]]:
    """Fetch every research_url of the catalog and the instruments (no LLM needed): can our
    fetcher read the pages the quotes are checked against?"""
    from haggler.research.evidence import PageCache

    owners: dict[str, list[str]] = {}
    for svc in load_catalog().services:
        for url in svc.research_urls:
            owners.setdefault(url, []).append(svc.id)
    for inst in load_payment_instruments().instruments:
        for url in inst.research_urls:
            owners.setdefault(url, []).append(inst.id)
    from haggler.fetch.client import HttpFetcher

    fetcher = HttpFetcher(settings)
    pages = PageCache(fetcher)
    rows: list[dict[str, Any]] = []
    try:
        for url, who in owners.items():
            page = await pages.get(url)
            # Rendered lines: html_to_lines for HTML, the extracted text for a PDF.
            text_chars = len(" ".join(page.lines())) if page.status is None else 0
            rows.append(
                {
                    "url": url,
                    "subjects": who,
                    "status": page.status or "ok",
                    "http": page.http_status,
                    "final_url": page.final_url,
                    "content_type": page.content_type,
                    "text_chars": text_chars,
                    "detail": page.detail[:160],
                }
            )
    finally:
        await fetcher.aclose()
    return rows


async def _run(settings: Settings, targets: ResearchTargets, store_dir: Path, today: date):
    from haggler.fetch.client import HttpFetcher
    from haggler.jobs._runner import run_workflow_once
    from haggler.llm.models import build_model
    from haggler.research.app import build_research_app
    from haggler.research.deps import DEFAULT_PROFILE, DEFAULT_RESIDENCE, ResearchDeps
    from haggler.store import LocalStore

    feasibility = load_feasibility()
    deps = ResearchDeps(
        settings=settings,
        catalog=load_catalog(),
        feasibility=feasibility,
        instruments=load_payment_instruments(),
        profiles=load_profiles()
        or synthesize_profiles(feasibility, residence=DEFAULT_RESIDENCE, profile=DEFAULT_PROFILE),
        fetcher=HttpFetcher(settings),
        store=LocalStore(store_dir),
        model=build_model(settings, settings.research_model, max_calls=settings.research_max_calls),
    )
    request = {
        "date": today.isoformat(),
        "services": targets.services,
        "pairs": targets.pairs,
        "instruments": targets.instruments,
    }
    try:
        final = await run_workflow_once(
            build_research_app(deps),
            request,
            user_id="probe",
            session_id="probe-research",
            max_llm_calls=settings.research_max_calls,
        )
    finally:
        await deps.aclose()
    return final, deps


def _subject_rows(store_dir: Path, planned: list[str]) -> list[dict[str, Any]]:
    from haggler.research import io
    from haggler.store import LocalStore

    subjects = io.load_latest(LocalStore(store_dir)).subjects
    rows: list[dict[str, Any]] = []
    for key in planned:
        s = subjects.get(key)
        if s is None:
            rows.append({"key": key, "status": "not persisted"})
            continue
        rows.append(
            {
                "key": key,
                "status": s.status,
                "verified": [
                    {
                        "field": c.field,
                        "value": c.value,
                        "class": c.check.source_class,
                        "host": host_of(c.url),
                        "via": c.check.via,
                        "quote": c.quote[:QUOTE_CHARS],
                    }
                    for c in s.claims
                ],
                "unverified": [
                    {
                        "field": u.field,
                        "value": u.value,
                        "status": u.status,
                        "host": host_of(u.cited_url),
                        "detail": u.detail[:120],
                    }
                    for u in s.unverified
                ],
                "open_questions": s.open_questions,
            }
        )
    return rows


def probe_research(
    targets_text: str, out_dir: Path, settings: Settings, *, today: date
) -> tuple[dict[str, Any], str]:
    targets = parse_research_targets(targets_text)
    llm = settings.llm_enabled and llm_credentials_available()
    models, models_error = list_models() if llm else ([], "LLM off")
    flash = flash_models(models)
    configured = settings.research_model
    model = configured
    if flash and configured not in {m.rsplit("/", 1)[-1] for m in models}:
        model = flash[0]  # the configured id is not served to this key: use the newest flash
    settings = dataclasses.replace(
        settings,
        research_model=model,
        research_max_calls=min(settings.research_max_calls, 20),
        research_max_queries=min(settings.research_max_queries, 120),
    )
    robots = asyncio.run(redirect_robots(settings))
    pages = asyncio.run(known_pages(settings))
    store_dir = out_dir / "raw" / "store"
    report: dict[str, Any] | None = None
    error: str | None = None
    grounding: dict[str, Any] = {}
    if llm and not targets.empty():
        try:
            final, deps = asyncio.run(_run(settings, targets, store_dir, today))
            report = final if isinstance(final, dict) else None
            for run_id, records in deps.buffer.budget.grounding.items():
                grounding[run_id] = {
                    "queries": [q for r in records for q in r.queries],
                    "domains": sorted(
                        {
                            (s.domain or s.title or host_of(s.uri))
                            for r in records
                            for s in r.sources
                        }
                    ),
                    "finish": [r.finish_reason for r in records],
                }
        except Exception as exc:
            error = f"{type(exc).__name__}: {str(exc)[:400]}"
    rows = _subject_rows(store_dir, report.get("planned", [])) if report else []
    official = (report or {}).get("checks", {}).get("official", {})
    total_official = sum(official.values())
    ratio = official.get("verified", 0) / total_official if total_official else None
    summary = {
        "mode": "research",
        "llm": llm,
        "configured_model": configured,
        "model": model,
        "flash_models": flash[:8],
        "models_error": models_error,
        "redirect_robots": robots,
        "known_pages": pages,
        "targets": dataclasses.asdict(targets),
        "error": error,
        "official_verified_ratio": ratio,
        "gate": None if ratio is None else ("pass" if ratio >= GATE_RATIO else "fail"),
        "report": report,
        "grounding": grounding,
        "subjects": rows,
    }
    return summary, render_md(summary, today)


def render_md(summary: dict[str, Any], today: date) -> str:
    report = summary.get("report") or {}
    budget = report.get("budget", {})
    ratio = summary["official_verified_ratio"]
    lines = [
        f"# Probe de investigación ({today.isoformat()})",
        "",
        f"- LLM: {'sí' if summary['llm'] else 'no'}; modelo: `{summary['model']}`"
        + (
            f" (configurado `{summary['configured_model']}` no disponible)"
            if summary["model"] != summary["configured_model"]
            else ""
        ),
        f"- Modelos flash disponibles: {', '.join(summary['flash_models']) or '—'}"
        + (f" ({summary['models_error']})" if summary["models_error"] else ""),
        f"- robots.txt de las redirecciones de grounding: {summary['redirect_robots']}",
        f"- Targets: {json.dumps(summary['targets'], ensure_ascii=False)}",
    ]
    if summary["error"]:
        lines.append(f"- **Error:** {summary['error']}")
    if report:
        lines += [
            f"- Sujetos: {report.get('by_status')}",
            f"- Claims verificadas: {report.get('claims_verified')}, "
            f"no verificadas: {report.get('claims_unverified')}",
            f"- Verificación por clase de fuente: {json.dumps(report.get('checks'))}",
            f"- Llamadas: {budget.get('calls')}, consultas de búsqueda: {budget.get('queries')}, "
            f"tokens prompt/salida/pensamiento/herramienta: {budget.get('prompt_tokens')}/"
            f"{budget.get('output_tokens')}/{budget.get('thought_tokens')}/"
            f"{budget.get('tool_prompt_tokens')}",
            "- Citas oficiales verificadas: "
            + (
                "—"
                if ratio is None
                else f"{ratio:.0%} (compuerta {GATE_RATIO:.0%}: {summary['gate']})"
            ),
        ]
        if report.get("errors"):
            lines += ["", "## Errores", "", *(f"- {e}" for e in report["errors"])]
    for row in summary["subjects"]:
        lines += ["", f"## {row['key']} — {row['status']}", ""]
        for c in row.get("verified", []):
            via = "" if c["via"] == "cited" else f" vía {c['via']}"
            lines.append(
                f"- ✅ `{c['field']}={c['value']}` [{c['class']}] {c['host']}{via}: “{c['quote']}”"
            )
        for u in row.get("unverified", []):
            lines.append(
                f"- ❌ `{u['field']}={u['value']}` {u['status']} {u['host']} {u['detail']}"
            )
        for q in row.get("open_questions", []):
            lines.append(f"- ❓ {q}")
    if summary.get("known_pages"):
        ok = sum(1 for p in summary["known_pages"] if p["status"] == "ok")
        lines += [
            "",
            f"## Páginas oficiales conocidas ({ok}/{len(summary['known_pages'])} legibles)",
            "",
            "| URL | Sujetos | Estado | HTTP | Texto | Detalle |",
            "|---|---|---|---|---|---|",
        ]
        for p in summary["known_pages"]:
            final = p["final_url"] if p["final_url"] and p["final_url"] != p["url"] else ""
            lines.append(
                f"| {p['url']}{' → ' + final if final else ''} | {', '.join(p['subjects'])} | "
                f"{p['status']} | {p['http'] or '—'} | {p['text_chars']} | {p['detail']} |"
            )
    if summary["grounding"]:
        lines += ["", "## Grounding por agente", ""]
        for run_id, g in summary["grounding"].items():
            lines.append(
                f"- `{run_id}`: {len(g['queries'])} consultas; dominios: "
                f"{', '.join(g['domains']) or '—'}; fin: {g['finish']}"
            )
    return "\n".join(lines) + "\n"


def run(targets_file: Path, out_dir: Path, settings: Settings, *, today: date) -> tuple[dict, str]:
    return probe_research(
        Path(targets_file).read_text(encoding="utf-8"), Path(out_dir), settings, today=today
    )

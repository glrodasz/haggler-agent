"""Publishing: copy a finished run from the pipeline store into the repo (`sync`) and build the
static site's data files (`site_data`).

WHY two steps: `sync` turns the bot's store output into git-friendly files under data/ (one price
per line, an append-only change log), so a price change is a one-line diff and the history is
auditable in git. `site_data` is a pure function of data/ plus the catalog and curated YAML, so
the site can be rebuilt anywhere (CI, a laptop) without cloud access.

Store layout read by `sync` (written by the pipeline, keys relative to the store root):
  latest/{prices,fx,coverage,review_queue}.json and latest/run.json, written LAST, holding
  {"schema_version": 1, "run_id", "as_of", "finished_at", "files": {"prices.json": sha256, ...},
  "report": {...}}. The hashes are how we know every file belongs to the same finished run (the
  job may be writing the next run while we read).
"""

from __future__ import annotations

import hashlib
import json
import logging
import re
from collections.abc import Callable, Iterable
from dataclasses import dataclass, field
from datetime import UTC, date, datetime, timedelta
from decimal import ROUND_HALF_UP, Decimal
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

from pydantic import TypeAdapter, ValidationError

from haggler.bundles.evaluate import BundleAssessment, assess_bundle
from haggler.bundles.pricing import price_bundle, regular_price, to_eur
from haggler.bundles.status import BundleState, bundle_state
from haggler.compare import RISK_LEVELS, OfferRef, cheapest
from haggler.config import Settings
from haggler.feasibility import (
    LABEL_INFO,
    LABEL_RANKS,
    LABELS,
    UNKNOWN_LABEL,
    Verdict,
)
from haggler.fx import ecb, frankfurter, open_er_api
from haggler.models.catalog import Catalog
from haggler.models.curated import (
    Bundle,
    BundleComponent,
    BundleRequirements,
    BundlesFile,
    Evidence,
    FeasibilityFile,
    PaymentInstrumentsFile,
)
from haggler.models.fx import FxTable
from haggler.models.observations import PublishedPrice
from haggler.models.profiles import Profile, ProfilesFile
from haggler.models.research import ReviewFile
from haggler.models.run import CoverageRow
from haggler.research.evaluate import UNKNOWN_RANK, Assessment, EvalContext, assess
from haggler.store import Store, dumps_lines

log = logging.getLogger(__name__)

RUN_SCHEMA_VERSION = 1
LATEST = "latest"
# Files a run must list in run.json for the site to be buildable.
REQUIRED_FILES = ("prices.json", "fx.json", "coverage.json")
OPTIONAL_FILES = ("review_queue.json",)
CHANGE_KINDS = ("added", "changed", "removed")
CHANGE_WINDOW_DAYS = 90
# A run older than this (vs `today`) is still synced, with a warning: the job may be failing.
STALE_RUN_DAYS = 2
SITE_FILES = (
    "meta",
    "services",
    "countries",
    "fx",
    "offers",
    "feasibility",
    "cheapest",
    "bundles",
    "changes",
    "coverage",
)
CENT = Decimal("0.01")

_PRICES = TypeAdapter(list[PublishedPrice])
_COVERAGE = TypeAdapter(list[CoverageRow])
_VALUE = re.compile(r"^\s*(-?\d+(?:\.\d+)?)\s+([A-Z]{3})(?:\s*/\s*(\w+))?\s*$")


class SyncError(Exception):
    """The source run cannot be published (missing, corrupt or mixed files). Nothing was written."""


# ============================================================================ helpers


def sha256_hex(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def dumps_pretty(obj: Any) -> bytes:
    """Indented JSON with sorted keys: stable bytes for git and for the run.json hashes."""
    return (json.dumps(obj, ensure_ascii=False, indent=2, sort_keys=True) + "\n").encode()


def _write_atomic(path: Path, data: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_bytes(data)
    tmp.replace(path)


def _round2(value: Decimal | None) -> float | None:
    if value is None:
        return None
    return float(value.quantize(CENT, rounding=ROUND_HALF_UP))


def _plain_amount(amount: Decimal) -> str:
    """129.00 -> "129", 4.90 -> "4.90", 18900 -> "18900" (never exponent notation)."""
    if amount == amount.to_integral_value():
        return str(int(amount))
    return format(amount, "f")


def format_value(row: PublishedPrice) -> str:
    """Change-log value: "amount currency/period" (e.g. "129 SEK/month")."""
    return f"{_plain_amount(row.amount)} {row.currency}/{row.billing_period}"


def _read_json_file(path: Path) -> Any | None:
    if not path.exists():
        return None
    return json.loads(path.read_text(encoding="utf-8"))


def _read_jsonl(path: Path, *, strict: bool = True) -> list[dict[str, Any]]:
    """Objects of a JSONL file. A broken line aborts a sync (appending after it would hide the
    damage) but is only skipped, with a warning, when building the site."""
    if not path.exists():
        return []
    rows: list[dict[str, Any]] = []
    for n, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        if not line.strip():
            continue
        try:
            row = json.loads(line)
        except json.JSONDecodeError as exc:
            if strict:
                raise SyncError(f"{path}: línea {n} no es JSON válido ({exc})") from exc
            log.warning("%s: línea %d no es JSON válido; se omite", path, n)
            continue
        if isinstance(row, dict):
            rows.append(row)
    return rows


# ============================================================================ sync


@dataclass
class SyncResult:
    run_id: str
    as_of: date
    previous_run_id: str | None
    prices: int
    # Every change between the previous data/latest and this run (appended or already logged).
    changes: list[dict[str, Any]] = field(default_factory=list)
    appended: int = 0
    changes_file: Path | None = None
    written: list[Path] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    # The research run copied into data/research (None = no research synced this time).
    research_run_id: str | None = None
    research_subjects: int = 0

    def counts(self) -> dict[str, int]:
        out = dict.fromkeys(CHANGE_KINDS, 0)
        for change in self.changes:
            out[change["kind"]] = out.get(change["kind"], 0) + 1
        return out


def _read_run(source: Store) -> tuple[dict[str, Any], date]:
    raw = source.read_bytes(f"{LATEST}/run.json")
    if raw is None:
        raise SyncError(
            f"{LATEST}/run.json no existe en el origen: el job diario aún no ha terminado "
            "ninguna corrida (o el origen es otro)."
        )
    try:
        run = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise SyncError(f"{LATEST}/run.json no es JSON válido ({exc})") from exc
    if not isinstance(run, dict):
        raise SyncError(f"{LATEST}/run.json debe ser un objeto JSON")
    if run.get("schema_version") != RUN_SCHEMA_VERSION:
        raise SyncError(
            f"{LATEST}/run.json: schema_version {run.get('schema_version')!r} no soportado "
            f"(se espera {RUN_SCHEMA_VERSION})"
        )
    if not isinstance(run.get("run_id"), str) or not run["run_id"]:
        raise SyncError(f"{LATEST}/run.json sin run_id")
    try:
        as_of = date.fromisoformat(str(run.get("as_of")))
    except ValueError as exc:
        raise SyncError(f"{LATEST}/run.json: as_of inválido {run.get('as_of')!r}") from exc
    files = run.get("files")
    if not isinstance(files, dict) or not files:
        raise SyncError(f"{LATEST}/run.json no lista archivos ('files')")
    return run, as_of


def _verified_files(source: Store, run: dict[str, Any]) -> dict[str, bytes]:
    """Bytes of every file listed in run.json, after checking each sha256. All or nothing."""
    problems: list[str] = []
    blobs: dict[str, bytes] = {}
    for listed, digest in sorted(run["files"].items()):
        name = str(listed).removeprefix(f"{LATEST}/")
        if not name or "/" in name or name == "run.json" or ".." in name:
            problems.append(f"{listed}: nombre de archivo no válido en run.json")
            continue
        data = source.read_bytes(f"{LATEST}/{name}")
        if data is None:
            problems.append(f"{name}: listado en run.json pero no existe")
            continue
        actual = sha256_hex(data)
        if not isinstance(digest, str) or actual != digest.strip().lower():
            problems.append(
                f"{name}: sha256 {actual[:12]}… no coincide con run.json ({str(digest)[:12]}…)"
            )
            continue
        blobs[name] = data
    for name in REQUIRED_FILES:
        if name not in {str(k).removeprefix(f"{LATEST}/") for k in run["files"]}:
            problems.append(f"{name}: no aparece en run.json")
    if problems:
        raise SyncError(
            f"la corrida {run['run_id']} no se puede sincronizar; no se escribió nada. Si el job "
            "estaba escribiendo latest/ en ese momento, reintenta más tarde.\n  - "
            + "\n  - ".join(problems)
        )
    return blobs


def _parse_source(blobs: dict[str, bytes]) -> tuple[list[PublishedPrice], FxTable, list, list]:
    try:
        prices = _PRICES.validate_json(blobs["prices.json"])
        fx = FxTable.model_validate_json(blobs["fx.json"])
        coverage = _COVERAGE.validate_json(blobs["coverage.json"])
        review = json.loads(blobs["review_queue.json"]) if "review_queue.json" in blobs else []
    except (ValidationError, json.JSONDecodeError) as exc:
        raise SyncError(f"archivos de la corrida con formato inesperado: {exc}") from exc
    if not isinstance(review, list):
        raise SyncError("review_queue.json debe ser una lista JSON")
    keys = [p.obs_key for p in prices]
    if len(keys) != len(set(keys)):
        raise SyncError("prices.json tiene obs_key duplicados")
    return prices, fx, coverage, review


def load_published(data_dir: Path) -> list[PublishedPrice]:
    path = Path(data_dir) / LATEST / "prices.json"
    if not path.exists():
        return []
    try:
        return _PRICES.validate_json(path.read_bytes())
    except ValidationError as exc:
        raise SyncError(
            f"{path} no es válido; corrígelo o bórralo antes de sincronizar: {exc}"
        ) from exc


def diff_prices(
    old: Iterable[PublishedPrice], new: Iterable[PublishedPrice], *, run_id: str, as_of: date
) -> list[dict[str, Any]]:
    """Change records between two published price lists, keyed by obs_key.

    Only the value (amount, currency, period) counts: flags, status, source_url or confidence
    changing alone is metadata and stays out of the change log, so the log reads as price history.
    """
    before = {p.obs_key: p for p in old}
    after = {p.obs_key: p for p in new}
    out: list[dict[str, Any]] = []
    for key in sorted(before.keys() | after.keys()):
        prev, cur = before.get(key), after.get(key)
        pct: float | None = None
        if prev is None:
            kind = "added"
        elif cur is None:
            kind = "removed"
        elif (prev.amount, prev.currency, prev.billing_period) == (
            cur.amount,
            cur.currency,
            cur.billing_period,
        ):
            continue
        else:
            kind = "changed"
            if prev.currency == cur.currency and prev.amount > 0:
                pct = _round2((cur.amount - prev.amount) / prev.amount * 100)
        row = cur or prev
        assert row is not None
        out.append(
            {
                "date": as_of.isoformat(),
                "run_id": run_id,
                "obs_key": key,
                "service_id": row.service_id,
                "plan_id": row.plan_id,
                "country": row.country,
                "channel": row.channel,
                "kind": kind,
                "old": format_value(prev) if prev else None,
                "new": format_value(cur) if cur else None,
                "pct": pct,
                "source_url": row.source_url,
                "extraction": row.extraction,
            }
        )
    return out


def _change_id(record: dict[str, Any]) -> tuple[Any, Any, Any]:
    return (record.get("run_id"), record.get("obs_key"), record.get("kind"))


def append_changes(changes_dir: Path, records: list[dict[str, Any]]) -> tuple[int, Path | None]:
    """Append to changes/YYYY-MM.jsonl, skipping records already logged for the same
    run_id + obs_key + kind (a re-run of the same sync must not duplicate history)."""
    appended = 0
    last: Path | None = None
    by_month: dict[str, list[dict[str, Any]]] = {}
    for record in records:
        by_month.setdefault(record["date"][:7], []).append(record)
    for month, rows in sorted(by_month.items()):
        path = Path(changes_dir) / f"{month}.jsonl"
        last = path
        seen = {_change_id(r) for r in _read_jsonl(path)}
        fresh = []
        for row in rows:
            if _change_id(row) not in seen:
                seen.add(_change_id(row))
                fresh.append(row)
        if not fresh:
            continue
        path.parent.mkdir(parents=True, exist_ok=True)
        prefix = ""
        if path.exists() and path.stat().st_size and not path.read_bytes().endswith(b"\n"):
            prefix = "\n"
        lines = "".join(
            json.dumps(r, ensure_ascii=False, sort_keys=True, separators=(",", ":")) + "\n"
            for r in fresh
        )
        with path.open("a", encoding="utf-8") as fh:
            fh.write(prefix + lines)
        appended += len(fresh)
    return appended, last


def sync(source: Store, data_dir: Path, *, today: date, allow_older: bool = False) -> SyncResult:
    """Publish the source store's latest run into data_dir (see the module docstring).

    Order matters for crash safety: everything is verified and parsed before the first write; the
    change log is appended before data/latest is replaced (a crash in between re-computes the same
    changes and the idempotent append skips them); run.json is written last.
    """
    data_dir = Path(data_dir)
    run, as_of = _read_run(source)
    blobs = _verified_files(source, run)
    prices, fx, coverage, review = _parse_source(blobs)
    run_id: str = run["run_id"]

    warnings: list[str] = []
    try:
        local_run = _read_json_file(data_dir / LATEST / "run.json")
    except ValueError:
        local_run = None
        warnings.append(f"{data_dir / LATEST / 'run.json'} no es JSON válido; se reemplaza")
    previous_run_id = local_run.get("run_id") if isinstance(local_run, dict) else None
    if isinstance(local_run, dict) and local_run.get("as_of") and previous_run_id != run_id:
        try:
            local_as_of = date.fromisoformat(str(local_run["as_of"]))
        except ValueError:
            local_as_of = None
        if local_as_of and as_of < local_as_of and not allow_older:
            raise SyncError(
                f"la corrida del origen ({run_id}, {as_of}) es más antigua que la publicada "
                f"({previous_run_id}, {local_as_of}); no se sincroniza hacia atrás"
            )
    age = (today - as_of).days
    if age > STALE_RUN_DAYS:
        warnings.append(f"la última corrida es del {as_of} (hace {age} días): ¿falla el job?")
    elif age < 0:
        warnings.append(f"la corrida tiene fecha futura ({as_of} > {today})")

    old = load_published(data_dir)
    changes = diff_prices(old, prices, run_id=run_id, as_of=as_of)
    appended, changes_file = append_changes(data_dir / "changes", changes)

    latest = data_dir / LATEST
    outputs: dict[str, bytes] = {
        "prices.json": dumps_lines(sorted(prices, key=lambda p: p.obs_key)),
        "fx.json": dumps_pretty(fx.model_dump(mode="json")),
        "coverage.json": dumps_pretty(
            [
                c.model_dump(mode="json")
                for c in sorted(coverage, key=lambda c: (c.service_id, c.country, c.channel))
            ]
        ),
        "review_queue.json": dumps_pretty(review),
    }
    written: list[Path] = []
    for name, data in outputs.items():
        _write_atomic(latest / name, data)
        written.append(latest / name)
    # Hashes of the files as written here, so data/latest is itself a valid sync source.
    run_out = {**run, "files": {name: sha256_hex(data) for name, data in sorted(outputs.items())}}
    _write_atomic(latest / "run.json", dumps_pretty(run_out))
    written.append(latest / "run.json")

    try:
        research = sync_research(source, data_dir)
    except SyncError as exc:  # research is optional: never fails the price sync
        research = None
        warnings.append(f"investigación no sincronizada: {exc}")
    if research is not None:
        written.extend(research.written)
        warnings.extend(research.warnings)

    for message in warnings:
        log.warning("sync: %s", message)
    return SyncResult(
        research_run_id=research.run_id if research and research.written else None,
        research_subjects=research.subjects if research else 0,
        run_id=run_id,
        as_of=as_of,
        previous_run_id=previous_run_id,
        prices=len(prices),
        changes=changes,
        appended=appended,
        changes_file=changes_file if appended else None,
        written=written,
        warnings=warnings,
    )


# ============================================================================ research sync


@dataclass
class ResearchSyncResult:
    run_id: str | None
    as_of: date | None
    subjects: int = 0
    written: list[Path] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)


def sync_research(source: Store, data_dir: Path) -> ResearchSyncResult | None:
    """Copy research/latest/{services,pairs,instruments}.json into data/research (run.json last).

    None when the source has no research yet. Raises SyncError when run.json or a listed file is
    broken (nothing is written then); an older research run than the published one is skipped
    with a warning.
    """
    from haggler.research import io as rio

    raw = source.read_bytes(f"{rio.LATEST}/run.json")
    if raw is None:
        return None
    try:
        run = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise SyncError(f"{rio.LATEST}/run.json no es JSON válido ({exc})") from exc
    if not isinstance(run, dict) or run.get("schema_version") != rio.RUN_SCHEMA_VERSION:
        raise SyncError(f"{rio.LATEST}/run.json: formato o schema_version no soportado")
    run_id = run.get("run_id")
    try:
        as_of = date.fromisoformat(str(run.get("as_of")))
    except ValueError as exc:
        raise SyncError(f"{rio.LATEST}/run.json: as_of inválido") from exc
    files = run.get("files") if isinstance(run.get("files"), dict) else {}
    blobs: dict[str, bytes] = {}
    problems: list[str] = []
    for name in rio.DATA_FILES:
        digest = files.get(name)
        data = source.read_bytes(f"{rio.LATEST}/{name}")
        if digest is None or data is None:
            problems.append(f"{name}: falta en el origen o en run.json")
            continue
        if sha256_hex(data) != str(digest).strip().lower():
            problems.append(f"{name}: sha256 no coincide con run.json")
            continue
        try:
            rows = rio.parse_subjects_bytes(data)
        except ValidationError as exc:
            problems.append(f"{name}: formato inesperado ({str(exc)[:200]})")
            continue
        blobs[name] = rio.subject_files(rows)[name]
    if problems:
        raise SyncError(f"investigación {run_id}: " + "; ".join(problems))

    target = Path(data_dir) / rio.PREFIX
    result = ResearchSyncResult(run_id=run_id, as_of=as_of)
    try:
        local = _read_json_file(target / "run.json")
    except ValueError:
        local = None
    if isinstance(local, dict) and local.get("run_id") != run_id:
        try:
            local_as_of = date.fromisoformat(str(local.get("as_of")))
        except ValueError:
            local_as_of = None
        if local_as_of and as_of < local_as_of:
            result.warnings.append(
                f"la investigación del origen ({run_id}, {as_of}) es más antigua que la publicada "
                f"({local.get('run_id')}, {local_as_of}); no se copia"
            )
            return result
    for name in rio.DATA_FILES:
        _write_atomic(target / name, blobs[name])
        result.written.append(target / name)
        result.subjects += len(json.loads(blobs[name]))
    manifest = {
        "schema_version": rio.RUN_SCHEMA_VERSION,
        "run_id": run_id,
        "as_of": as_of.isoformat(),
        "finished_at": run.get("finished_at"),
        "files": {name: sha256_hex(blobs[name]) for name in rio.DATA_FILES},
        "report": run.get("report", {}),
    }
    _write_atomic(target / "run.json", dumps_pretty(manifest))
    result.written.append(target / "run.json")
    return result


# ============================================================================ site data


@dataclass
class _Latest:
    run: dict[str, Any]
    prices: list[PublishedPrice]
    fx: FxTable | None
    coverage: list[CoverageRow]


def _load_latest(data_dir: Path) -> _Latest:
    """data/latest as published by `sync`; every file may be missing (first deploy)."""
    latest = Path(data_dir) / LATEST
    run = _read_json_file(latest / "run.json")
    fx_raw = _read_json_file(latest / "fx.json")
    coverage_raw = _read_json_file(latest / "coverage.json")
    prices_path = latest / "prices.json"
    prices = _PRICES.validate_json(prices_path.read_bytes()) if prices_path.exists() else []
    return _Latest(
        run=run if isinstance(run, dict) else {},
        prices=prices,
        fx=FxTable.model_validate(fx_raw) if fx_raw else None,
        coverage=_COVERAGE.validate_python(coverage_raw or []),
    )


def _attributions(catalog: Catalog) -> list[dict[str, str]]:
    """Data sources whose terms ask for credit: licensed secondary datasets (from the source's
    `license`/`attribution` params, e.g. tompec CC BY 4.0) and the FX providers."""
    out: list[dict[str, str]] = []
    seen: set[str] = set()
    for svc in catalog.services:
        for src in svc.sources:
            params = src.params
            if not (params.get("license") or params.get("attribution")):
                continue
            url = str(params.get("attribution_url") or _project_url(str(params.get("url", ""))))
            if url in seen:
                continue
            seen.add(url)
            out.append(
                {
                    "name": str(params.get("attribution") or src.adapter),
                    "url": url,
                    "license": str(params.get("license") or ""),
                }
            )
    for module in (ecb, frankfurter, open_er_api):
        info = module.ATTRIBUTION
        if info["url"] not in seen:
            seen.add(info["url"])
            out.append({"name": info["name"], "url": info["url"], "license": info["license"]})
    return out


def _project_url(url: str) -> str:
    """raw.githubusercontent.com/<owner>/<repo>/... -> github.com/<owner>/<repo> (human page)."""
    parts = urlsplit(url)
    segments = [s for s in parts.path.split("/") if s]
    if parts.netloc == "raw.githubusercontent.com" and len(segments) >= 2:
        return f"https://github.com/{segments[0]}/{segments[1]}"
    return url


def _services_json(catalog: Catalog, bundles: BundlesFile) -> list[dict[str, Any]]:
    # Every service a bundle names: its components, its observed plan and what it covers (the
    # site reaches a bundle from each of their pages).
    referenced = {c.service_id for b in bundles.bundles for c in b.components}
    referenced |= {sid for b in bundles.bundles if (sid := b.price.observed_service_id)}
    referenced |= {sid for b in bundles.bundles for sid in b.covered_services()}
    return [
        {
            "id": s.id,
            "name": s.name,
            "category": s.category,
            "homepage": s.homepage,
            "default_plan": s.default_plan,
            "notes_es": s.notes_es.strip(),
            "eu_geo_class": s.eu_geo_class,
            "plans": [
                {"id": p.id, "label_es": p.label_es, "rank": p.rank, "ads": p.ads} for p in s.plans
            ],
        }
        for s in catalog.services
        if s.sources or s.id in referenced
    ]


def _countries_json(catalog: Catalog) -> list[dict[str, Any]]:
    return [
        {
            "cc": c.cc,
            "name_es": c.name_es,
            "currency": c.currency,
            "region": c.region,
            "eu_eea": c.eu_eea,
        }
        for c in catalog.countries
    ]


def _fx_json(fx: FxTable | None) -> dict[str, Any]:
    if fx is None:
        return {"as_of": None, "base": "EUR", "rates": {}, "stale": []}
    return {
        "as_of": fx.as_of.isoformat(),
        "base": "EUR",
        "rates": {cur: float(rate.per_eur) for cur, rate in sorted(fx.rates.items())},
        "stale": sorted(cur for cur, rate in fx.rates.items() if rate.stale),
    }


def _offer_json(
    p: PublishedPrice, *, fx: FxTable | None, verdicts: Callable[[str, str], Verdict]
) -> dict[str, Any]:
    v = verdicts(p.service_id, p.country)
    return {
        "obs_key": p.obs_key,
        "service_id": p.service_id,
        "plan_id": p.plan_id,
        "country": p.country,
        "channel": p.channel,
        "currency": p.currency,
        "amount": _round2(p.amount),
        "billing_period": p.billing_period,
        "monthly_equiv": _round2(p.monthly_equiv),
        "monthly_eur": _round2(to_eur(p.monthly_equiv, p.currency, fx)),
        "vat_included": p.vat_included,
        "source_url": p.source_url,
        "source_kind": p.source_kind,
        "extraction": p.extraction,
        "status": p.status,
        "flags": list(p.flags),
        "first_seen": p.first_seen.isoformat(),
        "last_changed": p.last_changed.isoformat(),
        "label": v.label,
        "rank": v.rank,
        "eu_badge": v.eu_badge,
    }


def _assessment_json(a: Assessment) -> dict[str, Any]:
    tos_clause = next((e for e in a.evidence if a.provenance == "curated" and e.quote), None)
    return {
        "profile": a.profile,
        "service_id": a.service_id,
        "target_country": a.target_country,
        "label": a.label,
        "rank": a.rank,
        "eu_badge": a.eu_badge,
        "legality": a.legality,
        "tos": a.tos,
        "practical": a.practical,
        "confidence": a.confidence,
        "status": a.status,
        "provenance": a.provenance,
        "notes_es": a.notes_es,
        "tos_clause_url": str(tos_clause.url) if tos_clause else None,
        "steps": [s.model_dump(mode="json") for s in a.steps],
        "evidence": [
            {
                "url": str(e.url),
                "kind": e.kind,
                "quote": e.quote,
                "retrieved_at": e.retrieved_at.isoformat() if e.retrieved_at else None,
                "verified": e.verified,
            }
            for e in a.evidence
        ],
        "assumptions_es": list(a.assumptions_es),
        "reasons_es": list(a.reasons_es),
        "researched_on": a.researched_on.isoformat() if a.researched_on else None,
    }


def _same_verdict(a: Assessment, b: Assessment) -> bool:
    return (a.label, a.rank, a.legality, a.tos, a.practical, a.eu_badge) == (
        b.label,
        b.rank,
        b.legality,
        b.tos,
        b.practical,
        b.eu_badge,
    )


def _listed(a: Assessment, researched_pair: bool) -> bool:
    return a.evaluated and (a.rank != UNKNOWN_RANK or a.provenance == "curated" or researched_pair)


def _profile_assessments(
    profile: Profile, ctx: EvalContext, rows: list[PublishedPrice]
) -> list[Assessment]:
    """Every record the site shows for one profile: curated ones, research-derived verdicts
    without pending claims, and (when they differ) with them. Derived "unknown" verdicts are only
    listed for researched pairs, so the table shows their evidence without listing every offer."""
    r = profile.residence_country
    keys = {(p.service_id, p.country) for p in rows if p.country != r}
    keys |= {
        (rec.service_id, rec.target_country)
        for rec in ctx.feasibility.records
        if rec.profile == profile.id and rec.residence_country == r
    }
    keys |= {
        (k.split(":")[1], k.split(":")[2])
        for k in ctx.facts
        if k.startswith("pair:") and k.count(":") == 2
    }
    out: list[Assessment] = []
    for service_id, cc in sorted(keys):
        if cc == r:
            continue
        researched_pair = f"pair:{service_id}:{cc}" in ctx.facts
        reviewed = assess(service_id, cc, profile, ctx)
        pending = assess(service_id, cc, profile, ctx, include_pending=True)
        show_reviewed = _listed(reviewed, researched_pair)
        if show_reviewed:
            out.append(reviewed)
        if (
            pending.provenance == "agent_pending"
            and _listed(pending, researched_pair)
            and (not show_reviewed or not _same_verdict(reviewed, pending))
        ):
            out.append(pending)
    return out


def _public_profiles(profiles: ProfilesFile, default: Profile) -> list[Profile]:
    """The profiles the site evaluates: the default one first, then the other public ones."""
    return [default, *(p for p in profiles.public() if p.id != default.id)]


def _feasibility_json(
    profiles: ProfilesFile, default: Profile, ctx: EvalContext, rows: list[PublishedPrice]
) -> dict[str, Any]:
    labels = [
        {
            "id": label,
            "rank": LABEL_RANKS[label],
            "name_es": LABEL_INFO[label][0],
            "description_es": LABEL_INFO[label][1],
        }
        for label in (*LABELS, UNKNOWN_LABEL)
    ]
    public = _public_profiles(profiles, default)
    records = [_assessment_json(a) for p in public for a in _profile_assessments(p, ctx, rows)]
    records.sort(
        key=lambda r: (
            r["profile"] != default.id,
            r["profile"],
            r["service_id"],
            r["target_country"],
            r["provenance"] == "agent_pending",
        )
    )
    return {
        "labels": labels,
        "default_profile": default.id,
        "profiles": [
            {
                "id": p.id,
                "name_es": p.name_es,
                "description_es": " ".join(p.description_es.split()),
                "residence_country": p.residence_country,
            }
            for p in public
        ],
        "records": records,
    }


def _ref_json(ref: OfferRef | None) -> dict[str, Any] | None:
    if ref is None:
        return None
    return {
        "obs_key": ref.obs_key,
        "country": ref.country,
        "channel": ref.channel,
        "currency": ref.currency,
        "amount": _round2(ref.amount),
        "monthly_equiv": _round2(ref.monthly_equiv),
        "cost_eur": _round2(ref.cost_eur),
        "cost_eur_with_fee": _round2(ref.cost_eur_with_fee),
        "label": ref.label,
        "rank": ref.rank,
        "eu_badge": ref.eu_badge,
        "source_url": ref.source_url,
    }


def _cheapest_json(
    rows: list[PublishedPrice],
    *,
    fx: FxTable | None,
    catalog: Catalog,
    feas: FeasibilityFile,
    residence: str,
    profile: str,
    fx_fee_pct: float,
    verdicts: Callable[[str, str], Verdict],
) -> list[dict[str, Any]]:
    if fx is None:
        return []
    result = cheapest(
        rows,
        fx=fx,
        catalog=catalog,
        feasibility=feas,
        residence=residence,
        profile=profile,
        fx_fee_pct=fx_fee_pct,
        verdicts=verdicts,
    )
    return [
        {
            "service_id": row.service_id,
            "plan_id": row.plan_id,
            "home": _ref_json(row.home),
            "best_by_risk": {str(k): _ref_json(row.best_by_risk.get(k)) for k in RISK_LEVELS},
            "savings_month_eur": {
                str(k): _round2(row.savings_month_eur.get(k)) for k in RISK_LEVELS
            },
        }
        for row in result
    ]


def _iso(day: date | None) -> str | None:
    return day.isoformat() if day else None


def _known(value: str) -> str | None:
    """A free-text or enum requirement, null when it is not established ("unknown")."""
    return None if value.strip().lower() == "unknown" else value


def _requirements_json(r: BundleRequirements) -> dict[str, Any]:
    return {
        "local_id": _known(r.local_id),
        "local_line": _known(r.local_line),
        "local_payment": _known(r.local_payment),
        "credit_check": r.credit_check,
        "residency": r.residency,
        "billing_address": r.billing_address,
        "signup_presence": r.signup_presence,
        "new_customers_only": r.new_customers_only,
    }


def _bundle_verdict_json(a: BundleAssessment) -> dict[str, Any]:
    return {
        "profile": a.profile,
        "provenance": a.provenance,
        "label": a.label,
        "rank": a.rank,
        "eu_badge": a.eu_badge,
        "legality": a.legality,
        "tos": a.tos,
        "practical": a.practical,
        "confidence": a.confidence,
        "status": a.status,
        "notes_es": a.notes_es,
        "steps": [s.model_dump(mode="json") for s in a.steps],
        "assumptions_es": list(a.assumptions_es),
        "reasons_es": list(a.reasons_es),
        "components": [
            {
                "service_id": c.service_id,
                "target_country": c.target_country,
                "label": c.label,
                "rank": c.rank,
                "provenance": c.provenance,
            }
            for c in a.components
        ],
    }


def _bundle_verdicts(
    b: Bundle,
    profiles: list[Profile],
    ctx: EvalContext,
    counted: tuple[BundleComponent, ...],
    state: BundleState,
) -> list[BundleAssessment]:
    """One verdict per public profile (default first). As in feasibility.json, the variant with
    findings still pending review follows its reviewed verdict only when it differs. An expired
    bundle has none."""
    out: list[BundleAssessment] = []
    for profile in profiles:
        reviewed = assess_bundle(b, profile, ctx, counted=counted, state=state)
        if reviewed is None:
            return []
        out.append(reviewed)
        pending = assess_bundle(b, profile, ctx, include_pending=True, counted=counted, state=state)
        if (
            pending is not None
            and pending.provenance == "agent_pending"
            and not reviewed.same_verdict(pending)
        ):
            out.append(pending)
    return out


def _bundle_evidence_json(e: Evidence) -> dict[str, Any]:
    """A source listed in bundles.yaml. fact/via/edition/status come from the watcher
    (milestone H7); until then a source is never verified."""
    return {
        "fact": None,
        "url": str(e.url),
        "kind": e.kind,
        "quote": e.quote or None,
        "retrieved_at": _iso(e.retrieved_at),
        "verified": e.verified,
        "via": None,
        "edition": None,
        "status": None,
    }


def _bundle_json(
    b: Bundle,
    *,
    rows: list[PublishedPrice],
    fx: FxTable | None,
    residence_currency: str,
    fx_fee_pct: float,
    today: date,
    as_of: date | None,
    profiles: list[Profile],
    ctx: EvalContext,
) -> dict[str, Any] | None:
    """bundles.json v2 (None once an expired bundle leaves the site)."""
    regular = regular_price(b, rows, as_of=as_of)
    state = bundle_state(b, today=today, price_missing=regular.amount is None)
    if not state.published:
        return None
    pricing = price_bundle(
        b,
        rows,
        fx,
        residence_currency=residence_currency,
        fx_fee_pct=fx_fee_pct,
        today=today,
        as_of=as_of,
        verified=state.display_status == "verified",
    )
    p = b.price
    return {
        "bundle_id": b.bundle_id,
        "provider_id": b.provider_id,
        "provider_type": b.provider_type,
        "country": b.country,
        "name": b.name,
        "tier": b.tier,
        "official_url": str(b.official_url),
        "observed_service_id": p.observed_service_id if p.source == "observed" else None,
        "covers": b.covered_services(),
        "price": {
            "amount": _round2(pricing.regular.amount),
            "currency": pricing.regular.currency,
            "period": pricing.regular.period,
            "tax_included": p.tax_included,
            "source": p.source,
            "observed_on": _iso(pricing.regular.observed_on),
            "base_plan_name": p.base_plan_name,
            "base_plan_amount": _round2(p.base_plan_amount),
            "marginal_amount": _round2(pricing.marginal_amount),
            "intro_amount": _round2(p.intro_amount),
            "intro_months": p.intro_months,
            "intro_valid_until": _iso(p.intro_valid_until),
            "contract_months": p.contract_months,
        },
        "monthly_eur": _round2(pricing.monthly_eur),
        "full_monthly_eur": _round2(pricing.full_monthly_eur),
        "monthly_eur_with_fee": _round2(pricing.monthly_eur_with_fee),
        "first_year_monthly_eur": _round2(pricing.first_year_monthly_eur),
        "saving_basis": pricing.saving_basis,
        "standalone_sum_eur": _round2(pricing.standalone_sum_eur),
        "standalone_missing": list(pricing.standalone_missing),
        "saving_pct": _round2(pricing.saving_pct),
        "optional_savings": [
            {
                "service_id": o.service_id,
                "inclusion": o.inclusion,
                "value": _round2(o.value),
                "saving_month_eur": _round2(o.saving_month_eur),
            }
            for o in pricing.optional_savings
        ],
        "components": [
            {
                "service_id": c.service_id,
                "plan_id": c.plan_id,
                "inclusion": c.inclusion,
                "value": _round2(c.value),
                "ads": c.ads,
                "choice_group_id": c.choice_group_id,
                "choose_n": c.choose_n,
                "standalone_eur": _round2(eur),
                # Confirmed only by the watcher's anchors (milestone H7).
                "confirmed": False,
            }
            for c, eur in zip(b.components, pricing.component_eur, strict=True)
        ],
        "requirements": _requirements_json(b.requirements),
        "display_status": state.display_status,
        "status_reasons_es": list(state.reasons_es),
        "verified_by": b.verified_by,
        "verified_at": _iso(state.verified_at),
        "checked_at": _iso(state.checked_at),
        "valid_from": _iso(b.valid_from),
        "valid_until": _iso(b.valid_until),
        "confidence": state.confidence,
        "watch_mode": b.watch.mode,
        "evidence": [_bundle_evidence_json(e) for e in b.evidence],
        "verdicts": [
            _bundle_verdict_json(a)
            for a in _bundle_verdicts(b, profiles, ctx, pricing.counted, state)
        ],
        # Proposals of the watcher and the agent (milestone H8).
        "pending_changes": [],
        "notes_es": b.notes_es.strip(),
        "last_verified": b.last_verified.isoformat(),
    }


def _bundles_json(bundles: BundlesFile, **kwargs: Any) -> list[dict[str, Any]]:
    """bundles.json v2, in bundles.yaml order (expired bundles past their window left out)."""
    return [row for b in bundles.bundles if (row := _bundle_json(b, **kwargs)) is not None]


def _change_value(value: Any) -> Any:
    """Turn "129 SEK/month" into {"amount": 129.0, "currency": "SEK"} so the site formats it per
    locale. The currency is kept: a removed offer has no current row to take it from."""
    if value is None or isinstance(value, int | float):
        return value
    if isinstance(value, str) and (m := _VALUE.match(value)):
        return {"amount": _round2(Decimal(m.group(1))), "currency": m.group(2)}
    return value


def load_changes(changes_dir: Path, *, since: date) -> list[dict[str, Any]]:
    """Change records dated on/after `since` from changes/*.jsonl, newest first."""
    rows: dict[tuple, dict[str, Any]] = {}
    base = Path(changes_dir)
    if not base.exists():
        return []
    for path in sorted(base.glob("*.jsonl")):
        for r in _read_jsonl(path, strict=False):
            try:
                day = date.fromisoformat(str(r.get("date")))
            except ValueError:
                log.warning("changes: fecha inválida en %s: %r", path.name, r.get("date"))
                continue
            if day >= since and r.get("obs_key"):
                # The date is part of the key so legacy records without run_id stay distinct.
                rows.setdefault((r["date"], *_change_id(r)), r)
    ordered = sorted(rows.values(), key=lambda r: (r["obs_key"], r.get("kind", "")))
    ordered.sort(key=lambda r: r["date"], reverse=True)
    return [
        {
            "date": r["date"],
            "obs_key": r["obs_key"],
            "service_id": r.get("service_id"),
            "plan_id": r.get("plan_id"),
            "country": r.get("country"),
            "channel": r.get("channel"),
            "kind": r.get("kind"),
            "old": _change_value(r.get("old")),
            "new": _change_value(r.get("new")),
            "pct": r.get("pct"),
        }
        for r in ordered
    ]


def _coverage_json(rows: list[CoverageRow]) -> list[dict[str, Any]]:
    return [
        {
            "service_id": c.service_id,
            "country": c.country,
            "channel": c.channel,
            "status": c.status,
            "adapter_id": c.adapter_id,
            "failing_since": c.failing_since.isoformat() if c.failing_since else None,
        }
        for c in sorted(rows, key=lambda c: (c.service_id, c.country, c.channel))
    ]


def build_site_data(
    data_dir: Path,
    *,
    residence: str,
    profile: str,
    catalog: Catalog,
    feasibility: FeasibilityFile,
    bundles: BundlesFile,
    fx_fee_pct: float | None = None,
    now: datetime | None = None,
    profiles: ProfilesFile | None = None,
    instruments: PaymentInstrumentsFile | None = None,
    review: ReviewFile | None = None,
) -> dict[str, Any]:
    """The site data contract as Python objects ({file name without .json: content}).

    `profile` is the site's default profile: offers.json and cheapest.json use its verdicts
    (research findings still pending review excluded). Without profiles.yaml (profiles=None)
    one profile is synthesized from feasibility.yaml for (residence, profile)."""
    from haggler.catalog import synthesize_profiles
    from haggler.research.evaluate import EvalContext, verdict_fn
    from haggler.research.io import load_data_dir

    try:
        home_currency = catalog.country(residence).currency
    except KeyError:
        raise ValueError(f"país de residencia desconocido: {residence!r}") from None
    if profiles is None:
        if profile not in feasibility.profiles:
            raise ValueError(f"perfil desconocido: {profile!r} (ver data/curated/profiles.yaml)")
        profiles = synthesize_profiles(feasibility, residence=residence, profile=profile)
    default = profiles.get(profile)
    if default is None:
        raise ValueError(f"perfil desconocido: {profile!r} (ver data/curated/profiles.yaml)")
    if default.residence_country != residence:
        raise ValueError(
            f"el perfil {profile!r} vive en {default.residence_country}, no en {residence}"
        )
    fee = Settings().fx_fee_pct if fx_fee_pct is None else fx_fee_pct
    now = now or datetime.now(UTC)
    subjects, research_run, research_errors = load_data_dir(data_dir)
    for message in research_errors:
        log.warning("site-data: %s", message)
    ctx = EvalContext.build(
        catalog=catalog,
        feasibility=feasibility,
        instruments=instruments or PaymentInstrumentsFile(instruments=[]),
        subjects=subjects,
        review=review,
        today=now.date(),
    )
    verdicts = verdict_fn(default, ctx)

    latest = _load_latest(data_dir)
    known = {(s.id, p.id) for s in catalog.services for p in s.plans}
    ccs = set(catalog.country_codes())
    rows = sorted(
        (
            p
            for p in latest.prices
            if (p.service_id, p.plan_id) in known and p.country in ccs and p.status != "rejected"
        ),
        key=lambda p: p.obs_key,
    )
    if len(rows) != len(latest.prices):
        log.warning(
            "site-data: %d filas fuera del catálogo omitidas", len(latest.prices) - len(rows)
        )

    as_of = latest.run.get("as_of")
    try:
        as_of_day = date.fromisoformat(as_of) if as_of else None
    except ValueError:
        as_of_day = None
    anchor = as_of_day or now.date()
    display = list(dict.fromkeys([home_currency, "EUR", "USD"]))
    return {
        "meta": {
            "generated_at": now.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%SZ"),
            "run_id": latest.run.get("run_id") or None,
            "as_of": as_of or None,
            "residence": residence,
            "profile": profile,
            "research_run_id": research_run.get("run_id") or None,
            "research_as_of": research_run.get("as_of") or None,
            "fx_fee_pct": float(fee),
            "display_currencies": display,
            "attributions": _attributions(catalog),
        },
        "services": _services_json(catalog, bundles),
        "countries": _countries_json(catalog),
        "fx": _fx_json(latest.fx),
        "offers": [_offer_json(p, fx=latest.fx, verdicts=verdicts) for p in rows],
        "feasibility": _feasibility_json(profiles, default, ctx, rows),
        "cheapest": _cheapest_json(
            rows,
            fx=latest.fx,
            catalog=catalog,
            feas=feasibility,
            residence=residence,
            profile=profile,
            fx_fee_pct=fee,
            verdicts=verdicts,
        ),
        "bundles": _bundles_json(
            bundles,
            rows=rows,
            fx=latest.fx,
            residence_currency=home_currency,
            fx_fee_pct=fee,
            today=now.date(),
            as_of=as_of_day,
            profiles=_public_profiles(profiles, default),
            ctx=ctx,
        ),
        "changes": load_changes(
            Path(data_dir) / "changes", since=anchor - timedelta(days=CHANGE_WINDOW_DAYS)
        ),
        "coverage": _coverage_json(latest.coverage),
    }


def site_data(
    data_dir: Path,
    out_dir: Path,
    *,
    residence: str,
    profile: str,
    catalog: Catalog,
    feasibility: FeasibilityFile,
    bundles: BundlesFile,
    fx_fee_pct: float | None = None,
    now: datetime | None = None,
    profiles: ProfilesFile | None = None,
    instruments: PaymentInstrumentsFile | None = None,
    review: ReviewFile | None = None,
) -> dict[str, Path]:
    """Write the site data contract files into out_dir (web/src/data). Works on an empty data/."""
    content = build_site_data(
        data_dir,
        residence=residence,
        profile=profile,
        catalog=catalog,
        feasibility=feasibility,
        bundles=bundles,
        fx_fee_pct=fx_fee_pct,
        now=now,
        profiles=profiles,
        instruments=instruments,
        review=review,
    )
    out_dir = Path(out_dir)
    written: dict[str, Path] = {}
    for name in SITE_FILES:
        value = content[name]
        data = dumps_lines(value) if isinstance(value, list) else dumps_pretty(value)
        _write_atomic(out_dir / f"{name}.json", data)
        written[name] = out_dir / f"{name}.json"
    return written

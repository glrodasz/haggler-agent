"""Where research facts live, and how they are read and written (no ADK).

Store (GCS or local, written by the research job):
    research/runs/{date}/{run_id}/report.json      one per run
    research/latest/{services,pairs,instruments}.json   SubjectFacts lists (one per line)
    research/latest/state.json                      ResearchState
    research/latest/run.json                        written LAST; sha256 of every latest file
    research/usage/{YYYY-MM}.json                   calls and search queries spent in the month
                                                    (dry runs included): the monthly guard
    raw/research/{date}/{sha}.html.gz               fetched pages (30-day lifecycle, like
    raw/research/{date}/{sha}.pdf                   every key under raw/research/)

Repo (written by `haggler sync`, read by `haggler site-data`):
    data/research/{services,pairs,instruments}.json, data/research/run.json
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Iterable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import pydantic
from pydantic import TypeAdapter

from haggler.models.research import ResearchState, SubjectFacts
from haggler.research.kinds import KINDS, SubjectKind
from haggler.store import Store, dumps, dumps_lines

PREFIX = "research"
LATEST = f"{PREFIX}/latest"
# Kind -> its facts file, in registry order (research.kinds).
KIND_FILES: dict[SubjectKind, str] = {kind: spec.data_file for kind, spec in KINDS.items()}
LATEST_FILES = (*KIND_FILES.values(), "state.json")
DATA_FILES = tuple(KIND_FILES.values())
RUN_SCHEMA_VERSION = 1

_SUBJECTS = TypeAdapter(list[SubjectFacts])


def usage_key(month: str) -> str:
    return f"{PREFIX}/usage/{month}.json"


def read_usage(store: Store, month: str) -> dict[str, int]:
    """{"calls", "queries", "runs"} spent in `month` ("YYYY-MM"); zeros when unknown."""
    out = {"calls": 0, "queries": 0, "runs": 0}
    data = store.read_bytes(usage_key(month))
    if data is None:
        return out
    try:
        raw = json.loads(data)
    except ValueError:
        return out
    if isinstance(raw, dict):
        for k in out:
            v = raw.get(k)
            if isinstance(v, int) and v >= 0:
                out[k] = v
    return out


def add_usage(store: Store, month: str, *, calls: int, queries: int) -> dict[str, int]:
    """Add one run's spending to the month (written by every run that called the model, dry
    runs included: the queries are spent either way)."""
    usage = read_usage(store, month)
    usage["calls"] += calls
    usage["queries"] += queries
    usage["runs"] += 1
    store.write_bytes(usage_key(month), dumps({"month": month, **usage}))
    return usage


def sha256_hex(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def run_prefix(run_date: str, run_id: str) -> str:
    return f"{PREFIX}/runs/{run_date}/{run_id}"


def raw_key(run_date: str, url: str, ext: str = "html.gz") -> str:
    """Store key of a fetched page: "html.gz" for HTML (gzipped text), "pdf" for a PDF's bytes.
    Every key is under raw/research/, which the bucket's lifecycle deletes after 30 days."""
    return f"raw/research/{run_date}/{hashlib.sha256(url.encode()).hexdigest()[:16]}.{ext}"


@dataclass
class Latest:
    subjects: dict[str, SubjectFacts] = field(default_factory=dict)
    state: ResearchState = field(default_factory=ResearchState)
    run: dict[str, Any] = field(default_factory=dict)
    errors: list[str] = field(default_factory=list)


def _parse_subjects(data: bytes, name: str, errors: list[str]) -> list[SubjectFacts]:
    try:
        raw = json.loads(data)
    except ValueError as exc:
        errors.append(f"{name}: not JSON ({exc})")
        return []
    if not isinstance(raw, list):
        errors.append(f"{name}: not a JSON array")
        return []
    out: list[SubjectFacts] = []
    for row in raw:
        try:
            out.append(SubjectFacts.model_validate(row))
        except pydantic.ValidationError as exc:
            errors.append(f"{name}: skipped invalid subject: {str(exc)[:200]}")
    return out


def load_latest(store: Store) -> Latest:
    """research/latest/* of the store; missing files mean a first run."""
    latest = Latest()
    for name in KIND_FILES.values():
        data = store.read_bytes(f"{LATEST}/{name}")
        if data is None:
            continue
        for s in _parse_subjects(data, f"{LATEST}/{name}", latest.errors):
            latest.subjects[s.key] = s
    data = store.read_bytes(f"{LATEST}/state.json")
    if data is not None:
        try:
            latest.state = ResearchState.model_validate_json(data)
        except (ValueError, pydantic.ValidationError) as exc:
            latest.errors.append(f"{LATEST}/state.json: ignored ({str(exc)[:200]})")
    data = store.read_bytes(f"{LATEST}/run.json")
    if data is not None:
        try:
            run = json.loads(data)
            latest.run = run if isinstance(run, dict) else {}
        except ValueError:
            latest.errors.append(f"{LATEST}/run.json: ignored (not JSON)")
    return latest


def subject_files(subjects: Iterable[SubjectFacts]) -> dict[str, bytes]:
    """{file name: bytes} of every kind's subject file, sorted by key (stable git diffs)."""
    by_kind: dict[str, list[SubjectFacts]] = {k: [] for k in KIND_FILES}
    for s in subjects:
        by_kind[s.kind].append(s)
    return {
        KIND_FILES[kind]: dumps_lines(sorted(rows, key=lambda s: s.key))
        for kind, rows in by_kind.items()
    }


def write_latest(
    store: Store,
    *,
    subjects: Iterable[SubjectFacts],
    state: ResearchState,
    run_id: str,
    as_of: str,
    finished_at: str | None,
    report_summary: dict[str, Any],
) -> dict[str, str]:
    """research/latest/*, then run.json last; returns {file: sha256}."""
    files = subject_files(subjects)
    files["state.json"] = dumps(state)
    for name in LATEST_FILES:
        store.write_bytes(f"{LATEST}/{name}", files[name])
    hashes = {name: sha256_hex(files[name]) for name in LATEST_FILES}
    manifest = {
        "schema_version": RUN_SCHEMA_VERSION,
        "run_id": run_id,
        "as_of": as_of,
        "finished_at": finished_at,
        "files": hashes,
        "report": report_summary,
    }
    store.write_bytes(f"{LATEST}/run.json", dumps(manifest))
    return hashes


def load_data_dir(data_dir: Path) -> tuple[list[SubjectFacts], dict[str, Any], list[str]]:
    """data/research/* (from `haggler sync`): subjects, run.json and parse errors. A missing
    directory is not an error (no research yet)."""
    base = Path(data_dir) / PREFIX
    errors: list[str] = []
    subjects: list[SubjectFacts] = []
    for name in DATA_FILES:
        path = base / name
        if path.exists():
            subjects += _parse_subjects(path.read_bytes(), f"data/research/{name}", errors)
    run: dict[str, Any] = {}
    run_path = base / "run.json"
    if run_path.exists():
        try:
            loaded = json.loads(run_path.read_text(encoding="utf-8"))
            run = loaded if isinstance(loaded, dict) else {}
        except ValueError:
            errors.append("data/research/run.json: not JSON")
    return subjects, run, errors


def parse_subjects_bytes(data: bytes) -> list[SubjectFacts]:
    """Strict parse (sync): any invalid row raises."""
    return _SUBJECTS.validate_json(data)

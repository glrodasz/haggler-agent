"""Daily batch entry point (Cloud Run Job): `haggler daily [...]` or `python -m haggler.jobs.daily`.

Runs the ADK Workflow once with an in-memory session (the job's disk is ephemeral; everything
durable goes to the Store) and prints one JSON summary line on stdout.

Exit code 0 once the run persisted latest/* and latest/run.json, even when fewer than half of the
tasks succeeded (that is logged as a WARNING and flagged in the summary): the Cloud Run Job runs
with --max-retries 1, and re-running a persisted crawl would only fetch every vendor page again
and count as "the next run" for quarantine releases and carry-forward counters. Exit code 1 only
when the run crashed or produced no report (a --dry-run, which persists nothing by design, exits
0 with a report).
"""

from __future__ import annotations

import argparse
import asyncio
import dataclasses
import json
import logging
import sys
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path
from typing import Any

from haggler.config import Settings
from haggler.fetch.replay import ReplayFetcher
from haggler.fx.service import StaticFxService
from haggler.jobs._runner import quiet_handled_node_errors, run_workflow_once
from haggler.models.fx import FxTable
from haggler.models.run import RunReport
from haggler.pipeline.deps import Deps, build_default_deps

log = logging.getLogger(__name__)

USER_ID = "daily-job"
# Below this share of ok/partial/not_available tasks the run is reported as degraded.
MIN_SUCCESS_RATIO = 0.5


@dataclass
class DailyOptions:
    date: date | None = None
    services: list[str] = field(default_factory=list)
    countries: list[str] = field(default_factory=list)
    adapters: list[str] = field(default_factory=list)
    dry_run: bool = False
    no_llm: bool = False
    # local | gcs | memory; None = HAGGLER_STORE.
    store: str | None = None
    # Directory with index.json (see haggler.fetch.replay); also restricts the run to its URLs.
    replay: Path | None = None

    def request(self) -> dict[str, Any]:
        """The START message of the Workflow (see haggler.pipeline.nodes.parse_filters)."""
        return {
            "date": self.date.isoformat() if self.date else None,
            "services": self.services,
            "countries": self.countries,
            "adapters": self.adapters,
            "dry_run": self.dry_run,
        }


def _prepare_deps(options: DailyOptions, deps: Deps | None) -> tuple[Deps, list[Any]]:
    """Apply the options to deps; returns the deps plus the resources this call must close."""
    owned: list[Any] = []
    if deps is None:
        settings = Settings()
        if options.store:
            settings = dataclasses.replace(settings, store=options.store)
        deps = build_default_deps(settings)
        owned.append(deps)
    else:
        deps = dataclasses.replace(deps)
    if options.no_llm:
        deps.model = None
    if options.replay is not None:
        replay = ReplayFetcher(options.replay)
        deps.fetcher = replay
        deps.task_filter = replay.covers
        fx_file = Path(options.replay) / "fx.json"
        if fx_file.exists():
            table = FxTable.model_validate_json(fx_file.read_text(encoding="utf-8"))
            deps.fx = StaticFxService(table)
    return deps, owned


async def run_daily(options: DailyOptions, deps: Deps | None = None) -> RunReport | None:
    """Run the Workflow once; None when it produced no final output."""
    from haggler.pipeline.app import build_app  # keeps `import haggler.jobs.daily` light

    deps, owned = _prepare_deps(options, deps)
    run_date = options.date or deps.today()
    app = build_app(deps)
    try:
        final = await run_workflow_once(
            app,
            options.request(),
            user_id=USER_ID,
            session_id=f"daily-{run_date.isoformat()}",
            max_llm_calls=deps.settings.max_llm_calls,
        )
    finally:
        for res in owned:
            await res.aclose()
    if not isinstance(final, dict):
        return None
    return RunReport.model_validate(final)


def persisted(report: RunReport) -> bool:
    """True once persist wrote latest/run.json (its last write; outputs lists what it wrote)."""
    return not report.dry_run and "latest/run.json" in report.outputs


def degraded(report: RunReport) -> bool:
    return report.success_ratio < MIN_SUCCESS_RATIO


def summary(report: RunReport) -> dict[str, Any]:
    return {
        "ok": True,
        "run_id": report.run_id,
        "run_date": report.run_date.isoformat(),
        "dry_run": report.dry_run,
        "persisted": persisted(report),
        "tasks_total": report.tasks_total,
        "success_ratio": round(report.success_ratio, 4),
        "low_success_ratio": degraded(report),
        "min_success_ratio": MIN_SUCCESS_RATIO,
        "tasks_by_status": report.tasks_by_status,
        "obs_by_status": report.obs_by_status,
        "llm": report.llm.model_dump(mode="json"),
        "anomalies": len(report.anomalies),
        "errors": len(report.errors),
        "outputs": len(report.outputs),
    }


def _csv(value: str) -> list[str]:
    return [v.strip() for v in value.split(",") if v.strip()]


def add_daily_arguments(parser: argparse.ArgumentParser) -> argparse.ArgumentParser:
    """Flags of `haggler daily` (shared with haggler.cli)."""
    parser.add_argument("--store", choices=["local", "gcs", "memory"], default=None)
    parser.add_argument("--services", type=_csv, default=[], help="comma-separated service ids")
    parser.add_argument("--countries", type=_csv, default=[], help="comma-separated ISO codes")
    parser.add_argument("--adapters", type=_csv, default=[], help="comma-separated adapter ids")
    parser.add_argument("--date", type=date.fromisoformat, default=None, help="YYYY-MM-DD")
    parser.add_argument("--dry-run", action="store_true", help="run everything, write nothing")
    parser.add_argument("--no-llm", action="store_true", help="never call the model")
    parser.add_argument("--replay", type=Path, default=None, help="directory with index.json")
    return parser


def options_from_args(ns: argparse.Namespace) -> DailyOptions:
    return DailyOptions(
        date=ns.date,
        services=list(ns.services),
        countries=[c.upper() for c in ns.countries],
        adapters=list(ns.adapters),
        dry_run=ns.dry_run,
        no_llm=ns.no_llm,
        store=ns.store,
        replay=ns.replay,
    )


def exit_code(report: RunReport | None, *, dry_run: bool) -> int:
    """0 when the run did what it had to (persisted, or a dry run that reported); 1 otherwise,
    so Cloud Run's retry only repeats runs that persisted nothing. See the module docstring."""
    if report is None:
        return 1
    if dry_run:
        return 0
    return 0 if persisted(report) else 1


def main(argv: list[str] | None = None) -> int:
    parser = add_daily_arguments(argparse.ArgumentParser(prog="haggler daily"))
    options = options_from_args(parser.parse_args(argv))
    if not logging.getLogger().handlers:
        logging.basicConfig(
            level=logging.INFO, stream=sys.stderr, format="%(levelname)s %(name)s: %(message)s"
        )
    quiet_handled_node_errors()
    try:
        report = asyncio.run(run_daily(options))
    except Exception as exc:
        log.exception("daily run failed")
        print(json.dumps({"ok": False, "error": f"{type(exc).__name__}: {exc}"}), flush=True)
        return 1
    if report is None:
        print(json.dumps({"ok": False, "error": "the workflow produced no report"}), flush=True)
        return 1
    if degraded(report):
        log.warning(
            "run %s: low success ratio %.2f (< %.2f) — %s",
            report.run_id,
            report.success_ratio,
            MIN_SUCCESS_RATIO,
            json.dumps(report.tasks_by_status, sort_keys=True),
        )
    print(json.dumps(summary(report), ensure_ascii=False), flush=True)
    return exit_code(report, dry_run=options.dry_run)


if __name__ == "__main__":
    sys.exit(main())

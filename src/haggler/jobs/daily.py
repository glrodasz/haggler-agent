"""Daily batch entry point (Cloud Run Job): `haggler daily [...]` or `python -m haggler.jobs.daily`.

Runs the ADK Workflow once with an in-memory session (the job's disk is ephemeral; everything
durable goes to the Store) and prints one JSON summary line on stdout. Exit code 1 when the run
produced no report or fewer than half of the tasks succeeded (not for --dry-run).
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

from google.adk import Runner
from google.adk.agents.run_config import RunConfig
from google.adk.sessions import InMemorySessionService
from google.genai import types

from haggler.config import Settings
from haggler.fetch.replay import ReplayFetcher
from haggler.fx.service import StaticFxService
from haggler.models.fx import FxTable
from haggler.models.run import RunReport
from haggler.pipeline.deps import Deps, build_default_deps

log = logging.getLogger(__name__)

USER_ID = "daily-job"
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
    message = types.UserContent(parts=[types.Part(text=json.dumps(options.request()))])
    final: dict[str, Any] | None = None
    target = f"{app.root_agent.name}@1"
    try:
        runner = Runner(app=app, session_service=InMemorySessionService(), auto_create_session=True)
        async with runner:
            async for ev in runner.run_async(
                user_id=USER_ID,
                session_id=f"daily-{run_date.isoformat()}",
                new_message=message,
                run_config=RunConfig(max_llm_calls=deps.settings.max_llm_calls),
            ):
                if ev.error_code:
                    path = ev.node_info.path if ev.node_info else "?"
                    log.debug(
                        "node event error %s at %s: %s", ev.error_code, path, ev.error_message
                    )
                node_info = ev.node_info
                if ev.output is not None and node_info and target in (node_info.output_for or []):
                    final = ev.output
    finally:
        for res in owned:
            await res.aclose()
    if not isinstance(final, dict):
        return None
    return RunReport.model_validate(final)


def summary(report: RunReport) -> dict[str, Any]:
    return {
        "ok": True,
        "run_id": report.run_id,
        "run_date": report.run_date.isoformat(),
        "dry_run": report.dry_run,
        "tasks_total": report.tasks_total,
        "success_ratio": round(report.success_ratio, 4),
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
    if report is None:
        return 1
    if dry_run:
        return 0
    return 0 if report.success_ratio >= MIN_SUCCESS_RATIO else 1


def main(argv: list[str] | None = None) -> int:
    parser = add_daily_arguments(argparse.ArgumentParser(prog="haggler daily"))
    options = options_from_args(parser.parse_args(argv))
    if not logging.getLogger().handlers:
        logging.basicConfig(
            level=logging.INFO, stream=sys.stderr, format="%(levelname)s %(name)s: %(message)s"
        )
    try:
        report = asyncio.run(run_daily(options))
    except Exception as exc:
        log.exception("daily run failed")
        print(json.dumps({"ok": False, "error": f"{type(exc).__name__}: {exc}"}), flush=True)
        return 1
    if report is None:
        print(json.dumps({"ok": False, "error": "the workflow produced no report"}), flush=True)
        return 1
    print(json.dumps(summary(report), ensure_ascii=False), flush=True)
    return exit_code(report, dry_run=options.dry_run)


if __name__ == "__main__":
    sys.exit(main())

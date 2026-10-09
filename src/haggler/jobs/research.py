"""Weekly research entry point (Cloud Run Job): `haggler research run [...]`.

Runs the research Workflow once (in-memory session; everything durable goes to the Store) and
prints one JSON summary line on stdout. Exit code 0 once research/latest/run.json was written
(or a --dry-run reported), 1 when the run crashed or produced no report. The Cloud Run Job runs
with --max-retries 0: a retry would spend the search budget again.
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
from typing import Any

from haggler.config import Settings
from haggler.jobs._runner import quiet_handled_node_errors, run_workflow_once
from haggler.models.research import ResearchReport
from haggler.research.deps import ResearchDeps, build_default_research_deps

log = logging.getLogger(__name__)

USER_ID = "research-job"


@dataclass
class ResearchOptions:
    date: date | None = None
    services: list[str] = field(default_factory=list)
    # "netflix:CO"
    pairs: list[str] = field(default_factory=list)
    instruments: list[str] = field(default_factory=list)
    max_jobs: int | None = None
    dry_run: bool = False
    no_llm: bool = False
    # local | gcs | memory; None = HAGGLER_STORE.
    store: str | None = None

    def request(self) -> dict[str, Any]:
        """The START message of the Workflow (see haggler.research.nodes.parse_request)."""
        return {
            "date": self.date.isoformat() if self.date else None,
            "services": self.services,
            "pairs": self.pairs,
            "instruments": self.instruments,
            "max_jobs": self.max_jobs,
            "dry_run": self.dry_run,
        }


def _prepare_deps(
    options: ResearchOptions, deps: ResearchDeps | None
) -> tuple[ResearchDeps, list[Any]]:
    owned: list[Any] = []
    if deps is None:
        settings = Settings()
        if options.store:
            settings = dataclasses.replace(settings, store=options.store)
        deps = build_default_research_deps(settings)
        owned.append(deps)
    else:
        deps = dataclasses.replace(deps)
    if options.no_llm:
        deps.model = None
    return deps, owned


async def run_research(
    options: ResearchOptions, deps: ResearchDeps | None = None
) -> ResearchReport | None:
    from haggler.research.app import build_research_app

    deps, owned = _prepare_deps(options, deps)
    run_date = options.date or deps.today()
    app = build_research_app(deps)
    try:
        final = await run_workflow_once(
            app,
            options.request(),
            user_id=USER_ID,
            session_id=f"research-{run_date.isoformat()}",
            max_llm_calls=deps.settings.research_max_calls,
        )
    finally:
        for res in owned:
            await res.aclose()
    if not isinstance(final, dict):
        return None
    return ResearchReport.model_validate(final)


def persisted(report: ResearchReport) -> bool:
    return not report.dry_run and "research/latest/run.json" in report.outputs


def summary(report: ResearchReport) -> dict[str, Any]:
    return {
        "ok": True,
        "run_id": report.run_id,
        "run_date": report.run_date.isoformat(),
        "dry_run": report.dry_run,
        "persisted": persisted(report),
        "planned": report.planned,
        "by_status": report.by_status,
        "claims_verified": report.claims_verified,
        "claims_unverified": report.claims_unverified,
        "checks": report.checks,
        "budget": report.budget.model_dump(mode="json"),
        "errors": len(report.errors),
    }


def _csv(value: str) -> list[str]:
    return [v.strip() for v in value.split(",") if v.strip()]


def add_run_arguments(parser: argparse.ArgumentParser) -> argparse.ArgumentParser:
    parser.add_argument("--store", choices=["local", "gcs", "memory"], default=None)
    parser.add_argument("--services", type=_csv, default=[], help="service ids, comma-separated")
    parser.add_argument("--pairs", type=_csv, default=[], help="service:CC pairs, comma-separated")
    parser.add_argument("--instruments", type=_csv, default=[], help="instrument ids")
    parser.add_argument("--max-jobs", type=int, default=None, help="cap on researched subjects")
    parser.add_argument("--date", type=date.fromisoformat, default=None, help="YYYY-MM-DD")
    parser.add_argument("--dry-run", action="store_true", help="plan and research, write nothing")
    parser.add_argument("--no-llm", action="store_true", help="never call the model (plan only)")
    return parser


def options_from_args(ns: argparse.Namespace) -> ResearchOptions:
    return ResearchOptions(
        date=ns.date,
        services=list(ns.services),
        pairs=list(ns.pairs),
        instruments=list(ns.instruments),
        max_jobs=ns.max_jobs,
        dry_run=ns.dry_run,
        no_llm=ns.no_llm,
        store=ns.store,
    )


def main(argv: list[str] | None = None) -> int:
    parser = add_run_arguments(argparse.ArgumentParser(prog="haggler research run"))
    options = options_from_args(parser.parse_args(argv))
    if not logging.getLogger().handlers:
        logging.basicConfig(
            level=logging.INFO, stream=sys.stderr, format="%(levelname)s %(name)s: %(message)s"
        )
    quiet_handled_node_errors()
    try:
        report = asyncio.run(run_research(options))
    except Exception as exc:
        log.exception("research run failed")
        print(json.dumps({"ok": False, "error": f"{type(exc).__name__}: {exc}"}), flush=True)
        return 1
    if report is None:
        print(json.dumps({"ok": False, "error": "the workflow produced no report"}), flush=True)
        return 1
    print(json.dumps(summary(report), ensure_ascii=False), flush=True)
    if options.dry_run:
        return 0
    return 0 if persisted(report) else 1


if __name__ == "__main__":
    sys.exit(main())

"""When the Cloud Run jobs run: schedule.yaml at the repository root (the only place to edit).

Cloud Scheduler fires each job at its trigger slot; with HAGGLER_SCHEDULE_GATE=1 (set by
deploy-job.yml on both jobs) the job first reads the as_of date of its own last completed run and
skips the slot when that run is fewer than `every_days` days old. Manual runs from
.github/workflows/daily.yml and research.yml pass HAGGLER_SCHEDULE_GATE=0 and always run.

infra/bootstrap.sh reads the trigger slots and time zone from the same file, and publish.yml's
cron is checked against `publish.trigger_utc` by tests/infra/test_schedule.py.
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from typing import Any, Literal

import yaml

from haggler.config import repo_root
from haggler.store import Store

JobName = Literal["prices", "research"]
SCHEDULE_FILE = "schedule.yaml"
GATE_ENV = "HAGGLER_SCHEDULE_GATE"
# Where each job writes its run manifest (as_of = the run's date), last.
LAST_RUN_KEYS: dict[str, str] = {
    "prices": "latest/run.json",
    "research": "research/latest/run.json",
}


class ScheduleError(ValueError):
    pass


@dataclass(frozen=True)
class JobSchedule:
    every_days: int
    trigger: str


@dataclass(frozen=True)
class Schedule:
    timezone: str
    prices: JobSchedule
    research: JobSchedule
    publish_trigger_utc: str

    def job(self, name: JobName) -> JobSchedule:
        return self.prices if name == "prices" else self.research

    @property
    def stale_run_days(self) -> int:
        """publish warns when the prices run it syncs is older than this (one missed run)."""
        return self.prices.every_days + 1

    @property
    def bundle_verify_days(self) -> int:
        """How long a bundle value found by the watcher (inside the research job) counts as
        verified: one research interval plus a week, so one failed run does not unverify it."""
        return self.research.every_days + 8

    def site_json(self) -> dict[str, int]:
        """meta.schedule in the site data (the cadence the site copy states)."""
        return {
            "prices_every_days": self.prices.every_days,
            "research_every_days": self.research.every_days,
            "bundle_verify_days": self.bundle_verify_days,
        }


def _cron(value: Any, where: str) -> str:
    if not isinstance(value, str) or len(value.split()) != 5:
        raise ScheduleError(f"{where}: expected a 5-field cron string, got {value!r}")
    return value


def _job(raw: Any, name: str) -> JobSchedule:
    if not isinstance(raw, dict):
        raise ScheduleError(f"{SCHEDULE_FILE}: '{name}' must be a mapping")
    every = raw.get("every_days")
    if not isinstance(every, int) or isinstance(every, bool) or every < 1:
        raise ScheduleError(f"{SCHEDULE_FILE}: {name}.every_days must be a whole number >= 1")
    return JobSchedule(every_days=every, trigger=_cron(raw.get("trigger"), f"{name}.trigger"))


def parse_schedule(raw: Any) -> Schedule:
    if not isinstance(raw, dict):
        raise ScheduleError(f"{SCHEDULE_FILE} must be a mapping")
    tz = raw.get("timezone")
    if not isinstance(tz, str) or not tz:
        raise ScheduleError(f"{SCHEDULE_FILE}: 'timezone' is required")
    publish = raw.get("publish")
    if not isinstance(publish, dict):
        raise ScheduleError(f"{SCHEDULE_FILE}: 'publish' must be a mapping")
    return Schedule(
        timezone=tz,
        prices=_job(raw.get("prices"), "prices"),
        research=_job(raw.get("research"), "research"),
        publish_trigger_utc=_cron(publish.get("trigger_utc"), "publish.trigger_utc"),
    )


def load_schedule(root: Path | None = None) -> Schedule:
    path = (root or repo_root()) / SCHEDULE_FILE
    return parse_schedule(yaml.safe_load(path.read_text(encoding="utf-8")))


def gate_enabled() -> bool:
    return os.getenv(GATE_ENV, "0").strip().lower() in {"1", "true", "yes", "on"}


def last_run_date(store: Store, job: JobName) -> date | None:
    """as_of of the job's last completed run (None: no run yet, or an unreadable manifest)."""
    data = store.read_bytes(LAST_RUN_KEYS[job])
    if data is None:
        return None
    try:
        run = json.loads(data)
        return date.fromisoformat(str(run["as_of"]))
    except (ValueError, KeyError, TypeError):
        return None


@dataclass(frozen=True)
class Due:
    due: bool
    last_run: date | None
    next_run: date

    def skipped_summary(self, job: JobName, every_days: int) -> dict[str, Any]:
        return {
            "ok": True,
            "skipped": True,
            "job": job,
            "reason": f"last run {self.last_run} is less than {every_days} days old",
            "last_run": self.last_run.isoformat() if self.last_run else None,
            "next_run": self.next_run.isoformat(),
        }


def is_due(last_run: date | None, today: date, every_days: int) -> Due:
    if last_run is None or last_run > today:
        return Due(True, last_run, today)
    next_run = last_run + timedelta(days=every_days)
    return Due(today >= next_run, last_run, max(next_run, today))


def check(
    store: Store, job: JobName, *, today: date | None = None, schedule: Schedule | None = None
) -> Due:
    every = (schedule or load_schedule()).job(job).every_days
    return is_due(last_run_date(store, job), today or datetime.now(UTC).date(), every)


def skip_summary(job: JobName, *, store_name: str | None = None) -> dict[str, Any] | None:
    """The JSON line a scheduled run prints when it skips its slot; None = go ahead.

    Only active with HAGGLER_SCHEDULE_GATE=1 (the Cloud Run jobs). Store errors never block a
    run: an unreadable store means "run", and the job reports its own errors.
    """
    if not gate_enabled():
        return None
    import dataclasses

    from haggler.config import Settings
    from haggler.store import store_from_settings

    settings = Settings()
    if store_name:
        settings = dataclasses.replace(settings, store=store_name)
    schedule = load_schedule()
    try:
        due = check(store_from_settings(settings), job, schedule=schedule)
    except Exception:
        return None
    if due.due:
        return None
    return due.skipped_summary(job, schedule.job(job).every_days)

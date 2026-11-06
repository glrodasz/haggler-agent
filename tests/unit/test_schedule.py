from __future__ import annotations

import json
from datetime import date

import pytest

from haggler import schedule as sched
from haggler.jobs import daily, research
from haggler.store import MemoryStore, write_json


def test_repo_schedule_is_valid() -> None:
    s = sched.load_schedule()
    # A weekly research slot (Mondays) can only honour whole weeks.
    if s.research.trigger.split()[4] != "*":
        assert s.research.every_days % 7 == 0
    assert s.stale_run_days == s.prices.every_days + 1
    assert s.bundle_verify_days == s.research.every_days + 8
    assert s.site_json() == {
        "prices_every_days": s.prices.every_days,
        "research_every_days": s.research.every_days,
        "bundle_verify_days": s.bundle_verify_days,
    }


@pytest.mark.parametrize(
    ("last", "today", "every", "due", "next_run"),
    [
        (None, date(2026, 11, 9), 2, True, date(2026, 11, 9)),
        (date(2026, 11, 9), date(2026, 11, 9), 2, False, date(2026, 11, 11)),
        (date(2026, 11, 9), date(2026, 11, 10), 2, False, date(2026, 11, 11)),
        (date(2026, 11, 9), date(2026, 11, 11), 2, True, date(2026, 11, 11)),
        # A missed slot (failed run): the next one goes ahead.
        (date(2026, 11, 9), date(2026, 11, 12), 2, True, date(2026, 11, 12)),
        # Weekly slot, every 14 days: every other Monday.
        (date(2026, 11, 9), date(2026, 11, 16), 14, False, date(2026, 11, 23)),
        (date(2026, 11, 9), date(2026, 11, 23), 14, True, date(2026, 11, 23)),
        (date(2026, 11, 9), date(2026, 11, 10), 1, True, date(2026, 11, 10)),
        # A manifest dated in the future never blocks a run.
        (date(2026, 11, 20), date(2026, 11, 9), 14, True, date(2026, 11, 9)),
    ],
)
def test_is_due(last, today, every, due, next_run) -> None:
    result = sched.is_due(last, today, every)
    assert (result.due, result.next_run) == (due, next_run)


def test_last_run_date_reads_each_jobs_manifest() -> None:
    store = MemoryStore()
    assert sched.last_run_date(store, "prices") is None
    write_json(store, "latest/run.json", {"as_of": "2026-11-05"})
    write_json(store, "research/latest/run.json", {"as_of": "2026-11-02"})
    assert sched.last_run_date(store, "prices") == date(2026, 11, 5)
    assert sched.last_run_date(store, "research") == date(2026, 11, 2)
    store.write_bytes("latest/run.json", b"not json")
    assert sched.last_run_date(store, "prices") is None


@pytest.mark.parametrize(
    "raw",
    [
        None,
        {"prices": {}, "research": {}, "publish": {}},
        {
            "timezone": "Europe/Stockholm",
            "prices": {"every_days": 0, "trigger": "17 4 * * *"},
            "research": {"every_days": 14, "trigger": "23 5 * * 1"},
            "publish": {"trigger_utc": "15 5 * * *"},
        },
        {
            "timezone": "Europe/Stockholm",
            "prices": {"every_days": 2, "trigger": "17 4 * *"},
            "research": {"every_days": 14, "trigger": "23 5 * * 1"},
            "publish": {"trigger_utc": "15 5 * * *"},
        },
    ],
)
def test_invalid_schedules_are_rejected(raw) -> None:
    with pytest.raises(sched.ScheduleError):
        sched.parse_schedule(raw)


def _gate(monkeypatch, store: MemoryStore, *, enabled: bool) -> None:
    monkeypatch.setenv(sched.GATE_ENV, "1" if enabled else "0")
    monkeypatch.setattr("haggler.store.store_from_settings", lambda settings: store)


@pytest.mark.parametrize(
    ("job", "key", "module"),
    [
        ("prices", "latest/run.json", daily),
        ("research", "research/latest/run.json", research),
    ],
)
def test_scheduled_run_skips_when_not_due(monkeypatch, capsys, job, key, module) -> None:
    store = MemoryStore()
    write_json(store, key, {"as_of": date.today().isoformat()})
    _gate(monkeypatch, store, enabled=True)

    def boom(*args, **kwargs):
        raise AssertionError("the workflow must not run")

    monkeypatch.setattr(module, "run_daily" if job == "prices" else "run_research", boom)
    assert module.main(["--store", "memory"]) == 0
    line = json.loads(capsys.readouterr().out.strip())
    assert line["skipped"] is True and line["job"] == job
    assert line["last_run"] == date.today().isoformat()


def test_gate_off_or_due_lets_the_run_through(monkeypatch) -> None:
    store = MemoryStore()
    write_json(store, "latest/run.json", {"as_of": date.today().isoformat()})
    _gate(monkeypatch, store, enabled=False)
    assert sched.skip_summary("prices") is None
    _gate(monkeypatch, store, enabled=True)
    assert sched.skip_summary("prices") is not None
    write_json(store, "latest/run.json", {"as_of": "2000-01-01"})
    assert sched.skip_summary("prices") is None


def test_unreadable_store_never_blocks_a_run(monkeypatch) -> None:
    monkeypatch.setenv(sched.GATE_ENV, "1")

    def broken(settings):
        raise RuntimeError("no bucket")

    monkeypatch.setattr("haggler.store.store_from_settings", broken)
    assert sched.skip_summary("research") is None

"""schedule.yaml is the one place the cadence lives: everything else must agree with it."""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest
import yaml

from haggler.schedule import load_schedule

ROOT = Path(__file__).resolve().parents[2]
WORKFLOWS = ROOT / ".github" / "workflows"
SCHEDULE = load_schedule(ROOT)


def _workflow(name: str) -> dict:
    return yaml.safe_load((WORKFLOWS / name).read_text(encoding="utf-8"))


def test_publish_cron_matches_schedule_yaml() -> None:
    on = _workflow("publish.yml")[True]  # PyYAML reads the "on" key as True
    assert [c["cron"] for c in on["schedule"]] == [SCHEDULE.publish_trigger_utc]


@pytest.mark.skipif(shutil.which("bash") is None, reason="bash not installed")
@pytest.mark.parametrize(
    ("section", "key", "expected"),
    [
        ("prices", "trigger", SCHEDULE.prices.trigger),
        ("research", "trigger", SCHEDULE.research.trigger),
        ("", "timezone", SCHEDULE.timezone),
    ],
)
def test_bootstrap_reads_the_trigger_slots_from_schedule_yaml(section, key, expected) -> None:
    text = (ROOT / "infra" / "bootstrap.sh").read_text(encoding="utf-8")
    start = text.index("schedule_get() {")
    func = text[start : text.index("\n}\n", start) + 3]
    out = subprocess.run(
        ["bash", "-c", f'{func}\nschedule_get "$1" "$2"', "-", section, key],
        env={"SCHEDULE_FILE": str(ROOT / "schedule.yaml"), "PATH": "/usr/bin:/bin"},
        capture_output=True,
        text=True,
        check=True,
    )
    assert out.stdout.strip() == expected
    assert 'SCHEDULE="${SCHEDULE:-$(schedule_get prices trigger)}"' in text
    assert 'RESEARCH_SCHEDULE="${RESEARCH_SCHEDULE:-$(schedule_get research trigger)}"' in text


def test_scheduled_jobs_are_gated_and_manual_runs_are_not() -> None:
    deploy = (WORKFLOWS / "deploy-job.yml").read_text(encoding="utf-8")
    assert deploy.count('env_vars="${env_vars},HAGGLER_SCHEDULE_GATE=1"') == 2
    assert '"schedule.yaml"' in deploy  # a cadence change redeploys the jobs
    assert "HAGGLER_SCHEDULE_GATE=0" in (WORKFLOWS / "daily.yml").read_text(encoding="utf-8")
    assert "HAGGLER_SCHEDULE_GATE=0" in (WORKFLOWS / "research.yml").read_text(encoding="utf-8")
    assert "schedule.yaml" in (ROOT / "Dockerfile").read_text(encoding="utf-8")

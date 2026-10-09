"""Invariants of the GitHub workflows and infra/bootstrap.sh (docs/SETUP.md relies on them)."""

from __future__ import annotations

import os
import re
import shutil
import subprocess
from pathlib import Path

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[2]
WORKFLOWS = ROOT / ".github" / "workflows"
OWN_WORKFLOWS = ("ci.yml", "probe.yml", "deploy-job.yml", "publish.yml")

# Majors checked to declare `runs.using: node24` (node20 is deprecated on the runners). Upload and
# download artifact, and upload-pages-artifact/deploy-pages, are bumped together.
NODE24_MAJORS = {
    "actions/checkout": 7,
    "actions/setup-node": 7,
    "actions/upload-artifact": 7,
    "actions/download-artifact": 8,
    "actions/upload-pages-artifact": 5,
    "actions/deploy-pages": 5,
    "actions/configure-pages": 6,
    "astral-sh/setup-uv": 7,
    "google-github-actions/auth": 3,
    "google-github-actions/setup-gcloud": 3,
}


def load(name: str) -> dict:
    return yaml.safe_load((WORKFLOWS / name).read_text(encoding="utf-8"))


def steps(workflow: dict, job: str) -> list[dict]:
    return workflow["jobs"][job]["steps"]


def step(workflow: dict, job: str, name: str) -> dict:
    return next(s for s in steps(workflow, job) if s.get("name") == name or s.get("id") == name)


@pytest.mark.parametrize("name", OWN_WORKFLOWS)
def test_actions_run_on_node24(name: str) -> None:
    uses = re.findall(r"uses:\s*([\w.-]+/[\w.-]+)@v(\d+)", (WORKFLOWS / name).read_text())
    assert uses
    for action, major in uses:
        assert action in NODE24_MAJORS, f"{name}: unknown action {action}"
        assert int(major) >= NODE24_MAJORS[action], f"{name}: {action}@v{major} runs on node20"


def test_ci_builds_the_site_from_pipeline_output_and_from_empty_data() -> None:
    contract = load("ci.yml")["jobs"]["contract"]
    script = "\n".join(s.get("run", "") for s in contract["steps"])
    assert "haggler daily --replay tests/workflow/fixtures/replay --store local --no-llm" in (
        " ".join(script.split())
    )
    assert re.search(r"haggler sync\s+--from-dir", script)
    site_data = re.findall(r"haggler site-data\s+--data-dir \"\$RUNNER_TEMP/(\w+)\"", script)
    assert site_data == ["data", "empty"]
    assert script.count("npm run build") == 2
    assert script.count("npm run check") == 2


def test_probe_uploads_only_the_summary() -> None:
    upload = step(load("probe.yml"), "probe", "Upload probe summary")
    paths = upload["with"]["path"].split()
    assert paths == ["probe-out/summary.md", "probe-out/summary.json"]


def test_publish_builds_even_when_sync_fails() -> None:
    publish = load("publish.yml")
    sync = step(publish, "sync", "sync")
    assert sync["continue-on-error"] is True
    assert "::warning" in sync["run"]
    # synced follows the step outcome (not its conclusion), so build falls back to data/.
    assert publish["jobs"]["sync"]["outputs"]["synced"] == "${{ steps.sync.outcome == 'success' }}"
    assert "if" not in publish["jobs"]["build"]


def test_publish_deploys_the_synced_data_even_if_the_push_fails() -> None:
    publish = load("publish.yml")
    names = [s.get("name") for s in steps(publish, "sync")]
    # The artifact exists before the push runs, whatever the push does.
    assert names.index("Upload synced data") < names.index("Commit and push data/")
    upload = step(publish, "sync", "Upload synced data")
    assert upload["if"] == "steps.sync.outcome == 'success'"
    push = step(publish, "sync", "Commit and push data/")
    assert push["continue-on-error"] is True
    # build downloads the artifact when the sync worked, independently of the push.
    assert publish["jobs"]["sync"]["outputs"] == {
        "synced": "${{ steps.sync.outcome == 'success' }}"
    }
    assert "if" not in publish["jobs"]["build"]
    download = step(publish, "build", "Download synced data")
    assert download["if"] == "needs.sync.outputs.synced == 'true'"


def _git(cwd: Path, *args: str, env: dict[str, str]) -> None:
    subprocess.run(["git", *args], cwd=cwd, env=env, check=True, capture_output=True)


@pytest.mark.skipif(
    shutil.which("bash") is None or shutil.which("git") is None, reason="bash/git not installed"
)
def test_publish_push_rejected_by_branch_protection_is_a_warning(tmp_path: Path) -> None:
    """Runs the real step script against a remote that rejects like a protected main."""
    script = step(load("publish.yml"), "sync", "Commit and push data/")["run"]
    env = {
        "PATH": os.environ["PATH"],
        "HOME": str(tmp_path),
        "GIT_CONFIG_GLOBAL": os.devnull,
        "GIT_CONFIG_NOSYSTEM": "1",
        "GIT_AUTHOR_NAME": "t",
        "GIT_AUTHOR_EMAIL": "t@example.com",
        "GIT_COMMITTER_NAME": "t",
        "GIT_COMMITTER_EMAIL": "t@example.com",
    }
    remote = tmp_path / "remote.git"
    work = tmp_path / "work"
    _git(tmp_path, "init", "-q", "--bare", "-b", "main", str(remote), env=env)
    _git(tmp_path, "init", "-q", "-b", "main", str(work), env=env)
    (work / "README").write_text("x\n")
    _git(work, "add", "README", env=env)
    _git(work, "commit", "-q", "-m", "init", env=env)
    _git(work, "remote", "add", "origin", str(remote), env=env)
    _git(work, "push", "-q", "origin", "main", env=env)
    hook = remote / "hooks" / "pre-receive"
    hook.write_text("#!/bin/sh\necho 'GH006: Protected branch update failed' >&2\nexit 1\n")
    hook.chmod(0o755)
    (work / "data" / "latest").mkdir(parents=True)
    (work / "data" / "latest" / "run.json").write_text('{"as_of": "2026-10-08"}\n')

    summary = tmp_path / "summary.md"
    result = subprocess.run(
        # GitHub's default shell for run steps.
        ["bash", "--noprofile", "--norc", "-eo", "pipefail", "-c", script],
        cwd=work,
        env={
            **env,
            "IS_DEFAULT_BRANCH": "true",
            "GITHUB_REF_NAME": "main",
            "GITHUB_STEP_SUMMARY": str(summary),
        },
        capture_output=True,
        text=True,
        timeout=120,
    )
    # The step fails (continue-on-error keeps the job green) with a warning, not an error.
    assert result.returncode != 0
    assert "::warning title=push de data/ falló::" in result.stdout
    assert "::error" not in result.stdout
    assert "docs/SETUP.md" in result.stdout
    assert "se publica con los datos sincronizados" in summary.read_text()


def test_publish_build_can_read_the_pages_site() -> None:
    assert load("publish.yml")["jobs"]["build"]["permissions"] == {
        "contents": "read",
        "pages": "read",
    }


def test_gcp_workflows_skip_other_branches() -> None:
    publish_cfg = step(load("publish.yml"), "sync", "cfg")["run"]
    assert 'if [ "$IS_DEFAULT_BRANCH" != "true" ]' in publish_cfg
    deploy_cfg = step(load("deploy-job.yml"), "deploy", "cfg")["run"]
    assert 'if [ "$GITHUB_REF" != "refs/heads/${DEFAULT_BRANCH}" ]' in deploy_cfg


def test_deploy_job_targets_the_scheduled_job() -> None:
    deploy = load("deploy-job.yml")
    assert deploy["env"]["JOB_NAME"] == "${{ vars.GCP_JOB_NAME || 'haggler-daily' }}"
    run = step(deploy, "deploy", "Deploy Cloud Run Job")["run"]
    assert '"${JOB_NAME}"' in run
    # Retries only follow crashes: haggler daily exits 0 once latest/ is persisted.
    assert "--max-retries 1" in run


def test_bootstrap_trusts_only_the_default_branch_and_prints_the_job_name() -> None:
    text = (ROOT / "infra" / "bootstrap.sh").read_text(encoding="utf-8")
    condition = re.search(r'^WIF_CONDITION="(.*)"$', text, re.M)
    assert condition
    assert condition.group(1) == (
        "assertion.repository=='${GITHUB_REPO}' && assertion.ref=='refs/heads/${GITHUB_BRANCH}'"
    )
    assert 'GITHUB_BRANCH="${GITHUB_BRANCH:-main}"' in text
    assert "GCP_JOB_NAME     = ${JOB}" in text
    assert "gh variable set GCP_JOB_NAME" in text


@pytest.mark.skipif(shutil.which("bash") is None, reason="bash not installed")
def test_bootstrap_is_valid_bash() -> None:
    subprocess.run(["bash", "-n", str(ROOT / "infra" / "bootstrap.sh")], check=True)


def test_deploy_job_also_deploys_the_research_job() -> None:
    deploy = load("deploy-job.yml")
    assert deploy["env"]["RESEARCH_JOB_NAME"] == (
        "${{ vars.GCP_RESEARCH_JOB_NAME || 'haggler-research' }}"
    )
    run = step(deploy, "deploy", "Deploy research Cloud Run Job")["run"]
    assert '"${RESEARCH_JOB_NAME}"' in run
    assert "--args=research,run,--store,gcs" in run
    # A retry would spend the search budget again.
    assert "--max-retries 0" in run
    assert "HAGGLER_RESEARCH_MODEL" in run


def test_bootstrap_schedules_the_research_job_weekly_and_expires_its_pages() -> None:
    text = (ROOT / "infra" / "bootstrap.sh").read_text(encoding="utf-8")
    assert 'RESEARCH_SCHEDULE="${RESEARCH_SCHEDULE:-23 5 * * 1}"' in text
    assert 'ensure_job_and_trigger "${RESEARCH_JOB}"' in text
    assert '"condition": {"age": 30, "matchesPrefix": ["raw/research/"]}' in text
    assert "gh variable set GCP_RESEARCH_JOB_NAME" in text

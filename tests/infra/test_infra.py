"""Invariants of the GitHub workflows and infra/bootstrap.sh (docs/SETUP.md relies on them)."""

from __future__ import annotations

import argparse
import os
import re
import shutil
import subprocess
import sys
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


def test_probe_dispatch_offers_every_probe_mode() -> None:
    from haggler.probe import MODES

    inputs = load("probe.yml")[True]["workflow_dispatch"]["inputs"]  # YAML 1.1: on -> True
    assert inputs["mode"]["options"] == list(MODES)
    assert "bundles" in inputs["mode"]["description"]
    assert "watch" in inputs["mode"]["description"]
    assert "probe/bundle_targets.txt" in inputs["targets"]["description"]
    assert "probe/watch_targets.txt" in inputs["targets"]["description"]


def _run_probe_step(tmp_path: Path, mode: str, override: str = "", secret: str = "") -> str:
    """Runs the real "Run probe" step script with a fake `uv` that records its call."""
    script = next(
        s for s in steps(load("probe.yml"), "probe") if s.get("name", "").startswith("Run probe")
    )["run"]
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir(exist_ok=True)
    uv = bin_dir / "uv"
    uv.write_text(
        '#!/bin/sh\n{ echo "args: $*"; echo "llm: ${HAGGLER_LLM:-}"; '
        'echo "key: ${GOOGLE_API_KEY:+set}"; } > "$UV_RECORD"\n'
    )
    uv.chmod(0o755)
    record = tmp_path / "uv-call.txt"
    result = subprocess.run(
        ["bash", "--noprofile", "--norc", "-eo", "pipefail", "-c", script],
        cwd=tmp_path,
        env={
            "PATH": f"{bin_dir}{os.pathsep}{os.environ['PATH']}",
            "MODE": mode,
            "TARGETS_OVERRIDE": override,
            "GEMINI_SECRET": secret,
            "RUNNER_TEMP": str(tmp_path),
            "UV_RECORD": str(record),
        },
        capture_output=True,
        text=True,
        timeout=60,
    )
    assert result.returncode == 0, result.stderr
    return record.read_text()


@pytest.mark.skipif(shutil.which("bash") is None, reason="bash not installed")
def test_probe_step_picks_the_targets_file_of_each_mode(tmp_path: Path) -> None:
    for mode, targets in (
        ("adapters", "probe/targets.txt"),
        ("research", "probe/research_targets.txt"),
        ("bundles", "probe/bundle_targets.txt"),
        ("watch", "probe/watch_targets.txt"),
    ):
        call = _run_probe_step(tmp_path, mode, secret="s3cret")
        assert f"args: run --frozen --no-dev haggler probe --targets {targets} --out probe-out " \
               f"--mode {mode}\n" in call  # fmt: skip
        # The LLM key only reaches full and research runs.
        llm_on = mode == "research"
        assert ("key: set" in call) is llm_on
        assert ("llm: off" in call) is not llm_on


@pytest.mark.skipif(shutil.which("bash") is None, reason="bash not installed")
def test_probe_bundles_override_is_parsed_by_the_bundle_targets_parser(
    tmp_path: Path, catalog, bundles
) -> None:
    from haggler.probe_bundles import parse_bundle_targets

    override = (
        "meli_plus_co_total CO https://www.mercadolibre.com.co/suscripciones/melimas/planes;"
        "  apple_one_co CO https://www.apple.com/co/apple-one/"
    )
    call = _run_probe_step(tmp_path, "bundles", override=override)
    assert f"--targets {tmp_path / 'targets.txt'} --out probe-out --mode bundles" in call
    targets = parse_bundle_targets((tmp_path / "targets.txt").read_text(), catalog, bundles)
    assert [(t.label, t.country, t.line) for t in targets] == [
        ("meli_plus_co_total", "CO", 1),
        ("apple_one_co", "CO", 2),
    ]


@pytest.mark.skipif(shutil.which("bash") is None, reason="bash not installed")
def test_probe_watch_override_is_parsed_as_bundle_ids(tmp_path: Path, bundles) -> None:
    from haggler.probe_watch import parse_watch_targets

    call = _run_probe_step(tmp_path, "watch", override="meli_plus_co_total;  apple_one_family_se;")
    assert f"--targets {tmp_path / 'targets.txt'} --out probe-out --mode watch\n" in call
    assert "llm: off" in call and "key: set" not in call
    targets = (tmp_path / "targets.txt").read_text()
    assert parse_watch_targets(targets, bundles) == ["meli_plus_co_total", "apple_one_family_se"]


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


# ------------------------------------------------------------------ bundles (docs/SETUP.md §14)

BUNDLE_SUMMARY = "Bundle review summary"
BUNDLE_SETTINGS = (
    ("HAGGLER_RESEARCH_MAX_BUNDLES", "research_max_bundles"),
    ("HAGGLER_BUNDLE_WATCH_MAX_PAGES", "bundle_watch_max_pages"),
)
UV_HAGGLER = "run --frozen --no-dev haggler"


def _bash(script: str, cwd: Path, env: dict[str, str]) -> subprocess.CompletedProcess[str]:
    """A workflow step's script under GitHub's default shell for run steps."""
    return subprocess.run(
        ["bash", "--noprofile", "--norc", "-eo", "pipefail", "-c", script],
        cwd=cwd,
        env=env,
        capture_output=True,
        text=True,
        timeout=120,
    )


def _fake_bin(tmp_path: Path, name: str, body: str) -> Path:
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir(exist_ok=True)
    tool = bin_dir / name
    tool.write_text("#!/bin/sh\n" + body)
    tool.chmod(0o755)
    return bin_dir


def _run_bundle_summary(tmp_path: Path, uv_body: str) -> tuple[subprocess.CompletedProcess, str]:
    """Runs the real publish step with a fake `uv`; returns the result and the job summary."""
    script = step(load("publish.yml"), "sync", BUNDLE_SUMMARY)["run"]
    bin_dir = _fake_bin(tmp_path, "uv", uv_body)
    summary = tmp_path / "summary.md"
    result = _bash(
        script,
        tmp_path,
        {
            "PATH": f"{bin_dir}{os.pathsep}{os.environ['PATH']}",
            "RUNNER_TEMP": str(tmp_path),
            "GITHUB_STEP_SUMMARY": str(summary),
            "UV_RECORD": str(tmp_path / "uv-call.txt"),
            "PYTHON": sys.executable,
        },
    )
    return result, summary.read_text() if summary.exists() else ""


def test_publish_summarizes_the_bundles_to_review_without_gating_the_site() -> None:
    publish = load("publish.yml")
    names = [s.get("name") for s in steps(publish, "sync")]
    # On the synced data/, once it was committed: nothing it does reaches what is pushed or built.
    assert names.index("haggler sync") < names.index("Commit and push data/")
    assert names.index("Commit and push data/") < names.index(BUNDLE_SUMMARY)
    summary = step(publish, "sync", BUNDLE_SUMMARY)
    assert summary["if"] == "steps.sync.outcome == 'success'"
    assert summary["continue-on-error"] is True
    # A step timeout is absorbed by continue-on-error; the job's timeout would fail the job.
    assert 0 < summary["timeout-minutes"] < publish["jobs"]["sync"]["timeout-minutes"]
    command = " ".join(summary["run"].replace("\\\n", " ").split())
    assert "haggler research review --bundles --summary --data-dir data" in command
    assert "docs/SETUP.md §14" in summary["run"]
    # The site still only depends on the sync.
    assert publish["jobs"]["sync"]["outputs"] == {
        "synced": "${{ steps.sync.outcome == 'success' }}"
    }
    assert "if" not in publish["jobs"]["build"]


@pytest.mark.skipif(shutil.which("bash") is None, reason="bash not installed")
def test_publish_bundle_summary_writes_the_counts_line(tmp_path: Path) -> None:
    line = "Paquetes: 1 propuesta de valor pendiente; 1 paquete por revisar: meli_plus_co_total."
    result, summary = _run_bundle_summary(
        tmp_path, f'echo "$*" > "$UV_RECORD"\necho "aviso: x" >&2\necho "{line}"\n'
    )
    assert result.returncode == 0, result.stderr
    assert (tmp_path / "uv-call.txt").read_text() == (
        f"{UV_HAGGLER} research review --bundles --summary --data-dir data\n"
    )
    assert f"### Paquetes\n\n{line}\n" in summary
    assert "haggler research review --bundles" in summary
    assert "aviso: x" in result.stderr  # the command's own warnings stay in the log
    assert "::warning" not in result.stdout


@pytest.mark.skipif(shutil.which("bash") is None, reason="bash not installed")
@pytest.mark.parametrize(
    "uv_body",
    [
        'echo "haggler research: ValidationError: boom" >&2\nexit 1\n',
        "exit 0\n",  # no line at all
    ],
    ids=["fails", "prints-nothing"],
)
def test_publish_bundle_summary_never_fails_the_job(tmp_path: Path, uv_body: str) -> None:
    result, summary = _run_bundle_summary(tmp_path, uv_body)
    assert result.returncode == 0, result.stderr
    assert "::warning title=resumen de paquetes::" in result.stdout
    assert "docs/SETUP.md §14" in result.stdout
    assert "::error" not in result.stdout
    assert "### Paquetes" not in summary
    if "boom" in uv_body:
        assert "boom" in result.stderr


@pytest.mark.skipif(shutil.which("bash") is None, reason="bash not installed")
def test_publish_bundle_summary_runs_with_the_real_cli_on_a_first_deploy(tmp_path: Path) -> None:
    """The step's arguments against the real CLI, on a data/ without research yet."""
    uv_body = (
        f'[ "$1 $2 $3 $4" = "{UV_HAGGLER}" ] || exit 97\n'
        "shift 4\n"
        'exec "$PYTHON" -c \'import sys; from haggler.cli import main; '
        'sys.exit(main(sys.argv[1:]))\' "$@"\n'
    )
    (tmp_path / "data").mkdir()
    result, summary = _run_bundle_summary(tmp_path, uv_body)
    assert result.returncode == 0, result.stderr
    assert "::warning" not in result.stdout, result.stderr
    assert "### Paquetes\n\nPaquetes: nada pendiente de revisión.\n" in summary


def _run_research_deploy(tmp_path: Path, **variables: str) -> tuple[list[str], str, str]:
    """Runs the real research deploy step with a fake `gcloud`; returns its --set-env-vars
    entries, the step's stdout and the job summary."""
    script = step(load("deploy-job.yml"), "deploy", "Deploy research Cloud Run Job")["run"]
    record = tmp_path / "gcloud-args.txt"
    gcloud = 'for a in "$@"; do echo "$a"; done > "$GCLOUD_RECORD"\n'  # one argument per line
    bin_dir = _fake_bin(tmp_path, "gcloud", gcloud)
    summary = tmp_path / "summary.md"
    result = _bash(
        script,
        tmp_path,
        {
            "PATH": f"{bin_dir}{os.pathsep}{os.environ['PATH']}",
            "GCLOUD_RECORD": str(record),
            "GITHUB_STEP_SUMMARY": str(summary),
            "GITHUB_SHA": "0123456789abcdef",
            "IMAGE": "europe-north2-docker.pkg.dev/p/haggler/haggler:0123",
            "RESEARCH_JOB_NAME": "haggler-research",
            "PROJECT_ID": "p",
            "REGION": "europe-north2",
            "JOB_SA": "haggler-job@p.iam.gserviceaccount.com",
            "GCS_BUCKET": "p-haggler-data",
            "RESEARCH_MODEL": "",
            "RESEARCH_MAX_BUNDLES": "",
            "BUNDLE_WATCH_MAX_PAGES": "",
            **variables,
        },
    )
    assert result.returncode == 0, result.stderr
    args = record.read_text().splitlines()
    env_vars = args[args.index("--set-env-vars") + 1].split(",")
    return env_vars, result.stdout, summary.read_text()


def test_deploy_job_maps_the_optional_bundle_settings_to_repository_variables() -> None:
    deploy = load("deploy-job.yml")
    research = step(deploy, "deploy", "Deploy research Cloud Run Job")
    assert research["env"]["RESEARCH_MAX_BUNDLES"] == "${{ vars.HAGGLER_RESEARCH_MAX_BUNDLES }}"
    assert research["env"]["BUNDLE_WATCH_MAX_PAGES"] == (
        "${{ vars.HAGGLER_BUNDLE_WATCH_MAX_PAGES }}"
    )
    # Phase 1 is untouched: the daily job never gets them.
    daily = step(deploy, "deploy", "Deploy Cloud Run Job")
    for name, _attr in BUNDLE_SETTINGS:
        assert name not in daily["run"]
        assert name not in str(daily.get("env", {}))


@pytest.mark.skipif(shutil.which("bash") is None, reason="bash not installed")
def test_deploy_job_passes_the_bundle_settings_only_when_set(tmp_path: Path) -> None:
    base = [
        "HAGGLER_STORE=gcs",
        "HAGGLER_GCS_BUCKET=p-haggler-data",
        "GOOGLE_GENAI_USE_ENTERPRISE=1",
        "GOOGLE_CLOUD_PROJECT=p",
        "GOOGLE_CLOUD_LOCATION=global",
    ]
    env_vars, stdout, summary = _run_research_deploy(tmp_path)
    assert env_vars == base
    assert "::warning" not in stdout
    # The 0-query run of docs/SETUP.md §14, ready to copy.
    assert "--args=research,run,--store,gcs,--watch-only --wait" in summary

    env_vars, stdout, _ = _run_research_deploy(
        tmp_path, RESEARCH_MAX_BUNDLES="3", BUNDLE_WATCH_MAX_PAGES="25", RESEARCH_MODEL="m"
    )
    assert env_vars == [
        *base,
        "HAGGLER_RESEARCH_MODEL=m",
        "HAGGLER_RESEARCH_MAX_BUNDLES=3",
        "HAGGLER_BUNDLE_WATCH_MAX_PAGES=25",
    ]
    assert "::warning" not in stdout


@pytest.mark.skipif(shutil.which("bash") is None, reason="bash not installed")
@pytest.mark.parametrize("value", ["two", "2,HAGGLER_STORE=local", "-1", "2.5", " 2"])
def test_deploy_job_skips_a_bundle_setting_that_is_not_a_whole_number(
    tmp_path: Path, value: str
) -> None:
    env_vars, stdout, _ = _run_research_deploy(
        tmp_path, RESEARCH_MAX_BUNDLES=value, BUNDLE_WATCH_MAX_PAGES=value
    )
    # The job keeps its defaults (a bad value would crash it at start, a comma set another one).
    assert not [e for e in env_vars if e.startswith(("HAGGLER_RESEARCH_MAX", "HAGGLER_BUNDLE"))]
    assert env_vars.count("HAGGLER_STORE=gcs") == 1
    assert "HAGGLER_STORE=local" not in env_vars
    assert stdout.count("::warning title=deploy-job::") == 2
    assert "HAGGLER_RESEARCH_MAX_BUNDLES no es un número entero" in stdout


@pytest.mark.parametrize(("name", "attr"), BUNDLE_SETTINGS)
def test_bundle_settings_are_real_and_documented_with_their_defaults(
    name: str, attr: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    from haggler.config import Settings

    monkeypatch.delenv(name, raising=False)
    default = getattr(Settings(), attr)
    monkeypatch.setenv(name, str(default + 1))
    assert getattr(Settings(), attr) == default + 1
    env_example = (ROOT / ".env.example").read_text(encoding="utf-8")
    assert re.search(rf"^# {name}={default}\s", env_example, re.M)
    setup = (ROOT / "docs" / "SETUP.md").read_text(encoding="utf-8")
    assert f"| `{name}` | variable |" in setup  # §3, optional repository variables
    assert f"| `{name}` | `{default}` |" in setup  # §14, settings


def test_setup_watch_only_run_is_the_deployed_research_job_plus_the_flag() -> None:
    from haggler.jobs.research import add_run_arguments, options_from_args

    run = step(load("deploy-job.yml"), "deploy", "Deploy research Cloud Run Job")["run"]
    job_args = re.search(r"--args=(\S+)", run)
    assert job_args and job_args.group(1) == "research,run,--store,gcs"
    watch_only = f"--args={job_args.group(1)},--watch-only"
    setup = (ROOT / "docs" / "SETUP.md").read_text(encoding="utf-8")
    execute = "gcloud run jobs execute haggler-research --region europe-north2 \\\n"
    assert f"{execute}  {watch_only} --wait" in setup
    # "research run" is the CLI's; the rest goes to the job's own parser.
    argv = [*job_args.group(1).split(",")[2:], "--watch-only"]
    options = options_from_args(add_run_arguments(argparse.ArgumentParser()).parse_args(argv))
    assert options.watch_only and options.store == "gcs"

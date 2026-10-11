"""ReplayFetcher and `haggler daily --replay` (offline end-to-end runs)."""

from __future__ import annotations

import gzip
import json
from pathlib import Path

import pytest
import wf_support as W

from haggler.fetch.errors import (
    BlockedError,
    FetchError,
    RobotsDisallowedError,
    TransientFetchError,
)
from haggler.fetch.pdf import extract_pdf_text
from haggler.fetch.replay import ReplayFetcher
from haggler.jobs import daily
from pdf_support import build_pdf


def write_index(root: Path, index: dict) -> None:
    (root / "index.json").write_text(json.dumps(index), encoding="utf-8")


async def test_replay_serves_index_entries(tmp_path: Path) -> None:
    (tmp_path / "p").mkdir()
    (tmp_path / "p" / "a.html").write_text("<p>129 kr</p>", encoding="utf-8")
    (tmp_path / "p" / "b.json.gz").write_bytes(gzip.compress(b'{"x": 1}'))
    write_index(
        tmp_path,
        {
            "_comment": "ignored",
            "https://a.example/": {"file": "p/a.html", "final_url": "https://a.example/se/"},
            "https://b.example/api": {"file": "p/b.json.gz", "content_type": "application/json"},
        },
    )
    rf = ReplayFetcher(tmp_path)
    resp = await rf.fetch("https://a.example/")
    assert (resp.status_code, resp.text, resp.final_url) == (
        200,
        "<p>129 kr</p>",
        "https://a.example/se/",
    )
    assert resp.content_type == "text/html" and resp.fetched_at is not None
    js = await rf.fetch("https://b.example/api", accept="application/json")
    assert json.loads(js.text) == {"x": 1} and js.content_type == "application/json"
    assert rf.covers("https://a.example/") and not rf.covers("https://c.example/")
    assert rf.calls == ["https://a.example/", "https://b.example/api"]


async def test_replay_errors_match_the_http_fetcher(tmp_path: Path) -> None:
    (tmp_path / "gone.html").write_text("not here", encoding="utf-8")
    (tmp_path / "wall.html").write_text(
        "<title>Just a moment...</title><div id='cf-chl-widget'></div>", encoding="utf-8"
    )
    write_index(
        tmp_path,
        {
            "https://x/transient": {"error": "transient"},
            "https://x/blocked": {"error": "blocked", "status": 403},
            "https://x/robots": {"error": "robots"},
            "https://x/gone": {"file": "gone.html", "status": 410},
            "https://x/wall": {"file": "wall.html", "status": 403},
            "https://x/oops": {"file": "gone.html", "status": 503},
        },
    )
    rf = ReplayFetcher(tmp_path)
    with pytest.raises(FetchError) as missing:
        await rf.fetch("https://x/missing")
    assert missing.value.status == 404 and type(missing.value) is FetchError
    with pytest.raises(TransientFetchError):
        await rf.fetch("https://x/transient")
    with pytest.raises(BlockedError):
        await rf.fetch("https://x/blocked")
    with pytest.raises(RobotsDisallowedError):
        await rf.fetch("https://x/robots")
    with pytest.raises(FetchError) as gone:
        await rf.fetch("https://x/gone")
    assert gone.value.status == 410
    with pytest.raises(BlockedError):
        await rf.fetch("https://x/wall")
    with pytest.raises(TransientFetchError):
        await rf.fetch("https://x/oops")


async def test_replay_binary_entries(tmp_path: Path) -> None:
    pdf = build_pdf([["Prislista 299 kr/mån"]])
    (tmp_path / "list.pdf").write_bytes(pdf)
    (tmp_path / "list.pdf.gz").write_bytes(gzip.compress(pdf))
    (tmp_path / "image.png").write_bytes(b"\x89PNG\r\n\x1a\n\xff\xfe")
    (tmp_path / "page.html").write_text("<p>%PDF-1.4 129 kr</p>", encoding="utf-8")
    write_index(
        tmp_path,
        {
            "https://x/declared.pdf": {
                "file": "list.pdf",
                "binary": True,
                "content_type": "application/pdf",
            },
            "https://x/download": {"file": "list.pdf.gz", "binary": True},
            "https://x/image": {"file": "image.png", "binary": True, "content_type": "image/png"},
            "https://x/page": {"file": "page.html"},
        },
    )
    rf = ReplayFetcher(tmp_path)
    declared = await rf.fetch("https://x/declared.pdf")
    assert (declared.body, declared.content_type) == (pdf, "application/pdf")
    assert declared.text == pdf.decode("utf-8", errors="replace")
    # No content type: octet-stream, and the "%PDF-" signature still keeps the bytes.
    download = await rf.fetch("https://x/download")
    assert (download.body, download.content_type) == (pdf, "application/octet-stream")
    image = await rf.fetch("https://x/image")
    assert image.body == b"" and image.text.startswith("\ufffdPNG")
    page = await rf.fetch("https://x/page")
    assert (page.body, page.text, page.content_type) == (b"", "<p>%PDF-1.4 129 kr</p>", "text/html")
    # The bytes reach the extractor like an HttpFetcher download.
    text = await extract_pdf_text(download.body)
    assert text.lines == ("Prislista 299 kr/mån",)


def test_replay_rejects_bad_index(tmp_path: Path) -> None:
    write_index(tmp_path, {"https://x/": {"status": 200}})
    with pytest.raises(ValueError, match="needs 'file' or 'error'"):
        ReplayFetcher(tmp_path)


async def test_replay_refuses_files_outside_its_directory(tmp_path: Path) -> None:
    root = tmp_path / "replay"
    root.mkdir()
    (tmp_path / "secret.txt").write_text("x", encoding="utf-8")
    write_index(root, {"https://x/": {"file": "../secret.txt"}})
    with pytest.raises(FetchError, match="escapes"):
        await ReplayFetcher(root).fetch("https://x/")


def test_shipped_replay_set_is_consistent() -> None:
    rf = ReplayFetcher(W.REPLAY_DIR)
    for entry in rf.index.values():
        if "file" in entry:
            assert (W.REPLAY_DIR / entry["file"]).is_file()
    assert (W.REPLAY_DIR / "fx.json").is_file()


def _clean_env(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    for var in ("GOOGLE_API_KEY", "GEMINI_API_KEY", "GOOGLE_GENAI_USE_ENTERPRISE"):
        monkeypatch.delenv(var, raising=False)
    monkeypatch.setenv("HAGGLER_LOCAL_DIR", str(tmp_path / "store"))
    monkeypatch.setenv("HAGGLER_PER_HOST_INTERVAL_S", "0")


def test_daily_main_replay_to_local_store(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    _clean_env(monkeypatch, tmp_path)
    code = daily.main(
        ["--replay", str(W.REPLAY_DIR), "--store", "local", "--date", "2026-10-08", "--no-llm"]
    )
    out = capsys.readouterr().out.strip().splitlines()
    summary = json.loads(out[-1])
    assert code == 0, summary
    assert summary["ok"] is True and summary["run_date"] == "2026-10-08"
    # Only the URLs of the replay index are planned.
    assert summary["tasks_total"] == 5
    assert summary["tasks_by_status"] == {"blocked": 1, "failed": 1, "ok": 3}
    assert summary["llm"]["skipped_budget"] == 1
    latest = tmp_path / "store" / "latest"
    assert sorted(p.name for p in latest.iterdir()) == [
        "coverage.json",
        "fx.json",
        "prices.json",
        "review_queue.json",
        "run.json",
        "state.json",
    ]
    manifest = json.loads((latest / "run.json").read_text())
    assert manifest["run_id"] == summary["run_id"]
    rows = json.loads((latest / "prices.json").read_text())
    assert {r["obs_key"] for r in rows} >= {
        "netflix:standard:SE:web:month",
        "spotify:individual:SE:web:month",
        "apple_one:individual:SE:web:month",
    }
    raw = tmp_path / "store" / "raw" / "2026-10-08" / "netflix_help"
    assert (raw / "netflix_help__netflix__SE.html.gz").is_file()


def test_daily_main_dry_run_writes_nothing(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    _clean_env(monkeypatch, tmp_path)
    code = daily.main(["--replay", str(W.REPLAY_DIR), "--store", "local", "--dry-run", "--no-llm"])
    summary = json.loads(capsys.readouterr().out.strip().splitlines()[-1])
    assert code == 0 and summary["dry_run"] is True and summary["outputs"] == 0
    assert not (tmp_path / "store").exists()


def test_daily_main_low_success_ratio_is_a_warning_not_a_retry(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Once latest/* and run.json are written the job exits 0, whatever the success ratio:
    Cloud Run's --max-retries 1 would otherwise crawl every vendor again the same night."""
    _clean_env(monkeypatch, tmp_path)
    code = daily.main(
        ["--replay", str(W.REPLAY_DIR), "--store", "memory", "--no-llm", "--services", "spotify"]
    )
    summary = json.loads(capsys.readouterr().out.strip().splitlines()[-1])
    # spotify SE ok + spotify CO blocked = 50%; netflix filtered out.
    assert summary["tasks_total"] == 2 and code == 0
    assert summary["low_success_ratio"] is False
    code = daily.main(
        [
            "--replay",
            str(W.REPLAY_DIR),
            "--store",
            "local",
            "--no-llm",
            "--services",
            "spotify",
            "--countries",
            "CO",
        ]
    )
    summary = json.loads(capsys.readouterr().out.strip().splitlines()[-1])
    assert summary["tasks_by_status"] == {"blocked": 1}
    assert (summary["success_ratio"], summary["low_success_ratio"]) == (0.0, True)
    assert summary["persisted"] is True and code == 0
    assert (tmp_path / "store" / "latest" / "run.json").is_file()
    warnings = [r for r in caplog.records if r.name == "haggler.jobs.daily"]
    assert [r.levelname for r in warnings] == ["WARNING"]
    assert "low success ratio 0.00" in warnings[0].getMessage()


def test_daily_main_exits_1_when_nothing_was_persisted(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    _clean_env(monkeypatch, tmp_path)

    async def crash(options, deps=None):
        raise RuntimeError("store unreachable")

    monkeypatch.setattr(daily, "run_daily", crash)
    assert daily.main(["--store", "memory", "--no-llm"]) == 1
    out = json.loads(capsys.readouterr().out.strip().splitlines()[-1])
    assert out == {"ok": False, "error": "RuntimeError: store unreachable"}

    async def no_report(options, deps=None):
        return None

    monkeypatch.setattr(daily, "run_daily", no_report)
    assert daily.main(["--store", "memory", "--no-llm"]) == 1


def test_exit_code_rules() -> None:
    from datetime import UTC, date, datetime

    from haggler.models.run import RunReport

    report = RunReport(
        run_id="r",
        run_date=date(2026, 10, 8),
        started_at=datetime(2026, 10, 8, tzinfo=UTC),
        tasks_total=4,
        tasks_by_status={"ok": 1, "failed": 3},
        outputs=["latest/prices.json", "latest/run.json"],
    )
    assert daily.exit_code(None, dry_run=False) == 1
    assert daily.exit_code(None, dry_run=True) == 1
    assert daily.exit_code(report, dry_run=False) == 0  # persisted: low ratio is only a warning
    assert daily.exit_code(report, dry_run=True) == 0
    assert daily.degraded(report) and daily.summary(report)["low_success_ratio"] is True
    report.tasks_by_status = {"ok": 1, "partial": 1, "failed": 2}
    assert daily.exit_code(report, dry_run=False) == 0 and not daily.degraded(report)
    report.outputs = []  # a report, but persist wrote nothing
    assert daily.exit_code(report, dry_run=False) == 1


def test_daily_main_logs_handled_node_errors_as_warnings(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
    caplog: pytest.LogCaptureFixture,
) -> None:
    """ADK logs every exception a node raises at ERROR with a traceback, even a retried
    TransientFetchError the pipeline handles; Cloud Logging would flag healthy runs."""
    import dataclasses
    import logging

    _clean_env(monkeypatch, tmp_path)
    root = tmp_path / "replay"
    root.mkdir()
    write_index(root, {"https://www.spotify.com/se/premium/": {"error": "transient"}})
    (root / "fx.json").write_bytes((W.REPLAY_DIR / "fx.json").read_bytes())
    real = daily.build_default_deps
    monkeypatch.setattr(
        daily,
        "build_default_deps",
        lambda settings=None: dataclasses.replace(real(settings), fetch_retry_delay=0.01),
    )
    caplog.set_level(logging.INFO)
    daily.main(["--replay", str(root), "--store", "memory", "--no-llm"])
    summary = json.loads(capsys.readouterr().out.strip().splitlines()[-1])
    assert summary["tasks_by_status"] == {"failed": 1}
    adk = [r for r in caplog.records if r.name == "google_adk.google.adk.workflow._node_runner"]
    failures = [r for r in adk if r.getMessage().startswith("Node execution failed")]
    assert len(failures) == 2  # attempts 1 and 2 raise; the last attempt is a failed record
    assert all(r.levelname == "WARNING" and r.exc_info is None for r in failures)
    assert "TransientFetchError" in failures[0].getMessage()
    assert not [r for r in caplog.records if r.levelno >= logging.ERROR]


def test_cli_flags_parse() -> None:
    import argparse

    ns = daily.add_daily_arguments(argparse.ArgumentParser()).parse_args(
        [
            "--store",
            "gcs",
            "--services",
            "netflix, spotify",
            "--countries",
            "se,CO",
            "--adapters",
            "netflix_help",
            "--date",
            "2026-10-08",
            "--dry-run",
            "--no-llm",
            "--replay",
            "x",
        ]
    )
    opts = daily.options_from_args(ns)
    assert opts.services == ["netflix", "spotify"] and opts.countries == ["SE", "CO"]
    assert opts.adapters == ["netflix_help"] and opts.store == "gcs"
    assert opts.dry_run and opts.no_llm and opts.replay == Path("x")
    assert opts.request() == {
        "date": "2026-10-08",
        "services": ["netflix", "spotify"],
        "countries": ["SE", "CO"],
        "adapters": ["netflix_help"],
        "dry_run": True,
    }

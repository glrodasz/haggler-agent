"""haggler.fetch.pdf: PDF detection and text extraction in the sandboxed worker (PDFs are built
as bytes in the tests; the slow cases stay well under 10 s and run by default)."""

from __future__ import annotations

import asyncio
import functools
import itertools
import json
import os
import signal
import subprocess
import sys
from pathlib import Path

import pytest
from pypdf import PageObject
from pypdf.constants import UserAccessPermissions

from haggler.fetch import RawResponse
from haggler.fetch import pdf as pdf_module
from haggler.fetch._pdf_worker import Limits, clean_lines, extract
from haggler.fetch.pdf import (
    MAX_PAGES,
    WORKER_MODULE,
    extract_pdf_text,
    has_pdf_signature,
    is_pdf_response,
    parse_worker_output,
    pdf_payload,
    worker_argv,
)
from pdf_support import (
    TWO_COLUMN_ROWS,
    TWO_COLUMN_STREAM,
    blank_pdf,
    build_pdf,
    encrypted_pdf,
    flate_bomb_pdf,
    text_stream,
    two_column_pdf,
)

PRICE_LIST = [
    ["Prislista Telia Mobil", "Telia Mobil 20 GB 299 kr/mån", "Netflix Standard ingår"],
    ["Go3 Filmai + Netflix 17,99 € per månad", "(c) Telia Sverige AB"],
]


@functools.cache
def heavy_pdf() -> bytes:
    """One page of 150k text operators: seconds of pypdf CPU (more than the 20 s limit in the
    layout mode), so the CPU and wall-clock limits are what stops it."""
    stream = b"BT /F1 11 Tf 72 760 Td\n" + b"(x) Tj 1 0 Td\n" * 150_000 + b"ET"
    return build_pdf(streams=[stream], flate=True)


# ------------------------------------------------------------------ detection


@pytest.mark.parametrize(
    ("content_type", "data", "kept"),
    [
        ("application/pdf", b"%PDF-1.7 ...", True),
        ("application/pdf; charset=binary", b"not even a pdf", True),
        ("application/octet-stream", b"%PDF-1.4\n...", True),
        ("application/octet-stream", b"\xef\xbb\xbf \r\n\t%PDF-1.4\n...", True),
        ("binary/octet-stream", b"\x00\x00%PDF-1.5", True),
        ("", b"%PDF-1.4\n...", True),
        (None, b"\n%PDF-1.4\n...", True),
        ("application/octet-stream", b"PK\x03\x04 a zip file", False),
        ("application/octet-stream", b"<html>%PDF-1.4</html>", False),
        ("text/html", b"%PDF-1.4 served as HTML", False),
        ("text/html", b"<html><body>129 kr</body></html>", False),
        ("application/json", b'{"pdf": "%PDF-"}', False),
    ],
)
def test_pdf_payload(content_type, data, kept):
    assert pdf_payload(content_type, data) == (data if kept else b"")


def test_signature_and_response_helpers():
    assert has_pdf_signature(b"\xef\xbb\xbf%PDF-1.7") and not has_pdf_signature(b"%PDF")
    declared = RawResponse(url="u", final_url="u", status_code=200, text="%PDF",
                           content_type="application/pdf")  # fmt: skip
    sniffed = RawResponse(url="u", final_url="u", status_code=200, text="", body=b"%PDF-1.4",
                          content_type="application/octet-stream")  # fmt: skip
    page = RawResponse(url="u", final_url="u", status_code=200, text="<p>x</p>")
    assert is_pdf_response(declared) and is_pdf_response(sniffed)
    assert not is_pdf_response(page) and page.body == b""


# ------------------------------------------------------------------ line cleaning


def test_clean_lines_joins_hyphenated_breaks_and_collapses_whitespace():
    text = (
        "  Netflix   Standard ingår i abonne-\n  manget\r\n\n"
        "Telia-\nMobil kostar 299 kr\n"
        "avgift­\nfri 12-\n24 månader\n   \n"
    )
    assert clean_lines(text) == [
        "Netflix Standard ingår i abonnemanget",
        # A capital after the break is more likely a real compound: kept as two lines.
        "Telia-",
        "Mobil kostar 299 kr",
        # A soft hyphen always joins; digits never do.
        "avgiftfri 12-",
        "24 månader",
    ]


# ------------------------------------------------------------------ extraction


async def test_text_is_extracted_in_lines():
    result = await extract_pdf_text(build_pdf(PRICE_LIST))
    assert (result.status, result.pages, result.ok, result.detail) == ("ok", 2, True, "")
    assert result.lines == tuple(line for page in PRICE_LIST for line in page)
    assert result.stream_lines == result.lines
    assert result.text == "\n".join(result.lines)


async def test_hyphenated_break_within_a_page_is_joined():
    result = await extract_pdf_text(build_pdf([["Bindningstid 24 månader för abonne-", "manget"]]))
    assert result.lines == ("Bindningstid 24 månader för abonnemanget",)
    assert result.stream_lines == result.lines


async def test_layout_mode_keeps_table_rows_and_plain_mode_follows_the_stream():
    # The primary lines (Page.lines(), page_hash, the probe's amount lines) are layout mode: a
    # price stays next to its plan. Plain mode reads a column at a time.
    result = await extract_pdf_text(two_column_pdf())
    assert result.status == "ok"
    assert result.lines == TWO_COLUMN_ROWS
    assert result.stream_lines == TWO_COLUMN_STREAM


@pytest.mark.parametrize("failing", ["layout", "plain"])
@pytest.mark.parametrize("failure", ["raises", "empty"])
def test_a_mode_that_fails_on_a_page_falls_back_to_the_other(monkeypatch, failing, failure):
    original = PageObject.extract_text

    def flaky(self, *args, extraction_mode="plain", **kw):
        if extraction_mode == failing:
            if failure == "raises":
                raise ValueError("odd font")
            return ""
        return original(self, *args, extraction_mode=extraction_mode, **kw)

    monkeypatch.setattr(PageObject, "extract_text", flaky)
    out = extract(two_column_pdf(), Limits(0, 0, 10**7, MAX_PAGES, 400_000))
    other = TWO_COLUMN_STREAM if failing == "layout" else TWO_COLUMN_ROWS
    assert (out["status"], out["lines"], out["stream_lines"]) == ("ok", [*other], [*other])
    assert out["detail"] == ("1 extraction error(s) on some pages" if failure == "raises" else "")


async def test_lone_surrogates_from_a_broken_cmap_become_replacement_characters():
    # pypdf decodes ToUnicode with "surrogatepass": code 0x41 ("A") maps to a lone U+D800.
    body = build_pdf([["ABBA Netflix 17,99 € men"]], to_unicode={"41": "D800"})
    result = await extract_pdf_text(body)
    assert result.status == "ok"
    assert result.lines == result.stream_lines == ("\ufffdBB\ufffd Netflix 17,99 € men",)
    result.text.encode("utf-8")  # text_hash, summary.json: never a UnicodeEncodeError
    # A valid mapping is untouched.
    valid = await extract_pdf_text(build_pdf([["ABBA"]], to_unicode={"41": "00C5"}))
    assert valid.lines == ("ÅBBÅ",)


async def test_compressed_content_stream():
    result = await extract_pdf_text(
        build_pdf(streams=[text_stream(["Precio 24.900 COP"])], flate=True)
    )
    assert (result.status, result.lines) == ("ok", ("Precio 24.900 COP",))


async def test_encrypted_with_a_password_is_never_opened():
    result = await extract_pdf_text(encrypted_pdf(["Hemligt pris 99 kr"]))
    assert result.status == "pdf_encrypted" and result.lines == ()
    assert "password" in result.detail


async def test_encrypted_with_the_empty_password_opens_like_any_viewer():
    result = await extract_pdf_text(encrypted_pdf(["Öppen prislista 99 kr"], user_password=""))
    assert (result.status, result.lines) == ("ok", ("Öppen prislista 99 kr",))


async def test_encrypted_without_the_extract_permission_is_respected():
    body = encrypted_pdf(
        ["Utskrift ja, kopiering nej"], user_password="", permissions=UserAccessPermissions.PRINT
    )
    result = await extract_pdf_text(body)
    assert result.status == "pdf_encrypted" and "extraction" in result.detail


async def test_blank_pdf_has_no_text():
    result = await extract_pdf_text(blank_pdf())
    assert (result.status, result.pages, result.lines) == ("pdf_no_text", 1, ())


async def test_page_cap():
    at_cap = await extract_pdf_text(blank_pdf(MAX_PAGES))
    over = await extract_pdf_text(blank_pdf(MAX_PAGES + 1))
    assert at_cap.status == "pdf_no_text"
    assert (over.status, over.pages) == ("pdf_limit", MAX_PAGES + 1)
    assert over.reason == f"pdf_limit: {MAX_PAGES + 1} pages > {MAX_PAGES}"


async def test_text_cap(monkeypatch):
    monkeypatch.setattr(pdf_module, "MAX_TEXT_CHARS", 20)
    result = await extract_pdf_text(build_pdf([["A line of more than twenty characters"]]))
    assert result.status == "pdf_limit" and "characters" in result.detail


async def test_body_cap_and_empty_body_never_start_a_worker(monkeypatch):
    async def no_spawn(*args, **kw):
        raise AssertionError("worker started")

    monkeypatch.setattr(asyncio, "create_subprocess_exec", no_spawn)
    big = b"%PDF-1.4\n" + bytes(pdf_module.MAX_PDF_BYTES)
    assert (await extract_pdf_text(big)).status == "pdf_limit"
    assert (await extract_pdf_text(b"")).status == "pdf_error"


@pytest.mark.parametrize(
    "body", [b"%PDF-1.4 truncated garbage", b"%PDF-1.7\n1 0 obj << /Type /Catalog", b"<html>"]
)
async def test_broken_pdf_is_an_error_not_an_exception(body):
    result = await extract_pdf_text(body)
    assert result.status in {"pdf_error", "pdf_no_text"} and result.lines == ()


@pytest.mark.slow
async def test_decompression_bomb_hits_a_limit():
    # 128 MiB of zeros in one Flate stream: pypdf's own output cap (75 MB) stops it.
    result = await extract_pdf_text(flate_bomb_pdf(128))
    assert result.status == "pdf_limit" and result.lines == ()


@pytest.mark.slow
async def test_decompression_bomb_under_pypdfs_cap_hits_the_memory_limit():
    # 70 MiB stays under pypdf's cap: RLIMIT_AS (lowered for the test) is what stops it.
    result = await extract_pdf_text(flate_bomb_pdf(70), memory_bytes=128 * 1024 * 1024)
    assert result.status == "pdf_limit" and "memory" in result.detail


@pytest.mark.slow
async def test_cpu_limit_stops_a_pathological_document():
    result = await extract_pdf_text(heavy_pdf(), cpu_s=1)
    assert (result.status, result.detail) == ("pdf_limit", "CPU time limit (1 s)")


@pytest.mark.slow
async def test_wall_clock_kill():
    result = await extract_pdf_text(heavy_pdf(), timeout_s=0.5)
    assert result.status == "pdf_error" and result.detail == "worker killed after 0.5 s"


# ------------------------------------------------------------------ worker process


def _fake_worker(monkeypatch, code: str) -> None:
    monkeypatch.setattr(pdf_module, "worker_argv", lambda **kw: [sys.executable, "-I", "-c", code])


async def test_one_worker_at_a_time(monkeypatch):
    _fake_worker(
        monkeypatch,
        "import json, sys, time\n"
        "start = time.monotonic(); time.sleep(0.2)\n"
        "sys.stdout.write(json.dumps({'status': 'pdf_no_text', "
        "'detail': f'{start} {time.monotonic()}'}))",
    )
    results = await asyncio.gather(*(extract_pdf_text(b"%PDF-1.4") for _ in range(3)))
    spans = sorted(tuple(map(float, r.detail.split())) for r in results)
    assert all(a[1] <= b[0] for a, b in itertools.pairwise(spans))


async def test_a_cancelled_extraction_kills_its_worker(monkeypatch):
    procs = []
    spawn = asyncio.create_subprocess_exec

    async def recording(*args, **kw):
        procs.append(await spawn(*args, **kw))
        return procs[-1]

    monkeypatch.setattr(asyncio, "create_subprocess_exec", recording)
    _fake_worker(monkeypatch, "import time; time.sleep(30)")
    task = asyncio.ensure_future(extract_pdf_text(b"%PDF-1.4"))
    while not procs:
        await asyncio.sleep(0.01)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert await asyncio.wait_for(procs[0].wait(), 5) == -signal.SIGKILL


async def test_crashing_worker_is_an_error(monkeypatch):
    _fake_worker(monkeypatch, "raise SystemExit('boom: worker crashed')")
    result = await extract_pdf_text(b"%PDF-1.4")
    assert result.status == "pdf_error" and "boom: worker crashed" in result.detail


def test_parse_worker_output():
    ok = json.dumps({"status": "ok", "pages": 1, "lines": ["a"], "stream_lines": ["a"]}).encode()
    assert parse_worker_output(ok, b"", 0, cpu_s=20).lines == ("a",)
    cases = [
        (b"not json", b"Traceback ...\nValueError: x", 1, "pdf_error", "ValueError: x"),
        (b"", b"", -signal.SIGXCPU, "pdf_limit", "CPU time limit (20 s)"),
        (b"", b"", -signal.SIGKILL, "pdf_error", "worker killed by SIGKILL"),
        (b"", b"", -signal.SIGSEGV, "pdf_error", "worker killed by SIGSEGV"),
        (b'{"status": "pwned"}', b"", 0, "pdf_error", "bad worker output"),
        (b'{"status": "ok", "lines": []}', b"", 0, "pdf_error", "bad worker output"),
        (b'{"status": "ok", "lines": ["a", 1], "stream_lines": []}', b"", 0, "pdf_error", "bad"),
        (b'["ok"]', b"", 0, "pdf_error", "worker exit 0"),
    ]
    for out, err, code, status, detail in cases:
        got = parse_worker_output(out, err, code, cpu_s=20)
        assert (got.status, got.lines) == (status, ()), (out, code)
        assert detail in got.detail, (out, code)
    # The worker's output is checked again: a lone surrogate never leaves this function, a
    # valid escaped pair is one code point.
    lines, stream = ["\ud800 kr", "\ud83d\ude00"], ["\udc00"]
    hostile = json.dumps(
        {"status": "ok", "lines": lines, "stream_lines": stream, "detail": "x\udfff"}
    ).encode()
    got = parse_worker_output(hostile, b"", 0, cpu_s=20)
    assert got.lines == ("\ufffd kr", "\U0001f600") and got.stream_lines == ("\ufffd",)
    assert got.detail == "x\ufffd" and got.text.encode("utf-8")
    failed = json.dumps({"status": "pdf_error", "detail": "bad \ud800"}).encode()
    assert parse_worker_output(failed, b"", 0, cpu_s=20).reason == "pdf_error: bad \ufffd"
    too_long = json.dumps(
        {"status": "ok", "lines": ["x" * (pdf_module.MAX_TEXT_CHARS + 1)], "stream_lines": []}
    ).encode()
    assert parse_worker_output(too_long, b"", 0, cpu_s=20).status == "pdf_limit"


def test_child_environment_has_no_secrets(monkeypatch):
    monkeypatch.setenv("GEMINI_API_KEY", "secret")
    monkeypatch.setenv("GOOGLE_APPLICATION_CREDENTIALS", "/creds.json")
    env = pdf_module._child_env()
    assert "GEMINI_API_KEY" not in env and "GOOGLE_APPLICATION_CREDENTIALS" not in env
    assert set(env) <= set(pdf_module._CHILD_ENV_KEYS)


async def test_the_worker_process_gets_the_stripped_environment(monkeypatch):
    monkeypatch.setenv("GEMINI_API_KEY", "secret")
    monkeypatch.setenv("GOOGLE_APPLICATION_CREDENTIALS", "/creds.json")
    monkeypatch.setenv("AWS_SECRET_ACCESS_KEY", "secret")
    _fake_worker(
        monkeypatch,
        "import json, os, sys\n"
        "names = ' '.join(sorted(os.environ))\n"
        "sys.stdout.write(json.dumps({'status': 'pdf_no_text', 'detail': names}))",
    )
    result = await extract_pdf_text(b"%PDF-1.4")
    assert result.status == "pdf_no_text"
    seen = set(result.detail.split())
    assert seen <= set(pdf_module._CHILD_ENV_KEYS), seen
    assert not {"GEMINI_API_KEY", "GOOGLE_APPLICATION_CREDENTIALS", "AWS_SECRET_ACCESS_KEY"} & seen


def test_worker_imports_isolated_from_another_directory(tmp_path: Path):
    """`python -I -m haggler.fetch._pdf_worker` finds the installed package (editable here, as in
    the Docker image's `uv sync`) with neither the cwd nor PYTHONPATH on sys.path."""
    argv = worker_argv(cpu_s=20, memory_bytes=pdf_module.MEMORY_LIMIT_BYTES)
    assert argv[:4] == [sys.executable, "-I", "-m", WORKER_MODULE]
    done = subprocess.run(
        argv,
        input=build_pdf([["Fristående läsning 49 kr"]]),
        capture_output=True,
        cwd=tmp_path,
        env={"PYTHONPATH": "/nonexistent", "PATH": os.defpath},
        timeout=30,
        check=False,
    )
    assert done.returncode == 0, done.stderr
    out = json.loads(done.stdout)
    assert (out["status"], out["lines"]) == ("ok", ["Fristående läsning 49 kr"])

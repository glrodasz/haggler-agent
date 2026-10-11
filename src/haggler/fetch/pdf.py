"""PDF text, extracted in a sandboxed child process. CONTRACT.

WHY a child process: a PDF is an untrusted, compressed, recursive format, and pypdf is a pure
Python parser that a hostile or broken document can make eat memory (decompression bombs) or CPU
(pathological content streams). It never runs in the job's process:
- `extract_pdf_text(body)` starts `sys.executable -I -m haggler.fetch._pdf_worker` (isolated mode:
  no PYTHON* variables, no user site-packages, neither the script's directory nor the current
  one on sys.path; the installed haggler package is found through the venv's site-packages,
  editable or not), writes the bytes to its stdin and reads one JSON object from its stdout;
- the worker limits itself before parsing (RLIMIT_AS 384 MiB, RLIMIT_CPU 20 s; see
  _pdf_worker for why there and not in a preexec_fn); the parent kills it after 30 s of wall
  clock; one worker runs at a time (per event loop);
- the child gets a minimal environment (no API keys or credentials);
- caps: MAX_PDF_BYTES of body, MAX_PAGES pages, MAX_TEXT_CHARS characters of text.

Bad documents never raise: the result's status is ok | pdf_encrypted | pdf_no_text | pdf_limit
(a cap or a resource limit) | pdf_error (unparseable, worker crash or kill, bad output).
Every string of the result is valid Unicode: pypdf decodes ToUnicode CMaps with "surrogatepass",
so a broken or hostile CMap yields lone UTF-16 surrogates, which no UTF-8 encode (text_hash,
summary.json) accepts; parse_worker_output turns each into U+FFFD.
PDF text is evidence only: it is never sent to an LLM.

`pdf_payload` decides which responses carry their raw bytes (RawResponse.body): application/pdf,
or an octet-stream / missing content type whose body starts with "%PDF-" (after an optional BOM
or whitespace). Everything else keeps body == b"".
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import os
import re
import signal
import sys
import weakref
from dataclasses import dataclass
from typing import TYPE_CHECKING, Literal, get_args

if TYPE_CHECKING:
    from haggler.fetch import RawResponse

# Equal to fetch.client.MAX_BODY_BYTES: a PDF the fetcher accepts is never refused for its size.
MAX_PDF_BYTES = 10 * 1024 * 1024
MAX_PAGES = 60
MAX_TEXT_CHARS = 400_000
MEMORY_LIMIT_BYTES = 384 * 1024 * 1024
CPU_LIMIT_S = 20
WALL_TIMEOUT_S = 30.0
WORKER_MODULE = "haggler.fetch._pdf_worker"
DETAIL_CHARS = 200

PdfStatus = Literal["ok", "pdf_encrypted", "pdf_no_text", "pdf_limit", "pdf_error"]
PDF_STATUSES: tuple[str, ...] = get_args(PdfStatus)

PDF_TYPES = frozenset({"application/pdf", "application/x-pdf"})
# Content types under which a "%PDF-" body is still a PDF (servers and buckets often say so).
OCTET_TYPES = frozenset({"", "application/octet-stream", "binary/octet-stream"})
_BOM = b"\xef\xbb\xbf"
_LEAD = b" \t\r\n\x0c\x00"
# json.loads joins an escaped surrogate pair into one code point, so a surrogate left in a parsed
# string is a lone one.
_SURROGATE = re.compile("[\ud800-\udfff]")
# Environment variables the worker may inherit: what a Python needs to start, nothing secret.
_CHILD_ENV_KEYS = ("PATH", "LD_LIBRARY_PATH", "LANG", "LC_ALL", "LC_CTYPE", "TMPDIR", "SYSTEMROOT")


@dataclass(frozen=True)
class PdfText:
    status: PdfStatus
    pages: int = 0
    # Layout-mode lines (table rows kept together), plain mode on pages where layout had none.
    lines: tuple[str, ...] = ()
    # Plain-mode lines (content-stream order), layout mode on pages where plain had none.
    stream_lines: tuple[str, ...] = ()
    detail: str = ""

    @property
    def ok(self) -> bool:
        return self.status == "ok"

    @property
    def text(self) -> str:
        return "\n".join(self.lines)

    @property
    def reason(self) -> str:
        """The status and its detail, as Page.detail shows a failure ("pdf_limit: 75 pages
        > 60"); the status alone when there is no detail."""
        return f"{self.status}: {self.detail}" if self.detail else self.status


def mime_of(content_type: str | None) -> str:
    return (content_type or "").split(";", 1)[0].strip().lower()


def has_pdf_signature(data: bytes) -> bool:
    head = data[:1024]
    head = head.removeprefix(_BOM)
    return head.lstrip(_LEAD).startswith(b"%PDF-")


def pdf_payload(content_type: str | None, data: bytes) -> bytes:
    """`data` when the response is a PDF whose bytes RawResponse.body must carry, else b""."""
    mime = mime_of(content_type)
    if mime in PDF_TYPES or (mime in OCTET_TYPES and has_pdf_signature(data)):
        return data
    return b""


def is_pdf_response(resp: RawResponse) -> bool:
    """A PDF: the fetcher kept its bytes, or it is declared as one (a fetcher that keeps no
    bytes, such as a test double, still gets the PDF treatment)."""
    return bool(resp.body) or mime_of(resp.content_type) in PDF_TYPES


# One worker at a time. asyncio primitives belong to one event loop, and tests (and repeated
# asyncio.run calls) use several, so the semaphore is kept per loop.
_SLOTS: weakref.WeakKeyDictionary[asyncio.AbstractEventLoop, asyncio.Semaphore] = (
    weakref.WeakKeyDictionary()
)


def _slot() -> asyncio.Semaphore:
    loop = asyncio.get_running_loop()
    sem = _SLOTS.get(loop)
    if sem is None:
        sem = _SLOTS[loop] = asyncio.Semaphore(1)
    return sem


def _child_env() -> dict[str, str]:
    return {k: v for k in _CHILD_ENV_KEYS if (v := os.environ.get(k))}


def worker_argv(*, cpu_s: int, memory_bytes: int) -> list[str]:
    limits = {
        "memory_bytes": memory_bytes,
        "cpu_s": cpu_s,
        "max_bytes": MAX_PDF_BYTES,
        "max_pages": MAX_PAGES,
        "max_chars": MAX_TEXT_CHARS,
    }
    return [sys.executable, "-I", "-m", WORKER_MODULE, json.dumps(limits)]


async def extract_pdf_text(
    body: bytes,
    *,
    timeout_s: float = WALL_TIMEOUT_S,
    cpu_s: int = CPU_LIMIT_S,
    memory_bytes: int = MEMORY_LIMIT_BYTES,
) -> PdfText:
    """The text of a PDF, read by the sandboxed worker. Never raises for a bad document."""
    if not body:
        return PdfText("pdf_error", detail="no PDF bytes")
    if len(body) > MAX_PDF_BYTES:
        return PdfText("pdf_limit", detail=f"body of {len(body)} bytes > {MAX_PDF_BYTES}")
    if not sys.executable:
        return PdfText("pdf_error", detail="no Python executable to run the worker")
    argv = worker_argv(cpu_s=cpu_s, memory_bytes=memory_bytes)
    async with _slot():
        return await _run_worker(argv, body, timeout_s=timeout_s, cpu_s=cpu_s)


async def _run_worker(argv: list[str], body: bytes, *, timeout_s: float, cpu_s: int) -> PdfText:
    try:
        proc = await asyncio.create_subprocess_exec(
            *argv,
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            env=_child_env(),
        )
    except OSError as exc:
        return PdfText("pdf_error", detail=f"worker did not start ({type(exc).__name__})")
    try:
        out, err = await asyncio.wait_for(proc.communicate(body), timeout_s)
    except TimeoutError:
        _kill(proc)
        await proc.wait()
        return PdfText("pdf_error", detail=f"worker killed after {timeout_s:g} s")
    except BaseException:  # cancelled: never leave the child running
        _kill(proc)
        raise
    return parse_worker_output(out, err, proc.returncode, cpu_s=cpu_s)


def _kill(proc: asyncio.subprocess.Process) -> None:
    if proc.returncode is None:
        with contextlib.suppress(ProcessLookupError):  # it exited meanwhile
            proc.kill()


def _unicode(text: str) -> str:
    """`text` with every lone surrogate replaced by U+FFFD (same length, always encodable)."""
    return _SURROGATE.sub("\ufffd", text)


def _str_list(value: object) -> list[str] | None:
    if not isinstance(value, list) or not all(isinstance(v, str) for v in value):
        return None
    return [_unicode(v) for v in value]


def parse_worker_output(out: bytes, err: bytes, code: int | None, *, cpu_s: int) -> PdfText:
    """The worker's answer, checked again (it is parsed from a process that read hostile input)."""
    if code is not None and code < 0:
        if code == -getattr(signal, "SIGXCPU", -1):
            return PdfText("pdf_limit", detail=f"CPU time limit ({cpu_s} s)")
        try:
            name = signal.Signals(-code).name
        except ValueError:
            name = str(-code)
        return PdfText("pdf_error", detail=f"worker killed by {name}")
    try:
        data = json.loads(out)
    except ValueError:
        data = None
    if code != 0 or not isinstance(data, dict):
        tail = err.decode("utf-8", "replace").strip().splitlines()[-1:] or ["no output"]
        return PdfText("pdf_error", detail=f"worker exit {code}: {tail[0]}"[:DETAIL_CHARS])
    status = data.get("status")
    detail = _unicode(str(data.get("detail") or ""))[:DETAIL_CHARS]
    pages = data.get("pages", 0)
    pages = pages if isinstance(pages, int) and not isinstance(pages, bool) else 0
    if status not in PDF_STATUSES:
        return PdfText("pdf_error", detail="bad worker output")
    if status != "ok":
        return PdfText(status, pages=pages, detail=detail)  # type: ignore[arg-type]
    lines, stream = _str_list(data.get("lines")), _str_list(data.get("stream_lines"))
    if not lines or stream is None:
        return PdfText("pdf_error", pages=pages, detail="bad worker output")
    if max(sum(map(len, lines)), sum(map(len, stream))) > MAX_TEXT_CHARS:
        return PdfText("pdf_limit", pages=pages, detail=f"text > {MAX_TEXT_CHARS} characters")
    return PdfText("ok", pages=pages, lines=tuple(lines), stream_lines=tuple(stream), detail=detail)

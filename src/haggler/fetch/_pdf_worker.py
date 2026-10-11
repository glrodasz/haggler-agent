"""PDF text extraction worker. Run only by haggler.fetch.pdf, as
`python -I -m haggler.fetch._pdf_worker '<limits JSON>'`: the PDF bytes on stdin, one JSON object
on stdout ({"status", "pages", "lines", "stream_lines", "detail"}).

It parses untrusted documents with pypdf, so it limits itself FIRST, before it reads stdin or
imports pypdf: RLIMIT_AS (memory), RLIMIT_CPU (soft limit -> SIGXCPU, reported as pdf_limit; the
hard limit a few seconds later kills it) and RLIMIT_CORE 0 (a crash leaves no core file).
WHY here and not in a preexec_fn: preexec_fn runs between fork and exec in the parent's copy, and
the research job's process has threads (ADK, gRPC, google-auth), where Python documents it as
unsafe (a lock held by another thread at fork time deadlocks the child); it also forces the slow
fork path. Limits set by the worker's own first statements cover everything pypdf does. The
parent adds the wall-clock kill (30 s) and runs one worker at a time.

Extraction, page by page, in both pypdf modes:
- "layout" positions text by its coordinates, so a table row ("Telia Mobil 20 GB ... 299 kr")
  stays on one line: these are `lines`, the page's primary text (amount lines and anchors);
- "plain" follows the content stream, which keeps the sentences of multi-column prose together:
  these are `stream_lines`, the fallback a quote is also searched in (like text nodes for HTML).
Each mode falls back to the other on a page where it yields no lines (or raises), so a page
counts as long as either mode reads it. Line breaks after a hyphen between two letters are joined
("Abonne-\\nmang" -> "Abonnemang") when the next letter is lower-case (a capital after the break is
more likely a real compound, "Telia-\\nMobil"); a soft hyphen at a break is always joined.
Whitespace is collapsed per line and empty lines are dropped. Lone UTF-16 surrogates (pypdf
decodes ToUnicode CMaps with "surrogatepass", so a broken CMap yields them) become U+FFFD; a
valid pair becomes its one code point (the parent checks the output again).

Statuses: ok | pdf_encrypted | pdf_no_text | pdf_limit | pdf_error. An encrypted PDF is opened
only the way every viewer opens it, with the empty user password, and only when its publisher
allows text extraction (the EXTRACT permission); otherwise it is pdf_encrypted. Never a password
guess, never a permission bypass.
"""

from __future__ import annotations

import io
import json
import logging
import re
import resource
import signal
import sys
import warnings
from dataclasses import dataclass
from typing import Any

# Seconds between the soft CPU limit (SIGXCPU, reported) and the hard one (SIGKILL).
CPU_GRACE_S = 5
DETAIL_CHARS = 200

# A line break after a hyphen (or soft hyphen) between letters; the next letter is checked in
# _join_hyphen (Python's re has no \p{Ll}).
_HYPHEN_BREAK = re.compile(r"(?<=[^\W\d_])([-­])[ \t ]*\n[ \t ]*(?=([^\W\d_]))")


class CpuLimitReached(BaseException):
    """SIGXCPU arrived. A BaseException so pypdf's `except Exception` blocks cannot swallow it."""


@dataclass(frozen=True)
class Limits:
    memory_bytes: int
    cpu_s: int
    max_bytes: int
    max_pages: int
    max_chars: int


def _join_hyphen(m: re.Match[str]) -> str:
    return "" if m.group(1) == "­" or m.group(2).islower() else m.group(0)


def clean_lines(text: str) -> list[str]:
    """Lines of one page's extracted text: hyphenated breaks joined, whitespace collapsed per
    line, empty lines dropped, lone surrogates replaced."""
    text = text.encode("utf-16", "surrogatepass").decode("utf-16", "replace")
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    text = _HYPHEN_BREAK.sub(_join_hyphen, text)
    return [line for line in (" ".join(raw.split()) for raw in text.split("\n")) if line]


def _parse_limits(argv: list[str]) -> Limits:
    raw = json.loads(argv[0])
    return Limits(
        memory_bytes=int(raw["memory_bytes"]),
        cpu_s=int(raw["cpu_s"]),
        max_bytes=int(raw["max_bytes"]),
        max_pages=int(raw["max_pages"]),
        max_chars=int(raw["max_chars"]),
    )


def _set_limit(which: int, soft: int, hard: int) -> None:
    """Lower a limit (never above an existing hard limit, which an unprivileged process cannot
    raise)."""
    _, current = resource.getrlimit(which)
    if current != resource.RLIM_INFINITY:
        soft, hard = min(soft, current), min(hard, current)
    resource.setrlimit(which, (soft, hard))


def apply_limits(limits: Limits) -> None:
    _set_limit(resource.RLIMIT_CORE, 0, 0)
    _set_limit(resource.RLIMIT_AS, limits.memory_bytes, limits.memory_bytes)
    _set_limit(resource.RLIMIT_CPU, limits.cpu_s, limits.cpu_s + CPU_GRACE_S)


def _on_sigxcpu(signum: int, frame: Any) -> None:
    raise CpuLimitReached


def _result(status: str, detail: str = "", **kw: Any) -> dict[str, Any]:
    return {"status": status, "detail": detail[:DETAIL_CHARS], **kw}


def _open_encrypted(reader: Any) -> str | None:
    """None when the document opens with the empty user password and allows text extraction;
    otherwise why it stays closed."""
    from pypdf.constants import UserAccessPermissions
    from pypdf.errors import DependencyError

    try:
        opened = reader.decrypt("")
    except DependencyError:
        return "encryption needs a crypto library"
    except (MemoryError, RecursionError):
        raise
    except Exception as exc:
        return f"cannot decrypt ({type(exc).__name__})"
    if not opened:
        return "a password is required to open it"
    permissions = reader.user_access_permissions
    if permissions is not None and not permissions & UserAccessPermissions.EXTRACT:
        return "the publisher does not allow text extraction"
    return None


def _mode_lines(page: Any, mode: str, errors: list[str]) -> list[str] | None:
    """The page's lines in one extraction mode; None when that mode failed on it."""
    from pypdf.errors import LimitReachedError

    try:
        text = page.extract_text(extraction_mode=mode) or ""
    except (MemoryError, LimitReachedError):
        raise
    except Exception as exc:  # one mode failing on one page is not the document failing
        errors.append(f"{mode}: {type(exc).__name__}")
        return None
    return clean_lines(text)


def extract(data: bytes, limits: Limits) -> dict[str, Any]:
    from pypdf import PdfReader

    reader = PdfReader(io.BytesIO(data), strict=False)
    if reader.is_encrypted and (problem := _open_encrypted(reader)):
        return _result("pdf_encrypted", problem)
    pages = len(reader.pages)
    if pages > limits.max_pages:
        return _result("pdf_limit", f"{pages} pages > {limits.max_pages}", pages=pages)
    lines: list[str] = []
    stream_lines: list[str] = []
    errors: list[str] = []
    unreadable = 0  # pages on which both modes failed
    for page in reader.pages:
        layout = _mode_lines(page, "layout", errors)
        stream = _mode_lines(page, "plain", errors)
        if layout is None and stream is None:
            unreadable += 1
        lines += layout or stream or []
        stream_lines += stream or layout or []
        if max(sum(map(len, lines)), sum(map(len, stream_lines))) > limits.max_chars:
            return _result("pdf_limit", f"text > {limits.max_chars} characters", pages=pages)
    if not lines:
        if pages and unreadable == pages:
            detail = "no page could be read: " + ", ".join(dict.fromkeys(errors))
            return _result("pdf_error", detail, pages=pages)
        return _result("pdf_no_text", "no text layer (scanned or image-only?)", pages=pages)
    detail = f"{len(errors)} extraction error(s) on some pages" if errors else ""
    return _result("ok", detail, pages=pages, lines=lines, stream_lines=stream_lines)


def run(data: bytes, limits: Limits) -> dict[str, Any]:
    """extract(), with every failure turned into a status (never a traceback)."""
    if len(data) > limits.max_bytes:
        return _result("pdf_limit", f"body > {limits.max_bytes} bytes")
    if not data:
        return _result("pdf_error", "empty body")
    # The except clauses only pick the status: the result is built after them, once the
    # traceback (and the parser objects its frames hold) has been released.
    try:
        from pypdf.errors import LimitReachedError
    except MemoryError:
        return _result("pdf_limit", "memory limit while loading pypdf")
    try:
        return extract(data, limits)
    except CpuLimitReached:
        outcome = ("pdf_limit", f"CPU time limit ({limits.cpu_s} s)")
    except MemoryError:
        outcome = ("pdf_limit", f"memory limit ({limits.memory_bytes // (1024 * 1024)} MiB)")
    except LimitReachedError as exc:
        outcome = ("pdf_limit", f"pypdf limit: {exc}")
    except RecursionError:
        outcome = ("pdf_error", "objects nested too deep")
    except Exception as exc:
        outcome = ("pdf_error", f"unreadable PDF ({type(exc).__name__})")
    return _result(*outcome)


def _emit(result: dict[str, Any]) -> None:
    sys.stdout.buffer.write(json.dumps(result, ensure_ascii=True).encode("ascii"))
    sys.stdout.buffer.flush()


def main(argv: list[str]) -> int:
    try:
        limits = _parse_limits(argv)
        apply_limits(limits)
    except (IndexError, KeyError, TypeError, ValueError, OSError) as exc:
        # No sandbox, no parsing.
        _emit(_result("pdf_error", f"worker limits not applied ({type(exc).__name__})"))
        return 0
    signal.signal(signal.SIGXCPU, _on_sigxcpu)
    logging.disable(logging.CRITICAL)
    warnings.simplefilter("ignore")
    try:
        data = sys.stdin.buffer.read(limits.max_bytes + 1)
        result = run(data, limits)
    except CpuLimitReached:  # while reading stdin or importing pypdf
        result = _result("pdf_limit", f"CPU time limit ({limits.cpu_s} s)")
    # Writing the answer must not be cut short by a late SIGXCPU (the hard limit still applies).
    signal.signal(signal.SIGXCPU, signal.SIG_IGN)
    _emit(result)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))

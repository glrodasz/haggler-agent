"""PDFs built as bytes inside the tests (no binary fixtures): a minimal hand-written document with
a text content stream and a correct xref table, and pypdf-written blank, encrypted and many-page
ones."""

from __future__ import annotations

import io
import zlib

from pypdf import PdfReader, PdfWriter
from pypdf.constants import UserAccessPermissions

LEADING = 14


def _pdf_string(text: str) -> str:
    """A PDF literal string in WinAnsiEncoding (cp1252): "€", "å"... as octal escapes."""
    out = []
    for b in text.encode("cp1252"):
        ch = chr(b)
        if ch in "()\\":
            out.append("\\" + ch)
        elif 32 <= b < 127:
            out.append(ch)
        else:
            out.append(f"\\{b:03o}")
    return "(" + "".join(out) + ")"


def text_stream(lines: list[str]) -> bytes:
    """A content stream that writes `lines` top to bottom in Helvetica 11."""
    ops = ["BT", "/F1 11 Tf", f"{LEADING} TL", "72 760 Td"]
    ops += [f"{_pdf_string(line)} Tj T*" for line in lines]
    ops.append("ET")
    return "\n".join(ops).encode("ascii")


def _stream_object(data: bytes, *, flate: bool) -> bytes:
    if flate:
        data = zlib.compress(data)
        head = b"<< /Length %d /Filter /FlateDecode >>" % len(data)
    else:
        head = b"<< /Length %d >>" % len(data)
    return head + b"\nstream\n" + data + b"\nendstream"


def _to_unicode_cmap(mapping: dict[str, str]) -> bytes:
    """A ToUnicode CMap stream object: one-byte character codes (hex) -> UTF-16BE (hex)."""
    chars = "\n".join(f"<{code}> <{target}>" for code, target in mapping.items())
    cmap = (
        "/CIDInit /ProcSet findresource begin\n12 dict begin\nbegincmap\n"
        "/CMapName /Test def\n/CMapType 2 def\n"
        "1 begincodespacerange\n<00> <FF>\nendcodespacerange\n"
        f"{len(mapping)} beginbfchar\n{chars}\nendbfchar\n"
        "endcmap\nCMapName currentdict /CMap defineresource pop\nend\nend"
    ).encode("ascii")
    return _stream_object(cmap, flate=False)


def build_pdf(
    pages: list[list[str]] | None = None,
    *,
    streams: list[bytes] | None = None,
    flate: bool = False,
    raw_streams: list[bytes] | None = None,
    to_unicode: dict[str, str] | None = None,
) -> bytes:
    """A PDF with one page per entry: `pages` (lines of text), `streams` (content streams, Flate
    compressed when `flate`) or `raw_streams` (complete stream objects, dictionary included).
    `to_unicode` gives the font a ToUnicode CMap ({"41": "D800"}: code 0x41 -> U+D800)."""
    if raw_streams is None:
        contents = streams if streams is not None else [text_stream(p) for p in pages or [[]]]
        raw_streams = [_stream_object(s, flate=flate) for s in contents]
    n = len(raw_streams)
    kids = " ".join(f"{4 + 2 * i} 0 R" for i in range(n))
    font = "<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica /Encoding /WinAnsiEncoding"
    font += f" /ToUnicode {4 + 2 * n} 0 R >>" if to_unicode else " >>"
    objects = [
        b"<< /Type /Catalog /Pages 2 0 R >>",
        f"<< /Type /Pages /Kids [{kids}] /Count {n} >>".encode(),
        font.encode(),
    ]
    for i, stream in enumerate(raw_streams):
        objects.append(
            (
                "<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] "
                f"/Resources << /Font << /F1 3 0 R >> >> /Contents {5 + 2 * i} 0 R >>"
            ).encode()
        )
        objects.append(stream)
    if to_unicode:
        objects.append(_to_unicode_cmap(to_unicode))
    out = bytearray(b"%PDF-1.4\n%\xe2\xe3\xcf\xd3\n")
    offsets = []
    for number, body in enumerate(objects, 1):
        offsets.append(len(out))
        out += b"%d 0 obj\n" % number + body + b"\nendobj\n"
    xref = len(out)
    out += b"xref\n0 %d\n0000000000 65535 f \n" % (len(objects) + 1)
    out += b"".join(b"%010d 00000 n \n" % off for off in offsets)
    out += b"trailer\n<< /Size %d /Root 1 0 R >>\nstartxref\n%d\n%%%%EOF\n" % (
        len(objects) + 1,
        xref,
    )
    return bytes(out)


def _written(writer: PdfWriter) -> bytes:
    buf = io.BytesIO()
    writer.write(buf)
    return buf.getvalue()


def blank_pdf(pages: int = 1) -> bytes:
    writer = PdfWriter()
    for _ in range(pages):
        writer.add_blank_page(width=612, height=792)
    return _written(writer)


def encrypted_pdf(
    lines: list[str],
    *,
    user_password: str = "secret",
    permissions: UserAccessPermissions | None = None,
) -> bytes:
    writer = PdfWriter(clone_from=PdfReader(io.BytesIO(build_pdf([lines]))))
    kw = {} if permissions is None else {"permissions_flag": permissions}
    writer.encrypt(user_password, "owner-password", algorithm="AES-128", **kw)
    return _written(writer)


def flate_bomb_pdf(decompressed_mib: int) -> bytes:
    """One page whose content stream inflates to `decompressed_mib` MiB of zeros."""
    comp = zlib.compressobj(9)
    chunk = bytes(1024 * 1024)
    data = b"".join(comp.compress(chunk) for _ in range(decompressed_mib)) + comp.flush()
    stream = b"<< /Length %d /Filter /FlateDecode >>\nstream\n" % len(data)
    return build_pdf(raw_streams=[stream + data + b"\nendstream"])


# Two columns, plan names at x=72 and prices at x=400, each column its own text object: layout
# mode keeps every table row on one line, plain mode follows the content stream (a column at a
# time).
TWO_COLUMNS = (
    b"BT /F1 11 Tf 72 700 Td (Telia Mobil 20 GB) Tj 0 -14 Td (Telia Mobil 50 GB) Tj ET\n"
    b"BT /F1 11 Tf 400 700 Td (299 kr/m\\345n) Tj 0 -14 Td (349 kr/m\\345n) Tj ET"
)
TWO_COLUMN_ROWS = ("Telia Mobil 20 GB 299 kr/mån", "Telia Mobil 50 GB 349 kr/mån")
TWO_COLUMN_STREAM = ("Telia Mobil 20 GB", "Telia Mobil 50 GB", "299 kr/mån", "349 kr/mån")


def two_column_pdf() -> bytes:
    return build_pdf(streams=[TWO_COLUMNS])

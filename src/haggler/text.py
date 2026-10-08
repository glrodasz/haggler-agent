"""HTML -> text helpers. Unlike ADK's load_web_page, short lines ("129 kr") are kept."""

from __future__ import annotations

import hashlib
import json
import re
import unicodedata
from typing import Any

from selectolax.lexbor import LexborHTMLParser as HTMLParser

from haggler.money import find_prices

_DROP_TAGS = ("script", "style", "noscript", "svg", "template", "iframe", "head")
_WS = re.compile(r"[ \t    ​]+")


def html_to_lines(html: str) -> list[str]:
    tree = HTMLParser(html)
    for tag in _DROP_TAGS:
        for node in tree.css(tag):
            node.decompose()
    root = tree.body or tree.root
    if root is None:
        return []
    raw = root.text(separator="\n", strip=False)
    lines = []
    for line in raw.splitlines():
        line = _WS.sub(" ", line).strip()
        if line:
            lines.append(line)
    return lines


def html_to_text(html: str) -> str:
    return "\n".join(html_to_lines(html))


def page_title(html: str) -> str:
    node = HTMLParser(html).css_first("title")
    return node.text(strip=True) if node else ""


def normalize_for_match(text: str) -> str:
    """Canonical form used to check that LLM-quoted text really appears on the page."""
    text = unicodedata.normalize("NFKC", text).casefold()
    text = re.sub(r"[‐-―−]", "-", text)
    text = text.replace("’", "'")
    return _WS.sub(" ", re.sub(r"\s+", " ", text)).strip()


def contains_normalized(haystack: str, needle: str) -> bool:
    return normalize_for_match(needle) in normalize_for_match(haystack)


def text_hash(text: str) -> str:
    return hashlib.sha256(normalize_for_match(text).encode()).hexdigest()[:16]


def bytes_hash(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()[:16]


def price_windows(
    lines: list[str], expected: list[str], *, context: int = 2, max_chars: int = 8000
) -> str:
    """Lines around anything that looks like a price, merged, for compact LLM input."""
    keep: set[int] = set()
    for i, line in enumerate(lines):
        if find_prices(line, expected):
            keep.update(range(max(0, i - context), min(len(lines), i + context + 1)))
    if not keep:
        out = "\n".join(lines)
        return out[:max_chars]
    chunks: list[str] = []
    prev = -2
    for i in sorted(keep):
        if i != prev + 1:
            chunks.append("…")
        chunks.append(lines[i])
        prev = i
    out = "\n".join(chunks)
    return out[:max_chars]


def json_scripts(html: str) -> list[tuple[str, Any]]:
    """(identifier, parsed JSON) for ld+json, __NEXT_DATA__ and other application/json scripts."""
    tree = HTMLParser(html)
    found: list[tuple[str, Any]] = []
    for node in tree.css("script"):
        typ = (node.attributes.get("type") or "").lower()
        ident = node.attributes.get("id") or typ or "script"
        if typ not in {"application/ld+json", "application/json"} and ident != "__NEXT_DATA__":
            continue
        body = node.text(strip=True)
        if not body:
            continue
        try:
            found.append((ident, json.loads(body)))
        except json.JSONDecodeError:
            continue
    return found


def walk_json(obj: Any, path: str = ""):
    """Yield (path, key, value) for every dict entry in a nested JSON structure."""
    if isinstance(obj, dict):
        for k, v in obj.items():
            p = f"{path}.{k}" if path else str(k)
            yield p, k, v
            yield from walk_json(v, p)
    elif isinstance(obj, list):
        for i, v in enumerate(obj):
            yield from walk_json(v, f"{path}[{i}]")

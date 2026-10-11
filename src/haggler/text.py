"""HTML -> text helpers. Unlike ADK's load_web_page, short lines ("129 kr") are kept.

`html_to_lines` renders a page the way a browser lays it out: one line per block element, inline
markup joined ("<b>Basic</b>: 129 kr<span>/mån</span>" -> "Basic: 129 kr/mån"), <br> breaks a
line, and short <sup> footnote marks are dropped so "$59.99<sup>4</sup>" never reads "$59.994"
and "Bahamas<sup>2</sup> (USD)" stays one header. Adapters and the pipeline share it, so the
evidence an adapter quotes is text the pipeline (and the LLM) sees too.
"""

from __future__ import annotations

import hashlib
import json
import re
import unicodedata
from collections.abc import Iterable
from dataclasses import dataclass
from typing import Any

from selectolax.lexbor import LexborHTMLParser as HTMLParser
from selectolax.lexbor import LexborNode

from haggler.money import find_prices

_DROP_TAGS = ("script", "style", "noscript", "svg", "template", "iframe", "head")
_WS = re.compile(r"[ \t\u00a0\u202f\u2009\u2007\u200b]+")

# Never rendered: scripts, styles, metadata and comments.
SKIP_TAGS = frozenset(
    {"script", "style", "noscript", "svg", "template", "iframe", "head", "title", "-comment"}
)
# Site chrome whose menus repeat plan and product names (dropped with drop_chrome=True).
CHROME_TAGS = frozenset({"header", "footer", "nav"})
BLOCK_TAGS = frozenset(
    {
        "address", "article", "aside", "blockquote", "body", "br", "button", "caption", "dd",
        "details", "dialog", "div", "dl", "dt", "fieldset", "figcaption", "figure", "footer",
        "form", "h1", "h2", "h3", "h4", "h5", "h6", "header", "hgroup", "hr", "label", "legend",
        "li", "main", "nav", "ol", "option", "p", "pre", "section", "summary", "table", "tbody",
        "td", "tfoot", "th", "thead", "tr", "ul",
    }
)  # fmt: skip
_HEADINGS = {"h1": 1, "h2": 2, "h3": 3, "h4": 4, "h5": 5, "h6": 6}
# Characters a browser does not display: soft hyphen, zero-width space, word joiner, BOM.
_INVISIBLE = dict.fromkeys(map(ord, "­​⁠﻿"))
_SPACE_RUN = re.compile(r"\s+")
# <sup> content that is a footnote or trademark mark ("2", "*", "◊", "³", "a", "™") rather
# than text.
_FOOTNOTE_SUP = re.compile(r"[\w*†‡§◊¶#,™®℠\s]{1,4}")
_CONTEXT_ATTRS = ("id", "class", "data-analytics-section-engagement")


@dataclass(frozen=True)
class TextLine:
    """One rendered line plus where it sits in the page."""

    text: str
    heading: int = 0  # 1-6 when the line starts inside an <hN>, else 0
    context: str = ""  # lower-cased ids/classes of the enclosing elements


def is_footnote_sup(node: LexborNode) -> bool:
    """A short <sup> footnote mark: dropped, so it never glues onto a number or a name."""
    return bool(_FOOTNOTE_SUP.fullmatch((node.text(deep=True) or "").strip() or "*"))


class _Renderer:
    def __init__(self, drop: frozenset[str], context: bool) -> None:
        self.drop = drop
        self.track = context
        self.lines: list[TextLine] = []
        self.buf: list[str] = []
        self.stack: list[tuple[int, str]] = []  # (heading level, ids/classes) of open elements
        self.meta: tuple[int, str] | None = None  # of the first visible text in buf

    def flush(self) -> None:
        text = _SPACE_RUN.sub(" ", "".join(self.buf)).strip()
        self.buf.clear()
        if text:
            heading, ctx = self.meta or (0, "")
            self.lines.append(TextLine(text, heading, ctx))
        self.meta = None

    def text(self, raw: str) -> None:
        text = _SPACE_RUN.sub(" ", raw.translate(_INVISIBLE))
        last = self.buf[-1][-1:] if self.buf else ""
        if not text.strip():
            if last and not last.isspace():
                self.buf.append(" ")
            return
        if self.meta is None:
            level = next((lvl for lvl, _ in reversed(self.stack) if lvl), 0)
            ctx = " ".join(i for _, i in self.stack if i).lower() if self.track else ""
            self.meta = (level, ctx)
        # Adjacent inline elements ("<span>Premium</span><span>Duo</span>") stay separate
        # words, while "129 kr" + "/mån" still reads "129 kr/mån".
        if last.isalnum() and text[:1].isalnum():
            self.buf.append(" ")
        self.buf.append(text)

    def walk(self, node: LexborNode, depth: int = 0) -> None:
        if depth > 400:
            return
        for child in node.iter(include_text=True):
            tag = child.tag
            if tag == "-text":
                self.text(child.text_content or "")
                continue
            if tag is None or tag in SKIP_TAGS or tag in self.drop or child.is_comment_node:
                continue
            if tag == "br":
                self.flush()
                continue
            ident = ""
            if self.track:
                attrs = child.attributes
                ident = " ".join(v for k in _CONTEXT_ATTRS if (v := attrs.get(k)))
            self.stack.append((_HEADINGS.get(tag, 0), ident))
            if tag == "sup":
                if not is_footnote_sup(child):
                    self.buf.append(" ")
                    self.walk(child, depth + 1)
                    self.buf.append(" ")
            elif tag in BLOCK_TAGS:
                self.flush()
                self.walk(child, depth + 1)
                self.flush()
            else:
                self.walk(child, depth + 1)
            self.stack.pop()


def render_lines(
    node: LexborNode | None, *, drop: Iterable[str] = (), context: bool = False
) -> list[TextLine]:
    """Rendered lines of `node` with their heading level (and, with context=True, the ids and
    classes of the enclosing elements, for adapters that skip sections by class)."""
    if node is None:
        return []
    r = _Renderer(frozenset(drop), context)
    r.walk(node)
    r.flush()
    return r.lines


def node_lines(node: LexborNode | None, *, drop: Iterable[str] = ()) -> list[str]:
    """Text of `node` as a browser lays it out: one line per block, inline text joined."""
    return [ln.text for ln in render_lines(node, drop=drop)]


def html_to_lines(html: str, *, drop_chrome: bool = False) -> list[str]:
    """Rendered lines of the page body (see the module docstring).

    drop_chrome=True leaves out <header>, <nav> and <footer> (menus list plan and product
    names) unless that leaves nothing.
    """
    tree = HTMLParser(html)
    root = tree.body or tree.root
    if root is None:
        return []
    if drop_chrome:
        lines = node_lines(root, drop=CHROME_TAGS)
        if lines:
            return lines
    return node_lines(root)


def text_node_lines(html: str) -> list[str]:
    """One line per text node (no block layout): for listings where every text node is a field,
    such as App Store "name / price" rows whose markup we cannot see."""
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

"""The public description of the sources (methodology page, README) follows the catalog."""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from haggler.catalog import load_catalog

ROOT = Path(__file__).resolve().parents[2]
ES = (ROOT / "web" / "src" / "i18n" / "es.ts").read_text(encoding="utf-8")
README = (ROOT / "README.md").read_text(encoding="utf-8")
APPSTORE = "appstore_iap"


def _collect_text(title: str) -> str:
    """The `text` of the methodology "collect" item with this title (web/src/i18n/es.ts)."""
    m = re.search(r'title: "' + re.escape(title) + r'",\s*text: "((?:[^"\\]|\\.)*)"', ES)
    assert m, title
    return m.group(1)


def _services(only_appstore: bool) -> list[str]:
    out = []
    for s in load_catalog().services:
        adapters = {src.adapter for src in s.sources}
        if adapters and (adapters == {APPSTORE}) == only_appstore:
            out.append(s.name)
    return out


WEB_TEXT = _collect_text("Fuentes oficiales primero")
APPSTORE_TEXT = _collect_text("App Store como respaldo")
README_ROW = next(line for line in README.splitlines() if line.startswith("| Sources |"))
README_WEB, _, README_APPSTORE = README_ROW.partition(") and, for services")


def test_appstore_only_services_include_disney_plus() -> None:
    # The premise of the copy below; Disney+ blocks crawlers in robots.txt.
    assert "Disney+" in _services(only_appstore=True)


@pytest.mark.parametrize("name", _services(only_appstore=True))
def test_appstore_sourced_services_are_described_as_such(name: str) -> None:
    assert name in APPSTORE_TEXT
    assert name not in WEB_TEXT
    assert name in README_APPSTORE
    assert name not in README_WEB


def test_disney_plus_reason_and_no_disneyplus_web_source() -> None:
    assert "robots.txt" in APPSTORE_TEXT
    assert "robots.txt" in README_APPSTORE
    assert "disneyplus.com" not in README_ROW

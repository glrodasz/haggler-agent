"""The public description of the sources (methodology page, README) follows the catalog."""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from haggler.catalog import load_catalog

ROOT = Path(__file__).resolve().parents[2]
I18N = ROOT / "web" / "src" / "i18n"
# (dictionary, title of the official-sources item, title of the App Store item), per locale.
DICTS = {
    "en": ("en.ts", "Official sources first", "App Store as a fallback"),
    "es": ("es.ts", "Fuentes oficiales primero", "App Store como respaldo"),
    "pt": ("pt.ts", "Fontes oficiais primeiro", "App Store como alternativa"),
}
README = (ROOT / "README.md").read_text(encoding="utf-8")
APPSTORE = "appstore_iap"


def _collect_text(file: str, title: str) -> str:
    """The `text` of the methodology "collect" item with this title (web/src/i18n/<file>)."""
    source = (I18N / file).read_text(encoding="utf-8")
    m = re.search(r'title: "' + re.escape(title) + r'",\s*text: "((?:[^"\\]|\\.)*)"', source)
    assert m, title
    return m.group(1)


def _services(only_appstore: bool) -> list[str]:
    out = []
    for s in load_catalog().services:
        adapters = {src.adapter for src in s.sources}
        if adapters and (adapters == {APPSTORE}) == only_appstore:
            out.append(s.name)
    return out


TEXTS = {
    locale: (_collect_text(file, web), _collect_text(file, appstore))
    for locale, (file, web, appstore) in DICTS.items()
}
README_ROW = next(line for line in README.splitlines() if line.startswith("| Sources |"))
README_WEB, _, README_APPSTORE = README_ROW.partition(") and, for services")


def test_appstore_only_services_include_disney_plus() -> None:
    # The premise of the copy below; Disney+ blocks crawlers in robots.txt.
    assert "Disney+" in _services(only_appstore=True)


@pytest.mark.parametrize("locale", DICTS)
@pytest.mark.parametrize("name", _services(only_appstore=True))
def test_appstore_sourced_services_are_described_as_such(name: str, locale: str) -> None:
    web_text, appstore_text = TEXTS[locale]
    assert name in appstore_text
    assert name not in web_text
    assert name in README_APPSTORE
    assert name not in README_WEB


@pytest.mark.parametrize("locale", DICTS)
def test_disney_plus_reason_and_no_disneyplus_web_source(locale: str) -> None:
    assert "robots.txt" in TEXTS[locale][1]
    assert "robots.txt" in README_APPSTORE
    assert "disneyplus.com" not in README_ROW

"""Shared fixtures: catalog, a fixture-backed Fetcher and a scripted fake LLM for ADK agents."""

from __future__ import annotations

import json
from collections.abc import AsyncGenerator, Callable
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest
from google.adk.models.base_llm import BaseLlm
from google.adk.models.llm_request import LlmRequest
from google.adk.models.llm_response import LlmResponse
from google.genai import types

from haggler.catalog import load_bundles, load_catalog, load_feasibility
from haggler.fetch import RawResponse
from haggler.fetch.errors import FetchError
from haggler.models.curated import BundlesFile

FIXTURES = Path(__file__).parent / "sources" / "fixtures"
# A frozen data/curated/bundles.yaml (its header says why): the behaviour tests read it, so a
# correct edit of the real file does not change what they test.
FROZEN_CURATED = Path(__file__).parent / "fixtures" / "curated_pre_h10"


def frozen_bundles() -> BundlesFile:
    return load_bundles(FROZEN_CURATED)


@pytest.fixture(scope="session")
def catalog():
    return load_catalog()


@pytest.fixture(scope="session")
def feasibility():
    return load_feasibility()


@pytest.fixture(scope="session")
def bundles():
    return load_bundles()


def fixture_text(*parts: str) -> str:
    return FIXTURES.joinpath(*parts).read_text(encoding="utf-8")


class FixtureFetcher:
    """Serves canned responses by exact URL. Values: text, RawResponse, or an exception."""

    def __init__(self, routes: dict[str, Any] | None = None) -> None:
        self.routes: dict[str, Any] = dict(routes or {})
        self.calls: list[str] = []

    async def fetch(
        self, url: str, *, accept: str = "text/html", egress: str = "default"
    ) -> RawResponse:
        self.calls.append(url)
        value = self.routes.get(url)
        if value is None:
            raise FetchError(f"no fixture for {url}", status=404, url=url)
        if isinstance(value, BaseException):
            raise value
        if isinstance(value, RawResponse):
            return value
        ctype = "application/json" if accept == "application/json" else "text/html"
        return RawResponse(
            url=url,
            final_url=url,
            status_code=200,
            text=value,
            content_type=ctype,
            fetched_at=datetime.now(UTC),
        )

    async def aclose(self) -> None:
        return None


@pytest.fixture
def fixture_fetcher() -> FixtureFetcher:
    return FixtureFetcher()


def _request_text(req: LlmRequest) -> str:
    return "".join(p.text or "" for c in req.contents for p in (c.parts or []) if p.text)


class ScriptedLlm(BaseLlm):
    """Fake model: `responder(request_text) -> dict | str` builds each reply."""

    model: str = "scripted-fake"
    responder: Callable[[str], Any] | None = None
    requests: list[str] = []  # noqa: RUF012 - pydantic copies field defaults

    async def generate_content_async(
        self, llm_request: LlmRequest, stream: bool = False
    ) -> AsyncGenerator[LlmResponse, None]:
        text = _request_text(llm_request)
        self.requests.append(text)
        reply = (
            self.responder(text)
            if self.responder
            else {"page_status": "no_prices", "items": [], "notes": ""}
        )
        if not isinstance(reply, str):
            reply = json.dumps(reply)
        yield LlmResponse(content=types.ModelContent(parts=[types.Part(text=reply)]))


@pytest.fixture
def now() -> datetime:
    return datetime(2026, 10, 8, 4, 17, tzinfo=UTC)

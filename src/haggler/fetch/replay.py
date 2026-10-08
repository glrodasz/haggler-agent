"""ReplayFetcher: serves saved pages for offline end-to-end runs (`haggler daily --replay DIR`).

DIR/index.json maps request URLs to saved responses:

    {
      "https://help.netflix.com/en/node/24926/se": {
        "file": "netflix_help/se.html",      # relative to DIR; ".gz" files are decompressed
        "status": 200,                        # optional, default 200
        "final_url": "https://...",           # optional, default = the URL
        "content_type": "text/html"           # optional, default from the Accept value
      },
      "https://example.com/flaky": {"error": "transient"}   # transient|blocked|robots|fetch
    }

Keys starting with "_" are comments. A URL missing from the index raises FetchError(404), like a
removed page. Non-2xx statuses raise the same exceptions HttpFetcher would (classify_status), so a
replay exercises the pipeline's error handling exactly like production.
"""

from __future__ import annotations

import gzip
import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import httpx

from haggler.fetch import RawResponse
from haggler.fetch.client import classify_status
from haggler.fetch.errors import (
    BlockedError,
    FetchError,
    RobotsDisallowedError,
    TransientFetchError,
)

_ERRORS: dict[str, type[FetchError]] = {
    "transient": TransientFetchError,
    "blocked": BlockedError,
    "robots": RobotsDisallowedError,
    "fetch": FetchError,
}


class ReplayFetcher:
    def __init__(self, root: Path | str) -> None:
        self.root = Path(root)
        index_path = self.root / "index.json"
        data = json.loads(index_path.read_text(encoding="utf-8"))
        if not isinstance(data, dict):
            raise ValueError(f"{index_path}: expected a JSON object of url -> entry")
        self.index: dict[str, dict[str, Any]] = {}
        for url, entry in data.items():
            if url.startswith("_"):
                continue
            if not isinstance(entry, dict) or not ("file" in entry or "error" in entry):
                raise ValueError(f"{index_path}: entry for {url} needs 'file' or 'error'")
            self.index[url] = entry
        self.calls: list[str] = []

    def covers(self, url_or_task: Any) -> bool:
        """True when the index has the URL (accepts a URL or anything with a `.url`)."""
        url = url_or_task if isinstance(url_or_task, str) else getattr(url_or_task, "url", "")
        return url in self.index

    def _read(self, rel: str) -> str:
        path = (self.root / rel).resolve()
        if self.root.resolve() not in path.parents:
            raise FetchError(f"replay file escapes {self.root}: {rel}")
        data = path.read_bytes()
        if path.suffix == ".gz":
            data = gzip.decompress(data)
        return data.decode("utf-8")

    async def fetch(
        self, url: str, *, accept: str = "text/html", egress: str = "default"
    ) -> RawResponse:
        self.calls.append(url)
        entry = self.index.get(url)
        if entry is None:
            raise FetchError(f"HTTP 404 not in replay index: {url}", status=404, url=url)
        if error := entry.get("error"):
            cls = _ERRORS.get(str(error), FetchError)
            raise cls(f"replayed {error} error", status=entry.get("status"), url=url)
        try:
            text = self._read(str(entry["file"]))
        except OSError as exc:
            raise FetchError(f"replay file unreadable: {exc}", url=url) from exc
        status = int(entry.get("status", 200))
        headers = {str(k): str(v) for k, v in (entry.get("headers") or {}).items()}
        classify_status(url, status, httpx.Headers(headers), text)
        default_type = "application/json" if accept == "application/json" else "text/html"
        return RawResponse(
            url=url,
            final_url=str(entry.get("final_url") or url),
            status_code=status,
            text=text,
            content_type=str(entry.get("content_type") or default_type),
            fetched_at=datetime.now(UTC),
            headers=headers,
        )

    async def aclose(self) -> None:
        return None

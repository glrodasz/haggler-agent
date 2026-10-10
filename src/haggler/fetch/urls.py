"""URL paths as the server sees them.

httpx removes the dot segments of a URL's path before sending it ("/se/../co/" goes out as
"/co/"), and servers decode percent-encoded dots ("%2e%2e") the same way. robots.txt and the
official-domain checks compare paths, so they must look at the resolved one: else a URL such as
https://host/allowed/../disallowed/ passes the robots check (or an /se/ prefix) while the page
fetched is another one.
"""

from __future__ import annotations

import re
from urllib.parse import urlsplit, urlunsplit

# "." or "..", literal or percent-encoded (RFC 3986 §2.3: %2E is the unreserved ".").
_DOT_SEGMENT = re.compile(r"(?:\.|%2e){1,2}", re.IGNORECASE)


def _dots(segment: str) -> str | None:
    if not _DOT_SEGMENT.fullmatch(segment):
        return None
    return re.sub("%2e", ".", segment, flags=re.IGNORECASE)


def resolve_dot_segments(url: str) -> str:
    """`url` with the dot segments of its path resolved (RFC 3986 §5.2.4 remove_dot_segments,
    percent-encoded dots included): the path the request really asks for. A URL without dot
    segments is returned unchanged (as a string, byte for byte)."""
    parts = urlsplit(url)
    segments = parts.path.split("/")
    if not any(_dots(s) for s in segments):
        return url
    out: list[str] = []
    last = len(segments) - 1
    for i, segment in enumerate(segments):
        dots = _dots(segment)
        if dots is None:
            out.append(segment)
            continue
        if dots == ".." and len(out) > 1:
            out.pop()
        if i == last:
            out.append("")  # "/a/b/.." is the directory "/a/"
    path = "/".join(out)
    if parts.path.startswith("/") and not path.startswith("/"):
        path = "/" + path
    return urlunsplit((parts.scheme, parts.netloc, path, parts.query, parts.fragment))

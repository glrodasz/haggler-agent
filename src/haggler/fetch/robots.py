"""robots.txt rules (RFC 9309), fetched once per origin and cached for the process.

WHY a cache with explicit outcomes: RFC 9309 distinguishes "unavailable" (4xx: crawl freely) from
"unreachable" (5xx / network error: assume complete disallow). A run that cannot read robots.txt
must not hammer the site, so the disallow verdict sticks for the rest of the run instead of being
retried on every task.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Literal
from urllib.parse import urlsplit, urlunsplit
from urllib.robotparser import RobotFileParser

log = logging.getLogger(__name__)

# RFC 9309 §2.5: parsers must read at least 500 KiB; anything beyond is ignored.
ROBOTS_MAX_BYTES = 500 * 1024

RobotsMode = Literal["parsed", "allow_all", "disallow_all"]


@dataclass(frozen=True)
class RobotsFetchResult:
    """Outcome of downloading robots.txt (after following redirects)."""

    status: int
    text: str = ""


# Downloads the given robots.txt URL; raises on network errors (=> unreachable).
RobotsFetch = Callable[[str], Awaitable[RobotsFetchResult]]


def origin_of(url: str) -> str:
    """scheme://host[:port], lower-cased: robots.txt applies per origin (RFC 9309 §2.3)."""
    parts = urlsplit(url)
    return urlunsplit((parts.scheme.lower(), parts.netloc.lower(), "", "", ""))


def robots_url(url: str) -> str:
    return origin_of(url) + "/robots.txt"


def product_token(user_agent: str) -> str:
    """'haggler-agent/0.1 (+https://...)' -> 'haggler-agent' (what robots groups match on)."""
    token = user_agent.strip().split("/", 1)[0].split(" ", 1)[0]
    return token or user_agent


@dataclass(frozen=True)
class RobotsRules:
    origin: str
    mode: RobotsMode
    reason: str
    parser: RobotFileParser | None = None

    def can_fetch(self, user_agent: str, url: str) -> bool:
        if self.mode == "allow_all":
            return True
        if self.mode == "disallow_all" or self.parser is None:
            return False
        return self.parser.can_fetch(product_token(user_agent), url)

    def crawl_delay(self, user_agent: str) -> float | None:
        if self.parser is None:
            return None
        delay = self.parser.crawl_delay(product_token(user_agent))
        return float(delay) if delay is not None else None


def parse_robots(origin: str, text: str) -> RobotsRules:
    parser = RobotFileParser(origin + "/robots.txt")
    parser.parse(text[:ROBOTS_MAX_BYTES].splitlines())
    return RobotsRules(origin=origin, mode="parsed", reason="robots.txt parsed", parser=parser)


def rules_from_response(origin: str, result: RobotsFetchResult) -> RobotsRules:
    """Map the robots.txt HTTP outcome to rules, following RFC 9309 §2.3.1."""
    status = result.status
    if 200 <= status < 300:
        return parse_robots(origin, result.text)
    if status == 429:
        # Rate limited while asking for the rules: treat like a server error (as Google does);
        # being told to slow down is no licence to crawl everything.
        return RobotsRules(origin, "disallow_all", "robots.txt HTTP 429")
    if 300 <= status < 500:
        # 4xx = unavailable; >5 redirects MAY be treated as unavailable too (§2.3.1.2).
        return RobotsRules(origin, "allow_all", f"robots.txt HTTP {status}")
    return RobotsRules(origin, "disallow_all", f"robots.txt HTTP {status}")


class RobotsCache:
    """One robots.txt download per origin per process; concurrent callers share it."""

    def __init__(self, fetch: RobotsFetch) -> None:
        self._fetch = fetch
        self._rules: dict[str, RobotsRules] = {}
        self._locks: dict[str, asyncio.Lock] = {}

    def peek(self, url: str) -> RobotsRules | None:
        return self._rules.get(origin_of(url))

    async def rules_for(self, url: str) -> RobotsRules:
        origin = origin_of(url)
        cached = self._rules.get(origin)
        if cached is not None:
            return cached
        lock = self._locks.setdefault(origin, asyncio.Lock())
        async with lock:
            cached = self._rules.get(origin)
            if cached is not None:
                return cached
            try:
                result = await self._fetch(origin + "/robots.txt")
                rules = rules_from_response(origin, result)
            except Exception as exc:  # network error = unreachable (RFC 9309 §2.3.1.4)
                rules = RobotsRules(
                    origin, "disallow_all", f"robots.txt unreachable: {type(exc).__name__}"
                )
            if rules.mode == "disallow_all":
                log.warning("robots: %s -> disallow all for this run (%s)", origin, rules.reason)
            self._rules[origin] = rules
            return rules

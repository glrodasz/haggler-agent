"""robots.txt rules: group selection, RFC 9309 matching, HTTP outcome mapping and caching."""

from __future__ import annotations

import asyncio

import pytest

from haggler.fetch.robots import (
    RobotsCache,
    RobotsFetchResult,
    origin_of,
    parse_robots,
    product_token,
    robots_url,
    rules_from_response,
)

UA = "haggler-agent/0.1 (+https://github.com/glrodasz/haggler-agent; price research bot)"
ORIGIN = "https://www.example.com"

# Shaped like big consumer sites: a catch-all group, a group for named bots, wildcards.
REALISTIC = """\
# robots.txt for https://www.example.com
User-agent: *
Disallow: /*/account/
Disallow: /api/
Disallow: /search?
Allow: /api/public/
Disallow: /*.json$
Crawl-delay: 2

User-agent: GPTBot
User-agent: CCBot
Disallow: /

Sitemap: https://www.example.com/sitemap.xml
"""


def test_url_helpers():
    assert origin_of("HTTPS://WWW.Example.com:8443/a/b?c=d") == "https://www.example.com:8443"
    assert robots_url("https://www.example.com/se/premium/") == f"{ORIGIN}/robots.txt"
    assert product_token(UA) == "haggler-agent"
    assert product_token("plainbot") == "plainbot"


@pytest.mark.parametrize(
    ("path", "allowed"),
    [
        ("/se/premium/", True),
        ("/se/account/overview/", False),  # wildcard in the middle
        ("/api/prices", False),
        ("/api/public/prices", True),  # longest match wins over the shorter Disallow
        ("/search?q=netflix", False),
        ("/search", True),
        ("/data/prices.json", False),  # "$" anchors the end
        ("/data/prices.json?x=1", True),
        ("/robots.txt", True),
    ],
)
def test_catch_all_group_rfc9309_matching(path, allowed):
    rules = parse_robots(ORIGIN, REALISTIC)
    assert rules.can_fetch(UA, ORIGIN + path) is allowed


def test_named_group_beats_catch_all():
    text = REALISTIC + "\nUser-agent: haggler-agent\nDisallow: /co/\n"
    rules = parse_robots(ORIGIN, text)
    assert rules.can_fetch(UA, f"{ORIGIN}/co/premium/") is False
    # The named group replaces the catch-all one: /api/ is no longer disallowed for us.
    assert rules.can_fetch(UA, f"{ORIGIN}/api/prices") is True
    # Other bots' groups never apply to us.
    assert parse_robots(ORIGIN, REALISTIC).can_fetch(UA, f"{ORIGIN}/") is True


def test_allow_wins_ties_and_empty_disallow_allows_everything():
    tie = parse_robots(ORIGIN, "User-agent: *\nDisallow: /page\nAllow: /page\n")
    assert tie.can_fetch(UA, f"{ORIGIN}/page") is True
    empty = parse_robots(ORIGIN, "User-agent: *\nDisallow:\n")
    assert empty.can_fetch(UA, f"{ORIGIN}/anything") is True
    everything = parse_robots(ORIGIN, "User-agent: *\nDisallow: /\n")
    assert everything.can_fetch(UA, f"{ORIGIN}/se/premium/") is False


def test_no_matching_group_allows_and_crawl_delay_is_read():
    rules = parse_robots(ORIGIN, "User-agent: Googlebot\nDisallow: /\n")
    assert rules.can_fetch(UA, f"{ORIGIN}/x") is True
    assert parse_robots(ORIGIN, REALISTIC).crawl_delay(UA) == 2.0
    assert rules.crawl_delay(UA) is None


def test_percent_encoding_is_normalised():
    rules = parse_robots(ORIGIN, "User-agent: *\nDisallow: /caf%C3%A9/\n")
    assert rules.can_fetch(UA, f"{ORIGIN}/café/menu") is False


def test_garbage_or_html_body_allows():
    rules = parse_robots(ORIGIN, "<html><body>Not found</body></html>")
    assert rules.can_fetch(UA, f"{ORIGIN}/x") is True


@pytest.mark.parametrize(
    ("status", "mode"),
    [
        (200, "parsed"),
        (204, "parsed"),
        (301, "allow_all"),  # redirect chain exhausted -> "unavailable"
        (401, "allow_all"),
        (403, "allow_all"),
        (404, "allow_all"),
        (410, "allow_all"),
        (429, "disallow_all"),
        (500, "disallow_all"),
        (503, "disallow_all"),
    ],
)
def test_http_outcomes(status, mode):
    rules = rules_from_response(ORIGIN, RobotsFetchResult(status, "User-agent: *\nDisallow:\n"))
    assert rules.mode == mode
    assert rules.can_fetch(UA, f"{ORIGIN}/x") is (mode != "disallow_all")


async def test_cache_fetches_once_per_origin_even_concurrently():
    calls: list[str] = []

    async def fetch(url: str) -> RobotsFetchResult:
        calls.append(url)
        await asyncio.sleep(0)
        return RobotsFetchResult(200, "User-agent: *\nDisallow: /private/\n")

    cache = RobotsCache(fetch)
    results = await asyncio.gather(
        *(cache.rules_for(f"{ORIGIN}/page/{i}") for i in range(5)),
        cache.rules_for("https://other.example.org/"),
    )
    assert sorted(calls) == ["https://other.example.org/robots.txt", f"{ORIGIN}/robots.txt"]
    assert results[0] is results[4]
    assert cache.peek(f"{ORIGIN}/whatever") is results[0]
    assert results[0].can_fetch(UA, f"{ORIGIN}/private/x") is False


async def test_cache_network_error_disallows_for_the_run():
    calls = 0

    async def fetch(url: str) -> RobotsFetchResult:
        nonlocal calls
        calls += 1
        raise ConnectionError("reset by peer")

    cache = RobotsCache(fetch)
    first = await cache.rules_for(f"{ORIGIN}/a")
    second = await cache.rules_for(f"{ORIGIN}/b")
    assert first.mode == "disallow_all" and "unreachable" in first.reason
    assert second is first and calls == 1

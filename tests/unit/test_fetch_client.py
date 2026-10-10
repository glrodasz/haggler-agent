"""HttpFetcher: headers, redirects, robots, error mapping, bot walls, politeness, body cap."""

from __future__ import annotations

import asyncio

import httpx
import pytest
import respx

from haggler.config import Settings
from haggler.fetch import client as client_module
from haggler.fetch import pdf as pdf_module
from haggler.fetch.client import HttpFetcher, looks_like_bot_wall
from haggler.fetch.errors import (
    BlockedError,
    FetchError,
    RobotsDisallowedError,
    TransientFetchError,
)

UA = "haggler-test/0.1 (+https://github.com/glrodasz/haggler-agent; price research bot)"
SITE = "https://www.example.com"
ALLOW_ALL = "User-agent: *\nDisallow:\n"
# The start of a PDF: the fetcher only sniffs the signature, it never parses.
PDF = b"%PDF-1.4\n%\xe2\xe3\xcf\xd3\n1 0 obj << /Type /Catalog >> endobj\n"

# Trimmed from the public challenge/deny pages these vendors serve (structure and markers kept).
CLOUDFLARE_CHALLENGE = """<!DOCTYPE html><html lang="en-US"><head><title>Just a moment...</title>
<meta http-equiv="refresh" content="390"><meta name="robots" content="noindex,nofollow">
</head><body><div class="main-wrapper" role="main"><div class="main-content">
<noscript><div class="h2"><span id="challenge-error-text">Enable JavaScript and cookies to continue
</span></div></noscript></div></div><script>(function(){window._cf_chl_opt={cvId: '3',
cZone: "www.example.com",cType: 'managed',cRay: '8d1f2a3b4c5d6e7f',cH: 'abc'};
var cpo = document.createElement('script');
cpo.src = '/cdn-cgi/challenge-platform/h/g/orchestrate/chl_page/v1?ray=8d1f2a3b4c5d6e7f';
}());</script></body></html>"""

CLOUDFLARE_BLOCKED = """<!DOCTYPE html><html><head><title>Attention Required! | Cloudflare</title>
</head><body><div id="cf-wrapper"><div id="cf-error-details" class="cf-error-details-wrapper">
<h1 data-translate="block_headline">Sorry, you have been blocked</h1>
<h2 class="cf-subheadline">You are unable to access example.com</h2></div></div></body></html>"""

AKAMAI_DENIED = """<HTML><HEAD>
<TITLE>Access Denied</TITLE>
</HEAD><BODY>
<H1>Access Denied</H1>

You don't have permission to access "http&#58;&#47;&#47;www&#46;example&#46;com&#47;se&#47;"
 on this server.<P>
Reference&#32;&#35;18&#46;5f2e1302&#46;1728370000&#46;1a2b3c4d
<P>https&#58;&#47;&#47;errors&#46;edgesuite&#46;net&#47;18&#46;5f2e1302&#46;1728370000&#46;1a2b3c4d</P>
</BODY>
</HTML>
"""

PERIMETERX = """<!DOCTYPE html><html lang="en"><head>
<title>Access to this page has been denied</title>
<script>window._pxAppId = 'PXu6b0qd2S';window._pxJsClientSrc = '/u6b0qd2S/init.js';</script>
</head><body><div id="px-captcha"></div><p>Press &amp; Hold to confirm you are a human
(and not a bot).</p><script src="https://captcha.px-cloud.net/PXu6b0qd2S/captcha.js"></script>
</body></html>"""

DATADOME = """<html><head><title>example.com</title></head><body style="margin:0">
<p id="cmsg">Please enable JS and disable any ad blocker</p>
<script>var dd={'rt':'c','cid':'AHrlqAAAAAMA','hsh':'2211F522B61E269B869FA6EAFFB5E1','t':'fe',
'host':'geo.captcha-delivery.com'}</script><script src="https://ct.captcha-delivery.com/c.js">
</script></body></html>"""


class FakeClock:
    """monotonic()/sleep() pair: sleeping advances time instantly."""

    def __init__(self) -> None:
        self.now = 1000.0
        self.sleeps: list[float] = []

    def monotonic(self) -> float:
        return self.now

    async def sleep(self, seconds: float) -> None:
        self.sleeps.append(round(seconds, 6))
        self.now += seconds
        await asyncio.sleep(0)


def settings(**overrides) -> Settings:
    base = {"user_agent": UA, "per_host_interval_s": 3.0, "respect_robots": True}
    return Settings(**{**base, **overrides})


@pytest.fixture
def clock() -> FakeClock:
    return FakeClock()


@pytest.fixture
def router():
    with respx.mock(assert_all_called=False, assert_all_mocked=True) as r:
        yield r


@pytest.fixture
async def fetcher(clock, router):
    f = HttpFetcher(settings(), monotonic=clock.monotonic, sleep=clock.sleep)
    yield f
    await f.aclose()


def allow_robots(router, origin: str = SITE, body: str = ALLOW_ALL):
    return router.get(f"{origin}/robots.txt").mock(return_value=httpx.Response(200, text=body))


# ------------------------------------------------------------------ success path


async def test_sends_identifying_headers_and_returns_raw_response(fetcher, router):
    allow_robots(router)
    page = router.get(f"{SITE}/se/premium/").mock(
        return_value=httpx.Response(
            200,
            text="<html><body><p>129 kr/mån</p></body></html>",
            headers={"Content-Type": "text/html; charset=utf-8", "Set-Cookie": "sid=secret"},
        )
    )
    resp = await fetcher.fetch(f"{SITE}/se/premium/")

    sent = page.calls.last.request.headers
    assert sent["user-agent"] == UA
    assert sent["accept-language"] == "en-US,en;q=0.8"
    assert sent["accept"].startswith("text/html")
    assert resp.status_code == 200
    assert resp.url == resp.final_url == f"{SITE}/se/premium/"
    assert resp.content_type == "text/html"
    assert "129 kr/mån" in resp.text
    assert resp.fetched_at is not None and resp.fetched_at.tzinfo is not None
    assert "set-cookie" not in resp.headers
    assert resp.headers["content-type"].startswith("text/html")


async def test_accept_is_passed_per_call_for_json(fetcher, router):
    allow_robots(router)
    api = router.get(f"{SITE}/api/v1/products").mock(
        return_value=httpx.Response(200, json={"Products": []})
    )
    resp = await fetcher.fetch(f"{SITE}/api/v1/products", accept="application/json")
    assert api.calls.last.request.headers["accept"] == "application/json"
    assert resp.content_type == "application/json"
    assert resp.text == '{"Products":[]}'


async def test_charset_from_header_and_meta(fetcher, router):
    allow_robots(router)
    router.get(f"{SITE}/latin1").mock(
        return_value=httpx.Response(
            200,
            content="Precio: 18.900 pesos — señal".encode("cp1252"),
            headers={"Content-Type": "text/html; charset=windows-1252"},
        )
    )
    router.get(f"{SITE}/meta").mock(
        return_value=httpx.Response(
            200,
            content=b'<html><head><meta charset="iso-8859-1"></head><body>a\xf1o</body></html>',
            headers={"Content-Type": "text/html"},
        )
    )
    assert "señal" in (await fetcher.fetch(f"{SITE}/latin1")).text
    assert "año" in (await fetcher.fetch(f"{SITE}/meta")).text


# ------------------------------------------------------------------ redirects


async def test_follows_redirects_and_reports_final_url(fetcher, router):
    allow_robots(router)
    allow_robots(router, "https://open.example.com")
    router.get(f"{SITE}/co/premium/").mock(
        return_value=httpx.Response(301, headers={"Location": "/co-es/premium/"})
    )
    router.get(f"{SITE}/co-es/premium/").mock(
        return_value=httpx.Response(
            302, headers={"Location": "https://open.example.com/co-es/premium/?ref=x"}
        )
    )
    router.get("https://open.example.com/co-es/premium/").mock(
        return_value=httpx.Response(200, text="<p>COP 26.900</p>")
    )
    resp = await fetcher.fetch(f"{SITE}/co/premium/")
    assert resp.url == f"{SITE}/co/premium/"
    assert resp.final_url == "https://open.example.com/co-es/premium/?ref=x"
    assert "26.900" in resp.text


async def test_redirect_target_is_checked_against_its_own_robots(fetcher, router):
    allow_robots(router)
    allow_robots(router, "https://other.example.com", "User-agent: *\nDisallow: /\n")
    router.get(f"{SITE}/go").mock(
        return_value=httpx.Response(302, headers={"Location": "https://other.example.com/x"})
    )
    target = router.get("https://other.example.com/x").mock(return_value=httpx.Response(200))
    with pytest.raises(RobotsDisallowedError):
        await fetcher.fetch(f"{SITE}/go")
    assert not target.called


async def test_more_than_five_redirects_fails(fetcher, router):
    allow_robots(router)
    for i in range(7):
        router.get(f"{SITE}/r{i}").mock(
            return_value=httpx.Response(302, headers={"Location": f"/r{i + 1}"})
        )
    with pytest.raises(FetchError, match="redirects") as exc:
        await fetcher.fetch(f"{SITE}/r0")
    assert type(exc.value) is FetchError


async def test_redirect_without_location_fails(fetcher, router):
    allow_robots(router)
    router.get(f"{SITE}/x").mock(return_value=httpx.Response(302))
    with pytest.raises(FetchError, match="Location"):
        await fetcher.fetch(f"{SITE}/x")


# ------------------------------------------------------------------ robots.txt


async def test_robots_disallow_raises_without_fetching_page(fetcher, router):
    allow_robots(
        router,
        body="User-agent: *\nDisallow: /account/\n\nUser-agent: haggler-test\nDisallow: /se/\n",
    )
    page = router.get(f"{SITE}/se/premium/").mock(return_value=httpx.Response(200))
    with pytest.raises(RobotsDisallowedError) as exc:
        await fetcher.fetch(f"{SITE}/se/premium/")
    assert exc.value.url == f"{SITE}/se/premium/"
    assert not page.called


async def test_robots_404_allows_all(fetcher, router):
    robots = router.get(f"{SITE}/robots.txt").mock(return_value=httpx.Response(404))
    router.get(f"{SITE}/a").mock(return_value=httpx.Response(200, text="a"))
    router.get(f"{SITE}/b").mock(return_value=httpx.Response(200, text="b"))
    assert (await fetcher.fetch(f"{SITE}/a")).text == "a"
    assert (await fetcher.fetch(f"{SITE}/b")).text == "b"
    assert robots.call_count == 1  # cached for the process


@pytest.mark.parametrize(
    "outcome",
    [httpx.Response(500), httpx.Response(503), httpx.ConnectError("refused")],
    ids=["500", "503", "network"],
)
async def test_robots_unreachable_disallows_for_the_run(fetcher, router, outcome):
    route = router.get(f"{SITE}/robots.txt")
    if isinstance(outcome, Exception):
        route.mock(side_effect=outcome)
    else:
        route.mock(return_value=outcome)
    page = router.get(f"{SITE}/se/").mock(return_value=httpx.Response(200))
    for _ in range(2):
        with pytest.raises(RobotsDisallowedError, match="robots"):
            await fetcher.fetch(f"{SITE}/se/")
    assert route.call_count == 1
    assert not page.called


async def test_robots_redirect_is_followed(fetcher, router):
    router.get("https://example.com/robots.txt").mock(
        return_value=httpx.Response(301, headers={"Location": f"{SITE}/robots.txt"})
    )
    allow_robots(router, body="User-agent: *\nDisallow: /private/\n")
    with pytest.raises(RobotsDisallowedError):
        await fetcher.fetch("https://example.com/private/x")


async def test_respect_robots_off_skips_robots(clock, router):
    robots = allow_robots(router, body="User-agent: *\nDisallow: /\n")
    router.get(f"{SITE}/x").mock(return_value=httpx.Response(200, text="ok"))
    async with HttpFetcher(
        settings(respect_robots=False), monotonic=clock.monotonic, sleep=clock.sleep
    ) as f:
        assert (await f.fetch(f"{SITE}/x")).text == "ok"
    assert not robots.called


# ------------------------------------------------------------------ error mapping


@pytest.mark.parametrize(
    ("status", "exc_type"),
    [
        (429, TransientFetchError),
        (500, TransientFetchError),
        (502, TransientFetchError),
        (503, TransientFetchError),
        (404, FetchError),
        (410, FetchError),
        (400, FetchError),
        (403, FetchError),
        (451, FetchError),
    ],
)
async def test_status_mapping(fetcher, router, status, exc_type):
    allow_robots(router)
    router.get(f"{SITE}/x").mock(
        return_value=httpx.Response(status, text="<html><body>Error</body></html>")
    )
    with pytest.raises(exc_type) as exc:
        await fetcher.fetch(f"{SITE}/x")
    assert type(exc.value) is exc_type
    assert exc.value.status == status
    assert exc.value.url == f"{SITE}/x"


async def test_429_message_carries_retry_after(fetcher, router):
    allow_robots(router)
    router.get(f"{SITE}/x").mock(return_value=httpx.Response(429, headers={"Retry-After": "120"}))
    with pytest.raises(TransientFetchError, match="retry-after 120"):
        await fetcher.fetch(f"{SITE}/x")


@pytest.mark.parametrize(
    "error",
    [
        httpx.ConnectTimeout("connect timeout"),
        httpx.ReadTimeout("read timeout"),
        httpx.ConnectError("connection refused"),
        httpx.RemoteProtocolError("peer closed connection"),
    ],
)
async def test_network_errors_are_transient(fetcher, router, error):
    allow_robots(router)
    router.get(f"{SITE}/x").mock(side_effect=error)
    with pytest.raises(TransientFetchError) as exc:
        await fetcher.fetch(f"{SITE}/x")
    assert exc.value.status is None


@pytest.mark.parametrize(
    ("status", "body", "headers", "vendor"),
    [
        (
            403,
            CLOUDFLARE_CHALLENGE,
            {"cf-mitigated": "challenge", "server": "cloudflare"},
            "cloudflare",
        ),
        (503, CLOUDFLARE_CHALLENGE, {"server": "cloudflare"}, "cloudflare"),
        (403, CLOUDFLARE_BLOCKED, {"server": "cloudflare"}, "cloudflare"),
        (403, AKAMAI_DENIED, {"server": "AkamaiGHost"}, "akamai"),
        (403, PERIMETERX, {}, "perimeterx"),
        (403, DATADOME, {"x-datadome": "protected"}, "datadome"),
        (405, "<html>Please solve this CAPTCHA to continue</html>", {}, "captcha"),
    ],
)
async def test_bot_walls_are_blocked(fetcher, router, status, body, headers, vendor):
    allow_robots(router)
    router.get(f"{SITE}/se/").mock(return_value=httpx.Response(status, text=body, headers=headers))
    with pytest.raises(BlockedError, match=vendor) as exc:
        await fetcher.fetch(f"{SITE}/se/")
    assert exc.value.status == status


def test_bot_wall_detector_ignores_ordinary_pages():
    assert looks_like_bot_wall({}, "<html><title>Spotify Premium</title>129 kr</html>") is None
    assert looks_like_bot_wall({"server": "AkamaiGHost"}, "<h1>Not Found</h1>") is None
    assert looks_like_bot_wall({}, AKAMAI_DENIED) == "akamai"  # Reference &#35; is unescaped


async def test_unsupported_egress_and_scheme(fetcher):
    with pytest.raises(FetchError, match="egress not supported yet"):
        await fetcher.fetch(f"{SITE}/x", egress="proxy:CO")
    with pytest.raises(FetchError, match="unsupported URL"):
        await fetcher.fetch("ftp://example.com/x")


# ------------------------------------------------------------------ politeness


async def test_min_interval_between_request_starts_per_host(clock, router):
    starts: list[tuple[str, float]] = []

    def record(request: httpx.Request) -> httpx.Response:
        starts.append((f"{request.url.host}{request.url.path}", clock.now))
        body = ALLOW_ALL if request.url.path == "/robots.txt" else "ok"
        return httpx.Response(200, text=body)

    router.route(host="www.example.com").mock(side_effect=record)
    router.route(host="api.example.org").mock(side_effect=record)
    async with HttpFetcher(settings(), monotonic=clock.monotonic, sleep=clock.sleep) as f:
        await f.fetch(f"{SITE}/a")
        await f.fetch(f"{SITE}/b")
        await f.fetch("https://api.example.org/c")  # other host: robots right away
        await f.fetch(f"{SITE}/d")

    t0 = 1000.0
    assert starts == [
        ("www.example.com/robots.txt", t0),  # robots.txt counts as a request
        ("www.example.com/a", t0 + 3),
        ("www.example.com/b", t0 + 6),
        ("api.example.org/robots.txt", t0 + 6),
        ("api.example.org/c", t0 + 9),
        ("www.example.com/d", t0 + 9),  # 3 s already elapsed since /b
    ]


async def test_concurrent_requests_to_one_host_are_serialised(clock, router):
    starts: list[float] = []

    def record(request: httpx.Request) -> httpx.Response:
        starts.append(clock.now)
        return httpx.Response(200, text="ok")

    router.route(host="www.example.com").mock(side_effect=record)
    async with HttpFetcher(
        settings(respect_robots=False, per_host_interval_s=2.5),
        monotonic=clock.monotonic,
        sleep=clock.sleep,
    ) as f:
        await asyncio.gather(*(f.fetch(f"{SITE}/p{i}") for i in range(4)))
    assert starts == [1000.0, 1002.5, 1005.0, 1007.5]


async def test_crawl_delay_above_interval_is_honoured(clock, router):
    allow_robots(router, body="User-agent: *\nCrawl-delay: 10\nDisallow:\n")
    router.get(f"{SITE}/a").mock(return_value=httpx.Response(200))
    router.get(f"{SITE}/b").mock(return_value=httpx.Response(200))
    async with HttpFetcher(settings(), monotonic=clock.monotonic, sleep=clock.sleep) as f:
        await f.fetch(f"{SITE}/a")
        await f.fetch(f"{SITE}/b")
    assert clock.sleeps == [10.0, 10.0]


# ------------------------------------------------------------------ body cap


async def test_body_cap_by_content_length(fetcher, router, monkeypatch):
    monkeypatch.setattr(client_module, "MAX_BODY_BYTES", 1024)
    allow_robots(router)
    router.get(f"{SITE}/big").mock(return_value=httpx.Response(200, content=b"x" * 2048))
    with pytest.raises(FetchError, match="too large"):
        await fetcher.fetch(f"{SITE}/big")


async def test_body_cap_while_streaming(fetcher, router, monkeypatch):
    monkeypatch.setattr(client_module, "MAX_BODY_BYTES", 1024)
    allow_robots(router)

    async def chunks():
        for _ in range(8):
            yield b"y" * 256

    router.get(f"{SITE}/stream").mock(return_value=httpx.Response(200, content=chunks()))
    with pytest.raises(FetchError, match="too large") as exc:
        await fetcher.fetch(f"{SITE}/stream")
    assert type(exc.value) is FetchError


async def test_default_cap_is_ten_megabytes():
    assert client_module.MAX_BODY_BYTES == 10 * 1024 * 1024
    # The PDF extractor's body cap is the same: a PDF the fetcher accepts is never too big.
    assert pdf_module.MAX_PDF_BYTES == client_module.MAX_BODY_BYTES


async def test_pdf_over_the_cap_is_a_fetch_error(fetcher, router, monkeypatch):
    monkeypatch.setattr(client_module, "MAX_BODY_BYTES", 1024)
    allow_robots(router)
    router.get(f"{SITE}/big.pdf").mock(
        return_value=httpx.Response(
            200, content=PDF + b"0" * 2048, headers={"Content-Type": "application/pdf"}
        )
    )
    with pytest.raises(FetchError, match="too large"):
        await fetcher.fetch(f"{SITE}/big.pdf")


async def test_pdf_cap_applies_even_if_the_body_cap_grows(fetcher, router, monkeypatch):
    monkeypatch.setattr(client_module, "MAX_PDF_BYTES", 16)
    allow_robots(router)
    router.get(f"{SITE}/list.pdf").mock(
        return_value=httpx.Response(200, content=PDF, headers={"Content-Type": "application/pdf"})
    )
    with pytest.raises(FetchError, match="PDF too large") as exc:
        await fetcher.fetch(f"{SITE}/list.pdf")
    assert type(exc.value) is FetchError and exc.value.status == 200


# ------------------------------------------------------------------ bytes (PDF)


@pytest.mark.parametrize(
    ("headers", "content", "kept"),
    [
        ({"Content-Type": "application/pdf"}, PDF, True),
        ({"Content-Type": "application/octet-stream"}, PDF, True),
        ({"Content-Type": "application/octet-stream"}, b"\xef\xbb\xbf\r\n " + PDF, True),
        ({}, PDF, True),  # no content type at all
        ({"Content-Type": "application/octet-stream"}, b"PK\x03\x04zip bytes", False),
        ({"Content-Type": "text/html"}, b"%PDF-1.4 <p>an HTML page</p>", False),
    ],
)
async def test_body_bytes_are_kept_only_for_pdfs(fetcher, router, headers, content, kept):
    allow_robots(router)
    router.get(f"{SITE}/doc").mock(
        return_value=httpx.Response(200, content=content, headers=headers)
    )
    resp = await fetcher.fetch(f"{SITE}/doc")
    assert resp.body == (content if kept else b"")
    # `text` is the decoded body for every response, as before the bytes existed.
    assert resp.text == content.decode("utf-8", errors="replace")
    declared = headers.get("Content-Type")
    assert resp.content_type == (declared or "text/html")  # the server's type, or Accept's


async def test_html_response_is_unchanged_by_the_bytes(fetcher, router):
    allow_robots(router)
    body = "<html><body><p>Premium 129 kr/mån</p></body></html>"
    router.get(f"{SITE}/page").mock(
        return_value=httpx.Response(
            200, text=body, headers={"Content-Type": "text/html; charset=utf-8"}
        )
    )
    resp = await fetcher.fetch(f"{SITE}/page")
    assert (resp.text, resp.content_type, resp.body) == (body, "text/html", b"")


# ------------------------------------------------------------------ lifecycle


async def test_aclose_closes_only_owned_clients(clock):
    external = httpx.AsyncClient()
    f = HttpFetcher(settings(), client=external, monotonic=clock.monotonic, sleep=clock.sleep)
    await f.aclose()
    assert not external.is_closed
    await external.aclose()

    owned = HttpFetcher(settings(), monotonic=clock.monotonic, sleep=clock.sleep)
    await owned.aclose()
    assert owned._client.is_closed


async def test_external_client_is_used(clock, router):
    allow_robots(router)
    router.get(f"{SITE}/x").mock(return_value=httpx.Response(200, text="via external"))
    async with httpx.AsyncClient() as external:
        f = HttpFetcher(settings(), client=external, monotonic=clock.monotonic, sleep=clock.sleep)
        assert (await f.fetch(f"{SITE}/x")).text == "via external"

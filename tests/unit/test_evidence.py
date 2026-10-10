"""Claim verification (haggler.research.evidence)."""

from __future__ import annotations

from datetime import UTC, date, datetime

import pytest

from conftest import FixtureFetcher
from haggler.fetch import RawResponse
from haggler.fetch.errors import BlockedError, FetchError, RobotsDisallowedError
from haggler.llm.grounding import GroundingSource
from haggler.models.research import ClaimDraft, SubjectFacts
from haggler.research.evidence import (
    Page,
    PageCache,
    canonical_url,
    site_of,
    source_class,
    validate_draft,
    verify_subject,
)
from haggler.text import text_hash
from pdf_support import TWO_COLUMN_ROWS, blank_pdf, build_pdf, encrypted_pdf, two_column_pdf

TODAY = date(2026, 10, 9)
TERMS = "https://help.netflix.com/legal/termsofuse"
QUOTE = (
    "You may use the Netflix service primarily within the country in which you have "
    "established your account"
)
PAGE = f"""<html><body><nav>Menu</nav><main>
<h1>Terms of Use</h1>
<p>4.3. {QUOTE.replace("primarily", "<b>primarily</b>")} and only in geographic locations
where we offer our service.</p></main></body></html>"""
NETFLIX = ["netflix.com"]


def draft(**kw: str) -> ClaimDraft:
    base = dict(
        field="residence_clause",
        value="primary_use_in_account_country",
        event_date="",
        quote=QUOTE,
        url=TERMS,
    )
    return ClaimDraft(**(base | kw))


async def verify(drafts, routes, *, grounding=(), previous=None, store_raw=None):
    fetcher = FixtureFetcher(routes)
    pages = PageCache(fetcher, store_raw=store_raw)
    out = await verify_subject(
        list(drafts),
        key="service:netflix",
        kind="service",
        official_domains=NETFLIX,
        grounding=list(grounding),
        pages=pages,
        today=TODAY,
        previous=previous,
    )
    return out, fetcher


@pytest.mark.parametrize(
    ("kw", "reason"),
    [
        ({"field": "card_issuing_country", "value": "LT"}, "not allowed for service"),
        ({"value": "sometimes"}, "is not allowed for residence_clause"),
        ({"quote": "too short"}, "quote too short"),
        ({"quote": "You may use the service ... in your country of residence"}, "ellipsis"),
        ({"quote": "You may use the service … in your country of residence"}, "ellipsis"),
        ({"quote": "x" * 401}, "quote too long"),
        ({"url": "http://help.netflix.com/legal/termsofuse"}, "not https"),
        ({"field": "enforcement_event", "value": "cancellation"}, "needs an event_date"),
        (
            {"field": "enforcement_event", "value": "cancellation", "event_date": "2027-01-01"},
            "future",
        ),
    ],
)
def test_invalid_drafts(kw, reason):
    result = validate_draft(draft(**kw), "service", today=TODAY)
    assert isinstance(result, str) and reason in result


def test_valid_draft_is_normalized():
    v = validate_draft(draft(value=" Primary_Use_In_Account_Country "), "service", today=TODAY)
    assert not isinstance(v, str)
    assert v.value == "primary_use_in_account_country" and v.event_date is None
    e = validate_draft(
        draft(field="enforcement_event", value="cancellation", event_date="2024-06-01"),
        "service",
        today=TODAY,
    )
    assert not isinstance(e, str) and e.event_date == date(2024, 6, 1)


@pytest.mark.parametrize(
    ("url", "cls"),
    [
        ("https://help.netflix.com/legal/termsofuse", "official"),
        ("https://netflix.com/x", "official"),
        ("https://www.techradar.com/news/netflix", "press"),
        ("https://notnetflix.com/x", "press"),
        ("https://community.netflix.com/t/1", "forum"),
        ("https://www.reddit.com/r/netflix", "forum"),
        ("https://help.netflix.com/thread/123/", "forum"),
        # No legal facts in the vocabulary: EU law text is not a service's own statement.
        ("https://eur-lex.europa.eu/eli/reg/2018/302/oj", "press"),
    ],
)
def test_source_class(url, cls):
    assert source_class(url, NETFLIX) == cls


@pytest.mark.parametrize(
    "url",
    [
        "https://groups.google.com/g/youtube/c/abc",
        "https://sites.google.com/view/premium-tricks",
        "https://docs.google.com/document/d/1",
        "https://www.youtube.com/watch?v=abc",
        "https://www.youtube.com/@someone/community",
        "https://apps.apple.com/us/app/disney/id1446075923",
        "https://techcommunity.microsoft.com/t5/x/y",
        "https://learn.microsoft.com/en-us/answers/questions/123/foo",
        "https://support.google.com/youtube/thread/123",
    ],
)
def test_user_content_on_official_domains_is_forum(url):
    domains = ["google.com", "youtube.com", "apple.com", "microsoft.com"]
    assert source_class(url, domains) == "forum"
    assert source_class("https://support.google.com/youtube/answer/6307365", domains) == (
        "official"
    )


@pytest.mark.parametrize(
    "url",
    [
        "https://169.254.169.254/computeMetadata/v1/",
        "https://[::1]/x",
        "https://localhost/terms",
        "https://metadata.google.internal/computeMetadata/v1/",
        "https://intranet/terms",
    ],
)
def test_non_public_hosts_are_invalid(url):
    result = validate_draft(draft(url=url), "service", today=TODAY)
    assert result == "url host is not a public domain"


def test_site_and_canonical_url():
    assert site_of("help.netflix.com") == "netflix.com"
    assert site_of("www.bbc.co.uk") == "bbc.co.uk"
    assert canonical_url("https://Help.Netflix.com/legal#x") == "https://help.netflix.com/legal"


async def test_quote_across_inline_markup_is_verified():
    raw: list[str] = []
    out, _ = await verify(
        [draft()], {TERMS: PAGE}, store_raw=lambda url, text: raw.append(url) or "raw/x.html.gz"
    )
    assert [c.id for c in out.claims] == [
        "service:netflix:residence_clause:primary_use_in_account_country"
    ]
    c = out.claims[0]
    assert c.check.status == "verified" and c.check.source_class == "official"
    assert c.check.final_url == TERMS and c.check.page_hash and c.first_seen == TODAY
    assert out.checks == [("official", "verified")] and raw == [TERMS]


async def test_paraphrased_quote_is_not_verified():
    out, _ = await verify(
        [draft(quote="Netflix may only be used in the country where you live, always.")],
        {TERMS: PAGE},
    )
    assert out.claims == [] and out.unverified[0].status == "quote_not_found"


@pytest.mark.parametrize(
    ("error", "status"),
    [
        (RobotsDisallowedError("robots", url=TERMS), "robots_disallowed"),
        (BlockedError("bot wall", status=403, url=TERMS), "blocked"),
        (FetchError("HTTP 404 not found", status=404, url=TERMS), "not_found"),
        (FetchError("HTTP 500", status=500, url=TERMS), "fetch_failed"),
        (RuntimeError("bug"), "fetch_failed"),
    ],
)
async def test_fetch_failures_end_unverified(error, status):
    out, _ = await verify([draft()], {TERMS: error})
    assert out.claims == [] and out.unverified[0].status == status


def pdf_response(body: bytes, *, content_type: str = "application/pdf") -> RawResponse:
    return RawResponse(
        url=TERMS,
        final_url=TERMS,
        status_code=200,
        text=body.decode("utf-8", errors="replace"),
        content_type=content_type,
        body=body,
    )


# Phase 2 behaviour change (Phase 3, H4): a quote in an official PDF verifies; until then every
# PDF was "unsupported_content".
TERMS_PDF = [
    "Netflix Terms of Use",
    "4.3. You may use the Netflix service primarily within the country in which you have estab-",
    "lished your account and only in geographic locations where we offer our service.",
]


# "text/html": what HttpFetcher returns for a PDF served without a Content-Type (the Accept
# fallback), the bytes in body: PageCache checks for a PDF before the text types.
@pytest.mark.parametrize(
    "content_type", ["application/pdf", "application/octet-stream", "text/html"]
)
async def test_quote_in_a_pdf_is_verified(content_type):
    raw: list[tuple[str, object]] = []
    out, _ = await verify(
        [draft()],
        {TERMS: pdf_response(build_pdf([TERMS_PDF]), content_type=content_type)},
        store_raw=lambda url, data: raw.append((url, data)) or "raw/x.pdf",
    )
    assert [c.check.status for c in out.claims] == ["verified"]
    check = out.claims[0].check
    assert check.source_class == "official" and check.final_url == TERMS
    # page_hash is over the extracted text, the hyphenated line break joined.
    text = "\n".join([TERMS_PDF[0], TERMS_PDF[1][:-1] + TERMS_PDF[2]])
    assert check.page_hash == text_hash(text)
    # The PDF's bytes are what is kept for audits.
    assert raw == [(TERMS, build_pdf([TERMS_PDF]))]


@pytest.mark.parametrize(
    ("body", "reason"),
    [
        (encrypted_pdf([QUOTE]), "pdf_encrypted"),
        (blank_pdf(), "pdf_no_text"),
        (blank_pdf(61), "pdf_limit"),
        (b"%PDF-1.4 broken", "pdf_error"),
        (b"", "pdf_error"),  # declared as a PDF, but the fetcher kept no bytes
    ],
    ids=["encrypted", "blank", "61_pages", "broken", "no_bytes"],
)
async def test_unreadable_pdf_is_unsupported_with_its_reason(body, reason):
    raw: list[str] = []
    out, _ = await verify(
        [draft()], {TERMS: pdf_response(body)}, store_raw=lambda url, data: raw.append(url)
    )
    assert out.claims == [] and raw == []
    (u,) = out.unverified
    assert u.status == "unsupported_content" and u.detail.startswith(reason)


async def test_other_binary_content_is_still_unsupported():
    image = RawResponse(
        url=TERMS, final_url=TERMS, status_code=200, text="\ufffdPNG", content_type="image/png"
    )
    out, _ = await verify([draft()], {TERMS: image})
    assert (out.unverified[0].status, out.unverified[0].detail) == (
        "unsupported_content",
        "image/png",
    )


async def test_pdf_page_cache_fields():
    pages = PageCache(FixtureFetcher({TERMS: pdf_response(build_pdf([TERMS_PDF, ["Page 2"]]))}))
    page = await pages.get(TERMS)
    assert (page.status, page.is_pdf, page.pdf_pages) == (None, True, 2)
    assert page.lines()[-1] == "Page 2" and page.text == "\n".join(page.lines())
    assert page.contains(QUOTE) == "lines" and page.embedded() == []


async def test_pdf_plain_mode_lines_are_the_fallback_search():
    # Layout-mode lines are Page.lines(); plain mode (content-stream order) is Page.nodes().
    pages = PageCache(FixtureFetcher({TERMS: pdf_response(two_column_pdf())}))
    page = await pages.get(TERMS)
    assert page.status is None and page.lines() == list(TWO_COLUMN_ROWS)
    assert page.contains("Telia Mobil 20 GB 299 kr/mån") == "lines"
    assert page.contains("Telia Mobil 20 GB Telia Mobil 50 GB") == "nodes"
    assert page.locate("Telia Mobil 50 GB 299 kr/mån") == (1, page.nodes()[0:4])
    out, _ = await verify(
        [draft(quote="Telia Mobil 20 GB Telia Mobil 50 GB")],
        {TERMS: pdf_response(two_column_pdf())},
    )
    assert [c.check.status for c in out.claims] == ["verified"]


async def test_pdf_with_a_broken_cmap_is_hashed_and_verified():
    # A ToUnicode CMap mapping "A" to a lone surrogate: the text is still valid Unicode, so
    # page_hash (a UTF-8 encode) never raises and the rest of the page still verifies.
    body = build_pdf([[*TERMS_PDF, "ABBA 299 kr"]], to_unicode={"41": "D800"})
    out, _ = await verify([draft()], {TERMS: pdf_response(body)})
    assert [c.check.status for c in out.claims] == ["verified"]
    pages = PageCache(FixtureFetcher({TERMS: pdf_response(body)}))
    page = await pages.get(TERMS)
    assert page.lines()[-1] == "\ufffdBB\ufffd 299 kr" and page.page_hash == text_hash(page.text)


# ------------------------------------------------------------------ Page.contains / locate

NEXT_PAGE = """<html><head>
<script type="application/ld+json">
{"@type": "Offer", "description": "Meli+ Total por $ 24.900 al mes"}</script>
<script id="__NEXT_DATA__" type="application/json">
{"props": {"plans": [{"label": "Disney+ incluido sin costo"}]}}</script>
<script>var hidden = "Texto de un script que no es JSON";</script>
</head><body><main><h1>Meli+</h1><p>Elige tu plan</p></main></body></html>"""


def html_page(text: str, content_type: str = "text/html") -> Page:
    return Page(url=TERMS, text=text, content_type=content_type)


def test_contains_reports_where_the_quote_is():
    page = html_page(PAGE)
    assert page.contains(QUOTE) == "lines"
    assert page.contains("geographic locations where we offer our service") == "lines"
    assert page.contains("nowhere on this page at all") is None
    spa = html_page(NEXT_PAGE)
    assert spa.contains("Meli+ Total por $ 24.900 al mes") is None
    assert spa.contains("Meli+ Total por $ 24.900 al mes", allow_embedded=True) == "embedded"
    assert spa.contains("Disney+ incluido sin costo", allow_embedded=True) == "embedded"
    assert spa.contains("Texto de un script que no es JSON", allow_embedded=True) is None
    assert spa.contains("Elige tu plan", allow_embedded=True) == "lines"


def test_contains_falls_back_to_text_nodes():
    # The rendered lines drop a short <sup> footnote mark; the raw text nodes keep it.
    page = html_page("<html><body><p>Precio total<sup>2</sup> por mes</p></body></html>")
    assert page.lines() == ["Precio total por mes"] and page.contains("total por mes") == "lines"
    assert page.contains("Precio total 2 por mes") == "nodes"


def test_contains_on_plain_text_and_pdf_pages():
    plain = html_page("Line one\n\n  Line   two  \n", content_type="text/plain")
    assert plain.lines() == ["Line one", "Line two"] and plain.nodes() == []
    assert plain.contains("one line two") == "lines" and plain.contains("<p>") is None
    pdf = Page(url=TERMS, is_pdf=True, _lines=["Col A Col B", "row 299 kr"],
               _nodes=["Col A", "row", "Col B 299 kr"])  # fmt: skip
    assert pdf.contains("row 299 kr") == "lines" and pdf.contains("Col B 299 kr") == "nodes"
    assert pdf.contains("Col B", allow_embedded=True) == "lines" and pdf.embedded() == []


def test_locate_returns_the_line_and_its_context():
    lines = ["Header", "Meli+ Total", "Primeros 2 meses", "$ 9.900 / mes", "luego $ 24.900", "Fin"]
    page = Page(url=TERMS, is_pdf=True, _lines=lines, _nodes=[])
    assert page.locate("$ 9.900 / mes") == (3, lines[1:5])
    # A quote over two lines: 2 lines before its first line, 1 after its last.
    assert page.locate("9.900 / MES luego $ 24.900") == (3, lines[1:6])
    assert page.locate("Header Meli+") == (0, lines[0:3])
    assert page.locate("Fin") == (5, lines[3:6])
    assert page.locate("no such text") is None and page.locate("   ") is None


def test_locate_skips_empty_lines_and_falls_back_to_nodes():
    page = Page(url=TERMS, is_pdf=True, _lines=["A", "", "B 1 kr", "C"], _nodes=["x", "only here"])
    assert page.locate("a b 1 KR") == (0, ["A", "", "B 1 kr", "C"])
    assert page.locate("only here") == (1, ["x", "only here"])
    html = html_page(PAGE)
    index, context = html.locate(QUOTE)
    assert html.lines()[index].startswith("4.3.") and context[0] == "Menu"


async def test_grounding_source_of_the_same_site_can_verify():
    direct = "https://help.netflix.com/legal/termsofuse?lang=en"
    other = "https://www.techradar.com/news/netflix"
    redirect = "https://vertexaisearch.cloud.google.com/grounding-api-redirect/abc"
    page = RawResponse(
        url=direct,
        final_url=direct,
        status_code=200,
        text=PAGE,
        content_type="text/html",
        fetched_at=datetime(2026, 10, 9, tzinfo=UTC),
    )
    grounding = [
        GroundingSource(uri=other, domain="techradar.com"),
        GroundingSource(uri=redirect, domain="netflix.com"),
        GroundingSource(uri=direct, domain="netflix.com"),
    ]
    out, fetcher = await verify(
        [draft(url="https://help.netflix.com/legal/termsofuse-old")],
        {direct: page},
        grounding=grounding,
    )
    assert out.claims and out.claims[0].check.via == "grounding"
    assert out.claims[0].check.final_url == direct
    # Other sites and search redirects (robots.txt disallows them) are never fetched.
    assert other not in fetcher.calls and redirect not in fetcher.calls


async def test_grounding_page_of_another_site_does_not_verify():
    direct = "https://help.netflix.com/legal/moved"
    page = RawResponse(
        url=direct,
        final_url="https://copycat.example.com/netflix-terms",
        status_code=200,
        text=PAGE,
        content_type="text/html",
    )
    out, _ = await verify(
        [draft(url="https://help.netflix.com/missing")],
        {direct: page},
        grounding=[GroundingSource(uri=direct, domain="netflix.com")],
    )
    assert out.claims == [] and out.unverified[0].status == "not_found"


async def test_redirect_citations_are_checked_on_known_pages_only():
    redirect = "https://vertexaisearch.cloud.google.com/grounding-api-redirect/abc"
    fetcher = FixtureFetcher({TERMS: PAGE})
    out = await verify_subject(
        [draft(url=redirect), draft(value="must_reside", url=redirect)],
        key="service:netflix",
        kind="service",
        official_domains=NETFLIX,
        grounding=[],
        pages=PageCache(fetcher),
        today=TODAY,
        known_pages=[TERMS],
    )
    assert redirect not in fetcher.calls and fetcher.calls == [TERMS]
    c = out.claims[0]
    assert c.check.via == "known_page" and c.check.final_url == TERMS
    assert c.check.cited_url == redirect and c.check.source_class == "official"
    # The second draft's quote is on the page too, but its value is the agent's: still verified
    # against the quote only (the vocabulary value is reviewed by a person).
    assert len(out.claims) == 2


async def test_known_pages_without_the_quote_do_not_verify():
    out, _ = await verify_known([draft(url="https://help.netflix.com/missing")])
    assert out.claims == [] and out.unverified[0].status == "not_found"


async def verify_known(drafts):
    fetcher = FixtureFetcher({TERMS: "<p>Nothing relevant here.</p>"})
    out = await verify_subject(
        drafts,
        key="service:netflix",
        kind="service",
        official_domains=NETFLIX,
        grounding=[],
        pages=PageCache(fetcher),
        today=TODAY,
        known_pages=[TERMS],
    )
    return out, fetcher


async def test_press_beats_forum_for_the_same_claim():
    forum = "https://www.reddit.com/r/netflix/x"
    press = "https://www.techradar.com/news/netflix"
    out, _ = await verify([draft(url=forum), draft(url=press)], {forum: PAGE, press: PAGE})
    assert [c.check.source_class for c in out.claims] == ["press"]


async def test_recheck_drops_facts_whose_page_is_gone():
    from haggler.research.evidence import recheck_claims

    first, _ = await verify([draft()], {TERMS: PAGE})
    pages = PageCache(FixtureFetcher({}))  # 404 for everything
    kept, dropped = await recheck_claims(first.claims, pages=pages, official_domains=NETFLIX)
    assert kept == [] and dropped[0].status == "not_found"
    blocked = PageCache(FixtureFetcher({TERMS: BlockedError("wall", status=403, url=TERMS)}))
    kept, dropped = await recheck_claims(first.claims, pages=blocked, official_domains=NETFLIX)
    assert kept == first.claims and dropped == []


async def test_one_fetch_per_url_and_one_claim_per_id():
    out, fetcher = await verify([draft(), draft()], {TERMS: PAGE})
    assert len(out.claims) == 1 and fetcher.calls == [TERMS]


async def test_first_seen_is_kept_across_runs():
    first, _ = await verify([draft()], {TERMS: PAGE})
    prev = SubjectFacts(
        key="service:netflix",
        kind="service",
        researched_on=date(2026, 9, 1),
        run_id="old",
        status="researched",
        claims=[first.claims[0].model_copy(update={"first_seen": date(2026, 9, 1)})],
    )
    again, _ = await verify([draft()], {TERMS: PAGE}, previous=prev)
    assert again.claims[0].first_seen == date(2026, 9, 1)
    assert again.claims[0].hash == first.claims[0].hash

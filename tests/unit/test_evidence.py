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
    PageCache,
    canonical_url,
    site_of,
    source_class,
    validate_draft,
    verify_subject,
)

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
        ("https://eur-lex.europa.eu/eli/reg/2018/302/oj", "official"),
    ],
)
def test_source_class(url, cls):
    assert source_class(url, NETFLIX) == cls


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


async def test_pdf_is_unsupported():
    pdf = RawResponse(
        url=TERMS, final_url=TERMS, status_code=200, text="%PDF", content_type="application/pdf"
    )
    out, _ = await verify([draft()], {TERMS: pdf})
    assert out.unverified[0].status == "unsupported_content"


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

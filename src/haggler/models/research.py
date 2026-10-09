"""Research facts: what the agents answer, what survives verification, and the review log.

Only VERIFIED claims are persisted as facts (Claim): our own fetch of the cited page found the
literal quote. Everything else is kept as UnverifiedClaim for the run report and never affects a
verdict. Facts do not depend on any profile; verdicts are computed from them at site-data time
(haggler.research.evaluate).
"""

from __future__ import annotations

import hashlib
from datetime import date, datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from haggler.text import normalize_for_match

SubjectKind = Literal["service", "pair", "instrument"]
SourceClass = Literal["official", "press", "forum"]
CheckStatus = Literal[
    "verified",
    "quote_not_found",
    "fetch_failed",
    "not_found",
    "robots_disallowed",
    "blocked",
    "unsupported_content",
    "invalid",
]
SubjectStatus = Literal["researched", "nothing_found", "failed"]
ReviewChoice = Literal["accept", "reject"]

MAX_QUOTE = 400


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid")


# ------------------------------------------------- agent output (no Optional fields)


class ClaimDraft(BaseModel):
    field: str = Field(description="One field name from the allowed vocabulary.")
    value: str = Field(description="One allowed value code for that field.")
    event_date: str = Field(
        description="YYYY-MM-DD for enforcement_event (the date the source gives), else ''."
    )
    quote: str = Field(
        description="A contiguous verbatim excerpt (<= 300 chars, original language, no "
        "ellipsis) of the page at url that states the fact."
    )
    url: str = Field(description="Exact https URL of the page the quote was copied from.")


class ResearchAnswer(BaseModel):
    status: Literal["found", "nothing_found"]
    claims: list[ClaimDraft]
    open_questions: list[str] = Field(
        description="Up to 3 short questions the sources left open, or []."
    )
    suggested_instruments: list[str] = Field(
        description="Payment providers worth researching next (names only), or []."
    )
    notes: str = Field(description="One short sentence, or ''.")


# ---------------------------------------------------------------- persisted facts


def subject_key(kind: SubjectKind, *parts: str) -> str:
    """ "service:netflix", "pair:netflix:CO", "instrument:revolut_eea"."""
    return ":".join((kind, *parts))


def claim_id(subject: str, field: str, value: str, event_date: date | None = None) -> str:
    """Stable id of a fact: the same (subject, field, value[, date]) keeps its id across runs."""
    parts = [subject, field, value]
    if event_date is not None:
        parts.append(event_date.isoformat())
    return ":".join(parts)


def claim_hash(field: str, value: str, event_date: date | None, quote: str, url: str) -> str:
    """Hash of the evidence behind a fact: a review decision only holds for this exact hash."""
    raw = "|".join(
        [
            field,
            value,
            event_date.isoformat() if event_date else "",
            normalize_for_match(quote),
            url,
        ]
    )
    return hashlib.sha256(raw.encode()).hexdigest()[:12]


class SourceCheck(_Strict):
    cited_url: str
    # The page that was actually read (after redirects), when it was read.
    final_url: str | None = None
    source_class: SourceClass
    status: CheckStatus
    http_status: int | None = None
    page_hash: str | None = None
    retrieved_at: datetime | None = None
    # Where the quote was found: the cited URL, a grounding source of the same site, or one of
    # the subject's known official pages (catalog research_urls).
    via: Literal["cited", "grounding", "known_page"] = "cited"
    detail: str = ""


class Claim(_Strict):
    id: str
    hash: str
    field: str
    value: str
    event_date: date | None = None
    quote: str = Field(max_length=MAX_QUOTE)
    check: SourceCheck
    first_seen: date

    @property
    def url(self) -> str:
        return self.check.final_url or self.check.cited_url

    @property
    def official(self) -> bool:
        return self.check.source_class == "official"


class UnverifiedClaim(_Strict):
    field: str
    value: str
    event_date: str = ""
    quote: str = Field(default="", max_length=MAX_QUOTE)
    cited_url: str = ""
    status: CheckStatus
    detail: str = ""


class SubjectFacts(_Strict):
    key: str
    kind: SubjectKind
    researched_on: date
    run_id: str
    status: SubjectStatus
    claims: list[Claim] = Field(default_factory=list)
    unverified: list[UnverifiedClaim] = Field(default_factory=list)
    open_questions: list[str] = Field(default_factory=list)
    error: str | None = None

    def claim(self, claim_id_: str) -> Claim | None:
        return next((c for c in self.claims if c.id == claim_id_), None)


# ------------------------------------------------------------------------- review


class ReviewDecision(_Strict):
    id: str
    hash: str = Field(pattern=r"^[0-9a-f]{12}$")
    decision: ReviewChoice
    # Not "on": YAML 1.1 (PyYAML) reads an unquoted `on:` key as the boolean True.
    decided_on: date
    by: str = ""
    note: str = ""


class ReviewFile(_Strict):
    decisions: list[ReviewDecision] = Field(default_factory=list)

    def latest(self) -> dict[tuple[str, str], ReviewDecision]:
        """(claim id, hash) -> the last decision in file order (append-only log)."""
        out: dict[tuple[str, str], ReviewDecision] = {}
        for d in self.decisions:
            out[(d.id, d.hash)] = d
        return out


# -------------------------------------------------------------------------- state


class SubjectState(_Strict):
    last_attempt: date | None = None
    last_success: date | None = None
    consecutive_failures: int = 0
    paused_until: date | None = None


class ResearchState(_Strict):
    schema_version: int = 1
    run_id: str | None = None
    as_of: date | None = None
    subjects: dict[str, SubjectState] = Field(default_factory=dict)


class BudgetUsage(_Strict):
    max_calls: int
    max_queries: int
    calls: int = 0
    queries: int = 0
    month: str | None = None
    month_queries: int = 0
    max_month_queries: int = 0
    prompt_tokens: int = 0
    output_tokens: int = 0
    thought_tokens: int = 0
    tool_prompt_tokens: int = 0


class ResearchReport(_Strict):
    run_id: str
    run_date: date
    started_at: datetime
    finished_at: datetime | None = None
    dry_run: bool = False
    planned: list[str] = Field(default_factory=list)
    skipped_reasons: dict[str, str] = Field(default_factory=dict)
    by_status: dict[str, int] = Field(default_factory=dict)
    claims_verified: int = 0
    claims_unverified: int = 0
    # source class -> {check status -> count}, over every claim the agents returned.
    checks: dict[str, dict[str, int]] = Field(default_factory=dict)
    budget: BudgetUsage
    open_questions: dict[str, list[str]] = Field(default_factory=dict)
    suggested_instruments: list[str] = Field(default_factory=list)
    errors: list[str] = Field(default_factory=list)
    outputs: list[str] = Field(default_factory=list)

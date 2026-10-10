"""Research facts: what the agents answer, what survives verification, and the review log.

Only VERIFIED claims are persisted as facts (Claim): our own fetch of the cited page found the
literal quote and, for a bundle VALUE (research.fields yaml_value), the value check found the
value in it (research.values.check_value: "value_not_in_quote" / "implausible" otherwise).
Everything else is kept as UnverifiedClaim for the run report and never affects a verdict. Facts
do not depend on any profile; verdicts are computed from them at site-data time
(haggler.research.evaluate).

Fields added after Phase 2 (Claim.big_change, SubjectFacts.rechecked_on) are left out of the
JSON while unset, so the facts of the Phase 2 kinds serialize exactly as before.
"""

from __future__ import annotations

import hashlib
from datetime import date, datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from haggler.research import kinds
from haggler.research.kinds import SubjectKind, SubjectRef
from haggler.text import normalize_for_match

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
    # The bundle value hook (research.evidence): the quote verified on the page but does not
    # state the claimed value, or the value is not a plausible price.
    "value_not_in_quote",
    "implausible",
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
    suggestions: list[str] = Field(
        description="What the job asks to suggest for research next (names only), or []."
    )
    notes: str = Field(description="One short sentence, or ''.")

    @model_validator(mode="before")
    @classmethod
    def _old_suggestions_key(cls, data: Any) -> Any:
        """Answers stored before the rename called the list `suggested_instruments`."""
        if isinstance(data, dict) and "suggested_instruments" in data:
            data = dict(data)
            old = data.pop("suggested_instruments")
            data.setdefault("suggestions", old)
        return data


# ---------------------------------------------------------------- persisted facts


def subject_key(kind: SubjectKind, *parts: str) -> str:
    """ "service:netflix", "pair:netflix:CO", "instrument:revolut_eea" (research.kinds); the ids
    are joined unchecked."""
    return kinds.subject_key(kind, *parts)


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
    # A bundle value more than research.values.BIG_CHANGE away from the bundles.yaml value.
    big_change: bool = Field(default=False, exclude_if=lambda v: not v)
    # A bundle value whose quote states it only with the page lines above it (a tier named in a
    # heading): the excerpt from that line to the quote, one per line, which validate-catalog
    # accepts as an anchor's text (research.evidence.anchor_span). Never part of the hash.
    anchor_text: str | None = Field(
        default=None, max_length=MAX_QUOTE, exclude_if=lambda v: v is None
    )

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
    # The last run that re-checked the claims on their pages without researching the subject
    # (the bundle watcher's recheck); researched_on and the state's last_success stay as they
    # were.
    rechecked_on: date | None = Field(default=None, exclude_if=lambda v: v is None)

    def claim(self, claim_id_: str) -> Claim | None:
        return next((c for c in self.claims if c.id == claim_id_), None)

    @property
    def ref(self) -> SubjectRef:
        """The key split under this subject's kind, unchecked (research.kinds KindSpec.split):
        a malformed stored key still splits, so its readers never raise."""
        return kinds.KINDS[self.kind].split(self.key)


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
    # What the service, pair and instrument agents suggested (Phase 2: payment providers).
    suggested_instruments: list[str] = Field(default_factory=list)
    # Every agent's suggestions, by subject kind (bundle: other bundles of the same provider).
    suggestions: dict[str, list[str]] = Field(default_factory=dict, exclude_if=lambda v: not v)
    errors: list[str] = Field(default_factory=list)
    outputs: list[str] = Field(default_factory=list)

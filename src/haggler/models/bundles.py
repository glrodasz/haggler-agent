"""The bundle watcher's state (research/latest/bundle_watch.json, then data/research/). CONTRACT.

Written by haggler.bundles.watch once per weekly research run; read back by the next run (pauses,
miss counters, the last date each anchor was verified) and by haggler.bundles.status (the derived
display state of bundles.json v2). Evidence only: nothing here ever changes a value of
bundles.yaml (price hints are proposals for review, milestone H8).

PageCheck     one page of a bundle as the watcher last read it: a watch page (kind html|pdf) or
              the index page of a versioned PDF (kind index). status:
                ok                   read, on a domain of the provider for the bundle's country
                blocked / robots_disallowed / not_found / failed / unsupported_content
                                     the fetch outcome (haggler.research.evidence.PageCache)
                pdf_error            a PDF without readable text (encrypted, no text layer, over a
                                     cap, broken: the reason is in `detail`)
                wrong_country        the final URL is not on the provider's domains for the
                                     bundle's country, or the page shows prices but none in the
                                     country's currency (one.google.com pricing by IP)
                superseded           a versioned PDF whose index links a newer edition: the
                                     curated document is not fetched (newest_* say what was found)
                over_budget          not fetched: the run's page budget was spent
              Only ok counts as read. Every other status is unreadable and NEVER means that a value
              changed; unreadable_runs counts consecutive unreadable runs (over_budget and
              superseded are not attempts) and 3 of them pause the page for 28 days
              (paused_until: the watcher does not fetch it until then and carries this check).
              Versioned PDFs: `edition` is the curated document's edition (link_pattern on its own
              url), newest_edition / newest_url the newest edition the index links, newest_anchors
              the anchor_hash of the anchors found in that newest document, and edition_check how
              the curated document was established as current: "index" (it is the newest one the
              index links), "validity" (no usable index: a validity anchor was found in it; the
              confidence is then at most medium) or "unchecked" (neither: its anchors confirm
              nothing).
AnchorCheck   one anchor of bundles.yaml. anchor_hash = sha256(fact|normalize(text)|url)[:12] with
              normalize = haggler.text.normalize_for_match and url = its canonical URL; a component
              anchor writes its fact as "component:<service_id>" (one excerpt may confirm several
              components). An edited anchor gets a new hash, so the state of an old YAML is never
              applied to it. status:
                verified             in the page's visible text (via lines or nodes) and the
                                     value-quote check (haggler.research.values.check_value, with
                                     the page context of Page.locate) still passes
                missing              the page loaded and the excerpt is not there, or it no longer
                                     states the YAML value (the reason is in `detail`); misses
                                     counts consecutive runs
                embedded_only        only in the page's embedded JSON: never confirms a value
                unreadable           its page could not be read this run (not a change)
                outdated_document    on a versioned PDF that a newer edition replaces
                not_checked          its page was not fetched (budget, watch mode, no page)
              last_verified_at is the last run that verified it (carried over across runs).
              unconfirmed / unconfirmed_on: the last read that undid that verification (missing,
              embedded_only or outdated_document) and its date. Carried across the runs that do
              not read the anchor (unreadable, paused, over budget, not checked, a document not
              known to be the current edition) and cleared only by a read that verifies it again:
              while set the anchor confirms nothing, however recent last_verified_at is.
PriceHint     an amount in the bundle's currency on one of its pages that check_value reads as a
              regular (or intro) price of the bundle and that differs from the YAML value.
              first_seen: the first run that found it (carried while each run finds it again);
              a hint is a proposal (haggler.bundles.proposals), reviewed like an agent finding.
BundleWatchState  outcome: ok | changed (a price, base_price or component anchor missing on a
              page that loaded) | new_document | unreadable (no page could be read) | manual |
              off (watch.mode, or a provider whose terms forbid automated access; nothing
              fetched, the previous state carried).
WatchFile     {schema_version, run_id, generated_at, bundles}.
"""

from __future__ import annotations

import hashlib
from datetime import date, datetime
from decimal import Decimal
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from haggler.models.curated import Anchor, AnchorFact
from haggler.models.research import MAX_QUOTE
from haggler.research.evidence import canonical_url
from haggler.text import normalize_for_match

WATCH_SCHEMA_VERSION = 1
HINT_LINE_CHARS = 200

PageKind = Literal["html", "pdf", "index"]
PageStatus = Literal[
    "ok",
    "blocked",
    "robots_disallowed",
    "not_found",
    "failed",
    "unsupported_content",
    "pdf_error",
    "wrong_country",
    "superseded",
    "over_budget",
]
EditionCheck = Literal["index", "validity", "unchecked"]
AnchorStatus = Literal[
    "verified", "missing", "unreadable", "embedded_only", "outdated_document", "not_checked"
]
# The anchor states that undo an earlier verification (unreadable / not_checked do not).
UnconfirmedStatus = Literal["missing", "embedded_only", "outdated_document"]
# Where the excerpt was found (haggler.research.evidence.Page.contains).
AnchorVia = Literal["lines", "nodes", "embedded"]
WatchOutcome = Literal["ok", "changed", "new_document", "unreadable", "manual", "off"]
HintRole = Literal["regular", "intro"]


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid")


def anchor_hash(anchor: Anchor) -> str:
    fact = f"component:{anchor.service_id}" if anchor.fact == "component" else anchor.fact
    seed = "|".join((fact, normalize_for_match(anchor.text), canonical_url(str(anchor.url))))
    return hashlib.sha256(seed.encode()).hexdigest()[:12]


class PageCheck(_Strict):
    url: str
    kind: PageKind = "html"
    final_url: str | None = None
    status: PageStatus
    http: int | None = None
    content_type: str = ""
    detail: str = ""
    page_hash: str | None = None
    edition: str | None = None
    newest_edition: str | None = None
    newest_url: str | None = None
    newest_status: PageStatus | None = None
    newest_anchors: list[str] = Field(default_factory=list)
    edition_check: EditionCheck | None = None
    checked_at: date | None = None
    last_ok_at: date | None = None
    unreadable_runs: int = 0
    paused_until: date | None = None

    @property
    def read(self) -> bool:
        return self.status == "ok"

    def paused(self, today: date) -> bool:
        return self.paused_until is not None and self.paused_until > today


class AnchorCheck(_Strict):
    anchor_hash: str
    fact: AnchorFact
    service_id: str | None = None
    url: str
    status: AnchorStatus = "not_checked"
    via: AnchorVia | None = None
    detail: str = ""
    # The curated edition of the versioned PDF the anchor quotes.
    edition: str | None = None
    checked_at: date | None = None
    last_verified_at: date | None = None
    misses: int = 0
    unconfirmed: UnconfirmedStatus | None = None
    unconfirmed_on: date | None = None


class PriceHint(_Strict):
    url: str
    amount: Decimal
    currency: str
    period: str | None = None
    role: HintRole
    line: str = Field(max_length=HINT_LINE_CHARS)
    reason: str = ""
    # More than haggler.research.values.BIG_CHANGE away from the YAML value.
    big_change: bool = False
    # The first run that found this amount in this role (None in a file written before H8).
    first_seen: date | None = None
    # When `line` alone names no tier but the lines above it do (a heading): the excerpt from
    # that line to `line`, one per line, which validate-catalog accepts as the anchor's text
    # (research.evidence.anchor_span); the review snippet offers it.
    anchor_text: str | None = Field(
        default=None, max_length=MAX_QUOTE, exclude_if=lambda v: v is None
    )


class BundleWatchState(_Strict):
    bundle_id: str
    outcome: WatchOutcome
    detail: str = ""
    pages: list[PageCheck] = Field(default_factory=list)
    anchors: list[AnchorCheck] = Field(default_factory=list)
    hints: list[PriceHint] = Field(default_factory=list)
    # The run that last fetched this bundle's pages (carried for manual/off bundles).
    checked_at: date | None = None

    def page(self, url: str) -> PageCheck | None:
        return next((p for p in self.pages if p.url == url), None)

    def anchor(self, digest: str) -> AnchorCheck | None:
        return next((a for a in self.anchors if a.anchor_hash == digest), None)


class WatchFile(_Strict):
    schema_version: Literal[1] = WATCH_SCHEMA_VERSION
    run_id: str | None = None
    generated_at: datetime | None = None
    bundles: list[BundleWatchState] = Field(default_factory=list)

    def get(self, bundle_id: str) -> BundleWatchState | None:
        return next((b for b in self.bundles if b.bundle_id == bundle_id), None)

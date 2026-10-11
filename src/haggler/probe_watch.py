"""`haggler probe --mode watch`: the bundle watcher on the real pages, writing nothing.

WHY: a bundle only goes to watch.mode auto once the watcher, run for real from a GitHub runner,
leaves it verified (plan H10, "regla de corte"), and its anchors are copied from that run's
output only. This mode runs haggler.bundles.watch.watch_bundles as the weekly job does (a fresh
research.evidence.PageCache over HttpFetcher: robots.txt on every hop, the identifiable user
agent, the per-host rate limit, default egress, walls never evaded; previous=None; the job's
page budget, Settings.bundle_watch_max_pages; no LLM) and
reports per bundle what it found, with the display state haggler.bundles.status would derive
from it and ready-to-paste YAML for what the YAML does not say yet.

Read-only: data/curated/*.yaml is read (bundles.yaml, research_review.yaml for rejections) and
never written; no store is opened. data/latest/{fx,prices,run}.json are read when the checkout
has them (the FX table for the watcher's currency checks, the daily row of an observed price);
without them the FX table is None, as in a first weekly run.

Which bundles: the targets file lists bundle ids of bundles.yaml (one per line, or separated by
";", "," or blanks; "#" starts a comment at the start of a line or after a blank); a file with
no id means every bundle that has watch.pages. In memory only, each target is watched as
watch.mode auto (Bundle.model_copy: a manual bundle is tested before it is switched), every
other bundle as off (never fetched; still the sibling tiers of the value check). A target whose
provider is unknown or forbids automated access (Provider.automated_access forbidden), that is
expired or that has no watch.pages is reported as skipped and never fetched.

Report per bundle (summary.json and summary.md): the outcome; each page (url, final_url, status,
HTTP, wall, content type, editions of a versioned PDF, edition_check); each anchor (fact,
service_id, status, via, url, its YAML text clipped to ANCHOR_TEXT_CHARS); each price hint (field
and role, amount, line, anchor_text, the watcher's check, the offline check of the text its
snippet uses, big_change) with the snippet `haggler research review` prints for it
(haggler.bundles.proposals.snippet); the display_status, reasons and confidence of
haggler.bundles.status.bundle_state for this watch state (with its pending changes); the edition
snippets of a superseded versioned PDF; and the would-be anchors. Would-be anchors: for every
fact the watcher checks a value for (a curated price, base_price and intro when the YAML has
them or a hint found one, each component) without a verified anchor, up to MAX_CANDIDATES
excerpts of the pages that were read (watch pages, and the newest document of a superseded PDF;
index pages are only read for their links) that would pass both validate-catalog's offline check
of an anchor (catalog anchor rules: 20-400 characters, no ellipsis, research.values.check_value
on the text alone) and the watcher's check with the page context: first the hints' texts (their
value replaces the YAML's, see their snippet), then page lines stating the YAML's own value, one
line or, when the line alone names no tier, the excerpt from the heading above it
(research.evidence.anchor_span). An excerpt under another tier's card is never offered: the
nearest line at or up to TIER_LOOKBACK lines above it that names a tier (the bundle's or a
sibling's aliases or name) must not name only a sibling ("30% OFF en HBO Max" under "Meli+
Total" is Total's, though the value check, which reads no further than its context lines,
would accept it for Esencial). Each comes with the watch.anchors entry to paste; a hint's text
points at the hint's snippet instead. They are for a person to choose from, never values.

Quote budget: per bundle at most QUOTE_BUDGET characters of page text, spent in order on the
anchors' texts (each clipped to ANCHOR_TEXT_CHARS, a cut marked with "…"), the hints (line and
anchor_text; kept whole or dropped, since a cut text would not pass validate-catalog), the
would-be anchors (whole or dropped) and the edition snippets' anchors (whole or dropped). A
snippet repeats the text of its hint or would-be anchor (counted once); its notes may repeat an
existing YAML anchor in at most 160 characters (curated text). `dropped_by_budget` counts what
did not fit. Raw pages go under <out>/raw/watch/ for local debugging only; <out>/summary.{json,md}
are the only files meant to be published. Vendor text in summary.md is a code span or a fenced
block longer than any backtick run inside it (haggler.probe_bundles._code), and reasons are
escaped (_plain).

Exit code: 0 unless something unexpected happened (the watcher or the report crashed; a crash
building or rendering one bundle's report marks that bundle unexpected and the summary is still
written); a bundle whose pages fail, a wall or a missing anchor is a finding.
"""

from __future__ import annotations

import asyncio
import json
import re
import traceback
from collections import Counter
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from datetime import UTC, date, datetime
from decimal import Decimal
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

from pydantic import TypeAdapter

from haggler.bundles.pricing import regular_price
from haggler.bundles.proposals import (
    Proposal,
    Snippet,
    bundle_proposals,
    edition_snippets,
    pending_changes,
    snippet,
)
from haggler.bundles.status import bundle_state, is_expired
from haggler.bundles.watch import pages_over_budget, watch_bundles
from haggler.catalog import anchor_lines, load_bundles, load_review
from haggler.config import Settings, repo_root
from haggler.fetch import Fetcher
from haggler.models.bundles import BundleWatchState, PageCheck, PriceHint, anchor_hash
from haggler.models.catalog import Catalog
from haggler.models.curated import Anchor, Bundle, BundlesFile, Provider
from haggler.models.fx import FxTable
from haggler.models.observations import PublishedPrice
from haggler.models.research import MAX_QUOTE
from haggler.money import find_prices, format_money_code
from haggler.probe import TASK_TIMEOUT_S, _cell, _exc_text, _safe_name
from haggler.probe_bundles import _code, _plain
from haggler.research.evidence import (
    MIN_QUOTE,
    Page,
    PageCache,
    ValueContext,
    anchor_span,
    canonical_url,
)
from haggler.research.review import ReviewIndex
from haggler.research.values import ValueCheck, ValueKind, check_value
from haggler.text import normalize_for_match

QUOTE_BUDGET = 4000
ANCHOR_TEXT_CHARS = 120
MAX_CANDIDATES = 3
# Lines above an excerpt searched for the tier heading it belongs to (_Tiers).
TIER_LOOKBACK = 12
RAW_DIR = "raw/watch"
RAW_NAME_CHARS = 80

_COMMENT = re.compile(r"(?:^|\s)#.*$")
_SEPARATORS = re.compile(r"[\s,;]+")
_ELLIPSIS = re.compile(r"\.\.\.|…")
_BACKTICKS = re.compile(r"`+")
_HINT_FIELDS = {"regular": "bundle_price", "intro": "bundle_intro_price"}
_HINT_FACTS = {"regular": "price", "intro": "intro"}
_MONEY_FACTS: dict[str, tuple[ValueKind, str, str]] = {
    # fact: (check kind, BundlePrice attribute, research field)
    "price": ("money", "amount", "bundle_price"),
    "base_price": ("base_money", "base_plan_amount", "bundle_base_price"),
    "intro": ("intro_money", "intro_amount", "bundle_intro_price"),
}
_COUNTED_INCLUSIONS = ("discount_pct", "discount_abs", "free_months")
# Facts whose excerpt belongs to one tier (a base plan's price is the base plan's).
_TIERED_FACTS = frozenset({"price", "intro", "component"})
_PRICES = TypeAdapter(list[PublishedPrice])


# ============================================================================ targets


def parse_watch_targets(text: str, bundles: BundlesFile) -> list[str]:
    """Bundle ids of a targets file, in order and without repeats; every unknown id is reported
    with its line number. No id at all is []: every bundle with watch.pages (select_bundles)."""
    known = {b.bundle_id for b in bundles.bundles}
    ids: list[str] = []
    errors: list[str] = []
    for n, raw in enumerate(text.splitlines(), 1):
        for token in _SEPARATORS.split(_COMMENT.sub("", raw)):
            if not token:
                continue
            if token not in known:
                errors.append(f"línea {n}: paquete desconocido {token!r} (no está en bundles.yaml)")
            elif token not in ids:
                ids.append(token)
    if errors:
        raise ValueError("targets del vigilante inválidos:\n  " + "\n  ".join(errors))
    return ids


def select_bundles(ids: Sequence[str], bundles: BundlesFile) -> list[str]:
    """The targets, or (none given) every bundle with watch.pages in bundles.yaml order."""
    if ids:
        return list(ids)
    return [b.bundle_id for b in bundles.bundles if b.watch.pages]


def _skip_reason(b: Bundle, provider: Provider | None, today: date) -> str | None:
    if provider is None:
        return f"proveedor desconocido {b.provider_id}: no se lee"
    if provider.automated_access == "forbidden":
        return "los términos del proveedor prohíben el acceso automático: nunca se lee"
    if is_expired(b, today):
        return "la oferta está vencida: el vigilante no la lee"
    if not b.watch.pages:
        return "sin watch.pages: no hay nada que leer"
    return None


def in_memory_watch(
    bundles: BundlesFile, targets: Sequence[str], today: date
) -> tuple[BundlesFile, dict[str, str]]:
    """bundles.yaml as this probe watches it, in memory: each target that can be read as
    watch.mode auto, every other bundle as off. Returns it and {bundle_id: why it is skipped}
    for the targets that are not read."""
    wanted = set(targets)
    skipped: dict[str, str] = {}
    rows: list[Bundle] = []
    for b in bundles.bundles:
        mode = "off"
        if b.bundle_id in wanted:
            why = _skip_reason(b, bundles.provider(b.provider_id), today)
            if why is None:
                mode = "auto"
            else:
                skipped[b.bundle_id] = why
        rows.append(b.model_copy(update={"watch": b.watch.model_copy(update={"mode": mode})}))
    return bundles.model_copy(update={"bundles": rows}), skipped


# ============================================================================ offline data


@dataclass
class OfflineData:
    """What the checkout's data/latest/ says (all optional)."""

    prices: list[PublishedPrice] = field(default_factory=list)
    fx: FxTable | None = None
    as_of: date | None = None
    notes: list[str] = field(default_factory=list)


def load_offline_data(latest: Path) -> OfflineData:
    """data/latest/{prices,fx,run}.json when present and valid; a broken file is a note."""
    out = OfflineData()
    try:
        if (latest / "prices.json").exists():
            out.prices = _PRICES.validate_json((latest / "prices.json").read_bytes())
    except (OSError, ValueError) as exc:
        out.notes.append(f"data/latest/prices.json ignorado: {_exc_text(exc)[:200]}")
    try:
        if (latest / "fx.json").exists():
            out.fx = FxTable.model_validate_json((latest / "fx.json").read_bytes())
    except (OSError, ValueError) as exc:
        out.notes.append(f"data/latest/fx.json ignorado: {_exc_text(exc)[:200]}")
    try:
        if (latest / "run.json").exists():
            run = json.loads((latest / "run.json").read_text(encoding="utf-8"))
            out.as_of = date.fromisoformat(str(run.get("as_of")))
    except (OSError, ValueError, TypeError, AttributeError) as exc:
        out.notes.append(f"data/latest/run.json sin as_of legible: {_exc_text(exc)[:200]}")
    return out


# ============================================================================ the run


class _RecordingCache(PageCache):
    """The run's PageCache, remembering which URLs the watcher asked for (one fetch each)."""

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        self.requested: list[str] = []

    def get(self, url: str) -> Any:
        if url not in self.requested:
            self.requested.append(url)
        return super().get(url)


def _raw_writer(raw_dir: Path) -> Callable[[str, str | bytes], str]:
    """PageCache.store_raw: each page read goes to <out>/raw/watch/, never anywhere else."""
    count = 0

    def store(url: str, data: str | bytes) -> str:
        nonlocal count
        count += 1
        parts = urlsplit(url)
        stem = _safe_name(f"{parts.hostname or ''}{parts.path}")[:RAW_NAME_CHARS]
        name = f"{count:02d}_{stem}.{'pdf' if isinstance(data, bytes) else 'html'}"
        raw_dir.mkdir(parents=True, exist_ok=True)
        if isinstance(data, bytes):
            (raw_dir / name).write_bytes(data)
        else:
            (raw_dir / name).write_text(data, encoding="utf-8")
        return f"{RAW_DIR}/{name}"

    return store


@dataclass
class _Run:
    """One probe run: the curated data, the in-memory watch config and what the watcher read."""

    bundles: BundlesFile
    watched: BundlesFile
    catalog: Catalog
    today: date
    offline: OfflineData
    review: ReviewIndex
    states: dict[str, BundleWatchState] = field(default_factory=dict)
    pages: dict[str, Page] = field(default_factory=dict)

    def real(self, bundle_id: str) -> Bundle:
        return next(b for b in self.bundles.bundles if b.bundle_id == bundle_id)

    def auto(self, bundle_id: str) -> Bundle:
        return next(b for b in self.watched.bundles if b.bundle_id == bundle_id)


async def _watch(
    run: _Run, fetcher: Fetcher | None, settings: Settings, out_dir: Path, now: datetime
) -> int:
    """watch_bundles over the in-memory config; returns the number of fetches."""
    owned = fetcher is None
    if fetcher is None:
        from haggler.fetch.client import HttpFetcher

        # robots.txt on every hop, identifiable user agent, per-host rate limit; default egress.
        fetcher = HttpFetcher(settings)
    try:
        cache = _RecordingCache(
            fetcher, timeout_s=TASK_TIMEOUT_S, store_raw=_raw_writer(out_dir / RAW_DIR)
        )
        out = await watch_bundles(
            run.watched,
            pages=cache,
            previous=None,
            today=run.today,
            now=now,
            run_id=None,
            catalog=run.catalog,
            fx=run.offline.fx,
            max_pages=settings.bundle_watch_max_pages,  # as the weekly job
        )
        run.states = {s.bundle_id: s for s in out.bundles}
        run.pages = {url: await cache.get(url) for url in list(cache.requested)}
        return cache.fetches
    finally:
        if owned:
            await fetcher.aclose()


def run(
    targets_file: Path,
    out_dir: Path,
    settings: Settings,
    *,
    catalog: Catalog,
    now: datetime,
    today: date | None = None,
    fetcher: Fetcher | None = None,
    data_dir: Path | None = None,
    bundles: BundlesFile | None = None,
    offline: OfflineData | None = None,
) -> tuple[dict[str, Any], str]:
    """(summary json, markdown); raises ValueError on a bad targets file before any fetch.
    `data_dir` (default the repo's data/): curated/ and latest/ are read from it, never
    written; `bundles` / `offline` replace what it holds (tests)."""
    data = Path(data_dir) if data_dir is not None else repo_root() / "data"
    today = today or now.date()
    out_dir = Path(out_dir)
    if bundles is None:
        bundles = load_bundles(data / "curated")
    ids = parse_watch_targets(Path(targets_file).read_text(encoding="utf-8"), bundles)
    targets = select_bundles(ids, bundles)
    watched, skipped = in_memory_watch(bundles, targets, today)
    if offline is None:
        offline = load_offline_data(data / "latest")
    review = ReviewIndex(load_review(data / "curated"))
    probe = _Run(bundles, watched, catalog, today, offline, review)
    summary: dict[str, Any] = {
        "mode": "watch",
        "generated_at": now.astimezone(UTC).isoformat(),
        "today": today.isoformat(),
        "targets_file": str(targets_file),
        "targets_from": "file" if ids else "every bundle with watch.pages",
        "targets": targets,
        "quote_budget": QUOTE_BUDGET,
        "fx": offline.fx.as_of.isoformat() if offline.fx is not None else None,
        "notes": list(offline.notes),
        "fetches": 0,
        "error": None,
        "bundles": [],
    }
    try:
        summary["fetches"] = asyncio.run(_watch(probe, fetcher, settings, out_dir, now))
    except Exception as exc:  # the watcher is this mode's product: a crash is a bug
        summary["error"] = _exc_text(exc)
        summary["traceback"] = traceback.format_exc(limit=8).strip()[-3000:]
        return summary, render_watch_md(summary, generated_at=now)
    for bundle_id in targets:
        summary["bundles"].append(_bundle_report(probe, bundle_id, skipped.get(bundle_id)))
    summary["outcome_totals"] = dict(
        sorted(Counter(r["outcome"] for r in summary["bundles"]).items())
    )
    summary["display_totals"] = dict(
        sorted(Counter(str(r["state"]["display_status"]) for r in summary["bundles"]).items())
    )
    return summary, render_watch_md(summary, generated_at=now)


# ============================================================================ per bundle


class _QuoteBudget:
    """QUOTE_BUDGET characters of page text for one bundle's report."""

    def __init__(self, total: int | None = None) -> None:
        self.left = QUOTE_BUDGET if total is None else total
        self.used = 0
        self.dropped = 0
        # Texts already quoted in full: repeating one (a snippet, a shared line) costs nothing.
        self.seen: set[str] = set()

    def clip(self, text: str, cap: int) -> str:
        """At most `cap` characters of `text` (a cut ends in "…"), within what is left."""
        room = max(0, min(self.left, cap))
        kept = text if len(text) <= room else (text[: room - 1] + "…" if room > 1 else "")
        self._spend(len(kept))
        return kept

    def whole(self, *texts: str | None) -> bool:
        """Spends the texts not quoted yet only when they fit together; else counts one drop."""
        new = [t for t in dict.fromkeys(t for t in texts if t) if t not in self.seen]
        size = sum(len(t) for t in new)
        if size > self.left:
            self.dropped += 1
            return False
        self.seen.update(new)
        self._spend(size)
        return True

    def _spend(self, n: int) -> None:
        self.left -= n
        self.used += n


def _base_row(b: Bundle, yaml_mode: str) -> dict[str, Any]:
    return {
        "bundle_id": b.bundle_id,
        "name": b.name,
        "country": b.country,
        "provider_id": b.provider_id,
        "yaml_mode": yaml_mode,
        "watched_as": None,
        "skipped": None,
        "outcome": None,
        "outcome_detail": "",
        "checked_at": None,
        "pages": [],
        "pages_over_budget": [],
        "anchors": [],
        "hints": [],
        "edition_snippets": [],
        "candidates": [],
        "facts_without_candidate": [],
        "state": {
            "display_status": None,
            "reasons_es": [],
            "confidence": None,
            "price_basis": None,
            "notes": [],
        },
        "quoted_chars": 0,
        "dropped_by_budget": 0,
        "error": None,
        "unexpected": False,
    }


def _bundle_report(run: _Run, bundle_id: str, skipped: str | None) -> dict[str, Any]:
    real = run.real(bundle_id)
    row = _base_row(real, real.watch.mode)
    state = run.states.get(bundle_id)
    if skipped is not None or state is None:
        row.update(outcome="skipped", skipped=skipped or "el vigilante no lo leyó")
        return row
    row.update(
        watched_as="auto",
        outcome=state.outcome,
        outcome_detail=state.detail,
        checked_at=state.checked_at.isoformat() if state.checked_at else None,
    )
    budget = _QuoteBudget()
    try:
        auto = run.auto(bundle_id)
        row["pages"] = [_page_row(p, run.pages) for p in state.pages]
        row["pages_over_budget"] = pages_over_budget(state)
        row["anchors"] = _anchor_rows(real, state, budget)
        index_of: dict[tuple[str, str], int] = {}
        row["hints"] = _hint_rows(run, real, auto, state, budget, index_of)
        row["candidates"], row["facts_without_candidate"] = _candidates(
            run, real, state, budget, index_of
        )
        row["edition_snippets"] = _edition_rows(auto, state, budget)
        row["state"] = _state_row(run, auto, state)
    except Exception as exc:  # the report is this mode's product: a crash is a bug
        row.update(error=_exc_text(exc), unexpected=True)
        row["state"]["notes"].append(traceback.format_exc(limit=4).strip()[-1500:])
    row["quoted_chars"] = budget.used
    row["dropped_by_budget"] = budget.dropped
    return row


def _fetched_page(check: PageCheck, pages: dict[str, Page]) -> Page | None:
    """The page the watcher read for a check: its url, or the newest URL where the index
    links the same edition (a re-upload)."""
    page = pages.get(check.url)
    if page is None and check.edition_check == "index" and check.newest_url:
        page = pages.get(check.newest_url)
    return page


def _page_row(check: PageCheck, pages: dict[str, Page]) -> dict[str, Any]:
    page = _fetched_page(check, pages)
    return {
        "url": check.url,
        "kind": check.kind,
        "final_url": check.final_url,
        "status": check.status,
        "http": check.http,
        "wall": page.wall() if page is not None else None,
        "content_type": check.content_type,
        "detail": check.detail,
        "edition": check.edition,
        "newest_edition": check.newest_edition,
        "newest_url": check.newest_url,
        "newest_status": check.newest_status,
        "edition_check": check.edition_check,
        "raw_file": page.raw_ref if page is not None else None,
    }


def _anchor_rows(b: Bundle, state: BundleWatchState, budget: _QuoteBudget) -> list[dict]:
    by_hash = {anchor_hash(a): a for a in b.watch.anchors}
    out = []
    for check in state.anchors:
        a = by_hash.get(check.anchor_hash)
        text = " ".join(a.text.split()) if a is not None else ""
        out.append(
            {
                "fact": check.fact,
                "service_id": check.service_id,
                "url": check.url,
                "status": check.status,
                "via": check.via,
                "detail": check.detail,
                "text": budget.clip(text, ANCHOR_TEXT_CHARS),
            }
        )
    return out


def _value_context(run: _Run, b: Bundle) -> ValueContext:
    return ValueContext(
        bundle=b,
        provider=run.bundles.provider(b.provider_id),
        bundles=tuple(run.bundles.bundles),
        catalog=run.catalog,
    )


def _check_json(check: ValueCheck | None) -> dict[str, Any] | None:
    if check is None:
        return None
    return {"status": check.status, "reason": check.reason}


def _hint_value(b: Bundle, h: PriceHint) -> str:
    return format_money_code(h.amount, h.currency, h.period or b.price.period)


def _hint_rows(
    run: _Run,
    real: Bundle,
    auto: Bundle,
    state: BundleWatchState,
    budget: _QuoteBudget,
    index_of: dict[tuple[str, str], int],
) -> list[dict[str, Any]]:
    """Each hint with the offline check of its anchor text and the review snippet."""
    proposals = bundle_proposals(
        auto, watch=state, facts=None, review=run.review, today=run.today, include_rejected=True
    )
    ctx = _value_context(run, real)
    out: list[dict[str, Any]] = []
    for h in state.hints:
        field_name = _HINT_FIELDS[h.role]
        value = _hint_value(real, h)
        if not budget.whole(h.line, h.anchor_text):
            continue
        p = _proposal_of(proposals, field_name, value, h)
        text = h.anchor_text or " ".join(h.line.split())
        decision = run.review.lookup(p.id, p.hash) if p is not None else None
        if p is not None:
            # A would-be anchor with this text points at this hint's snippet.
            index_of[(field_name, value)] = len(out)
        out.append(
            {
                "field": field_name,
                "role": h.role,
                "amount": str(h.amount),
                "currency": h.currency,
                "period": h.period,
                "value": value,
                "url": h.url,
                "line": h.line,
                "anchor_text": h.anchor_text,
                "page_check": {"status": "ok", "reason": h.reason},
                "anchor_check": _check_json(ctx.offline(field_name, value, text)),
                "big_change": p.big_change if p is not None else h.big_change,
                "first_seen": h.first_seen.isoformat() if h.first_seen else None,
                "proposal_id": p.id if p is not None else None,
                "rejected": decision is not None and decision.decision == "reject",
                "snippet": (
                    snippet(real, p, bundles=run.bundles, catalog=run.catalog).text()
                    if p is not None
                    else None
                ),
            }
        )
    return out


def _proposal_of(
    proposals: Sequence[Proposal], field_name: str, value: str, h: PriceHint
) -> Proposal | None:
    """The watcher proposal a hint makes (None when the YAML already holds the value)."""
    return next(
        (
            p
            for p in proposals
            if p.source == "watcher"
            and (p.field, p.proposed, p.url, p.quote) == (field_name, value, h.url, h.line)
        ),
        None,
    )


def _edition_rows(auto: Bundle, state: BundleWatchState, budget: _QuoteBudget) -> list[dict]:
    out = []
    for s in edition_snippets(auto, state):
        moved = [a["text"] for a in s.fragment.get("watch", {}).get("anchors", [])]
        if budget.whole(*moved):
            out.append({"path": s.path, "notes": s.notes, "snippet": s.text()})
    return out


def _state_row(run: _Run, auto: Bundle, state: BundleWatchState) -> dict[str, Any]:
    notes: list[str] = []
    regular = regular_price(auto, run.offline.prices, as_of=run.offline.as_of)
    if auto.price.source != "observed":
        basis = "curated"
        price_missing = regular.amount is None
    elif regular.amount is not None:
        basis = "observed"
        price_missing = False
    else:
        # The site needs the daily row; the probe's checkout usually has no data/latest/.
        basis = "observed_assumed"
        price_missing = False
        notes.append(
            "Precio observado sin fila diaria en data/latest/prices.json: el estado supone que "
            "la fila de hoy existe (el sitio la necesita para mostrarlo verificado)."
        )
    changes = pending_changes(
        bundle_proposals(auto, watch=state, facts=None, review=run.review, today=run.today)
    )
    st = bundle_state(
        auto,
        today=run.today,
        price_missing=price_missing,
        watch=state,
        price_observed_on=regular.observed_on,
        service_names={s.id: s.name for s in run.catalog.services},
        pending_changes=changes,
    )
    return {
        "display_status": st.display_status,
        "reasons_es": list(st.reasons_es),
        "confidence": st.confidence,
        "price_basis": basis,
        "notes": notes,
    }


# ============================================================================ would-be anchors


@dataclass(frozen=True)
class _Fact:
    fact: str
    service_id: str | None
    # Vocabulary form ("21900 COP/month", "disney_plus.standard_with_ads", "hbo_max:30").
    value: str
    # The money facts' amount and period (None for a component).
    amount: Decimal | None = None
    period: str | None = None

    @property
    def label(self) -> str:
        return f"{self.fact} {self.service_id}" if self.service_id else self.fact


def _component_value(b: Bundle, service_id: str) -> str | None:
    """A component's value as validate-catalog checks its anchor (haggler.catalog)."""
    comp = next((c for c in b.components if c.service_id == service_id), None)
    if comp is None:
        return None
    if comp.inclusion in _COUNTED_INCLUSIONS and comp.value is not None:
        return f"{comp.service_id}:{comp.value}"
    return comp.service_id + (f".{comp.plan_id}" if comp.plan_id else "")


def _facts(b: Bundle, hints: Sequence[PriceHint]) -> list[_Fact]:
    """The facts the watcher checks a value for, with the YAML's value (a curated price, and an
    intro price a hint found, without one: their hints' texts only)."""
    p = b.price
    roles = {h.role for h in hints}
    out: list[_Fact] = []
    for fact, (_kind, attr, _field) in _MONEY_FACTS.items():
        amount = getattr(p, attr)
        if fact == "price" and p.source == "observed":
            continue  # the daily row stands in for a price anchor
        if amount is not None:
            out.append(_Fact(fact, None, format_money_code(amount, p.currency, p.period), amount))
        elif fact == "price" or (fact == "intro" and "intro" in roles):
            out.append(_Fact(fact, None, ""))
    for c in b.components:
        value = _component_value(b, c.service_id)
        if value is not None and all(f.service_id != c.service_id for f in out):
            out.append(_Fact("component", c.service_id, value))
    return out


def _verified(state: BundleWatchState, f: _Fact) -> bool:
    return any(
        a.status == "verified"
        and a.fact == f.fact
        and (f.fact != "component" or a.service_id == f.service_id)
        for a in state.anchors
    )


@dataclass(frozen=True)
class _Source:
    """A page that was read, and the URL an anchor quoting it names (a watch page's url)."""

    anchor_url: str
    page: Page
    newest_edition: str | None = None


def _sources(state: BundleWatchState, pages: dict[str, Page]) -> list[_Source]:
    out: list[_Source] = []
    for check in state.pages:
        if check.status == "ok" and check.kind != "index":
            page = _fetched_page(check, pages)
            if page is not None and page.status is None:
                out.append(_Source(check.url, page))
        elif check.status == "superseded" and check.newest_status == "ok" and check.newest_url:
            page = pages.get(check.newest_url)
            if page is not None and page.status is None:
                out.append(_Source(check.newest_url, page, check.newest_edition))
    return out


class _Checker:
    """validate-catalog's offline anchor check plus the watcher's check on the page."""

    def __init__(self, run: _Run, b: Bundle) -> None:
        self.b = b
        self.provider = run.bundles.provider(b.provider_id)
        self.siblings = tuple(run.bundles.bundles)
        self.catalog = run.catalog

    def accepts(self, f: _Fact, text: str, page: Page) -> bool:
        flat = " ".join(text.split())
        if not MIN_QUOTE <= len(flat) <= MAX_QUOTE or _ELLIPSIS.search(flat):
            return False
        if page.contains(text) not in ("lines", "nodes"):
            return False
        located = page.locate(flat)
        context = located[1] if located else ()
        lines = anchor_lines(text)
        offline = self._check(f, lines, (), None)
        return offline.ok and self._check(f, lines, context, page.final_url or page.url).ok

    def _check(self, f: _Fact, text: str, context: Sequence[str], url: str | None) -> ValueCheck:
        b = self.b
        common: dict[str, Any] = {
            "country": b.country,
            "currency": b.price.currency,
            "bundle": b,
            "bundles": self.siblings,
            "catalog": self.catalog,
        }
        if f.fact == "component":
            return check_value("component", f.value, text, context_lines=context, **common)
        kind = _MONEY_FACTS[f.fact][0]
        return check_value(
            kind,
            f.amount,
            text,
            context_lines=context,
            period=f.period or b.price.period,
            provider=self.provider,
            url=url,
            **common,
        )


def _names(catalog: Catalog, service_id: str) -> list[str]:
    try:
        svc = catalog.service(service_id)
    except KeyError:
        return [service_id.replace("_", " ")]
    return [n for n in (normalize_for_match(x) for x in (svc.name, *svc.aliases)) if n]


def _line_mentions(f: _Fact, line: str, b: Bundle, names: list[str]) -> bool:
    """A page line that could state the fact (the value check decides)."""
    if f.fact == "component":
        norm = normalize_for_match(line)
        return any(n in norm for n in names)
    return f.amount is not None and any(
        m.currency == b.price.currency and m.amount == f.amount
        for m in find_prices(line, [b.price.currency], words=True)
    )


def _tier_pattern(names: Sequence[str]) -> re.Pattern[str] | None:
    alts = sorted({n for n in (normalize_for_match(x) for x in names) if n}, key=len, reverse=True)
    return re.compile(rf"(?<!\w)(?:{'|'.join(map(re.escape, alts))})") if alts else None


class _Tiers:
    """Which tier the nearest line naming one, at or above an excerpt, names: a line of another
    tier's card ("30% OFF en HBO Max" under "Meli+ Total") is never offered to a sibling, even
    where the value check, which reads no further than the context lines, would accept it."""

    def __init__(self, b: Bundle, bundles: BundlesFile) -> None:
        siblings = [
            x
            for x in bundles.bundles
            if x.provider_id == b.provider_id
            and x.country == b.country
            and x.bundle_id != b.bundle_id
        ]
        self.own = _tier_pattern([*b.aliases, b.name])
        self.other = _tier_pattern([n for x in siblings for n in (*x.aliases, x.name)])

    def another(self, lines: Sequence[str], last: int | None) -> bool:
        if last is None or self.other is None:
            return False
        for j in range(last, max(-1, last - TIER_LOOKBACK - 1), -1):
            norm = normalize_for_match(lines[j])
            if self.own is not None and self.own.search(norm):
                return False
            if self.other.search(norm):
                return True
        return False


def _line_index(lines: Sequence[str], line: str) -> int | None:
    needle = normalize_for_match(line)
    return next((i for i, ln in enumerate(lines) if needle in normalize_for_match(ln)), None)


def _page_candidates(
    f: _Fact, src: _Source, checker: _Checker, tiers: _Tiers, names: list[str], limit: int
) -> list[str]:
    """Excerpts of `src` stating the fact's YAML value: a line, or the lines from the heading
    above it (anchor_span), that checker accepts and that no other tier's heading owns."""
    out: list[str] = []
    lines = src.page.lines()
    for i, line in enumerate(lines):
        if len(out) >= limit:
            break
        flat = " ".join(line.split())
        if len(flat) > MAX_QUOTE or not _line_mentions(f, flat, checker.b, names):
            continue
        if f.fact in _TIERED_FACTS and tiers.another(lines, i):
            continue
        if checker.accepts(f, flat, src.page):
            text: str | None = flat
        else:
            text = anchor_span(src.page, flat, lambda t: checker.accepts(f, t, src.page))
        if text is not None and text not in out:
            out.append(text)
    return out


def _hint_fact(b: Bundle, h: PriceHint) -> _Fact:
    return _Fact(_HINT_FACTS[h.role], None, _hint_value(b, h), h.amount, h.period)


def _candidates(
    run: _Run,
    b: Bundle,
    state: BundleWatchState,
    budget: _QuoteBudget,
    index_of: dict[tuple[str, str], int],
) -> tuple[list[dict[str, Any]], list[str]]:
    """The would-be anchors (module docstring) and the facts that have none."""
    checker = _Checker(run, b)
    tiers = _Tiers(b, run.bundles)
    sources = _sources(state, run.pages)
    by_url = {
        canonical_url(u): s for s in sources for u in (s.page.final_url or s.page.url, s.anchor_url)
    }
    out: list[dict[str, Any]] = []
    without: list[str] = []
    for f in _facts(b, state.hints):
        if _verified(state, f):
            continue
        found: list[dict[str, Any]] = []
        seen: set[str] = set()
        # 1. The hints' texts (the hint's own snippet pastes them with the new value).
        for h in state.hints:
            if f.fact != _HINT_FACTS[h.role] or len(found) >= MAX_CANDIDATES:
                continue
            hf = _hint_fact(b, h)
            src = by_url.get(canonical_url(h.url))
            text = h.anchor_text or " ".join(h.line.split())
            if src is None or text in seen or not checker.accepts(hf, text, src.page):
                continue
            lines = src.page.lines()
            if tiers.another(lines, _line_index(lines, h.line)):
                continue
            seen.add(text)
            n = index_of.get((_HINT_FIELDS[h.role], hf.value))
            found.append(_candidate(hf, src, text, "hint", n))
        # 2. Page lines stating the YAML's own value.
        names = _names(run.catalog, f.service_id) if f.service_id else []
        for src in sources:
            room = MAX_CANDIDATES - len(found)
            if room <= 0:
                break
            for text in _page_candidates(f, src, checker, tiers, names, room):
                if text not in seen:
                    seen.add(text)
                    found.append(_candidate(f, src, text, "yaml", None))
        if not found:
            without.append(f.label)
        for c in found:
            # A hint's text was counted with the hint (or dropped with it: then shown here).
            counted = c["source"] == "hint" and c["hint"] is not None
            if counted or budget.whole(c["text"]):
                if not counted:
                    c["snippet"] = _anchor_snippet(b, c).text()
                out.append(c)
    return out, without


def _candidate(f: _Fact, src: _Source, text: str, source: str, hint: int | None) -> dict:
    return {
        "fact": f.fact,
        "service_id": f.service_id,
        "value": f.value,
        "source": source,
        "hint": hint,
        "url": src.anchor_url,
        "final_url": src.page.final_url,
        "newest_edition": src.newest_edition,
        "text": text,
        "snippet": None,
    }


def _anchor_snippet(b: Bundle, c: dict[str, Any]) -> Snippet:
    entry: dict[str, Any] = {"fact": c["fact"]}
    if c["service_id"]:
        entry["service_id"] = c["service_id"]
    entry |= {"url": c["url"], "text": c["text"]}
    notes = []
    if c["source"] == "hint":
        notes.append(f"el ancla confirma {c['value']}: cambia también el valor del YAML")
    if c["newest_edition"]:
        notes.append(
            f"url de la edición {c['newest_edition']}: pega antes el snippet de la edición nueva"
        )
    if b.watch.mode != "auto":
        notes.append(f"watch.mode es {b.watch.mode}: el vigilante solo comprueba anclas en auto")
    Anchor.model_validate(entry)  # a malformed entry here would print an unpasteable snippet
    return Snippet(b.bundle_id, "watch.anchors[]", {"watch": {"anchors": [entry]}}, notes)


# ============================================================================ markdown


def _fence(text: str, lang: str = "yaml") -> list[str]:
    """A fenced block longer than any backtick run inside `text`."""
    fence = "`" * max(3, max((len(m) for m in _BACKTICKS.findall(text)), default=0) + 1)
    return [f"{fence}{lang}", *text.splitlines(), fence]


def _counts_line(totals: dict[str, int]) -> str:
    return ", ".join(f"{k} {v}" for k, v in totals.items()) or "—"


def render_watch_md(summary: dict[str, Any], *, generated_at: datetime) -> str:
    lines = [f"# Probe del vigilante de paquetes ({generated_at:%Y-%m-%d %H:%M} UTC)", ""]
    if summary.get("error"):
        lines += [
            f"El vigilante falló: {_code(summary['error'])}",
            "",
            *_fence(summary.get("traceback", ""), ""),
        ]
        return "\n".join(lines) + "\n"
    rows = summary["bundles"]
    lines += [
        f"**{len(rows)} paquetes**: resultado {_counts_line(summary['outcome_totals'])}; "
        f"estado {_counts_line(summary['display_totals'])}. Descargas: {summary['fetches']}.",
        "",
        "Solo lectura: cada paquete se vigila como `auto` solo en memoria; nada se escribe en "
        "`data/curated/` ni en ningún store. Las anclas se copian de esta salida (texto literal, "
        "URL y hecho); un paquete pasa a `auto` en el YAML solo si aquí sale `verified`.",
        "",
        "| Paquete | CC | Modo YAML | Resultado | Estado | Confianza | Páginas ok | "
        "Anclas verificadas | Pistas | Anclas posibles |",
        "|---|---|---|---|---|---|---|---|---|---|",
    ]
    for r in rows:
        ok = sum(1 for p in r["pages"] if p["status"] == "ok")
        verified = sum(1 for a in r["anchors"] if a["status"] == "verified")
        cells = (
            r["bundle_id"],
            r["country"],
            r["yaml_mode"],
            r["outcome"],
            r["state"]["display_status"],
            r["state"]["confidence"],
            f"{ok}/{len(r['pages'])}",
            f"{verified}/{len(r['anchors'])}",
            len(r["hints"]),
            len(r["candidates"]),
        )
        lines.append("| " + " | ".join(_cell(v) for v in cells) + " |")
    for note in summary.get("notes", []):
        lines += ["", f"Nota: {_code(note)}"]
    lines += ["", "## Detalles", ""]
    for n, r in enumerate(rows, 1):
        try:
            lines += _details(n, r)
        except Exception as exc:  # a report bug costs this bundle's details, never the summary
            r.update(error=r["error"] or _exc_text(exc), unexpected=True)
            lines += [
                f"### {n}. {r['bundle_id']}: el informe falló",
                "",
                f"- Error: {_code(_exc_text(exc)[:300])}",
                "",
            ]
    return "\n".join(lines) + "\n"


def _details(n: int, r: dict[str, Any]) -> list[str]:
    head = f"### {n}. {r['bundle_id']} ({r['country']}): {r['outcome']}"
    if r["state"]["display_status"]:
        head += f" → {r['state']['display_status']}"
    out = [head, ""]
    out.append(f"- Paquete: {_code(r['name'])}; proveedor {_code(r['provider_id'])}")
    if r["skipped"]:
        out += [f"- No se lee: {_plain(r['skipped'])}", ""]
        return out
    out.append(f"- Modo en el YAML: {r['yaml_mode']} (aquí, `auto` solo en memoria)")
    if r["outcome_detail"]:
        out.append(f"- Detalle: {_plain(r['outcome_detail'])}")
    st = r["state"]
    if st["display_status"]:
        out.append(
            f"- Estado según bundles.status: **{st['display_status']}** (confianza "
            f"{st['confidence']}; precio: {st['price_basis']})"
        )
        out += [f"  - {_plain(reason)}" for reason in st["reasons_es"]]
    for note in st["notes"]:
        out.append(f"- Nota: {_code(note[:1500])}")
    if r["error"]:
        out.append(f"- Error: {_code(r['error'][:300])}")
    if r["dropped_by_budget"]:
        out.append(
            f"- Presupuesto de cita: {r['quoted_chars']} de {QUOTE_BUDGET} caracteres; "
            f"{r['dropped_by_budget']} elemento(s) no caben y se omiten"
        )
    out += ["", "#### Páginas", ""]
    out += [_page_md(p) for p in r["pages"]] or ["- ninguna"]
    if r["pages_over_budget"]:
        out.append("- Sin leer por el límite de páginas: " + ", ".join(
            _code(u) for u in r["pages_over_budget"]
        ))  # fmt: skip
    out += ["", "#### Anclas", ""]
    out += [_anchor_md(a) for a in r["anchors"]] or ["- ninguna en el YAML"]
    out += ["", "#### Pistas de precio", ""]
    if not r["hints"]:
        out.append("- ninguna")
    for i, h in enumerate(r["hints"], 1):
        out += _hint_md(i, h)
    out += ["", "#### Anclas posibles", ""]
    if not r["candidates"] and not r["facts_without_candidate"]:
        out.append("- todas las anclas que se comprueban están verificadas")
    for c in r["candidates"]:
        out += _candidate_md(c)
    if r["facts_without_candidate"]:
        out.append(
            "- Sin ancla verificada ni candidata: "
            + ", ".join(_code(f) for f in r["facts_without_candidate"])
        )
    if r["edition_snippets"]:
        out += ["", "#### Edición nueva de la lista", ""]
        for e in r["edition_snippets"]:
            out += [*_fence(e["snippet"]), ""]
    out.append("")
    return out


def _page_md(p: dict[str, Any]) -> str:
    final = p["final_url"]
    text = f"- {_code(p['url'])}"
    if final and final != p["url"]:
        text += f" -> {_code(final)}"
    text += f": **{p['status']}**"
    if p["http"] is not None:
        text += f", HTTP {p['http']}"
    if p["content_type"]:
        text += f", {_code(p['content_type'])}"
    text += f", {p['kind']}"
    if p["wall"]:
        text += f", muro {_code(p['wall'])}"
    if p["edition"] or p["newest_edition"]:
        text += f"; edición {p['edition'] or '—'}, la más nueva {p['newest_edition'] or '—'}"
        if p["newest_url"]:
            text += f" en {_code(p['newest_url'])} ({p['newest_status'] or 'sin leer'})"
        text += f", comprobación {p['edition_check'] or '—'}"
    if p["detail"]:
        text += f"; {_code(p['detail'])}"
    return text


def _anchor_md(a: dict[str, Any]) -> str:
    what = a["fact"] + (f" {a['service_id']}" if a["service_id"] else "")
    text = f"- {what}: **{a['status']}**"
    if a["via"]:
        text += f" (vía {a['via']})"
    text += f" en {_code(a['url'])}"
    if a["text"]:
        text += f": {_code(a['text'])}"
    if a["detail"]:
        text += f"; {_plain(a['detail'])}"
    return text


def _hint_md(i: int, h: dict[str, Any]) -> list[str]:
    check = h["anchor_check"] or {}
    out = [
        f"- Pista {i}: {h['role']} {_code(h['value'])} ({h['field']})"
        + (", cambio grande" if h["big_change"] else "")
        + (", rechazada en research_review.yaml" if h["rejected"] else ""),
        f"  - Línea: {_code(h['line'])}",
        f"  - Página: {_code(h['url'])}",
        "  - Comprobación en la página: ok"
        + (f" ({_plain(h['page_check']['reason'])})" if h["page_check"]["reason"] else ""),
        f"  - Texto del ancla sin red (validate-catalog): {check.get('status', '—')}"
        + (f" ({_plain(check['reason'])})" if check.get("reason") else "")
        + (
            f"; {len(h['anchor_text'].splitlines())} líneas desde el encabezado del nivel"
            if h["anchor_text"]
            else "; la línea sola"
        ),
    ]
    if h["snippet"]:
        out += ["", *_fence(h["snippet"]), ""]
    else:
        out.append("  - Sin snippet: el YAML ya tiene este valor")
    return out


def _candidate_md(c: dict[str, Any]) -> list[str]:
    what = c["fact"] + (f" {c['service_id']}" if c["service_id"] else "")
    if c["hint"] is not None:
        origin = f"de la pista {c['hint'] + 1}"
    elif c["source"] == "hint":
        # Its hint was dropped by the quote budget (or has no snippet): this entry has its own.
        origin = "de una pista sin snippet en este informe"
    else:
        origin = "el valor del YAML"
    head = f"- {what} ({_code(c['value'])}, {origin}) en {_code(c['url'])}"
    if c["snippet"] is None:
        return [f"{head}: el mismo texto que el snippet de esa pista"]
    return [f"{head}:", "", *_fence(c["snippet"]), ""]


__all__ = [
    "ANCHOR_TEXT_CHARS",
    "MAX_CANDIDATES",
    "QUOTE_BUDGET",
    "OfflineData",
    "in_memory_watch",
    "load_offline_data",
    "parse_watch_targets",
    "render_watch_md",
    "run",
    "select_bundles",
]

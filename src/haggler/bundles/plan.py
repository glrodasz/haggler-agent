"""Which bundle subjects a research run gives the agent (pure). CONTRACT.

Called by the research workflow right after the bundle watcher (research.nodes, node
watch_bundles), so this run's watch outcomes count; the bundle subjects run last
(research.kinds run_order), after every Phase 2 subject.

Automatic choice (no explicit request):
* Candidates (automatic(): also the bundles whose pending findings the run re-checks without
  the model): bundles with watch.mode auto, a provider whose terms allow automated access, not
  expired (haggler.bundles.status.is_expired). manual bundles are only researched when asked
  for by name; off, expired and forbidden-provider bundles never.
* Freshness: due after BUNDLE_DAYS (research.kinds) since the last successful research, or
  ENDING_WINDOW_DAYS when the offer (valid_until) or its introductory price (intro_valid_until)
  ends within ENDING_SOON_DAYS. A subject that failed PAUSE_AFTER_FAILURES runs in a row stays
  paused (research.plan.due). The watcher's outcome never makes a bundle due: it stays changed
  (or new_document) until a person edits bundles.yaml, so a shorter window would re-research
  the same changed bundles every run and starve the others.
* Priority among the due ones, in order: the watcher found a change this run (changed /
  new_document; `watch` is this run's state, None when the watcher crashed); never researched;
  YAML values without an anchor (a price, base price or intro amount, an included component or
  an end date that no watch anchor confirms); the offer or its intro ends soon; a never
  researched bundle with an observed price (one product in many countries, such as Apple One,
  whose observed service is researched as a service already: a batch of new countries must not
  hold back the bundles above for months); then the oldest last success first (bundles.yaml
  order breaks ties).
* Cap: `max_bundles` per run (HAGGLER_RESEARCH_MAX_BUNDLES); the rest are skipped with the reason.

Explicit request (`haggler research run --bundles a,b`): exactly those bundles, ignoring
freshness, pauses and the cap; a manual bundle may be asked for, an unknown, expired, off or
forbidden-provider one is an error. Any other explicit request (services, pairs, instruments)
plans no bundle. The monthly guard, --watch-only and --max-jobs are applied by the caller.
"""

from __future__ import annotations

from collections.abc import Iterable, Sequence
from dataclasses import dataclass, field
from datetime import date, timedelta

from haggler.bundles.status import is_expired
from haggler.models.bundles import BundleWatchState, WatchFile
from haggler.models.curated import Bundle, BundlesFile
from haggler.models.research import ResearchState, SubjectFacts, subject_key
from haggler.research.kinds import KINDS
from haggler.research.plan import PlannedSubject, due

BUNDLE_DAYS = KINDS["bundle"].window_days
ENDING_SOON_DAYS = 14
ENDING_WINDOW_DAYS = 14
CHANGED_OUTCOMES = ("changed", "new_document")
# Priority ranks (lower first).
_CHANGED, _NEW, _UNANCHORED, _ENDING, _NEW_OBSERVED, _STALE = range(6)
_PRIORITY_NOTES = {
    _CHANGED: "watch: {outcome}",
    _NEW: "never researched",
    _NEW_OBSERVED: "never researched",
    _UNANCHORED: "values without an anchor: {missing}",
    _ENDING: "ends soon",
    _STALE: "oldest research",
}


@dataclass
class BundleSelection:
    subjects: list[PlannedSubject] = field(default_factory=list)
    # Subject key -> why it was not planned.
    skipped: dict[str, str] = field(default_factory=dict)
    errors: list[str] = field(default_factory=list)


def bundle_key(bundle_id: str) -> str:
    return subject_key("bundle", bundle_id)


def ending_soon(bundle: Bundle, today: date) -> bool:
    """The offer, or its introductory price, ends within ENDING_SOON_DAYS (today included)."""
    horizon = today + timedelta(days=ENDING_SOON_DAYS)
    ends = (bundle.valid_until, bundle.price.intro_valid_until)
    return any(d is not None and today <= d <= horizon for d in ends)


def unanchored(bundle: Bundle) -> list[str]:
    """The YAML values of `bundle` that no watch anchor confirms (price, base_price, intro, a
    component's service id, validity)."""
    facts = {a.fact for a in bundle.watch.anchors}
    components = {a.service_id for a in bundle.watch.anchors if a.fact == "component"}
    price = bundle.price
    out: list[str] = []
    if price.source == "curated" and price.amount is not None and "price" not in facts:
        out.append("price")
    if price.base_plan_amount is not None and "base_price" not in facts:
        out.append("base_price")
    if price.intro_amount is not None and "intro" not in facts:
        out.append("intro")
    out += [
        c.service_id
        for c in bundle.components
        if c.inclusion == "included" and c.service_id not in components
    ]
    if (bundle.valid_until or price.intro_valid_until) and "validity" not in facts:
        out.append("validity")
    return out


def _researchable(bundle: Bundle, bundles: BundlesFile, today: date) -> str | None:
    """Why the agent may never research `bundle` (None: it may)."""
    if is_expired(bundle, today):
        return "the offer has expired"
    provider = bundles.provider(bundle.provider_id)
    if provider is None:
        return f"unknown provider {bundle.provider_id}"
    if provider.automated_access == "forbidden":
        return "the provider's terms forbid automated access"
    if bundle.watch.mode == "off":
        return "watch.mode off"
    return None


def automatic(bundle: Bundle, bundles: BundlesFile, today: date) -> bool:
    """The run may fetch `bundle`'s pages on its own (plan or recheck), not only when a person
    names it: watch.mode auto and researchable (module docstring, "Candidates")."""
    return bundle.watch.mode == "auto" and _researchable(bundle, bundles, today) is None


def _last_success(key: str, facts: dict[str, SubjectFacts], state: ResearchState) -> date | None:
    st = state.subjects.get(key)
    if st is not None and st.last_success is not None:
        return st.last_success
    return facts[key].researched_on if key in facts else None


def plan_bundles(
    bundles: BundlesFile,
    *,
    facts: dict[str, SubjectFacts],
    state: ResearchState,
    watch: WatchFile | None,
    today: date,
    max_bundles: int,
    requested: Sequence[str] = (),
    explicit: bool = False,
) -> BundleSelection:
    """The bundle subjects of this run (see the module docstring). `explicit`: the run's request
    names subjects (`requested` are the bundle ids it names, maybe none)."""
    sel = BundleSelection()
    if explicit:
        return _requested(bundles, requested, today, sel)
    states: dict[str, BundleWatchState] = {s.bundle_id: s for s in (watch.bundles if watch else [])}
    ranked: list[tuple[int, date, int, PlannedSubject]] = []
    for order, b in enumerate(bundles.bundles):
        if not automatic(b, bundles, today):
            continue
        key = bundle_key(b.bundle_id)
        ws = states.get(b.bundle_id)
        changed = ws is not None and ws.outcome in CHANGED_OUTCOMES
        soon = ending_soon(b, today)
        window = ENDING_WINDOW_DAYS if soon else BUNDLE_DAYS
        why = due(key, facts, state, today, window=window)
        if why is None:
            sel.skipped[key] = "fresh or paused"
            continue
        missing = unanchored(b)
        if changed:
            rank = _CHANGED
        elif why == "new":
            rank = _NEW_OBSERVED if b.price.source == "observed" else _NEW
        elif missing:
            rank = _UNANCHORED
        elif soon:
            rank = _ENDING
        else:
            rank = _STALE
        note = _PRIORITY_NOTES[rank].format(
            outcome=ws.outcome if ws else "", missing=", ".join(missing)
        )
        reason = why if rank in (_NEW, _NEW_OBSERVED) else f"{why}; {note}"
        last = _last_success(key, facts, state) or date.min
        ranked.append(
            (
                rank,
                last,
                order,
                PlannedSubject(key=key, kind="bundle", reason=reason, bundle_id=b.bundle_id),
            )
        )
    for i, (*_, subject) in enumerate(sorted(ranked, key=lambda r: r[:3])):
        if i >= max(0, max_bundles):
            sel.skipped[subject.key] = "over the per-run bundle cap"
            continue
        sel.subjects.append(subject)
    return sel


def _requested(
    bundles: BundlesFile, requested: Iterable[str], today: date, sel: BundleSelection
) -> BundleSelection:
    by_id = {b.bundle_id: b for b in bundles.bundles}
    for bundle_id in dict.fromkeys(requested):
        b = by_id.get(bundle_id)
        if b is None:
            sel.errors.append(f"request: unknown bundle {bundle_id}")
            continue
        why_not = _researchable(b, bundles, today)
        if why_not is not None:
            sel.errors.append(f"request: bundle {bundle_id} is not researched ({why_not})")
            continue
        sel.subjects.append(
            PlannedSubject(
                key=bundle_key(bundle_id), kind="bundle", reason="requested", bundle_id=bundle_id
            )
        )
    return sel

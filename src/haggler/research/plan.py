"""Which subjects a research run looks at (pure). CONTRACT.

* Freshness windows (declared per kind in research.kinds): a service is due after SERVICE_DAYS,
  a pair after PAIR_DAYS, an instrument after INSTRUMENT_DAYS (since its last successful
  research). A subject that failed PAUSE_AFTER_FAILURES runs in a row is paused for PAUSE_DAYS.
* Pairs are (service, country) offers worth at least MIN_SAVING_EUR a month AND MIN_SAVING_PCT
  of the home price against ANY catalog country as residence (visitors describe their own
  profile on the site, so a pair matters as soon as it saves someone living anywhere), computed
  with compare.cheapest WITHOUT feasibility (every verdict rank 0) on the latest published
  prices. Pair facts do not depend on the profile, so one research serves every profile. A pair
  curated for every public profile of that residence is skipped (residences without a public
  profile have no curated records). Highest saving first.
* Services: first those the selected pairs need (no fresh service facts), then other due
  priced services in catalog order.
* Instruments: due instruments with official domains that some public profile does not hold
  yet, never-researched first.
* Caps per run: Limits.max_pairs, max_services, max_instruments.
* Monthly guard: nothing is planned once the search queries spent in the current calendar month
  (research/usage/, real clock, dry runs included) reach Limits.max_month_queries.
* An explicit request (CLI filters) replaces the automatic choice and ignores freshness and
  pauses, but not the monthly guard.
* Bundles are planned by haggler.bundles.plan (after the bundle watcher, last in the run);
  Request.bundles only makes the request explicit here.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass, field
from datetime import date, timedelta
from decimal import Decimal

from haggler.compare import cheapest
from haggler.feasibility import Verdict, find_record
from haggler.models.catalog import Catalog
from haggler.models.curated import FeasibilityFile, PaymentInstrumentsFile
from haggler.models.fx import FxTable
from haggler.models.observations import PublishedPrice
from haggler.models.profiles import ProfilesFile
from haggler.models.research import ResearchState, SubjectFacts, SubjectKind, subject_key
from haggler.research.kinds import KINDS, kind_of

# Freshness windows (days), declared per kind in research.kinds.
_WINDOWS: dict[SubjectKind, int] = {kind: spec.window_days for kind, spec in KINDS.items()}
SERVICE_DAYS = _WINDOWS["service"]
PAIR_DAYS = _WINDOWS["pair"]
INSTRUMENT_DAYS = _WINDOWS["instrument"]
PAUSE_AFTER_FAILURES = 3
PAUSE_DAYS = 28
MIN_SAVING_EUR = Decimal("1")
MIN_SAVING_PCT = Decimal("15")


@dataclass(frozen=True)
class Limits:
    max_pairs: int = 6
    max_services: int = 3
    max_instruments: int = 2
    max_month_queries: int = 1500


@dataclass(frozen=True)
class PlannedSubject:
    key: str
    kind: SubjectKind
    reason: str
    service_id: str | None = None
    country: str | None = None
    instrument_id: str | None = None
    saving_eur: Decimal | None = None
    bundle_id: str | None = None

    def as_dict(self) -> dict[str, object]:
        out: dict[str, object] = {
            "key": self.key,
            "kind": self.kind,
            "reason": self.reason,
            "service_id": self.service_id,
            "country": self.country,
            "instrument_id": self.instrument_id,
            "saving_eur": str(self.saving_eur) if self.saving_eur is not None else None,
        }
        if self.bundle_id is not None:
            out["bundle_id"] = self.bundle_id
        return out

    @classmethod
    def from_dict(cls, d: dict[str, object]) -> PlannedSubject:
        saving = d.get("saving_eur")
        return cls(
            key=str(d["key"]),
            kind=d["kind"],  # type: ignore[arg-type]
            reason=str(d.get("reason", "")),
            service_id=d.get("service_id"),  # type: ignore[arg-type]
            country=d.get("country"),  # type: ignore[arg-type]
            instrument_id=d.get("instrument_id"),  # type: ignore[arg-type]
            saving_eur=Decimal(str(saving)) if saving is not None else None,
            bundle_id=d.get("bundle_id"),  # type: ignore[arg-type]
        )


@dataclass(frozen=True)
class Request:
    """Explicit subjects from the CLI ("netflix", "netflix:CO", "revolut_eea",
    "meli_plus_co_total"). Bundles are planned by haggler.bundles.plan; naming one still makes
    the whole request explicit (no automatic subject of any kind)."""

    services: tuple[str, ...] = ()
    pairs: tuple[tuple[str, str], ...] = ()
    instruments: tuple[str, ...] = ()
    bundles: tuple[str, ...] = ()

    @property
    def explicit(self) -> bool:
        return bool(self.services or self.pairs or self.instruments or self.bundles)


@dataclass
class Selection:
    subjects: list[PlannedSubject] = field(default_factory=list)
    # Subject key (or "*") -> why it was not planned.
    skipped: dict[str, str] = field(default_factory=dict)
    errors: list[str] = field(default_factory=list)


def month_of(day: date) -> str:
    return day.strftime("%Y-%m")


def due(
    key: str,
    facts: dict[str, SubjectFacts],
    state: ResearchState,
    today: date,
    *,
    window: int | None = None,
) -> str | None:
    """Why the subject is due ("new", "stale"), or None when it is fresh or paused. `window`
    replaces the kind's freshness window (haggler.bundles.plan shortens a bundle's)."""
    st = state.subjects.get(key)
    if st is not None and st.paused_until is not None and st.paused_until > today:
        return None
    last = st.last_success if st is not None else None
    if last is None and key in facts:
        last = facts[key].researched_on
    if last is None:
        return "new"
    if (today - last).days >= (window if window is not None else _WINDOWS[kind_of(key)]):
        return "stale"
    return None


def _rank0(service_id: str, country: str) -> Verdict:
    return Verdict(label="any", rank=0, eu_badge=False, record_id=None)


def pair_savings(
    prices: list[PublishedPrice],
    fx: FxTable | None,
    catalog: Catalog,
    feasibility: FeasibilityFile,
    profiles: ProfilesFile,
    *,
    fx_fee_pct: float = 2.0,
) -> dict[tuple[str, str], Decimal]:
    """(service, country) -> best monthly EUR saving over any plan and any catalog country as
    residence, for pairs above both thresholds and not curated for every public profile living
    there."""
    out: dict[tuple[str, str], Decimal] = {}
    if fx is None or not prices:
        return out
    public = profiles.public()
    for residence in sorted(catalog.country_codes()):
        same = [p for p in public if p.residence_country == residence]
        try:
            rows = cheapest(
                prices,
                fx=fx,
                catalog=catalog,
                feasibility=feasibility,
                residence=residence,
                profile=same[0].id if same else "custom",
                fx_fee_pct=fx_fee_pct,
                verdicts=_rank0,
            )
        except ValueError:
            continue
        for row in rows:
            if row.home is None or row.home.cost_eur_with_fee <= 0:
                continue
            home = row.home.cost_eur_with_fee
            for offer in row.all_offers:
                if offer.country == residence:
                    continue
                saving = home - offer.cost_eur_with_fee
                if saving < MIN_SAVING_EUR or saving * 100 / home < MIN_SAVING_PCT:
                    continue
                if same and all(
                    find_record(
                        feasibility,
                        row.service_id,
                        offer.country,
                        residence_country=residence,
                        profile=p.id,
                    )
                    for p in same
                ):
                    continue
                key = (row.service_id, offer.country)
                if saving > out.get(key, Decimal(0)):
                    out[key] = saving
    return out


def select(
    *,
    catalog: Catalog,
    feasibility: FeasibilityFile,
    instruments: PaymentInstrumentsFile,
    profiles: ProfilesFile,
    prices: list[PublishedPrice],
    fx: FxTable | None,
    facts: dict[str, SubjectFacts],
    state: ResearchState,
    today: date,
    limits: Limits | None = None,
    request: Request | None = None,
    fx_fee_pct: float = 2.0,
    month_queries_used: int = 0,
    month: str | None = None,
) -> Selection:
    limits = limits or Limits()
    request = request or Request()
    sel = Selection()
    used = month_queries_used
    if used >= limits.max_month_queries:
        sel.skipped["*"] = (
            f"monthly guard: {used} search queries in {month or month_of(today)} "
            f"(>= {limits.max_month_queries})"
        )
        return sel
    if request.explicit:
        return _explicit(catalog, instruments, request, sel)

    savings = pair_savings(prices, fx, catalog, feasibility, profiles, fx_fee_pct=fx_fee_pct)
    pairs: list[PlannedSubject] = []
    for (sid, cc), saving in sorted(savings.items(), key=lambda kv: (-kv[1], kv[0])):
        key = subject_key("pair", sid, cc)
        why = due(key, facts, state, today)
        if why is None:
            sel.skipped[key] = "fresh or paused"
            continue
        if len(pairs) >= limits.max_pairs:
            sel.skipped[key] = "over the per-run pair cap"
            continue
        pairs.append(
            PlannedSubject(
                key=key,
                kind="pair",
                reason=f"{why}; saving {saving} EUR/month",
                service_id=sid,
                country=cc,
                saving_eur=saving,
            )
        )

    services: list[PlannedSubject] = []
    wanted = [p.service_id for p in pairs if p.service_id]
    # Then the priced services (research on a service without prices changes no verdict).
    wanted += [s.id for s in catalog.services if s.sources]
    for sid in dict.fromkeys(wanted):
        key = subject_key("service", sid)
        why = due(key, facts, state, today)
        if why is None:
            continue
        if len(services) >= limits.max_services:
            sel.skipped[key] = "over the per-run service cap"
            continue
        needed = " (needed by a selected pair)" if sid in {p.service_id for p in pairs} else ""
        services.append(
            PlannedSubject(key=key, kind="service", reason=why + needed, service_id=sid)
        )

    public = profiles.public()
    held_by_all = set.intersection(*(set(p.instruments_held) for p in public)) if public else set()
    candidates: list[tuple[int, str, PlannedSubject]] = []
    for inst in instruments.instruments:
        if not inst.official_domains or inst.id in held_by_all:
            continue
        key = subject_key("instrument", inst.id)
        why = due(key, facts, state, today)
        if why is None:
            continue
        candidates.append(
            (
                0 if why == "new" else 1,
                inst.id,
                PlannedSubject(key=key, kind="instrument", reason=why, instrument_id=inst.id),
            )
        )
    for i, (_, _, subject) in enumerate(sorted(candidates, key=lambda c: (c[0], c[1]))):
        if i >= limits.max_instruments:
            sel.skipped[subject.key] = "over the per-run instrument cap"
            continue
        sel.subjects.append(subject)
    sel.subjects = [*pairs, *services, *sel.subjects]
    return sel


def _explicit(
    catalog: Catalog, instruments: PaymentInstrumentsFile, request: Request, sel: Selection
) -> Selection:
    service_ids = {s.id for s in catalog.services}
    ccs = set(catalog.country_codes())
    inst_ids = {i.id for i in instruments.instruments}
    for sid, cc in request.pairs:
        if sid not in service_ids or cc not in ccs:
            sel.errors.append(f"request: unknown pair {sid}:{cc}")
            continue
        sel.subjects.append(
            PlannedSubject(
                key=subject_key("pair", sid, cc),
                kind="pair",
                reason="requested",
                service_id=sid,
                country=cc,
            )
        )
    for sid in request.services:
        if sid not in service_ids:
            sel.errors.append(f"request: unknown service {sid}")
            continue
        sel.subjects.append(
            PlannedSubject(
                key=subject_key("service", sid), kind="service", reason="requested", service_id=sid
            )
        )
    for iid in request.instruments:
        if iid not in inst_ids:
            sel.errors.append(f"request: unknown instrument {iid}")
            continue
        sel.subjects.append(
            PlannedSubject(
                key=subject_key("instrument", iid),
                kind="instrument",
                reason="requested",
                instrument_id=iid,
            )
        )
    sel.subjects = list({s.key: s for s in sel.subjects}.values())
    return sel


def parse_pairs(values: Iterable[str]) -> tuple[tuple[str, str], ...]:
    """ "netflix:CO" -> ("netflix", "CO")."""
    out: list[tuple[str, str]] = []
    for v in values:
        sid, _, cc = v.strip().partition(":")
        if not sid or len(cc) != 2:
            raise ValueError(f"pair must look like service:CC, got {v!r}")
        out.append((sid, cc.upper()))
    return tuple(out)


def pause_after(today: date) -> date:
    return today + timedelta(days=PAUSE_DAYS)

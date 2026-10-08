"""Deterministic validation of today's observations. CONTRACT (see validate()).

Rules (thresholds from Settings):
  REJECT (status=rejected, kept only in the snapshot):
    * currency not in CountryDef.expected_currencies(channel) — plus the country's
      appstore_currency when the draft carries flag "apple_billing" (Apple's own services are
      billed in the storefront currency), plus params.currency_overrides[country] of the catalog
      source (same service, adapter and channel) that produced the row  -> flag currency_mismatch
    * monthly EUR outside PlanDef.plausible_monthly_eur (needs FX)       -> flag implausible
    * amount <= 0                                                        -> flag non_positive
    * unknown service/plan id                                            -> flag unknown_plan
  QUARANTINE (status=quarantined; the previously published row stays live):
    * |change| > settings.quarantine_change_pct vs previous published amount (same key,
      same currency)                                                     -> flag big_change
    * tier order broken: within (service, country, channel, period) a higher-rank non-ads plan
      is cheaper than a lower-rank non-ads plan                          -> flag tier_order
    * key never published before and extraction == "llm" and no secondary row agrees
                                                                         -> flag llm_new_key
  RELEASE from quarantine -> accepted:
    * KeyState.pending_value equals today's value (seen two runs in a row), or
    * the observation carries flag "second_opinion_agrees" (set by the adjudicate node).
  FLAGS only:
    * secondary_disagrees: official and secondary (source_kind) rows for the same key differ >2%
  SECONDARY rows: when an official row exists for the key, the secondary row is only used for
    the comparison above and dropped from the output. Without a usable official row today:
    * key last published by an official source -> the official row is carried forward (counters
      below) and the secondary row is compared with it, then dropped; the secondary row becomes
      the output row only on the run where the official row would be dropped (aged out);
    * otherwise (never published, or published from the secondary) it is the output row.
  CARRY FORWARD (status=carried_forward, copied from previous published row):
    * key published before, absent today, and its task did not succeed -> missing_runs += 1;
      dropped once missing_runs > 7
    * key absent while its task succeeded -> absent_ok_runs += 1; carried until > 3, then dropped

Details the rules above leave open (decided here, covered by tests/unit/test_validate.py):
  * monthly_eur = FxTable.to_eur(monthly_equiv). Without a rate the plausibility check is
    skipped and the row gets flag "no_fx" (one Anomaly per missing currency, not per row).
  * A row from an unknown country is rejected with flag "unknown_country".
  * Input rows the pipeline already rejected (e.g. LLM post-checks) stay rejected. Input rows
    with status carried_forward are ignored and flags derived here are stripped from the rest, so
    the adjudicate node can re-run validate() on rows that already went through it after adding
    "second_opinion_agrees" (same previous/state/tasks). The output drops secondary rows, so
    pass today's original rows again when secondary sources are involved.
  * One live row per key: the best official row that passed the reject rules (parser before llm,
    then higher confidence), else the best secondary row (subject to the SECONDARY rule above).
    A quarantined official row still wins over the secondary row, so a quarantine is never
    bypassed by a stale secondary value. Rows that lose the selection are dropped; rejected rows
    always stay in the snapshot.
  * Rows are compared in the same currency only (big_change, tier_order, secondary checks).
  * tier_order quarantines only the rows of a violating pair whose value is new or differs from
    the published one: an order that was already published (and so confirmed) is not re-flagged
    every day, and the release rule cannot loop.
  * On release the quarantine flags are removed from the row (its Anomaly says it was released).
    A quarantined row stores pending_value "amount|currency|period" + pending_since in KeyState;
    pending_value survives runs where the key is absent, so two sightings around a failed fetch
    still confirm a value.
  * A key whose rows were all rejected today counts as missing (as if its task had failed); so
    does an official row carried behind a secondary row when today's official rows were rejected.
  * "Its task" = the TaskResults whose service_ids/countries/channel cover the key, restricted to
    the adapter of the previous published row when one of them is from that adapter (so a
    secondary dataset that lacks the plan does not count as the official page succeeding). For
    an official row carried behind a secondary row, only official-source tasks count: a run
    without them (e.g. --adapters tompec_netflix) leaves the row and its state untouched.
    Success = status in ok|partial|not_available (any of those tasks).
  * Keys no task covers today (a run filtered with --services/--countries/--adapters) are
    re-emitted unchanged with their published status and keep their KeyState. Keys whose
    service/plan/country left the catalog, or that no source covers any more, are dropped.
  * Counters reset only when the key is observed again (accepted or quarantined), except for a
    quarantined secondary row that replaced an aged-out official row: it keeps the counters, so
    the official row is not carried through a second full cycle while the quarantine waits.
  * key_state holds an entry for every key still tracked (defaults included), so the pipeline
    can replace or merge PipelineState.keys with it.
  * Outputs are sorted by obs_key (observations, review_queue, anomalies).
"""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Iterable
from dataclasses import dataclass, field
from datetime import UTC, date, datetime, time
from decimal import Decimal, InvalidOperation

from haggler.config import Settings
from haggler.models.catalog import Catalog, Channel, CountryDef, ServiceDef, SourceKind
from haggler.models.fx import FxTable
from haggler.models.observations import ObsStatus, PriceObservation, PublishedPrice
from haggler.models.run import Anomaly, KeyState, PipelineState, TaskResult

# Flags (also the Anomaly.rule names).
FLAG_UNKNOWN_PLAN = "unknown_plan"
FLAG_UNKNOWN_COUNTRY = "unknown_country"
FLAG_NON_POSITIVE = "non_positive"
FLAG_CURRENCY_MISMATCH = "currency_mismatch"
FLAG_IMPLAUSIBLE = "implausible"
FLAG_BIG_CHANGE = "big_change"
FLAG_TIER_ORDER = "tier_order"
FLAG_LLM_NEW_KEY = "llm_new_key"
FLAG_SECONDARY_DISAGREES = "secondary_disagrees"
FLAG_NO_FX = "no_fx"
FLAG_QUARANTINE_HOLD = "quarantine_hold"
# Set by others; read here.
FLAG_APPLE_BILLING = "apple_billing"
FLAG_SECOND_OPINION_AGREES = "second_opinion_agrees"

REJECT_FLAGS = (
    FLAG_UNKNOWN_PLAN,
    FLAG_UNKNOWN_COUNTRY,
    FLAG_NON_POSITIVE,
    FLAG_CURRENCY_MISMATCH,
    FLAG_IMPLAUSIBLE,
)
# In precedence order: the first one found is the review_queue entry's "rule".
QUARANTINE_FLAGS = (FLAG_BIG_CHANGE, FLAG_TIER_ORDER, FLAG_LLM_NEW_KEY)
# Flags computed by this module; stripped from non-rejected inputs before re-validation.
DERIVED_FLAGS = frozenset(
    {
        *REJECT_FLAGS,
        *QUARANTINE_FLAGS,
        FLAG_SECONDARY_DISAGREES,
        FLAG_NO_FX,
        FLAG_QUARANTINE_HOLD,
    }
)

SUCCESS_STATUSES = frozenset({"ok", "partial", "not_available"})
SECONDARY_TOLERANCE_PCT = Decimal(2)
MAX_MISSING_RUNS = 7
MAX_ABSENT_OK_RUNS = 3

_STATUS_ORDER: dict[str, int] = {
    "accepted": 0,
    "quarantined": 1,
    "carried_forward": 2,
    "rejected": 3,
}


@dataclass
class ValidationOutcome:
    # Final rows for today: accepted + quarantined + carried_forward (+ rejected, for the snapshot).
    observations: list[PriceObservation]
    anomalies: list[Anomaly] = field(default_factory=list)
    # Quarantined rows: {"obs_key", "rule", "previous": PublishedPrice dump | None, "current": dump}
    review_queue: list[dict] = field(default_factory=list)
    # New per-key state to persist.
    key_state: dict[str, KeyState] = field(default_factory=dict)


# ----------------------------------------------------------------------------- helpers


def _plain(amount: Decimal) -> str:
    """Decimal without exponent or trailing zeros: 129.00 -> "129", 1E+2 -> "100"."""
    return format(amount.normalize(), "f")


def _value_token(amount: Decimal, currency: str, period: str) -> str:
    return f"{_plain(amount)}|{currency}|{period}"


def _token_matches(token: str | None, row: PriceObservation) -> bool:
    if not token:
        return False
    parts = token.split("|")
    if len(parts) != 3:
        return False
    try:
        amount = Decimal(parts[0])
    except InvalidOperation:
        return False
    return amount == row.amount and parts[1] == row.currency and parts[2] == row.billing_period


def _fmt(amount: Decimal, currency: str, period: str) -> str:
    return f"{amount} {currency}/{period}"


def _pct_change(new: Decimal, old: Decimal) -> Decimal:
    return abs(new - old) / old * 100


def _allowed_currencies(
    country: CountryDef, channel: Channel, flags: Iterable[str], pinned: Iterable[str] = ()
) -> list[str]:
    allowed = list(country.expected_currencies(channel))
    extra = [country.appstore_currency] if FLAG_APPLE_BILLING in flags else []
    for currency in (*extra, *pinned):
        if currency and currency not in allowed:
            allowed.append(currency)
    return allowed


def _pinned_currencies(svc: ServiceDef | None, obs: PriceObservation) -> list[str]:
    """params.currency_overrides[country] (a code or a list) of the sources behind the row."""
    out: list[str] = []
    for src in svc.sources if svc else []:
        if src.adapter != obs.adapter_id or src.channel != obs.channel:
            continue
        pinned = (src.params.get("currency_overrides") or {}).get(obs.country)
        if pinned:
            out.extend([pinned] if isinstance(pinned, str) else [str(c) for c in pinned])
    return out


def _source_kind(svc: ServiceDef | None, adapter_id: str, channel: str) -> SourceKind:
    for src in svc.sources if svc else []:
        if src.adapter == adapter_id and src.channel == channel:
            return src.kind
    return "official"


def _with_flags(flags: list[str], extra: Iterable[str]) -> list[str]:
    out = list(flags)
    for flag in extra:
        if flag not in out:
            out.append(flag)
    return out


def _pick(rows: list[PriceObservation]) -> PriceObservation:
    return min(
        rows,
        key=lambda r: (
            r.extraction != "parser",
            -r.confidence,
            r.amount,
            r.adapter_id,
            r.source_url,
        ),
    )


def _sort_key(row: PriceObservation) -> tuple:
    return (
        row.obs_key,
        _STATUS_ORDER.get(row.status, 9),
        row.source_kind,
        row.adapter_id,
        row.amount,
        row.source_url,
    )


def _split_key(key: str) -> tuple[str, str, str, str, str] | None:
    parts = key.split(":")
    if len(parts) != 5:
        return None
    return parts[0], parts[1], parts[2], parts[3], parts[4]


def _from_published(
    prev: PublishedPrice, *, status: ObsStatus, flags: list[str], monthly_eur: Decimal | None
) -> PriceObservation:
    """A carried row: today's copy of a published row (snapshot fields it lacks are neutral)."""
    return PriceObservation(
        obs_key=prev.obs_key,
        service_id=prev.service_id,
        plan_id=prev.plan_id,
        plan_label_raw=prev.plan_label_raw,
        country=prev.country,
        currency=prev.currency,
        amount=prev.amount,
        billing_period=prev.billing_period,
        monthly_equiv=prev.monthly_equiv,
        monthly_eur=monthly_eur,
        vat_included=prev.vat_included,
        channel=prev.channel,
        source_url=prev.source_url,
        source_kind=prev.source_kind,
        adapter_id=prev.adapter_id,
        observed_at=datetime.combine(prev.last_changed, time.min, tzinfo=UTC),
        content_hash="",
        extraction=prev.extraction,
        confidence=prev.confidence,
        status=status,
        flags=flags,
    )


@dataclass
class _Checked:
    row: PriceObservation
    reasons: list[tuple[str, str]]
    missing_fx: bool


def _check_row(
    obs: PriceObservation,
    services: dict[str, ServiceDef],
    countries: dict[str, CountryDef],
    fx: FxTable,
) -> _Checked:
    """Reject rules for one row; returns a copy with monthly_eur, status and flags set."""
    externally_rejected = obs.status == "rejected"
    flags = (
        list(obs.flags) if externally_rejected else [f for f in obs.flags if f not in DERIVED_FLAGS]
    )
    monthly_eur = fx.to_eur(obs.monthly_equiv, obs.currency)
    reasons: list[tuple[str, str]] = []

    svc = services.get(obs.service_id)
    plan = svc.plan(obs.plan_id) if svc else None
    country = countries.get(obs.country)
    if plan is None:
        reasons.append((FLAG_UNKNOWN_PLAN, f"{obs.service_id}:{obs.plan_id} is not in the catalog"))
    if country is None:
        reasons.append((FLAG_UNKNOWN_COUNTRY, f"{obs.country} is not in the catalog"))
    if obs.amount <= 0:
        reasons.append((FLAG_NON_POSITIVE, f"amount {obs.amount}"))
    if country is not None:
        allowed = _allowed_currencies(country, obs.channel, obs.flags, _pinned_currencies(svc, obs))
        if obs.currency not in allowed:
            reasons.append((FLAG_CURRENCY_MISMATCH, f"{obs.currency} not in {'/'.join(allowed)}"))

    missing_fx = False
    if plan is not None and not reasons:
        if monthly_eur is None:
            missing_fx = True
            flags = _with_flags(flags, [FLAG_NO_FX])
        else:
            lo, hi = (Decimal(str(b)) for b in plan.plausible_monthly_eur)
            if not lo <= monthly_eur <= hi:
                reasons.append((FLAG_IMPLAUSIBLE, f"{monthly_eur} EUR/month outside [{lo}, {hi}]"))

    rejected = externally_rejected or bool(reasons)
    row = obs.model_copy(
        update={
            "monthly_eur": monthly_eur,
            "status": "rejected" if rejected else "accepted",
            "flags": _with_flags(flags, (f for f, _ in reasons)),
        }
    )
    return _Checked(row=row, reasons=reasons, missing_fx=missing_fx)


def _changed(row: PriceObservation, prev: PublishedPrice | None) -> bool:
    return prev is None or prev.currency != row.currency or prev.amount != row.amount


def _tier_violations(
    live: dict[str, PriceObservation],
    previous: dict[str, PublishedPrice],
    services: dict[str, ServiceDef],
) -> dict[str, list[str]]:
    """obs_key -> details, for changed rows that break the plan rank order of their group."""
    groups: dict[tuple, list[tuple[int, PriceObservation]]] = defaultdict(list)
    for row in live.values():
        svc = services.get(row.service_id)
        plan = svc.plan(row.plan_id) if svc else None
        if plan is None or plan.ads:
            continue
        group = (row.service_id, row.country, row.channel, row.billing_period, row.currency)
        groups[group].append((plan.rank, row))

    found: dict[str, list[str]] = defaultdict(list)
    for members in groups.values():
        members.sort(key=lambda m: (m[0], m[1].obs_key))
        for lo_rank, lo in members:
            for hi_rank, hi in members:
                if not (lo_rank < hi_rank and hi.amount < lo.amount):
                    continue
                detail = (
                    f"{hi.plan_id} (rank {hi_rank}) {hi.amount} < "
                    f"{lo.plan_id} (rank {lo_rank}) {lo.amount} {lo.currency}"
                )
                for row in (lo, hi):
                    if (
                        _changed(row, previous.get(row.obs_key))
                        and detail not in found[row.obs_key]
                    ):
                        found[row.obs_key].append(detail)
    return dict(found)


def _covering_tasks(
    task_results: list[TaskResult],
    service_id: str,
    country: str,
    channel: str,
    adapter_id: str | None,
) -> list[TaskResult]:
    covering = [
        t
        for t in task_results
        if service_id in t.service_ids and country in t.countries and t.channel == channel
    ]
    if adapter_id:
        same = [t for t in covering if t.adapter_id == adapter_id]
        if same:
            return same
    return covering


def _absent_state(
    old: KeyState, tasks: list[TaskResult], *, failed: bool = False, same_day: bool = False
) -> KeyState | None:
    """Counters after a run without a live row for a tracked key (`tasks` = its covering tasks);
    None when no task of this run covers it (the key is left as it was).

    A same-day re-run (Cloud Run retry, manual re-execution) already counted today, so the
    counters stay as they are: retries must not age keys out faster.
    """
    if not failed:
        if not tasks:
            return None
        failed = not any(t.status in SUCCESS_STATUSES for t in tasks)
    if same_day:
        return old
    return old.model_copy(
        update={"missing_runs": old.missing_runs + 1}
        if failed
        else {"absent_ok_runs": old.absent_ok_runs + 1}
    )


def _aged_out(state: KeyState) -> bool:
    return state.missing_runs > MAX_MISSING_RUNS or state.absent_ok_runs > MAX_ABSENT_OK_RUNS


def _carried_flags(prev: PublishedPrice, secondary_disagrees: bool | None) -> list[str]:
    """Flags of a carried row; secondary_disagrees follows today's comparison when there is one."""
    flags = list(prev.flags)
    if secondary_disagrees is None:
        return flags
    flags = [f for f in flags if f != FLAG_SECONDARY_DISAGREES]
    return _with_flags(flags, [FLAG_SECONDARY_DISAGREES]) if secondary_disagrees else flags


def _still_configured(
    services: dict[str, ServiceDef],
    countries: dict[str, CountryDef],
    service_id: str,
    plan_id: str,
    country: str,
    channel: str,
) -> bool:
    svc = services.get(service_id)
    if svc is None or svc.plan(plan_id) is None or country not in countries:
        return False
    return any(src.channel == channel and src.applies_to(country) for src in svc.sources)


# ----------------------------------------------------------------------------- validate


def validate(
    current: list[PriceObservation],
    *,
    previous: dict[str, PublishedPrice],
    state: PipelineState,
    task_results: list[TaskResult],
    catalog: Catalog,
    fx: FxTable,
    run_date: date,
    settings: Settings,
) -> ValidationOutcome:
    """Apply the rules above. Pure function: no I/O."""
    services = {s.id: s for s in catalog.services}
    countries = {c.cc: c for c in catalog.countries}
    threshold = Decimal(str(settings.quarantine_change_pct))
    # The stored state was written by an earlier run on this same date (retry / re-execution).
    same_day = state.as_of == run_date
    anomalies: list[Anomaly] = []
    output: list[PriceObservation] = []

    # 1. Reject rules, row by row.
    rows_by_key: dict[str, list[PriceObservation]] = defaultdict(list)
    no_fx: dict[str, list[str]] = defaultdict(list)
    for obs in current:
        if obs.status == "carried_forward":
            continue
        checked = _check_row(obs, services, countries, fx)
        row = checked.row
        rows_by_key[row.obs_key].append(row)
        if checked.missing_fx:
            no_fx[row.currency].append(row.obs_key)
        value = _fmt(row.amount, row.currency, row.billing_period)
        for rule, detail in checked.reasons:
            anomalies.append(Anomaly(obs_key=row.obs_key, rule=rule, detail=detail, current=value))
    for currency, keys in no_fx.items():
        anomalies.append(
            Anomaly(
                obs_key=min(keys),
                rule=FLAG_NO_FX,
                detail=f"no FX rate for {currency}: plausibility not checked on {len(keys)} row(s)",
            )
        )

    # 2. One live row per key; secondary rows are compared, then dropped.
    live: dict[str, PriceObservation] = {}
    secondary_agrees: set[str] = set()
    # Keys whose published official row is carried forward behind today's secondary row:
    # key -> its next KeyState (None = no official task this run), and today's comparison.
    held: dict[str, KeyState | None] = {}
    held_disagrees: dict[str, bool] = {}
    # Secondary rows that replace an aged-out official row: key -> the official row's counters.
    fallback: dict[str, KeyState] = {}
    for key, rows in rows_by_key.items():
        output.extend(r for r in rows if r.status == "rejected")
        official = [r for r in rows if r.status != "rejected" and r.source_kind == "official"]
        secondary = [r for r in rows if r.status != "rejected" and r.source_kind == "secondary"]
        if not official:
            if not secondary:
                continue
            sec = _pick(secondary)
            prev = previous.get(key)
            if prev is not None and prev.source_kind == "official":
                svc = services.get(prev.service_id)
                tasks = [
                    t
                    for t in _covering_tasks(
                        task_results, prev.service_id, prev.country, prev.channel, prev.adapter_id
                    )
                    if _source_kind(svc, t.adapter_id, t.channel) == "official"
                ]
                rejected_official = any(r.source_kind == "official" for r in rows)
                old = state.keys.get(key) or KeyState()
                nxt = _absent_state(old, tasks, failed=rejected_official, same_day=same_day)
                if nxt is not None and _aged_out(nxt):
                    fallback[key] = nxt
                else:
                    held[key] = nxt
                    if sec.currency == prev.currency and prev.amount > 0:
                        diff = _pct_change(sec.amount, prev.amount)
                        held_disagrees[key] = diff > SECONDARY_TOLERANCE_PCT
                        if held_disagrees[key]:
                            anomalies.append(
                                Anomaly(
                                    obs_key=key,
                                    rule=FLAG_SECONDARY_DISAGREES,
                                    detail=(
                                        f"{sec.adapter_id} says {sec.amount} ({diff:.1f}% off); "
                                        f"official {prev.adapter_id} value carried forward"
                                    ),
                                    previous=_fmt(sec.amount, sec.currency, sec.billing_period),
                                    current=_fmt(prev.amount, prev.currency, prev.billing_period),
                                )
                            )
                    continue
            live[key] = sec
            continue
        row = _pick(official)
        live[key] = row
        if not secondary:
            continue
        sec = _pick(secondary)
        if sec.currency != row.currency:
            continue
        diff = _pct_change(sec.amount, row.amount)
        if diff > SECONDARY_TOLERANCE_PCT:
            row.flags = _with_flags(row.flags, [FLAG_SECONDARY_DISAGREES])
            anomalies.append(
                Anomaly(
                    obs_key=key,
                    rule=FLAG_SECONDARY_DISAGREES,
                    detail=f"{sec.adapter_id} says {sec.amount} ({diff:.1f}% off)",
                    previous=_fmt(sec.amount, sec.currency, sec.billing_period),
                    current=_fmt(row.amount, row.currency, row.billing_period),
                )
            )
        else:
            secondary_agrees.add(key)

    # 3. Quarantine rules.
    reasons: dict[str, list[tuple[str, str]]] = defaultdict(list)
    for key, row in live.items():
        prev = previous.get(key)
        if prev is not None and prev.currency == row.currency and prev.amount > 0:
            pct = _pct_change(row.amount, prev.amount)
            if pct > threshold:
                reasons[key].append((FLAG_BIG_CHANGE, f"{pct:.1f}% vs published {prev.amount}"))
        if prev is None and row.extraction == "llm" and key not in secondary_agrees:
            reasons[key].append((FLAG_LLM_NEW_KEY, "new key seen only by the LLM"))
    for key, details in _tier_violations(live, previous, services).items():
        reasons[key].append((FLAG_TIER_ORDER, "; ".join(details)))

    # 4. Release or quarantine; per-key state for observed keys.
    review_queue: list[dict] = []
    key_state: dict[str, KeyState] = {}
    for key in sorted(live):
        row = live[key]
        output.append(row)
        found = sorted(reasons.get(key, []), key=lambda r: QUARANTINE_FLAGS.index(r[0]))
        if not found:
            key_state[key] = KeyState()
            continue
        old = state.keys.get(key)
        if FLAG_SECOND_OPINION_AGREES in row.flags:
            released = "released: second opinion agrees"
        elif (
            old is not None
            and _token_matches(old.pending_value, row)
            and (old.pending_since is None or old.pending_since < run_date)
        ):
            # Confirmation needs a LATER run: a same-day retry re-reads the same page.
            released = "released: same value as the previous run"
        else:
            released = ""
        prev = previous.get(key)
        for rule, detail in found:
            anomalies.append(
                Anomaly(
                    obs_key=key,
                    rule=rule,
                    detail=f"{detail}; {released or 'quarantined'}",
                    previous=_fmt(prev.amount, prev.currency, prev.billing_period)
                    if prev
                    else None,
                    current=_fmt(row.amount, row.currency, row.billing_period),
                )
            )
        if released:
            key_state[key] = KeyState()
            continue
        rules = [rule for rule, _ in found]
        row.status = "quarantined"
        row.flags = _with_flags(row.flags, rules)
        key_state[key] = (fallback.get(key) or KeyState()).model_copy(
            update={
                "pending_value": _value_token(row.amount, row.currency, row.billing_period),
                "pending_since": run_date,
            }
        )
        review_queue.append(
            {
                "obs_key": key,
                "rule": rules[0],
                "rules": rules,
                "previous": prev.model_dump(mode="json") if prev else None,
                "current": row.model_dump(mode="json"),
            }
        )

    # 5. Carry forward published keys that are missing today.
    for key in sorted(set(previous) | set(state.keys)):
        if key in live:
            continue
        prev = previous.get(key)
        old = state.keys.get(key) or KeyState()
        parts = (
            (prev.service_id, prev.plan_id, prev.country, prev.channel, prev.billing_period)
            if prev
            else _split_key(key)
        )
        if parts is None or not _still_configured(services, countries, *parts[:4]):
            continue  # left the catalog: drop row and state
        if key in held:
            new = held[key]  # official row carried behind today's secondary row (step 2)
        elif key in rows_by_key:
            new = _absent_state(
                old, [], failed=True, same_day=same_day
            )  # observed, but every row was rejected
        else:
            tasks = _covering_tasks(
                task_results, parts[0], parts[2], parts[3], prev.adapter_id if prev else None
            )
            new = _absent_state(old, tasks, same_day=same_day)
        if new is None:
            # Not attempted by this run: keep everything as it was.
            if key in state.keys:
                key_state[key] = old.model_copy()
            if prev is not None:
                status: ObsStatus = (
                    prev.status
                    if prev.status in ("accepted", "carried_forward")
                    else "carried_forward"
                )
                output.append(
                    _from_published(
                        prev,
                        status=status,
                        flags=_carried_flags(prev, held_disagrees.get(key)),
                        monthly_eur=fx.to_eur(prev.monthly_equiv, prev.currency),
                    )
                )
            continue
        if _aged_out(new):
            continue  # dropped
        key_state[key] = new
        if prev is not None:
            output.append(
                _from_published(
                    prev,
                    status="carried_forward",
                    flags=_carried_flags(prev, held_disagrees.get(key)),
                    monthly_eur=fx.to_eur(prev.monthly_equiv, prev.currency),
                )
            )

    output.sort(key=_sort_key)
    review_queue.sort(key=lambda e: e["obs_key"])
    anomalies.sort(key=lambda a: (a.obs_key, a.rule, a.detail))
    return ValidationOutcome(
        observations=output,
        anomalies=anomalies,
        review_queue=review_queue,
        key_state=dict(sorted(key_state.items())),
    )


def publishable(outcome: ValidationOutcome) -> list[PriceObservation]:
    """Rows that go to latest/prices.json: accepted, carried_forward, and for quarantined keys the
    previous published value (re-emitted with status carried_forward + flag 'quarantine_hold')."""
    holds = {entry["obs_key"]: entry.get("previous") for entry in outcome.review_queue}
    rows: dict[str, PriceObservation] = {}
    for obs in outcome.observations:
        if obs.status == "rejected" or obs.obs_key in rows:
            continue
        if obs.status != "quarantined":
            rows[obs.obs_key] = obs
            continue
        dump = holds.get(obs.obs_key)
        if not dump:
            continue  # new key: nothing published yet, nothing to hold
        prev = PublishedPrice.model_validate(dump)
        monthly_eur = None
        if obs.monthly_eur is not None and obs.currency == prev.currency and obs.monthly_equiv:
            # Same currency: reuse today's rate implied by the quarantined row.
            rate = obs.monthly_eur / obs.monthly_equiv
            monthly_eur = (prev.monthly_equiv * rate).quantize(Decimal("0.0001"))
        rows[obs.obs_key] = _from_published(
            prev,
            status="carried_forward",
            flags=_with_flags(prev.flags, [FLAG_QUARANTINE_HOLD]),
            monthly_eur=monthly_eur,
        )
    return [rows[key] for key in sorted(rows)]

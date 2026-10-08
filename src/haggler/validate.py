"""Deterministic validation of today's observations. CONTRACT (see validate()).

Rules (thresholds from Settings):
  REJECT (status=rejected, kept only in the snapshot):
    * currency not in CountryDef.expected_currencies(channel)            -> flag currency_mismatch
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
  SECONDARY rows: when an accepted official row exists for the key, the secondary row is only
    used for the comparison above and dropped from the output; otherwise it is the output row.
  CARRY FORWARD (status=carried_forward, copied from previous published row):
    * key published before, absent today, and its task did not succeed -> missing_runs += 1;
      dropped once missing_runs > 7
    * key absent while its task succeeded -> absent_ok_runs += 1; carried until > 3, then dropped
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date

from haggler.config import Settings
from haggler.models.catalog import Catalog
from haggler.models.fx import FxTable
from haggler.models.observations import PriceObservation, PublishedPrice
from haggler.models.run import Anomaly, KeyState, PipelineState, TaskResult


@dataclass
class ValidationOutcome:
    # Final rows for today: accepted + quarantined + carried_forward (+ rejected, for the snapshot).
    observations: list[PriceObservation]
    anomalies: list[Anomaly] = field(default_factory=list)
    # Quarantined rows: {"obs_key", "rule", "previous": PublishedPrice dump | None, "current": dump}
    review_queue: list[dict] = field(default_factory=list)
    # New per-key state to persist.
    key_state: dict[str, KeyState] = field(default_factory=dict)


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
    raise NotImplementedError


def publishable(outcome: ValidationOutcome) -> list[PriceObservation]:
    """Rows that go to latest/prices.json: accepted, carried_forward, and for quarantined keys the
    previous published value (re-emitted with status carried_forward + flag 'quarantine_hold')."""
    raise NotImplementedError

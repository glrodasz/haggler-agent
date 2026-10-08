"""Deterministic feasibility labels. CONTRACT.

Axis ranks:
  legality:  lawful=0, contestable_eu_right=1, requires_misrepresentation=2
  tos:       compliant=0, breach_unenforced=2, breach_enforced=3
  practical: remote=0, presence_once=1, presence_periodic=2, impossible=4
rank = max(axis ranks); any step with requires_false_declaration forces rank >= 2.
Labels by rank: 0 official_home, 1 eu_right_untested, 2 tos_breach_low_enforcement,
3 tos_breach_enforced, 4 not_feasible. No record -> "unknown" (rank UNKNOWN_RANK, never chosen as
cheapest). target_country == residence_country -> official_home (rank 0) without a record.
eu_badge(record) is True when any legal_basis has strength textual|arguable (secondary badge).
"""

from __future__ import annotations

from dataclasses import dataclass

from haggler.models.curated import FeasibilityFile, FeasibilityRecord

LABELS = (
    "official_home",
    "eu_right_untested",
    "tos_breach_low_enforcement",
    "tos_breach_enforced",
    "not_feasible",
)
UNKNOWN_RANK = 99


@dataclass(frozen=True)
class Verdict:
    label: str
    rank: int
    eu_badge: bool
    record_id: str | None


def record_rank(record: FeasibilityRecord) -> int:
    raise NotImplementedError


def verdict(
    service_id: str,
    target_country: str,
    *,
    residence_country: str,
    profile: str,
    feasibility: FeasibilityFile,
) -> Verdict:
    raise NotImplementedError

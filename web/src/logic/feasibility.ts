/**
 * Axis ranks of the feasibility verdict, mirrored from src/haggler/feasibility.py so the
 * methodology page explains exactly the rule the Python publish step applies.
 */

export const AXIS_RANKS: Record<"legality" | "tos" | "practical", Record<string, number>> = {
  legality: { lawful: 0, contestable_eu_right: 1, requires_misrepresentation: 2 },
  tos: { compliant: 0, breach_unenforced: 2, breach_enforced: 3 },
  practical: { remote: 0, presence_once: 1, presence_periodic: 2, impossible: 4 },
};

/** Worst axis wins; a false declaration in any step forces at least rank 2. */
export function recordRank(
  legality: string,
  tos: string,
  practical: string,
  requiresFalseDeclaration = false,
): number {
  const rank = Math.max(
    AXIS_RANKS.legality[legality] ?? 0,
    AXIS_RANKS.tos[tos] ?? 0,
    AXIS_RANKS.practical[practical] ?? 0,
  );
  return requiresFalseDeclaration ? Math.max(rank, 2) : rank;
}

/** Display order of coverage statuses (healthy first). */
export const COVERAGE_ORDER = [
  "ok",
  "partial",
  "not_available",
  "wrong_country",
  "blocked",
  "robots_disallowed",
  "failed",
  "not_configured",
];

/**
 * Verdicts per profile. offers.json carries the DEFAULT profile's labels with pending findings
 * excluded; the comparator relabels the same offers for the profile the visitor picks, with or
 * without the findings still pending review, from feasibility.json's records. Mirrors
 * verdict_fn in src/haggler/research/evaluate.py, as far as the published records allow:
 *
 *  - an offer of the profile's residence country is "official_home" (rank 0, no EU badge);
 *  - else the profile's record for (service, country): the agent_pending one when pending
 *    findings are included and one exists, else the non-pending one (curated / agent_reviewed);
 *  - no record -> "unknown" (rank 99, never chosen). site-data leaves out derived "unknown"
 *    verdicts of pairs nobody researched, and a pending record only when it differs, so this
 *    fallback reproduces offers.json exactly (verdicts.test.ts checks it).
 *
 * Pure and small, because the comparator island ships it to the browser.
 */

import type { FeasibilityView, LabelId, Offer, Profile, Provenance } from "./types";
import { UNKNOWN_RANK } from "./types";

/** The record fields a verdict needs (the island ships only these). */
export type VerdictRecord = Pick<
  FeasibilityView,
  "profile" | "service_id" | "target_country" | "provenance" | "label" | "rank" | "eu_badge"
>;

/** What verdicts are computed from: feasibility.json, or the subset the island ships. */
export interface VerdictSource {
  default_profile: string;
  profiles: readonly Pick<Profile, "id" | "residence_country">[];
  records: readonly VerdictRecord[];
}

export interface Verdict {
  label: LabelId;
  rank: number;
  eu_badge: boolean;
  /** "home" for the residence country, null when there is no record. */
  provenance: Provenance | "home" | null;
}

export type VerdictFields = Pick<Offer, "service_id" | "country" | "label" | "rank" | "eu_badge">;

const HOME: Verdict = { label: "official_home", rank: 0, eu_badge: false, provenance: "home" };
const UNKNOWN: Verdict = { label: "unknown", rank: UNKNOWN_RANK, eu_badge: false, provenance: null };

export function isPending(record: Pick<FeasibilityView, "provenance">): boolean {
  return record.provenance === "agent_pending";
}

/** The profile with this id, else the default one (an id from an old URL may be gone). */
export function resolveProfile<P extends Pick<Profile, "id">>(
  source: { default_profile: string; profiles: readonly P[] },
  profileId: string | null | undefined,
): P | undefined {
  return (
    source.profiles.find((p) => p.id === profileId) ??
    source.profiles.find((p) => p.id === source.default_profile)
  );
}

/** (service, country) -> verdict for one profile. */
export function verdictLookup(
  source: VerdictSource,
  profileId: string | null | undefined,
  includePending: boolean,
): (serviceId: string, country: string) => Verdict {
  const profile = resolveProfile(source, profileId);
  const id = profile?.id ?? source.default_profile;
  const reviewed = new Map<string, VerdictRecord>();
  const pending = new Map<string, VerdictRecord>();
  for (const r of source.records) {
    if (r.profile !== id) continue;
    const key = `${r.service_id}\u0000${r.target_country}`;
    (isPending(r) ? pending : reviewed).set(key, r);
  }
  return (serviceId, country) => {
    if (profile && country === profile.residence_country) return HOME;
    const key = `${serviceId}\u0000${country}`;
    const r = (includePending ? pending.get(key) : undefined) ?? reviewed.get(key);
    if (!r) return UNKNOWN;
    return { label: r.label, rank: r.rank, eu_badge: r.eu_badge, provenance: r.provenance };
  };
}

/** The offers with label, rank and eu_badge of `profileId` (unknown ids: default profile). */
export function relabelOffers<T extends VerdictFields>(
  offers: readonly T[],
  source: VerdictSource,
  profileId: string | null | undefined,
  includePending: boolean,
): T[] {
  const verdict = verdictLookup(source, profileId, includePending);
  return offers.map((o) => {
    const v = verdict(o.service_id, o.country);
    return { ...o, label: v.label, rank: v.rank, eu_badge: v.eu_badge };
  });
}

/**
 * One profile's records for a table: every non-pending record first, then the pending ones (kept
 * so findings awaiting review stay visible with their badge), each group sorted by `order`.
 */
export function recordsForDisplay<T extends Pick<FeasibilityView, "profile" | "provenance">>(
  records: readonly T[],
  profileId: string,
  order: (a: T, b: T) => number = () => 0,
): T[] {
  return records
    .filter((r) => r.profile === profileId)
    .sort((a, b) => Number(isPending(a)) - Number(isPending(b)) || order(a, b));
}

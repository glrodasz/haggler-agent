/**
 * Verdicts for any profile. offers.json carries the DEFAULT profile's labels with pending
 * findings excluded; the comparator relabels the same offers for the profile the visitor picks
 * or describes (residence and ties with other countries), with or without the findings still
 * pending review, from feasibility.json's rules. Mirrors haggler.profile_rules:
 *
 *  - a pair with a rule: walk its tree (`pending` when pending findings are included and it
 *    exists, else `reviewed`), answering each question from the profile's capabilities;
 *  - a pair without one: "official_home" (rank 0, no EU badge) in the residence country, else
 *    "unknown" (rank 99, never chosen). site-data leaves out exactly the pairs whose tree is
 *    this plain rule, so this reproduces offers.json (verdicts.test.ts checks it).
 *
 * Curated records only apply to a curated profile ("preset" questions): a described profile
 * gets them when its capabilities are exactly a preset's (matchingPreset).
 *
 * Pure and small, because the comparator island ships it to the browser.
 */

import type {
  FeasibilityRule,
  FeasibilityView,
  Offer,
  Profile,
  ProfileCapabilities,
  RuleTree,
  RuleVerdict,
} from "./types";
import { UNKNOWN_RANK } from "./types";

export type Verdict = RuleVerdict;

/** What verdicts are computed from: feasibility.json, or the subset the island ships. */
export interface VerdictSource {
  default_profile: string;
  profiles: readonly Pick<Profile, "id" | "capabilities">[];
  rules: readonly FeasibilityRule[];
  eea_countries: readonly string[];
}

/** A curated profile id (null or an unknown id: the default one), or a described profile. */
export type ProfileChoice = string | ProfileCapabilities | null | undefined;

export type VerdictFields = Pick<Offer, "service_id" | "country" | "label" | "rank" | "eu_badge">;

const HOME: Verdict = { label: "official_home", rank: 0, eu_badge: false, provenance: "home" };
const UNKNOWN: Verdict = {
  label: "unknown",
  rank: UNKNOWN_RANK,
  eu_badge: false,
  provenance: null,
};

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

const sameSet = (a: readonly string[], b: readonly string[]) => {
  const sa = new Set(a);
  const sb = new Set(b);
  return sa.size === sb.size && [...sa].every((x) => sb.has(x));
};

/** Whether two capability sets describe the same profile (profile_rules.matches). */
export function sameCapabilities(a: ProfileCapabilities, b: ProfileCapabilities): boolean {
  const pa = Object.entries(a.presence).sort();
  const pb = Object.entries(b.presence).sort();
  return (
    a.residence_country === b.residence_country &&
    sameSet(a.id_document_countries, b.id_document_countries) &&
    sameSet(a.phone_countries, b.phone_countries) &&
    sameSet(a.address_countries, b.address_countries) &&
    sameSet(a.card_countries, b.card_countries) &&
    JSON.stringify(pa) === JSON.stringify(pb)
  );
}

/** Id of the curated profile with exactly these capabilities, if any. */
export function matchingPreset(
  source: Pick<VerdictSource, "profiles">,
  caps: ProfileCapabilities,
): string | null {
  return source.profiles.find((p) => sameCapabilities(p.capabilities, caps))?.id ?? null;
}

export function capabilitiesOf(
  source: Pick<VerdictSource, "default_profile" | "profiles">,
  choice: ProfileChoice,
): ProfileCapabilities | undefined {
  if (choice && typeof choice === "object") return choice;
  return resolveProfile(source, choice)?.capabilities;
}

interface AnswerContext {
  eea: ReadonlySet<string>;
  presets: ReadonlyMap<string, ProfileCapabilities>;
}

/** The profile's answer to one rule question (profile_rules.answer). */
export function answer(
  question: readonly string[],
  caps: ProfileCapabilities,
  ctx: AnswerContext,
): string {
  const [kind, cc = ""] = question;
  const r = caps.residence_country;
  const docs = caps.id_document_countries;
  if (kind === "presence") return caps.presence[cc] ?? "none";
  let value: boolean;
  switch (kind) {
    case "lives":
      value = r === cc;
      break;
    case "lives_eea":
      value = ctx.eea.has(r);
      break;
    case "doc":
      value = docs.includes(cc);
      break;
    case "doc_any":
      value = docs.length > 0;
      break;
    case "doc_eea":
      value = docs.some((d) => ctx.eea.has(d));
      break;
    case "doc_residence":
      value = docs.includes(r);
      break;
    case "phone":
      value = caps.phone_countries.includes(cc);
      break;
    case "address":
      value = cc === r || caps.address_countries.includes(cc);
      break;
    case "card":
      value = caps.card_countries.includes(cc);
      break;
    case "preset": {
      const preset = ctx.presets.get(cc);
      value = preset !== undefined && sameCapabilities(caps, preset);
      break;
    }
    default:
      // A question this build does not know: no branch matches, so the verdict is unknown.
      return "";
  }
  return value ? "true" : "false";
}

export function evaluateRule(
  tree: RuleTree,
  caps: ProfileCapabilities,
  ctx: AnswerContext,
): Verdict {
  let node: RuleTree | undefined = tree;
  while (node && !("verdict" in node)) {
    node = node.answers[answer(node.ask, caps, ctx)];
  }
  return node ? node.verdict : UNKNOWN;
}

/** (service, country) -> verdict for one profile. */
export function verdictLookup(
  source: VerdictSource,
  choice: ProfileChoice,
  includePending: boolean,
): (serviceId: string, country: string) => Verdict {
  const caps = capabilitiesOf(source, choice);
  const ctx: AnswerContext = {
    eea: new Set(source.eea_countries),
    presets: new Map(source.profiles.map((p) => [p.id, p.capabilities])),
  };
  const rules = new Map<string, FeasibilityRule>();
  for (const r of source.rules) rules.set(`${r.service_id}\u0000${r.target_country}`, r);
  return (serviceId, country) => {
    if (!caps) return UNKNOWN;
    const rule = rules.get(`${serviceId}\u0000${country}`);
    if (!rule) return country === caps.residence_country ? HOME : UNKNOWN;
    const tree = (includePending ? rule.pending : null) ?? rule.reviewed;
    return evaluateRule(tree, caps, ctx);
  };
}

/** The offers with label, rank and eu_badge of the chosen profile. */
export function relabelOffers<T extends VerdictFields>(
  offers: readonly T[],
  source: VerdictSource,
  choice: ProfileChoice,
  includePending: boolean,
): T[] {
  const verdict = verdictLookup(source, choice, includePending);
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

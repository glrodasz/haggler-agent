/**
 * The hero's rotating savings messages: what the comparator would show at level 3 for the
 * visitor's (guessed) residence, first with no ties, then with ties to each other country.
 * Each teaser carries the comparator state it was computed from, so a click loads exactly that
 * comparison and the percentage it advertised is the one the result card shows.
 *
 * A tie here is the form's default tie (logic/profile.ts emptyTie: ID document, regular trips)
 * plus a card issued there, which is what most level-3 offers ask for.
 */

import {
  type CompareContext,
  type CompareOfferFields,
  cheapestFor,
  type DisplayOptions,
  savingsPctByRisk,
  summarize,
} from "./compare";
import { emptyTie, toCapabilities, type VisitorProfile } from "./profile";
import type { CompareState } from "./selection";
import type { RiskLevel } from "./types";
import { relabelOffers, type VerdictSource } from "./verdicts";

export const TEASER_RISK: RiskLevel = 3;
/** A tie is only advertised when it saves at least this many points more than having none. */
export const TEASER_MIN_GAIN_PCT = 1;
export const TEASER_MAX = 8;

export interface Teaser {
  /** Null: the residence alone, no ties. */
  tie: string | null;
  /** Level-3 saving as a share of the home price (7.5 = 7,5 %). */
  pct: number;
  /**
   * Countries the cheapest offers come from (the residence left out): the tie first, then by
   * number of services.
   */
  countries: string[];
  state: CompareState;
}

export interface TeaserInput {
  /** Offers of every service; only the ones in `base.services` are looked at. */
  offers: readonly CompareOfferFields[];
  source: VerdictSource;
  residence: string;
  residenceCurrency: string;
  /** Countries a tie can be with (the ones with prices). */
  tieCountries: readonly string[];
  rates: Record<string, number>;
  feePct: number;
  /** The comparator's starting state: services, plans and currency are kept. */
  base: CompareState;
}

function teaserFor(
  input: TeaserInput,
  profile: VisitorProfile,
  offers: readonly CompareOfferFields[],
) {
  const { base, rates, feePct } = input;
  const relabelled = relabelOffers(offers, input.source, toCapabilities(profile), false);
  const ctx: CompareContext = {
    rates,
    residence: input.residence,
    residenceCurrency: input.residenceCurrency,
    feePct,
  };
  const rows = base.services.map((id) => cheapestFor(id, base.plans[id] ?? "", relabelled, ctx));
  const display: DisplayOptions = { currency: base.currency, rates, feePct };
  const pct = savingsPctByRisk(rows, display)[TEASER_RISK];
  const counts = new Map<string, number>();
  for (const r of summarize(rows, TEASER_RISK, display).rows) {
    const cc = r.best?.country;
    if (cc && cc !== input.residence && (r.saving ?? 0) > 0.005) {
      counts.set(cc, (counts.get(cc) ?? 0) + 1);
    }
  }
  const tie = profile.ties[0]?.cc;
  const countries = [...counts]
    .sort((a, b) => Number(b[0] === tie) - Number(a[0] === tie) || b[1] - a[1])
    .map(([cc]) => cc);
  return { pct, countries };
}

/**
 * The no-tie teaser first (when it saves anything), then the ties that save at least
 * TEASER_MIN_GAIN_PCT points more, best first, up to `max` in all.
 */
export function savingsTeasers(input: TeaserInput, max = TEASER_MAX): Teaser[] {
  const wanted = new Set(input.base.services);
  const offers = input.offers.filter((o) => wanted.has(o.service_id));
  const alone: VisitorProfile = {
    residence: input.residence,
    residenceDoc: true,
    residencePhone: true,
    residenceCard: false,
    ties: [],
  };
  const stateOf = (custom: VisitorProfile): CompareState => ({
    ...input.base,
    risk: TEASER_RISK,
    profile: null,
    custom,
    pending: false,
  });
  const out: Teaser[] = [];
  const own = teaserFor(input, alone, offers);
  if (own.pct > 0.5) out.push({ tie: null, ...own, state: stateOf(alone) });
  const withTies: Teaser[] = [];
  for (const cc of input.tieCountries) {
    if (cc === input.residence) continue;
    const profile = { ...alone, ties: [{ ...emptyTie(cc), card: true }] };
    const found = teaserFor(input, profile, offers);
    if (found.pct - own.pct >= TEASER_MIN_GAIN_PCT) {
      withTies.push({ tie: cc, ...found, state: stateOf(profile) });
    }
  }
  withTies.sort((a, b) => b.pct - a.pct || (a.tie ?? "").localeCompare(b.tie ?? ""));
  return [...out, ...withTies].slice(0, max);
}

/**
 * Cheapest acceptable offer per (service, plan). Mirrors src/haggler/compare.py so the browser
 * comparator and the published cheapest.json agree:
 *
 *  - candidates: status accepted | carried_forward (quarantined/rejected never compete);
 *  - cost_eur = monthly_equiv / per_eur(currency), rounded to 4 decimals;
 *  - offers in a currency other than the residence currency pay the FX fee (fx_fee_pct) ->
 *    cost_eur_with_fee; all comparisons use cost_eur_with_fee so savings are net of the fee;
 *  - home = residence-country offer, channel "web" preferred, else "ios_iap";
 *  - for each risk threshold k in 0..3: best = cheapest offer with rank <= k; ties prefer web,
 *    then official sources, then the lower country code;
 *  - "unknown" verdicts (rank 99) and "not_feasible" (rank 4) are listed but never chosen.
 */

import type { Offer, OfferRef, RiskKey, RiskLevel } from "./types";
import { RISK_LEVELS } from "./types";

/** The subset of an offer the comparison needs (the island ships only these fields). */
export type CompareOfferFields = Pick<
  Offer,
  | "obs_key"
  | "service_id"
  | "plan_id"
  | "country"
  | "channel"
  | "currency"
  | "amount"
  | "billing_period"
  | "monthly_equiv"
  | "source_kind"
  | "status"
  | "label"
  | "rank"
  | "eu_badge"
>;

export interface CompareContext {
  /** Units of currency per 1 EUR (fx.json rates). */
  rates: Record<string, number>;
  residence: string;
  residenceCurrency: string;
  /** Percent, e.g. 2 = 2 %. */
  feePct: number;
}

export interface PricedOffer extends CompareOfferFields {
  cost_eur: number;
  cost_eur_with_fee: number;
  fee_applied: boolean;
}

export interface Cheapest {
  service_id: string;
  plan_id: string;
  home: PricedOffer | null;
  bestByRisk: Record<RiskLevel, PricedOffer | null>;
  savingsMonthEur: Record<RiskLevel, number | null>;
  /** Every priced candidate, cheapest first (includes unknown / not feasible ones). */
  offers: PricedOffer[];
}

const CANDIDATE_STATUSES: ReadonlySet<string> = new Set(["accepted", "carried_forward"]);

/** Decimal-safe rounding (avoids 1.005 -> 1.00 binary artefacts). */
export function roundTo(value: number, decimals: number): number {
  if (!Number.isFinite(value)) return value;
  const sign = value < 0 ? -1 : 1;
  const shifted = Number(`${Math.abs(value)}e${decimals}`);
  return sign * Number(`${Math.round(shifted)}e-${decimals}`);
}

export function perEur(currency: string, rates: Record<string, number>): number | null {
  if (currency === "EUR") return 1;
  const rate = rates[currency];
  return typeof rate === "number" && Number.isFinite(rate) && rate > 0 ? rate : null;
}

export function isCandidate(offer: Pick<CompareOfferFields, "status">): boolean {
  return CANDIDATE_STATUSES.has(offer.status);
}

/** Within the accepted threshold; rank 4 (not feasible) and 99 (unknown) never qualify. */
export function withinRisk(offer: Pick<CompareOfferFields, "rank">, k: RiskLevel): boolean {
  return offer.rank <= k;
}

/** Monthly EUR cost with and without the FX fee; null when the currency has no rate. */
export function priceOffer<T extends CompareOfferFields>(
  offer: T,
  ctx: CompareContext,
): (T & PricedOffer) | null {
  const per = perEur(offer.currency, ctx.rates);
  if (per === null || !Number.isFinite(offer.monthly_equiv)) return null;
  const cost_eur = roundTo(offer.monthly_equiv / per, 4);
  const fee_applied = offer.currency !== ctx.residenceCurrency;
  const cost_eur_with_fee = fee_applied ? roundTo(cost_eur * (1 + ctx.feePct / 100), 4) : cost_eur;
  return { ...offer, cost_eur, cost_eur_with_fee, fee_applied };
}

/** Total order used everywhere: cost, then web, then official, then country, then key. */
export function compareOffers(a: PricedOffer, b: PricedOffer): number {
  if (a.cost_eur_with_fee !== b.cost_eur_with_fee) return a.cost_eur_with_fee - b.cost_eur_with_fee;
  if (a.channel !== b.channel) return a.channel === "web" ? -1 : 1;
  if (a.source_kind !== b.source_kind) return a.source_kind === "official" ? -1 : 1;
  if (a.country !== b.country) return a.country < b.country ? -1 : 1;
  return a.obs_key < b.obs_key ? -1 : a.obs_key > b.obs_key ? 1 : 0;
}

export function pickHome(priced: PricedOffer[], residence: string): PricedOffer | null {
  const local = priced.filter((o) => o.country === residence);
  const web = local.filter((o) => o.channel === "web");
  const pool = web.length > 0 ? web : local.filter((o) => o.channel === "ios_iap");
  return [...pool].sort(compareOffers)[0] ?? null;
}

function emptyByRisk<V>(value: V): Record<RiskLevel, V> {
  return { 0: value, 1: value, 2: value, 3: value };
}

/** Cheapest result for the offers of ONE (service, plan). */
export function cheapestFor(
  serviceId: string,
  planId: string,
  offers: readonly CompareOfferFields[],
  ctx: CompareContext,
): Cheapest {
  const priced = offers
    .filter((o) => o.service_id === serviceId && o.plan_id === planId && isCandidate(o))
    .map((o) => priceOffer(o, ctx))
    .filter((o): o is PricedOffer => o !== null)
    .sort(compareOffers);
  const home = pickHome(priced, ctx.residence);
  const bestByRisk = emptyByRisk<PricedOffer | null>(null);
  const savingsMonthEur = emptyByRisk<number | null>(null);
  for (const k of RISK_LEVELS) {
    const best = priced.find((o) => withinRisk(o, k)) ?? null;
    bestByRisk[k] = best;
    savingsMonthEur[k] =
      home && best ? roundTo(home.cost_eur_with_fee - best.cost_eur_with_fee, 4) : null;
  }
  return {
    service_id: serviceId,
    plan_id: planId,
    home,
    bestByRisk,
    savingsMonthEur,
    offers: priced,
  };
}

/** Every (service, plan) present in `offers`, sorted by service then plan id (like Python). */
export function cheapestAll(
  offers: readonly CompareOfferFields[],
  ctx: CompareContext,
): Cheapest[] {
  const pairs = new Map<string, [string, string]>();
  for (const o of offers) pairs.set(`${o.service_id}\u0000${o.plan_id}`, [o.service_id, o.plan_id]);
  return [...pairs.values()]
    .sort(([s1, p1], [s2, p2]) => (s1 === s2 ? cmp(p1, p2) : cmp(s1, s2)))
    .map(([s, p]) => cheapestFor(s, p, offers, ctx));
}

function cmp(a: string, b: string): number {
  return a < b ? -1 : a > b ? 1 : 0;
}

/** Same shape as cheapest.json OfferRef minus source_url (money rounded to 2 decimals). */
export function toOfferRef(o: PricedOffer): Omit<OfferRef, "source_url"> {
  return {
    obs_key: o.obs_key,
    country: o.country,
    channel: o.channel,
    currency: o.currency,
    amount: o.amount,
    monthly_equiv: o.monthly_equiv,
    cost_eur: roundTo(o.cost_eur, 2),
    cost_eur_with_fee: roundTo(o.cost_eur_with_fee, 2),
    label: o.label,
    rank: o.rank,
    eu_badge: o.eu_badge,
  };
}

export function riskKey(k: RiskLevel): RiskKey {
  return `${k}`;
}

// ------------------------------------------------------------------ display currency

/** Convert a EUR amount into `currency`; null when the rate is missing. */
export function eurTo(
  amountEur: number,
  currency: string,
  rates: Record<string, number>,
): number | null {
  const per = perEur(currency, rates);
  return per === null ? null : amountEur * per;
}

/**
 * Monthly cost of an offer in the display currency, fee included. When the offer is already in
 * that currency the published amount is used directly so "139 kr" never shows as "138,99 kr".
 */
export function displayCost(
  o: PricedOffer,
  currency: string,
  rates: Record<string, number>,
  feePct: number,
): number | null {
  if (o.currency === currency) {
    return o.monthly_equiv * (o.fee_applied ? 1 + feePct / 100 : 1);
  }
  return eurTo(o.cost_eur_with_fee, currency, rates);
}

/** FX fee part of an offer's monthly cost, in the display currency. */
export function displayFee(
  o: PricedOffer,
  currency: string,
  rates: Record<string, number>,
  feePct: number,
): number | null {
  if (!o.fee_applied) return 0;
  if (o.currency === currency) return o.monthly_equiv * (feePct / 100);
  return eurTo(o.cost_eur_with_fee - o.cost_eur, currency, rates);
}

export interface DisplayOptions {
  currency: string;
  rates: Record<string, number>;
  feePct: number;
}

export interface SummaryRow {
  service_id: string;
  plan_id: string;
  home: PricedOffer | null;
  best: PricedOffer | null;
  homeCost: number | null;
  bestCost: number | null;
  /** home - best in the display currency; null if either side is missing. */
  saving: number | null;
}

export interface Totals {
  /** Rows that have both a home price and a best offer: the only ones summed. */
  comparable: number;
  home: number;
  optimized: number;
  saving: number;
  /** FX fee included in `optimized`. */
  fee: number;
  withoutHome: string[];
  withoutOffers: string[];
}

/** Rows and totals (display currency) for the services the visitor picked at threshold k. */
export function summarize(
  rows: readonly Cheapest[],
  k: RiskLevel,
  display: DisplayOptions,
): { rows: SummaryRow[]; totals: Totals } {
  const { currency, rates, feePct } = display;
  const out: SummaryRow[] = [];
  const totals: Totals = {
    comparable: 0,
    home: 0,
    optimized: 0,
    saving: 0,
    fee: 0,
    withoutHome: [],
    withoutOffers: [],
  };
  for (const row of rows) {
    const best = row.bestByRisk[k];
    const homeCost = row.home ? displayCost(row.home, currency, rates, feePct) : null;
    const bestCost = best ? displayCost(best, currency, rates, feePct) : null;
    const saving = homeCost !== null && bestCost !== null ? homeCost - bestCost : null;
    out.push({
      service_id: row.service_id,
      plan_id: row.plan_id,
      home: row.home,
      best,
      homeCost,
      bestCost,
      saving,
    });
    if (homeCost !== null && bestCost !== null && best) {
      totals.comparable += 1;
      totals.home += homeCost;
      totals.optimized += bestCost;
      totals.fee += displayFee(best, currency, rates, feePct) ?? 0;
    } else if (best) {
      totals.withoutHome.push(row.service_id);
    } else {
      totals.withoutOffers.push(row.service_id);
    }
  }
  totals.saving = totals.home - totals.optimized;
  return { rows: out, totals };
}

/** Monthly saving at every risk threshold, so the trade-off is visible without nudging. */
export function savingsByRisk(
  rows: readonly Cheapest[],
  display: DisplayOptions,
): Record<RiskLevel, number> {
  const out = emptyByRisk(0);
  for (const k of RISK_LEVELS) out[k] = summarize(rows, k, display).totals.saving;
  return out;
}

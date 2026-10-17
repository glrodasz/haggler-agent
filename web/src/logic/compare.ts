/**
 * Cheapest acceptable offer per (service, plan). Mirrors src/haggler/compare.py so the browser
 * comparator and the published cheapest.json agree:
 *
 *  - candidates: status accepted | carried_forward (quarantined/rejected never compete);
 *  - eur = monthly_equiv / per_eur(currency); offers in a currency other than the residence
 *    currency pay the FX fee (fx_fee_pct) on the unrounded eur; cost_eur and cost_eur_with_fee
 *    are then rounded to cents (half up), so sub-cent differences are ties;
 *  - all comparisons use cost_eur_with_fee, so savings (home - best, in cents) are net of the fee;
 *  - home = residence-country offer, channel "web" preferred, else "ios_iap";
 *  - for each risk threshold k in 0..3: best = cheapest offer with rank <= k; ties prefer web,
 *    then official sources, then the lower country code, then monthly before longer billing;
 *  - "unknown" verdicts (rank 99) and "not_feasible" (rank 4) are listed but never chosen.
 *
 * Float division can still differ from Python's Decimal by a cent at exact .xx5 boundaries; the
 * contract test (contract.test.ts) compares both on every CI run.
 */

import type { Offer, OfferRef, RiskKey, RiskLevel, ServiceInfo } from "./types";
import { RISK_LEVELS, UNKNOWN_RANK } from "./types";

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
const PERIOD_ORDER: Readonly<Record<string, number>> = { month: 0, quarter: 1, year: 2 };

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
  const eur = offer.monthly_equiv / per;
  const cost_eur = roundTo(eur, 2);
  const fee_applied = offer.currency !== ctx.residenceCurrency;
  const cost_eur_with_fee = fee_applied ? roundTo(eur * (1 + ctx.feePct / 100), 2) : cost_eur;
  return { ...offer, cost_eur, cost_eur_with_fee, fee_applied };
}

/** Total order used everywhere: cost (cents), web, official, country, billing period, key. */
export function compareOffers(a: PricedOffer, b: PricedOffer): number {
  if (a.cost_eur_with_fee !== b.cost_eur_with_fee) return a.cost_eur_with_fee - b.cost_eur_with_fee;
  if (a.channel !== b.channel) return a.channel === "web" ? -1 : 1;
  if (a.source_kind !== b.source_kind) return a.source_kind === "official" ? -1 : 1;
  if (a.country !== b.country) return a.country < b.country ? -1 : 1;
  const pa = PERIOD_ORDER[a.billing_period] ?? 9;
  const pb = PERIOD_ORDER[b.billing_period] ?? 9;
  if (pa !== pb) return pa - pb;
  return cmp(a.obs_key, b.obs_key);
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
      home && best ? roundTo(home.cost_eur_with_fee - best.cost_eur_with_fee, 2) : null;
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

/**
 * Every (service, plan) with at least one costed candidate, like cheapest.json. With `services`
 * (services.json) rows follow compare.py: plans outside the catalog are left out and rows are
 * sorted by service id, then plan rank, then catalog order. Without it, by service then plan id.
 */
export function cheapestAll(
  offers: readonly CompareOfferFields[],
  ctx: CompareContext,
  services?: readonly Pick<ServiceInfo, "id" | "plans">[],
): Cheapest[] {
  const planOrder = services
    ? new Map<string, readonly [number, number]>(
        services.flatMap((s) => s.plans.map((p, pos) => [`${s.id}\u0000${p.id}`, [p.rank, pos]])),
      )
    : null;
  const pairs = new Map<string, [string, string]>();
  for (const o of offers) {
    const key = `${o.service_id}\u0000${o.plan_id}`;
    if (!planOrder || planOrder.has(key)) pairs.set(key, [o.service_id, o.plan_id]);
  }
  const order = (s: string, p: string) => planOrder?.get(`${s}\u0000${p}`) ?? [0, 0];
  return [...pairs.values()]
    .sort(([s1, p1], [s2, p2]) => {
      if (s1 !== s2) return cmp(s1, s2);
      if (!planOrder) return cmp(p1, p2);
      const [r1, i1] = order(s1, p1);
      const [r2, i2] = order(s2, p2);
      return r1 - r2 || i1 - i2;
    })
    .map(([s, p]) => cheapestFor(s, p, offers, ctx))
    .filter((row) => row.offers.length > 0);
}

function cmp(a: string, b: string): number {
  return a < b ? -1 : a > b ? 1 : 0;
}

/** Same shape as cheapest.json OfferRef minus source_url. */
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
  /**
   * When the best offer is the residence country's own: the cheapest offer from another country,
   * i.e. what it was compared against. Within the threshold when there is one, else the cheapest
   * feasible one above it (runnerUpAboveRisk). Null otherwise or when there is none.
   */
  runnerUp: PricedOffer | null;
  runnerUpCost: number | null;
  runnerUpAboveRisk: boolean;
  /**
   * The cheapest offer from another country that is cheaper than `best` but has no verdict yet
   * for this profile (rank 99). Never chosen nor summed: shown so a profile nobody has evaluated
   * still sees what the price difference is. Null when there is none.
   */
  unevaluated: PricedOffer | null;
  unevaluatedCost: number | null;
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
  /** What the unevaluated offers would save on top of `saving` (rows with a home price only). */
  unevaluatedSaving: number;
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
    unevaluatedSaving: 0,
  };
  for (const row of rows) {
    const best = row.bestByRisk[k];
    const homeCost = row.home ? displayCost(row.home, currency, rates, feePct) : null;
    const bestCost = best ? displayCost(best, currency, rates, feePct) : null;
    const saving = homeCost !== null && bestCost !== null ? homeCost - bestCost : null;
    const foreign =
      best && best.country === row.home?.country
        ? row.offers.filter((o) => o.country !== best.country)
        : [];
    const withinK = foreign.find((o) => withinRisk(o, k)) ?? null;
    const runnerUp = withinK ?? foreign.find((o) => withinRisk(o, 3)) ?? null;
    const reference = best ?? row.home;
    const unevaluated =
      row.offers.find(
        (o) =>
          o.rank === UNKNOWN_RANK &&
          o.country !== row.home?.country &&
          (reference === null || o.cost_eur_with_fee < reference.cost_eur_with_fee),
      ) ?? null;
    const unevaluatedCost = unevaluated
      ? displayCost(unevaluated, currency, rates, feePct)
      : null;
    out.push({
      service_id: row.service_id,
      plan_id: row.plan_id,
      home: row.home,
      best,
      homeCost,
      bestCost,
      saving,
      runnerUp,
      runnerUpCost: runnerUp ? displayCost(runnerUp, currency, rates, feePct) : null,
      runnerUpAboveRisk: runnerUp !== null && withinK === null,
      unevaluated,
      unevaluatedCost,
    });
    if (homeCost !== null && unevaluatedCost !== null) {
      totals.unevaluatedSaving += (bestCost ?? homeCost) - unevaluatedCost;
    }
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

/**
 * Levels the comparator offers for this selection: "lo que pagas hoy" (level 0), 2 and 3 always,
 * and level 1 only when it saves more than level 0, since it rarely has an offer of its own.
 */
export function visibleRiskLevels(byRisk: Record<RiskLevel, number>): RiskLevel[] {
  return RISK_LEVELS.filter((k) => k !== 1 || byRisk[1] - byRisk[0] > 0.005);
}

/** A level the comparator does not offer falls back to the closest lower one it does. */
export function snapRisk(risk: RiskLevel, levels: readonly RiskLevel[]): RiskLevel {
  return [...levels].reverse().find((k) => k <= risk) ?? levels[0] ?? 0;
}

/**
 * The same saving as a percentage of what the visitor pays at home for the services that have an
 * option at that threshold (7.5 = 7,5 %). A share keeps the level strip readable whatever the
 * currency; the amounts are in the result card and the summary.
 */
export function savingsPctByRisk(
  rows: readonly Cheapest[],
  display: DisplayOptions,
): Record<RiskLevel, number> {
  const out = emptyByRisk(0);
  for (const k of RISK_LEVELS) {
    const { home, saving } = summarize(rows, k, display).totals;
    out[k] = home > 0 ? (saving / home) * 100 : 0;
  }
  return out;
}

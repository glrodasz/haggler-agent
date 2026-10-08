/**
 * Indexed view over the site data: lookups and per-page selections. Pure (no file access) so
 * vitest can exercise it against web/src/data-sample.
 */

import { es } from "../i18n/es";
import { formatPrice } from "./format";
import type {
  Bundle,
  ChangeRow,
  ChangeValue,
  CountryInfo,
  FeasibilityView,
  Offer,
  RiskLabel,
  ServiceInfo,
  SiteData,
} from "./types";
import { DATA_FILES, UNKNOWN_RANK } from "./types";

export interface LabelInfo {
  id: string;
  rank: number;
  name: string;
  description: string;
}

export interface SiteIndex extends SiteData {
  residenceCountry: CountryInfo | undefined;
  residenceName: string;
  residenceCurrency: string;
  isSample: boolean;
  service(id: string): ServiceInfo | undefined;
  country(cc: string): CountryInfo | undefined;
  serviceName(id: string): string;
  countryName(cc: string): string;
  planLabel(serviceId: string, planId: string | null | undefined): string;
  planRank(serviceId: string, planId: string): number;
  label(id: string, rank?: number): LabelInfo;
  /** Labels the visitor can accept (ranks 0..3), in rank order. */
  acceptableLabels(): LabelInfo[];
  offersForService(serviceId: string): Offer[];
  offersForCountry(cc: string): Offer[];
  bundlesForService(serviceId: string): Bundle[];
  bundlesForCountry(cc: string): Bundle[];
  feasibilityForService(serviceId: string): FeasibilityView[];
  feasibilityForCountry(cc: string): FeasibilityView[];
  /** Monthly cost without FX fee in `currency` (exact when the offer already uses it). */
  monthlyIn(
    offer: Pick<Offer, "currency" | "monthly_equiv" | "monthly_eur">,
    currency: string,
  ): number | null;
  isStale(currency: string): boolean;
  /** Services that have at least one published offer (the comparator only lists these). */
  pricedServices(): ServiceInfo[];
  /** changes.json sorted newest first, limited to `days` before meta.as_of (all without it). */
  recentChanges(days?: number): ChangeRow[];
  formatChangeValue(row: ChangeRow, value: ChangeValue): string;
}

/** Fail loudly with an actionable message instead of rendering an empty site. */
export function assertSiteData(raw: Partial<Record<string, unknown>>): SiteData {
  const missing = DATA_FILES.filter((name) => raw[name] === undefined);
  if (missing.length > 0) {
    throw new Error(missing.map((name) => es.errors.missingData(name)).join("\n"));
  }
  const listFiles = [
    "services",
    "countries",
    "offers",
    "cheapest",
    "bundles",
    "changes",
    "coverage",
  ];
  for (const name of listFiles) {
    if (!Array.isArray(raw[name]))
      throw new Error(`web/src/data/${name}.json debe ser una lista JSON.`);
  }
  const meta = raw.meta as { residence?: unknown } | null;
  if (!meta || typeof meta.residence !== "string") {
    throw new Error("web/src/data/meta.json no tiene el campo 'residence'.");
  }
  return raw as unknown as SiteData;
}

function byMonthlyEur(a: Offer, b: Offer): number {
  const ea = a.monthly_eur ?? Number.POSITIVE_INFINITY;
  const eb = b.monthly_eur ?? Number.POSITIVE_INFINITY;
  if (ea !== eb) return ea - eb;
  if (a.country !== b.country) return a.country < b.country ? -1 : 1;
  return a.obs_key < b.obs_key ? -1 : 1;
}

function daysBetween(fromIso: string, toIso: string): number {
  const ms =
    Date.parse(`${toIso.slice(0, 10)}T00:00:00Z`) - Date.parse(`${fromIso.slice(0, 10)}T00:00:00Z`);
  return ms / 86_400_000;
}

export function buildIndex(data: SiteData): SiteIndex {
  const services = new Map(data.services.map((s) => [s.id, s]));
  const countries = new Map(data.countries.map((c) => [c.cc, c]));
  const labels = new Map<string, RiskLabel>(data.feasibility.labels.map((l) => [l.id, l]));
  const residenceCountry = countries.get(data.meta.residence);
  const stale = new Set(data.fx.stale ?? []);
  const offerCurrency = new Map(data.offers.map((o) => [o.obs_key, o.currency]));
  // site-data already limits changes.json to the window before as_of (or before today).
  const asOf = data.meta.as_of;

  const serviceName = (id: string) => services.get(id)?.name ?? id;
  const countryName = (cc: string) => countries.get(cc)?.name_es ?? cc;
  const planOf = (serviceId: string, planId: string) =>
    services.get(serviceId)?.plans.find((p) => p.id === planId);

  const label = (id: string, rank?: number): LabelInfo => {
    const fromData = labels.get(id);
    const fallback = es.riskLabels[id];
    return {
      id,
      rank: fromData?.rank ?? rank ?? UNKNOWN_RANK,
      name: fromData?.name_es ?? fallback?.name ?? id,
      description: fromData?.description_es ?? fallback?.description ?? "",
    };
  };

  const index: SiteIndex = {
    ...data,
    residenceCountry,
    residenceName: residenceCountry?.name_es ?? data.meta.residence,
    residenceCurrency: residenceCountry?.currency ?? "EUR",
    isSample: (data.meta.run_id ?? "").startsWith("sample"),
    service: (id) => services.get(id),
    country: (cc) => countries.get(cc),
    serviceName,
    countryName,
    planLabel: (serviceId, planId) =>
      planId ? (planOf(serviceId, planId)?.label_es ?? planId) : es.common.unknown,
    planRank: (serviceId, planId) => planOf(serviceId, planId)?.rank ?? 0,
    label,
    acceptableLabels: () => {
      const ids = [
        "official_home",
        "eu_right_untested",
        "tos_breach_low_enforcement",
        "tos_breach_enforced",
      ];
      const fromData = data.feasibility.labels.filter((l) => l.rank >= 0 && l.rank <= 3);
      const chosen = fromData.length === 4 ? fromData.map((l) => l.id) : ids;
      return chosen.map((id, i) => label(id, i)).sort((a, b) => a.rank - b.rank);
    },
    offersForService: (serviceId) =>
      data.offers.filter((o) => o.service_id === serviceId).sort(byMonthlyEur),
    offersForCountry: (cc) =>
      data.offers
        .filter((o) => o.country === cc)
        .sort((a, b) => {
          if (a.service_id !== b.service_id) {
            return serviceName(a.service_id).localeCompare(serviceName(b.service_id), "es");
          }
          const ra = planOf(a.service_id, a.plan_id)?.rank ?? 0;
          const rb = planOf(b.service_id, b.plan_id)?.rank ?? 0;
          if (ra !== rb) return ra - rb;
          return a.obs_key < b.obs_key ? -1 : 1;
        }),
    bundlesForService: (serviceId) =>
      data.bundles.filter((b) => b.components.some((c) => c.service_id === serviceId)),
    bundlesForCountry: (cc) => data.bundles.filter((b) => b.country === cc),
    feasibilityForService: (serviceId) =>
      data.feasibility.records
        .filter((r) => r.service_id === serviceId)
        .sort((a, b) => a.rank - b.rank || a.target_country.localeCompare(b.target_country)),
    feasibilityForCountry: (cc) =>
      data.feasibility.records
        .filter((r) => r.target_country === cc)
        .sort((a, b) => serviceName(a.service_id).localeCompare(serviceName(b.service_id), "es")),
    monthlyIn: (offer, currency) => {
      if (offer.currency === currency) return offer.monthly_equiv;
      if (offer.monthly_eur === null || offer.monthly_eur === undefined) return null;
      if (currency === "EUR") return offer.monthly_eur;
      const rate = data.fx.rates[currency];
      return typeof rate === "number" && rate > 0 ? offer.monthly_eur * rate : null;
    },
    isStale: (currency) => stale.has(currency),
    pricedServices: () => {
      const withOffers = new Set(data.offers.map((o) => o.service_id));
      return data.services
        .filter((s) => withOffers.has(s.id))
        .sort((a, b) => a.name.localeCompare(b.name, "es"));
    },
    recentChanges: (days = 90) =>
      [...data.changes]
        .filter((c) => asOf === null || daysBetween(c.date, asOf) <= days)
        .sort((a, b) =>
          a.date === b.date ? (a.obs_key < b.obs_key ? -1 : 1) : a.date < b.date ? 1 : -1,
        ),
    formatChangeValue: (row, value) => {
      if (value === null || value === undefined) return "—";
      if (typeof value === "string") return value;
      const fallbackCurrency =
        offerCurrency.get(row.obs_key) ?? countries.get(row.country)?.currency ?? "EUR";
      if (typeof value === "number") return formatPrice(value, fallbackCurrency);
      return formatPrice(value.amount, value.currency ?? fallbackCurrency);
    },
  };
  return index;
}

/** CSS modifier for a risk rank: 0..4, anything else is "unknown". */
export function riskClass(rank: number): string {
  return rank >= 0 && rank <= 4 ? `risk-${rank}` : "risk-unknown";
}

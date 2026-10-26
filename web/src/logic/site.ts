/**
 * Indexed view over the site data: lookups and per-page selections. Pure (no file access) so
 * vitest can exercise it against web/src/data-sample.
 */

import { getMessages, type Messages } from "../i18n";
import { DEFAULT_LOCALE, type Locale } from "../i18n/locale";
import { documentName, type Format, formatFor } from "./format";
import type {
  Bundle,
  BundleVerdict,
  ChangeRow,
  ChangeValue,
  CountryInfo,
  FeasibilityView,
  Offer,
  PendingValueType,
  Profile,
  RiskLabel,
  ServiceInfo,
  SiteData,
} from "./types";
import { DATA_FILES, UNKNOWN_RANK } from "./types";
import { validateFile, validateReferences } from "./validate";
import { recordsForDisplay, resolveProfile } from "./verdicts";

export interface LabelInfo {
  id: string;
  rank: number;
  name: string;
  description: string;
}

export interface SiteIndex extends SiteData {
  /** The locale every name, label and formatted value of this index is in. */
  locale: Locale;
  msg: Messages;
  fmt: Format;
  residenceCountry: CountryInfo | undefined;
  residenceName: string;
  residenceCurrency: string;
  /** The profile offers.json, cheapest.json and the feasibility tables are computed for. */
  defaultProfile: Profile | undefined;
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
  /** Name of a public profile (its id when feasibility.json does not list it). */
  profileName(id: string): string;
  /**
   * Active bundles that stand in for the service: its observed price (observed_service_id), the
   * services it covers, or an included (or to-choose) component. Discounts do not count.
   */
  bundlesForService(serviceId: string): Bundle[];
  /**
   * Active bundles that only give a discount or free months on the service (an optional saving),
   * so its page still leads to them.
   */
  discountBundlesForService(serviceId: string): Bundle[];
  /** Active bundles of the country, in bundles.json order. */
  bundlesForCountry(cc: string): Bundle[];
  /** Bundles of the country whose offer ended (published for a while, never computed with). */
  expiredBundlesForCountry(cc: string): Bundle[];
  /**
   * /paquetes: the active bundles grouped by the default profile's verdict (see
   * groupBundlesByVerdict).
   */
  bundleGroups(): BundleGroup[];
  /** Every expired bundle, the most recently ended first (see compareExpiredBundles). */
  expiredBundles(): Bundle[];
  /**
   * The day an expired bundle's offer ended, null when bundles.json does not say (see
   * bundleEndedOn): never a day after the one site-data ran.
   */
  bundleEndedOn(bundle: Pick<Bundle, "valid_until">): string | null;
  /** Every published bundle (expired ones too) by country name, then by name. */
  watchedBundles(): Bundle[];
  /** A bundle's verdicts split for its card: the default profile's, then the other profiles'. */
  bundleVerdicts(bundle: Bundle): BundleVerdicts;
  /**
   * Whole days from `iso` to the day site-data ran (meta.generated_at): the day Python dated a
   * bundle's status on, e.g. the 45-day manual verification (null without a date).
   */
  daysBeforeGeneration(iso: string | null | undefined): number | null;
  /** The default profile's records: non-pending first, then those pending review. */
  feasibilityForService(serviceId: string): FeasibilityView[];
  /** The default profile's records: non-pending first, then those pending review. */
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

export interface BundleVerdicts {
  /** The default profile's verdict with pending findings excluded. */
  main: BundleVerdict | undefined;
  /** The default profile's verdict including pending findings, only when it differs. */
  pending: BundleVerdict | undefined;
  /** Every other profile's verdicts, in bundles.json order (each pending one after its own). */
  others: BundleVerdict[];
}

/** A /paquetes group: the active bundles whose default-profile verdict carries `label`. */
export interface BundleGroup<B = Bundle> {
  label: string;
  /** 0..4, or UNKNOWN_RANK (unknown and out-of-range ranks): the groups are sorted on it. */
  rank: number;
  /** Highest saving first (see compareBundlesBySaving). */
  bundles: B[];
}

const UNKNOWN_LABEL = "unknown";

/** An expired bundle is only listed apart ("Ofertas vencidas"), never with the active ones. */
export function isActiveBundle(bundle: Pick<Bundle, "display_status">): boolean {
  return bundle.display_status !== "expired";
}

/** Whether a bundle stands in for the service (see SiteIndex.bundlesForService). */
export function bundleCoversService(
  bundle: Pick<Bundle, "observed_service_id" | "covers" | "components">,
  serviceId: string,
): boolean {
  return (
    bundle.observed_service_id === serviceId ||
    bundle.covers.includes(serviceId) ||
    bundle.components.some(
      (c) =>
        c.service_id === serviceId && (c.inclusion === "included" || c.inclusion === "choose_n"),
    )
  );
}

const OPTIONAL_INCLUSIONS: readonly string[] = ["discount_pct", "discount_abs", "free_months"];

/** Whether a bundle only discounts the service (or gives free months of it) without covering it. */
export function bundleDiscountsService(
  bundle: Pick<Bundle, "observed_service_id" | "covers" | "components">,
  serviceId: string,
): boolean {
  return (
    !bundleCoversService(bundle, serviceId) &&
    bundle.components.some(
      (c) => c.service_id === serviceId && OPTIONAL_INCLUSIONS.includes(c.inclusion),
    )
  );
}

/** Splits a bundle's verdicts into the default profile's (reviewed, pending) and the rest. */
export function splitBundleVerdicts(
  verdicts: readonly BundleVerdict[],
  defaultProfile: string,
): BundleVerdicts {
  const own = verdicts.filter((v) => v.profile === defaultProfile);
  return {
    main: own.find((v) => v.provenance !== "agent_pending"),
    pending: own.find((v) => v.provenance === "agent_pending"),
    others: verdicts.filter((v) => v.profile !== defaultProfile),
  };
}

/** Rank a verdict is grouped and sorted on: 0..4; anything else (99, out of range) is unknown. */
function groupRank(rank: number): number {
  return Number.isInteger(rank) && rank >= 0 && rank <= 4 ? rank : UNKNOWN_RANK;
}

function byId(a: Pick<Bundle, "bundle_id">, b: Pick<Bundle, "bundle_id">): number {
  return a.bundle_id < b.bundle_id ? -1 : a.bundle_id > b.bundle_id ? 1 : 0;
}

/** Highest saving_pct first, bundles without a saving last; ties by name, then by id. */
export function compareBundlesBySaving(
  a: Pick<Bundle, "saving_pct" | "name" | "bundle_id">,
  b: Pick<Bundle, "saving_pct" | "name" | "bundle_id">,
): number {
  if (a.saving_pct !== b.saving_pct) {
    if (a.saving_pct === null) return 1;
    if (b.saving_pct === null) return -1;
    return b.saving_pct - a.saving_pct;
  }
  return a.name.localeCompare(b.name, "es") || byId(a, b);
}

/**
 * The day an expired bundle's offer ended, as far as bundles.json says: its valid_until once
 * that is before `generatedOn` (the day site-data ran, which Python dated the status on). null
 * for a bundle the curated data marks expired before its announced end (valid_until still ahead
 * or that very day) or without an end date: the day it really ended is not published.
 */
export function bundleEndedOn(
  bundle: Pick<Bundle, "valid_until">,
  generatedOn: string,
): string | null {
  const end = bundle.valid_until;
  return end !== null && end < generatedOn ? end : null;
}

/**
 * When an expired bundle ended, for sorting: bundleEndedOn, else the earlier of valid_until and
 * the last review of its row (marked expired by then), as status.expired_on dates it in Python.
 */
function expiredSortDate(
  bundle: Pick<Bundle, "valid_until" | "last_verified">,
  generatedOn: string,
): string | null {
  const ended = bundleEndedOn(bundle, generatedOn);
  if (ended !== null) return ended;
  const dates = [bundle.valid_until, bundle.last_verified].filter((d): d is string => d !== null);
  return dates.length === 0 ? null : dates.reduce((a, b) => (b < a ? b : a));
}

/**
 * The most recently ended first (expiredSortDate against `generatedOn`, bundles without any date
 * last); ties by name, then by id.
 */
export function compareExpiredBundles(
  a: Pick<Bundle, "valid_until" | "last_verified" | "name" | "bundle_id">,
  b: Pick<Bundle, "valid_until" | "last_verified" | "name" | "bundle_id">,
  generatedOn: string,
): number {
  const endA = expiredSortDate(a, generatedOn);
  const endB = expiredSortDate(b, generatedOn);
  if (endA !== endB) {
    if (endA === null) return 1;
    if (endB === null) return -1;
    return endA < endB ? 1 : -1;
  }
  return a.name.localeCompare(b.name, "es") || byId(a, b);
}

/**
 * Groups the ACTIVE bundles by the default profile's reviewed verdict (never the variant with
 * pending findings): groups in rank order 0..4, unknown last (a bundle without a verdict for the
 * profile counts as unknown); at equal rank by label, "unknown" last. A label seen with
 * different ranks takes the worst one. Within a group, compareBundlesBySaving.
 */
export function groupBundlesByVerdict(
  bundles: readonly Bundle[],
  defaultProfile: string,
): BundleGroup[] {
  return groupBundles(bundles, (b) => splitBundleVerdicts(b.verdicts, defaultProfile).main);
}

/** groupBundlesByVerdict with any verdict per bundle (e.g. the visitor's, from its rule). */
export function groupBundles<
  B extends Pick<Bundle, "display_status" | "saving_pct" | "name" | "bundle_id">,
>(
  bundles: readonly B[],
  verdictOf: (bundle: B) => Pick<BundleVerdict, "label" | "rank"> | undefined,
): BundleGroup<B>[] {
  const groups = new Map<string, BundleGroup<B>>();
  for (const bundle of bundles) {
    if (!isActiveBundle(bundle)) continue;
    const main = verdictOf(bundle);
    const label = main?.label ?? UNKNOWN_LABEL;
    const rank = main ? groupRank(main.rank) : UNKNOWN_RANK;
    const group = groups.get(label);
    if (group) {
      group.rank = Math.max(group.rank, rank);
      group.bundles.push(bundle);
    } else {
      groups.set(label, { label, rank, bundles: [bundle] });
    }
  }
  for (const group of groups.values()) group.bundles.sort(compareBundlesBySaving);
  const lastIfUnknown = (label: string) => (label === UNKNOWN_LABEL ? 1 : 0);
  return [...groups.values()].sort(
    (a, b) =>
      a.rank - b.rank ||
      lastIfUnknown(a.label) - lastIfUnknown(b.label) ||
      (a.label < b.label ? -1 : a.label > b.label ? 1 : 0),
  );
}

/** The other public profiles' reviewed verdicts on a bundle, in bundles.json order. */
export function otherProfileVerdicts(
  bundle: Pick<Bundle, "verdicts">,
  defaultProfile: string,
): BundleVerdict[] {
  return splitBundleVerdicts(bundle.verdicts, defaultProfile).others.filter(
    (v) => v.provenance !== "agent_pending",
  );
}

/**
 * A bundle's anchors: its evidence entries that back a value (fact set), and how many of them
 * our own fetch verified. total 0 before the watcher has anything to check.
 */
export function bundleAnchors(bundle: Pick<Bundle, "evidence">): {
  verified: number;
  total: number;
} {
  const anchors = bundle.evidence.filter((e) => e.fact !== null);
  return {
    verified: anchors.filter((e) => e.verified).length,
    total: anchors.length,
  };
}

/**
 * What qualifies a bundle's saving wherever it is shown: computed on the marginal cost, and
 * "sin verificar" until the bundle is verified. None without a saving.
 */
export function bundleSavingNotes(
  bundle: Pick<Bundle, "saving_pct" | "saving_basis" | "display_status">,
  msg: Messages,
): string[] {
  if (bundle.saving_pct === null) return [];
  return [
    ...(bundle.saving_basis === "marginal" ? [msg.bundle.marginalSaving] : []),
    ...(bundle.display_status !== "verified" ? [msg.bundle.unverifiedSaving] : []),
  ];
}

const MONEY_CODE = /^(\d+(?:\.\d+)?) ([A-Z]{3})(?:\/([a-z]+))?$/;

/**
 * Display text of a pending change's value, a research vocabulary code (PENDING_VALUE_TYPES in
 * types.ts): "21900 COP/month" -> "21.900 COP/mes", "disney_plus.standard_with_ads" -> the
 * service and plan names, "hbo_max:30" -> "30 % en HBO Max". A code that does not parse is
 * shown as is; null is "sin registrar".
 */
export function pendingValueText(
  valueType: PendingValueType,
  value: string | null,
  names: Pick<SiteIndex, "serviceName" | "planLabel" | "msg" | "fmt">,
): string {
  const { msg: es, fmt } = names;
  const { formatDate, formatPercent, formatPrice, periodSuffix } = fmt;
  if (value === null) return es.bundle.pendingNone;
  switch (valueType) {
    case "money": {
      const [, amount, currency, period] = MONEY_CODE.exec(value) ?? [];
      if (amount === undefined || currency === undefined) return value;
      return formatPrice(Number(amount), currency) + (period ? periodSuffix(period) : "");
    }
    case "int":
      return /^\d+$/.test(value) ? es.bundle.pendingMonths(Number(value)) : value;
    case "date":
      return formatDate(value);
    case "service_ref": {
      const [serviceId = value, planId] = value.split(".", 2);
      const name = names.serviceName(serviceId);
      return planId ? `${name} (${names.planLabel(serviceId, planId)})` : name;
    }
    case "service_pct": {
      const [serviceId, pct] = value.split(":", 2);
      const n = Number(pct);
      if (!serviceId || pct === undefined || !Number.isFinite(n)) return value;
      return es.bundle.pendingDiscount(names.serviceName(serviceId), formatPercent(n, { digits: 2 }));
    }
    case "bool":
      return value === "true" ? es.common.yes : value === "false" ? es.common.no : value;
    case "url": // a new edition of a price list lives on the same host
      return documentName(value);
    default:
      return value;
  }
}

/** CSS modifier of a saving: positive, negative, or none (zero or unknown). */
export function savingClass(pct: number | null): string {
  if (pct === null || pct === 0) return "";
  // A saving reads as a plain price cut; only a dearer bundle is flagged.
  return pct > 0 ? "" : "negative";
}

/**
 * Fail loudly with an actionable message instead of rendering an empty or broken site: every
 * file must exist and match the contract in types.ts (field names, JSON types, nullability).
 */
export function assertSiteData(raw: Partial<Record<string, unknown>>): SiteData {
  // Build errors stay in Spanish, like the problems validate.ts lists.
  const es = getMessages("es");
  const missing = DATA_FILES.filter((name) => raw[name] === undefined);
  if (missing.length > 0) {
    throw new Error(missing.map((name) => es.errors.missingData(name)).join("\n"));
  }
  const problems = DATA_FILES.flatMap((name) => validateFile(name, raw[name]));
  if (problems.length === 0) problems.push(...validateReferences(raw));
  if (problems.length > 0) throw new Error(es.errors.invalidData(problems));
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

/**
 * A country's name in `locale`: the catalog's Spanish name for Spanish, the runtime's region
 * names otherwise (the catalog's name, then the code, when Intl has none).
 */
export function localizedCountryName(
  cc: string,
  nameEs: string | undefined,
  locale: Locale,
): string {
  if (locale !== "es") {
    try {
      const name = new Intl.DisplayNames([formatFor(locale).intlLocale], { type: "region" }).of(cc);
      if (name && name !== cc) return name;
    } catch {
      // Not a region code Intl knows: fall back to the catalog.
    }
  }
  return nameEs ?? cc;
}

/** A plan's name: catalog labels are Spanish; the other locales translate the common words. */
export function planName(labelEs: string, msg: Messages): string {
  return msg.planNames[labelEs] ?? labelEs;
}

export function buildIndex(data: SiteData, locale: Locale = DEFAULT_LOCALE): SiteIndex {
  const msg = getMessages(locale);
  const fmt = formatFor(locale);
  const { compareNames, formatPrice } = fmt;
  const services = new Map(data.services.map((s) => [s.id, s]));
  const countries = new Map(data.countries.map((c) => [c.cc, c]));
  const labels = new Map<string, RiskLabel>(data.feasibility.labels.map((l) => [l.id, l]));
  const residenceCountry = countries.get(data.meta.residence);
  const stale = new Set(data.fx.stale ?? []);
  const offerCurrency = new Map(data.offers.map((o) => [o.obs_key, o.currency]));
  // site-data already limits changes.json to the window before as_of (or before today).
  const asOf = data.meta.as_of;
  // The day site-data ran: bundles.json's status (manual age, expiry) is dated on it.
  const generatedOn = data.meta.generated_at.slice(0, 10);
  const defaultProfile = resolveProfile(data.feasibility, data.feasibility.default_profile);
  // The pages show the default profile's records: offers.json carries that profile's verdicts.
  const defaultRecords = (
    keep: (r: FeasibilityView) => boolean,
    order: (a: FeasibilityView, b: FeasibilityView) => number,
  ) =>
    recordsForDisplay(
      data.feasibility.records.filter(keep),
      data.feasibility.default_profile,
      order,
    );

  const serviceName = (id: string) => services.get(id)?.name ?? id;
  const countryName = (cc: string) => localizedCountryName(cc, countries.get(cc)?.name_es, locale);
  const planOf = (serviceId: string, planId: string) =>
    services.get(serviceId)?.plans.find((p) => p.id === planId);

  const label = (id: string, rank?: number): LabelInfo => {
    const fromData = labels.get(id);
    const fallback = msg.riskLabels[id];
    // The data's names are Spanish: the other locales prefer their own copy of a label.
    const own = locale === "es" ? undefined : fallback;
    return {
      id,
      rank: fromData?.rank ?? rank ?? UNKNOWN_RANK,
      name: own?.name ?? fromData?.name_es ?? fallback?.name ?? id,
      description: own?.description ?? fromData?.description_es ?? fallback?.description ?? "",
    };
  };

  const index: SiteIndex = {
    ...data,
    locale,
    msg,
    fmt,
    residenceCountry,
    residenceName: countryName(data.meta.residence),
    residenceCurrency: residenceCountry?.currency ?? "EUR",
    defaultProfile,
    isSample: (data.meta.run_id ?? "").startsWith("sample"),
    service: (id) => services.get(id),
    country: (cc) => countries.get(cc),
    serviceName,
    countryName,
    planLabel: (serviceId, planId) =>
      planId ? planName(planOf(serviceId, planId)?.label_es ?? planId, msg) : msg.common.unknown,
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
            return compareNames(serviceName(a.service_id), serviceName(b.service_id));
          }
          const ra = planOf(a.service_id, a.plan_id)?.rank ?? 0;
          const rb = planOf(b.service_id, b.plan_id)?.rank ?? 0;
          if (ra !== rb) return ra - rb;
          return a.obs_key < b.obs_key ? -1 : 1;
        }),
    profileName: (id) => {
      const profile = data.feasibility.profiles.find((p) => p.id === id);
      const residence = profile?.capabilities.residence_country;
      const name = residence ? msg.profileNames[id]?.(countryName(residence)) : undefined;
      return name ?? profile?.name_es ?? id;
    },
    bundlesForService: (serviceId) =>
      data.bundles.filter((b) => isActiveBundle(b) && bundleCoversService(b, serviceId)),
    discountBundlesForService: (serviceId) =>
      data.bundles.filter((b) => isActiveBundle(b) && bundleDiscountsService(b, serviceId)),
    bundlesForCountry: (cc) => data.bundles.filter((b) => b.country === cc && isActiveBundle(b)),
    expiredBundlesForCountry: (cc) =>
      data.bundles.filter((b) => b.country === cc && !isActiveBundle(b)),
    bundleGroups: () => groupBundlesByVerdict(data.bundles, data.feasibility.default_profile),
    expiredBundles: () =>
      data.bundles
        .filter((b) => !isActiveBundle(b))
        .sort((a, b) => compareExpiredBundles(a, b, generatedOn)),
    bundleEndedOn: (bundle) => bundleEndedOn(bundle, generatedOn),
    watchedBundles: () =>
      [...data.bundles].sort(
        (a, b) =>
          compareNames(countryName(a.country), countryName(b.country)) ||
          compareNames(a.name, b.name) ||
          byId(a, b),
      ),
    bundleVerdicts: (bundle) =>
      splitBundleVerdicts(bundle.verdicts, data.feasibility.default_profile),
    daysBeforeGeneration: (iso) => (iso ? Math.round(daysBetween(iso, generatedOn)) : null),
    feasibilityForService: (serviceId) =>
      defaultRecords(
        (r) => r.service_id === serviceId,
        (a, b) => a.rank - b.rank || a.target_country.localeCompare(b.target_country),
      ),
    feasibilityForCountry: (cc) =>
      defaultRecords(
        (r) => r.target_country === cc,
        (a, b) => compareNames(serviceName(a.service_id), serviceName(b.service_id)),
      ),
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
        .sort((a, b) => compareNames(a.name, b.name));
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

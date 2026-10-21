/**
 * What the pages other than the comparator need to show prices and verdicts for the visitor's
 * profile (logic/visitor.ts) instead of the site's default one. Built once at build time into
 * /datos/<locale>/perfil.json (pages/datos/[locale]/perfil.json.ts), which each page fetches after it renders the
 * default profile's view (the no-JS fallback); the browser caches it across pages.
 *
 * Pure: buildProfileKit takes the site index, the rest only the kit.
 */

import { getMessages } from "../i18n";
import type { Locale } from "../i18n/locale";
import {
  type Cheapest,
  type CompareContext,
  type CompareOfferFields,
  cheapestFor,
  isCandidate,
  roundTo,
} from "./compare";
import { countryOptions, toCapabilities, type VisitorProfile } from "./profile";
import type { LabelInfo, SiteIndex } from "./site";
import { type PackedOffers, packOffers, unpackOffers } from "./transport";
import type { FeasibilityRule, ProfileCapabilities, RuleTree, RuleVerdict } from "./types";
import { UNKNOWN_RANK } from "./types";
import { evaluateRule, matchingPreset, relabelOffers, type Verdict, verdictLookup } from "./verdicts";

export interface KitResidence {
  cc: string;
  name: string;
  currency: string;
}

export interface ProfileKit {
  /** The locale of every name and label below (the page fetching it reads its copy in it). */
  locale: Locale;
  default_profile: string;
  /** The default profile's residence: the fallback when the visitor cannot be located. */
  default_residence: string;
  profiles: { id: string; capabilities: ProfileCapabilities }[];
  eea_countries: string[];
  rules: FeasibilityRule[];
  /** Countries with prices: where a visitor can live, sorted by name. */
  residences: KitResidence[];
  /** Countries a visitor can have ties with (the same ones). */
  tie_countries: string[];
  /** Names of every catalog country. */
  country_names: Record<string, string>;
  rates: Record<string, number>;
  fee_pct: number;
  /** Candidate offers, compare fields only (logic/transport.ts). */
  offers: PackedOffers;
  /** Every verdict label by id (name, description, rank) for badges. */
  labels: Record<string, LabelInfo>;
  /** Instrument names by id, for verdicts that pay with a card opened online (card_via). */
  instruments: Record<string, string>;
  /** bundles.json `rule` by bundle id (active bundles only). */
  bundle_rules: Record<string, { reviewed: RuleTree; pending: RuleTree | null }>;
}

export function buildProfileKit(site: SiteIndex): ProfileKit {
  const offers: CompareOfferFields[] = site.offers.filter(isCandidate);
  const priced = new Set(offers.map((o) => o.country));
  const covered = site.countries
    .map((c) => c.cc)
    .filter((cc) => priced.size === 0 || priced.has(cc));
  const names = Object.fromEntries(site.countries.map((c) => [c.cc, site.countryName(c.cc)]));
  return {
    locale: site.locale,
    default_profile: site.feasibility.default_profile,
    default_residence: site.meta.residence,
    profiles: site.feasibility.profiles.map((p) => ({ id: p.id, capabilities: p.capabilities })),
    eea_countries: site.feasibility.eea_countries,
    rules: site.feasibility.rules,
    residences: countryOptions(covered, names, site.fmt.intlLocale).map((o) => ({
      ...o,
      currency: site.country(o.cc)?.currency ?? "EUR",
    })),
    tie_countries: covered,
    country_names: names,
    rates: site.fx.rates,
    fee_pct: site.meta.fx_fee_pct,
    offers: packOffers(offers),
    labels: Object.fromEntries(
      [
        ...new Set([
          ...Object.keys(getMessages(site.locale).riskLabels),
          ...site.feasibility.labels.map((l) => l.id),
          ...site.offers.map((o) => o.label),
        ]),
      ].map((id) => [id, site.label(id)]),
    ),
    instruments: Object.fromEntries(site.feasibility.instruments.map((i) => [i.id, i.name])),
    bundle_rules: Object.fromEntries(
      site.bundles.flatMap((b) => (b.rule ? [[b.bundle_id, b.rule]] : [])),
    ),
  };
}

/** The visitor's profile applied to the kit: what every page needs to redraw. */
export interface ProfileView {
  profile: VisitorProfile;
  caps: ProfileCapabilities;
  residence: string;
  residenceName: string;
  residenceCurrency: string;
  /** The curated profile with exactly these capabilities (its records apply), else null. */
  preset: string | null;
  /** The (service, country) verdict, findings pending review counted, like the comparator. */
  verdict: (serviceId: string, country: string) => Verdict;
  /** The cheapest offers of one (service, plan) for this profile (compare.ts). */
  cheapest: (serviceId: string, planId: string) => Cheapest;
  /** The bundle's verdict for this profile; undefined without a published rule. */
  bundleVerdict: (bundleId: string) => RuleVerdict | undefined;
  /** Monthly EUR plus the FX fee when `currency` is not the residence's (compare.ts rule). */
  withFee: (eur: number | null, currency: string) => number | null;
}

export function profileView(kit: ProfileKit, profile: VisitorProfile): ProfileView {
  const caps = toCapabilities(profile);
  const home = kit.residences.find((r) => r.cc === profile.residence);
  const source = {
    default_profile: kit.default_profile,
    profiles: kit.profiles,
    rules: kit.rules,
    eea_countries: kit.eea_countries,
  };
  const residenceCurrency = home?.currency ?? "EUR";
  let relabelled: CompareOfferFields[] | null = null;
  const offers = () => {
    relabelled ??= relabelOffers(unpackOffers(kit.offers), source, caps, true);
    return relabelled;
  };
  const ctx: CompareContext = {
    rates: kit.rates,
    residence: profile.residence,
    residenceCurrency,
    feePct: kit.fee_pct,
  };
  const answers = {
    eea: new Set(kit.eea_countries),
    presets: new Map(kit.profiles.map((p) => [p.id, p.capabilities])),
  };
  return {
    profile,
    caps,
    residence: profile.residence,
    residenceName: home?.name ?? profile.residence,
    residenceCurrency,
    preset: matchingPreset(source, caps),
    verdict: verdictLookup(source, caps, true),
    cheapest: (serviceId, planId) => cheapestFor(serviceId, planId, offers(), ctx),
    bundleVerdict: (bundleId) => {
      const rule = kit.bundle_rules[bundleId];
      if (!rule) return undefined;
      return evaluateRule(rule.pending ?? rule.reviewed, caps, answers);
    },
    withFee: (eur, currency) => {
      if (eur === null) return null;
      if (currency === residenceCurrency) return eur;
      return roundTo(eur * (1 + kit.fee_pct / 100), 2);
    },
  };
}

/** Whether a verdict was evaluated (unknown ones are never chosen nor grouped as a level). */
export function isEvaluated(v: Pick<RuleVerdict, "rank">): boolean {
  return v.rank !== UNKNOWN_RANK;
}

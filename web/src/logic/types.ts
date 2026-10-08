/**
 * Site data contract: the JSON files that `haggler site-data` writes into web/src/data/.
 *
 * These types mirror the Python publish step (src/haggler/publish.py). Every number is a JSON
 * number; money is already rounded to 2 decimals. Keep this file in sync with the contract
 * described in the project plan: pages only ever read these shapes. validate.ts checks the same
 * shapes at runtime (assertSiteData), so a field changed here must change there too. A data/ dir without any
 * completed run still produces a valid contract (run_id/as_of/fx.as_of null, empty lists); CI
 * builds the site from that too.
 */

export type Channel = "web" | "ios_iap";
export type BillingPeriod = "month" | "quarter" | "year";
export type SourceKind = "official" | "secondary";
export type Extraction = "parser" | "llm";
export type ObsStatus = "accepted" | "quarantined" | "rejected" | "carried_forward";
export type CuratedStatus = "verified" | "unverified" | "expired";
export type Confidence = "low" | "medium" | "high";

/** Risk thresholds the visitor can accept (rank 4 = not feasible, 99 = unknown are never chosen). */
export const RISK_LEVELS = [0, 1, 2, 3] as const;
export type RiskLevel = (typeof RISK_LEVELS)[number];
export type RiskKey = `${RiskLevel}`;
export const UNKNOWN_RANK = 99;

export type KnownLabelId =
  | "official_home"
  | "eu_right_untested"
  | "tos_breach_low_enforcement"
  | "tos_breach_enforced"
  | "not_feasible"
  | "unknown";
/** Unknown future labels must still render, so the type stays open. */
export type LabelId = KnownLabelId | (string & {});

export interface Attribution {
  name: string;
  url: string;
  license: string;
}

export interface Meta {
  generated_at: string;
  /** Null before the first completed run. */
  run_id: string | null;
  /** Date of the run (YYYY-MM-DD); null before the first completed run. */
  as_of: string | null;
  residence: string;
  profile: string;
  /** Percent, e.g. 2 means 2 %. */
  fx_fee_pct: number;
  display_currencies: string[];
  attributions: Attribution[];
}

export interface PlanInfo {
  id: string;
  label_es: string;
  rank: number;
  ads: boolean;
}

export interface ServiceInfo {
  id: string;
  name: string;
  category: string;
  homepage: string;
  default_plan: string;
  notes_es: string;
  eu_geo_class: string;
  plans: PlanInfo[];
}

export interface CountryInfo {
  cc: string;
  name_es: string;
  currency: string;
  region: string;
  eu_eea: boolean;
}

export interface FxData {
  /** Null when no FX table was published yet (rates is then empty). */
  as_of: string | null;
  base: "EUR";
  /** Units of currency per 1 EUR. */
  rates: Record<string, number>;
  stale: string[];
}

export interface Offer {
  obs_key: string;
  service_id: string;
  plan_id: string;
  country: string;
  channel: Channel;
  currency: string;
  amount: number;
  billing_period: BillingPeriod;
  monthly_equiv: number;
  /** Null when no FX rate was available for the currency. */
  monthly_eur: number | null;
  vat_included: boolean | null;
  source_url: string;
  source_kind: SourceKind;
  extraction: Extraction;
  status: ObsStatus;
  flags: string[];
  first_seen: string;
  last_changed: string;
  label: LabelId;
  rank: number;
  eu_badge: boolean;
}

export interface RiskLabel {
  id: LabelId;
  rank: number;
  name_es: string;
  description_es: string;
}

export interface FeasibilityView {
  service_id: string;
  target_country: string;
  label: LabelId;
  rank: number;
  eu_badge: boolean;
  legality: string;
  tos: string;
  practical: string;
  confidence: Confidence;
  status: CuratedStatus;
  notes_es: string;
  tos_clause_url: string | null;
}

export interface FeasibilityData {
  labels: RiskLabel[];
  records: FeasibilityView[];
}

export interface OfferRef {
  obs_key: string;
  country: string;
  channel: Channel;
  currency: string;
  amount: number;
  monthly_equiv: number;
  cost_eur: number;
  cost_eur_with_fee: number;
  label: LabelId;
  rank: number;
  eu_badge: boolean;
  source_url: string;
}

export interface CheapestRow {
  service_id: string;
  plan_id: string;
  home: OfferRef | null;
  best_by_risk: Record<RiskKey, OfferRef | null>;
  savings_month_eur: Record<RiskKey, number | null>;
}

export type BundleInclusion =
  | "included"
  | "discount_pct"
  | "discount_abs"
  | "free_months"
  | "choose_n";

export interface BundlePrice {
  amount: number | null;
  currency: string;
  period: "month" | "year";
  tax_included: boolean | null;
  marginal_amount: number | null;
}

export interface BundleComponent {
  service_id: string;
  plan_id: string | null;
  inclusion: BundleInclusion;
  value: number | null;
  ads: boolean | null;
}

export interface BundleRequirements {
  residency?: string | null;
  local_id?: string;
  local_line?: string;
  local_payment?: string;
  credit_check?: boolean | null;
}

export interface BundleFeasibility {
  purchasable_remotely: string;
  usable_abroad: string;
  tos_risk: string;
  evidence: string;
}

export interface Bundle {
  bundle_id: string;
  provider_id: string;
  provider_type: string;
  country: string;
  name: string;
  tier: string;
  official_url: string;
  price: BundlePrice;
  monthly_eur: number | null;
  components: BundleComponent[];
  standalone_sum_eur: number | null;
  /** Percent saved vs. buying the included components separately; negative = more expensive. */
  saving_pct: number | null;
  requirements: BundleRequirements;
  feasibility: BundleFeasibility | null;
  status: CuratedStatus;
  notes_es: string;
  last_verified: string;
}

/** Old/new value of a change: an amount, a preformatted string, or a {amount, currency} pair. */
export type ChangeValue = number | string | { amount: number; currency?: string } | null;

export interface ChangeRow {
  date: string;
  obs_key: string;
  service_id: string;
  plan_id: string;
  country: string;
  channel: Channel;
  kind: string;
  old: ChangeValue;
  new: ChangeValue;
  /** Percent change (7.5 = +7.5 %), null for additions/removals. */
  pct: number | null;
}

export interface CoverageRow {
  service_id: string;
  country: string;
  channel: Channel;
  status: string;
  adapter_id: string;
  failing_since: string | null;
}

export interface SiteData {
  meta: Meta;
  services: ServiceInfo[];
  countries: CountryInfo[];
  fx: FxData;
  offers: Offer[];
  feasibility: FeasibilityData;
  cheapest: CheapestRow[];
  bundles: Bundle[];
  changes: ChangeRow[];
  coverage: CoverageRow[];
}

/** File names (without .json) that must exist in web/src/data/. */
export const DATA_FILES = [
  "meta",
  "services",
  "countries",
  "fx",
  "offers",
  "feasibility",
  "cheapest",
  "bundles",
  "changes",
  "coverage",
] as const satisfies readonly (keyof SiteData)[];

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
  /** The site's default feasibility profile (== feasibility.default_profile). */
  profile: string;
  /** Last feasibility research run; null before the first one. */
  research_run_id: string | null;
  /** Date of that research run (YYYY-MM-DD); null before the first one. */
  research_as_of: string | null;
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

/**
 * Where a feasibility record comes from: written by hand ("curated", always wins), derived by the
 * research evaluator from findings a person accepted ("agent_reviewed"), or derived including
 * findings still pending review ("agent_pending": never used unless the visitor opts in).
 */
export const PROVENANCES = ["curated", "agent_reviewed", "agent_pending"] as const;
export type Provenance = (typeof PROVENANCES)[number];

/** Axis value of a research-derived record whose facts are missing (its rank is then 99). */
export const UNKNOWN_AXIS = "unknown";

export type KnownEvidenceKind = "official" | "press" | "forum" | "aggregator" | "user_report";
/** Unknown future source classes must still render, so the type stays open. */
export type EvidenceKind = KnownEvidenceKind | (string & {});

/**
 * What the evaluator can tell about a profile (haggler.models.profiles.Profile.capabilities):
 * the comparator's profile builder edits one of these, and the rules (FeasibilityRule) ask it
 * questions. address_countries includes the residence.
 */
export interface ProfileCapabilities {
  residence_country: string;
  id_document_countries: string[];
  phone_countries: string[];
  address_countries: string[];
  /** Countries where a card the person holds was issued. */
  card_countries: string[];
  /** Country -> how often the person can be there. */
  presence: Record<string, PresenceKind>;
}

export type PresenceKind = "once" | "periodic";

/** A public profile: who the verdicts are computed for (default profile first). */
export interface Profile {
  id: string;
  name_es: string;
  description_es: string;
  residence_country: string;
  citizenships: string[];
  capabilities: ProfileCapabilities;
}

/** The verdict at a leaf of a rule tree (provenance null = no evaluated record). */
export interface RuleVerdict {
  label: LabelId;
  rank: number;
  eu_badge: boolean;
  provenance: Provenance | "home" | null;
}

/**
 * A pair's verdict as a decision tree over the profile (haggler.profile_rules): an inner node
 * asks one question (["lives", "CO"], ["doc", "CO"], ["presence", "CO"], ["preset", id] ...)
 * and follows the branch of the answer ("true" | "false", or "none" | "once" | "periodic").
 */
export type RuleTree =
  { verdict: RuleVerdict } | { ask: string[]; answers: Record<string, RuleTree> };

/**
 * One (service, country) pair's rule. Pairs without one get the plain rule: official_home in
 * the residence country, else unknown. pending (findings pending review included) is null when
 * it equals reviewed.
 */
export interface FeasibilityRule {
  service_id: string;
  target_country: string;
  reviewed: RuleTree;
  pending: RuleTree | null;
}

/** One step of the route to the foreign offer (e.g. a payment card or a trip to the country). */
export interface Step {
  type: string;
  value: string;
  requires_presence: boolean;
  requires_false_declaration: boolean;
}

/** A cited source: the literal quote, and whether our own fetch of the page found it. */
export interface Evidence {
  url: string;
  kind: EvidenceKind;
  quote: string;
  /** Date of our fetch (YYYY-MM-DD); null for hand-curated citations. */
  retrieved_at: string | null;
  verified: boolean;
}

export interface FeasibilityView {
  /** Profile id (one of FeasibilityData.profiles). */
  profile: string;
  service_id: string;
  target_country: string;
  label: LabelId;
  rank: number;
  eu_badge: boolean;
  /** Axis values; any of them may be "unknown" (UNKNOWN_AXIS) for research-derived records. */
  legality: string;
  tos: string;
  practical: string;
  confidence: Confidence;
  status: CuratedStatus;
  provenance: Provenance;
  notes_es: string;
  tos_clause_url: string | null;
  steps: Step[];
  evidence: Evidence[];
  assumptions_es: string[];
  reasons_es: string[];
  /** YYYY-MM-DD; null when unknown. */
  researched_on: string | null;
}

/**
 * Every public profile's records. A (profile, service, country) has at most one non-pending
 * record (curated or agent_reviewed) and one agent_pending record (the verdict when pending
 * findings are included, listed only when it differs). Order: default profile first, then by
 * profile, service_id, target_country, non-pending before pending. offers.json and
 * cheapest.json carry the default profile's verdicts with pending findings excluded.
 */
export interface FeasibilityData {
  labels: RiskLabel[];
  default_profile: string;
  profiles: Profile[];
  records: FeasibilityView[];
  /** Verdicts for any profile, for the comparator's profile builder. */
  rules: FeasibilityRule[];
  /** EU/EEA country codes (the rules' "lives_eea" and "doc_eea" questions). */
  eea_countries: string[];
  /** Payment instruments anyone can open online (the comparator's residence suggestions). */
  instruments: PaymentInstrumentView[];
}

/**
 * A payment instrument opened online. eligible_residence holds country codes, "EEA" (any
 * EU/EEA residence) or "ANY"; required_id_countries, when not empty, the countries one of whose
 * ID documents the issuer asks for. card_country is the observed card BIN country (null:
 * not observed yet, never inferred from the issuer's licence).
 */
export interface PaymentInstrumentView {
  id: string;
  name: string;
  issuer_country: string;
  iban_country: string | null;
  eligible_residence: string[];
  required_id_countries: string[];
  card_country: string | null;
  currencies: string[];
  url: string | null;
  notes_es: string;
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

export const BUNDLE_INCLUSIONS = [
  "included",
  "discount_pct",
  "discount_abs",
  "free_months",
  "choose_n",
] as const;
export type BundleInclusion = (typeof BUNDLE_INCLUSIONS)[number];

export const BUNDLE_PERIODS = ["month", "year"] as const;
export type BundlePeriod = (typeof BUNDLE_PERIODS)[number];

/** "observed": the price is today's row of observed_service_id in offers.json. */
export const BUNDLE_PRICE_SOURCES = ["curated", "observed"] as const;
export type BundlePriceSource = (typeof BUNDLE_PRICE_SOURCES)[number];

/** What a bundle's state is derived from (never typed in the YAML), most trusted first. */
export const BUNDLE_DISPLAY_STATUSES = [
  "verified",
  "needs_review",
  "manual",
  "unverified",
  "expired",
] as const;
export type BundleDisplayStatus = (typeof BUNDLE_DISPLAY_STATUSES)[number];

/** Where a bundle's values come from (user_report never counts as an official source). */
export const BUNDLE_VERIFIED_BY = [
  "official_fetch",
  "user_report",
  "search_summary",
  "agent_research",
] as const;
export type BundleVerifiedBy = (typeof BUNDLE_VERIFIED_BY)[number];

export const WATCH_MODES = ["auto", "manual", "off"] as const;
export type WatchMode = (typeof WATCH_MODES)[number];

/** "marginal": the cost on top of a base plan the subscriber already pays (a telco add-on). */
export const SAVING_BASES = ["full", "marginal"] as const;
export type SavingBasis = (typeof SAVING_BASES)[number];

export const CONFIDENCES = ["low", "medium", "high"] as const satisfies readonly Confidence[];

/** A condition the provider imposes; "unknown" is never read as "not required". */
export const REQUIREMENT_VALUES = ["required", "not_required", "unknown"] as const;
export type RequirementValue = (typeof REQUIREMENT_VALUES)[number];

/** Whether the provider checks that the subscriber is in the bundle's country at signup. */
export const SIGNUP_PRESENCE_VALUES = ["checked", "not_checked", "unknown"] as const;
export type SignupPresence = (typeof SIGNUP_PRESENCE_VALUES)[number];

/**
 * Where a bundle verdict comes from: the residence country ("home"), a hand-written record
 * ("curated"), the bundle's own requirements plus its components' verdicts ("composed"), or the
 * research evaluator with reviewed / pending findings.
 */
export const BUNDLE_PROVENANCES = [
  "home",
  "curated",
  "composed",
  "agent_reviewed",
  "agent_pending",
] as const;
export type BundleProvenance = (typeof BUNDLE_PROVENANCES)[number];

/** A component's verdict comes from its service's own record (null: not evaluated). */
export const COMPONENT_PROVENANCES = [
  "home",
  "curated",
  "agent_reviewed",
  "agent_pending",
] as const;
export type ComponentProvenance = (typeof COMPONENT_PROVENANCES)[number];

export const VERDICT_STATUSES = ["unverified", "verified"] as const;
export type VerdictStatus = (typeof VERDICT_STATUSES)[number];

/** Every amount is in `currency` per `period`; null when no source gives it. */
export interface BundlePrice {
  /** The regular price (never the intro one); null for an observed price without today's row. */
  amount: number | null;
  currency: string;
  period: BundlePeriod;
  tax_included: boolean;
  source: BundlePriceSource;
  /** Date of the offers.json row an observed price comes from (YYYY-MM-DD). */
  observed_on: string | null;
  base_plan_name: string | null;
  base_plan_amount: number | null;
  /** amount - base_plan_amount: the cost on top of the base plan. */
  marginal_amount: number | null;
  intro_amount: number | null;
  intro_months: number | null;
  intro_valid_until: string | null;
  contract_months: number | null;
}

export interface BundleComponent {
  service_id: string;
  plan_id: string | null;
  inclusion: BundleInclusion;
  /** Percent for discount_pct, an amount in the bundle's currency for discount_abs, months. */
  value: number | null;
  ads: boolean | null;
  choice_group_id: string | null;
  /** How many of the choice group the subscriber picks. */
  choose_n: number | null;
  /** Cheapest monthly EUR price of the service bought on its own in the bundle's country. */
  standalone_eur: number | null;
  /** True once the watcher found the component on an official page (never before H7). */
  confirmed: boolean;
}

/** What the subscriber saves per month IF they also subscribe to a discounted service. */
export interface BundleOptionalSaving {
  service_id: string;
  inclusion: BundleInclusion;
  value: number | null;
  /** Null for free months, or without a standalone price. */
  saving_month_eur: number | null;
}

export interface BundleRequirements {
  /** Local ID document ("cedula", "CURP", "personnummer"…, "none"); null when not established. */
  local_id: string | null;
  /** "mobile", "fixed", "none"…; null when not established. */
  local_line: string | null;
  /** "any_card", "local_card", "local_bank"…; null when not established. */
  local_payment: string | null;
  credit_check: boolean | null;
  residency: RequirementValue;
  billing_address: RequirementValue;
  signup_presence: SignupPresence;
  new_customers_only: boolean | null;
}

/** A cited source of a bundle value; the watcher fills fact/via/edition/status (H7). */
export interface BundleEvidence {
  /** price | base_price | intro | component | validity; null for a plain source link. */
  fact: string | null;
  url: string;
  kind: EvidenceKind;
  quote: string | null;
  /** Date of our own fetch (YYYY-MM-DD); null until the watcher read the page. */
  retrieved_at: string | null;
  verified: boolean;
  /** How the quote was found ("embedded" data instead of visible text…). */
  via: string | null;
  /** Edition of a versioned document (a price-list PDF). */
  edition: string | null;
  status: string | null;
}

/** The verdict of one component (its own service in the bundle's country) for the profile. */
export interface BundleVerdictComponent {
  service_id: string;
  target_country: string;
  label: LabelId;
  rank: number;
  provenance: ComponentProvenance | null;
}

/**
 * One profile's verdict on a bundle: the same labels, axes and steps as feasibility.json, plus
 * each included component's verdict (the bundle's rank is never below the worst of them).
 */
export interface BundleVerdict {
  profile: string;
  provenance: BundleProvenance;
  label: LabelId;
  rank: number;
  eu_badge: boolean;
  legality: string;
  tos: string;
  practical: string;
  confidence: Confidence;
  status: VerdictStatus;
  notes_es: string;
  steps: Step[];
  assumptions_es: string[];
  reasons_es: string[];
  components: BundleVerdictComponent[];
}

/** Who proposed a value change: the deterministic watcher, or the research agent. */
export const PENDING_SOURCES = ["watcher", "agent"] as const;
export type PendingSource = (typeof PENDING_SOURCES)[number];

/**
 * The form of a pending change's values (research vocabulary codes, never prose):
 * money "21900 COP/month", int "2", date "2026-12-31", service_ref "disney_plus" or
 * "disney_plus.standard_with_ads", service_pct "hbo_max:30", bool "true" | "false",
 * url an https URL.
 */
export const PENDING_VALUE_TYPES = [
  "money",
  "int",
  "date",
  "service_ref",
  "service_pct",
  "bool",
  "url",
] as const;
export type PendingValueType = (typeof PENDING_VALUE_TYPES)[number];

/**
 * A value an official page of the bundle's country states and bundles.yaml does not: a watcher
 * price hint or an agent finding whose value check passed. It disappears once the YAML holds
 * the value (or when it is rejected); the YAML stays the only source of the bundle's values.
 */
export interface BundlePendingChange {
  id: string;
  source: PendingSource;
  /** The research field (bundle_price, bundle_intro_price, bundle_includes…). */
  field: string;
  value_type: PendingValueType;
  /** The YAML value it would replace (same form); null when the YAML has none. */
  current: string | null;
  proposed: string;
  /** Literal excerpt of the official page that states the proposed value. */
  quote: string;
  url: string;
  /** Date of our own fetch of the page (YYYY-MM-DD). */
  retrieved_at: string | null;
  first_seen: string | null;
  /** More than 50 % away from the YAML value (money fields). */
  big_change: boolean;
}

/**
 * bundles.json v2. Costs are monthly EUR rounded to 2 decimals: monthly_eur is the regular cost
 * basis of the saving (the marginal cost when the bundle sits on a base plan, yearly prices /12,
 * never an intro price); saving_pct compares it with standalone_sum_eur (a lower bound: the
 * cheapest standalone price of each included component, partial when standalone_missing is not
 * empty) and never counts the FX fee, intro prices, discounts or free months.
 */
export interface Bundle {
  bundle_id: string;
  provider_id: string;
  provider_type: string;
  country: string;
  name: string;
  tier: string;
  official_url: string;
  /** The service whose offers.json row gives the price (price.source "observed"). */
  observed_service_id: string | null;
  /** Service ids the bundle stands in for (by default its included components). */
  covers: string[];
  price: BundlePrice;
  monthly_eur: number | null;
  /** Full regular price per month, base plan included. */
  full_monthly_eur: number | null;
  /** monthly_eur plus the FX fee when the bundle's currency is not the residence's. */
  monthly_eur_with_fee: number | null;
  /** Average monthly cost of the first year with a current, verified intro price. */
  first_year_monthly_eur: number | null;
  saving_basis: SavingBasis;
  standalone_sum_eur: number | null;
  standalone_missing: string[];
  /** Percent saved vs. buying the included components separately; negative = more expensive. */
  saving_pct: number | null;
  optional_savings: BundleOptionalSaving[];
  components: BundleComponent[];
  requirements: BundleRequirements;
  display_status: BundleDisplayStatus;
  /** Why the bundle is not verified (at least one unless display_status is "verified"). */
  status_reasons_es: string[];
  verified_by: BundleVerifiedBy;
  verified_at: string | null;
  checked_at: string | null;
  valid_from: string | null;
  valid_until: string | null;
  confidence: Confidence;
  watch_mode: WatchMode;
  evidence: BundleEvidence[];
  /**
   * One verdict per public profile (default profile first, then by id); a pending variant
   * follows its reviewed one only when it differs. Empty when the bundle expired.
   */
  verdicts: BundleVerdict[];
  /** Official, value-checked changes awaiting review (the card lists them). */
  pending_changes: BundlePendingChange[];
  notes_es: string;
  /** Last human review of the YAML row (YYYY-MM-DD). */
  last_verified: string | null;
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

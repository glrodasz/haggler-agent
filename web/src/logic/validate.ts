/**
 * Runtime check of the site data contract (types.ts) against the JSON that `haggler site-data`
 * writes. The files are imported as `unknown`, so `astro check` never sees their shape: without
 * this a renamed field, a type change or an unexpected null in publish.py builds fine and renders
 * "undefined" (or a page without links). assertSiteData calls it, so `astro build`, vitest and
 * CI's contract job all fail on that drift with the file, record and field at fault.
 *
 * It checks presence, JSON type and nullability of every field types.ts declares. Enum values
 * are mostly left open (the pages fall back to the raw value for an id they do not know), except
 * where they decide what is computed or shown as trustworthy, which are checked exactly: a
 * record's provenance (pending findings are only used on request), the profile ids that
 * feasibility.json, meta.json and bundles.json refer to, and every closed enum of bundles.json
 * v2 (display status, saving basis, price source and period, provenance, watch mode, verified_by,
 * confidence, the requirement values and a pending change's source and value type).
 */

import {
  BUNDLE_DISPLAY_STATUSES,
  BUNDLE_INCLUSIONS,
  BUNDLE_PERIODS,
  BUNDLE_PRICE_SOURCES,
  BUNDLE_PROVENANCES,
  BUNDLE_VERIFIED_BY,
  COMPONENT_PROVENANCES,
  CONFIDENCES,
  type DATA_FILES,
  PENDING_SOURCES,
  PENDING_VALUE_TYPES,
  PROVENANCES,
  REQUIREMENT_VALUES,
  SAVING_BASES,
  SIGNUP_PRESENCE_VALUES,
  VERDICT_STATUSES,
  WATCH_MODES,
} from "./types";

type DataFile = (typeof DATA_FILES)[number];
type Check = (value: unknown, path: string, problems: string[]) => void;

/** At most this many problems are reported (the first ones, in file order). */
const MAX_PROBLEMS = 20;

function kindOf(value: unknown): string {
  if (value === null) return "null";
  if (Array.isArray(value)) return "una lista";
  if (typeof value === "object") return "un objeto";
  return `${typeof value} ${JSON.stringify(value)}`;
}

function primitive(kind: "string" | "number" | "boolean"): Check {
  return (value, path, problems) => {
    const ok = typeof value === kind && (kind !== "number" || Number.isFinite(value));
    if (!ok) problems.push(`${path}: se esperaba ${kind}, hay ${kindOf(value)}`);
  };
}

const str = primitive("string");
const num = primitive("number");
const bool = primitive("boolean");

function nullable(check: Check): Check {
  return (value, path, problems) => {
    if (value !== null) check(value, path, problems);
  };
}

function isObject(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}

function list(item: Check): Check {
  return (value, path, problems) => {
    if (!Array.isArray(value)) {
      problems.push(`${path}: se esperaba una lista, hay ${kindOf(value)}`);
      return;
    }
    value.forEach((v, i) => {
      if (problems.length < MAX_PROBLEMS) item(v, `${path}[${i}]`, problems);
    });
  };
}

/** An object with arbitrary keys whose values all pass `item` (e.g. fx.rates). */
function mapOf(item: Check): Check {
  return (value, path, problems) => {
    if (!isObject(value)) {
      problems.push(`${path}: se esperaba un objeto, hay ${kindOf(value)}`);
      return;
    }
    for (const [k, v] of Object.entries(value)) item(v, `${path}.${k}`, problems);
  };
}

/** Every `required` key must be present (null only where its check allows it). */
function shape(required: Record<string, Check>, optional: Record<string, Check> = {}): Check {
  return (value, path, problems) => {
    if (!isObject(value)) {
      problems.push(`${path}: se esperaba un objeto, hay ${kindOf(value)}`);
      return;
    }
    for (const [key, check] of Object.entries(required)) {
      if (!(key in value)) problems.push(`${path}: falta el campo "${key}"`);
      else check(value[key], `${path}.${key}`, problems);
    }
    for (const [key, check] of Object.entries(optional)) {
      if (key in value && value[key] !== undefined) check(value[key], `${path}.${key}`, problems);
    }
  };
}

/** A string from a closed set. */
function oneOf(values: readonly string[]): Check {
  return (value, path, problems) => {
    if (typeof value !== "string" || !values.includes(value)) {
      problems.push(`${path}: se esperaba ${values.join(" | ")}, hay ${kindOf(value)}`);
    }
  };
}

/** Every check in order (e.g. a shape, then a rule across its fields). */
function allOf(...checks: Check[]): Check {
  return (value, path, problems) => {
    for (const check of checks) check(value, path, problems);
  };
}

function anyOf(label: string, ...checks: Check[]): Check {
  return (value, path, problems) => {
    const ok = checks.some((check) => {
      const scratch: string[] = [];
      check(value, path, scratch);
      return scratch.length === 0;
    });
    if (!ok) problems.push(`${path}: se esperaba ${label}, hay ${kindOf(value)}`);
  };
}

const offerRef = shape({
  obs_key: str,
  country: str,
  channel: str,
  currency: str,
  amount: num,
  monthly_equiv: num,
  cost_eur: num,
  cost_eur_with_fee: num,
  label: str,
  rank: num,
  eu_badge: bool,
  source_url: str,
});

/** One entry per accepted risk level ("0".."3"), as RiskKey in types.ts. */
function byRisk(item: Check): Check {
  return shape({ "0": item, "1": item, "2": item, "3": item });
}

/**
 * feasibility.json: default_profile and every record's profile must be one of `profiles` (the
 * comparator and the pages pick records by profile id). Runs after the shape check, so it only
 * looks at fields that already have the right type.
 */
const profileRefs: Check = (value, path, problems) => {
  if (!isObject(value) || !Array.isArray(value.profiles)) return;
  const ids = new Set(value.profiles.map((p) => (isObject(p) ? p.id : undefined)));
  const unknown = (id: unknown) => typeof id === "string" && !ids.has(id);
  if (unknown(value.default_profile)) {
    problems.push(`${path}.default_profile: "${value.default_profile}" no está en profiles`);
  }
  if (!Array.isArray(value.records)) return;
  value.records.forEach((r, i) => {
    if (problems.length < MAX_PROBLEMS && isObject(r) && unknown(r.profile)) {
      problems.push(`${path}.records[${i}].profile: "${r.profile}" no está en profiles`);
    }
  });
};

const ruleVerdict = shape(
  {
    label: str,
    rank: num,
    eu_badge: bool,
    provenance: nullable(oneOf([...new Set([...PROVENANCES, ...BUNDLE_PROVENANCES, "home"])])),
  },
  { card_via: str },
);

/** A rule tree node: a {verdict} leaf, or {ask, answers} with a subtree per answer. */
const ruleTree: Check = (value, path, problems) => {
  if (isObject(value) && "verdict" in value) {
    ruleVerdict(value.verdict, `${path}.verdict`, problems);
    return;
  }
  shape({ ask: list(str), answers: mapOf(ruleTree) })(value, path, problems);
};

const profileCapabilities = shape({
  residence_country: str,
  id_document_countries: list(str),
  phone_countries: list(str),
  address_countries: list(str),
  card_countries: list(str),
  presence: mapOf(oneOf(["once", "periodic"])),
});

const changeValue = anyOf(
  "número, texto, {amount, currency} o null",
  nullable(num),
  str,
  shape({ amount: num }, { currency: str }),
);

const step = shape({
  type: str,
  value: str,
  requires_presence: bool,
  requires_false_declaration: bool,
});

/** bundles.json v2 (Bundle in types.ts): every key present, null only where types.ts allows. */
const bundle = shape({
  bundle_id: str,
  provider_id: str,
  provider_type: str,
  country: str,
  name: str,
  tier: str,
  official_url: str,
  observed_service_id: nullable(str),
  covers: list(str),
  price: shape({
    amount: nullable(num),
    currency: str,
    period: oneOf(BUNDLE_PERIODS),
    tax_included: bool,
    source: oneOf(BUNDLE_PRICE_SOURCES),
    observed_on: nullable(str),
    base_plan_name: nullable(str),
    base_plan_amount: nullable(num),
    marginal_amount: nullable(num),
    intro_amount: nullable(num),
    intro_months: nullable(num),
    intro_valid_until: nullable(str),
    contract_months: nullable(num),
  }),
  monthly_eur: nullable(num),
  full_monthly_eur: nullable(num),
  monthly_eur_with_fee: nullable(num),
  first_year_monthly_eur: nullable(num),
  saving_basis: oneOf(SAVING_BASES),
  standalone_sum_eur: nullable(num),
  standalone_missing: list(str),
  saving_pct: nullable(num),
  optional_savings: list(
    shape({
      service_id: str,
      inclusion: oneOf(BUNDLE_INCLUSIONS),
      value: nullable(num),
      saving_month_eur: nullable(num),
    }),
  ),
  components: list(
    shape({
      service_id: str,
      plan_id: nullable(str),
      inclusion: oneOf(BUNDLE_INCLUSIONS),
      value: nullable(num),
      ads: nullable(bool),
      choice_group_id: nullable(str),
      choose_n: nullable(num),
      standalone_eur: nullable(num),
      confirmed: bool,
    }),
  ),
  requirements: shape({
    local_id: nullable(str),
    local_line: nullable(str),
    local_payment: nullable(str),
    credit_check: nullable(bool),
    residency: oneOf(REQUIREMENT_VALUES),
    billing_address: oneOf(REQUIREMENT_VALUES),
    signup_presence: oneOf(SIGNUP_PRESENCE_VALUES),
    new_customers_only: nullable(bool),
  }),
  display_status: oneOf(BUNDLE_DISPLAY_STATUSES),
  status_reasons_es: list(str),
  status_reasons_en: list(str),
  status_reasons_pt: list(str),
  verified_by: oneOf(BUNDLE_VERIFIED_BY),
  verified_at: nullable(str),
  checked_at: nullable(str),
  valid_from: nullable(str),
  valid_until: nullable(str),
  confidence: oneOf(CONFIDENCES),
  watch_mode: oneOf(WATCH_MODES),
  evidence: list(
    shape({
      fact: nullable(str),
      url: str,
      kind: str,
      quote: nullable(str),
      retrieved_at: nullable(str),
      verified: bool,
      via: nullable(str),
      edition: nullable(str),
      status: nullable(str),
    }),
  ),
  rule: nullable(shape({ reviewed: ruleTree, pending: nullable(ruleTree) })),
  verdicts: list(
    shape({
      profile: str,
      provenance: oneOf(BUNDLE_PROVENANCES),
      label: str,
      rank: num,
      eu_badge: bool,
      legality: str,
      tos: str,
      practical: str,
      confidence: oneOf(CONFIDENCES),
      status: oneOf(VERDICT_STATUSES),
      notes_es: str,
      notes_en: str,
      notes_pt: str,
      steps: list(step),
      assumptions_es: list(str),
      assumptions_en: list(str),
      assumptions_pt: list(str),
      reasons_es: list(str),
      reasons_en: list(str),
      reasons_pt: list(str),
      components: list(
        shape({
          service_id: str,
          target_country: str,
          label: str,
          rank: num,
          provenance: nullable(oneOf(COMPONENT_PROVENANCES)),
        }),
      ),
    }),
  ),
  pending_changes: list(
    shape({
      id: str,
      source: oneOf(PENDING_SOURCES),
      field: str,
      value_type: oneOf(PENDING_VALUE_TYPES),
      current: nullable(str),
      proposed: str,
      quote: str,
      url: str,
      retrieved_at: nullable(str),
      first_seen: nullable(str),
      big_change: bool,
    }),
  ),
  notes_es: str,
  notes_en: str,
  notes_pt: str,
  last_verified: nullable(str),
});

/** The contract per file name (without .json), mirroring types.ts. */
export const SITE_DATA_SCHEMA = {
  meta: shape({
    generated_at: str,
    run_id: nullable(str),
    as_of: nullable(str),
    residence: str,
    profile: str,
    research_run_id: nullable(str),
    research_as_of: nullable(str),
    fx_fee_pct: num,
    display_currencies: list(str),
    attributions: list(shape({ name: str, url: str, license: str })),
  }),
  services: list(
    shape({
      id: str,
      name: str,
      category: str,
      homepage: str,
      default_plan: str,
      notes_es: str,
      notes_en: str,
      notes_pt: str,
      eu_geo_class: str,
      plans: list(shape({ id: str, label_es: str, rank: num, ads: bool })),
    }),
  ),
  countries: list(shape({ cc: str, name_es: str, currency: str, region: str, eu_eea: bool })),
  fx: shape({ as_of: nullable(str), base: str, rates: mapOf(num), stale: list(str) }),
  offers: list(
    shape({
      obs_key: str,
      service_id: str,
      plan_id: str,
      country: str,
      channel: str,
      currency: str,
      amount: num,
      billing_period: str,
      monthly_equiv: num,
      monthly_eur: nullable(num),
      vat_included: nullable(bool),
      source_url: str,
      source_kind: str,
      extraction: str,
      status: str,
      flags: list(str),
      first_seen: str,
      last_changed: str,
      label: str,
      rank: num,
      eu_badge: bool,
    }),
  ),
  feasibility: allOf(
    shape({
      labels: list(shape({ id: str, rank: num, name_es: str, description_es: str })),
      default_profile: str,
      profiles: list(
        shape({
          id: str,
          name_es: str,
          description_es: str,
          residence_country: str,
          citizenships: list(str),
          capabilities: profileCapabilities,
        }),
      ),
      records: list(
        shape({
          profile: str,
          service_id: str,
          target_country: str,
          label: str,
          rank: num,
          eu_badge: bool,
          legality: str,
          tos: str,
          practical: str,
          confidence: str,
          status: str,
          provenance: oneOf(PROVENANCES),
          notes_es: str,
          notes_en: str,
          notes_pt: str,
          tos_clause_url: nullable(str),
          steps: list(step),
          evidence: list(
            shape({
              url: str,
              kind: str,
              quote: str,
              retrieved_at: nullable(str),
              verified: bool,
            }),
          ),
          assumptions_es: list(str),
          assumptions_en: list(str),
          assumptions_pt: list(str),
          reasons_es: list(str),
          reasons_en: list(str),
          reasons_pt: list(str),
          researched_on: nullable(str),
        }),
      ),
      rules: list(
        shape({
          service_id: str,
          target_country: str,
          reviewed: ruleTree,
          pending: nullable(ruleTree),
        }),
      ),
      eea_countries: list(str),
      instruments: list(
        shape({
          id: str,
          name: str,
          issuer_country: str,
          iban_country: nullable(str),
          eligible_residence: list(str),
          required_id_countries: list(str),
          card_country: nullable(str),
          currencies: list(str),
          url: nullable(str),
          notes_es: str,
          notes_en: str,
          notes_pt: str,
        }),
      ),
    }),
    profileRefs,
  ),
  cheapest: list(
    shape({
      service_id: str,
      plan_id: str,
      home: nullable(offerRef),
      best_by_risk: byRisk(nullable(offerRef)),
      savings_month_eur: byRisk(nullable(num)),
    }),
  ),
  bundles: list(bundle),
  changes: list(
    shape({
      date: str,
      obs_key: str,
      service_id: str,
      plan_id: str,
      country: str,
      channel: str,
      kind: str,
      old: changeValue,
      new: changeValue,
      pct: nullable(num),
    }),
  ),
  coverage: list(
    shape({
      service_id: str,
      country: str,
      channel: str,
      status: str,
      adapter_id: str,
      failing_since: nullable(str),
    }),
  ),
} satisfies Record<DataFile, Check>;

/**
 * Rules across files: offers.json and cheapest.json carry meta.profile's verdicts, which must be
 * the default profile of feasibility.json; every bundle verdict is for one of feasibility.json's
 * profiles (the cards pick the default profile's verdict by id). Only checked once the fields
 * involved are strings.
 */
export function validateReferences(raw: Partial<Record<DataFile, unknown>>): string[] {
  const { meta, feasibility, bundles } = raw;
  if (!isObject(meta) || !isObject(feasibility)) return [];
  const problems: string[] = [];
  const profile = meta.profile;
  const defaultProfile = feasibility.default_profile;
  if (typeof profile === "string" && typeof defaultProfile === "string") {
    if (profile !== defaultProfile) {
      problems.push(
        `meta.json.profile: "${profile}" no coincide con feasibility.json.default_profile "${defaultProfile}"`,
      );
    }
  }
  if (Array.isArray(feasibility.profiles) && Array.isArray(bundles)) {
    const ids = new Set(feasibility.profiles.map((p) => (isObject(p) ? p.id : undefined)));
    bundles.forEach((b, i) => {
      if (!isObject(b) || !Array.isArray(b.verdicts)) return;
      b.verdicts.forEach((v, j) => {
        const id = isObject(v) ? v.profile : undefined;
        if (typeof id === "string" && !ids.has(id) && problems.length < MAX_PROBLEMS) {
          problems.push(
            `bundles.json[${i}].verdicts[${j}].profile: "${id}" no está en feasibility.json profiles`,
          );
        }
      });
    });
  }
  return problems;
}

/** Contract violations of one file's content, as "offers.json[3].source_url: ..." lines. */
export function validateFile(name: DataFile, value: unknown): string[] {
  const problems: string[] = [];
  SITE_DATA_SCHEMA[name](value, `${name}.json`, problems);
  return problems.slice(0, MAX_PROBLEMS);
}

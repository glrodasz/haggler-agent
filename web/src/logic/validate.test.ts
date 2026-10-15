import { readFileSync } from "node:fs";
import { join } from "node:path";
import { describe, expect, it } from "vitest";
import { assertSiteData } from "./site";
import { DATA_FILES } from "./types";
import { validateFile } from "./validate";

const sampleDir = join(import.meta.dirname, "..", "data-sample");

/** A fresh, mutable copy of the sample data (plain JSON objects). */
function sample(): Record<string, any> {
  const raw: Record<string, any> = {};
  for (const name of DATA_FILES) {
    raw[name] = JSON.parse(readFileSync(join(sampleDir, `${name}.json`), "utf8"));
  }
  return raw;
}

function rename(row: Record<string, unknown>, from: string, to: string): void {
  row[to] = row[from];
  delete row[from];
}

describe("site data contract (runtime)", () => {
  it("accepts the sample data and the empty-run contract", () => {
    const raw = sample();
    for (const name of DATA_FILES) expect(validateFile(name, raw[name]), name).toEqual([]);
    const empty = {
      ...raw,
      meta: { ...raw.meta, run_id: null, as_of: null, research_run_id: null, research_as_of: null },
      fx: { as_of: null, base: "EUR", rates: {}, stale: [] },
      offers: [],
      cheapest: [],
      coverage: [],
    };
    expect(() => assertSiteData(empty)).not.toThrow();
  });

  it("covers the nullable fields the pages handle", () => {
    const raw = sample();
    const offer = raw.offers[0];
    offer.monthly_eur = null;
    offer.vat_included = null;
    raw.cheapest[0].home = null;
    raw.cheapest[0].best_by_risk["0"] = null;
    raw.cheapest[0].savings_month_eur["0"] = null;
    const bundle = raw.bundles[0];
    for (const key of [
      "observed_service_id",
      "monthly_eur",
      "full_monthly_eur",
      "monthly_eur_with_fee",
      "first_year_monthly_eur",
      "standalone_sum_eur",
      "saving_pct",
      "verified_at",
      "checked_at",
      "valid_from",
      "valid_until",
      "last_verified",
    ]) {
      bundle[key] = null;
    }
    for (const key of [
      "amount",
      "observed_on",
      "base_plan_name",
      "base_plan_amount",
      "marginal_amount",
      "intro_amount",
      "intro_months",
      "intro_valid_until",
      "contract_months",
    ]) {
      bundle.price[key] = null;
    }
    Object.assign(bundle.requirements, {
      local_id: null,
      local_line: null,
      local_payment: null,
      credit_check: null,
      new_customers_only: null,
    });
    Object.assign(bundle.components[0], {
      plan_id: null,
      value: null,
      ads: null,
      choice_group_id: null,
      choose_n: null,
      standalone_eur: null,
    });
    bundle.optional_savings[0].value = null;
    bundle.optional_savings[0].saving_month_eur = null;
    bundle.verdicts[0].components[0].provenance = null;
    raw.changes[0].old = null;
    raw.changes[0].new = { amount: 9.5, currency: "EUR" };
    raw.changes[1].old = "129 SEK/month";
    raw.coverage[0].failing_since = "2026-10-01";
    raw.meta.research_run_id = null;
    raw.meta.research_as_of = null;
    const record = raw.feasibility.records[0];
    record.researched_on = null;
    record.tos_clause_url = null;
    record.evidence = [
      {
        url: "https://example.org/t",
        kind: "official",
        quote: "",
        retrieved_at: null,
        verified: false,
      },
    ];
    expect(() => assertSiteData(raw)).not.toThrow();
  });

  it('accepts "unknown" axis values of research-derived records (rank 99)', () => {
    const raw = sample();
    const record = raw.feasibility.records.find((r: any) => r.provenance !== "curated");
    Object.assign(record, {
      legality: "unknown",
      tos: "unknown",
      practical: "unknown",
      label: "unknown",
      rank: 99,
    });
    expect(validateFile("feasibility", raw.feasibility)).toEqual([]);
  });

  it("checks provenance and the profile references in feasibility.json", () => {
    const cases: [(raw: Record<string, any>) => void, RegExp][] = [
      [
        (r) => (r.feasibility.records[0].provenance = "agent"),
        /records\[0\]\.provenance: se esperaba curated \| template \| agent_reviewed \| agent_pending, hay string/,
      ],
      [
        (r) => delete r.feasibility.records[1].provenance,
        /feasibility\.json\.records\[1\]: falta el campo "provenance"/,
      ],
      [
        (r) => (r.feasibility.records[2].profile = "nadie"),
        /feasibility\.json\.records\[2\]\.profile: "nadie" no está en profiles/,
      ],
      [
        (r) => (r.feasibility.default_profile = "nadie"),
        /feasibility\.json\.default_profile: "nadie" no está en profiles/,
      ],
      [
        (r) => (r.feasibility.profiles = r.feasibility.profiles.slice(1)),
        /default_profile: "se_resident_co_citizen" no está en profiles/,
      ],
      [
        (r) => delete r.feasibility.profiles[1].residence_country,
        /feasibility\.json\.profiles\[1\]: falta el campo "residence_country"/,
      ],
      [
        (r) => (r.feasibility.records[3].steps[0].requires_presence = "no"),
        /records\[3\]\.steps\[0\]\.requires_presence: se esperaba boolean/,
      ],
      [
        (r) => (r.feasibility.records[3].assumptions_es = "ninguno"),
        /records\[3\]\.assumptions_es: se esperaba una lista/,
      ],
      [
        (r) => (r.meta.profile = "se_resident_eu_generic"),
        /meta\.json\.profile: "se_resident_eu_generic" no coincide con feasibility\.json/,
      ],
      [(r) => delete r.meta.research_as_of, /meta\.json: falta el campo "research_as_of"/],
    ];
    for (const [mutate, expected] of cases) {
      const raw = sample();
      mutate(raw);
      expect(() => assertSiteData(raw), `${expected}`).toThrow(expected);
    }
  });

  it("rejects malformed evidence items", () => {
    const item = {
      url: "https://x.example",
      kind: "official",
      quote: "q",
      retrieved_at: "2026-10-06",
      verified: true,
    };
    const withoutDate: Record<string, unknown> = { ...item };
    delete withoutDate.retrieved_at;
    const bad: [Record<string, unknown>, RegExp][] = [
      [withoutDate, /evidence\[0\]: falta el campo "retrieved_at"/],
      [{ ...item, quote: null }, /evidence\[0\]\.quote: se esperaba string, hay null/],
      [{ ...item, verified: "yes" }, /evidence\[0\]\.verified: se esperaba boolean/],
      [{ ...item, url: null, kind: "forum" }, /evidence\[0\]\.url: se esperaba string, hay null/],
      [{ ...item, retrieved_at: 20261006 }, /evidence\[0\]\.retrieved_at: se esperaba string/],
    ];
    for (const [evidence, expected] of bad) {
      const raw = sample();
      raw.feasibility.records[3].evidence = [evidence];
      const problems = validateFile("feasibility", raw.feasibility);
      expect(problems.join("\n"), `${expected}`).toMatch(expected);
      expect(problems.join("\n")).toMatch(/^feasibility\.json\.records\[3\]\.evidence\[0\]/);
    }
    const raw = sample();
    raw.feasibility.records[3].evidence = {};
    expect(validateFile("feasibility", raw.feasibility)).toEqual([
      "feasibility.json.records[3].evidence: se esperaba una lista, hay un objeto",
    ]);
  });

  it("fails on renamed offer fields (the page would render '/undefined')", () => {
    const raw = sample();
    for (const o of raw.offers) {
      rename(o, "source_url", "url");
      rename(o, "billing_period", "period");
      rename(o, "vat_included", "vat");
    }
    expect(() => assertSiteData(raw)).toThrow(/offers\.json\[0\]: falta el campo "billing_period"/);
    const problems = validateFile("offers", raw.offers);
    expect(problems).toContain('offers.json[0]: falta el campo "source_url"');
    expect(problems).toContain('offers.json[0]: falta el campo "vat_included"');
    expect(problems.length).toBeLessThanOrEqual(20);
  });

  it("fails on type changes and unexpected nulls in every file", () => {
    const cases: [string, (raw: Record<string, any>) => void, RegExp][] = [
      ["meta", (r) => delete r.meta.residence, /meta\.json: falta el campo "residence"/],
      ["meta", (r) => (r.meta.fx_fee_pct = "2"), /meta\.json\.fx_fee_pct: se esperaba number/],
      [
        "services",
        (r) => (r.services[0].plans[0].rank = null),
        /services\.json\[0\]\.plans\[0\]\.rank/,
      ],
      ["countries", (r) => (r.countries[1].eu_eea = "yes"), /countries\.json\[1\]\.eu_eea/],
      ["fx", (r) => (r.fx.rates.SEK = "11.2"), /fx\.json\.rates\.SEK: se esperaba number/],
      ["offers", (r) => (r.offers[2].monthly_equiv = "9.99"), /offers\.json\[2\]\.monthly_equiv/],
      [
        "offers",
        (r) => (r.offers[0].label = null),
        /offers\.json\[0\]\.label: se esperaba string, hay null/,
      ],
      [
        "offers",
        (r) => (r.offers[0].flags = null),
        /offers\.json\[0\]\.flags: se esperaba una lista/,
      ],
      [
        "feasibility",
        (r) => delete r.feasibility.records[0].rank,
        /feasibility\.json\.records\[0\]/,
      ],
      [
        "cheapest",
        (r) => delete r.cheapest[0].best_by_risk["3"],
        /best_by_risk: falta el campo "3"/,
      ],
      [
        "cheapest",
        (r) => (r.cheapest[0].home = { obs_key: "x" }),
        /cheapest\.json\[0\]\.home: falta/,
      ],
      ["bundles", (r) => (r.bundles[0].components = {}), /bundles\.json\[0\]\.components/],
      ["changes", (r) => (r.changes[0].old = { value: 1 }), /changes\.json\[0\]\.old/],
      ["coverage", (r) => rename(r.coverage[0], "adapter_id", "adapter"), /coverage\.json\[0\]/],
      ["offers", (r) => (r.offers = {}), /offers\.json: se esperaba una lista/],
    ];
    for (const [name, mutate, expected] of cases) {
      const raw = sample();
      mutate(raw);
      expect(() => assertSiteData(raw), `${name}: ${expected}`).toThrow(expected);
    }
  });

  it("accepts every value of the bundles.json v2 enums", () => {
    const values: [string[], (b: Record<string, any>, v: string) => void][] = [
      [
        ["verified", "needs_review", "manual", "unverified", "expired"],
        (b, v) => (b.display_status = v),
      ],
      [["full", "marginal"], (b, v) => (b.saving_basis = v)],
      [
        ["official_fetch", "user_report", "search_summary", "agent_research"],
        (b, v) => (b.verified_by = v),
      ],
      [["auto", "manual", "off"], (b, v) => (b.watch_mode = v)],
      [["low", "medium", "high"], (b, v) => (b.confidence = v)],
      [["curated", "observed"], (b, v) => (b.price.source = v)],
      [["month", "year"], (b, v) => (b.price.period = v)],
      [["required", "not_required", "unknown"], (b, v) => (b.requirements.residency = v)],
      [["required", "not_required", "unknown"], (b, v) => (b.requirements.billing_address = v)],
      [["checked", "not_checked", "unknown"], (b, v) => (b.requirements.signup_presence = v)],
      [
        ["included", "discount_pct", "discount_abs", "free_months", "choose_n"],
        (b, v) => (b.components[0].inclusion = v),
      ],
      [
        ["home", "curated", "composed", "agent_reviewed", "agent_pending"],
        (b, v) => (b.verdicts[0].provenance = v),
      ],
      [["unverified", "verified"], (b, v) => (b.verdicts[0].status = v)],
      [
        ["home", "curated", "template", "agent_reviewed", "agent_pending"],
        (b, v) => (b.verdicts[0].components[0].provenance = v),
      ],
      [["watcher", "agent"], (b, v) => (b.pending_changes[0].source = v)],
      [
        ["money", "int", "date", "service_ref", "service_pct", "bool", "url"],
        (b, v) => (b.pending_changes[0].value_type = v),
      ],
    ];
    for (const [allowed, set] of values) {
      for (const value of allowed) {
        const raw = sample();
        set(raw.bundles[0], value);
        expect(validateFile("bundles", raw.bundles), value).toEqual([]);
      }
    }
  });

  it("rejects a value outside each bundles.json v2 enum", () => {
    const cases: [(b: Record<string, any>) => void, RegExp][] = [
      [
        (b) => (b.display_status = "ok"),
        /\[0\]\.display_status: se esperaba verified \| needs_review/,
      ],
      [(b) => (b.display_status = null), /\[0\]\.display_status: se esperaba .*, hay null/],
      [(b) => (b.saving_basis = "partial"), /\[0\]\.saving_basis: se esperaba full \| marginal/],
      [(b) => (b.verified_by = "watcher"), /\[0\]\.verified_by: se esperaba official_fetch/],
      [
        (b) => (b.watch_mode = false),
        /\[0\]\.watch_mode: se esperaba auto \| manual \| off, hay boolean/,
      ],
      [(b) => (b.confidence = "unknown"), /\[0\]\.confidence: se esperaba low \| medium \| high/],
      [(b) => (b.price.source = "agent"), /\[0\]\.price\.source: se esperaba curated \| observed/],
      [(b) => (b.price.period = "quarter"), /\[0\]\.price\.period: se esperaba month \| year/],
      [(b) => (b.requirements.residency = null), /\.requirements\.residency: se esperaba required/],
      [
        (b) => (b.requirements.residency = "yes"),
        /\.requirements\.residency: se esperaba required/,
      ],
      [(b) => (b.requirements.billing_address = "no"), /\.requirements\.billing_address/],
      [(b) => (b.requirements.signup_presence = "required"), /\.requirements\.signup_presence/],
      [(b) => (b.components[0].inclusion = "bonus"), /\.components\[0\]\.inclusion/],
      [(b) => (b.optional_savings[0].inclusion = "gift"), /\.optional_savings\[0\]\.inclusion/],
      [
        (b) => (b.verdicts[0].provenance = "agent"),
        /\.verdicts\[0\]\.provenance: se esperaba home \| curated/,
      ],
      [
        (b) => (b.verdicts[0].status = "expired"),
        /\.verdicts\[0\]\.status: se esperaba unverified \| verified/,
      ],
      [(b) => (b.verdicts[0].confidence = "none"), /\.verdicts\[0\]\.confidence/],
      [
        (b) => (b.verdicts[0].components[0].provenance = "composed"),
        /\.verdicts\[0\]\.components\[0\]\.provenance: se esperaba home \| curated \| template \| agent_reviewed \| agent_pending/,
      ],
    ];
    for (const [mutate, expected] of cases) {
      const raw = sample();
      mutate(raw.bundles[0]);
      expect(validateFile("bundles", raw.bundles).join("\n"), `${expected}`).toMatch(expected);
      expect(() => assertSiteData(raw), `${expected}`).toThrow(expected);
    }
  });

  it("requires every bundles.json v2 key, nested ones included", () => {
    /** The objects of the first sample bundle, by their path under bundles.json[0]. */
    const nested = (b: Record<string, any>): Record<string, Record<string, unknown>> => ({
      "": b,
      ".price": b.price,
      ".requirements": b.requirements,
      ".components[0]": b.components[0],
      ".optional_savings[0]": b.optional_savings[0],
      ".evidence[0]": b.evidence[0],
      ".verdicts[0]": b.verdicts[0],
      ".verdicts[0].steps[0]": b.verdicts[0].steps[0],
      ".verdicts[0].components[0]": b.verdicts[0].components[0],
    });
    const counts: Record<string, number> = {};
    for (const [suffix, object] of Object.entries(nested(sample().bundles[0]))) {
      expect(object, suffix).toBeDefined();
      counts[suffix] = Object.keys(object).length;
      for (const key of Object.keys(object)) {
        const raw = sample();
        delete nested(raw.bundles[0])[suffix]![key];
        expect(validateFile("bundles", raw.bundles), `${suffix}.${key}`).toContain(
          `bundles.json[0]${suffix}: falta el campo "${key}"`,
        );
      }
    }
    // The v2 contract's key counts (the sample bundle carries every key).
    expect(counts).toEqual({
      "": 35,
      ".price": 13,
      ".requirements": 8,
      ".components[0]": 9,
      ".optional_savings[0]": 4,
      ".evidence[0]": 9,
      ".verdicts[0]": 15,
      ".verdicts[0].steps[0]": 4,
      ".verdicts[0].components[0]": 5,
    });
  });

  it("ignores extra keys; the v1 feasibility and status keys are gone", () => {
    // A newer site-data may add fields; the pages never read the v1 ones again.
    const raw = sample();
    raw.bundles[0].feasibility = null;
    raw.bundles[0].status = "unverified";
    expect(validateFile("bundles", raw.bundles)).toEqual([]);
    for (const b of sample().bundles) {
      expect("feasibility" in b, b.bundle_id).toBe(false);
      expect("status" in b, b.bundle_id).toBe(false);
    }
  });

  it("fails on bundle verdicts for a profile feasibility.json does not list", () => {
    const raw = sample();
    raw.bundles[0].verdicts[1].profile = "nadie";
    expect(() => assertSiteData(raw)).toThrow(
      /bundles\.json\[0\]\.verdicts\[1\]\.profile: "nadie" no está en feasibility\.json profiles/,
    );
  });

  it("fails on wrong bundle types (lists, numbers, booleans)", () => {
    const cases: [(b: Record<string, any>) => void, RegExp][] = [
      [(b) => (b.covers = "disney_plus"), /\[0\]\.covers: se esperaba una lista/],
      [(b) => (b.standalone_missing = null), /\[0\]\.standalone_missing: se esperaba una lista/],
      [(b) => (b.status_reasons_es = [1]), /\[0\]\.status_reasons_es\[0\]: se esperaba string/],
      [(b) => (b.pending_changes = {}), /\[0\]\.pending_changes: se esperaba una lista/],
      [
        (b) => (b.pending_changes[0].source = "human"),
        /\.pending_changes\[0\]\.source: se esperaba watcher \| agent/,
      ],
      [
        (b) => (b.pending_changes[0].value_type = "text"),
        /\.pending_changes\[0\]\.value_type: se esperaba money \| int/,
      ],
      [
        (b) => delete b.pending_changes[0].quote,
        /\.pending_changes\[0\]: falta el campo "quote"/,
      ],
      [
        (b) => (b.pending_changes[0].proposed = null),
        /\.pending_changes\[0\]\.proposed: se esperaba string, hay null/,
      ],
      [
        (b) => (b.pending_changes[0].big_change = "no"),
        /\.pending_changes\[0\]\.big_change: se esperaba boolean/,
      ],
      [
        (b) => (b.price.tax_included = null),
        /\.price\.tax_included: se esperaba boolean, hay null/,
      ],
      [(b) => (b.monthly_eur_with_fee = "5.54"), /\[0\]\.monthly_eur_with_fee: se esperaba number/],
      [
        (b) => (b.components[0].confirmed = null),
        /\.components\[0\]\.confirmed: se esperaba boolean/,
      ],
      [(b) => (b.evidence[0].verified = "no"), /\.evidence\[0\]\.verified: se esperaba boolean/],
      [(b) => (b.evidence[0].url = null), /\.evidence\[0\]\.url: se esperaba string, hay null/],
      [(b) => (b.verdicts[0].rank = "2"), /\.verdicts\[0\]\.rank: se esperaba number/],
      [
        (b) => (b.verdicts[0].components = null),
        /\.verdicts\[0\]\.components: se esperaba una lista/,
      ],
    ];
    for (const [mutate, expected] of cases) {
      const raw = sample();
      mutate(raw.bundles[0]);
      expect(validateFile("bundles", raw.bundles).join("\n"), `${expected}`).toMatch(expected);
    }
  });

  it("explains how to fix the drift", () => {
    const raw = sample();
    raw.meta.as_of = 20261006;
    expect(() => assertSiteData(raw)).toThrow(/types\.ts[\s\S]*meta\.json\.as_of/);
  });
});

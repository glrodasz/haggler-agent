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
      meta: { ...raw.meta, run_id: null, as_of: null },
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
    raw.bundles[0].feasibility = null;
    raw.bundles[0].price.amount = null;
    raw.changes[0].old = null;
    raw.changes[0].new = { amount: 9.5, currency: "EUR" };
    raw.changes[1].old = "129 SEK/month";
    raw.coverage[0].failing_since = "2026-10-01";
    expect(() => assertSiteData(raw)).not.toThrow();
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

  it("explains how to fix the drift", () => {
    const raw = sample();
    raw.meta.as_of = 20261006;
    expect(() => assertSiteData(raw)).toThrow(/types\.ts[\s\S]*meta\.json\.as_of/);
  });
});

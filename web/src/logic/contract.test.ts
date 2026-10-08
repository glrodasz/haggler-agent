/**
 * Parity with the Python publish step: the comparator (compare.ts, run in the browser over
 * offers.json) must reproduce cheapest.json (compare.py, shown on the service pages).
 *
 * Runs over src/data-sample always, and over src/data when it exists. CI's "contract" job
 * fills src/data with `haggler site-data` output generated from the replay fixtures, so this is
 * where a rounding, ordering or schema drift between the two implementations shows up.
 */

import { existsSync, readFileSync } from "node:fs";
import { join } from "node:path";
import { describe, expect, it } from "vitest";
import { type Cheapest, cheapestAll, type PricedOffer } from "./compare";
import { assertSiteData } from "./site";
import type { CheapestRow, OfferRef, SiteData } from "./types";
import { DATA_FILES, RISK_LEVELS } from "./types";

const srcDir = join(import.meta.dirname, "..");

function load(dir: string): SiteData {
  const raw: Record<string, unknown> = {};
  for (const name of DATA_FILES) {
    raw[name] = JSON.parse(readFileSync(join(dir, `${name}.json`), "utf8"));
  }
  return assertSiteData(raw);
}

/** The fields both sides publish, money in cents. */
function ref(o: PricedOffer | OfferRef | null) {
  if (!o) return null;
  return {
    obs_key: o.obs_key,
    cost_eur: o.cost_eur,
    cost_eur_with_fee: o.cost_eur_with_fee,
    label: o.label,
    rank: o.rank,
  };
}

function view(row: Cheapest | CheapestRow) {
  const isTs = "bestByRisk" in row;
  return {
    key: `${row.service_id}:${row.plan_id}`,
    home: ref(row.home),
    best: RISK_LEVELS.map((k) => ref(isTs ? row.bestByRisk[k] : row.best_by_risk[`${k}`])),
    savings: RISK_LEVELS.map((k) =>
      isTs ? row.savingsMonthEur[k] : row.savings_month_eur[`${k}`],
    ),
  };
}

const datasets: [string, string][] = [
  ["data-sample", join(srcDir, "data-sample")],
  ["data (generated)", join(srcDir, "data")],
];

describe.each(datasets)("compare.ts reproduces cheapest.json (%s)", (_name, dir) => {
  const present = DATA_FILES.every((name) => existsSync(join(dir, `${name}.json`)));

  it.skipIf(!present)("same rows, order, choices, costs and savings", () => {
    const data = load(dir);
    if (data.fx.as_of === null) {
      // No FX table: site-data publishes no cheapest rows at all.
      expect(data.cheapest).toEqual([]);
      return;
    }
    const residenceCurrency =
      data.countries.find((c) => c.cc === data.meta.residence)?.currency ?? "EUR";
    const actual = cheapestAll(
      data.offers,
      {
        rates: data.fx.rates,
        residence: data.meta.residence,
        residenceCurrency,
        feePct: data.meta.fx_fee_pct,
      },
      data.services,
    );
    expect(actual.map(view)).toEqual(data.cheapest.map(view));
  });
});

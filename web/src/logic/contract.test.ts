/**
 * Parity with the Python publish step: the comparator (compare.ts, run in the browser over
 * offers.json) must reproduce cheapest.json (compare.py, shown on the service pages), and
 * bundles.json v2 must follow the rules the cards rely on (costs, status, verdicts).
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
import type { Bundle, CheapestRow, OfferRef, SiteData } from "./types";
import { DATA_FILES, RISK_LEVELS, UNKNOWN_RANK } from "./types";

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

/** Money is rounded to cents on both sides; a figure derived from other rounded ones may differ
 * by a cent depending on whether it was computed before or after rounding. */
const CENT = 0.01 + 1e-9;

const round2 = (x: number) => Math.round(x * 100) / 100;

/** Components whose standalone price enters the sum: included ones and choose_n choices. */
const summed = (b: Bundle) =>
  b.components.filter((c) => c.inclusion === "included" || c.inclusion === "choose_n");

describe.each(datasets)("bundles.json v2 rules (%s)", (_name, dir) => {
  const present = DATA_FILES.every((name) => existsSync(join(dir, `${name}.json`)));
  let cached: SiteData | undefined;
  const site = () => (cached ??= load(dir));

  it.skipIf(!present)("unique ids, observed prices and covers", () => {
    const { bundles } = site();
    const ids = bundles.map((b) => b.bundle_id);
    expect(new Set(ids).size).toBe(ids.length);
    for (const b of bundles) {
      expect(b.observed_service_id !== null, b.bundle_id).toBe(b.price.source === "observed");
      if (b.price.source === "curated") expect(b.price.observed_on, b.bundle_id).toBeNull();
      expect(new Set(b.covers).size, b.bundle_id).toBe(b.covers.length);
    }
  });

  it.skipIf(!present)("monthly costs: marginal basis, FX fee, never the intro price", () => {
    const { bundles, countries, meta } = site();
    const residenceCurrency = countries.find((c) => c.cc === meta.residence)?.currency ?? "EUR";
    const fee = 1 + meta.fx_fee_pct / 100;
    for (const b of bundles) {
      const id = b.bundle_id;
      if (b.saving_basis === "marginal") {
        expect(b.price.marginal_amount, id).not.toBeNull();
        if (b.monthly_eur !== null && b.full_monthly_eur !== null) {
          expect(b.monthly_eur, id).toBeLessThanOrEqual(b.full_monthly_eur);
        }
      } else {
        expect(b.monthly_eur, id).toBe(b.full_monthly_eur);
      }
      if (b.monthly_eur === null) {
        expect(b.monthly_eur_with_fee, id).toBeNull();
        expect(b.saving_pct, id).toBeNull();
      } else if (b.price.currency === residenceCurrency) {
        expect(b.monthly_eur_with_fee, id).toBe(b.monthly_eur);
      } else {
        expect(Math.abs(b.monthly_eur_with_fee! - b.monthly_eur * fee), id).toBeLessThanOrEqual(
          CENT,
        );
      }
      if (b.first_year_monthly_eur !== null) {
        expect(b.display_status, id).toBe("verified");
        expect(b.price.intro_amount, id).not.toBeNull();
        expect(b.price.intro_months, id).not.toBeNull();
      }
    }
  });

  it.skipIf(!present)("saving: from the rounded figures, partial sums flagged", () => {
    for (const b of site().bundles) {
      const id = b.bundle_id;
      const parts = summed(b);
      const ids = new Set(parts.map((c) => c.service_id));
      for (const m of b.standalone_missing) expect(ids.has(m), `${id}: ${m}`).toBe(true);
      const plain = parts.every((c) => c.inclusion === "included");
      if (plain) {
        // Every included component without a standalone price is listed as missing.
        const missing = parts.filter((c) => c.standalone_eur === null).map((c) => c.service_id);
        expect([...b.standalone_missing].sort(), id).toEqual([...new Set(missing)].sort());
        const priced = parts.filter((c) => c.standalone_eur !== null);
        if (priced.length === 0) {
          expect(b.standalone_sum_eur, id).toBeNull();
        } else {
          const sum = priced.reduce((acc, c) => acc + c.standalone_eur!, 0);
          expect(Math.abs(b.standalone_sum_eur! - sum), id).toBeLessThanOrEqual(
            0.005 * priced.length + 1e-9,
          );
        }
      }
      if (b.standalone_sum_eur === null || b.monthly_eur === null || b.standalone_sum_eur === 0) {
        expect(b.saving_pct, id).toBeNull();
      } else {
        const expected = round2(
          ((b.standalone_sum_eur - b.monthly_eur) / b.standalone_sum_eur) * 100,
        );
        expect(Math.abs(b.saving_pct! - expected), id).toBeLessThanOrEqual(CENT);
      }
    }
  });

  it.skipIf(!present)("optional savings: discounts and free months, never in the saving", () => {
    for (const b of site().bundles) {
      for (const o of b.optional_savings) {
        const where = `${b.bundle_id}: ${o.service_id}`;
        expect(["discount_pct", "discount_abs", "free_months"], where).toContain(o.inclusion);
        expect(
          b.components.some((c) => c.service_id === o.service_id && c.inclusion === o.inclusion),
          where,
        ).toBe(true);
        if (o.inclusion === "free_months") expect(o.saving_month_eur, where).toBeNull();
        const c = b.components.find(
          (x) => x.service_id === o.service_id && x.inclusion === o.inclusion,
        )!;
        if (o.inclusion === "discount_pct" && c.standalone_eur !== null && o.value !== null) {
          expect(
            Math.abs(o.saving_month_eur! - (c.standalone_eur * o.value) / 100),
            where,
          ).toBeLessThanOrEqual(CENT);
        }
      }
    }
  });

  it.skipIf(!present)("status: reasons unless verified; expired bundles get no verdict", () => {
    for (const b of site().bundles) {
      if (b.display_status !== "verified") {
        expect(b.status_reasons_es.length, b.bundle_id).toBeGreaterThan(0);
      }
      if (b.display_status === "expired") expect(b.verdicts, b.bundle_id).toEqual([]);
    }
  });

  it.skipIf(!present)("pending changes: each differs from the YAML and puts the bundle in review", () => {
    for (const b of site().bundles) {
      const seen = new Set<string>();
      for (const c of b.pending_changes) {
        const where = `${b.bundle_id}: ${c.id}`;
        // A proposal the YAML already holds resolves itself and is never published.
        expect(c.proposed, where).not.toBe(c.current);
        expect(c.quote.length, where).toBeGreaterThan(0);
        expect(c.url.startsWith("https://"), where).toBe(true);
        // One entry per change, whichever source found it first.
        const key = `${c.field}=${c.proposed}`;
        expect(seen.has(key), where).toBe(false);
        seen.add(key);
      }
      if (b.pending_changes.length > 0) {
        expect(["needs_review", "expired"], b.bundle_id).toContain(b.display_status);
      }
    }
  });

  it.skipIf(!present)("verdicts: one per profile, default first, ranks match their labels", () => {
    const { bundles, feasibility } = site();
    const order = feasibility.profiles.map((p) => p.id);
    const labelRank = new Map(feasibility.labels.map((l) => [l.id, l.rank]));
    for (const b of bundles) {
      if (b.display_status === "expired") continue;
      const id = b.bundle_id;
      const reviewed = b.verdicts.filter((v) => v.provenance !== "agent_pending");
      const profiles = reviewed.map((v) => v.profile);
      // Every public profile, the default one first, then by id; at most one of each.
      expect(profiles, id).toEqual([
        feasibility.default_profile,
        ...order.filter((p) => p !== feasibility.default_profile).sort(),
      ]);
      b.verdicts.forEach((v, i) => {
        const where = `${id}: ${v.profile}/${v.provenance}`;
        if (v.provenance === "agent_pending") {
          // A pending variant follows its own reviewed verdict.
          expect(b.verdicts[i - 1]?.profile, where).toBe(v.profile);
          expect(b.verdicts[i - 1]?.provenance, where).not.toBe("agent_pending");
        }
        expect(v.label === "unknown", where).toBe(v.rank === UNKNOWN_RANK);
        if (labelRank.has(v.label)) expect(v.rank, where).toBe(labelRank.get(v.label));
        // Never below the worst component whose verdict is known.
        const known = v.components.map((c) => c.rank).filter((r) => r !== UNKNOWN_RANK);
        if (v.rank !== UNKNOWN_RANK && known.length > 0) {
          expect(v.rank, where).toBeGreaterThanOrEqual(Math.max(...known));
        }
        for (const c of v.components) {
          expect(c.target_country, where).toBe(b.country);
          expect(c.label === "unknown", `${where} ${c.service_id}`).toBe(c.rank === UNKNOWN_RANK);
        }
        const home = feasibility.profiles.find((p) => p.id === v.profile)?.residence_country;
        if (v.provenance === "home") expect(home, where).toBe(b.country);
      });
    }
  });
});

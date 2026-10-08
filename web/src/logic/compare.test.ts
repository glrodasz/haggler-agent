import { describe, expect, it } from "vitest";
import cheapestSample from "../data-sample/cheapest.json";
import countriesSample from "../data-sample/countries.json";
import fxSample from "../data-sample/fx.json";
import metaSample from "../data-sample/meta.json";
import offersSample from "../data-sample/offers.json";
import {
  type CompareContext,
  type CompareOfferFields,
  cheapestAll,
  cheapestFor,
  compareOffers,
  displayCost,
  isCandidate,
  priceOffer,
  roundTo,
  savingsByRisk,
  summarize,
  toOfferRef,
} from "./compare";
import type { CheapestRow, Offer, OfferRef, RiskKey } from "./types";
import { RISK_LEVELS, UNKNOWN_RANK } from "./types";

const ctx: CompareContext = {
  rates: { SEK: 11, COP: 4500, USD: 1.2, TRY: 50 },
  residence: "SE",
  residenceCurrency: "SEK",
  feePct: 2,
};

function offer(
  partial: Partial<CompareOfferFields> & Pick<CompareOfferFields, "country">,
): CompareOfferFields {
  const base: CompareOfferFields = {
    obs_key: "",
    service_id: "netflix",
    plan_id: "standard",
    country: partial.country,
    channel: "web",
    currency: "SEK",
    amount: 0,
    billing_period: "month",
    monthly_equiv: 0,
    source_kind: "official",
    status: "accepted",
    label: "official_home",
    rank: 0,
    eu_badge: false,
  };
  const o = { ...base, ...partial };
  if (o.amount === 0) o.amount = o.monthly_equiv;
  o.obs_key ||= `${o.service_id}:${o.plan_id}:${o.country}:${o.channel}:${o.billing_period}`;
  return o;
}

const se = offer({ country: "SE", currency: "SEK", monthly_equiv: 169 });
const co = offer({
  country: "CO",
  currency: "COP",
  monthly_equiv: 33900,
  label: "tos_breach_low_enforcement",
  rank: 2,
});
const tr = offer({
  country: "TR",
  currency: "TRY",
  monthly_equiv: 349.99,
  label: "unknown",
  rank: UNKNOWN_RANK,
});
const us = offer({
  country: "US",
  currency: "USD",
  monthly_equiv: 17.99,
  label: "tos_breach_enforced",
  rank: 3,
});

describe("roundTo", () => {
  it("rounds half away from zero without binary artefacts", () => {
    expect(roundTo(1.005, 2)).toBe(1.01);
    expect(roundTo(2.675, 2)).toBe(2.68);
    expect(roundTo(-1.005, 2)).toBe(-1.01);
    expect(roundTo(15.36363636, 4)).toBe(15.3636);
  });
});

describe("priceOffer", () => {
  it("converts to EUR and adds the FX fee only for foreign currencies", () => {
    const home = priceOffer(se, ctx);
    expect(home?.cost_eur).toBe(15.3636);
    expect(home?.cost_eur_with_fee).toBe(15.3636);
    expect(home?.fee_applied).toBe(false);

    const foreign = priceOffer(co, ctx);
    expect(foreign?.cost_eur).toBe(7.5333);
    expect(foreign?.cost_eur_with_fee).toBe(roundTo(7.5333 * 1.02, 4));
    expect(foreign?.fee_applied).toBe(true);
  });

  it("charges the fee on EUR offers too when the residence is not in the euro area", () => {
    const lt = priceOffer(offer({ country: "LT", currency: "EUR", monthly_equiv: 12.99 }), ctx);
    expect(lt?.cost_eur).toBe(12.99);
    expect(lt?.cost_eur_with_fee).toBe(roundTo(12.99 * 1.02, 4));
  });

  it("returns null when the currency has no rate", () => {
    expect(
      priceOffer(offer({ country: "BR", currency: "BRL", monthly_equiv: 44.9 }), ctx),
    ).toBeNull();
  });
});

describe("isCandidate", () => {
  it("accepts accepted and carried-forward rows only", () => {
    expect(isCandidate({ status: "accepted" })).toBe(true);
    expect(isCandidate({ status: "carried_forward" })).toBe(true);
    expect(isCandidate({ status: "quarantined" })).toBe(false);
    expect(isCandidate({ status: "rejected" })).toBe(false);
  });
});

describe("cheapestFor", () => {
  const rows = [se, co, tr, us];

  it("picks the cheapest offer with rank <= k for every threshold", () => {
    const r = cheapestFor("netflix", "standard", rows, ctx);
    expect(r.home?.country).toBe("SE");
    expect(r.bestByRisk[0]?.country).toBe("SE");
    expect(r.bestByRisk[1]?.country).toBe("SE");
    expect(r.bestByRisk[2]?.country).toBe("CO");
    expect(r.bestByRisk[3]?.country).toBe("CO");
    expect(r.savingsMonthEur[0]).toBe(0);
    expect(r.savingsMonthEur[2]).toBe(roundTo(15.3636 - roundTo(7.5333 * 1.02, 4), 4));
  });

  it("never chooses unknown offers, even when they are the cheapest", () => {
    const cheapTr = offer({ ...tr, monthly_equiv: 10, amount: 10 });
    const r = cheapestFor("netflix", "standard", [se, cheapTr], ctx);
    for (const k of RISK_LEVELS) expect(r.bestByRisk[k]?.country).toBe("SE");
    // ...but it is still listed among the offers.
    expect(r.offers[0]?.country).toBe("TR");
  });

  it("never chooses not-feasible offers (rank 4)", () => {
    const nf = offer({
      country: "US",
      currency: "USD",
      monthly_equiv: 1,
      label: "not_feasible",
      rank: 4,
    });
    const r = cheapestFor("netflix", "standard", [se, nf], ctx);
    expect(r.bestByRisk[3]?.country).toBe("SE");
  });

  it("ignores quarantined and rejected offers", () => {
    const q = offer({ ...co, status: "quarantined", monthly_equiv: 100, amount: 100 });
    const r = cheapestFor("netflix", "standard", [se, q], ctx);
    expect(r.bestByRisk[3]?.country).toBe("SE");
    expect(r.offers).toHaveLength(1);
  });

  it("prefers the residence web offer as home, else App Store", () => {
    const iap = offer({ country: "SE", channel: "ios_iap", monthly_equiv: 150 });
    expect(cheapestFor("netflix", "standard", [se, iap], ctx).home?.channel).toBe("web");
    expect(cheapestFor("netflix", "standard", [iap, co], ctx).home?.channel).toBe("ios_iap");
  });

  it("has no home and no savings without a residence offer", () => {
    const r = cheapestFor("netflix", "standard", [co], ctx);
    expect(r.home).toBeNull();
    expect(r.bestByRisk[2]?.country).toBe("CO");
    expect(r.savingsMonthEur[2]).toBeNull();
    expect(r.bestByRisk[0]).toBeNull();
  });

  it("uses monthly_equiv so yearly plans compete on their monthly cost", () => {
    const yearly = offer({
      country: "SE",
      plan_id: "family",
      billing_period: "year",
      amount: 1299,
      monthly_equiv: 108.25,
    });
    const r = cheapestFor("netflix", "family", [yearly], ctx);
    expect(r.home?.cost_eur).toBe(roundTo(108.25 / 11, 4));
  });

  it("breaks ties: web, then official, then lower country code", () => {
    const a = priceOffer(
      offer({ country: "MX", currency: "EUR", monthly_equiv: 5, rank: 2 }),
      ctx,
    )!;
    const b = priceOffer(
      offer({ country: "BR", currency: "EUR", monthly_equiv: 5, rank: 2 }),
      ctx,
    )!;
    const iap = priceOffer(
      offer({ country: "AR", currency: "EUR", monthly_equiv: 5, rank: 2, channel: "ios_iap" }),
      ctx,
    )!;
    const sec = priceOffer(
      offer({
        country: "AA",
        currency: "EUR",
        monthly_equiv: 5,
        rank: 2,
        source_kind: "secondary",
      }),
      ctx,
    )!;
    expect([a, iap, sec, b].sort(compareOffers).map((o) => o.country)).toEqual([
      "BR",
      "MX",
      "AA",
      "AR",
    ]);
  });
});

describe("summarize", () => {
  const display = { currency: "SEK", rates: ctx.rates, feePct: 2 };
  const netflix = cheapestFor("netflix", "standard", [se, co], ctx);
  const onlyAbroad = cheapestFor(
    "spotify",
    "individual",
    [offer({ ...co, service_id: "spotify", plan_id: "individual" })],
    ctx,
  );
  const nothing = cheapestFor("vix", "premium", [], ctx);

  it("shows residence-currency prices exactly and sums only comparable rows", () => {
    const { rows, totals } = summarize([netflix, onlyAbroad, nothing], 2, display);
    expect(rows[0]?.homeCost).toBe(169);
    expect(totals.comparable).toBe(1);
    expect(totals.home).toBe(169);
    expect(totals.optimized).toBeCloseTo(roundTo(7.5333 * 1.02, 4) * 11, 6);
    expect(totals.saving).toBeCloseTo(169 - roundTo(7.5333 * 1.02, 4) * 11, 6);
    expect(totals.fee).toBeCloseTo((roundTo(7.5333 * 1.02, 4) - 7.5333) * 11, 6);
    expect(totals.withoutHome).toEqual(["spotify"]);
    expect(totals.withoutOffers).toEqual(["vix"]);
  });

  it("is zero saving at the most conservative level", () => {
    expect(summarize([netflix], 0, display).totals.saving).toBe(0);
  });

  it("reports the saving for every threshold", () => {
    const byRisk = savingsByRisk([netflix], display);
    expect(byRisk[0]).toBe(0);
    expect(byRisk[1]).toBe(0);
    expect(byRisk[2]).toBeGreaterThan(0);
    expect(byRisk[3]).toBe(byRisk[2]);
  });

  it("applies the fee when the display currency equals a foreign offer currency", () => {
    const p = priceOffer(co, ctx)!;
    expect(displayCost(p, "COP", ctx.rates, 2)).toBeCloseTo(33900 * 1.02, 6);
    expect(displayCost(priceOffer(se, ctx)!, "EUR", ctx.rates, 2)).toBeCloseTo(15.3636, 6);
  });
});

describe("sample data", () => {
  // Golden test: the TypeScript comparison must reproduce the published cheapest.json.
  const residenceCurrency =
    countriesSample.find((c) => c.cc === metaSample.residence)?.currency ?? "EUR";
  const sampleCtx: CompareContext = {
    rates: fxSample.rates as Record<string, number>,
    residence: metaSample.residence,
    residenceCurrency,
    feePct: metaSample.fx_fee_pct,
  };
  const offers = offersSample as Offer[];
  const expected = cheapestSample as unknown as CheapestRow[];
  const actual = cheapestAll(offers, sampleCtx);

  it("covers the same (service, plan) pairs", () => {
    expect(actual.map((r) => `${r.service_id}:${r.plan_id}`)).toEqual(
      expected.map((r) => `${r.service_id}:${r.plan_id}`),
    );
  });

  it("chooses the same home and best offers with the same costs", () => {
    for (const [i, row] of actual.entries()) {
      const exp = expected[i]!;
      const strip = (o: OfferRef | null): Omit<OfferRef, "source_url"> | null => {
        if (!o) return null;
        const { source_url: _ignored, ...rest } = o;
        return rest;
      };
      const near = (
        a: Omit<OfferRef, "source_url"> | null,
        b: Omit<OfferRef, "source_url"> | null,
      ) => {
        if (a === null || b === null) return expect(a).toBe(b);
        expect(a.obs_key).toBe(b.obs_key);
        expect(a.cost_eur_with_fee).toBeCloseTo(b.cost_eur_with_fee, 2);
      };
      near(row.home ? toOfferRef(row.home) : null, strip(exp.home));
      for (const k of RISK_LEVELS) {
        const key = `${k}` as RiskKey;
        const best = row.bestByRisk[k];
        near(best ? toOfferRef(best) : null, strip(exp.best_by_risk[key]));
        const saving = row.savingsMonthEur[k];
        const expSaving = exp.savings_month_eur[key];
        if (saving === null || expSaving === null) expect(saving).toBe(expSaving);
        else expect(saving).toBeCloseTo(expSaving, 1);
      }
    }
  });
});

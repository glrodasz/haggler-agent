import { describe, expect, it } from "vitest";
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
  savingsPctByRisk,
  snapRisk,
  summarize,
  toOfferRef,
  visibleRiskLevels,
} from "./compare";
import type { PlanInfo } from "./types";
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
    expect(home?.cost_eur).toBe(15.36);
    expect(home?.cost_eur_with_fee).toBe(15.36);
    expect(home?.fee_applied).toBe(false);

    const foreign = priceOffer(co, ctx);
    expect(foreign?.cost_eur).toBe(7.53);
    expect(foreign?.cost_eur_with_fee).toBe(7.68); // 7.5333 * 1.02 = 7.684
    expect(foreign?.fee_applied).toBe(true);
  });

  it("charges the fee on EUR offers too when the residence is not in the euro area", () => {
    const lt = priceOffer(offer({ country: "LT", currency: "EUR", monthly_equiv: 12.99 }), ctx);
    expect(lt?.cost_eur).toBe(12.99);
    expect(lt?.cost_eur_with_fee).toBe(13.25); // 13.2498
  });

  it("rounds to cents half up, applying the fee to the unrounded EUR cost (like compare.py)", () => {
    const lt = priceOffer(offer({ country: "LT", currency: "EUR", monthly_equiv: 0.995 }), ctx);
    expect(lt?.cost_eur).toBe(1);
    // 0.995 * 1.02 = 1.0149, not 1.00 * 1.02 = 1.02.
    expect(lt?.cost_eur_with_fee).toBe(1.01);
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
    // Saving = difference of the rounded costs, in cents.
    expect(r.savingsMonthEur[2]).toBe(7.68);
  });

  it("treats sub-cent differences as ties and applies the tie-breaks", () => {
    // 5.004 and 5.001 EUR both cost 5.10 with the fee: web wins although App Store is cheaper.
    const web = offer({ country: "LT", currency: "EUR", monthly_equiv: 5.004, rank: 2 });
    const iap = offer({
      country: "LT",
      currency: "EUR",
      channel: "ios_iap",
      monthly_equiv: 5.001,
      rank: 2,
    });
    const r = cheapestFor("netflix", "standard", [iap, web], ctx);
    expect(r.bestByRisk[2]?.channel).toBe("web");
    expect(r.offers.map((o) => o.cost_eur_with_fee)).toEqual([5.1, 5.1]);
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
    expect(r.home?.cost_eur).toBe(roundTo(108.25 / 11, 2));
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

  it("prefers monthly billing before the obs_key on a full tie", () => {
    const base = priceOffer(offer({ country: "MX", currency: "EUR", monthly_equiv: 5 }), ctx)!;
    const yearly = { ...base, billing_period: "year" as const, obs_key: "a" };
    const monthly = { ...base, billing_period: "month" as const, obs_key: "b" };
    expect([yearly, monthly].sort(compareOffers).map((o) => o.billing_period)).toEqual([
      "month",
      "year",
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
    expect(totals.optimized).toBeCloseTo(7.68 * 11, 6);
    expect(totals.saving).toBeCloseTo(169 - 7.68 * 11, 6);
    expect(totals.fee).toBeCloseTo((7.68 - 7.53) * 11, 6);
    expect(totals.withoutHome).toEqual(["spotify"]);
    expect(totals.withoutOffers).toEqual(["vix"]);
  });

  it("names the cheapest foreign offer when the home offer wins", () => {
    const cheapHome = cheapestFor(
      "netflix",
      "standard",
      [offer({ country: "SE", currency: "SEK", monthly_equiv: 50 }), co, us],
      ctx,
    );
    const [row] = summarize([cheapHome], 3, display).rows;
    expect(row?.best?.country).toBe("SE");
    expect(row?.runnerUp?.country).toBe("CO");
    expect(row?.runnerUpCost).toBeCloseTo(7.68 * 11, 6);
    expect(row?.runnerUpAboveRisk).toBe(false);
    expect(summarize([netflix], 2, display).rows[0]?.runnerUp).toBeNull();
    const [strict] = summarize([cheapHome], 0, display).rows;
    expect(strict?.runnerUp?.country).toBe("CO");
    expect(strict?.runnerUpAboveRisk).toBe(true);
    const onlyUnknown = cheapestFor("netflix", "standard", [se, tr], ctx);
    expect(summarize([onlyUnknown], 3, display).rows[0]?.runnerUp).toBeNull();
  });

  it("shows a cheaper unevaluated offer without choosing or summing it", () => {
    const onlyUnknown = cheapestFor("netflix", "standard", [se, tr], ctx);
    const { rows, totals } = summarize([onlyUnknown], 3, display);
    expect(rows[0]?.best?.country).toBe("SE");
    expect(rows[0]?.unevaluated?.country).toBe("TR");
    expect(totals.saving).toBe(0);
    expect(totals.unevaluatedSaving).toBeCloseTo(
      (rows[0]?.homeCost ?? 0) - (rows[0]?.unevaluatedCost ?? 0),
      6,
    );
    expect(totals.unevaluatedSaving).toBeGreaterThan(0);
    const cheapHome = cheapestFor(
      "netflix",
      "standard",
      [offer({ country: "SE", currency: "SEK", monthly_equiv: 1 }), tr],
      ctx,
    );
    expect(summarize([cheapHome], 3, display).rows[0]?.unevaluated).toBeNull();
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

  it("reports the saving as a share of the home cost", () => {
    const pct = savingsPctByRisk([netflix], display);
    const { home, saving } = summarize([netflix], 2, display).totals;
    expect(pct[0]).toBe(0);
    expect(pct[2]).toBeCloseTo((saving / home) * 100, 9);
    expect(pct[2]).toBeGreaterThan(0);
    expect(pct[2]).toBeLessThan(100);
    expect(savingsPctByRisk([], display)[3]).toBe(0);
  });

  it("applies the fee when the display currency equals a foreign offer currency", () => {
    const p = priceOffer(co, ctx)!;
    expect(displayCost(p, "COP", ctx.rates, 2)).toBeCloseTo(33900 * 1.02, 6);
    expect(displayCost(priceOffer(se, ctx)!, "EUR", ctx.rates, 2)).toBeCloseTo(15.36, 6);
  });
});

describe("cheapestAll", () => {
  const plan = (id: string, rank: number): PlanInfo => ({ id, label_es: id, rank, ads: false });
  const services = [
    { id: "netflix", plans: [plan("premium", 3), plan("basic", 1), plan("standard", 2)] },
  ];
  const rows = [
    offer({ country: "SE", plan_id: "premium", monthly_equiv: 229 }),
    offer({ country: "SE", plan_id: "basic", monthly_equiv: 99 }),
    offer({ country: "SE", plan_id: "standard", monthly_equiv: 169 }),
    offer({ country: "SE", plan_id: "retired", monthly_equiv: 1 }),
    offer({ country: "SE", plan_id: "ads", monthly_equiv: 1, status: "quarantined" }),
  ];

  it("orders rows by plan rank and drops plans outside the catalog, like compare.py", () => {
    expect(cheapestAll(rows, ctx, services).map((r) => r.plan_id)).toEqual([
      "basic",
      "standard",
      "premium",
    ]);
  });

  it("without the catalog sorts by plan id and keeps only plans with a candidate", () => {
    expect(cheapestAll(rows, ctx).map((r) => r.plan_id)).toEqual([
      "basic",
      "premium",
      "retired",
      "standard",
    ]);
  });

  it("publishes refs in cents", () => {
    const home = cheapestAll(rows, ctx, services)[1]!.home!;
    expect(toOfferRef(home)).toMatchObject({ cost_eur: 15.36, cost_eur_with_fee: 15.36 });
  });
});

describe("visibleRiskLevels", () => {
  it("hides level 1 when it saves nothing over level 0", () => {
    expect(visibleRiskLevels({ 0: 0, 1: 0, 2: 5, 3: 8 })).toEqual([0, 2, 3]);
  });

  it("shows level 1 when it has a saving of its own", () => {
    expect(visibleRiskLevels({ 0: 0, 1: 2, 2: 5, 3: 8 })).toEqual([0, 1, 2, 3]);
  });

  it("keeps levels 0, 2 and 3 even with no saving at all", () => {
    expect(visibleRiskLevels({ 0: 0, 1: 0, 2: 0, 3: 0 })).toEqual([0, 2, 3]);
  });
});

describe("snapRisk", () => {
  it("keeps a level that is shown", () => {
    expect(snapRisk(2, [0, 2, 3])).toBe(2);
    expect(snapRisk(1, [0, 1, 2, 3])).toBe(1);
  });

  it("falls back from a hidden level 1 to level 0, which gives the same result", () => {
    expect(snapRisk(1, [0, 2, 3])).toBe(0);
  });
});

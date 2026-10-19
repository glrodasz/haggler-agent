import { describe, expect, it } from "vitest";
import { type CompareOfferFields, cheapestFor, savingsPctByRisk } from "./compare";
import { toCapabilities } from "./profile";
import { defaultState } from "./selection";
import { savingsTeasers, TEASER_RISK, type TeaserInput } from "./teasers";
import type { FeasibilityRule, RuleVerdict } from "./types";
import { relabelOffers, type VerdictSource } from "./verdicts";

const LEVEL3: RuleVerdict = {
  label: "tos_breach_enforced",
  rank: 3,
  eu_badge: false,
  provenance: "curated",
};
const NO: RuleVerdict = { label: "not_feasible", rank: 4, eu_badge: false, provenance: "curated" };

function offer(service: string, country: string, eur: number): CompareOfferFields {
  return {
    obs_key: `${service}:p:${country}:web:month`,
    service_id: service,
    plan_id: "p",
    country,
    channel: "web",
    currency: "EUR",
    amount: eur,
    billing_period: "month",
    monthly_equiv: eur,
    source_kind: "official",
    status: "accepted",
    label: "unknown",
    rank: 99,
    eu_badge: false,
  };
}

/** Level 3 with a card of `cc`; `open` makes it level 3 for anyone. */
function rule(service: string, cc: string, open = false): FeasibilityRule {
  return {
    service_id: service,
    target_country: cc,
    reviewed: open
      ? { verdict: LEVEL3 }
      : { ask: ["card", cc], answers: { true: { verdict: LEVEL3 }, false: { verdict: NO } } },
    pending: null,
  };
}

const services = [
  { id: "a", name: "A", default_plan: "p", plans: [{ id: "p", label_es: "P" }], href: "" },
  { id: "b", name: "B", default_plan: "p", plans: [{ id: "p", label_es: "P" }], href: "" },
];

const source: VerdictSource = {
  default_profile: "x",
  profiles: [],
  rules: [rule("a", "LT", true), rule("a", "CO"), rule("b", "CO"), rule("b", "AR"), rule("a", "MX")],
  eea_countries: ["SE", "LT"],
};

function input(): TeaserInput {
  return {
    offers: [
      ...["a", "b"].map((s) => offer(s, "SE", 10)),
      offer("a", "LT", 8),
      offer("a", "CO", 4),
      offer("b", "CO", 5),
      offer("b", "AR", 2),
      offer("a", "MX", 7.95),
      offer("c", "CO", 1),
    ],
    source,
    residence: "SE",
    residenceCurrency: "EUR",
    tieCountries: ["AR", "CO", "LT", "MX", "SE"],
    rates: { EUR: 1 },
    feePct: 0,
    base: { ...defaultState(services, "EUR", ["a", "b"]) },
  };
}

describe("savingsTeasers", () => {
  it("lists no ties first, then ties that save more, best first", () => {
    const teasers = savingsTeasers(input());
    expect(teasers.map((t) => [t.tie, Math.round(t.pct), t.countries])).toEqual([
      [null, 10, ["LT"]],
      ["CO", 55, ["CO"]],
      ["AR", 50, ["AR", "LT"]],
    ]);
  });

  it("leaves out ties that add less than a point over no ties", () => {
    // MX saves 8 -> 7.95 on top of LT: 0.25 % of 20, under the minimum gain.
    expect(savingsTeasers(input()).some((t) => t.tie === "MX")).toBe(false);
  });

  it("carries the comparator state whose level-3 result is the advertised share", () => {
    const data = input();
    for (const teaser of savingsTeasers(data)) {
      expect(teaser.state.risk).toBe(TEASER_RISK);
      expect(teaser.state.services).toEqual(["a", "b"]);
      const custom = teaser.state.custom;
      expect(custom?.residence).toBe("SE");
      expect(custom?.ties.map((t) => [t.cc, t.card, t.doc, t.presence])).toEqual(
        teaser.tie ? [[teaser.tie, true, true, "periodic"]] : [],
      );
      const relabelled = relabelOffers(
        data.offers,
        data.source,
        toCapabilities(custom ?? data.base.custom!),
        false,
      );
      const ctx = { rates: data.rates, residence: "SE", residenceCurrency: "EUR", feePct: 0 };
      const rows = ["a", "b"].map((id) => cheapestFor(id, "p", relabelled, ctx));
      const pct = savingsPctByRisk(rows, { currency: "EUR", rates: data.rates, feePct: 0 });
      expect(pct[TEASER_RISK]).toBeCloseTo(teaser.pct, 6);
    }
  });

  it("caps the list and skips the no-ties message when it saves nothing", () => {
    const data = { ...input(), source: { ...source, rules: source.rules.slice(1) } };
    const teasers = savingsTeasers(data, 1);
    expect(teasers.map((t) => t.tie)).toEqual(["CO"]);
  });
});

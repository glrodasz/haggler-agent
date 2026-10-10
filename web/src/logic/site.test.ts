import { readdirSync, readFileSync } from "node:fs";
import { join } from "node:path";
import { describe, expect, it } from "vitest";
import { es } from "../i18n/es";
import { cheapestFor } from "./compare";
import { decodeState, defaultState, encodeState } from "./selection";
import {
  assertSiteData,
  bundleCoversService,
  bundleDiscountsService,
  buildIndex,
  isActiveBundle,
  riskClass,
  splitBundleVerdicts,
} from "./site";
import { inlineJson, packOffers, unpackOffers } from "./transport";
import type { Bundle, BundleComponent, BundleVerdict, ChangeRow, Offer, SiteData } from "./types";
import { DATA_FILES } from "./types";
import { routes, withBase } from "./paths";

const sampleDir = join(import.meta.dirname, "..", "data-sample");

function loadSample(): SiteData {
  const raw: Record<string, unknown> = {};
  for (const name of DATA_FILES) {
    raw[name] = JSON.parse(readFileSync(join(sampleDir, `${name}.json`), "utf8"));
  }
  return assertSiteData(raw);
}

const data = loadSample();
const site = buildIndex(data);

describe("sample data integrity", () => {
  it("ships exactly the contract files", () => {
    const files = readdirSync(sampleDir)
      .filter((f) => f.endsWith(".json"))
      .sort();
    expect(files).toEqual([...DATA_FILES].map((n) => `${n}.json`).sort());
  });

  it("references only known services, plans and countries", () => {
    for (const o of data.offers) {
      expect(site.service(o.service_id), o.obs_key).toBeDefined();
      expect(
        site.service(o.service_id)?.plans.some((p) => p.id === o.plan_id),
        o.obs_key,
      ).toBe(true);
      expect(site.country(o.country), o.obs_key).toBeDefined();
      expect(o.obs_key).toBe(
        `${o.service_id}:${o.plan_id}:${o.country}:${o.channel}:${o.billing_period}`,
      );
    }
    const profiles = new Set(data.feasibility.profiles.map((p) => p.id));
    for (const b of data.bundles) {
      expect(site.country(b.country), b.bundle_id).toBeDefined();
      const ids = [
        ...b.components.map((c) => c.service_id),
        ...b.covers,
        ...b.standalone_missing,
        ...b.optional_savings.map((o) => o.service_id),
        ...b.verdicts.flatMap((v) => v.components.map((c) => c.service_id)),
        ...(b.observed_service_id ? [b.observed_service_id] : []),
      ];
      for (const id of ids) expect(site.service(id), `${b.bundle_id}: ${id}`).toBeDefined();
      for (const c of b.components) {
        if (c.plan_id) {
          expect(
            site.service(c.service_id)?.plans.some((p) => p.id === c.plan_id),
            `${b.bundle_id}: ${c.service_id}.${c.plan_id}`,
          ).toBe(true);
        }
      }
      for (const v of b.verdicts) expect(profiles.has(v.profile), b.bundle_id).toBe(true);
    }
  });

  it("includes the services, countries and bundles the site is designed around", () => {
    const services = new Set(data.offers.map((o) => o.service_id));
    for (const id of [
      "netflix",
      "youtube_premium",
      "disney_plus",
      "hbo_max",
      "spotify",
      "icloud_plus",
      "google_one",
      "apple_one",
      "apple_music",
      "apple_tv",
      "microsoft_365",
    ]) {
      expect(services.has(id), id).toBe(true);
    }
    expect(data.countries.map((c) => c.cc).sort()).toEqual([
      "BR",
      "CO",
      "DE",
      "ES",
      "IN",
      "LT",
      "MX",
      "SE",
      "TR",
      "US",
    ]);
    expect(data.bundles.some((b) => b.bundle_id === "meli_plus_co_total")).toBe(true);
    expect(data.changes.length).toBeGreaterThan(0);
  });

  it("marks the sample run so the site shows the banner", () => {
    expect(site.isSample).toBe(true);
    expect(site.residenceCurrency).toBe("SEK");
  });

  it("flags App Store offers as possibly marked up and Apple services as Apple-billed", () => {
    for (const o of data.offers) {
      if (o.channel === "ios_iap") expect(o.flags, o.obs_key).toContain("iap_maybe_marked_up");
      if (["icloud_plus", "apple_one", "apple_music", "apple_tv"].includes(o.service_id)) {
        expect(o.flags, o.obs_key).toContain("apple_billing");
      }
    }
  });
});

describe("assertSiteData", () => {
  it("lists every missing file with instructions", () => {
    expect(() => assertSiteData({ meta: data.meta })).toThrow(
      /offers\.json[\s\S]*npm run sample-data/,
    );
  });

  it("rejects non-list files", () => {
    expect(() => assertSiteData({ ...data, offers: {} as unknown as Offer[] })).toThrow(/offers/);
  });
});

describe("SiteIndex", () => {
  it("sorts service offers by monthly EUR", () => {
    const eur = site.offersForService("netflix").map((o) => o.monthly_eur ?? Infinity);
    expect(eur).toEqual([...eur].sort((a, b) => a - b));
  });

  it("converts to the residence currency exactly for local offers", () => {
    const local = data.offers.find((o) => o.country === "SE" && o.service_id === "spotify")!;
    expect(site.monthlyIn(local, "SEK")).toBe(local.monthly_equiv);
    const foreign = data.offers.find((o) => o.country === "CO" && o.service_id === "spotify")!;
    expect(site.monthlyIn(foreign, "SEK")).toBeCloseTo(
      foreign.monthly_eur! * data.fx.rates.SEK!,
      6,
    );
    expect(site.monthlyIn({ ...foreign, monthly_eur: null }, "SEK")).toBeNull();
  });

  it("resolves labels from feasibility.json with Spanish fallbacks", () => {
    expect(site.label("official_home").rank).toBe(0);
    expect(site.label("tos_breach_enforced").name).toMatch(/control activo/);
    expect(site.label("brand_new_label", 7)).toMatchObject({ name: "brand_new_label", rank: 7 });
    expect(site.acceptableLabels().map((l) => l.rank)).toEqual([0, 1, 2, 3]);
  });

  it("keeps changes newest first within 90 days of as_of", () => {
    const extra: ChangeRow = { ...data.changes[0]!, date: "2026-06-01" };
    const idx = buildIndex({ ...data, changes: [extra, ...data.changes] });
    const dates = idx.recentChanges(90).map((c) => c.date);
    expect(dates).not.toContain("2026-06-01");
    expect(dates).toEqual([...dates].sort().reverse());
  });

  it("formats change values with the offer currency", () => {
    const row = data.changes.find((c) => c.obs_key === "spotify:individual:SE:web:month")!;
    expect(site.formatChangeValue(row, 139).replace(/ /g, " ")).toBe("139 SEK");
    expect(site.formatChangeValue(row, null)).toBe("—");
    expect(site.formatChangeValue(row, "129 kr")).toBe("129 kr");
    expect(site.formatChangeValue(row, { amount: 5, currency: "EUR" }).replace(/ /g, " ")).toBe(
      "5 €",
    );
  });

  it("shows only the default profile's feasibility records, pending ones last", () => {
    expect(site.defaultProfile?.id).toBe(data.meta.profile);
    const spotify = site.feasibilityForService("spotify");
    expect(spotify.map((r) => [r.target_country, r.provenance])).toEqual([
      ["CO", "curated"],
      ["LT", "agent_pending"],
    ]);
    const netflix = site.feasibilityForService("netflix");
    expect(netflix.every((r) => r.profile === data.meta.profile)).toBe(true);
    // Another profile's records (netflix CO for se_resident_eu_generic) stay out of the pages.
    expect(netflix.map((r) => r.target_country)).toEqual(["CO", "LT"]);
    const ranks = netflix.filter((r) => r.provenance !== "agent_pending").map((r) => r.rank);
    expect(ranks).toEqual([...ranks].sort((a, b) => a - b));

    const lt = site.feasibilityForCountry("LT");
    expect(lt.every((r) => r.profile === data.meta.profile)).toBe(true);
    expect(lt.at(-1)).toMatchObject({ service_id: "spotify", provenance: "agent_pending" });
    const reviewed = lt.filter((r) => r.provenance !== "agent_pending");
    expect(reviewed.map((r) => site.serviceName(r.service_id))).toEqual(
      reviewed.map((r) => site.serviceName(r.service_id)).sort((a, b) => a.localeCompare(b, "es")),
    );
    expect(site.feasibilityForCountry("TR")).toEqual([]);
  });

  it("lists active bundles by what they stand in for, never by a discount", () => {
    const ids = (bundles: Bundle[]) => bundles.map((b) => b.bundle_id);
    // Apple One has no component "apple_one": its bundles come from the observed price.
    expect(ids(site.bundlesForService("apple_one"))).toEqual([
      "apple_one_individual_se",
      "apple_one_family_se",
    ]);
    expect(ids(site.bundlesForService("disney_plus"))).toEqual([
      "meli_plus_co_total",
      "meli_plus_mx_mega",
      "meli_plus_br_mega",
      "telia_se_obegransad_streaming",
    ]);
    // HBO Max is only a 30 % discount in Meli+ CO; the expired MX row is left out.
    expect(ids(site.bundlesForService("hbo_max"))).toEqual([
      "meli_plus_mx_mega",
      "meli_plus_br_mega",
      "telia_se_obegransad_streaming",
    ]);
    expect(site.bundlesForService("paramount_plus")).toEqual([]);
    // ... but its page still leads to the bundles that discount it.
    expect(ids(site.discountBundlesForService("paramount_plus"))).toEqual(["meli_plus_co_total"]);
    expect(ids(site.discountBundlesForService("hbo_max"))).toEqual([
      "meli_plus_co_total",
      "meli_plus_co_esencial",
    ]);
    expect(site.discountBundlesForService("disney_plus")).toEqual([]);
    for (const b of data.bundles) {
      for (const id of b.covers) {
        expect(bundleCoversService(b, id), `${b.bundle_id}: ${id}`).toBe(true);
      }
    }
    const covers = (b: Partial<Bundle>) =>
      bundleCoversService(
        { observed_service_id: null, covers: [], components: [], ...b } as Bundle,
        "spotify",
      );
    expect(covers({ covers: ["spotify"] })).toBe(true);
    expect(covers({ observed_service_id: "spotify" })).toBe(true);
    const component = data.bundles[0]!.components[0]!;
    const as = (inclusion: BundleComponent["inclusion"]) => ({
      components: [{ ...component, service_id: "spotify", inclusion }],
    });
    expect(covers(as("choose_n"))).toBe(true);
    expect(covers(as("included"))).toBe(true);
    expect(covers(as("free_months"))).toBe(false);
    expect(covers(as("discount_pct"))).toBe(false);
    const discounts = (b: Partial<Bundle>) =>
      bundleDiscountsService(
        { observed_service_id: null, covers: [], components: [], ...b } as Bundle,
        "spotify",
      );
    expect(discounts(as("free_months"))).toBe(true);
    expect(discounts(as("discount_pct"))).toBe(true);
    expect(discounts(as("discount_abs"))).toBe(true);
    expect(discounts(as("included"))).toBe(false);
    // Covered and also discounted: listed once, among the bundles that include it.
    expect(discounts({ ...as("discount_pct"), covers: ["spotify"] })).toBe(false);
  });

  it("keeps expired bundles apart from the active ones", () => {
    const mx = site.bundlesForCountry("MX");
    expect(mx.map((b) => b.bundle_id)).toEqual(["meli_plus_mx_mega"]);
    expect(mx.every(isActiveBundle)).toBe(true);
    const expired = site.expiredBundlesForCountry("MX");
    expect(expired.map((b) => b.bundle_id)).toEqual(["meli_plus_mx_mega_lanzamiento"]);
    expect(expired[0]).toMatchObject({ display_status: "expired", verdicts: [] });
    expect(site.expiredBundlesForCountry("SE")).toEqual([]);
    expect(site.bundlesForCountry("CO").map((b) => b.bundle_id)).toEqual([
      "meli_plus_co_total",
      "meli_plus_co_esencial",
    ]);
    const all = data.countries.flatMap((c) => [
      ...site.bundlesForCountry(c.cc),
      ...site.expiredBundlesForCountry(c.cc),
    ]);
    expect(all).toHaveLength(data.bundles.length);
  });

  it("puts the default profile's bundle verdict first, the others apart", () => {
    const meli = data.bundles.find((b) => b.bundle_id === "meli_plus_co_total")!;
    const { main, pending, others } = site.bundleVerdicts(meli);
    expect(main).toMatchObject({
      profile: "se_resident_co_citizen",
      provenance: "curated",
      label: "tos_breach_low_enforcement",
      rank: 2,
    });
    expect(pending).toBeUndefined();
    expect(others.map((v) => [v.profile, v.provenance, v.label, v.rank])).toEqual([
      ["se_resident_eu_generic", "composed", "not_feasible", 4],
    ]);
    expect(others[0]!.reasons_es).toContain(
      "Exige un documento de identidad de CO (cédula); el perfil no tiene uno.",
    );

    const reviewed = main!;
    const withPending: BundleVerdict = { ...reviewed, provenance: "agent_pending", rank: 3 };
    const split = splitBundleVerdicts([reviewed, withPending, ...others], reviewed.profile);
    expect(split.main).toBe(reviewed);
    expect(split.pending).toBe(withPending);
    expect(split.others).toEqual(others);
    expect(splitBundleVerdicts([], "x")).toEqual({
      main: undefined,
      pending: undefined,
      others: [],
    });
  });

  it("names profiles and counts days to the generation day", () => {
    expect(site.profileName("se_resident_eu_generic")).toMatch(/Residente en/);
    expect(site.profileName("gone")).toBe("gone");
    expect(site.daysBeforeGeneration(data.meta.generated_at)).toBe(0);
    expect(site.daysBeforeGeneration("2026-09-23")).toBe(15);
    expect(site.daysBeforeGeneration(null)).toBeNull();
    // The day Python dated the status on, even when the price run (as_of) is older.
    const late = buildIndex({
      ...data,
      meta: { ...data.meta, as_of: "2026-10-01", generated_at: "2026-10-10T05:00:00Z" },
    });
    expect(late.daysBeforeGeneration("2026-10-05")).toBe(5);
  });

  it("maps ranks to CSS classes", () => {
    expect(riskClass(0)).toBe("risk-0");
    expect(riskClass(4)).toBe("risk-4");
    expect(riskClass(99)).toBe("risk-unknown");
  });
});

describe("empty data contract", () => {
  // What `haggler site-data` writes for a data/ dir without any completed run (first deploy,
  // fresh clone): no run_id, no as_of, no FX table, no prices; the changelog may still exist.
  const empty: SiteData = {
    ...data,
    meta: { ...data.meta, run_id: null, as_of: null, research_run_id: null, research_as_of: null },
    fx: { as_of: null, base: "EUR", rates: {}, stale: [] },
    offers: [],
    cheapest: [],
    coverage: [],
    changes: [{ ...data.changes[0]!, date: "2026-01-15" }],
  };

  it("builds the index without a run", () => {
    const idx = buildIndex(assertSiteData({ ...empty }));
    expect(idx.isSample).toBe(false);
    expect(idx.pricedServices()).toEqual([]);
    expect(idx.residenceName).toBe("Suecia");
  });

  it("keeps the changelog when as_of is unknown", () => {
    expect(
      buildIndex(empty)
        .recentChanges(90)
        .map((c) => c.date),
    ).toEqual(["2026-01-15"]);
  });

  it("never prints a null run in the footer", () => {
    expect(es.site.footer.dataAsOf("—", null)).not.toMatch(/null/);
    expect(es.site.footer.dataAsOf("8 oct 2026", "2026-10-08-abc")).toContain("2026-10-08-abc");
  });
});

describe("transport", () => {
  it("round-trips the comparator offers and rebuilds obs_key", () => {
    const offers = data.offers.slice(0, 20).map((o) => ({ ...o }));
    const unpacked = unpackOffers(JSON.parse(inlineJson(packOffers(offers))));
    expect(unpacked).toHaveLength(20);
    expect(unpacked[0]?.obs_key).toBe(offers[0]?.obs_key);
    expect(unpacked[0]?.monthly_equiv).toBe(offers[0]?.monthly_equiv);
    const ctx = { rates: data.fx.rates, residence: "SE", residenceCurrency: "SEK", feePct: 2 };
    const a = cheapestFor("apple_music", "family", offers, ctx);
    const b = cheapestFor("apple_music", "family", unpacked, ctx);
    expect(b.bestByRisk[2]?.obs_key).toBe(a.bestByRisk[2]?.obs_key);
  });

  it("escapes characters that could close the script element", () => {
    expect(inlineJson({ s: "</script><b>" })).not.toContain("</");
    expect(JSON.parse(inlineJson({ s: "</script>" }))).toEqual({ s: "</script>" });
  });

  it("decodes garbage as an empty list", () => {
    expect(unpackOffers(null)).toEqual([]);
    expect(unpackOffers({ fields: ["a"], rows: [[1, 2]] })).toEqual([]);
  });
});

describe("selection hash", () => {
  const services = data.services.map((s) => ({
    id: s.id,
    default_plan: s.default_plan,
    plans: s.plans.map((p) => ({ id: p.id })),
  }));
  const fallback = defaultState(services, "SEK");

  it("preselects common services with their default plans", () => {
    expect(fallback.services).toEqual(["netflix", "youtube_premium", "spotify", "icloud_plus"]);
    expect(fallback.plans.netflix).toBe("standard");
    expect(fallback.risk).toBe(0);
  });

  it("round-trips through the URL hash", () => {
    const state = {
      ...fallback,
      services: ["netflix", "spotify"],
      plans: { ...fallback.plans, spotify: "family" },
      risk: 2 as const,
      currency: "EUR",
    };
    const hash = `#${encodeState(state)}`;
    expect(hash).toBe("#s=netflix.standard,spotify.family&r=2&c=EUR");
    expect(decodeState(hash, services, ["SEK", "EUR", "USD"], fallback)).toEqual(state);
  });

  it("adds the profile and pending findings only when they differ from the defaults", () => {
    const options = { profiles: ["se_resident_eu_generic"], pending: true };
    const state = {
      ...fallback,
      services: ["netflix"],
      profile: "se_resident_eu_generic",
      pending: true,
    };
    const hash = `#${encodeState(state)}`;
    expect(hash).toBe("#s=netflix.standard&r=0&c=SEK&p=se_resident_eu_generic&u=1");
    expect(decodeState(hash, services, ["SEK"], fallback, options)).toEqual(state);
    expect(encodeState({ ...state, profile: null, pending: false })).toBe(
      "s=netflix.standard&r=0&c=SEK",
    );
    // Old links (no p / u) keep the default profile without pending findings.
    expect(decodeState("#s=netflix.standard&r=0&c=SEK", services, ["SEK"], fallback, options))
      .toMatchObject({ profile: null, pending: false });
  });

  it("ignores unknown profiles, the default profile and u=1 without pending records", () => {
    const options = { profiles: ["se_resident_eu_generic"], pending: true };
    const decode = (hash: string, opts = options) =>
      decodeState(hash, services, ["SEK"], fallback, opts);
    expect(decode("#s=netflix.standard&p=gone").profile).toBeNull();
    expect(decode("#s=netflix.standard&p=se_resident_co_citizen").profile).toBeNull();
    expect(decode("#s=netflix.standard&u=yes").pending).toBe(false);
    expect(decode("#s=netflix.standard&u=1", { profiles: [], pending: false })).toMatchObject({
      profile: null,
      pending: false,
    });
    expect(decodeState("#u=1&p=se_resident_eu_generic", services, ["SEK"], fallback)).toMatchObject(
      { profile: null, pending: false },
    );
  });

  it("ignores unknown services, plans, risks and currencies", () => {
    const s = decodeState(
      "#s=nope.x,spotify.zzz,netflix.premium&r=9&c=JPY",
      services,
      ["SEK", "EUR"],
      fallback,
    );
    expect(s.services).toEqual(["netflix", "spotify"]);
    expect(s.plans.netflix).toBe("premium");
    expect(s.plans.spotify).toBe("individual");
    expect(s.risk).toBe(0);
    expect(s.currency).toBe("SEK");
    expect(decodeState("", services, ["SEK"], fallback)).toBe(fallback);
  });
});

describe("paths", () => {
  it("prefixes every internal link with the base", () => {
    expect(withBase("servicios/", "/haggler-agent/")).toBe("/haggler-agent/servicios/");
    expect(withBase("/servicios/", "/haggler-agent")).toBe("/haggler-agent/servicios/");
    expect(withBase("", "/")).toBe("/");
    expect(routes.service("netflix", "/haggler-agent/")).toBe("/haggler-agent/servicios/netflix/");
    expect(routes.country("CO", "/x/")).toBe("/x/paises/co/");
  });
});

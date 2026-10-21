import { readFileSync } from "node:fs";
import { join } from "node:path";
import { describe, expect, it } from "vitest";
import { fromCapabilities } from "./profile";
import { buildProfileKit, profileView } from "./profileKit";
import { assertSiteData, buildIndex, splitBundleVerdicts } from "./site";
import { DATA_FILES, RISK_LEVELS } from "./types";
import { neutralProfile } from "./visitor";

const sampleDir = join(import.meta.dirname, "..", "data-sample");
const raw: Record<string, unknown> = {};
for (const name of DATA_FILES) {
  raw[name] = JSON.parse(readFileSync(join(sampleDir, `${name}.json`), "utf8"));
}
const site = buildIndex(assertSiteData(raw), "es");
const kit = buildProfileKit(site);
const defaultCaps = site.defaultProfile?.capabilities;

describe("profile kit", () => {
  it("lists the countries with prices as residences, with their currency", () => {
    const es = kit.residences.find((r) => r.cc === "ES");
    expect(es).toEqual({ cc: "ES", name: "España", currency: "EUR" });
    expect(kit.default_residence).toBe(site.meta.residence);
  });

  it("names countries and labels in the site's locale", () => {
    const en = buildProfileKit(buildIndex(assertSiteData(raw), "en"));
    expect(en.locale).toBe("en");
    expect(kit.locale).toBe("es");
    expect(en.residences.find((r) => r.cc === "ES")?.name).toBe("Spain");
  });

  it("gives the default profile the cheapest offers cheapest.json publishes", () => {
    expect(defaultCaps).toBeDefined();
    const view = profileView(kit, fromCapabilities(defaultCaps!));
    expect(view.preset).toBe(site.feasibility.default_profile);
    for (const row of site.cheapest) {
      // cheapest.json leaves findings pending review out; the view counts them like the
      // comparator, so only pairs whose rule has no pending variant must agree.
      const pending = site.feasibility.rules.some(
        (r) => r.service_id === row.service_id && r.pending !== null,
      );
      if (pending) continue;
      const mine = view.cheapest(row.service_id, row.plan_id);
      expect(mine.home?.country ?? null).toBe(row.home?.country ?? null);
      for (const k of RISK_LEVELS) {
        expect(mine.bestByRisk[k]?.obs_key ?? null, `${row.service_id} ${k}`).toBe(
          row.best_by_risk[`${k}`]?.obs_key ?? null,
        );
      }
    }
  });

  it("walks each bundle's rule to the default profile's published verdict", () => {
    const view = profileView(kit, fromCapabilities(defaultCaps!));
    for (const b of site.bundles) {
      const main = splitBundleVerdicts(b.verdicts, site.feasibility.default_profile).main;
      if (!main || !b.rule) continue;
      expect(view.bundleVerdict(b.bundle_id)?.label, b.bundle_id).toBe(main.label);
    }
  });

  it("judges a profile that lives elsewhere from its own residence", () => {
    const view = profileView(kit, neutralProfile("MX"));
    expect(view.preset).toBeNull();
    expect(view.residenceName).toBe("México");
    expect(view.residenceCurrency).toBe("MXN");
    const mx = site.bundles.find((b) => b.country === "MX" && b.rule);
    if (mx) expect(view.bundleVerdict(mx.bundle_id)?.label).toBe("official_home");
    expect(view.verdict("netflix", "MX").label).toBe("official_home");
    expect(view.cheapest("netflix", "standard").home?.country).toBe("MX");
  });

  it("adds the FX fee only outside the residence currency", () => {
    const view = profileView(kit, neutralProfile("ES"));
    expect(view.withFee(10, "EUR")).toBe(10);
    expect(view.withFee(10, "SEK")).toBe(10 * (1 + kit.fee_pct / 100));
    expect(view.withFee(null, "SEK")).toBeNull();
  });
});

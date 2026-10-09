import { existsSync, readFileSync } from "node:fs";
import { join } from "node:path";
import { describe, expect, it } from "vitest";
import { cheapestFor } from "./compare";
import { assertSiteData } from "./site";
import { packVerdicts, unpackVerdicts } from "./transport";
import type { FeasibilityData, SiteData } from "./types";
import { DATA_FILES } from "./types";
import {
  isPending,
  recordsForDisplay,
  relabelOffers,
  resolveProfile,
  type VerdictSource,
  verdictLookup,
} from "./verdicts";

const srcDir = join(import.meta.dirname, "..");

function load(dir: string): SiteData {
  const raw: Record<string, unknown> = {};
  for (const name of DATA_FILES) {
    raw[name] = JSON.parse(readFileSync(join(dir, `${name}.json`), "utf8"));
  }
  return assertSiteData(raw);
}

const labels = (offers: readonly { label: string; rank: number; eu_badge: boolean }[]) =>
  offers.map((o) => [o.label, o.rank, o.eu_badge]);

const datasets: [string, string][] = [
  ["data-sample", join(srcDir, "data-sample")],
  ["data (generated)", join(srcDir, "data")],
];

describe.each(datasets)("relabelOffers reproduces offers.json (%s)", (_name, dir) => {
  const present = DATA_FILES.every((name) => existsSync(join(dir, `${name}.json`)));

  it.skipIf(!present)("default profile, pending findings excluded", () => {
    const data = load(dir);
    const { feasibility, offers } = data;
    expect(feasibility.default_profile).toBe(data.meta.profile);
    const relabelled = relabelOffers(offers, feasibility, feasibility.default_profile, false);
    expect(labels(relabelled)).toEqual(labels(offers));
    // An unknown or missing profile id falls back to the default profile.
    expect(labels(relabelOffers(offers, feasibility, "no_such_profile", false))).toEqual(
      labels(offers),
    );
    expect(labels(relabelOffers(offers, feasibility, null, false))).toEqual(labels(offers));
  });

  it.skipIf(!present)("every profile and pending mode stays consistent with its records", () => {
    const { feasibility, offers } = load(dir);
    for (const profile of feasibility.profiles) {
      for (const includePending of [false, true]) {
        const relabelled = relabelOffers(offers, feasibility, profile.id, includePending);
        relabelled.forEach((o, i) => {
          expect(o.obs_key).toBe(offers[i]?.obs_key);
          if (o.country === profile.residence_country) {
            expect([o.label, o.rank, o.eu_badge]).toEqual(["official_home", 0, false]);
            return;
          }
          const own = feasibility.records.filter(
            (r) =>
              r.profile === profile.id &&
              r.service_id === o.service_id &&
              r.target_country === o.country &&
              (includePending || !isPending(r)),
          );
          const chosen = own.find(isPending) ?? own[0];
          expect([o.label, o.rank], o.obs_key).toEqual(
            chosen ? [chosen.label, chosen.rank] : ["unknown", 99],
          );
        });
      }
    }
  });
});

describe("verdicts on the sample data", () => {
  const data = load(join(srcDir, "data-sample"));
  const { feasibility } = data;
  const other = "se_resident_eu_generic";

  it("ships two profiles, the default one first", () => {
    expect(feasibility.profiles.map((p) => p.id)).toEqual([data.meta.profile, other]);
    expect(resolveProfile(feasibility, other)?.residence_country).toBe("SE");
    expect(resolveProfile(feasibility, "gone")?.id).toBe(data.meta.profile);
  });

  it("uses the residence country as home, then the profile's own record", () => {
    const v = verdictLookup(feasibility, other, false);
    expect(v("netflix", "SE")).toMatchObject({ label: "official_home", rank: 0 });
    // Reviewed research verdict with missing facts: listed, but never chosen.
    expect(v("netflix", "CO")).toEqual({
      label: "unknown",
      rank: 99,
      eu_badge: false,
      provenance: "agent_reviewed",
    });
    // A false declaration (billing address) is never offered.
    expect(v("icloud_plus", "LT")).toMatchObject({ label: "not_feasible", rank: 4 });
    // The default profile's curated records do not apply to another profile.
    expect(v("spotify", "CO")).toEqual({
      label: "unknown",
      rank: 99,
      eu_badge: false,
      provenance: null,
    });
  });

  it("prefers the pending record only when pending findings are included", () => {
    const off = verdictLookup(feasibility, other, false);
    const on = verdictLookup(feasibility, other, true);
    expect(off("spotify", "LT")).toMatchObject({ rank: 99, provenance: null });
    expect(on("spotify", "LT")).toMatchObject({ rank: 2, provenance: "agent_pending" });
    expect(on("netflix", "CO")).toMatchObject({ label: "not_feasible", provenance: "agent_pending" });
    // Without a pending record the non-pending one is kept.
    expect(on("icloud_plus", "LT")).toMatchObject({ rank: 4, provenance: "agent_reviewed" });
  });

  it("changes what the comparator picks", () => {
    const ctx = { rates: data.fx.rates, residence: "SE", residenceCurrency: "SEK", feePct: 2 };
    const pick = (profile: string, pending: boolean) =>
      cheapestFor(
        "spotify",
        "individual",
        relabelOffers(data.offers, feasibility, profile, pending),
        ctx,
      ).bestByRisk[2]?.country;
    expect(pick(data.meta.profile, false)).toBe("SE");
    expect(pick(data.meta.profile, true)).toBe("LT");
    expect(pick(other, false)).toBe("SE");
    expect(pick(other, true)).toBe("LT");
  });

  it("keeps a pair's reviewed and pending records, reviewed first", () => {
    const rows = recordsForDisplay(feasibility.records, other);
    expect(rows.every((r) => r.profile === other)).toBe(true);
    const provenances = rows.map((r) => r.provenance);
    expect(provenances.indexOf("agent_pending")).toBeGreaterThan(
      provenances.lastIndexOf("agent_reviewed"),
    );
    const netflix = rows.filter((r) => r.service_id === "netflix" && r.target_country === "CO");
    expect(netflix.map((r) => r.provenance)).toEqual(["agent_reviewed", "agent_pending"]);
    const byCountry = recordsForDisplay(feasibility.records, data.meta.profile, (a, b) =>
      a.target_country.localeCompare(b.target_country),
    );
    expect(byCountry.at(-1)?.provenance).toBe("agent_pending");
    expect(byCountry.filter((r) => !isPending(r)).map((r) => r.target_country)).toEqual(
      byCountry
        .filter((r) => !isPending(r))
        .map((r) => r.target_country)
        .sort(),
    );
  });

  it("round-trips the records the island ships", () => {
    const source: VerdictSource = {
      default_profile: feasibility.default_profile,
      profiles: feasibility.profiles,
      records: unpackVerdicts(JSON.parse(JSON.stringify(packVerdicts(feasibility.records)))),
    };
    for (const profile of feasibility.profiles) {
      for (const pending of [false, true]) {
        expect(labels(relabelOffers(data.offers, source, profile.id, pending))).toEqual(
          labels(relabelOffers(data.offers, feasibility, profile.id, pending)),
        );
      }
    }
    expect(unpackVerdicts(null)).toEqual([]);
    expect(unpackVerdicts({ fields: ["profile"], rows: [["a", "b"]] })).toEqual([]);
  });
});

describe("verdicts without research", () => {
  // A site-data run with one synthesized profile and only curated records.
  const feasibility: FeasibilityData = {
    labels: [],
    default_profile: "p",
    profiles: [{ id: "p", name_es: "P", description_es: "", residence_country: "SE" }],
    records: [],
  };

  it("labels every foreign offer unknown and the residence official", () => {
    const offers = [
      { service_id: "netflix", country: "SE", label: "x", rank: 7, eu_badge: true },
      { service_id: "netflix", country: "CO", label: "x", rank: 7, eu_badge: true },
    ];
    expect(labels(relabelOffers(offers, feasibility, "p", true))).toEqual([
      ["official_home", 0, false],
      ["unknown", 99, false],
    ]);
  });
});

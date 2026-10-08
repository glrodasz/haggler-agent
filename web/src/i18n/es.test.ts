import { describe, expect, it } from "vitest";
import { es, profileDescription } from "./es";

/** Every literal string in the copy (functions are templates and are checked separately). */
function literals(value: unknown, path = "es"): [string, string][] {
  if (typeof value === "string") return [[path, value]];
  if (Array.isArray(value)) return value.flatMap((v, i) => literals(v, `${path}[${i}]`));
  if (value && typeof value === "object") {
    return Object.entries(value).flatMap(([k, v]) => literals(v, `${path}.${k}`));
  }
  return [];
}

describe("residence-neutral copy", () => {
  // The residence is a site-data option (`haggler site-data --residence`); the copy must take it
  // from meta.residence instead of assuming Sweden.
  it("never hard-codes the residence country", () => {
    const offenders = literals(es).filter(([, text]) => /suec/i.test(text));
    expect(offenders).toEqual([]);
  });

  it("names the residence passed in", () => {
    const name = "España";
    for (const text of [
      es.site.description(name),
      es.home.lead(name),
      es.axes.practical!.question(name),
      es.bundle.feasibility(name),
      es.service.colSaving(name),
      es.service.feasibilityTitle(name),
      es.service.feasibilityLead(name),
      es.service.noFeasibility(name),
      es.country.feasibilityTitle(name),
      es.euBadgeTitle(name),
      es.methodology.assumptions(name)[2]!.text,
    ]) {
      expect(text).toContain(name);
    }
  });

  it("describes the feasibility profile from meta.profile", () => {
    expect(profileDescription("se_resident_co_citizen", "Suecia")).toMatch(
      /residente en Suecia.*colombian/,
    );
    expect(profileDescription("otro_perfil", "España")).toContain("otro_perfil");
    expect(es.methodology.disclaimer("residente en España")[1]).toContain("residente en España");
  });
});

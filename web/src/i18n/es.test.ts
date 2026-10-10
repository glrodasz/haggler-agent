import { existsSync, readFileSync } from "node:fs";
import { join } from "node:path";
import { describe, expect, it } from "vitest";
import type { Bundle } from "../logic/types";
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

describe("bundle copy never suggests misrepresenting anything", () => {
  // A requirement the profile does not meet is stated as a fact; nothing proposes a workaround.
  const forbidden =
    /\bvpn\b|proxy|otra direcci[oó]n|direcci[oó]n falsa|datos falsos|falsific|fing(ir|e)|simular/i;
  /** Every string in the copy, templates filled with placeholders. */
  const texts = (value: unknown): string[] => {
    if (typeof value === "function") {
      return [String((value as (...args: string[]) => unknown)("X", "Y", "Z"))];
    }
    if (Array.isArray(value)) return value.flatMap(texts);
    if (value && typeof value === "object") return Object.values(value).flatMap(texts);
    return typeof value === "string" ? [value] : [];
  };

  it("in the card strings", () => {
    const copy = texts(es.bundle);
    expect(copy.length).toBeGreaterThan(50);
    expect(copy.filter((text) => forbidden.test(text))).toEqual([]);
  });

  // The sample, and src/data when it exists: CI's contract job fills it with `haggler
  // site-data` output, so the reasons the Python evaluator writes are checked here too.
  const bundleFiles: [string, string][] = [
    ["data-sample", join(import.meta.dirname, "..", "data-sample", "bundles.json")],
    ["data (generated)", join(import.meta.dirname, "..", "data", "bundles.json")],
  ];

  it.each(bundleFiles)("in the bundles' reasons, assumptions and notes (%s)", (_name, file) => {
    if (!existsSync(file)) return;
    const bundles: Bundle[] = JSON.parse(readFileSync(file, "utf8"));
    const data = bundles.flatMap((b) => [
      b.notes_es,
      ...b.status_reasons_es,
      ...b.verdicts.flatMap((v) => [v.notes_es, ...v.reasons_es, ...v.assumptions_es]),
    ]);
    if (bundles.length > 0) expect(data.length).toBeGreaterThan(0);
    expect(data.filter((t) => forbidden.test(t))).toEqual([]);
  });
});

describe("bundle inclusions", () => {
  it("agree in number and never call free months a missing price", () => {
    expect(es.bundle.inclusion.free_months!("1")).toBe("1 mes gratis");
    expect(es.bundle.inclusion.free_months!("3")).toBe("3 meses gratis");
    expect(es.bundle.freeMonthsNote).not.toBe(es.bundle.optionalNoPrice);
  });
});

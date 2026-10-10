import { existsSync, readFileSync } from "node:fs";
import { join } from "node:path";
import { describe, expect, it } from "vitest";
import type { Bundle } from "../logic/types";
import { BUNDLE_DISPLAY_STATUSES, WATCH_MODES } from "../logic/types";
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

  it("in the bundles page and its navigation entry", () => {
    const copy = [es.site.nav.bundles, ...texts(es.bundlesIndex)];
    expect(copy.length).toBeGreaterThan(15);
    expect(copy.filter((text) => forbidden.test(text))).toEqual([]);
  });

  // The methodology has to name what the site never suggests: every sentence of its bundle
  // section that mentions one of those must rule it out, and the statement must be there.
  it("in the methodology's bundle section, only to rule it out", () => {
    const m = es.methodology;
    const copy = [
      m.bundlesTitle,
      m.bundlesLead,
      ...m.bundles("2 %").flatMap((item) => [item.title, item.text]),
      m.bundlesNotLegal,
      m.watchedTitle,
      m.watchedLead,
      m.watchedCaption,
      m.watchedEmpty,
      m.colBundle,
      m.colSource,
      m.colWatch,
      m.colLastCheck,
      m.colAnchors,
      m.anchors(1, 3),
      m.anchorsNone,
      m.pdfTag,
      m.pdfTitle,
    ];
    const sentences = copy.flatMap((text) => text.split(/(?<=[.;:])\s+/));
    const mentions = sentences.filter((text) => forbidden.test(text));
    expect(mentions.filter((text) => !/\bnunca\b/i.test(text))).toEqual([]);
    expect(mentions.some((text) => /\bnunca sugiere usar una VPN\b/i.test(text))).toBe(true);
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

describe("methodology of bundles", () => {
  it("states the rules a bundle's state, saving and verdict rest on", () => {
    const text = es.methodology
      .bundles("2 %")
      .map((item) => item.text)
      .join(" ");
    for (const rule of [
      "archivo curado a mano", // the YAML is the only source of values
      "cita literal",
      "PDF oficial del país del paquete",
      "15 días o menos",
      "edición vigente",
      "Verificación manual",
      "45 días",
      "nunca cuenta como fuente oficial",
      "precios de introducción, descuentos ni meses gratis",
      "precio marginal",
      "plan base",
      "(2 %)",
      "ficha curada a mano",
      "oferta oficial de tu país",
      "se compone de los requisitos",
      "lo dice tal cual",
      "«No factible»",
      "hereda ese paso",
    ]) {
      expect(text, rule).toContain(rule);
    }
    expect(es.methodology.bundlesNotLegal).toMatch(/No es asesoría legal/);
  });

  // Written as the rules, never as a watcher, a proof or an assumption the data does not have.
  it("claims nothing the pipeline does not do yet", () => {
    const m = es.methodology;
    const text = [
      ...m.bundles("2 %").map((item) => item.text),
      m.watchedLead,
      ...Object.values(es.bundle.watchModeTitles),
    ].join(" ");
    expect(text).not.toMatch(/comprobante|el vigilante (lee|y la investigación)|última lectura/);
    expect(text).not.toMatch(/encuentra su cita literal/);
    expect(text).toContain("encontró su cita literal");
    expect(text).toContain("queda registrada la fecha de esa comprobación");
    // Unknown requirements are assumptions only on the bundle's own composed route.
    expect(text).toContain(
      "Cuando el veredicto se compone de los propios términos y requisitos del paquete, un requisito sin datos figura como supuesto",
    );
    expect(text).toContain("solo cuentan los requisitos conocidos");
    // An intro price is only published for a verified bundle (pricing.first_year_monthly_eur).
    expect(text).toContain("un precio de introducción, solo cuando el paquete está verificado");
    // The bundle's own terms: unknown without a rule, allowed only on an official citation.
    expect(text).toContain("regla sobre el país de residencia");
    expect(text).toContain("solo cuenta con una cita oficial");
  });

  it("names every watch mode and display status the table can show", () => {
    for (const mode of WATCH_MODES) {
      expect(es.bundle.watchModes[mode], mode).toBeTruthy();
      expect(es.bundle.watchModeTitles[mode], mode).toBeTruthy();
    }
    for (const status of BUNDLE_DISPLAY_STATUSES) {
      expect(es.bundle.displayStatus[status], status).toBeTruthy();
    }
    expect(es.methodology.anchors(2, 3)).toBe("2 de 3");
    expect(es.bundlesIndex.groupCount(1)).toBe("1 paquete");
    expect(es.bundlesIndex.groupCount(3)).toBe("3 paquetes");
  });
});

describe("bundle inclusions", () => {
  it("agree in number and never call free months a missing price", () => {
    expect(es.bundle.inclusion.free_months!("1")).toBe("1 mes gratis");
    expect(es.bundle.inclusion.free_months!("3")).toBe("3 meses gratis");
    expect(es.bundle.freeMonthsNote).not.toBe(es.bundle.optionalNoPrice);
  });
});

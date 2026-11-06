import { describe, expect, it } from "vitest";
import { en } from "./en";
import { es } from "./es";
import { pt } from "./pt";

// The methodology states the cadence of schedule.yaml (meta.schedule), in every language.
describe("methodology cadence", () => {
  it.each([
    [1, "Every day", "Cada día", "Todos os dias"],
    [2, "Every 2 days", "Cada 2 días", "A cada 2 dias"],
    [7, "Every week", "Cada semana", "Toda semana"],
    [14, "Every 2 weeks", "Cada 2 semanas", "A cada 2 semanas"],
  ])("%i days", (days, enText, esText, ptText) => {
    expect(en.methodology.cadence(days)).toBe(enText);
    expect(es.methodology.cadence(days)).toBe(esText);
    expect(pt.methodology.cadence(days)).toBe(ptText);
  });

  it("opens the collection and research sections", () => {
    for (const m of [en.methodology, es.methodology, pt.methodology]) {
      expect(m.collect(m.cadence(2))[0]?.text.startsWith(m.cadence(2))).toBe(true);
      expect(m.research(m.cadence(14))[0]?.text.startsWith(m.cadence(14))).toBe(true);
    }
  });

  it("states the bundle verification window", () => {
    for (const msg of [en, es, pt]) {
      expect(msg.bundle.displayStatusTitles(22).verified).toContain("22");
      expect(msg.methodology.bundles("2 %", 22)[1]?.text).toContain("22");
    }
  });
});

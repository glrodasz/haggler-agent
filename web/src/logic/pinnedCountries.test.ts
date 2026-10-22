import { describe, expect, it } from "vitest";
import { pinnedCountries } from "./pinnedCountries";

const table = new Set(["AR", "CO", "SE"]);

describe("pinnedCountries", () => {
  it("pins the located country, then a different saved residence", () => {
    expect(pinnedCountries("SE", "CO", table)).toEqual([
      { cc: "SE", tag: "yours" },
      { cc: "CO", tag: "comparison" },
    ]);
  });

  it("shows one row when both are the same country", () => {
    expect(pinnedCountries("SE", "SE", table)).toEqual([{ cc: "SE", tag: "yours" }]);
  });

  it("keeps the saved residence when the visitor could not be located", () => {
    expect(pinnedCountries(null, "AR", table)).toEqual([{ cc: "AR", tag: "comparison" }]);
  });

  it("skips countries the table does not list", () => {
    expect(pinnedCountries("JP", "XX", table)).toEqual([]);
    expect(pinnedCountries("JP", "CO", table)).toEqual([{ cc: "CO", tag: "comparison" }]);
  });
});

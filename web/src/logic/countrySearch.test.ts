import { describe, expect, it } from "vitest";
import { foldText, searchCountries } from "./countrySearch";

const countries = [
  { cc: "AR", name: "Argentina" },
  { cc: "MX", name: "México" },
  { cc: "US", name: "Estados Unidos" },
  { cc: "GB", name: "Reino Unido" },
  { cc: "AE", name: "Emiratos Árabes Unidos" },
  { cc: "SE", name: "Suecia" },
];

describe("foldText", () => {
  it("drops case and accents", () => {
    expect(foldText("  Émiratos Árabes ")).toBe("emiratos arabes");
  });
});

describe("searchCountries", () => {
  it("keeps every country in order for an empty query", () => {
    expect(searchCountries(countries, " ").map((c) => c.cc)).toEqual(["AR", "MX", "US", "GB", "AE", "SE"]);
  });

  it("matches without accents in either direction", () => {
    expect(searchCountries(countries, "mexico").map((c) => c.cc)).toEqual(["MX"]);
    expect(searchCountries(countries, "ÁRABES").map((c) => c.cc)).toEqual(["AE"]);
  });

  it("ranks name prefixes, then codes, then word starts, then substrings", () => {
    expect(searchCountries(countries, "uni").map((c) => c.cc)).toEqual(["US", "GB", "AE"]);
    expect(searchCountries(countries, "e").map((c) => c.cc)).toEqual(["US", "AE", "AR", "MX", "GB", "SE"]);
    expect(searchCountries(countries, "se").map((c) => c.cc)).toEqual(["SE"]);
    expect(searchCountries(countries, "us").map((c) => c.cc)).toEqual(["US"]);
  });

  it("returns nothing when no country matches", () => {
    expect(searchCountries(countries, "zz")).toEqual([]);
  });
});

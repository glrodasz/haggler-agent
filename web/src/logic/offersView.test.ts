import { describe, expect, it } from "vitest";
import {
  type OfferRowFacts,
  matchesOfferFilters,
  parseOffersQuery,
  sanitizeOfferFilters,
  sortOffers,
  toOffersQuery,
} from "./offersView";

const row = (over: Partial<OfferRowFacts>): OfferRowFacts => ({
  service: "spotify",
  country: "SE",
  plan: "individual",
  channel: "web",
  serviceName: "Spotify",
  countryName: "Sweden",
  planName: "Individual",
  planRank: 1,
  channelName: "Web",
  eur: 10,
  rank: 0,
  updated: "2026-10-01",
  index: 0,
  ...over,
});

const collator = new Intl.Collator("en", { sensitivity: "base", numeric: true });

describe("offers query", () => {
  it("round-trips filters, sort and page with English keys", () => {
    const state = parseOffersQuery("?country=co&plan=family&sort=price&order=desc&page=3", "price");
    expect(state).toEqual({
      service: "",
      country: "CO",
      plan: "family",
      channel: "",
      sort: "price",
      order: "desc",
      page: 3,
    });
    expect(toOffersQuery(state, "price")).toBe("?country=co&plan=family&sort=price&order=desc&page=3");
  });

  it("leaves the default sort out of the URL", () => {
    const state = parseOffersQuery("", "price");
    expect(state.sort).toBe("price");
    expect(state.order).toBe("asc");
    expect(toOffersQuery(state, "price")).toBe("");
  });

  it("ignores unknown sort keys and sorts dates newest first by default", () => {
    expect(parseOffersQuery("?sort=nope", "service").sort).toBe("service");
    expect(parseOffersQuery("?sort=updated", "price").order).toBe("desc");
  });

  it("drops filter values the table has no option for", () => {
    const state = parseOffersQuery("?country=xx&channel=web", "price");
    const clean = sanitizeOfferFilters(state, { country: new Set(["SE"]), channel: new Set(["web"]) });
    expect(clean.country).toBe("");
    expect(clean.channel).toBe("web");
  });
});

describe("offer rows", () => {
  it("filters on every field that has a value", () => {
    const filters = { service: "", country: "SE", plan: "", channel: "ios_iap" };
    expect(matchesOfferFilters(row({}), filters)).toBe(false);
    expect(matchesOfferFilters(row({ channel: "ios_iap" }), filters)).toBe(true);
  });

  it("sorts by price both ways, with unconverted prices last", () => {
    const rows = [row({ eur: 5, index: 0 }), row({ eur: null, index: 1 }), row({ eur: 2, index: 2 })];
    expect(sortOffers(rows, "price", "asc", collator).map((r) => r.index)).toEqual([2, 0, 1]);
    expect(sortOffers(rows, "price", "desc", collator).map((r) => r.index)).toEqual([0, 2, 1]);
  });

  it("sorts names in the page's language and keeps the page order on ties", () => {
    const rows = [
      row({ countryName: "Österreich", index: 0 }),
      row({ countryName: "Argentina", index: 1 }),
      row({ countryName: "argentina", index: 2 }),
    ];
    expect(sortOffers(rows, "country", "asc", collator).map((r) => r.index)).toEqual([1, 2, 0]);
  });

  it("sorts plans by rank, then by name", () => {
    const rows = [
      row({ planName: "Family", planRank: 3, index: 0 }),
      row({ planName: "Lite", planRank: 0, index: 1 }),
      row({ planName: "Individual", planRank: 1, index: 2 }),
    ];
    expect(sortOffers(rows, "plan", "asc", collator).map((r) => r.index)).toEqual([1, 2, 0]);
  });

  it("sorts by risk, cheapest first within a level", () => {
    const rows = [
      row({ rank: 2, eur: 1, index: 0 }),
      row({ rank: 0, eur: 9, index: 1 }),
      row({ rank: 0, eur: 3, index: 2 }),
    ];
    expect(sortOffers(rows, "risk", "asc", collator).map((r) => r.index)).toEqual([2, 1, 0]);
  });
});

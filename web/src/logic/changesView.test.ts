import { describe, expect, it } from "vitest";
import {
  kindGroup,
  matchesFilters,
  pageItems,
  pageSlice,
  parseChangesQuery,
  sanitizeFilters,
  toChangesQuery,
} from "./changesView";

const empty = { service: "", country: "", kind: "", channel: "" };

describe("kindGroup", () => {
  it("maps synonyms and splits price changes by direction", () => {
    expect(kindGroup("added", null)).toBe("new");
    expect(kindGroup("dropped", null)).toBe("removed");
    expect(kindGroup("changed", 79.03)).toBe("up");
    expect(kindGroup("changed", -2.71)).toBe("down");
    expect(kindGroup("changed", null)).toBe("other");
    expect(kindGroup("status", null)).toBe("other");
  });
});

describe("query string", () => {
  it("round-trips filters and page", () => {
    const state = { service: "netflix", country: "SE", kind: "up", channel: "web", page: 3 };
    const query = toChangesQuery(state);
    expect(query).toBe("?service=netflix&country=se&type=up&channel=web&page=3");
    expect(parseChangesQuery(query)).toEqual(state);
  });

  it("omits defaults and ignores invalid pages", () => {
    expect(toChangesQuery({ ...empty, page: 1 })).toBe("");
    expect(parseChangesQuery("?page=abc").page).toBe(1);
    expect(parseChangesQuery("?page=-4").page).toBe(1);
    expect(parseChangesQuery("").country).toBe("");
  });
});

describe("filters", () => {
  const row = { service: "netflix", country: "SE", kind: "up", channel: "web" };

  it("treats empty filters as wildcards", () => {
    expect(matchesFilters(row, empty)).toBe(true);
    expect(matchesFilters(row, { ...empty, country: "SE", kind: "up" })).toBe(true);
    expect(matchesFilters(row, { ...empty, channel: "ios_iap" })).toBe(false);
  });

  it("drops values with no option", () => {
    const options = {
      service: new Set(["netflix"]),
      country: new Set(["SE"]),
      kind: new Set(["new"]),
      channel: new Set(["web"]),
    };
    const state = { service: "netflix", country: "XX", kind: "up", channel: "web", page: 2 };
    expect(sanitizeFilters(state, options)).toEqual({ ...state, country: "", kind: "" });
  });
});

describe("pagination", () => {
  it("clamps the page and slices 50 rows", () => {
    expect(pageSlice(120, 1)).toEqual({ page: 1, pages: 3, start: 0, end: 50 });
    expect(pageSlice(120, 3)).toEqual({ page: 3, pages: 3, start: 100, end: 120 });
    expect(pageSlice(120, 9).page).toBe(3);
    expect(pageSlice(0, 4)).toEqual({ page: 1, pages: 1, start: 0, end: 0 });
  });

  it("shows ends, neighbours and gaps", () => {
    expect(pageItems(1, 1)).toEqual([1]);
    expect(pageItems(1, 5)).toEqual([1, 2, null, 5]);
    expect(pageItems(6, 12)).toEqual([1, null, 5, 6, 7, null, 12]);
    expect(pageItems(3, 5)).toEqual([1, 2, 3, 4, 5]);
  });
});

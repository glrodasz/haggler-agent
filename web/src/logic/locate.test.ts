import { describe, expect, it } from "vitest";
import { countryOfLanguage, guessFromBrowser, lookupIp } from "./locate";

describe("guessFromBrowser", () => {
  it("prefers the time zone", () => {
    expect(guessFromBrowser({ timeZone: "Europe/Stockholm", languages: ["es-CO"] })).toEqual({
      cc: "SE",
      source: "timezone",
    });
  });

  it("falls back to the first language with a region", () => {
    expect(guessFromBrowser({ timeZone: "Etc/UTC", languages: ["es", "es-419", "es-CO"] })).toEqual(
      { cc: "CO", source: "language" },
    );
  });

  it("gives up without hints", () => {
    expect(guessFromBrowser({ timeZone: undefined, languages: ["en"] })).toBeNull();
  });

  it("reads the region subtag only", () => {
    expect(countryOfLanguage("zh-Hant-TW")).toBe("TW");
    expect(countryOfLanguage("pt_br")).toBe("BR");
    expect(countryOfLanguage("de")).toBeNull();
  });
});

describe("lookupIp", () => {
  const respond = (body: unknown, ok = true) =>
    (async () => ({ ok, json: async () => body })) as unknown as typeof fetch;

  it("returns the country of the IP", async () => {
    expect(await lookupIp(respond({ ip: "1.2.3.4", country: "co" }))).toEqual({
      cc: "CO",
      source: "ip",
    });
  });

  it("returns null on errors and odd answers", async () => {
    expect(await lookupIp(respond({ country: "Sweden" }))).toBeNull();
    expect(await lookupIp(respond({}, false))).toBeNull();
    const failing = (async () => {
      throw new TypeError("network");
    }) as unknown as typeof fetch;
    expect(await lookupIp(failing)).toBeNull();
  });
});

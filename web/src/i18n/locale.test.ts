import { describe, expect, it } from "vitest";
import { en } from "./en";
import { es } from "./es";
import { getMessages } from "./index";
import {
  alternateLinks,
  LOCALES,
  localizePath,
  negotiateLocale,
  pageLocale,
  parseAcceptLanguage,
  readCookie,
  resolveLocaleRedirect,
  switchLocalePath,
} from "./locale";
import { pt } from "./pt";

describe("locale redirect truth table", () => {
  const cases: [string, string | null, string[], string | null][] = [
    ["/", null, ["es-ES"], "/es/"],
    ["/about", null, parseAcceptLanguage("sv, es;q=0.8"), "/es/about"],
    ["/", null, ["en-US", "es"], null],
    ["/", "es", ["en-US"], "/es/"],
    ["/", "en", ["es-ES"], null],
    ["/es/about", "en", ["en"], null],
    ["/apis", null, ["es"], "/es/apis"],
    ["/", "garbage", ["es"], "/es/"],
    ["/services/netflix/", null, ["pt-BR", "en"], "/pt/services/netflix/"],
    ["/pt/", "es", ["es"], null],
    ["/", null, ["sv-SE", "de"], null],
  ];
  it.each(cases)("%s, saved %s, %j -> %s", (pathname, savedLocale, languages, expected) => {
    expect(resolveLocaleRedirect({ pathname, savedLocale, languages })).toBe(expected);
  });
});

describe("locale paths", () => {
  it("round-trips between locales", () => {
    expect(localizePath("/es/", "en")).toBe("/");
    expect(localizePath("/", "pt")).toBe("/pt/");
    expect(switchLocalePath("/es/about", "en")).toBe("/about");
    expect(switchLocalePath("/pt/services/", "es")).toBe("/es/services/");
    expect(pageLocale("/pt/countries/co/")).toBe("pt");
    expect(pageLocale("/en/about")).toBe("en");
    expect(pageLocale("/espresso/")).toBe("en");
  });

  it("honors alternates and falls back to the locale home", () => {
    expect(switchLocalePath("/posts/a", "es", { en: "/posts/a", es: "/es/posts/b" })).toBe(
      "/es/posts/b",
    );
    expect(switchLocalePath("/posts/a", "pt", { en: "/posts/a" })).toBe("/pt/");
  });

  it("lists every version plus x-default", () => {
    const links = alternateLinks("/services/", "https://example.com");
    expect(links.map((l) => l.hreflang)).toEqual(["en", "es", "pt", "x-default"]);
    expect(links.at(-1)?.href).toBe("https://example.com/services/");
  });

  it("negotiates in the user's priority order", () => {
    expect(parseAcceptLanguage("sv, es;q=0.8, en;q=0.9")).toEqual(["sv", "en", "es"]);
    expect(negotiateLocale(["sv", "pt-PT", "es"])).toBe("pt");
    expect(readCookie("a=1; locale=es", "locale")).toBe("es");
  });
});

/** Every string of a dictionary, templates filled with placeholders. */
function texts(value: unknown): string[] {
  if (typeof value === "function") {
    return [String((value as (...args: unknown[]) => unknown)("X", "Y", "Z"))];
  }
  if (Array.isArray(value)) return value.flatMap(texts);
  if (value && typeof value === "object") {
    // Build errors (developer-facing) take a list, not text.
    return Object.entries(value).flatMap(([k, v]) => (k === "errors" ? [] : texts(v)));
  }
  return typeof value === "string" ? [value] : [];
}

/** The key paths of a dictionary, to compare shapes at runtime too (records included). */
function keys(value: unknown, path = ""): string[] {
  if (typeof value === "function" || typeof value === "string") return [path];
  if (Array.isArray(value)) return [`${path}[${value.length}]`, ...value.flatMap((v, i) => keys(v, `${path}[${i}]`))];
  if (value && typeof value === "object") {
    return Object.entries(value).flatMap(([k, v]) => keys(v, `${path}.${k}`));
  }
  return [path];
}

describe("dictionaries", () => {
  // planNames and profileNames are lookups: Spanish takes them from the data.
  const comparable = (m: object) =>
    keys(m).filter((k) => !k.startsWith(".planNames") && !k.startsWith(".profileNames"));

  it.each([
    ["es", es],
    ["pt", pt],
  ])("%s has exactly the English keys", (_name, dict) => {
    expect(comparable(dict)).toEqual(comparable(en));
  });

  it("names each language in itself", () => {
    expect(LOCALES.map((l) => getMessages(l).language.switchTo)).toEqual([
      "Switch to English",
      "Cambiar a español",
      "Mudar para português",
    ]);
  });

  it.each([
    ["en", en],
    ["pt", pt],
  ])("%s never hard-codes the residence country", (_name, dict) => {
    expect(texts(dict).filter((t) => /swed|suéc|suec/i.test(t))).toEqual([]);
  });

  // Same rule as es.test.ts: a VPN or false data is only ever mentioned to rule it out.
  it.each([
    ["en", en, /\bvpn\b|proxy|another address|fake address|false information|forg/i, /\bnever\b|\bnot\b/i],
    ["pt", pt, /\bvpn\b|proxy|outro endereço|endereço falso|dados falsos|falsific/i, /\bnunca\b|\bnão\b/i],
  ] as const)("%s mentions workarounds only to rule them out", (_name, dict, forbidden, negation) => {
    const sentences = texts(dict).flatMap((t) => t.split(/(?<=[.;:])\s+/));
    const mentions = sentences.filter((t) => forbidden.test(t));
    expect(mentions.filter((t) => !negation.test(t) && !/requires giving|exige declarar/i.test(t))).toEqual([]);
  });
});

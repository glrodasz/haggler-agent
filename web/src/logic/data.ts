/**
 * Loads web/src/data/*.json at build time. A glob (instead of static imports) keeps
 * `astro check` and vitest working when the generated data is absent, and lets us throw one
 * clear message listing every missing file.
 */

import { DEFAULT_LOCALE, type Locale } from "../i18n/locale";
import { assertSiteData, buildIndex, type SiteIndex } from "./site";
import type { SiteData } from "./types";

const modules = import.meta.glob<unknown>("../data/*.json", { eager: true, import: "default" });

let data: SiteData | undefined;
const cached = new Map<Locale, SiteIndex>();

/** The site data with names, labels and formatting in `locale`. */
export function getSite(locale: Locale = DEFAULT_LOCALE): SiteIndex {
  let site = cached.get(locale);
  if (!site) {
    if (!data) {
      const raw: Record<string, unknown> = {};
      for (const [path, value] of Object.entries(modules)) {
        const name = path.replace(/^.*\//, "").replace(/\.json$/, "");
        raw[name] = value;
      }
      data = assertSiteData(raw);
    }
    site = buildIndex(data, locale);
    cached.set(locale, site);
  }
  return site;
}

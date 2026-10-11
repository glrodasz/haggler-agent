/**
 * Loads web/src/data/*.json at build time. A glob (instead of static imports) keeps
 * `astro check` and vitest working when the generated data is absent, and lets us throw one
 * clear message listing every missing file.
 */

import { assertSiteData, buildIndex, type SiteIndex } from "./site";

const modules = import.meta.glob<unknown>("../data/*.json", { eager: true, import: "default" });

let cached: SiteIndex | undefined;

export function getSite(): SiteIndex {
  if (!cached) {
    const raw: Record<string, unknown> = {};
    for (const [path, value] of Object.entries(modules)) {
      const name = path.replace(/^.*\//, "").replace(/\.json$/, "");
      raw[name] = value;
    }
    cached = buildIndex(assertSiteData(raw));
  }
  return cached;
}

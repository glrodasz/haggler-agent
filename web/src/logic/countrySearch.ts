/**
 * Country search for the comparator's autocomplete fields: matches the Spanish name ignoring
 * case and accents ("mexico" finds México), and ranks name prefixes first, then word starts,
 * then any substring. An exact ISO code ("us", "se") also matches.
 */

export interface SearchableCountry {
  cc: string;
  name: string;
}

/** Lowercase without diacritics, so "México" and "mexico" compare equal. */
export function foldText(text: string): string {
  return text.normalize("NFD").replace(/\p{M}/gu, "").toLowerCase().trim();
}

function rank(country: SearchableCountry, query: string): number {
  const name = foldText(country.name);
  if (name.startsWith(query)) return 0;
  if (country.cc.toLowerCase() === query) return 1;
  if (name.split(/[\s\-()]+/).some((word) => word.startsWith(query))) return 2;
  if (name.includes(query)) return 3;
  return -1;
}

/** Countries matching `query`, best first; an empty query keeps the original order. */
export function searchCountries<C extends SearchableCountry>(countries: readonly C[], query: string): C[] {
  const q = foldText(query);
  if (!q) return [...countries];
  return countries
    .map((country, index) => ({ country, index, score: rank(country, q) }))
    .filter((hit) => hit.score >= 0)
    .sort((a, b) => a.score - b.score || a.index - b.index)
    .map((hit) => hit.country);
}

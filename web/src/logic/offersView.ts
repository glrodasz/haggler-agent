/**
 * Filters, sorting and pagination of an OfferTable ("Prices by country" on a service page,
 * "Prices" on a country page). The page renders every row; the browser script narrows, orders
 * and pages them with these pure helpers and keeps the state in the query string, with English
 * keys like /changes, so a link works in every language.
 */

export const OFFERS_PAGE_SIZE = 25;

/** profilePatch fires it after it redraws the risk cells, so a risk sort follows the profile. */
export const OFFERS_PATCHED_EVENT = "haggler:offers-patched";

/** Below this many rows the table shows no filters (sorting stays). */
export const OFFERS_FILTER_MIN_ROWS = 12;

export type OfferFilterField = "service" | "country" | "plan" | "channel";

export const OFFER_FILTER_FIELDS: readonly OfferFilterField[] = [
  "service",
  "country",
  "plan",
  "channel",
];

export type OfferSortKey =
  "service" | "country" | "plan" | "price" | "channel" | "risk" | "updated";

export const OFFER_SORT_KEYS: readonly OfferSortKey[] = [
  "service",
  "country",
  "plan",
  "price",
  "channel",
  "risk",
  "updated",
];

export type SortOrder = "asc" | "desc";

export type OfferFilters = Record<OfferFilterField, string>;

export interface OffersState extends OfferFilters {
  sort: OfferSortKey;
  order: SortOrder;
  page: number;
}

/** The first click on a column sorts it this way: newest first for dates, ascending otherwise. */
export function firstOrder(key: OfferSortKey): SortOrder {
  return key === "updated" ? "desc" : "asc";
}

export function emptyFilters(): OfferFilters {
  return { service: "", country: "", plan: "", channel: "" };
}

export function parseOffersQuery(
  search: string,
  defaultSort: OfferSortKey,
): OffersState {
  const params = new URLSearchParams(search);
  const sortParam = params.get("sort") ?? "";
  const sort = (OFFER_SORT_KEYS as readonly string[]).includes(sortParam)
    ? (sortParam as OfferSortKey)
    : defaultSort;
  const orderParam = params.get("order");
  const order: SortOrder =
    orderParam === "asc" || orderParam === "desc"
      ? orderParam
      : firstOrder(sort);
  const page = Number.parseInt(params.get("page") ?? "", 10);
  const filters = emptyFilters();
  for (const field of OFFER_FILTER_FIELDS)
    filters[field] = params.get(field) ?? "";
  filters.country = filters.country.toUpperCase();
  return {
    ...filters,
    sort,
    order,
    page: Number.isFinite(page) && page > 1 ? page : 1,
  };
}

/**
 * "?country=co&sort=price&order=desc&page=2", or "" for the default view. The default sort and
 * its first order are left out, so the plain page keeps a clean URL.
 */
export function toOffersQuery(
  state: OffersState,
  defaultSort: OfferSortKey,
): string {
  const params = new URLSearchParams();
  for (const field of OFFER_FILTER_FIELDS) {
    if (state[field])
      params.set(
        field,
        field === "country" ? state[field].toLowerCase() : state[field],
      );
  }
  if (state.sort !== defaultSort || state.order !== firstOrder(state.sort)) {
    params.set("sort", state.sort);
    params.set("order", state.order);
  }
  if (state.page > 1) params.set("page", String(state.page));
  const query = params.toString();
  return query ? `?${query}` : "";
}

/** Drops filter values the table has no option for (stale or hand-edited links). */
export function sanitizeOfferFilters<T extends OfferFilters>(
  state: T,
  options: Partial<Record<OfferFilterField, ReadonlySet<string>>>,
): T {
  const clean = { ...state };
  for (const field of OFFER_FILTER_FIELDS) {
    const allowed = options[field];
    if (clean[field] && !allowed?.has(clean[field])) clean[field] = "";
  }
  return clean;
}

/** What a row is filtered and sorted by, as the page stores it in data-* attributes. */
export interface OfferRowFacts extends OfferFilters {
  /** Display names, in the page's language. */
  serviceName: string;
  countryName: string;
  planName: string;
  planRank: number;
  channelName: string;
  /** Monthly price in EUR; null when it could not be converted (sorts last either way). */
  eur: number | null;
  rank: number;
  updated: string;
  /** Position in the page's own order, the tie-breaker. */
  index: number;
}

export function matchesOfferFilters(
  row: OfferFilters,
  filters: OfferFilters,
): boolean {
  return OFFER_FILTER_FIELDS.every(
    (field) => !filters[field] || row[field] === filters[field],
  );
}

function compareNullable(
  a: number | null,
  b: number | null,
  sign: number,
): number | null {
  if (a === null && b === null) return null;
  if (a === null) return 1;
  if (b === null) return -1;
  return a === b ? null : (a - b) * sign;
}

/** Rows in the requested order; ties keep the page's own order. */
export function sortOffers<T extends OfferRowFacts>(
  rows: readonly T[],
  key: OfferSortKey,
  order: SortOrder,
  collator: Intl.Collator,
): T[] {
  const sign = order === "asc" ? 1 : -1;
  const text = (a: string, b: string) => collator.compare(a, b) * sign;
  const by = (a: T, b: T): number => {
    switch (key) {
      case "service":
        return text(a.serviceName, b.serviceName);
      case "country":
        return text(a.countryName, b.countryName);
      case "plan":
        return (a.planRank - b.planRank) * sign || text(a.planName, b.planName);
      case "price":
        return compareNullable(a.eur, b.eur, sign) ?? 0;
      case "channel":
        return text(a.channelName, b.channelName);
      case "risk":
        return (
          (a.rank - b.rank) * sign || (compareNullable(a.eur, b.eur, 1) ?? 0)
        );
      case "updated":
        return a.updated === b.updated
          ? 0
          : (a.updated < b.updated ? -1 : 1) * sign;
    }
  };
  return [...rows].sort((a, b) => by(a, b) || a.index - b.index);
}

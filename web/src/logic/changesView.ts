/**
 * /changes filters and pagination. The page renders every row; the browser script narrows them
 * with these pure helpers and keeps the state in the query string so a filtered page can be shared.
 */

export const CHANGES_PAGE_SIZE = 50;

/** What the "Tipo" filter offers: price changes split by direction, so "Subida" is one click. */
export type ChangeKindGroup = "new" | "removed" | "up" | "down" | "other";

export const KIND_GROUP_ORDER: ChangeKindGroup[] = ["new", "up", "down", "removed", "other"];

const KIND_GROUPS: Record<string, ChangeKindGroup> = {
  new: "new",
  added: "new",
  removed: "removed",
  dropped: "removed",
  up: "up",
  increase: "up",
  down: "down",
  decrease: "down",
};

export function kindGroup(kind: string, pct: number | null): ChangeKindGroup {
  const group = KIND_GROUPS[kind];
  if (group) return group;
  if ((kind === "changed" || kind === "price_change") && pct !== null && pct !== 0) {
    return pct > 0 ? "up" : "down";
  }
  return "other";
}

export interface ChangesFilters {
  service: string;
  country: string;
  kind: string;
  channel: string;
}

export interface ChangesState extends ChangesFilters {
  page: number;
}

/** Query-string keys, in English like the site's paths, so a link works in every language. */
export const QUERY_KEYS: Record<keyof ChangesState, string> = {
  service: "service",
  country: "country",
  kind: "type",
  channel: "channel",
  page: "page",
};

const FILTER_FIELDS = ["service", "country", "kind", "channel"] as const;

export function parseChangesQuery(search: string): ChangesState {
  const params = new URLSearchParams(search);
  const page = Number.parseInt(params.get(QUERY_KEYS.page) ?? "", 10);
  return {
    service: params.get(QUERY_KEYS.service) ?? "",
    country: (params.get(QUERY_KEYS.country) ?? "").toUpperCase(),
    kind: params.get(QUERY_KEYS.kind) ?? "",
    channel: params.get(QUERY_KEYS.channel) ?? "",
    page: Number.isFinite(page) && page > 1 ? page : 1,
  };
}

/** "?service=netflix&page=2", or "" for the unfiltered first page. Empty values are dropped. */
export function toChangesQuery(state: ChangesState): string {
  const params = new URLSearchParams();
  for (const field of FILTER_FIELDS) {
    if (state[field]) params.set(QUERY_KEYS[field], field === "country" ? state[field].toLowerCase() : state[field]);
  }
  if (state.page > 1) params.set(QUERY_KEYS.page, String(state.page));
  const query = params.toString();
  return query ? `?${query}` : "";
}

/** A row's filterable facets, as the page stores them in data-* attributes. */
export type ChangeFacets = ChangesFilters;

export function matchesFilters(row: ChangeFacets, filters: ChangesFilters): boolean {
  return FILTER_FIELDS.every((field) => !filters[field] || row[field] === filters[field]);
}

/** Drops filter values the page has no option for (stale or hand-edited links). */
export function sanitizeFilters<T extends ChangesFilters>(
  state: T,
  options: Record<keyof ChangesFilters, ReadonlySet<string>>,
): T {
  const clean = { ...state };
  for (const field of FILTER_FIELDS) {
    if (clean[field] && !options[field].has(clean[field])) clean[field] = "";
  }
  return clean;
}

export interface PageSlice {
  /** 1-based, clamped to [1, pages]. */
  page: number;
  pages: number;
  /** Index range [start, end) into the filtered rows. */
  start: number;
  end: number;
}

export function pageSlice(total: number, page: number, size = CHANGES_PAGE_SIZE): PageSlice {
  const pages = Math.max(1, Math.ceil(total / size));
  const current = Math.min(Math.max(1, Math.floor(page) || 1), pages);
  const start = (current - 1) * size;
  return { page: current, pages, start, end: Math.min(total, start + size) };
}

/**
 * Page numbers to show in the pager: the first, the last and the current one with its
 * neighbours; null marks a gap ("…"). Example for page 6 of 12: 1 … 5 6 7 … 12.
 */
export function pageItems(page: number, pages: number): (number | null)[] {
  const wanted = new Set([1, pages, page - 1, page, page + 1]);
  const items: (number | null)[] = [];
  let previous = 0;
  for (let n = 1; n <= pages; n++) {
    if (!wanted.has(n)) continue;
    if (n - previous === 2) items.push(n - 1);
    else if (n - previous > 2) items.push(null);
    items.push(n);
    previous = n;
  }
  return items;
}

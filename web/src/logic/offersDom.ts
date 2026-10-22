/**
 * Browser side of OfferTable: filters, sortable headings and pager over the rows the page
 * rendered, with the state in the query string (logic/offersView.ts holds the pure parts).
 */

import { isLocale } from "../i18n/locale";
import { pageSlice } from "./changesView";
import { formatFor } from "./format";
import {
  OFFERS_PAGE_SIZE,
  type OfferFilterField,
  type OfferFilters,
  type OfferRowFacts,
  type OfferSortKey,
  type OffersState,
  OFFER_SORT_KEYS,
  OFFERS_PATCHED_EVENT,
  emptyFilters,
  firstOrder,
  matchesOfferFilters,
  parseOffersQuery,
  sanitizeOfferFilters,
  sortOffers,
  toOffersQuery,
} from "./offersView";
import { renderPager } from "./pagerDom";

interface Row extends OfferRowFacts {
  tr: HTMLTableRowElement;
}

function factsOf(tr: HTMLTableRowElement, index: number): Row {
  const d = tr.dataset;
  const eur = d.offerEur ? Number(d.offerEur) : Number.NaN;
  return {
    tr,
    index,
    service: d.service ?? "",
    country: d.country ?? "",
    plan: d.plan ?? "",
    channel: d.channel ?? "",
    serviceName: d.serviceName ?? "",
    countryName: d.countryName ?? "",
    planName: d.planName ?? "",
    planRank: Number(d.planRank) || 0,
    channelName: d.channelName ?? "",
    eur: Number.isFinite(eur) ? eur : null,
    rank: Number(d.rank) || 0,
    updated: d.updated ?? "",
  };
}

export function setupOfferTable(root: HTMLElement): void {
  const form = root.querySelector<HTMLFormElement>("[data-offers-filters]");
  const selects = form
    ? [...form.querySelectorAll<HTMLSelectElement>("select[data-field]")]
    : [];
  const clear = root.querySelector<HTMLElement>("[data-offers-clear]");
  const count = root.querySelector<HTMLElement>("[data-offers-count]")!;
  const empty = root.querySelector<HTMLElement>("[data-offers-empty]")!;
  const pager = root.querySelector<HTMLElement>("[data-offers-pager]")!;
  const table = root.querySelector<HTMLElement>(".table-wrap")!;
  const body = root.querySelector<HTMLElement>("[data-offers-rows]")!;
  const heads = [
    ...root.querySelectorAll<HTMLTableCellElement>("thead th[data-sort]"),
  ];
  let rows = [...body.querySelectorAll<HTMLTableRowElement>(":scope > tr")].map(
    factsOf,
  );

  const text = (key: string) => root.dataset[key] ?? "";
  const locale = isLocale(root.dataset.locale) ? root.dataset.locale : "en";
  const fmt = formatFor(locale);
  const integer = (n: number) => fmt.formatNumber(n, 0);
  const collator = new Intl.Collator(locale, {
    sensitivity: "base",
    numeric: true,
  });
  const defaultSort = (OFFER_SORT_KEYS as readonly string[]).includes(
    root.dataset.defaultSort ?? "",
  )
    ? (root.dataset.defaultSort as OfferSortKey)
    : "price";
  const options: Partial<Record<OfferFilterField, Set<string>>> =
    Object.fromEntries(
      selects.map((s) => [
        s.dataset.field,
        new Set([...s.options].map((o) => o.value).filter(Boolean)),
      ]),
    );

  const read = (): OffersState =>
    sanitizeOfferFilters(
      parseOffersQuery(location.search, defaultSort),
      options,
    );
  let state = read();

  // Each sortable heading gets a button; aria-sort on the heading says which order is shown.
  const buttons = heads.map((th) => {
    const button = document.createElement("button");
    button.type = "button";
    button.className = "sort-button";
    button.dataset.sort = th.dataset.sort;
    const label = document.createElement("span");
    label.className = "sort-label";
    label.append(...th.childNodes);
    const icon = document.createElement("span");
    icon.className = "sort-icon";
    icon.setAttribute("aria-hidden", "true");
    button.append(label, icon);
    th.replaceChildren(button);
    th.classList.add("is-sortable");
    return button;
  });

  function render() {
    for (const s of selects)
      s.value = state[s.dataset.field as OfferFilterField];
    for (const th of heads) {
      const active = th.dataset.sort === state.sort;
      if (active)
        th.setAttribute(
          "aria-sort",
          state.order === "asc" ? "ascending" : "descending",
        );
      else th.removeAttribute("aria-sort");
      const icon = th.querySelector(".sort-icon");
      if (icon)
        icon.textContent = active ? (state.order === "asc" ? "↑" : "↓") : "↕";
    }

    const ordered = sortOffers(rows, state.sort, state.order, collator);
    body.append(...ordered.map((r) => r.tr));
    const matching = ordered.filter((r) => matchesOfferFilters(r, state));
    const slice = pageSlice(matching.length, state.page, OFFERS_PAGE_SIZE);
    state.page = slice.page;
    const visible = new Set(
      matching.slice(slice.start, slice.end).map((r) => r.tr),
    );
    for (const r of rows) r.tr.hidden = !visible.has(r.tr);

    const none = matching.length === 0;
    table.hidden = none;
    empty.hidden = !none;
    // The count only helps when the table is narrowed or split into pages.
    count.hidden =
      none || (slice.pages === 1 && matching.length === rows.length);
    count.textContent = text("showing")
      .replace("{from}", integer(slice.start + 1))
      .replace("{to}", integer(slice.end))
      .replace("{total}", integer(matching.length));

    renderPager(pager, slice, (page) => href({ ...state, page }), {
      previous: text("previous"),
      next: text("next"),
      pageLabel: (n) => text("pageLabel").replace("{n}", String(n)),
    });
    if (clear) clear.hidden = !selects.some((s) => s.value);
  }

  const href = (s: OffersState) =>
    location.pathname + toOffersQuery(s, defaultSort) + location.hash;

  function commit(push: boolean) {
    if (push) history.pushState(null, "", href(state));
    else history.replaceState(null, "", href(state));
  }

  function fromForm(): OfferFilters {
    const filters = emptyFilters();
    for (const s of selects)
      filters[s.dataset.field as OfferFilterField] = s.value;
    return filters;
  }

  form?.addEventListener("change", () => {
    state = { ...state, ...fromForm(), page: 1 };
    render();
    commit(false);
  });
  form?.addEventListener("reset", (event) => {
    event.preventDefault();
    state = { ...state, ...emptyFilters(), page: 1 };
    render();
    commit(false);
    selects[0]?.focus();
  });
  form?.addEventListener("submit", (event) => event.preventDefault());

  for (const button of buttons) {
    button.addEventListener("click", () => {
      const key = button.dataset.sort as OfferSortKey;
      const order =
        key === state.sort
          ? state.order === "asc"
            ? "desc"
            : "asc"
          : firstOrder(key);
      state = { ...state, sort: key, order, page: 1 };
      render();
      commit(false);
    });
  }

  pager.addEventListener("click", (event) => {
    const link = (event.target as Element).closest<HTMLAnchorElement>(
      "a[data-page]",
    );
    if (
      !link ||
      event.metaKey ||
      event.ctrlKey ||
      event.shiftKey ||
      event.button !== 0
    )
      return;
    event.preventDefault();
    state = { ...state, page: Number(link.dataset.page) };
    render();
    commit(true);
    // Back to the top of the table, where the new page starts.
    root.scrollIntoView({ block: "start" });
    count.focus({ preventScroll: true });
  });
  window.addEventListener("popstate", () => {
    state = read();
    render();
  });
  document.addEventListener(OFFERS_PATCHED_EVENT, () => {
    rows = rows.map((r) => factsOf(r.tr, r.index));
    render();
  });

  count.tabIndex = -1;
  if (form) form.hidden = false;
  render();
  // Normalise the URL (stale filters dropped, out-of-range page clamped) without a history entry.
  if (location.search) commit(false);
}

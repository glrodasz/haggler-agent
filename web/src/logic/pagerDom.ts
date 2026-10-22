/**
 * The pager under a paginated table (/changes, OfferTable): page links built in the browser.
 * Clicks are handled by the page script through each link's data-page.
 */

import { pageItems } from "./changesView";

export interface PagerText {
  previous: string;
  next: string;
  /** aria-label of a page number link. */
  pageLabel: (n: number) => string;
}

/** Fills `pager` with "← Previous 1 … 5 6 7 … 12 Next →" and hides it when there is one page. */
export function renderPager(
  pager: HTMLElement,
  slice: { page: number; pages: number },
  href: (page: number) => string,
  text: PagerText,
): void {
  function pageLink(page: number, label: string, current = false): HTMLElement {
    const link = document.createElement("a");
    link.href = href(page);
    link.textContent = label;
    link.className = "pager-link";
    link.dataset.page = String(page);
    if (current) link.setAttribute("aria-current", "page");
    return link;
  }

  function inactive(): HTMLElement {
    const span = document.createElement("span");
    span.className = "pager-link is-disabled";
    span.setAttribute("aria-disabled", "true");
    return span;
  }

  // "← Previous" / "Next →"; phones keep only the arrow so the pager fits one line.
  function step(
    page: number,
    enabled: boolean,
    label: string,
    arrow: string,
    before: boolean,
  ): HTMLElement {
    const el = enabled ? pageLink(page, "") : inactive();
    const icon = document.createElement("span");
    icon.setAttribute("aria-hidden", "true");
    icon.textContent = arrow;
    const word = document.createElement("span");
    word.className = "pager-word";
    word.textContent = label;
    el.append(...(before ? [icon, word] : [word, icon]));
    el.setAttribute("aria-label", label);
    return el;
  }

  pager.replaceChildren();
  pager.hidden = slice.pages <= 1;
  if (slice.pages <= 1) return;
  const list = document.createElement("ul");
  const item = (child: HTMLElement) => {
    const li = document.createElement("li");
    li.append(child);
    list.append(li);
  };
  item(step(slice.page - 1, slice.page > 1, text.previous, "←", true));
  for (const n of pageItems(slice.page, slice.pages)) {
    if (n === null) {
      const gap = document.createElement("span");
      gap.className = "pager-gap";
      gap.textContent = "…";
      item(gap);
      continue;
    }
    const link = pageLink(n, String(n), n === slice.page);
    link.setAttribute("aria-label", text.pageLabel(n));
    item(link);
  }
  item(step(slice.page + 1, slice.page < slice.pages, text.next, "→", false));
  pager.append(list);
}

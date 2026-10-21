/**
 * Redraws the server-rendered parts of a page that depend on the visitor's profile: the pages
 * are built for the site's default profile (what a visitor without JavaScript sees) and mark
 * those parts with data-* attributes; once the visitor's profile is known (profileClient.ts)
 * this fills them again for it. Parts that change shape (feasibility lists, bundle groups) are
 * Preact islands instead (islands/ProfileFeasibility.tsx, islands/BundleGroups.tsx).
 *
 *   [data-profile-residence]        text: the visitor's residence country
 *   [data-profile-note]             the "precios y riesgos para tu perfil" note
 *   [data-home-country=CC]          shown only when CC is the visitor's residence
 *   table[data-profile-services]    /servicios: rows tr[data-service][data-plan], cells
 *                                   [data-cell=home] and [data-cell=risk][data-level=k]
 *   table[data-profile-summary]     a service's cheapest per level: [data-service][data-plan],
 *                                   rows tr[data-level], cells [data-cell=offer|cost|saving]
 *   tr[data-offer-svc]              an OfferTable row: [data-cell=risk], [data-home-tag] and
 *                                   [data-cell=second] (monthly in the residence currency)
 *   [data-cell=home-head|saving-head|second-head]   headers naming the residence/currency
 */

import { es } from "../i18n/es";
import type { PricedOffer } from "./compare";
import { flagEmoji, formatMoney, formatSignedMoney } from "./format";
import { routes } from "./paths";
import { encodeProfile } from "./profile";
import type { ProfileState } from "./profileClient";
import { RISK_LEVELS, type RiskLevel } from "./types";

type Child = Node | string;

function riskClass(rank: number): string {
  return rank >= 0 && rank <= 4 ? `risk-${rank}` : "risk-unknown";
}

function el(tag: string, attrs: Record<string, string>, ...children: Child[]): HTMLElement {
  const node = document.createElement(tag);
  for (const [k, v] of Object.entries(attrs)) node.setAttribute(k, v);
  node.append(...children);
  return node;
}

function fill(node: Element, ...children: Child[]): void {
  node.replaceChildren(...children);
}

export function riskBadge(
  { kit }: ProfileState,
  label: string,
  rank: number,
): HTMLElement {
  const info = kit.labels[label];
  const name = info?.name ?? label;
  return el("span", { class: `badge ${riskClass(info?.rank ?? rank)}`, title: info?.description ?? "" }, name);
}

export function euBadge({ view }: ProfileState): HTMLElement {
  return el("span", { class: "badge badge-eu", title: es.euBadgeTitle(view.residenceName) }, es.euBadge);
}

export function countryLabel({ kit }: ProfileState, cc: string, link = true): HTMLElement {
  const name = kit.country_names[cc] ?? cc;
  return el(
    "span",
    { class: "nowrap" },
    el("span", { class: "flag", "aria-hidden": "true" }, flagEmoji(cc)),
    " ",
    link && kit.country_names[cc] ? el("a", { href: routes.country(cc) }, name) : name,
  );
}

function channelBadge(channel: string): HTMLElement {
  return el(
    "span",
    { class: `badge channel-${channel}`, title: es.channelTitles[channel] ?? "" },
    es.channels[channel] ?? channel,
  );
}

/** The link that opens the comparator with this profile. */
export function comparatorHref({ view }: ProfileState): string {
  return `${routes.home()}#pf=${encodeURIComponent(encodeProfile(view.profile))}`;
}

function patchNotes(state: ProfileState): void {
  const { view, kit } = state;
  const ties = view.profile.ties.map((t) => kit.country_names[t.cc] ?? t.cc);
  for (const node of document.querySelectorAll("[data-profile-note]")) {
    fill(
      node,
      es.visitorProfile.forYou(view.residenceName, ties),
      " ",
      el("a", { href: comparatorHref(state) }, es.visitorProfile.change),
    );
  }
  for (const node of document.querySelectorAll("[data-profile-residence]")) {
    node.textContent = view.residenceName;
  }
  for (const node of document.querySelectorAll<HTMLElement>("[data-home-country]")) {
    node.hidden = node.dataset.homeCountry !== view.residence;
  }
}

function patchServices(state: ProfileState): void {
  const { view } = state;
  for (const table of document.querySelectorAll("table[data-profile-services]")) {
    const head = table.querySelector('[data-cell="home-head"]');
    if (head) head.textContent = es.servicesIndex.colHome(view.residenceName);
    for (const row of table.querySelectorAll<HTMLElement>("tr[data-service]")) {
      const { service = "", plan = "" } = row.dataset;
      const result = view.cheapest(service, plan);
      const home = row.querySelector('[data-cell="home"]');
      if (home) home.textContent = formatMoney(result.home?.cost_eur_with_fee, "EUR");
      for (const cell of row.querySelectorAll<HTMLElement>('[data-cell="risk"]')) {
        const k = Number(cell.dataset.level) as RiskLevel;
        const best = result.bestByRisk[k];
        if (best) {
          fill(
            cell,
            countryLabel(state, best.country, false),
            " ",
            el("span", { class: "num" }, formatMoney(best.cost_eur_with_fee, "EUR")),
          );
        } else {
          fill(cell, "—");
        }
      }
    }
  }
}

function offerBadges(state: ProfileState, best: PricedOffer): HTMLElement {
  return el(
    "span",
    { class: "badges" },
    countryLabel(state, best.country),
    channelBadge(best.channel),
    riskBadge(state, best.label, best.rank),
    ...(best.eu_badge ? [euBadge(state)] : []),
  );
}

function patchSummaries(state: ProfileState): void {
  const { view } = state;
  for (const table of document.querySelectorAll<HTMLElement>("table[data-profile-summary]")) {
    const head = table.querySelector('[data-cell="saving-head"]');
    if (head) head.textContent = es.service.colSaving(view.residenceName);
    const result = view.cheapest(table.dataset.service ?? "", table.dataset.plan ?? "");
    for (const row of table.querySelectorAll<HTMLElement>("tr[data-level]")) {
      const k = Number(row.dataset.level) as RiskLevel;
      if (!RISK_LEVELS.includes(k)) continue;
      const best = result.bestByRisk[k];
      const offer = row.querySelector('[data-cell="offer"]');
      const cost = row.querySelector('[data-cell="cost"]');
      const saving = row.querySelector('[data-cell="saving"]');
      if (offer) fill(offer, best ? offerBadges(state, best) : "—");
      if (cost) cost.textContent = formatMoney(best?.cost_eur_with_fee, "EUR");
      if (saving) saving.textContent = formatSignedMoney(result.savingsMonthEur[k], "EUR");
    }
  }
}

function patchOffers(state: ProfileState): void {
  const { view, kit } = state;
  const second = view.residenceCurrency !== "EUR" ? view.residenceCurrency : null;
  for (const head of document.querySelectorAll<HTMLElement>('[data-cell="second-head"]')) {
    head.hidden = second === null;
    if (second) head.textContent = es.service.colMonthlyIn(second);
  }
  for (const row of document.querySelectorAll<HTMLElement>("tr[data-offer-svc]")) {
    const { offerSvc = "", offerCc = "", offerCurrency = "" } = row.dataset;
    const v = view.verdict(offerSvc, offerCc);
    const risk = row.querySelector('[data-cell="risk"]');
    if (risk) {
      fill(
        risk,
        el(
          "span",
          { class: "badges" },
          riskBadge(state, v.label, v.rank),
          ...(v.eu_badge ? [euBadge(state)] : []),
        ),
      );
    }
    const isHome = offerCc === view.residence;
    row.classList.toggle("is-home", isHome);
    const tag = row.querySelector<HTMLElement>("[data-home-tag]");
    if (tag) tag.hidden = !isHome;
    const cell = row.querySelector<HTMLElement>('[data-cell="second"]');
    if (cell) {
      cell.hidden = second === null;
      if (second) {
        const monthly = Number(row.dataset.offerMonthly);
        const eur = Number(row.dataset.offerEur);
        const rate = kit.rates[second];
        const value =
          offerCurrency === second
            ? monthly
            : Number.isFinite(eur) && rate
              ? eur * rate
              : null;
        cell.textContent = formatMoney(value, second);
      }
    }
  }
}

export function patchPage(state: ProfileState): void {
  patchNotes(state);
  patchServices(state);
  patchSummaries(state);
  patchOffers(state);
}

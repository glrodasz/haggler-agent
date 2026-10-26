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
 *   tr[data-offer-svc]              an OfferTable row: [data-cell=risk] (and data-rank, then
 *                                   OFFERS_PATCHED_EVENT so a risk sort re-sorts), [data-home-tag] and
 *                                   [data-cell=second] (monthly in the residence currency)
 *   [data-bundle-visitor=ID]        a bundle card's visitor verdict (shown unless the visitor is the
 *                                   default profile), [data-bundle-visitor-who|verdict]
 *   [data-cell=home-head|saving-head|second-head]   headers naming the residence/currency
 */

import { getMessages } from "../i18n";
import type { PricedOffer } from "./compare";
import { flagEmoji, formatFor } from "./format";
import { OFFERS_PATCHED_EVENT } from "./offersView";
import { routesFor } from "./paths";
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

/** The copy, formats and links of the kit's locale (the page's). */
function kitOf({ kit }: ProfileState) {
  return { msg: getMessages(kit.locale), fmt: formatFor(kit.locale), routes: routesFor(kit.locale) };
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

export function euBadge(state: ProfileState): HTMLElement {
  const { msg } = kitOf(state);
  return el(
    "span",
    { class: "badge badge-eu", title: msg.euBadgeTitle(state.view.residenceName) },
    msg.euBadge,
  );
}

export function countryLabel(state: ProfileState, cc: string, link = true): HTMLElement {
  const { kit } = state;
  const { routes } = kitOf(state);
  const name = kit.country_names[cc] ?? cc;
  return el(
    "span",
    { class: "nowrap" },
    el("span", { class: "flag", "aria-hidden": "true" }, flagEmoji(cc)),
    " ",
    link && kit.country_names[cc] ? el("a", { href: routes.country(cc) }, name) : name,
  );
}

function channelBadge(state: ProfileState, channel: string): HTMLElement {
  const { msg } = kitOf(state);
  return el(
    "span",
    { class: `badge channel-${channel}`, title: msg.channelTitles[channel] ?? "" },
    msg.channels[channel] ?? channel,
  );
}

/** The link that opens the comparator with this profile. */
export function comparatorHref(state: ProfileState): string {
  const { view } = state;
  const { routes } = kitOf(state);
  return `${routes.home()}#pf=${encodeURIComponent(encodeProfile(view.profile))}`;
}

function patchNotes(state: ProfileState): void {
  const { view, kit } = state;
  const { msg, fmt } = kitOf(state);
  const ties = view.profile.ties.map((t) => kit.country_names[t.cc] ?? t.cc);
  for (const node of document.querySelectorAll("[data-profile-note]")) {
    fill(
      node,
      msg.visitorProfile.forYou(view.residenceName, ties.length > 0 ? fmt.formatList(ties) : null),
      " ",
      el("a", { href: comparatorHref(state) }, msg.visitorProfile.change),
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
  const { msg, fmt } = kitOf(state);
  for (const table of document.querySelectorAll("table[data-profile-services]")) {
    const head = table.querySelector('[data-cell="home-head"]');
    const homeHead = msg.servicesIndex.colHome(view.residenceName);
    if (head) head.textContent = homeHead;
    // Phones show each cell with its column name (.stack-on-phone), so that name follows too.
    for (const cell of table.querySelectorAll<HTMLElement>('[data-cell="home"]')) {
      cell.dataset.label = homeHead;
    }
    for (const row of table.querySelectorAll<HTMLElement>("tr[data-service]")) {
      const { service = "", plan = "" } = row.dataset;
      const result = view.cheapest(service, plan);
      const home = row.querySelector('[data-cell="home"]');
      if (home) home.textContent = fmt.formatMoney(result.home?.cost_eur_with_fee, "EUR");
      for (const cell of row.querySelectorAll<HTMLElement>('[data-cell="risk"]')) {
        const k = Number(cell.dataset.level) as RiskLevel;
        const best = result.bestByRisk[k];
        if (best) {
          fill(
            cell,
            countryLabel(state, best.country, false),
            " ",
            el("span", { class: "num" }, fmt.formatMoney(best.cost_eur_with_fee, "EUR")),
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
    channelBadge(state, best.channel),
    riskBadge(state, best.label, best.rank),
    ...(best.eu_badge ? [euBadge(state)] : []),
  );
}

function patchSummaries(state: ProfileState): void {
  const { view } = state;
  const { msg, fmt } = kitOf(state);
  for (const table of document.querySelectorAll<HTMLElement>("table[data-profile-summary]")) {
    const head = table.querySelector('[data-cell="saving-head"]');
    if (head) head.textContent = msg.service.colSaving(view.residenceName);
    const result = view.cheapest(table.dataset.service ?? "", table.dataset.plan ?? "");
    for (const row of table.querySelectorAll<HTMLElement>("tr[data-level]")) {
      const k = Number(row.dataset.level) as RiskLevel;
      if (!RISK_LEVELS.includes(k)) continue;
      const best = result.bestByRisk[k];
      const offer = row.querySelector('[data-cell="offer"]');
      const cost = row.querySelector('[data-cell="cost"]');
      const saving = row.querySelector('[data-cell="saving"]');
      if (offer) fill(offer, best ? offerBadges(state, best) : "—");
      if (cost) cost.textContent = fmt.formatMoney(best?.cost_eur_with_fee, "EUR");
      if (saving) saving.textContent = fmt.formatSignedMoney(result.savingsMonthEur[k], "EUR");
    }
  }
}

function patchOffers(state: ProfileState): void {
  const { view, kit } = state;
  const { msg, fmt } = kitOf(state);
  const second = view.residenceCurrency !== "EUR" ? view.residenceCurrency : null;
  for (const head of document.querySelectorAll<HTMLElement>('[data-cell="second-head"]')) {
    head.hidden = second === null;
    if (second) head.textContent = msg.service.colMonthlyIn(second);
  }
  for (const row of document.querySelectorAll<HTMLElement>("tr[data-offer-svc]")) {
    const { offerSvc = "", offerCc = "", offerCurrency = "" } = row.dataset;
    const v = view.verdict(offerSvc, offerCc);
    row.dataset.rank = String(v.rank);
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
        cell.textContent = fmt.formatMoney(value, second);
      }
    }
  }
}

/** A bundle card's "For your profile": the visitor's own label when they are not the default
 * profile (whose detailed verdict, built into the card, stays below as the example). */
function patchBundleCards(state: ProfileState): void {
  const { view, kit } = state;
  const { msg, fmt } = kitOf(state);
  const own = view.preset !== kit.default_profile;
  const ties = view.profile.ties.map((t) => kit.country_names[t.cc] ?? t.cc);
  for (const box of document.querySelectorAll<HTMLElement>("[data-bundle-visitor]")) {
    const v = own ? view.bundleVerdict(box.dataset.bundleVisitor ?? "") : undefined;
    box.hidden = v === undefined;
    if (v === undefined) continue;
    const who = box.querySelector("[data-bundle-visitor-who]");
    if (who) {
      who.textContent = msg.visitorProfile.youLive(
        view.residenceName,
        ties.length > 0 ? fmt.formatList(ties) : null,
      );
    }
    const verdict = box.querySelector("[data-bundle-visitor-verdict]");
    if (verdict) {
      fill(verdict, riskBadge(state, v.label, v.rank), ...(v.eu_badge ? [" ", euBadge(state)] : []));
    }
  }
}

export function patchPage(state: ProfileState): void {
  patchNotes(state);
  patchBundleCards(state);
  patchServices(state);
  patchSummaries(state);
  patchOffers(state);
  document.dispatchEvent(new Event(OFFERS_PATCHED_EVENT));
}

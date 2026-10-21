/**
 * Comparator state <-> URL hash ("#s=netflix.standard,spotify.family&r=2&c=EUR&p=x&u=1"), so a
 * selection can be bookmarked or shared without any server or storage. p (curated feasibility
 * profile) is left out for the default profile, pf (a profile the visitor described, see
 * profile.ts) replaces it, so the common links stay short. Old links may still carry u=1 (from the
 * removed "include pending findings" toggle); it is ignored, since pending findings always count.
 */

import { decodeProfile, encodeProfile, type VisitorProfile } from "./profile";
import type { RiskLevel } from "./types";
import { RISK_LEVELS } from "./types";

export interface CompareState {
  /** Selected service ids, in catalog order. */
  services: string[];
  /** Chosen plan per service (kept for unselected services too, so toggling is lossless). */
  plans: Record<string, string>;
  risk: RiskLevel;
  currency: string;
  /** Curated feasibility profile id; null = the site's default profile. */
  profile: string | null;
  /** A profile the visitor described; when set it replaces `profile`. */
  custom: VisitorProfile | null;
}

export interface DecodeOptions {
  /** Profile ids accepted for p= (the public profiles other than the default one). */
  profiles?: readonly string[];
  /** Residence countries accepted in pf= (none: pf= is ignored). */
  residences?: readonly string[];
  /** Tie countries accepted in pf=. */
  countries?: ReadonlySet<string>;
}

export interface ServiceChoice {
  id: string;
  default_plan: string;
  plans: { id: string }[];
}

/** Preselected when the visitor arrives without a hash (the most common subscriptions). */
export const DEFAULT_SERVICES = ["netflix", "spotify", "youtube_premium", "icloud_plus"];
export const DEFAULT_RISK: RiskLevel = 0;

export function defaultState(
  services: readonly ServiceChoice[],
  currency: string,
  preselected: readonly string[] = DEFAULT_SERVICES,
): CompareState {
  const known = new Set(services.map((s) => s.id));
  return {
    services: services.map((s) => s.id).filter((id) => preselected.includes(id) && known.has(id)),
    plans: Object.fromEntries(services.map((s) => [s.id, s.default_plan])),
    risk: DEFAULT_RISK,
    currency,
    profile: null,
    custom: null,
  };
}

export function encodeState(state: CompareState): string {
  const s = state.services
    .map((id) => `${encodeURIComponent(id)}.${encodeURIComponent(state.plans[id] ?? "")}`)
    .join(",");
  const p = state.custom
    ? `&pf=${encodeURIComponent(encodeProfile(state.custom))}`
    : state.profile === null
      ? ""
      : `&p=${encodeURIComponent(state.profile)}`;
  return `s=${s}&r=${state.risk}&c=${encodeURIComponent(state.currency)}${p}`;
}

function isRisk(value: number): value is RiskLevel {
  return (RISK_LEVELS as readonly number[]).includes(value);
}

/** Tolerant parse: unknown services/plans/currencies/profiles fall back to defaults. */
export function decodeState(
  hash: string,
  services: readonly ServiceChoice[],
  currencies: readonly string[],
  fallback: CompareState,
  options: DecodeOptions = {},
): CompareState {
  const params = new URLSearchParams(hash.replace(/^#/, ""));
  if (![...params.keys()].length) return fallback;
  const byId = new Map(services.map((s) => [s.id, s]));
  const plans = { ...fallback.plans };
  let selected = fallback.services;
  const raw = params.get("s");
  if (raw !== null) {
    const picked = new Set<string>();
    for (const item of raw.split(",").filter(Boolean)) {
      const [id = "", plan = ""] = item.split(".");
      const svc = byId.get(id);
      if (!svc) continue;
      picked.add(id);
      if (svc.plans.some((p) => p.id === plan)) plans[id] = plan;
    }
    // Keep catalog order regardless of the order in the URL.
    selected = services.map((s) => s.id).filter((id) => picked.has(id));
  }
  const risk = Number(params.get("r"));
  const currency = params.get("c") ?? "";
  const profile = params.get("p") ?? "";
  const custom = params.has("pf")
    ? decodeProfile(
        params.get("pf") ?? "",
        options.residences ?? [],
        options.countries ?? new Set(),
      )
    : null;
  return {
    services: selected,
    plans,
    risk: params.has("r") && isRisk(risk) ? risk : fallback.risk,
    currency: currencies.includes(currency) ? currency : fallback.currency,
    // A link to a profile that is gone (or the default one) shows the default profile.
    profile: !custom && (options.profiles ?? []).includes(profile) ? profile : null,
    custom,
  };
}

/**
 * The visitor's profile across pages. The comparator is where a visitor describes it; every
 * other page (services, countries, bundles, the hero) shows prices and verdicts for that same
 * profile instead of the site's default one:
 *
 *  - a profile the visitor set in the comparator is kept in this browser (localStorage, never
 *    sent anywhere) and wins;
 *  - otherwise the visitor's country (locateVisitor) with no ties;
 *  - otherwise the site's default residence with no ties.
 *
 * LOCATE_BY_IP decides how the country is found: on arrival by asking country.is for the
 * country of the visitor's IP (it receives the IP), falling back to the browser's time zone and
 * language; or by the browser hints alone. Nothing is stored for a located country, so a VPN or
 * a trip shows up on the next page.
 */

import { guessFromBrowser, type Located, lookupIp } from "./locate";
import { decodeProfile, encodeProfile, type VisitorProfile } from "./profile";

/** True: locate by IP on arrival (browser hints as fallback). False: browser hints only. */
export const LOCATE_BY_IP = true;

const STORAGE_KEY = "haggler.perfil";
/** Fired on window when the comparator saves or clears the visitor's profile. */
export const PROFILE_EVENT = "haggler:perfil";

export function readSavedProfile(
  residences: readonly string[],
  countries: ReadonlySet<string>,
): VisitorProfile | null {
  try {
    const raw = window.localStorage.getItem(STORAGE_KEY);
    return raw ? decodeProfile(raw, residences, countries) : null;
  } catch {
    return null;
  }
}

export function saveProfile(profile: VisitorProfile): void {
  try {
    window.localStorage.setItem(STORAGE_KEY, encodeProfile(profile));
  } catch {
    // Storage blocked (private mode, site data off): the profile lasts for this page only.
  }
  window.dispatchEvent(new CustomEvent(PROFILE_EVENT));
}

let located: Promise<Located | null> | null = null;

/** Where the visitor is, once per page load (see LOCATE_BY_IP). */
export function locateVisitor(): Promise<Located | null> {
  located ??= LOCATE_BY_IP
    ? lookupIp().then((found) => found ?? guessFromBrowser())
    : Promise.resolve(guessFromBrowser());
  return located;
}

/** Someone who only lives in `cc`: its ID document and phone, no ties. */
export function neutralProfile(cc: string): VisitorProfile {
  return { residence: cc, residenceDoc: true, residencePhone: true, residenceCard: false, ties: [] };
}

export type VisitorSource = "saved" | "located" | "default";

export interface ResolvedVisitor {
  profile: VisitorProfile;
  source: VisitorSource;
  located: Located | null;
}

/** The profile to show: saved, else located with no ties, else `fallback` with no ties. */
export async function resolveVisitor(
  residences: readonly string[],
  countries: ReadonlySet<string>,
  fallback: string,
): Promise<ResolvedVisitor> {
  const saved = readSavedProfile(residences, countries);
  if (saved) return { profile: saved, source: "saved", located: null };
  const found = await locateVisitor();
  if (found && residences.includes(found.cc)) {
    return { profile: neutralProfile(found.cc), source: "located", located: found };
  }
  return { profile: neutralProfile(fallback), source: "default", located: found };
}

/** Calls `listener` when the saved profile changes, here or in another tab. */
export function onProfileChange(listener: () => void): () => void {
  const storage = (e: StorageEvent) => {
    if (e.key === STORAGE_KEY) listener();
  };
  window.addEventListener(PROFILE_EVENT, listener);
  window.addEventListener("storage", storage);
  return () => {
    window.removeEventListener(PROFILE_EVENT, listener);
    window.removeEventListener("storage", storage);
  };
}

import { LOCALE_COOKIE, type Locale } from "./locale";

/**
 * The visitor's explicit language choice, written only when they pick one in the language menu.
 * The site promises "no cookies", and on a static host nothing server-side would read one, so the
 * choice lives in localStorage under the same key. Storage can be blocked (private mode, site
 * data cleared): then the choice simply is not remembered.
 */
export function persistLocale(locale: Locale): void {
  try {
    localStorage.setItem(LOCALE_COOKIE, locale);
  } catch {
    // Not remembered; the URL still decides what this page shows.
  }
}

export function readSavedLocale(): string | null {
  try {
    return localStorage.getItem(LOCALE_COOKIE);
  } catch {
    return null;
  }
}

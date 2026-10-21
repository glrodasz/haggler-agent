/**
 * Blocking boot script (bundled to an IIFE and inlined at the top of <head> by Base.astro):
 * sends an unprefixed first visit to the visitor's language before the first paint. The site is
 * served under a base path on GitHub Pages, passed in data-base.
 */
import { resolveLocaleRedirect } from "./locale";
import { readSavedLocale } from "./persistLocale";

const root = document.documentElement;
const base = (document.currentScript as HTMLScriptElement | null)?.dataset.base ?? "/";
try {
  const prefix = base.endsWith("/") ? base.slice(0, -1) : base;
  const pathname = location.pathname.startsWith(prefix)
    ? location.pathname.slice(prefix.length) || "/"
    : location.pathname;
  const target = resolveLocaleRedirect({
    pathname,
    savedLocale: readSavedLocale(),
    languages: navigator.languages?.length ? navigator.languages : [navigator.language],
  });
  if (target) {
    root.setAttribute("data-locale-pending", "");
    setTimeout(() => root.removeAttribute("data-locale-pending"), 300); // never strand a blank page
    location.replace(prefix + target + location.search + location.hash);
  }
} catch {
  root.removeAttribute("data-locale-pending");
}

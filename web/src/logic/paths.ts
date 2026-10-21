/**
 * Internal links. The site can be served under a base path (GitHub Pages without a custom
 * domain: "/haggler-agent/"), so every internal href goes through withBase(); hard-coded "/..."
 * links would 404 there.
 * English lives at the root and every other locale under its prefix ("/es/services/"), so
 * links are built per locale with routesFor(); i18n/locale.ts decides the prefix.
 */

import {
  DEFAULT_LOCALE,
  type Locale,
  LOCALES,
  localizePath,
  pageLocale,
} from "../i18n/locale";

export function withBase(path: string, base: string = import.meta.env.BASE_URL ?? "/"): string {
  const prefix = base.endsWith("/") ? base : `${base}/`;
  return prefix + path.replace(/^\/+/, "");
}

/** The path without the base ("/haggler-agent/es/services/" -> "/es/services/"). */
export function stripBase(pathname: string, base: string = import.meta.env.BASE_URL ?? "/"): string {
  const prefix = base.endsWith("/") ? base.slice(0, -1) : base;
  return prefix && pathname.startsWith(prefix) ? pathname.slice(prefix.length) || "/" : pathname;
}

/** The locale a page URL renders in. */
export function localeOfUrl(url: URL): Locale {
  return pageLocale(stripBase(url.pathname));
}

/** getStaticPaths entries for a page that exists in every locale ([...lang] routes). */
export function localeParams(): { params: { lang: string | undefined }; props: { locale: Locale } }[] {
  return LOCALES.map((locale) => ({
    params: { lang: locale === DEFAULT_LOCALE ? undefined : locale },
    props: { locale },
  }));
}

export function countrySlug(cc: string): string {
  return cc.toLowerCase();
}

/** Element id of a bundle's card (BundleCard), the anchor the bundles page links to. */
export function bundleAnchor(bundleId: string): string {
  return `paquete-${bundleId}`;
}

export function routesFor(locale: Locale) {
  const at = (path: string, base?: string) => withBase(localizePath(`/${path}`, locale), base);
  const routes = {
    home: (base?: string) => at("", base),
    services: (base?: string) => at("services/", base),
    service: (id: string, base?: string) => at(`services/${encodeURIComponent(id)}/`, base),
    countries: (base?: string) => at("countries/", base),
    country: (cc: string, base?: string) => at(`countries/${countrySlug(cc)}/`, base),
    bundles: (base?: string) => at("bundles/", base),
    /** A bundle's full card on its country page (active and expired ones alike). */
    bundle: (cc: string, bundleId: string, base?: string) =>
      `${routes.country(cc, base)}#${encodeURIComponent(bundleAnchor(bundleId))}`,
    changes: (base?: string) => at("changes/", base),
    methodology: (base?: string) => at("methodology/", base),
  };
  return routes;
}

export type Routes = ReturnType<typeof routesFor>;

/** The visitor-profile kit in `locale` (pages/datos/[locale]/perfil.json.ts). */
export function profileKitUrl(locale: Locale, base?: string): string {
  return withBase(`datos/${locale}/perfil.json`, base);
}

/**
 * Internal links. The site is served under a base path on GitHub Pages ("/haggler-agent/"),
 * so every internal href goes through withBase(); hard-coded "/..." links would 404 there.
 */

export function withBase(path: string, base: string = import.meta.env.BASE_URL ?? "/"): string {
  const prefix = base.endsWith("/") ? base : `${base}/`;
  return prefix + path.replace(/^\/+/, "");
}

export function countrySlug(cc: string): string {
  return cc.toLowerCase();
}

/** Element id of a bundle's card (BundleCard), the anchor /paquetes links to. */
export function bundleAnchor(bundleId: string): string {
  return `paquete-${bundleId}`;
}

export const routes = {
  home: (base?: string) => withBase("", base),
  services: (base?: string) => withBase("servicios/", base),
  service: (id: string, base?: string) => withBase(`servicios/${encodeURIComponent(id)}/`, base),
  countries: (base?: string) => withBase("paises/", base),
  country: (cc: string, base?: string) => withBase(`paises/${countrySlug(cc)}/`, base),
  bundles: (base?: string) => withBase("paquetes/", base),
  /** A bundle's full card on its country page (active and expired ones alike). */
  bundle: (cc: string, bundleId: string, base?: string) =>
    `${routes.country(cc, base)}#${encodeURIComponent(bundleAnchor(bundleId))}`,
  changes: (base?: string) => withBase("cambios/", base),
  methodology: (base?: string) => withBase("metodologia/", base),
};

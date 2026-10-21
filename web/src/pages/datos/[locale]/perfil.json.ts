/**
 * /datos/<locale>/perfil.json: what pages need to redraw for the visitor's profile, with names
 * and labels in that locale (logic/profileKit.ts).
 */

import { type Locale, LOCALES } from "../../../i18n/locale";
import { getSite } from "../../../logic/data";
import { buildProfileKit } from "../../../logic/profileKit";

export function getStaticPaths() {
  return LOCALES.map((locale) => ({ params: { locale } }));
}

export function GET({ params }: { params: { locale: Locale } }): Response {
  return new Response(JSON.stringify(buildProfileKit(getSite(params.locale))), {
    headers: { "Content-Type": "application/json" },
  });
}

/** /datos/perfil.json: what pages need to redraw for the visitor's profile (logic/profileKit.ts). */

import { getSite } from "../../logic/data";
import { buildProfileKit } from "../../logic/profileKit";

export function GET(): Response {
  return new Response(JSON.stringify(buildProfileKit(getSite())), {
    headers: { "Content-Type": "application/json" },
  });
}

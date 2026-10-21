/**
 * Browser side of the visitor's profile on the pages other than the comparator: loads the kit
 * (/datos/perfil.json, once per page), resolves the visitor (logic/visitor.ts) and hands every
 * subscriber the ProfileView, again whenever the comparator saves another profile.
 */

import { routes } from "./paths";
import { type ProfileKit, type ProfileView, profileView } from "./profileKit";
import { onProfileChange, type ResolvedVisitor, resolveVisitor } from "./visitor";

let kit: Promise<ProfileKit | null> | null = null;

export function loadKit(): Promise<ProfileKit | null> {
  kit ??= fetch(routes.profileKit())
    .then((res) => (res.ok ? (res.json() as Promise<ProfileKit>) : null))
    .catch(() => null);
  return kit;
}

export interface ProfileState {
  view: ProfileView;
  kit: ProfileKit;
  source: ResolvedVisitor["source"];
}

/**
 * Calls `listener` with the visitor's view once it is known, and again after each change.
 * Without the kit (offline, old cache) nothing is called: the page keeps the default profile.
 */
export function watchProfile(listener: (state: ProfileState) => void): () => void {
  let stopped = false;
  let run = 0;
  const update = async () => {
    const mine = ++run;
    const loaded = await loadKit();
    if (!loaded || stopped) return;
    const resolved = await resolveVisitor(
      loaded.residences.map((r) => r.cc),
      new Set(loaded.tie_countries),
      loaded.default_residence,
    );
    if (stopped || mine !== run) return;
    listener({ view: profileView(loaded, resolved.profile), kit: loaded, source: resolved.source });
  };
  void update();
  const off = onProfileChange(() => void update());
  return () => {
    stopped = true;
    off();
  };
}

import { afterEach, describe, expect, it, vi } from "vitest";
import { encodeProfile } from "./profile";
import { neutralProfile, onProfileChange, resolveVisitor, saveProfile } from "./visitor";

const RESIDENCES = ["SE", "ES", "AR"];
const TIES = new Set(["SE", "ES", "AR", "CO"]);

function browser(stored: Record<string, string> = {}, ipCountry: string | null = "AR") {
  const store = new Map(Object.entries(stored));
  const target = new EventTarget();
  vi.stubGlobal("window", {
    localStorage: {
      getItem: (k: string) => store.get(k) ?? null,
      setItem: (k: string, v: string) => void store.set(k, v),
    },
    addEventListener: target.addEventListener.bind(target),
    removeEventListener: target.removeEventListener.bind(target),
    dispatchEvent: target.dispatchEvent.bind(target),
  });
  vi.stubGlobal(
    "fetch",
    vi.fn(async () =>
      ipCountry ? new Response(JSON.stringify({ country: ipCountry })) : new Response("", { status: 500 }),
    ),
  );
  vi.stubGlobal("navigator", { languages: [] });
  return store;
}

afterEach(() => {
  vi.unstubAllGlobals();
  vi.resetModules();
});

describe("resolveVisitor", () => {
  it("prefers the profile saved in the comparator", async () => {
    const saved = { ...neutralProfile("ES"), ties: [] };
    browser({ "haggler.perfil": `${encodeProfile(saved)},CO.dc` });
    const { profile, source } = await resolveVisitor(RESIDENCES, TIES, "SE");
    expect(source).toBe("saved");
    expect(profile.residence).toBe("ES");
    expect(profile.ties.map((t) => t.cc)).toEqual(["CO"]);
  });

  it("starts from the located country with no ties", async () => {
    browser({}, "AR");
    const { located, ...rest } = await (await import("./visitor")).resolveVisitor(
      RESIDENCES,
      TIES,
      "SE",
    );
    expect(rest).toEqual({ profile: neutralProfile("AR"), source: "located" });
    expect(located?.source).toBe("ip");
  });

  it("falls back to the site's residence for a country without prices", async () => {
    browser({}, "JP");
    const { resolveVisitor: resolve } = await import("./visitor");
    const { profile, source } = await resolve(RESIDENCES, TIES, "SE");
    expect(source).toBe("default");
    expect(profile).toEqual(neutralProfile("SE"));
  });

  it("saves the profile and tells the listeners", () => {
    const store = browser();
    const heard = vi.fn();
    const off = onProfileChange(heard);
    saveProfile(neutralProfile("ES"));
    expect(store.get("haggler.perfil")).toBe("ES.dp");
    expect(heard).toHaveBeenCalledTimes(1);
    off();
  });
});

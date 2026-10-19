/**
 * HeroSavings: rotating level-3 savings under the hero title for the country the browser
 * suggests (logic/locate.ts guessFromBrowser; no IP lookup, nothing leaves the browser). The
 * figures come from the same offers and rules the comparator reads (its inline JSON) through
 * logic/teasers.ts. Each message is a link to the comparator state it was computed from: a
 * click puts it in location.hash, which the comparator follows, and scrolls to the form.
 * No controls, on purpose: it pauses on hover and focus, and stays still with reduced motion.
 */

import { useEffect, useRef, useState } from "preact/hooks";
import { es } from "../i18n/es";
import { formatPercent } from "../logic/format";
import { guessFromBrowser } from "../logic/locate";
import { defaultState, encodeState } from "../logic/selection";
import { savingsTeasers, type Teaser } from "../logic/teasers";
import { unpackOffers } from "../logic/transport";
import type { FeasibilityRule, ProfileCapabilities } from "../logic/types";
import type { ComparadorResidence, ComparadorService } from "./Comparador";

export interface HeroSavingsProps {
  services: ComparadorService[];
  residences: ComparadorResidence[];
  /** Countries a tie can be with (the ones with prices). */
  tieCountries: string[];
  countryNames: Record<string, string>;
  rates: Record<string, number>;
  feePct: number;
  /** The site's residence, when the browser suggests none we have prices for. */
  residence: string;
  profiles: { id: string; capabilities: ProfileCapabilities }[];
  defaultProfile: string;
  eeaCountries: string[];
  /** ids of the comparator's inline JSON (offers and rules). */
  dataId: string;
  verdictsId: string;
  /** Where a click scrolls to. */
  targetId: string;
}

const t = es.home.teasers;
const ROTATE_MS = 5000;
const COUNTRIES = new Intl.ListFormat("es", { type: "conjunction" });

function readJson(id: string): unknown {
  try {
    return JSON.parse(document.getElementById(id)?.textContent ?? "null");
  } catch {
    return null;
  }
}

function prefersReducedMotion(): boolean {
  return window.matchMedia?.("(prefers-reduced-motion: reduce)").matches ?? false;
}

export default function HeroSavings(props: HeroSavingsProps) {
  const [teasers, setTeasers] = useState<Teaser[] | null>(null);
  const [residence, setResidence] = useState(props.residence);
  const [index, setIndex] = useState(0);
  /** Reduced motion: the first message stays put. */
  const [still, setStill] = useState(false);
  /** Hover or focus: rotation waits while the visitor reads or is about to click. */
  const [holding, setHolding] = useState(false);
  const root = useRef<HTMLDivElement>(null);

  useEffect(() => {
    if (prefersReducedMotion()) setStill(true);
    // After the first paint: the comparator hydrates first and the hero stays responsive.
    const run = () => {
      const guess = guessFromBrowser();
      const home =
        props.residences.find((r) => r.cc === guess?.cc) ??
        props.residences.find((r) => r.cc === props.residence);
      if (!home) {
        setTeasers([]);
        return;
      }
      const rules = (readJson(props.verdictsId) as { rules?: FeasibilityRule[] } | null)?.rules;
      setResidence(home.cc);
      setTeasers(
        savingsTeasers({
          offers: unpackOffers(readJson(props.dataId)),
          source: {
            default_profile: props.defaultProfile,
            profiles: props.profiles,
            rules: Array.isArray(rules) ? rules : [],
            eea_countries: props.eeaCountries,
          },
          residence: home.cc,
          residenceCurrency: home.currency,
          tieCountries: props.tieCountries,
          rates: props.rates,
          feePct: props.feePct,
          base: defaultState(props.services, home.currency),
        }),
      );
    };
    const idle = window.requestIdleCallback ?? ((cb: () => void) => window.setTimeout(cb, 1));
    idle(run);
  }, []);

  const count = teasers?.length ?? 0;
  useEffect(() => {
    if (still || holding || count < 2) return;
    const timer = window.setTimeout(() => setIndex((i) => (i + 1) % count), ROTATE_MS);
    return () => window.clearTimeout(timer);
  }, [index, still, holding, count]);

  // Reserves the line while computing so the hero does not jump; nothing to say, nothing shown.
  if (teasers === null) return <div class="hero-savings is-loading" aria-hidden="true" />;
  if (count === 0) return null;

  const name = (cc: string) => props.countryNames[cc] ?? cc;
  const current = teasers[Math.min(index, count - 1)] as Teaser;
  const hash = `#${encodeState(current.state)}`;
  const open = (event: MouseEvent) => {
    if (event.metaKey || event.ctrlKey || event.shiftKey || event.button !== 0) return;
    event.preventDefault();
    // Assigning the hash fires hashchange, which the comparator loads; no element has that id,
    // so the browser does not scroll by itself.
    window.location.hash = hash;
    const target = document.getElementById(props.targetId);
    target?.scrollIntoView({ behavior: prefersReducedMotion() ? "auto" : "smooth" });
    target?.focus({ preventScroll: true });
  };

  return (
    <div
      ref={root}
      class="hero-savings"
      role="region"
      aria-roledescription="carrusel"
      aria-label={t.label(name(residence))}
      onMouseEnter={() => setHolding(true)}
      onMouseLeave={() => setHolding(false)}
      onFocusIn={() => setHolding(true)}
      onFocusOut={(e) => {
        if (!root.current?.contains(e.relatedTarget as Node | null)) setHolding(false);
      }}
    >
      <a
        class="hero-savings-link"
        href={hash}
        title={t.linkTitle}
        onClick={open}
        aria-live={still || holding ? "polite" : "off"}
        key={index}
      >
        <span class="hero-savings-head">
          <strong>
            {t.headline(
              formatPercent(current.pct, { digits: 0 }),
              COUNTRIES.format(current.countries.slice(0, 2).map(name)),
            )}
          </strong>
          <span class="hero-savings-arrow" aria-hidden="true">
            →
          </span>
        </span>
        <span class="hero-savings-sub">
          {current.tie === null ? t.alone : t.tie(name(current.tie))}
        </span>
      </a>
    </div>
  );
}

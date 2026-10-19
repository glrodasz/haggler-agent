/**
 * HeroSavings: a marquee of level-3 savings tags under the hero title for the country the browser
 * suggests (logic/locate.ts guessFromBrowser; no IP lookup, nothing leaves the browser). The
 * figures come from the same offers and rules the comparator reads (its inline JSON) through
 * logic/teasers.ts. Each tag is a link to the comparator state it was computed from: a click
 * puts it in location.hash, which the comparator follows, and scrolls to the form.
 * Several options at once, because not every visitor can use every country. The row scrolls by
 * itself (CSS), pauses on hover and focus, and is a plain scrollable row with reduced motion.
 */

import { useEffect, useState } from "preact/hooks";
import { es } from "../i18n/es";
import { flagEmoji, formatPercent } from "../logic/format";
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
const COUNTRIES = new Intl.ListFormat("es", { type: "conjunction" });
/** More options than fit on screen, so the marquee has something to show. */
const MAX_TAGS = 12;
/** Marquee speed: seconds per tag for one full loop. */
const SECONDS_PER_TAG = 4;

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

  useEffect(() => {
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
        }, MAX_TAGS),
      );
    };
    const idle = window.requestIdleCallback ?? ((cb: () => void) => window.setTimeout(cb, 1));
    idle(run);
  }, []);

  // Reserves the row while computing so the hero does not jump; nothing to say, nothing shown.
  if (teasers === null) return <div class="hero-savings is-loading" aria-hidden="true" />;
  if (teasers.length === 0) return null;

  const name = (cc: string) => props.countryNames[cc] ?? cc;
  const open = (event: MouseEvent, hash: string) => {
    if (event.metaKey || event.ctrlKey || event.shiftKey || event.button !== 0) return;
    event.preventDefault();
    // Assigning the hash fires hashchange, which the comparator loads; no element has that id,
    // so the browser does not scroll by itself.
    window.location.hash = hash;
    const target = document.getElementById(props.targetId);
    target?.scrollIntoView({ behavior: prefersReducedMotion() ? "auto" : "smooth" });
    target?.focus({ preventScroll: true });
  };
  const tag = (teaser: Teaser, i: number, copy: boolean) => {
    const hash = `#${encodeState(teaser.state)}`;
    const pct = formatPercent(teaser.pct, { digits: 0 });
    const where = teaser.countries[0] ?? teaser.tie ?? "";
    const condition = teaser.tie === null ? t.alone : t.tie(name(teaser.tie));
    return (
      <li key={`${copy ? "b" : "a"}${i}`} aria-hidden={copy ? "true" : undefined}>
        <a
          class="hero-tag"
          href={hash}
          tabIndex={copy ? -1 : undefined}
          title={`${condition}. ${t.linkTitle}`}
          aria-label={`${t.headline(pct, COUNTRIES.format(teaser.countries.slice(0, 2).map(name)))}. ${condition}. ${t.linkTitle}`}
          onClick={(e) => open(e, hash)}
        >
          <span aria-hidden="true">{flagEmoji(where)}</span>
          <span>{name(where)}</span>
          <strong>−{pct}</strong>
          {teaser.tie === null && <span class="hero-tag-note">{t.aloneTag}</span>}
        </a>
      </li>
    );
  };

  return (
    <div class="hero-savings">
      <p class="hero-savings-label" id="hero-savings-label">
        {t.label(name(residence))}
      </p>
      <div class="hero-marquee">
        <ul
          class="hero-marquee-track"
          aria-labelledby="hero-savings-label"
          style={{ animationDuration: `${teasers.length * SECONDS_PER_TAG}s` }}
        >
          {teasers.map((x, i) => tag(x, i, false))}
          {teasers.map((x, i) => tag(x, i, true))}
        </ul>
      </div>
    </div>
  );
}

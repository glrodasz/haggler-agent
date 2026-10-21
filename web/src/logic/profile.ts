/**
 * The comparator's profile builder: a visitor describes where they live and their ties with
 * other countries, and the rules (verdicts.ts) are evaluated for that description. A
 * VisitorProfile is the form's model; toCapabilities turns it into the ProfileCapabilities the
 * rules ask questions about, fromCapabilities loads a curated profile into the form.
 *
 * URL form (selection.ts, `pf=`): the residence first, then one item per tie, comma separated,
 * each "CC" or "CC.flags": d = ID document or passport, c = card issued there, p = phone number,
 * a = real address (ties only), o = travels there now and then, r = goes regularly (ties only).
 * E.g. "SE.dp,CO.dco" = lives in Sweden with a Swedish ID and phone; Colombian ID and card,
 * travels to Colombia now and then.
 */

import type { PresenceKind, ProfileCapabilities } from "./types";

export type Presence = PresenceKind | "none";

/** What the visitor has in one country other than the residence. */
export interface Tie {
  cc: string;
  doc: boolean;
  card: boolean;
  phone: boolean;
  address: boolean;
  presence: Presence;
}

export interface VisitorProfile {
  residence: string;
  /** An ID document, phone number and card of the residence country (the address is implied). */
  residenceDoc: boolean;
  residencePhone: boolean;
  residenceCard: boolean;
  ties: Tie[];
}

/**
 * A tie added in the form. It assumes regular trips, which most level-2 offers need; the
 * visitor lowers it if not. Shared links keep what they encode: no o/r flag means no trips.
 */
export function emptyTie(cc: string): Tie {
  return { cc, doc: true, card: false, phone: false, address: false, presence: "periodic" };
}

export function fromCapabilities(caps: ProfileCapabilities): VisitorProfile {
  const r = caps.residence_country;
  const others = [
    ...caps.id_document_countries,
    ...caps.card_countries,
    ...caps.phone_countries,
    ...caps.address_countries,
    ...Object.keys(caps.presence),
  ].filter((cc) => cc !== r);
  return {
    residence: r,
    residenceDoc: caps.id_document_countries.includes(r),
    residencePhone: caps.phone_countries.includes(r),
    residenceCard: caps.card_countries.includes(r),
    ties: [...new Set(others)].map((cc) => ({
      cc,
      doc: caps.id_document_countries.includes(cc),
      card: caps.card_countries.includes(cc),
      phone: caps.phone_countries.includes(cc),
      address: caps.address_countries.includes(cc),
      presence: caps.presence[cc] ?? "none",
    })),
  };
}

export function toCapabilities(v: VisitorProfile): ProfileCapabilities {
  const ties = v.ties.filter((t) => t.cc !== v.residence);
  const pick = (own: boolean, flag: (t: Tie) => boolean) =>
    [...(own ? [v.residence] : []), ...ties.filter(flag).map((t) => t.cc)].sort();
  return {
    residence_country: v.residence,
    id_document_countries: pick(v.residenceDoc, (t) => t.doc),
    phone_countries: pick(v.residencePhone, (t) => t.phone),
    address_countries: pick(true, (t) => t.address),
    card_countries: pick(v.residenceCard, (t) => t.card),
    presence: Object.fromEntries(
      ties.flatMap((t) => (t.presence === "none" ? [] : [[t.cc, t.presence] as const])),
    ),
  };
}

/**
 * Moving the residence: the new country's ID and phone are assumed (and its card, when the
 * visitor had one), and it stops being a tie; the old residence is dropped.
 */
export function changeResidence(v: VisitorProfile, cc: string): VisitorProfile {
  if (cc === v.residence) return v;
  const tie = v.ties.find((t) => t.cc === cc);
  return {
    residence: cc,
    residenceDoc: true,
    residencePhone: true,
    residenceCard: tie?.card ?? false,
    ties: v.ties.filter((t) => t.cc !== cc),
  };
}

const PRESENCE_FLAG: Record<Presence, string> = { none: "", once: "o", periodic: "r" };

function item(cc: string, flags: string): string {
  return flags ? `${cc}.${flags}` : cc;
}

export function encodeProfile(v: VisitorProfile): string {
  const own = `${v.residenceDoc ? "d" : ""}${v.residenceCard ? "c" : ""}${v.residencePhone ? "p" : ""}`;
  const ties = v.ties.map((t) =>
    item(
      t.cc,
      `${t.doc ? "d" : ""}${t.card ? "c" : ""}${t.phone ? "p" : ""}${t.address ? "a" : ""}${PRESENCE_FLAG[t.presence]}`,
    ),
  );
  return [item(v.residence, own), ...ties].join(",");
}

/**
 * Tolerant parse of encodeProfile's output: null when the residence is not one of
 * `residences`; ties with an unknown country, or repeated, are skipped.
 */
export function decodeProfile(
  raw: string,
  residences: readonly string[],
  countries: ReadonlySet<string>,
): VisitorProfile | null {
  const [head = "", ...rest] = raw.split(",");
  const [residence = "", own = ""] = head.split(".");
  if (!residences.includes(residence)) return null;
  const seen = new Set([residence]);
  const ties: Tie[] = [];
  for (const part of rest) {
    const [cc = "", flags = ""] = part.split(".");
    if (!countries.has(cc) || seen.has(cc)) continue;
    seen.add(cc);
    ties.push({
      cc,
      doc: flags.includes("d"),
      card: flags.includes("c"),
      phone: flags.includes("p"),
      address: flags.includes("a"),
      presence: flags.includes("r") ? "periodic" : flags.includes("o") ? "once" : "none",
    });
  }
  return {
    residence,
    residenceDoc: own.includes("d"),
    residencePhone: own.includes("p"),
    residenceCard: own.includes("c"),
    ties,
  };
}

/** ISO 3166-1 alpha-2 codes of every country and territory a visitor may have ties with. */
export const ALL_COUNTRY_CODES = (
  "AD AE AF AG AI AL AM AO AR AS AT AU AW AX AZ BA BB BD BE BF BG BH BI BJ BL BM BN BO BQ BR BS " +
  "BT BW BY BZ CA CC CD CF CG CH CI CK CL CM CN CO CR CU CV CW CX CY CZ DE DJ DK DM DO DZ EC EE " +
  "EG EH ER ES ET FI FJ FK FM FO FR GA GB GD GE GF GG GH GI GL GM GN GP GQ GR GT GU GW GY HK HN " +
  "HR HT HU ID IE IL IM IN IQ IR IS IT JE JM JO JP KE KG KH KI KM KN KP KR KW KY KZ LA LB LC LI " +
  "LK LR LS LT LU LV LY MA MC MD ME MF MG MH MK ML MM MN MO MP MQ MR MS MT MU MV MW MX MY MZ NA " +
  "NC NE NF NG NI NL NO NP NR NU NZ OM PA PE PF PG PH PK PL PM PN PR PS PT PW PY QA RE RO RS RU " +
  "RW SA SB SC SD SE SG SH SI SK SL SM SN SO SR SS ST SV SX SY SZ TC TD TG TH TJ TK TL TM TN TO " +
  "TR TT TV TW TZ UA UG UM US UY UZ VA VC VE VG VI VN VU WF WS XK YE YT ZA ZM ZW"
).split(" ");

/**
 * Names for `codes` in `intlLocale`, sorted by name: `known` (the site's own names) first, else
 * the runtime's region names, else the code.
 */
export function countryOptions(
  codes: readonly string[],
  known: Readonly<Record<string, string>>,
  intlLocale = "es",
): { cc: string; name: string }[] {
  let names: Intl.DisplayNames | undefined;
  try {
    names = new Intl.DisplayNames([intlLocale], { type: "region" });
  } catch {
    names = undefined;
  }
  const name = (cc: string) => {
    if (known[cc]) return known[cc];
    try {
      return names?.of(cc) ?? cc;
    } catch {
      return cc;
    }
  };
  return codes
    .map((cc) => ({ cc, name: name(cc) }))
    .sort((a, b) => a.name.localeCompare(b.name, intlLocale));
}

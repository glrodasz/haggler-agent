import { en, type Messages } from "./en";
import { es } from "./es";
import { DEFAULT_LOCALE, type Locale } from "./locale";
import { pt } from "./pt";

export type { Messages } from "./en";

const MESSAGES: Record<Locale, Messages> = { en, es, pt };

export function getMessages(locale: Locale): Messages {
  return MESSAGES[locale] ?? MESSAGES[DEFAULT_LOCALE];
}

/** A table's entry for `key`, the key itself when the table lacks it, "unknown" without a key. */
export function lookupFor(msg: Messages) {
  return (
    table: Record<string, string>,
    key: string | null | undefined,
  ): string => {
    if (key === null || key === undefined) return msg.common.unknown;
    return table[key] ?? key;
  };
}

export function profileDescription(
  msg: Messages,
  id: string,
  residence: string,
): string {
  return msg.profiles[id]?.(residence) ?? msg.unknownProfile(id, residence);
}

export interface LocalizedNotes {
  notes_es: string;
  notes_en: string;
  notes_pt: string;
}

/**
 * A curated note in the visitor's language. Notes are written in Spanish first, so a missing
 * translation falls back to it; `lang` says which one the text is in (for the lang attribute).
 */
export function localNote(
  locale: Locale,
  n: LocalizedNotes,
): { text: string; lang: Locale } {
  const own =
    locale === "es" ? n.notes_es : locale === "en" ? n.notes_en : n.notes_pt;
  return own ? { text: own, lang: locale } : { text: n.notes_es, lang: "es" };
}

type LocalizedLists<K extends string> = Record<
  `${K}_es` | `${K}_en` | `${K}_pt`,
  string[]
>;

/**
 * Generated sentences (reasons, assumptions, a bundle's status reasons) in the visitor's
 * language. publish.py writes the three lists in step; a list missing a locale (data older than
 * the translations) falls back to the Spanish one.
 */
export function localList<K extends string>(
  locale: Locale,
  record: LocalizedLists<K>,
  name: K,
): { items: string[]; lang: Locale } {
  const es = record[`${name}_es`];
  const own = record[`${name}_${locale}`];
  return own && own.length === es.length
    ? { items: own, lang: locale }
    : { items: es, lang: "es" };
}

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
  return (table: Record<string, string>, key: string | null | undefined): string => {
    if (key === null || key === undefined) return msg.common.unknown;
    return table[key] ?? key;
  };
}

export function profileDescription(msg: Messages, id: string, residence: string): string {
  return msg.profiles[id]?.(residence) ?? msg.unknownProfile(id, residence);
}


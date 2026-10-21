/**
 * The page's locale for the islands: the top-level island gets it as a prop and provides it, so
 * nested components read their copy and number formats without threading it through every prop.
 */

import { createContext } from "preact";
import { useContext } from "preact/hooks";
import { getMessages } from "../i18n";
import { DEFAULT_LOCALE, type Locale } from "../i18n/locale";
import { formatFor } from "../logic/format";

export const LocaleContext = createContext<Locale>(DEFAULT_LOCALE);

export function useLocaleKit() {
  const locale = useContext(LocaleContext);
  return { locale, msg: getMessages(locale), fmt: formatFor(locale) };
}

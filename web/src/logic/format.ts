/**
 * Display formatting per locale. Intl does the heavy lifting; these helpers only add the
 * conventions the site needs: "—" for missing values, list prices without ",00", UTC dates
 * (ISO dates in the data have no time zone and must not shift a day in the browser), and, in the
 * decimal-comma locales (es, pt), thousands always grouped with a no-break space ("3 834",
 * "12 345,68 €"): es-ES leaves four-digit numbers ungrouped and uses a dot, which reads poorly
 * next to the decimal comma. English keeps its own "3,834" / "€12,345.68".
 */

import { getMessages } from "../i18n";
import type { Locale } from "../i18n/locale";

export const DASH = "—";

/** Intl locale behind each site locale. */
export const INTL_LOCALES: Record<Locale, string> = { en: "en-US", es: "es-ES", pt: "pt-BR" };
/** Group separator replacing Intl's, where the locale's own reads poorly. */
const GROUP_SEPARATORS: Partial<Record<Locale, string>> = { es: "\u00A0", pt: "\u00A0" };

function isNumber(value: unknown): value is number {
  return typeof value === "number" && Number.isFinite(value);
}

function parseDate(iso: string | null | undefined): Date | null {
  if (!iso) return null;
  // Bare dates are calendar days: anchor them at UTC midnight and format in UTC.
  const d = new Date(/^\d{4}-\d{2}-\d{2}$/.test(iso) ? `${iso}T00:00:00Z` : iso);
  return Number.isNaN(d.getTime()) ? null : d;
}

function createFormat(locale: Locale) {
  const intlLocale = INTL_LOCALES[locale];
  const groupSeparator = GROUP_SEPARATORS[locale];
  const formatters = new Map<string, Intl.NumberFormat>();

  function numberFormat(options: Intl.NumberFormatOptions): Intl.NumberFormat {
    const key = JSON.stringify(options);
    let nf = formatters.get(key);
    if (!nf) {
      nf = new Intl.NumberFormat(intlLocale, { useGrouping: "always", ...options });
      formatters.set(key, nf);
    }
    return nf;
  }

  function formatWith(options: Intl.NumberFormatOptions, value: number): string {
    const nf = numberFormat(options);
    if (groupSeparator === undefined) return nf.format(value);
    return nf
      .formatToParts(value)
      .map((part) => (part.type === "group" ? groupSeparator : part.value))
      .join("");
  }

  let currencyNames: Intl.DisplayNames | null | undefined;

  /** "SEK · Swedish krona"; just the code when Intl has no name for it. */
  function currencyOption(code: string): string {
    if (currencyNames === undefined) {
      try {
        currencyNames = new Intl.DisplayNames(intlLocale, { type: "currency", fallback: "none" });
      } catch {
        currencyNames = null;
      }
    }
    let name: string | undefined;
    try {
      name = currencyNames?.of(code);
    } catch {
      name = undefined;
    }
    return name && name !== code ? `${code} · ${name}` : code;
  }

  function formatNumber(value: number | null | undefined, digits = 2): string {
    if (!isNumber(value)) return DASH;
    return formatWith({ minimumFractionDigits: digits, maximumFractionDigits: digits }, value);
  }

  function currencyFormat(
    value: number,
    currency: string,
    extra: Intl.NumberFormatOptions = {},
  ): string {
    try {
      return formatWith({ style: "currency", currency, ...extra }, value);
    } catch {
      // Unknown ISO code: still show something readable instead of crashing the build.
      return `${formatNumber(value, 2)} ${currency}`;
    }
  }

  /** Computed amounts (conversions, totals): always with the currency's decimals. */
  function formatMoney(value: number | null | undefined, currency: string): string {
    return isNumber(value) ? currencyFormat(value, currency) : DASH;
  }

  /** List prices as published: "139 SEK", "26 900 COP", but "9,99 €". */
  function formatPrice(value: number | null | undefined, currency: string): string {
    return isNumber(value)
      ? currencyFormat(value, currency, { trailingZeroDisplay: "stripIfInteger" })
      : DASH;
  }

  /** Savings and differences: explicit sign, "+12,30 SEK" / "-4,00 €"; zero has no sign. */
  function formatSignedMoney(value: number | null | undefined, currency: string): string {
    return isNumber(value) ? currencyFormat(value, currency, { signDisplay: "exceptZero" }) : DASH;
  }

  /** `pct` is a percentage (7.5 = 7,5 %), as in the data files. */
  function formatPercent(
    pct: number | null | undefined,
    { signed = false, digits = 1 }: { signed?: boolean; digits?: number } = {},
  ): string {
    if (!isNumber(pct)) return DASH;
    return formatWith(
      {
        style: "percent",
        maximumFractionDigits: digits,
        signDisplay: signed ? "exceptZero" : "auto",
      },
      pct / 100,
    );
  }

  /** "8 oct 2026" */
  function formatDate(iso: string | null | undefined): string {
    const d = parseDate(iso);
    if (!d) return DASH;
    return new Intl.DateTimeFormat(intlLocale, { dateStyle: "medium", timeZone: "UTC" }).format(d);
  }

  /** "8 de octubre de 2026" */
  function formatDateLong(iso: string | null | undefined): string {
    const d = parseDate(iso);
    if (!d) return DASH;
    return new Intl.DateTimeFormat(intlLocale, { dateStyle: "long", timeZone: "UTC" }).format(d);
  }

  /** "8 oct 2026, 04:17 UTC" */
  function formatDateTime(iso: string | null | undefined): string {
    const d = parseDate(iso);
    if (!d) return DASH;
    const text = new Intl.DateTimeFormat(intlLocale, {
      dateStyle: "medium",
      timeStyle: "short",
      timeZone: "UTC",
    }).format(d);
    return `${text} UTC`;
  }

  function periodSuffix(period: string): string {
    return getMessages(locale).periods[period] ?? `/${period}`;
  }

  /** "Colombia, Chile y Brasil" */
  function formatList(items: readonly string[]): string {
    try {
      return new Intl.ListFormat(intlLocale, { type: "conjunction" }).format(items);
    } catch {
      return items.join(", ");
    }
  }

  return {
    locale,
    intlLocale,
    currencyOption,
    formatMoney,
    formatPrice,
    formatSignedMoney,
    formatNumber,
    formatPercent,
    formatDate,
    formatDateLong,
    formatDateTime,
    periodSuffix,
    formatList,
    /** Sort comparator for names in this locale. */
    compareNames: (a: string, b: string) => a.localeCompare(b, intlLocale),
  };
}

export type Format = ReturnType<typeof createFormat>;

const cache = new Map<Locale, Format>();

/** The formatting helpers of one locale (cached). */
export function formatFor(locale: Locale): Format {
  let format = cache.get(locale);
  if (!format) {
    format = createFormat(locale);
    cache.set(locale, format);
  }
  return format;
}

/** Regional-indicator flag ("CO" -> 🇨🇴). Decorative only: always show the name next to it. */
export function flagEmoji(cc: string): string {
  if (!/^[A-Za-z]{2}$/.test(cc)) return "";
  const base = 0x1f1e6 - "A".charCodeAt(0);
  return String.fromCodePoint(...[...cc.toUpperCase()].map((c) => base + c.charCodeAt(0)));
}

/** Host of a link for display ("www." dropped); the text itself when it is not a URL. */
export function hostOf(url: string): string {
  try {
    return new URL(url).hostname.replace(/^www\./, "");
  } catch {
    return url;
  }
}

/**
 * The document a link names, for telling two editions of a price list apart: the last segment of
 * its path ("…/TSP-4667_19_0701.pdf" -> "TSP-4667_19_0701.pdf"), the host when the path has
 * none, the text itself when it is not a URL.
 */
export function documentName(url: string): string {
  let segment: string | undefined;
  try {
    segment = new URL(url).pathname.split("/").filter(Boolean).pop();
  } catch {
    return url;
  }
  if (!segment) return hostOf(url);
  try {
    return decodeURIComponent(segment);
  } catch {
    return segment;
  }
}

/** Whether a link points to a PDF document (by its path, the only thing the data says). */
export function isPdfUrl(url: string): boolean {
  try {
    return new URL(url).pathname.toLowerCase().endsWith(".pdf");
  } catch {
    return false;
  }
}

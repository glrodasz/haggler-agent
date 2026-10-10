/**
 * Display formatting (es-ES). Intl does the heavy lifting; these helpers only add the
 * conventions the site needs: "—" for missing values, list prices without ",00", UTC dates
 * (ISO dates in the data have no time zone and must not shift a day in the browser).
 */

import { es } from "../i18n/es";

export const LOCALE = "es-ES";
export const DASH = "—";

const formatters = new Map<string, Intl.NumberFormat>();

function numberFormat(options: Intl.NumberFormatOptions): Intl.NumberFormat {
  const key = JSON.stringify(options);
  let nf = formatters.get(key);
  if (!nf) {
    nf = new Intl.NumberFormat(LOCALE, options);
    formatters.set(key, nf);
  }
  return nf;
}

function isNumber(value: unknown): value is number {
  return typeof value === "number" && Number.isFinite(value);
}

function currencyFormat(
  value: number,
  currency: string,
  extra: Intl.NumberFormatOptions = {},
): string {
  try {
    return numberFormat({ style: "currency", currency, ...extra }).format(value);
  } catch {
    // Unknown ISO code: still show something readable instead of crashing the build.
    return `${formatNumber(value, 2)} ${currency}`;
  }
}

/** Computed amounts (conversions, totals): always with the currency's decimals. */
export function formatMoney(value: number | null | undefined, currency: string): string {
  return isNumber(value) ? currencyFormat(value, currency) : DASH;
}

/** List prices as published: "139 SEK", "26.900 COP", but "9,99 €". */
export function formatPrice(value: number | null | undefined, currency: string): string {
  return isNumber(value)
    ? currencyFormat(value, currency, { trailingZeroDisplay: "stripIfInteger" })
    : DASH;
}

/** Savings and differences: explicit sign, "+12,30 SEK" / "-4,00 €"; zero has no sign. */
export function formatSignedMoney(value: number | null | undefined, currency: string): string {
  return isNumber(value) ? currencyFormat(value, currency, { signDisplay: "exceptZero" }) : DASH;
}

export function formatNumber(value: number | null | undefined, digits = 2): string {
  if (!isNumber(value)) return DASH;
  return numberFormat({ minimumFractionDigits: digits, maximumFractionDigits: digits }).format(
    value,
  );
}

/** `pct` is a percentage (7.5 = 7,5 %), as in the data files. */
export function formatPercent(
  pct: number | null | undefined,
  { signed = false, digits = 1 }: { signed?: boolean; digits?: number } = {},
): string {
  if (!isNumber(pct)) return DASH;
  return numberFormat({
    style: "percent",
    maximumFractionDigits: digits,
    signDisplay: signed ? "exceptZero" : "auto",
  }).format(pct / 100);
}

function parseDate(iso: string | null | undefined): Date | null {
  if (!iso) return null;
  // Bare dates are calendar days: anchor them at UTC midnight and format in UTC.
  const d = new Date(/^\d{4}-\d{2}-\d{2}$/.test(iso) ? `${iso}T00:00:00Z` : iso);
  return Number.isNaN(d.getTime()) ? null : d;
}

/** "8 oct 2026" */
export function formatDate(iso: string | null | undefined): string {
  const d = parseDate(iso);
  if (!d) return DASH;
  return new Intl.DateTimeFormat(LOCALE, { dateStyle: "medium", timeZone: "UTC" }).format(d);
}

/** "8 de octubre de 2026" */
export function formatDateLong(iso: string | null | undefined): string {
  const d = parseDate(iso);
  if (!d) return DASH;
  return new Intl.DateTimeFormat(LOCALE, { dateStyle: "long", timeZone: "UTC" }).format(d);
}

/** "8 oct 2026, 04:17 UTC" */
export function formatDateTime(iso: string | null | undefined): string {
  const d = parseDate(iso);
  if (!d) return DASH;
  const text = new Intl.DateTimeFormat(LOCALE, {
    dateStyle: "medium",
    timeStyle: "short",
    timeZone: "UTC",
  }).format(d);
  return `${text} UTC`;
}

export function periodSuffix(period: string): string {
  return es.periods[period] ?? `/${period}`;
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

/** Whether a link points to a PDF document (by its path, the only thing the data says). */
export function isPdfUrl(url: string): boolean {
  try {
    return new URL(url).pathname.toLowerCase().endsWith(".pdf");
  } catch {
    return false;
  }
}

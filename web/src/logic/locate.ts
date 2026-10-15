/**
 * Where the visitor probably is, to start the comparator from their country instead of the
 * site's. Two ways, neither of which blocks the page:
 *
 *  - guessFromBrowser: the browser's time zone, else the region of its preferred languages.
 *    Nothing leaves the browser, so it runs on arrival.
 *  - lookupIp: asks a free geo-IP service (GEO_IP_URL) for the country of the visitor's IP. It
 *    sends the IP to that service, so it only runs when the visitor asks for it.
 *
 * Either answer is only a starting point: the visitor can change the residence at any time.
 */

export type LocateSource = "ip" | "timezone" | "language";

export interface Located {
  cc: string;
  source: LocateSource;
}

/** No key, CORS enabled, answers {"ip": "...", "country": "SE"}. */
export const GEO_IP_URL = "https://api.country.is/";
export const GEO_IP_HOST = "country.is";
const GEO_IP_TIMEOUT_MS = 3000;

/**
 * IANA time zones -> country. Only zones that identify one country; shared ones (Etc/UTC,
 * Europe/London for the Crown dependencies...) resolve to the main country or are left out.
 */
const TIME_ZONES: Record<string, string> = {
  "Africa/Algiers": "DZ",
  "Africa/Cairo": "EG",
  "Africa/Casablanca": "MA",
  "Africa/Johannesburg": "ZA",
  "Africa/Lagos": "NG",
  "Africa/Nairobi": "KE",
  "Africa/Tunis": "TN",
  "America/Argentina/Buenos_Aires": "AR",
  "America/Buenos_Aires": "AR",
  "America/Asuncion": "PY",
  "America/Bogota": "CO",
  "America/Caracas": "VE",
  "America/Chicago": "US",
  "America/Costa_Rica": "CR",
  "America/Denver": "US",
  "America/El_Salvador": "SV",
  "America/Guatemala": "GT",
  "America/Guayaquil": "EC",
  "America/Halifax": "CA",
  "America/Havana": "CU",
  "America/La_Paz": "BO",
  "America/Lima": "PE",
  "America/Los_Angeles": "US",
  "America/Managua": "NI",
  "America/Mexico_City": "MX",
  "America/Monterrey": "MX",
  "America/Cancun": "MX",
  "America/Tijuana": "MX",
  "America/Montevideo": "UY",
  "America/New_York": "US",
  "America/Panama": "PA",
  "America/Phoenix": "US",
  "America/Anchorage": "US",
  "Pacific/Honolulu": "US",
  "America/Puerto_Rico": "PR",
  "America/Santiago": "CL",
  "America/Santo_Domingo": "DO",
  "America/Sao_Paulo": "BR",
  "America/Tegucigalpa": "HN",
  "America/Toronto": "CA",
  "America/Vancouver": "CA",
  "America/Edmonton": "CA",
  "America/Winnipeg": "CA",
  "Asia/Bangkok": "TH",
  "Asia/Calcutta": "IN",
  "Asia/Kolkata": "IN",
  "Asia/Dubai": "AE",
  "Asia/Ho_Chi_Minh": "VN",
  "Asia/Hong_Kong": "HK",
  "Asia/Jakarta": "ID",
  "Asia/Jerusalem": "IL",
  "Asia/Karachi": "PK",
  "Asia/Kuala_Lumpur": "MY",
  "Asia/Manila": "PH",
  "Asia/Riyadh": "SA",
  "Asia/Seoul": "KR",
  "Asia/Shanghai": "CN",
  "Asia/Singapore": "SG",
  "Asia/Taipei": "TW",
  "Asia/Tokyo": "JP",
  "Australia/Sydney": "AU",
  "Australia/Melbourne": "AU",
  "Australia/Brisbane": "AU",
  "Australia/Perth": "AU",
  "Australia/Adelaide": "AU",
  "Pacific/Auckland": "NZ",
  "Atlantic/Canary": "ES",
  "Atlantic/Reykjavik": "IS",
  "Europe/Amsterdam": "NL",
  "Europe/Athens": "GR",
  "Europe/Belgrade": "RS",
  "Europe/Berlin": "DE",
  "Europe/Bratislava": "SK",
  "Europe/Brussels": "BE",
  "Europe/Bucharest": "RO",
  "Europe/Budapest": "HU",
  "Europe/Copenhagen": "DK",
  "Europe/Dublin": "IE",
  "Europe/Helsinki": "FI",
  "Europe/Istanbul": "TR",
  "Europe/Kiev": "UA",
  "Europe/Kyiv": "UA",
  "Europe/Lisbon": "PT",
  "Europe/Ljubljana": "SI",
  "Europe/London": "GB",
  "Europe/Luxembourg": "LU",
  "Europe/Madrid": "ES",
  "Europe/Malta": "MT",
  "Europe/Moscow": "RU",
  "Europe/Nicosia": "CY",
  "Europe/Oslo": "NO",
  "Europe/Paris": "FR",
  "Europe/Prague": "CZ",
  "Europe/Riga": "LV",
  "Europe/Rome": "IT",
  "Europe/Sofia": "BG",
  "Europe/Stockholm": "SE",
  "Europe/Tallinn": "EE",
  "Europe/Vienna": "AT",
  "Europe/Vilnius": "LT",
  "Europe/Warsaw": "PL",
  "Europe/Zagreb": "HR",
  "Europe/Zurich": "CH",
};

export function countryOfTimeZone(zone: string | undefined): string | null {
  return (zone && TIME_ZONES[zone]) || null;
}

/** "es-CO" -> "CO"; null for a language without a region ("es") or a non-country one ("419"). */
export function countryOfLanguage(tag: string): string | null {
  const region = tag.split(/[-_]/).find((part, i) => i > 0 && /^[A-Za-z]{2}$/.test(part));
  return region ? region.toUpperCase() : null;
}

export interface BrowserHints {
  timeZone?: string;
  languages?: readonly string[];
}

function browserHints(): BrowserHints {
  let timeZone: string | undefined;
  try {
    timeZone = Intl.DateTimeFormat().resolvedOptions().timeZone;
  } catch {
    timeZone = undefined;
  }
  const languages =
    typeof navigator === "undefined"
      ? []
      : navigator.languages?.length
        ? navigator.languages
        : [navigator.language];
  return { timeZone, languages };
}

/** The visitor's country from the browser alone (time zone first), or null. */
export function guessFromBrowser(hints: BrowserHints = browserHints()): Located | null {
  const byZone = countryOfTimeZone(hints.timeZone);
  if (byZone) return { cc: byZone, source: "timezone" };
  for (const tag of hints.languages ?? []) {
    const cc = countryOfLanguage(tag);
    if (cc) return { cc, source: "language" };
  }
  return null;
}

/** The country of the visitor's IP per GEO_IP_URL, or null on any failure or timeout. */
export async function lookupIp(fetcher: typeof fetch = fetch): Promise<Located | null> {
  const controller = new AbortController();
  const timer = setTimeout(() => controller.abort(), GEO_IP_TIMEOUT_MS);
  try {
    const res = await fetcher(GEO_IP_URL, {
      signal: controller.signal,
      credentials: "omit",
      referrerPolicy: "no-referrer",
    });
    if (!res.ok) return null;
    const body = (await res.json()) as { country?: unknown };
    const cc = typeof body.country === "string" ? body.country.toUpperCase() : "";
    return /^[A-Z]{2}$/.test(cc) ? { cc, source: "ip" } : null;
  } catch {
    return null;
  } finally {
    clearTimeout(timer);
  }
}

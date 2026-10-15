import { describe, expect, it } from "vitest";
import {
  currencyOption,
  DASH,
  documentName,
  flagEmoji,
  formatDate,
  formatDateLong,
  formatDateTime,
  formatMoney,
  formatNumber,
  formatPercent,
  formatPrice,
  formatSignedMoney,
  hostOf,
  isPdfUrl,
  periodSuffix,
} from "./format";

// Intl separates number and symbol with (narrow) no-break spaces; compare with plain spaces.
const plain = (s: string) => s.replace(/[  ]/g, " ");

describe("formatMoney", () => {
  it("uses es-ES conventions with currency decimals", () => {
    expect(plain(formatMoney(9.99, "EUR"))).toBe("9,99 €");
    expect(plain(formatMoney(139, "SEK"))).toBe("139,00 SEK");
    expect(plain(formatMoney(12345.678, "EUR"))).toBe("12.345,68 €");
    expect(plain(formatMoney(12.99, "USD"))).toBe("12,99 US$");
  });

  it("renders a dash for missing values", () => {
    expect(formatMoney(null, "EUR")).toBe(DASH);
    expect(formatMoney(undefined, "EUR")).toBe(DASH);
    expect(formatMoney(Number.NaN, "EUR")).toBe(DASH);
  });

  it("does not crash on an invalid currency code", () => {
    expect(plain(formatMoney(5, "NOT-A-CODE"))).toBe("5,00 NOT-A-CODE");
  });
});

describe("formatPrice", () => {
  it("drops ,00 on whole list prices but keeps real cents", () => {
    expect(plain(formatPrice(139, "SEK"))).toBe("139 SEK");
    expect(plain(formatPrice(26900, "COP"))).toBe("26.900 COP");
    expect(plain(formatPrice(129.9, "SEK"))).toBe("129,90 SEK");
    expect(plain(formatPrice(7.99, "EUR"))).toBe("7,99 €");
  });
});

describe("formatSignedMoney", () => {
  it("shows the sign of savings and none for zero", () => {
    expect(plain(formatSignedMoney(12.3, "SEK"))).toBe("+12,30 SEK");
    expect(plain(formatSignedMoney(-4, "EUR"))).toBe("-4,00 €");
    expect(plain(formatSignedMoney(0, "EUR"))).toBe("0,00 €");
  });
});

describe("formatPercent and formatNumber", () => {
  it("takes percentages, not fractions", () => {
    expect(plain(formatPercent(7.75))).toBe("7,8 %");
    expect(plain(formatPercent(2))).toBe("2 %");
    expect(plain(formatPercent(30, { digits: 0 }))).toBe("30 %");
    expect(plain(formatPercent(-4.22, { signed: true }))).toBe("-4,2 %");
    expect(plain(formatPercent(16.67, { signed: true }))).toBe("+16,7 %");
    expect(formatPercent(null)).toBe(DASH);
  });

  it("formats plain numbers", () => {
    expect(formatNumber(1234.5)).toBe("1234,50");
    expect(formatNumber(12345.5, 0)).toBe("12.346");
  });
});

describe("dates", () => {
  it("treats bare ISO dates as calendar days (no time-zone shift)", () => {
    expect(formatDate("2026-10-08")).toBe("8 oct 2026");
    expect(formatDate("2026-01-01")).toBe("1 ene 2026");
    expect(formatDateLong("2026-10-08")).toBe("8 de octubre de 2026");
  });

  it("formats timestamps in UTC", () => {
    expect(plain(formatDateTime("2026-10-08T05:21:14Z"))).toBe("8 oct 2026, 5:21 UTC");
  });

  it("returns a dash for empty or invalid input", () => {
    expect(formatDate(null)).toBe(DASH);
    expect(formatDate("")).toBe(DASH);
    expect(formatDate("not a date")).toBe(DASH);
  });
});

describe("periodSuffix and flagEmoji", () => {
  it("maps billing periods to Spanish suffixes", () => {
    expect(periodSuffix("month")).toBe("/mes");
    expect(periodSuffix("year")).toBe("/año");
    expect(periodSuffix("quarter")).toBe("/trimestre");
    expect(periodSuffix("week")).toBe("/week");
  });

  it("builds regional-indicator flags", () => {
    expect(flagEmoji("CO")).toBe("🇨🇴");
    expect(flagEmoji("se")).toBe("🇸🇪");
    expect(flagEmoji("XYZ")).toBe("");
    expect(flagEmoji("")).toBe("");
  });
});

describe("documentName", () => {
  it("names the file a link ends in, else its host", () => {
    expect(documentName("https://www.telia.se/dam/TSP-4667_19_0701.pdf")).toBe(
      "TSP-4667_19_0701.pdf",
    );
    expect(documentName("https://www.telia.se/priser/")).toBe("priser");
    expect(documentName("https://www.telia.se/lista%20precios.pdf?v=2")).toBe("lista precios.pdf");
    expect(documentName("https://www.telia.se")).toBe("telia.se");
    expect(documentName("https://www.telia.se/%E0%A4%A.pdf")).toBe("%E0%A4%A.pdf");
    expect(documentName("no es una url")).toBe("no es una url");
  });
});

describe("hostOf and isPdfUrl", () => {
  it("shows the host of a link without www", () => {
    expect(hostOf("https://www.telia.se/mobilabonnemang/")).toBe("telia.se");
    expect(hostOf("https://static3.go3.tv/file/list.pdf")).toBe("static3.go3.tv");
    expect(hostOf("no es una url")).toBe("no es una url");
  });

  it("recognises PDF documents by their path", () => {
    expect(isPdfUrl("https://static3.go3.tv/file/go3/pricelist.PDF")).toBe(true);
    expect(isPdfUrl("https://example.com/precios.pdf?v=2#p3")).toBe(true);
    expect(isPdfUrl("https://example.com/pdf/planes")).toBe(false);
    expect(isPdfUrl("precios.pdf")).toBe(false);
  });
});

describe("currencyOption", () => {
  it("adds the Spanish currency name after the code", () => {
    expect(currencyOption("SEK")).toBe("SEK · corona sueca");
    expect(currencyOption("USD")).toBe("USD · dólar estadounidense");
  });

  it("falls back to the bare code when Intl has no name", () => {
    expect(currencyOption("XQZ")).toBe("XQZ");
  });
});

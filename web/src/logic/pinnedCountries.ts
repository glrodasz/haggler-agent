/**
 * Rows the countries table pins above the alphabetical list: the visitor's own country (where
 * they are, located by logic/visitor.ts) and, when it differs, the residence of the profile they
 * saved in the comparator. Countries missing from the table are skipped.
 */

export type PinTag = "yours" | "comparison";

export interface Pin {
  cc: string;
  tag: PinTag;
}

export function pinnedCountries(
  located: string | null,
  saved: string | null,
  inTable: ReadonlySet<string>,
): Pin[] {
  const pins: Pin[] = [];
  if (located && inTable.has(located)) pins.push({ cc: located, tag: "yours" });
  if (saved && saved !== located && inTable.has(saved)) pins.push({ cc: saved, tag: "comparison" });
  return pins;
}

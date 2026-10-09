/**
 * Compact encoding of the comparator offers embedded in the home page. Repeating JSON keys
 * per offer roughly triples the payload; a field list + positional rows keeps a ~1.5k-offer
 * dataset small enough to inline. obs_key is rebuilt from its parts (svc:plan:cc:channel:period).
 * The feasibility records the comparator relabels offers with (verdicts.ts) travel the same way.
 */

import type { CompareOfferFields } from "./compare";
import type { VerdictRecord } from "./verdicts";

export const PACK_FIELDS = [
  "service_id",
  "plan_id",
  "country",
  "channel",
  "currency",
  "amount",
  "billing_period",
  "monthly_equiv",
  "source_kind",
  "status",
  "label",
  "rank",
  "eu_badge",
] as const;

type PackField = (typeof PACK_FIELDS)[number];
type PackValue = CompareOfferFields[PackField];

export interface PackedOffers {
  v: 1;
  fields: PackField[];
  rows: PackValue[][];
}

export function obsKeyOf(
  o: Pick<CompareOfferFields, "service_id" | "plan_id" | "country" | "channel" | "billing_period">,
): string {
  return `${o.service_id}:${o.plan_id}:${o.country}:${o.channel}:${o.billing_period}`;
}

export function packOffers(offers: readonly CompareOfferFields[]): PackedOffers {
  return {
    v: 1,
    fields: [...PACK_FIELDS],
    rows: offers.map((o) => PACK_FIELDS.map((f) => o[f])),
  };
}

/** Tolerant decode: unknown shapes yield an empty list instead of breaking the page. */
export function unpackOffers(value: unknown): CompareOfferFields[] {
  if (!value || typeof value !== "object") return [];
  const packed = value as Partial<PackedOffers>;
  if (!Array.isArray(packed.fields) || !Array.isArray(packed.rows)) return [];
  const fields = packed.fields;
  return packed.rows
    .filter((row): row is PackValue[] => Array.isArray(row) && row.length === fields.length)
    .map((row) => {
      const o = Object.fromEntries(fields.map((f, i) => [f, row[i]])) as Omit<
        CompareOfferFields,
        "obs_key"
      >;
      return { ...o, obs_key: obsKeyOf(o) };
    });
}

export const VERDICT_FIELDS = [
  "profile",
  "service_id",
  "target_country",
  "provenance",
  "label",
  "rank",
  "eu_badge",
] as const satisfies readonly (keyof VerdictRecord)[];

type VerdictField = (typeof VERDICT_FIELDS)[number];

export interface PackedVerdicts {
  v: 1;
  fields: VerdictField[];
  rows: VerdictRecord[VerdictField][][];
}

export function packVerdicts(records: readonly VerdictRecord[]): PackedVerdicts {
  return {
    v: 1,
    fields: [...VERDICT_FIELDS],
    rows: records.map((r) => VERDICT_FIELDS.map((f) => r[f])),
  };
}

/** Tolerant decode, like unpackOffers. */
export function unpackVerdicts(value: unknown): VerdictRecord[] {
  if (!value || typeof value !== "object") return [];
  const packed = value as Partial<PackedVerdicts>;
  if (!Array.isArray(packed.fields) || !Array.isArray(packed.rows)) return [];
  const fields = packed.fields;
  return packed.rows
    .filter((row) => Array.isArray(row) && row.length === fields.length)
    .map((row) => Object.fromEntries(fields.map((f, i) => [f, row[i]])) as VerdictRecord);
}

/** JSON safe to place inside <script type="application/json"> (cannot close the element). */
export function inlineJson(value: unknown): string {
  return JSON.stringify(value).replace(/</g, "\\u003c");
}

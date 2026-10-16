/**
 * What a residence (and the documents the visitor holds) opens up for paying subscriptions:
 * the payment instruments in feasibility.json `instruments` the visitor can open online. One that
 * needs a document the visitor did not mark is left out (Bancolombia for a non-Colombian).
 *
 * Mirrors the eligibility the evaluator uses when it counts obtainable instruments
 * (haggler.research.evaluate): "ANY" residence, the residence country itself, or "EEA" for a
 * residence in the EU/EEA; and at least one ID document of `required_id_countries` when it is
 * not empty. Opening one never changes where the visitor lives: the labels already count them.
 */

import type { PaymentInstrumentView, ProfileCapabilities } from "./types";

export interface InstrumentAdvice {
  /** Can be opened online with this residence and these documents. */
  open: PaymentInstrumentView[];
}

export function residenceQualifies(
  instrument: Pick<PaymentInstrumentView, "eligible_residence">,
  residence: string,
  eea: ReadonlySet<string>,
): boolean {
  return instrument.eligible_residence.some(
    (e) => e === "ANY" || e === residence || (e === "EEA" && eea.has(residence)),
  );
}

export function instrumentAdvice(
  instruments: readonly PaymentInstrumentView[],
  caps: Pick<ProfileCapabilities, "residence_country" | "id_document_countries">,
  eea: ReadonlySet<string>,
): InstrumentAdvice {
  const docs = new Set(caps.id_document_countries);
  const out: InstrumentAdvice = { open: [] };
  for (const i of instruments) {
    if (!residenceQualifies(i, caps.residence_country, eea)) continue;
    const required = i.required_id_countries;
    if (required.length === 0 || required.some((cc) => docs.has(cc))) out.open.push(i);
  }
  const byName = (a: PaymentInstrumentView, b: PaymentInstrumentView) =>
    a.name.localeCompare(b.name, "es");
  out.open.sort(byName);
  return out;
}

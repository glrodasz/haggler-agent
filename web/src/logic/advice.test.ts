import { describe, expect, it } from "vitest";
import { instrumentAdvice } from "./advice";
import type { PaymentInstrumentView } from "./types";

const inst = (id: string, eligible: string[], required: string[] = []): PaymentInstrumentView => ({
  id,
  name: id,
  issuer_country: "LT",
  iban_country: null,
  eligible_residence: eligible,
  required_id_countries: required,
  card_country: null,
  currencies: ["EUR"],
  url: null,
  notes_es: "",
});

const EEA = new Set(["SE", "LT", "DE"]);
const ALL = [inst("eea", ["EEA"]), inst("se", ["SE"]), inst("co", ["ANY"], ["CO"])];
const ids = (xs: { id: string }[]) => xs.map((x) => x.id);

describe("instrumentAdvice", () => {
  it("opens EEA and own-country accounts for an EEA resident", () => {
    const a = instrumentAdvice(
      ALL,
      { residence_country: "SE", id_document_countries: ["SE"] },
      EEA,
    );
    expect(ids(a.open)).toEqual(["eea", "se"]);
  });

  it("counts a held document of the required country", () => {
    const a = instrumentAdvice(
      ALL,
      { residence_country: "US", id_document_countries: ["US", "CO"] },
      EEA,
    );
    expect(ids(a.open)).toEqual(["co"]);
  });
});

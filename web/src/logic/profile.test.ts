import { describe, expect, it } from "vitest";
import {
  ALL_COUNTRY_CODES,
  changeResidence,
  countryOptions,
  decodeProfile,
  emptyTie,
  encodeProfile,
  fromCapabilities,
  toCapabilities,
  type VisitorProfile,
} from "./profile";
import type { ProfileCapabilities } from "./types";

const CO_IN_SE: ProfileCapabilities = {
  residence_country: "SE",
  id_document_countries: ["CO", "SE"],
  phone_countries: ["SE"],
  address_countries: ["SE"],
  card_countries: ["CO"],
  presence: { CO: "once" },
};

describe("profile builder", () => {
  it("assumes regular trips for a tie added in the form, but not in a link without the flag", () => {
    expect(emptyTie("CO").presence).toBe("periodic");
    expect(decodeProfile("SE.dp,CO.dc", ["SE"], new Set(["SE", "CO"]))?.ties[0].presence).toBe("none");
  });

  it("loads a curated profile into the form and back unchanged", () => {
    const v = fromCapabilities(CO_IN_SE);
    expect(v).toEqual({
      residence: "SE",
      residenceDoc: true,
      residencePhone: true,
      residenceCard: false,
      ties: [{ cc: "CO", doc: true, card: true, phone: false, address: false, presence: "once" }],
    });
    expect(toCapabilities(v)).toEqual(CO_IN_SE);
    // A profile without its own documents (synthesized by site-data) round-trips too.
    const bare = {
      ...CO_IN_SE,
      id_document_countries: [],
      phone_countries: [],
      card_countries: [],
      presence: {},
    };
    expect(toCapabilities(fromCapabilities(bare))).toEqual(bare);
  });

  it("moves the residence: new ID and phone assumed, the old tie dropped", () => {
    const moved = changeResidence(fromCapabilities(CO_IN_SE), "CO");
    expect(moved).toEqual({
      residence: "CO",
      residenceDoc: true,
      residencePhone: true,
      residenceCard: true,
      ties: [],
    });
    expect(toCapabilities(moved)).toMatchObject({
      residence_country: "CO",
      address_countries: ["CO"],
      card_countries: ["CO"],
      presence: {},
    });
  });

  it("round-trips through the URL form, skipping unknown and repeated countries", () => {
    const v: VisitorProfile = {
      residence: "SE",
      residenceDoc: true,
      residencePhone: false,
      residenceCard: true,
      ties: [
        { cc: "CO", doc: true, card: true, phone: false, address: false, presence: "once" },
        { cc: "US", doc: false, card: false, phone: true, address: true, presence: "periodic" },
        { cc: "AR", doc: false, card: false, phone: false, address: false, presence: "none" },
      ],
    };
    const raw = encodeProfile(v);
    expect(raw).toBe("SE.dc,CO.dco,US.par,AR");
    const countries = new Set(["SE", "CO", "US", "AR"]);
    expect(decodeProfile(raw, ["SE"], countries)).toEqual(v);
    expect(decodeProfile(raw, ["NO"], countries)).toBeNull();
    expect(decodeProfile("SE,ZZ.d,CO.d,CO.c,SE.d", ["SE"], countries)?.ties).toEqual([
      { cc: "CO", doc: true, card: false, phone: false, address: false, presence: "none" },
    ]);
  });

  it("names every country in Spanish, catalog names first, sorted", () => {
    const options = countryOptions(["SE", "CO", "AR"], { SE: "Suecia (catálogo)" });
    expect(options.map((o) => o.cc)).toEqual(["AR", "CO", "SE"]);
    expect(options[2]?.name).toBe("Suecia (catálogo)");
    expect(options[1]?.name).toBe("Colombia");
    expect(new Set(ALL_COUNTRY_CODES).size).toBe(ALL_COUNTRY_CODES.length);
    expect(ALL_COUNTRY_CODES.length).toBeGreaterThan(240);
  });
});

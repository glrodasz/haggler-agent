"""Who the site evaluates feasibility for (data/curated/profiles.yaml).

A profile lists what a person can honestly show: where they live, their citizenships, the
countries of their ID documents, phone numbers and addresses, the payment instruments they hold
and the countries they can be physically present in. The research evaluator
(haggler.research.evaluate) derives every verdict from these capabilities and the verified facts;
it never assumes a capability the profile does not list.

The evaluator only reads a profile through the predicate methods below (lives_in, has_document,
presence_in ...), never its fields: haggler.profile_rules swaps in a symbolic profile that
answers them one by one, so the site can evaluate any combination of residence and ties in the
browser with the same rules.
"""

from __future__ import annotations

from collections.abc import Iterable
from typing import TYPE_CHECKING, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

if TYPE_CHECKING:
    from haggler.models.curated import BundleVerdictRecord, FeasibilityFile, FeasibilityRecord

PresenceKind = Literal["once", "periodic"]
_CC = r"^[A-Z]{2}$"


class Profile(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    id: str = Field(pattern=r"^[a-z0-9_]+$")
    name_es: str
    description_es: str = ""
    residence_country: str = Field(pattern=_CC)
    citizenships: list[str] = Field(default_factory=list)
    id_document_countries: list[str] = Field(default_factory=list)
    phone_countries: list[str] = Field(default_factory=list)
    # Real addresses the person can give as billing address (the residence always counts).
    address_countries: list[str] = Field(default_factory=list)
    # Ids from data/curated/payment_instruments.yaml.
    instruments_held: list[str] = Field(default_factory=list)
    # Countries where a card the person holds was issued, when it is not a curated instrument
    # (the site's profile builder describes cards this way).
    card_countries: list[str] = Field(default_factory=list)
    # May the evaluator count instruments the person could open online (eligible for the
    # residence, documents they have)?
    obtain_remote_instruments: bool = True
    # Country -> how often the person can be there ("once" = a trip, "periodic" = regularly).
    presence: dict[str, PresenceKind] = Field(default_factory=dict)
    # Shown in the site's profile selector.
    public: bool = True

    def addresses(self) -> set[str]:
        return {self.residence_country, *self.address_countries}

    def documents(self) -> set[str]:
        return set(self.id_document_countries)

    def phones(self) -> set[str]:
        return set(self.phone_countries)

    def countries(self) -> set[str]:
        """Every country code the profile mentions (for integrity checks)."""
        return {
            self.residence_country,
            *self.citizenships,
            *self.id_document_countries,
            *self.phone_countries,
            *self.address_countries,
            *self.card_countries,
            *self.presence,
        }

    # ------------------------------------------------------------- evaluator predicates

    def lives_in(self, cc: str) -> bool:
        return self.residence_country == cc

    def lives_in_eea(self, eea: frozenset[str]) -> bool:
        return self.residence_country in eea

    def has_document(self, cc: str) -> bool:
        return cc in self.id_document_countries

    def has_any_document(self) -> bool:
        return bool(self.id_document_countries)

    def has_eea_document(self, eea: frozenset[str]) -> bool:
        return any(cc in eea for cc in self.id_document_countries)

    def has_residence_document(self) -> bool:
        return self.residence_country in self.id_document_countries

    def has_phone(self, cc: str) -> bool:
        return cc in self.phone_countries

    def has_address(self, cc: str) -> bool:
        return cc in self.addresses()

    def holds_card_of(self, cc: str) -> bool:
        """A card issued in `cc` that is not a curated instrument (see instruments_held)."""
        return cc in self.card_countries

    def presence_in(self, cc: str) -> PresenceKind | None:
        return self.presence.get(cc)

    def curated_record(
        self, feasibility: FeasibilityFile, service_id: str, target: str
    ) -> FeasibilityRecord | None:
        from haggler.feasibility import find_record

        return find_record(
            feasibility,
            service_id,
            target,
            residence_country=self.residence_country,
            profile=self.id,
        )

    def bundle_record(self, records: Iterable[BundleVerdictRecord]) -> BundleVerdictRecord | None:
        """This profile's curated verdict among a bundle's `verdicts`."""
        return next((v for v in records if v.profile == self.id), None)

    def capabilities(self, held_card_countries: Iterable[str] = ()) -> dict[str, object]:
        """What the evaluator can tell about this profile, as the site describes a visitor's
        own profile (haggler.profile_rules.matches): `held_card_countries` are the observed
        card countries of instruments_held."""
        return {
            "residence_country": self.residence_country,
            "id_document_countries": sorted(set(self.id_document_countries)),
            "phone_countries": sorted(set(self.phone_countries)),
            "address_countries": sorted(self.addresses()),
            "card_countries": sorted({*self.card_countries, *held_card_countries}),
            "presence": dict(sorted(self.presence.items())),
        }


class ProfilesFile(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    default: str
    profiles: list[Profile]

    @model_validator(mode="after")
    def _check(self) -> ProfilesFile:
        ids = [p.id for p in self.profiles]
        if len(ids) != len(set(ids)):
            raise ValueError("duplicate profile ids")
        if self.default not in ids:
            raise ValueError(f"default profile {self.default!r} is not defined")
        return self

    def get(self, profile_id: str) -> Profile | None:
        return next((p for p in self.profiles if p.id == profile_id), None)

    def profile(self, profile_id: str) -> Profile:
        p = self.get(profile_id)
        if p is None:
            raise KeyError(profile_id)
        return p

    def public(self) -> list[Profile]:
        """Public profiles, default first."""
        return sorted(
            (p for p in self.profiles if p.public or p.id == self.default),
            key=lambda p: (p.id != self.default, p.id),
        )

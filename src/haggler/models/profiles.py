"""Who the site evaluates feasibility for (data/curated/profiles.yaml).

A profile lists what a person can honestly show: where they live, their citizenships, the
countries of their ID documents, phone numbers and addresses, the payment instruments they hold
and the countries they can be physically present in. The research evaluator
(haggler.research.evaluate) derives every verdict from these capabilities and the verified facts;
it never assumes a capability the profile does not list.
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

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
            *self.presence,
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

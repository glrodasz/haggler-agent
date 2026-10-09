"""The closed vocabulary of research claims.

The research agents may only report facts from this table, each as (field, value) plus a literal
quote and its URL. Values are short codes (never prose), so a claim can be reviewed, hashed and
evaluated deterministically. `values` lists the allowed codes from the most favourable to the
least favourable for a buyer: when two verified claims of a single-valued field disagree, the
evaluator keeps the least favourable one.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import date
from typing import Literal

SubjectKind = Literal["service", "pair", "instrument"]
ValueType = Literal["enum", "bool", "int", "country", "text"]

_CC = re.compile(r"^[A-Z]{2}$")
BOOL_VALUES = ("false", "true")


@dataclass(frozen=True)
class FieldSpec:
    name: str
    level: SubjectKind
    value_type: ValueType
    label_es: str
    # Allowed codes, most favourable to the buyer first (enum and bool fields).
    values: tuple[str, ...] = ()
    value_labels_es: dict[str, str] = field(default_factory=dict)
    # Several claims with different values may hold at once (e.g. several enforcement events).
    multi: bool = False
    # The claim needs an event_date (YYYY-MM-DD).
    needs_date: bool = False
    # Extra codes accepted by country fields ("EEA", "ANY").
    extra_codes: tuple[str, ...] = ()
    # Shown to the agent: what the field means and when to report it.
    hint: str = ""


def _bool_labels(yes: str, no: str) -> dict[str, str]:
    return {"true": yes, "false": no}


FIELDS: tuple[FieldSpec, ...] = (
    # ------------------------------------------------------------------ service level
    FieldSpec(
        "account_country_basis",
        "service",
        "enum",
        "Qué fija el país de la cuenta",
        values=(
            "self_declared",
            "payment_method_country",
            "billing_address",
            "store_region",
            "phone_number",
            "signup_location",
        ),
        value_labels_es={
            "self_declared": "lo declara el usuario",
            "payment_method_country": "el país del medio de pago",
            "billing_address": "la dirección de facturación",
            "store_region": "la región de la tienda (App Store/Google Play)",
            "phone_number": "el número de teléfono",
            "signup_location": "la ubicación al registrarse",
        },
        multi=True,
        hint="What the service uses to decide which country an account belongs to.",
    ),
    FieldSpec(
        "residence_clause",
        "service",
        "enum",
        "Cláusula de residencia en los términos",
        values=(
            "primary_use_in_account_country",
            "account_country_must_match_residence",
            "must_reside",
        ),
        value_labels_es={
            "primary_use_in_account_country": "uso principal en el país de la cuenta",
            "account_country_must_match_residence": (
                "el país de la cuenta debe ser el de residencia"
            ),
            "must_reside": "solo para residentes del país",
        },
        hint="A terms-of-use clause tying the subscription to the country where the user lives "
        "or mainly uses it.",
    ),
    FieldSpec(
        "foreign_offer_allowed",
        "service",
        "enum",
        "Los términos permiten contratar desde otro país",
        values=("true",),
        value_labels_es={"true": "sí, explícitamente"},
        hint="Only when the terms EXPLICITLY allow subscribing to another country's offer.",
    ),
    FieldSpec(
        "region_change",
        "service",
        "enum",
        "Cambio de país de la cuenta",
        values=("self_service", "support_only", "new_account_required"),
        value_labels_es={
            "self_service": "lo hace el usuario",
            "support_only": "solo vía soporte",
            "new_account_required": "requiere una cuenta nueva",
        },
        hint="How an existing account moves to another country.",
    ),
    FieldSpec(
        "region_change_cooldown_days",
        "service",
        "int",
        "Espera mínima entre cambios de país (días)",
        hint="Minimum number of days between two country changes, as printed.",
    ),
    FieldSpec(
        "travel_use",
        "service",
        "enum",
        "Uso de viaje",
        values=("allowed", "limited_duration", "not_allowed"),
        value_labels_es={
            "allowed": "se puede usar al viajar",
            "limited_duration": "solo por un tiempo limitado",
            "not_allowed": "no se puede usar fuera del país",
        },
        hint="Whether the subscription keeps working while travelling abroad.",
    ),
    FieldSpec(
        "vpn_stance",
        "service",
        "enum",
        "Postura sobre VPN/proxy",
        values=("prohibited", "detected_and_blocked"),
        value_labels_es={
            "prohibited": "prohibido en los términos",
            "detected_and_blocked": "se detecta y bloquea",
        },
        hint="Only what the terms or the help pages say about VPNs or proxies. Never how to use "
        "or evade them.",
    ),
    FieldSpec(
        "enforcement_event",
        "service",
        "enum",
        "Control documentado",
        values=("location_verification", "forced_price_change", "suspension", "cancellation"),
        value_labels_es={
            "location_verification": "verificación de ubicación",
            "forced_price_change": "cambio forzado de precio o país",
            "suspension": "suspensiones",
            "cancellation": "cancelaciones",
        },
        multi=True,
        needs_date=True,
        hint="A dated event where the service acted against accounts registered in another "
        "country than the user's (event_date = the date the source gives).",
    ),
    # --------------------------------------------------------------------- pair level
    FieldSpec(
        "available",
        "pair",
        "bool",
        "El servicio se vende en el país",
        values=("true", "false"),
        value_labels_es=_bool_labels("sí", "no"),
    ),
    FieldSpec(
        "payment_card_country",
        "pair",
        "enum",
        "País de la tarjeta",
        values=("any_country", "must_match"),
        value_labels_es={
            "any_country": "acepta tarjetas de cualquier país",
            "must_match": "la tarjeta debe ser del país",
        },
        hint="Whether the payment card must be issued in the target country.",
    ),
    FieldSpec(
        "gift_cards",
        "pair",
        "enum",
        "Tarjetas regalo",
        values=("remote_purchase", "local_purchase_only", "none"),
        value_labels_es={
            "remote_purchase": "se compran en línea",
            "local_purchase_only": "solo en tiendas del país",
            "none": "no hay",
        },
    ),
    FieldSpec(
        "signup_location",
        "pair",
        "enum",
        "Ubicación al registrarse",
        values=("not_checked", "checked"),
        value_labels_es={"not_checked": "no se verifica", "checked": "se verifica"},
        hint="Whether signing up requires being in the country.",
    ),
    FieldSpec(
        "periodic_location_check",
        "pair",
        "bool",
        "Verificación periódica de ubicación",
        values=BOOL_VALUES,
        value_labels_es=_bool_labels("sí", "no"),
    ),
    FieldSpec(
        "billing_address",
        "pair",
        "enum",
        "Dirección de facturación",
        values=("not_required", "required"),
        value_labels_es={"not_required": "no se pide", "required": "se pide una del país"},
        hint="Whether a billing address in the target country is required.",
    ),
    FieldSpec(
        "local_id_required",
        "pair",
        "bool",
        "Documento local",
        values=BOOL_VALUES,
        value_labels_es=_bool_labels("se pide un documento del país", "no se pide"),
    ),
    FieldSpec(
        "local_phone_required",
        "pair",
        "bool",
        "Teléfono local",
        values=BOOL_VALUES,
        value_labels_es=_bool_labels("se pide un teléfono del país", "no se pide"),
    ),
    # --------------------------------------------------------------- instrument level
    FieldSpec("issuer_entity", "instrument", "text", "Entidad emisora"),
    FieldSpec(
        "card_issuing_country",
        "instrument",
        "country",
        "País emisor de la tarjeta",
        hint="Country where the payment card is issued (as stated by the issuer), ISO code.",
    ),
    FieldSpec("iban_country", "instrument", "country", "País del IBAN"),
    FieldSpec(
        "eligible_residence",
        "instrument",
        "country",
        "Residencias admitidas",
        multi=True,
        extra_codes=("EEA", "ANY"),
        hint="Country codes of residence that may open the account, or EEA / ANY.",
    ),
    FieldSpec(
        "onboarding_remote",
        "instrument",
        "bool",
        "Apertura 100 % en línea",
        values=("true", "false"),
        value_labels_es=_bool_labels("sí", "no"),
    ),
    FieldSpec(
        "id_documents",
        "instrument",
        "enum",
        "Documentos aceptados",
        values=("any_passport", "eea_id", "residence_permit", "issuer_country_id"),
        value_labels_es={
            "any_passport": "cualquier pasaporte",
            "eea_id": "documento de identidad del EEE",
            "residence_permit": "permiso de residencia",
            "issuer_country_id": "documento del país emisor",
        },
        multi=True,
    ),
    FieldSpec(
        "requires_local_address",
        "instrument",
        "bool",
        "Exige dirección en el país emisor",
        values=BOOL_VALUES,
        value_labels_es=_bool_labels("sí", "no"),
    ),
)

FIELD_SPECS: dict[str, FieldSpec] = {f.name: f for f in FIELDS}
FIELDS_BY_LEVEL: dict[str, tuple[str, ...]] = {
    level: tuple(f.name for f in FIELDS if f.level == level)
    for level in ("service", "pair", "instrument")
}
MAX_TEXT_VALUE = 120


def normalize_value(spec: FieldSpec, raw: str) -> str | None:
    """The canonical code of a raw value, or None when the field does not allow it."""
    value = raw.strip()
    if spec.value_type in ("enum", "bool"):
        value = value.lower()
        return value if value in spec.values else None
    if spec.value_type == "int":
        return str(int(value)) if re.fullmatch(r"\d{1,4}", value) else None
    if spec.value_type == "country":
        value = value.upper()
        if value in spec.extra_codes or _CC.fullmatch(value):
            return value
        return None
    # text
    value = " ".join(value.split())
    return value if 0 < len(value) <= MAX_TEXT_VALUE else None


def parse_event_date(raw: str) -> date | None:
    try:
        return date.fromisoformat(raw.strip())
    except ValueError:
        return None


def severity(spec: FieldSpec, value: str) -> int:
    """Position of the value in the favourable -> unfavourable order (unknown values last)."""
    try:
        return spec.values.index(value)
    except ValueError:
        return len(spec.values)


def value_label_es(field_name: str, value: str) -> str:
    spec = FIELD_SPECS.get(field_name)
    if spec is None:
        return value
    return spec.value_labels_es.get(value, value)


def vocabulary_prompt(level: SubjectKind) -> str:
    """The allowed fields of one level, as the agent's instruction lists them."""
    lines: list[str] = []
    for name in FIELDS_BY_LEVEL[level]:
        spec = FIELD_SPECS[name]
        if spec.value_type in ("enum", "bool"):
            allowed = " | ".join(spec.values)
        elif spec.value_type == "int":
            allowed = "an integer"
        elif spec.value_type == "country":
            extra = f" or {' / '.join(spec.extra_codes)}" if spec.extra_codes else ""
            allowed = f"an ISO 3166-1 alpha-2 code{extra}"
        else:
            allowed = f"short text (<= {MAX_TEXT_VALUE} chars)"
        flags = []
        if spec.multi:
            flags.append("may repeat with different values")
        if spec.needs_date:
            flags.append("event_date required")
        suffix = f" ({'; '.join(flags)})" if flags else ""
        hint = f" — {spec.hint}" if spec.hint else ""
        lines.append(f"- {name}: {allowed}{suffix}{hint}")
    return "\n".join(lines)

"""The closed vocabulary of research claims.

The research agents may only report facts from this table, each as (field, value) plus a literal
quote and its URL. Values are short codes (never prose), so a claim can be reviewed, hashed and
evaluated deterministically. `values` lists the allowed codes from the most favourable to the
least favourable for a buyer: when two verified claims of a single-valued field disagree, the
evaluator keeps the least favourable one.

Bundle subjects (level "bundle", research.kinds) report two classes of facts:
  * VALUES of bundles.yaml (yaml_value, official_only): price, introductory price and months,
    base plan price, contract months, end dates, included services, discounts, availability and
    the official price list. Only an official page of the bundle's country establishes one, the
    value check (research.values.check_value, kind `value_check`) must find the value in the
    quote, and it is accepted by EDITING bundles.yaml, never by a review decision (no job writes
    the YAML). Value forms: money "21900 COP/month" (haggler.money.format_money_code: digits, a
    decimal point, the ISO code, /month | /year | /quarter), date "2026-12-31", service_ref
    "disney_plus" or "disney_plus.standard_with_ads", service_pct "hbo_max:30", url the https
    URL of the document.
  * FEASIBILITY twins: the service and pair fields a bundle's terms can state, named bundle_<name>
    with the same values, order and semantics (dataclasses.replace of the original), plus
    bundle_local_line, bundle_payment_method and bundle_credit_check, whose values are those of
    the curated BundleRequirements (local_line, local_payment, credit_check) so curated and
    researched requirements compare without a translation. They are accepted by (id, hash) like
    every Phase 2 fact.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field, replace
from datetime import date
from decimal import Decimal, InvalidOperation
from typing import Any, Literal
from urllib.parse import urlsplit, urlunsplit

from haggler.i18n import Text
from haggler.money import format_money_code, parse_money_code
from haggler.research.kinds import KINDS, SubjectKind

ValueType = Literal[
    "enum",
    "bool",
    "int",
    "country",
    "text",
    # Bundle values (see the module docstring for their forms).
    "money",
    "date",
    "pct",
    "service_ref",
    "service_pct",
    "url",
]
# research.values.ValueKind of a value field (a plain string here: this module stays pure).
ValueCheckKind = Literal[
    "money",
    "intro_money",
    "base_money",
    "int",
    "pct",
    "months",
    "date_until",
    "component",
    "discount_pct",
    "url",
]

_CC = re.compile(r"^[A-Z]{2}$")
_SERVICE_REF = re.compile(r"^[a-z0-9_]+(?:\.[a-z0-9_]+)?$")
_SERVICE_ID = re.compile(r"^[a-z0-9_]+$")
_PCT = re.compile(r"^\d{1,3}(?:\.\d{1,2})?$")
_ISO_DATE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
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
    # Inclusive bounds of an int field (None = any 0-9999).
    int_range: tuple[int, int] | None = None
    # Only a page on the subject's official domains (for a bundle: its provider's domains for
    # the bundle's country) establishes this fact; any other source fails verification.
    official_only: bool = False
    # A value of bundles.yaml: accepted by editing the YAML, never by (id, hash).
    yaml_value: bool = False
    # The value check that must pass on the quote (research.values.check_value), if any.
    value_check: ValueCheckKind | None = None


def _bool_labels(yes: str, no: str) -> dict[str, str]:
    return {"true": yes, "false": no}


_PHASE2_FIELDS: tuple[FieldSpec, ...] = (
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


def _value(
    name: str, value_type: ValueType, label_es: str, check: ValueCheckKind | None, **kw: Any
) -> FieldSpec:
    """A bundle VALUE field: official pages only, accepted by editing bundles.yaml."""
    return FieldSpec(
        name,
        "bundle",
        value_type,
        label_es,
        official_only=True,
        yaml_value=True,
        value_check=check,
        **kw,
    )


_MONEY_HINT = (
    'as "<amount> <ISO currency>/<month|year>", digits and a decimal point only, e.g. '
    '"21900 COP/month" for "$ 21.900/mes"'
)
_BUNDLE_VALUES: tuple[FieldSpec, ...] = (
    _value(
        "bundle_price",
        "money",
        "Precio del paquete",
        "money",
        hint=f"The regular price (what is paid once any introductory offer ends), {_MONEY_HINT}.",
    ),
    _value(
        "bundle_intro_price",
        "money",
        "Precio de introducción",
        "intro_money",
        hint=f"The introductory price of the first months, {_MONEY_HINT}.",
    ),
    _value(
        "bundle_base_price",
        "money",
        "Precio del plan base",
        "base_money",
        hint=f"The price of the base plan the bundle is sold on top of, {_MONEY_HINT}.",
    ),
    _value(
        "bundle_intro_months",
        "int",
        "Meses del precio de introducción",
        "months",
        int_range=(1, 36),
        hint="How many months the introductory price lasts.",
    ),
    _value(
        "bundle_contract_months",
        "int",
        "Permanencia mínima (meses)",
        "months",
        # "Sin permanencia" states no number a value check could confirm: not reported.
        int_range=(1, 36),
        hint="Minimum contract length in months, as printed (a bundle without one: omit).",
    ),
    _value(
        "bundle_intro_until",
        "date",
        "Fin del precio de introducción",
        "date_until",
        hint="Last day the introductory price can be taken, as printed (with its year).",
    ),
    _value(
        "bundle_offer_until",
        "date",
        "Fin de la oferta",
        "date_until",
        hint="Last day the bundle is sold, as printed (with its year).",
    ),
    _value(
        "bundle_includes",
        "service_ref",
        "Servicio incluido",
        "component",
        multi=True,
        hint="A service the bundle includes at no extra cost, as one of the job's service ids, "
        'with ".plan_id" when the page names the plan.',
    ),
    _value(
        "bundle_discount",
        "service_pct",
        "Descuento en un servicio",
        "discount_pct",
        multi=True,
        hint='A percentage discount on a service, as "<service_id>:<percent>".',
    ),
    _value(
        "bundle_available",
        "bool",
        "La oferta se sigue vendiendo",
        None,
        values=("true", "false"),
        value_labels_es={"true": "sí", "false": "no"},
        hint="Whether the provider still sells the bundle.",
    ),
    _value(
        "bundle_price_list_url",
        "url",
        "Lista de precios oficial",
        "url",
        hint="The https URL of the official price list (often a PDF); the quote must come from "
        "that document.",
    ),
)


def _twin(name: str, **changes: object) -> FieldSpec:
    """The bundle twin of a service or pair field: same values, order and semantics."""
    base = next(f for f in _PHASE2_FIELDS if f.name == name)
    return replace(base, name=f"bundle_{name}", level="bundle", **changes)  # type: ignore[arg-type]


_BUNDLE_FEASIBILITY: tuple[FieldSpec, ...] = (
    _twin(
        "account_country_basis",
        hint="What the provider uses to decide which country a subscription belongs to.",
    ),
    _twin(
        "residence_clause",
        hint="A clause of the bundle's terms tying it to the country where the subscriber "
        "lives or mainly uses it.",
    ),
    _twin(
        "foreign_offer_allowed",
        hint="Only when the bundle's terms EXPLICITLY allow subscribing from another country.",
    ),
    _twin(
        "enforcement_event",
        hint="A dated event where the provider acted against subscribers of the bundle living "
        "in another country (event_date = the date the source gives).",
    ),
    _twin("travel_use", hint="Whether the bundle keeps working while travelling abroad."),
    _twin("signup_location", hint="Whether subscribing requires being in the bundle's country."),
    _twin(
        "billing_address",
        hint="Whether a billing address in the bundle's country is required.",
    ),
    _twin("local_id_required"),
    FieldSpec(
        "bundle_local_line",
        "bundle",
        "enum",
        "Línea telefónica del proveedor",
        values=("none", "mobile", "fixed", "both"),
        value_labels_es={
            "none": "no se pide una línea",
            "mobile": "se pide una línea móvil del país",
            "fixed": "se pide una línea fija del país",
            "both": "se piden una línea móvil y una fija del país",
        },
        hint="Whether the bundle is only sold with a mobile or fixed line of the provider.",
    ),
    FieldSpec(
        "bundle_payment_method",
        "bundle",
        "enum",
        "Medio de pago",
        values=("any_card", "local_card", "local_bank", "carrier_bill"),
        value_labels_es={
            "any_card": "acepta tarjetas de cualquier país",
            "local_card": "exige una tarjeta emitida en el país",
            "local_bank": "exige una cuenta bancaria del país",
            "carrier_bill": "se paga en la factura de una línea del país",
        },
        hint="How the bundle must be paid.",
    ),
    FieldSpec(
        "bundle_credit_check",
        "bundle",
        "bool",
        "Consulta de crédito",
        values=BOOL_VALUES,
        value_labels_es=_bool_labels("hay una consulta de crédito", "no hay"),
        hint="Whether subscribing involves a credit check.",
    ),
)

FIELDS: tuple[FieldSpec, ...] = (*_PHASE2_FIELDS, *_BUNDLE_VALUES, *_BUNDLE_FEASIBILITY)
FIELD_SPECS: dict[str, FieldSpec] = {f.name: f for f in FIELDS}
# Kind -> the fields its agent may report, in FIELDS order (kinds in registry order).
FIELDS_BY_LEVEL: dict[str, tuple[str, ...]] = {
    kind: tuple(f.name for f in FIELDS if f.level == spec.field_level)
    for kind, spec in KINDS.items()
}
MAX_TEXT_VALUE = 120


def _pct(raw: str) -> str | None:
    """A percentage 0 < p <= 100 as its plain form ("30", "12.5")."""
    if not _PCT.fullmatch(raw):
        return None
    try:
        pct = Decimal(raw)
    except InvalidOperation:
        return None
    if not 0 < pct <= 100:
        return None
    return str(int(pct)) if pct == pct.to_integral_value() else format(pct.normalize(), "f")


def _url(raw: str) -> str | None:
    """An https URL without its fragment, host in lower case (research.evidence.canonical_url)."""
    if any(ch.isspace() for ch in raw):
        return None
    parts = urlsplit(raw)
    if parts.scheme != "https" or not parts.hostname or "." not in parts.hostname:
        return None
    return urlunsplit((parts.scheme, parts.netloc.lower(), parts.path or "/", parts.query, ""))


def normalize_value(spec: FieldSpec, raw: str) -> str | None:
    """The canonical code of a raw value, or None when the field does not allow it."""
    value = raw.strip()
    if spec.value_type in ("enum", "bool"):
        value = value.lower()
        return value if value in spec.values else None
    if spec.value_type == "int":
        if not re.fullmatch(r"\d{1,4}", value):
            return None
        n = int(value)
        if spec.int_range is not None and not spec.int_range[0] <= n <= spec.int_range[1]:
            return None
        return str(n)
    if spec.value_type == "country":
        value = value.upper()
        if value in spec.extra_codes or _CC.fullmatch(value):
            return value
        return None
    if spec.value_type == "money":
        parsed = parse_money_code(value)
        if parsed is None or parsed[2] is None:
            return None  # the period is part of the value: "21900 COP/month"
        return format_money_code(*parsed)
    if spec.value_type == "date":
        if not _ISO_DATE.fullmatch(value):
            return None
        try:
            return date.fromisoformat(value).isoformat()
        except ValueError:
            return None
    if spec.value_type == "pct":
        return _pct(value.removesuffix("%").strip())
    if spec.value_type == "service_ref":
        value = value.lower()
        return value if _SERVICE_REF.fullmatch(value) else None
    if spec.value_type == "service_pct":
        service_id, sep, pct = value.lower().partition(":")
        service_id, pct = service_id.strip(), _pct(pct.strip().removesuffix("%").strip())
        if not sep or not _SERVICE_ID.fullmatch(service_id) or pct is None:
            return None
        return f"{service_id}:{pct}"
    if spec.value_type == "url":
        return _url(value)
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


# English and Portuguese labels of the values the site's reasons quote (research.evaluate
# tos_axis); a bundle_ twin shares its service field's labels.
_VALUE_LABELS_EN_PT: dict[str, dict[str, tuple[str, str]]] = {
    "residence_clause": {
        "primary_use_in_account_country": (
            "primary use in the account's country",
            "uso principal no país da conta",
        ),
        "account_country_must_match_residence": (
            "the account's country must be the country of residence",
            "o país da conta deve ser o de residência",
        ),
        "must_reside": ("residents of the country only", "só para residentes do país"),
    },
    "enforcement_event": {
        "location_verification": ("location checks", "verificação de localização"),
        "forced_price_change": (
            "forced price or country change",
            "mudança forçada de preço ou país",
        ),
        "suspension": ("suspensions", "suspensões"),
        "cancellation": ("cancellations", "cancelamentos"),
    },
}


def value_label(field_name: str, value: str) -> str:
    """value_label_es as a Text when the value has English and Portuguese labels."""
    es = value_label_es(field_name, value)
    en_pt = _VALUE_LABELS_EN_PT.get(field_name.removeprefix("bundle_"), {}).get(value)
    return Text(es, *en_pt) if en_pt else es


def vocabulary_prompt(level: SubjectKind) -> str:
    """The allowed fields of one level, as the agent's instruction lists them."""
    lines: list[str] = []
    for name in FIELDS_BY_LEVEL[level]:
        spec = FIELD_SPECS[name]
        if spec.value_type in ("enum", "bool"):
            allowed = " | ".join(spec.values)
        elif spec.value_type == "int":
            allowed = (
                f"an integer {spec.int_range[0]}-{spec.int_range[1]}"
                if spec.int_range
                else "an integer"
            )
        elif spec.value_type == "country":
            extra = f" or {' / '.join(spec.extra_codes)}" if spec.extra_codes else ""
            allowed = f"an ISO 3166-1 alpha-2 code{extra}"
        elif spec.value_type == "money":
            allowed = 'an amount like "21900 COP/month"'
        elif spec.value_type == "date":
            allowed = "a date YYYY-MM-DD"
        elif spec.value_type == "pct":
            allowed = "a percentage 1-100"
        elif spec.value_type == "service_ref":
            allowed = 'a service id, optionally ".plan_id"'
        elif spec.value_type == "service_pct":
            allowed = '"<service_id>:<percent>"'
        elif spec.value_type == "url":
            allowed = "an https URL"
        else:
            allowed = f"short text (<= {MAX_TEXT_VALUE} chars)"
        flags = []
        if spec.multi:
            flags.append("may repeat with different values")
        if spec.needs_date:
            flags.append("event_date required")
        if spec.official_only:
            flags.append("official pages of the job's country only")
        suffix = f" ({'; '.join(flags)})" if flags else ""
        hint = f" — {spec.hint}" if spec.hint else ""
        lines.append(f"- {name}: {allowed}{suffix}{hint}")
    return "\n".join(lines)

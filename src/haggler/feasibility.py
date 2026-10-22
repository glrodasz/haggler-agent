"""Deterministic feasibility labels. CONTRACT.

Axis ranks:
  legality:  lawful=0, contestable_eu_right=1, requires_misrepresentation=2
  tos:       compliant=0, breach_unenforced=2, breach_enforced=3
  practical: remote=0, presence_once=1, presence_periodic=2, impossible=4
rank = max(axis ranks); any step with requires_false_declaration forces rank >= 2
(axes_rank; record_rank applies it to a feasibility record).
Labels by rank: 0 official_home, 1 eu_right_untested, 2 tos_breach_low_enforcement,
3 tos_breach_enforced, 4 not_feasible. No record -> "unknown" (rank UNKNOWN_RANK, never chosen as
cheapest). target_country == residence_country -> official_home (rank 0) without a record.
eu_badge(record) is True when any legal_basis has strength textual|arguable (secondary badge).

The worst axis wins on purpose: a lawful, remote purchase that breaks enforced ToS is still a
"you may lose the account" offer, and the EU geo-blocking angle is shown as a badge instead of
lowering the label (it is an untested argument, not a guarantee). The record's `status`
(verified/unverified/expired) and `confidence` are shown next to the label, not folded into it.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass

from haggler.i18n import Text
from haggler.models.curated import FeasibilityFile, FeasibilityRecord, FeasibilityStep

LABELS = (
    "official_home",
    "eu_right_untested",
    "tos_breach_low_enforcement",
    "tos_breach_enforced",
    "not_feasible",
)
UNKNOWN_RANK = 99
UNKNOWN_LABEL = "unknown"

LEGALITY_RANK: dict[str, int] = {
    "lawful": 0,
    "contestable_eu_right": 1,
    "requires_misrepresentation": 2,
}
TOS_RANK: dict[str, int] = {
    "compliant": 0,
    "breach_unenforced": 2,
    "breach_enforced": 3,
}
PRACTICAL_RANK: dict[str, int] = {
    "remote": 0,
    "presence_once": 1,
    "presence_periodic": 2,
    "impossible": 4,
}
# A step that needs a false declaration (e.g. a fake billing address) is never below this rank.
FALSE_DECLARATION_MIN_RANK = 2
EU_BADGE_STRENGTHS = frozenset({"textual", "arguable"})

# Label id -> rank, including "unknown".
LABEL_RANKS: dict[str, int] = {label: rank for rank, label in enumerate(LABELS)} | {
    UNKNOWN_LABEL: UNKNOWN_RANK
}

# Label id -> (name, description) shown on the site. Informative only: never advice to lie.
LABEL_INFO: dict[str, tuple[str, str]] = {
    "official_home": (
        "Oferta oficial de tu país",
        "Precio del país donde vives, contratado de la forma normal. Sin riesgo de incumplir "
        "los términos del servicio.",
    ),
    "eu_right_untested": (
        "Posible derecho UE, sin probar",
        "Comprar la oferta de otro país del EEE podría estar amparado por el Reglamento (UE) "
        "2018/302 sobre geobloqueo, pero no hay precedentes y el proveedor puede negarse. No es "
        "asesoría legal.",
    ),
    "tos_breach_low_enforcement": (
        "Contra los términos, control bajo",
        "Incumple los términos del servicio (por ejemplo, la regla de país de residencia), pero "
        "no se conocen cancelaciones o bloqueos sistemáticos. Eso puede cambiar sin aviso y "
        "podrías perder la suscripción.",
    ),
    "tos_breach_enforced": (
        "Contra los términos, control activo",
        "Incumple los términos y el proveedor verifica activamente el país de la cuenta "
        "(hay cancelaciones o bloqueos documentados). Riesgo alto de perder la suscripción.",
    ),
    "not_feasible": (
        "No factible",
        "No es viable para tu perfil: exige requisitos que no puedes cumplir de forma honesta "
        "(por ejemplo, un medio de pago o presencia en el país que no tienes).",
    ),
    UNKNOWN_LABEL: (
        "Sin evaluar",
        "Aún no hay un análisis de factibilidad para comprar en este país desde tu residencia. "
        "Se muestra como referencia, pero nunca se elige como la opción más barata.",
    ),
}

# The names the English and Portuguese site gives each label (web/src/i18n riskLabels).
_LABEL_NAMES_EN_PT: dict[str, tuple[str, str]] = {
    "official_home": ("Official", "Oficial"),
    "eu_right_untested": ("EU right untested", "Direito da UE não testado"),
    "tos_breach_low_enforcement": (
        "Breaches ToS, no known enforcement",
        "Viola os termos, sem controle conhecido",
    ),
    "tos_breach_enforced": (
        "Breaches ToS, actively enforced",
        "Viola os termos, com controle ativo",
    ),
    "not_feasible": ("Not feasible", "Não viável"),
    UNKNOWN_LABEL: ("Not evaluated", "Sem avaliação"),
}


def label_name(label: str) -> Text:
    """A label's name in the site's three languages, for sentences that quote it."""
    en, pt = _LABEL_NAMES_EN_PT.get(label, (label, label))
    return Text(LABEL_INFO[label][0] if label in LABEL_INFO else label, en, pt)


@dataclass(frozen=True)
class Verdict:
    label: str
    rank: int
    eu_badge: bool
    record_id: str | None
    # "home" | "curated" | "agent_reviewed" | "agent_pending"; None = nothing evaluated.
    provenance: str | None = None


def axes_rank(
    legality: str, tos: str, practical: str, steps: Iterable[FeasibilityStep] = ()
) -> int:
    """The rank of three curated axes: the worst axis wins, and a step that needs a false
    declaration forces at least FALSE_DECLARATION_MIN_RANK. Shared by feasibility records and
    the curated bundle verdicts (bundles.yaml `verdicts`)."""
    rank = max(LEGALITY_RANK[legality], TOS_RANK[tos], PRACTICAL_RANK[practical])
    if any(step.requires_false_declaration for step in steps):
        rank = max(rank, FALSE_DECLARATION_MIN_RANK)
    return rank


def record_rank(record: FeasibilityRecord) -> int:
    return axes_rank(record.legality, record.tos, record.practical, record.steps)


def eu_badge(record: FeasibilityRecord) -> bool:
    return any(basis.strength in EU_BADGE_STRENGTHS for basis in record.legal_basis)


def find_record(
    feasibility: FeasibilityFile,
    service_id: str,
    target_country: str,
    *,
    residence_country: str,
    profile: str,
) -> FeasibilityRecord | None:
    """The curated record for exactly this (service, target, residence, profile), if any."""
    for record in feasibility.records:
        if (
            record.service_id == service_id
            and record.target_country == target_country
            and record.residence_country == residence_country
            and record.profile == profile
        ):
            return record
    return None


def verdict(
    service_id: str,
    target_country: str,
    *,
    residence_country: str,
    profile: str,
    feasibility: FeasibilityFile,
) -> Verdict:
    if target_country == residence_country:
        return Verdict(label=LABELS[0], rank=0, eu_badge=False, record_id=None, provenance="home")
    record = find_record(
        feasibility,
        service_id,
        target_country,
        residence_country=residence_country,
        profile=profile,
    )
    if record is None:
        return Verdict(label=UNKNOWN_LABEL, rank=UNKNOWN_RANK, eu_badge=False, record_id=None)
    rank = record_rank(record)
    return Verdict(
        label=LABELS[rank],
        rank=rank,
        eu_badge=eu_badge(record),
        record_id=record.id,
        provenance="curated",
    )

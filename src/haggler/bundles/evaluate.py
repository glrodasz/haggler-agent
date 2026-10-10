"""Per-profile feasibility verdict of a bundle (bundles.json v2 `verdicts`). CONTRACT.

Pure. T = the bundle's country, R = the profile's residence country.
assess_bundle(bundle, profile, ctx) applies, in order:

0. expired: an expired bundle (haggler.bundles.status.is_expired) gets no verdict (None).
1. curated: the bundle's `verdicts` record for the profile is used as is; its axes and steps give
   the rank (haggler.feasibility.axes_rank). Without a verified tos_clause (none can be verified
   before the watcher exists) it is shown unverified with low confidence. The stated requirements
   (rule 3 gates) still apply and can only make it harder: a document, line, payment or presence
   the profile lacks -> impossible; a FALSE DECLARATION the YAML states (residency required;
   billing_address required without a real address in T) adds its step and reason, so the legality
   is requires_misrepresentation and the rank at least 2 (decision 1 keeps a curated false
   declaration at its own rank, never below 2); a credit check is stated as a reason (the record
   already judged practicality). Components (rule 4) can only raise its rank.
2. home (T == R): official_home, rank 0, with hard gates: a local ID document (local_id other than
   none/unknown) needs T among the profile's documents; a mobile line needs a phone of T; a fixed
   line needs a real address in T. A failed gate -> not_feasible (rank 4, practical impossible)
   with the requirement stated as a plain fact. Paying at home is not a gate: profiles only model
   foreign payment instruments. The gates rest on the bundle's own data, so the verdict claims no
   more than the bundle's display state (haggler.bundles.status): verified only when the bundle
   is, with the state's confidence.
3. composed (T != R, no curated record):
   - an observed bundle (price.source observed) takes the research verdict of its observed service
     in T (haggler.research.evaluate.assess) for the bundle as a whole;
   - any other bundle takes its own route: ToS from the bundle subject's facts (research kind
     "bundle", key bundle:<bundle_id>, in ctx.facts like every research subject: accepted
     findings, and pending ones with include_pending; bundle_residence_clause,
     bundle_enforcement_event, bundle_foreign_offer_allowed through research.evaluate.tos_axis;
     compliant only with official claims; unknown without facts);
   - the curated requirements are gates on both: local_id -> a document of T; local_line
     mobile/fixed/both -> a phone / a real address of T; local_payment local_card -> a card issued
     in T, held or obtainable online; local_bank -> an instrument with an IBAN of T, held or
     obtainable online; carrier_bill -> a phone line of T; signup_presence checked -> the profile
     can be in T once; credit_check -> practicality unknown. A requirement the profile does not
     meet makes the route impossible and is stated as a plain fact;
   - a route that needs a FALSE DECLARATION (residency required; billing_address required without
     a real address of the profile in T) is never offered: legality requires_misrepresentation,
     practical impossible, rank 4;
   - on the bundle's own route an unknown requirement is an assumption ("se asume que ..."), and
     an unknown payment rule without a held card of T leaves practicality unknown; an observed
     route only takes the stated requirements (its service verdict covers the rest).
4. components: every included component and the N cheapest options of each choose_n group
   (`counted`, from haggler.bundles.pricing), each assessed with research.evaluate.assess(service,
   T). The worst axis wins and a component that is not feasible makes the bundle not feasible. An
   unknown component makes a verdict on the bundle's own route unknown; next to a curated or
   observed verdict (which already covers the whole product) it is listed as "sin evaluar". When a
   component's CURATED feasibility record has a step with requires_false_declaration, that step is
   copied with a reason naming what it makes the subscriber declare, and the bundle inherits the
   record's rank as a floor (user decision 1): never lower, even when other axes are unknown.
   The worst value of an axis (requires_misrepresentation, breach_enforced, impossible) stands
   next to an unknown one: nothing unknown can make it worse. The EU badge also comes from a
   component whose contestable_eu_right legality the bundle takes.
5. rank: 4 when anything is impossible; else unknown (UNKNOWN_RANK) when an axis is unknown, unless
   rule 4 set a floor (then the floor or the worst known rank, with an assumption saying that
   parts are not evaluated); else the worst of the axes, the components and the floor. A composed
   rank below 2 that rests on assumptions -> unknown: improvements need facts.

Provenance: home | curated | composed; agent_reviewed / agent_pending once the bundle subject's
own research facts decide its ToS. With include_pending, a verdict that used a
finding still pending review (in a component, the observed service or the bundle subject) is
agent_pending; publish lists it only when it differs from the reviewed one.

Reasons and assumptions only state requirements and facts, never a way around them (no VPN, other
addresses or false data). Not legal advice.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, field

from haggler.bundles.pricing import counted_components
from haggler.bundles.status import BundleState, bundle_state, is_expired
from haggler.feasibility import (
    EU_BADGE_STRENGTHS,
    LABEL_INFO,
    LABELS,
    LEGALITY_RANK,
    PRACTICAL_RANK,
    TOS_RANK,
    UNKNOWN_LABEL,
    UNKNOWN_RANK,
    axes_rank,
)
from haggler.models.curated import (
    Bundle,
    BundleComponent,
    BundleVerdictRecord,
    FeasibilityStep,
)
from haggler.models.profiles import Profile
from haggler.models.research import SubjectKind, subject_key
from haggler.research.evaluate import (
    UNKNOWN_AXIS,
    Assessment,
    EvalContext,
    Facts,
    Work,
    assess,
    held_card,
    obtainable,
    obtainable_card,
    tos_axis,
)

BUNDLE_SUBJECT: SubjectKind = "bundle"
_NO_ID = frozenset({"", "none", "unknown"})
_ID_NAMES = {"cedula": "cédula", "curp": "CURP", "cpf": "CPF"}
_CONFIDENCE_ORDER = ("low", "medium", "high")
_AXIS_ORDER = {"legality": LEGALITY_RANK, "tos": TOS_RANK, "practical": PRACTICAL_RANK}
_HOME, _CURATED, _OBSERVED, _ROUTE = "home", "curated", "observed", "route"


@dataclass(frozen=True)
class ComponentVerdict:
    service_id: str
    target_country: str
    label: str
    rank: int
    # The component's own research.evaluate provenance (home | curated | agent_reviewed |
    # agent_pending); None when nothing evaluated it.
    provenance: str | None


@dataclass(frozen=True)
class BundleAssessment:
    bundle_id: str
    profile: str
    # "home" | "curated" | "composed" | "agent_reviewed" | "agent_pending"
    provenance: str
    label: str
    rank: int
    eu_badge: bool
    legality: str
    tos: str
    practical: str
    confidence: str
    status: str
    notes_es: str = ""
    steps: tuple[FeasibilityStep, ...] = ()
    assumptions_es: tuple[str, ...] = ()
    reasons_es: tuple[str, ...] = ()
    components: tuple[ComponentVerdict, ...] = ()

    def same_verdict(self, other: BundleAssessment) -> bool:
        return (self.label, self.rank, self.legality, self.tos, self.practical, self.eu_badge) == (
            other.label,
            other.rank,
            other.legality,
            other.tos,
            other.practical,
            other.eu_badge,
        )


@dataclass
class _Route:
    """The bundle's own route (rules 1-3) before its components are folded in."""

    kind: str
    legality: str
    tos: str
    practical: str
    confidence: str
    status: str
    eu_badge: bool = False
    notes_es: str = ""
    pending: bool = False
    researched: bool = False
    work: Work = field(default_factory=Work)


def _sentence(text: str) -> str:
    text = " ".join(text.split())
    if not text:
        return text
    text = text[0].upper() + text[1:]
    return text if text[-1] in ".!?" else text + "."


def _label(rank: int) -> str:
    return LABELS[rank] if 0 <= rank < len(LABELS) else UNKNOWN_LABEL


def _service_name(ctx: EvalContext, service_id: str) -> str:
    try:
        return ctx.catalog.service(service_id).name
    except KeyError:
        return service_id


def _false_step_es(step: FeasibilityStep) -> str:
    """What a step that needs a false declaration makes the subscriber declare. A presence step
    of that kind is false about the residence (the subscription country must be where one lives),
    not about the trip itself."""
    v = step.value
    return {
        "presence": f"declarar {v} como país de residencia",
        "declare_country": f"declarar {v} como país de residencia",
        "billing_address": f"declarar una dirección de facturación en {v}",
        "store_region": f"declarar {v} como país de la cuenta de la tienda",
        "payment_country": f"un medio de pago de {v}",
        "local_id": f"un documento de {v}",
        "local_phone": f"un teléfono de {v}",
    }.get(step.type, f"{step.type} {v}")


# ----------------------------------------------------------------------------- gates


def _local_bank(t: str, profile: Profile, facts: Facts, w: Work) -> str | None:
    """Id of an instrument with an IBAN of `t` the profile holds, or could open online."""
    held = set(profile.instruments_held)
    instruments = facts.ctx.instruments.instruments
    for inst in instruments:
        if inst.id in held and inst.iban_country == t:
            return inst.id
    for inst in instruments:
        if inst.id in held or inst.iban_country != t:
            continue
        probe = Work()
        if obtainable(inst, profile, facts, probe):
            w.use(probe.claims.values(), subject_key("instrument", inst.id))
            w.assumptions.extend(probe.assumptions)
            w.step("obtain_instrument", inst.id)
            return inst.id
    return None


def _impossible(w: Work, reason: str) -> None:
    w.practical.append("impossible")
    w.reasons.append(reason)


def _gates(bundle: Bundle, profile: Profile, facts: Facts, w: Work, mode: str) -> bool:
    """Apply the bundle's curated requirements to a route (see the module rules for which ones
    each route takes). Returns True when a stated requirement leaves practicality unknown."""
    r = bundle.requirements
    t = bundle.country
    route = mode == _ROUTE
    unknown = False

    local_id = r.local_id.strip()
    if local_id.lower() not in _NO_ID:
        if t in profile.documents():
            w.step("local_id", t)
        else:
            name = _ID_NAMES.get(local_id.lower(), local_id)
            _impossible(
                w, f"Exige un documento de identidad de {t} ({name}); el perfil no tiene uno."
            )
    elif route and local_id.lower() == "unknown":
        w.assumptions.append(f"se asume que no se pide un documento de identidad de {t}")

    if r.local_line in ("mobile", "both"):
        if t in profile.phones():
            w.step("local_phone", t)
        else:
            _impossible(w, f"Exige una línea móvil de {t}; el perfil no tiene un teléfono de {t}.")
    if r.local_line in ("fixed", "both") and t not in profile.addresses():
        _impossible(
            w,
            f"Exige una línea fija de {t}, instalada en un domicilio en {t}; "
            f"el perfil no tiene uno.",
        )
    if route and r.local_line == "unknown":
        w.assumptions.append(f"se asume que no se pide una línea de teléfono de {t}")
    if mode == _HOME:
        return False

    pay = r.local_payment
    if pay == "local_card":
        if held_card(t, profile, facts, w) is not None:
            w.step("payment_country", t)
        elif (inst := obtainable_card(t, profile, facts, w)) is not None:
            w.step("obtain_instrument", inst)
            w.step("payment_country", t)
        else:
            _impossible(
                w, f"Exige una tarjeta emitida en {t}; el perfil no tiene ni puede obtener una."
            )
    elif pay == "local_bank":
        if _local_bank(t, profile, facts, w) is not None:
            w.step("payment_country", t)
        else:
            _impossible(
                w, f"Exige una cuenta bancaria de {t}; el perfil no tiene ni puede abrir una."
            )
    elif pay == "carrier_bill":
        if t in profile.phones():
            w.step("local_phone", t)
        else:
            _impossible(
                w,
                f"Se paga en la factura de una línea de {t}; el perfil no tiene una línea de {t}.",
            )
    elif route and pay == "unknown":
        if held_card(t, profile, facts, w) is not None:
            w.step("payment_country", t)  # a card of T works whatever the rule is
        else:
            w.payment_unknown = True
            w.reasons.append(
                f"no se sabe qué medio de pago acepta y el perfil no tiene una tarjeta de {t}"
            )

    if r.signup_presence == "checked":
        if t in profile.presence:
            w.practical.append("presence_once")
            w.step("presence", t, requires_presence=True)
        else:
            _impossible(w, f"Exige estar en {t} al contratar; el perfil no viaja a {t}.")
    elif route and r.signup_presence == "unknown":
        w.assumptions.append(f"se asume que no se verifica la ubicación al contratar en {t}")

    # On the curated route the record already judged practicality: a credit check is only
    # stated (the return value is ignored there), a false declaration still adds its step.
    if r.credit_check:
        unknown = True
        w.reasons.append(f"hace una consulta de crédito en {t}; no se sabe si el perfil la supera")
    elif route and r.credit_check is None:
        w.assumptions.append("se asume que no hay una consulta de crédito")

    if r.billing_address == "required":
        if t in profile.addresses():
            w.step("billing_address", t)
        else:
            w.false_declaration = True
            w.step("billing_address", t, requires_false_declaration=True)
            w.reasons.append(
                f"exige una dirección de facturación en {t} y el perfil no tiene una real allí"
            )
    elif route and r.billing_address == "unknown":
        w.assumptions.append(f"se asume que no se pide una dirección de facturación en {t}")
    if r.residency == "required":
        w.false_declaration = True
        w.step("declare_country", t, requires_false_declaration=True)
        w.reasons.append(f"exige vivir en {t} y el perfil vive en {profile.residence_country}")
    return unknown


# ---------------------------------------------------------------------------- routes


def _home(bundle: Bundle, profile: Profile, facts: Facts, state: BundleState) -> _Route:
    """The official offer of the profile's own country. Its gates come from the bundle's own
    (YAML) requirements, so it is verified only when the bundle is, with the bundle's
    confidence."""
    w = Work()
    _gates(bundle, profile, facts, w, _HOME)
    if "impossible" in w.practical:
        return _Route(_HOME, "lawful", "compliant", "impossible", "low", "unverified", work=w)
    verified = state.display_status == "verified"
    return _Route(
        _HOME,
        "lawful",
        "compliant",
        "remote",
        confidence=state.confidence,
        status="verified" if verified else "unverified",
        work=w,
    )


def _clause_verified(record: BundleVerdictRecord) -> bool:
    """Whether our own fetch confirmed the record's tos_clause. That evidence comes from the
    watcher (milestone H7): until then no clause counts as verified."""
    return False


def _curated(bundle: Bundle, record: BundleVerdictRecord, profile: Profile, facts: Facts) -> _Route:
    w = Work()
    w.steps.extend(record.steps)
    w.reasons.extend(record.reasons_es)
    _gates(bundle, profile, facts, w, _CURATED)
    verified = _clause_verified(record)
    # The stated requirements only make the record's practicality harder (presence, impossible).
    practical = max([record.practical, *w.practical], key=PRACTICAL_RANK.__getitem__)
    # A false declaration the YAML states: its step forces rank >= 2 (axes_rank) and the
    # legality says so, whatever the record wrote (decision 1: never below 2, never derived 4).
    legality = "requires_misrepresentation" if w.false_declaration else record.legality
    return _Route(
        _CURATED,
        legality,
        record.tos,
        practical,
        confidence=record.confidence if verified else "low",
        status=record.status if verified else "unverified",
        eu_badge=any(b.strength in EU_BADGE_STRENGTHS for b in record.legal_basis),
        notes_es=" ".join(record.notes_es.split()),
        work=w,
    )


def _observed(
    bundle: Bundle, profile: Profile, ctx: EvalContext, facts: Facts, include_pending: bool
) -> _Route:
    service_id = bundle.price.observed_service_id or ""
    a = assess(service_id, bundle.country, profile, ctx, include_pending=include_pending)
    w = Work()
    w.steps.extend(a.steps)
    w.assumptions.extend(a.assumptions_es)
    w.reasons.extend(a.reasons_es)
    credit_unknown = _gates(bundle, profile, facts, w, _OBSERVED)
    legality, practical = a.legality, a.practical
    if w.false_declaration:
        legality, practical = "requires_misrepresentation", "impossible"
    elif "impossible" in w.practical:
        practical = "impossible"
    elif credit_unknown and practical != "impossible":
        practical = UNKNOWN_AXIS
    elif practical not in (UNKNOWN_AXIS, "impossible") and w.practical:
        practical = max([practical, *w.practical], key=PRACTICAL_RANK.__getitem__)
    return _Route(
        _OBSERVED,
        legality,
        a.tos,
        practical,
        confidence=a.confidence if a.evaluated else "low",
        status=a.status if a.evaluated else "unverified",
        eu_badge=a.eu_badge,
        notes_es=a.notes_es,
        pending=a.provenance == "agent_pending",
        work=w,
    )


def _own_route(bundle: Bundle, profile: Profile, facts: Facts) -> _Route:
    w = Work()
    key = subject_key(BUNDLE_SUBJECT, bundle.bundle_id)
    tos, claims = tos_axis(
        facts,
        w,
        key,
        clause_field="bundle_residence_clause",
        event_field="bundle_enforcement_event",
        allowed_field="bundle_foreign_offer_allowed",
    )
    if tos == "compliant" and not all(c.official for c in claims):
        tos = UNKNOWN_AXIS  # a foreign offer is only allowed on the provider's own word
    unknown = _gates(bundle, profile, facts, w, _ROUTE)
    if w.false_declaration:
        legality, practical = "requires_misrepresentation", "impossible"
    else:
        legality = "lawful"
        practical = w.practical_level()
        if practical != "impossible" and (unknown or w.payment_unknown):
            practical = UNKNOWN_AXIS
    return _Route(
        _ROUTE,
        legality,
        tos,
        practical,
        confidence="low",
        status="unverified",
        pending=facts.pending(claims),
        # The bundle subject's own facts decided the ToS (agent_reviewed / agent_pending).
        researched=bool(claims),
        work=w,
    )


# ------------------------------------------------------------------------ components


def _combine(axis: str, values: list[str]) -> str:
    """The worst value of an axis; unknown when an unknown value could still make it worse
    (the worst value of the scale, e.g. requires_misrepresentation or impossible, stands)."""
    order = _AXIS_ORDER[axis]
    worst = _worst_known(axis, values)
    if UNKNOWN_AXIS in values and (worst == UNKNOWN_AXIS or order[worst] < max(order.values())):
        return UNKNOWN_AXIS
    return worst


def _worst_known(axis: str, values: list[str]) -> str:
    known = [v for v in values if v != UNKNOWN_AXIS]
    return max(known, key=_AXIS_ORDER[axis].__getitem__) if known else UNKNOWN_AXIS


def _known_rank(legality: str, tos: str, practical: str) -> int:
    ranks = [
        _AXIS_ORDER[axis][value]
        for axis, value in (("legality", legality), ("tos", tos), ("practical", practical))
        if value != UNKNOWN_AXIS
    ]
    return max(ranks, default=0)


def assess_bundle(
    bundle: Bundle,
    profile: Profile,
    ctx: EvalContext,
    *,
    include_pending: bool = False,
    counted: Sequence[BundleComponent] | None = None,
    state: BundleState | None = None,
) -> BundleAssessment | None:
    """The verdict of `bundle` for `profile` (None for an expired bundle). `counted`: the
    components the price buys (haggler.bundles.pricing: included + the N cheapest of each
    choose_n group); by default the included ones and the first N options in YAML order.
    `state`: the bundle's display state (haggler.bundles.status.bundle_state); by default
    computed from the YAML for ctx.today."""
    if is_expired(bundle, ctx.today):
        return None
    t, r = bundle.country, profile.residence_country
    facts = Facts(ctx, include_pending=include_pending)
    record = next((v for v in bundle.verdicts if v.profile == profile.id), None)
    if t == r:
        route = _home(bundle, profile, facts, state or bundle_state(bundle, today=ctx.today))
    elif record is not None:
        route = _curated(bundle, record, profile, facts)
    elif bundle.price.source == "observed":
        route = _observed(bundle, profile, ctx, facts, include_pending)
    else:
        route = _own_route(bundle, profile, facts)
    w = route.work
    strict = route.kind == _ROUTE

    parts = {"legality": [route.legality], "tos": [route.tos], "practical": [route.practical]}
    components: list[ComponentVerdict] = []
    comp_ranks: list[int] = []
    confidences = [route.confidence]
    statuses = [route.status]
    floor: int | None = None
    # Any finding still pending review (instrument facts of the gates included).
    pending = route.pending or facts.pending(w.claims.values())
    impossible_parts = False
    eu_component = False
    comps = counted if counted is not None else counted_components(bundle)
    for comp in comps:
        a: Assessment = assess(comp.service_id, t, profile, ctx, include_pending=include_pending)
        components.append(
            ComponentVerdict(
                comp.service_id, t, a.label, a.rank, a.provenance if a.evaluated else None
            )
        )
        pending = pending or a.provenance == "agent_pending"
        name = _service_name(ctx, comp.service_id)
        false_steps = [s for s in a.steps if s.requires_false_declaration]
        if a.provenance == "curated" and false_steps:
            floor = max(floor or 0, a.rank)
            for s in false_steps:
                if s not in w.steps:
                    w.steps.append(s)
            # The record's free-text note stays on the service's own feasibility record: it may
            # name what is false in words the bundle wording check does not let through.
            steps_es = ", ".join(_false_step_es(s) for s in false_steps)
            w.reasons.append(
                f"Incluye {name}: su ficha curada en {t} exige una declaración falsa "
                f"({steps_es}), así que el paquete hereda su nivel «{LABEL_INFO[a.label][0]}»."
            )
        if a.rank == 4 or a.practical == "impossible":
            impossible_parts = True
            comp_ranks.append(4)
            w.reasons.append(f"Incluye {name}, que no es factible en {t} para este perfil.")
            continue
        if a.rank == UNKNOWN_RANK:
            w.reasons.append(f"Incluye {name}, sin evaluar en {t}.")
            if strict:
                for axis in parts:
                    parts[axis].append(UNKNOWN_AXIS)
                confidences.append("low")
                statuses.append("unverified")
            continue
        comp_ranks.append(a.rank)
        confidences.append(a.confidence)
        statuses.append(a.status)
        eu_component = eu_component or (a.eu_badge and a.legality == "contestable_eu_right")
        parts["legality"].append(a.legality)
        parts["tos"].append(a.tos)
        parts["practical"].append(a.practical)

    legality = _combine("legality", parts["legality"])
    tos = _combine("tos", parts["tos"])
    practical = _combine("practical", parts["practical"])
    if impossible_parts:
        practical = "impossible"
    steps = tuple(w.steps)
    if practical == "impossible":
        rank = 4
    elif UNKNOWN_AXIS in (legality, tos, practical):
        if floor is not None:
            # User decision 1: never below an inherited curated rank, even with unknown parts.
            legality = _worst_known("legality", parts["legality"])
            tos = _worst_known("tos", parts["tos"])
            practical = _worst_known("practical", parts["practical"])
            rank = max(floor, _known_rank(legality, tos, practical), *comp_ranks)
            w.assumptions.append("hay partes del paquete sin evaluar: el nivel podría ser peor")
        else:
            rank = UNKNOWN_RANK
    else:
        rank = max(axes_rank(legality, tos, practical, steps), floor or 0, *comp_ranks)
        if route.kind in (_OBSERVED, _ROUTE) and rank < 2 and w.assumptions:
            rank = UNKNOWN_RANK
            w.reasons.append("faltan hechos con fuente oficial para descartar riesgos")

    if route.kind == _HOME:
        provenance = "home"
    elif route.kind == _CURATED:
        provenance = "curated"
    elif route.researched:
        provenance = "agent_pending" if route.pending else "agent_reviewed"
    else:
        provenance = "composed"
    if include_pending and pending:
        provenance = "agent_pending"
    verified = rank != UNKNOWN_RANK and all(s == "verified" for s in statuses)
    return BundleAssessment(
        bundle_id=bundle.bundle_id,
        profile=profile.id,
        provenance=provenance,
        label=_label(rank),
        rank=rank,
        # A component's EU badge comes along when the bundle takes its contestable legality.
        eu_badge=route.eu_badge or (eu_component and legality == "contestable_eu_right"),
        legality=legality,
        tos=tos,
        practical=practical,
        confidence="low" if rank == UNKNOWN_RANK else min(confidences, key=_CONFIDENCE_ORDER.index),
        status="verified" if verified else "unverified",
        notes_es=route.notes_es,
        steps=steps,
        assumptions_es=tuple(dict.fromkeys(_sentence(x) for x in w.assumptions)),
        reasons_es=tuple(dict.fromkeys(_sentence(x) for x in w.reasons)),
        components=tuple(components),
    )

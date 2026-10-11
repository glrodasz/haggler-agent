"""Deterministic feasibility evaluator: verified facts x profile -> three axes. CONTRACT.

Pure (no I/O, no ADK). For a profile P living in R and a target country T:

1. Inputs. T == R -> official_home. A curated FeasibilityRecord for (service, T, R, P) is
   returned as is (provenance "curated"). Otherwise only research claims count, minus rejected
   ones (review), minus subjects older than STALE_AFTER_DAYS; without include_pending only
   accepted claims count. Forum sources never count; press sources only for negative facts
   (values not in POSITIVE_VALUES) and never for instruments nor official_only fields.
2. Field resolution. A single-valued field resolves to its least favourable claimed value
   (fields.FieldSpec.values order). Multi-valued fields keep every claimed value.
3. Payment. An instrument's card country is its curated observed BIN country, else a verified
   official card_issuing_country claim; never the issuer's licence country. "Obtainable" =
   P.obtain_remote_instruments, onboarding remote, eligible for R, P has the required documents
   and (when required) an address in the issuer's country.
4. Practicality (worst requirement wins):
   payment_card_country must_match (or account_country_basis payment_method_country /
   store_region) -> a held card of T, else an obtainable one, else remote gift cards, else
   impossible; unknown and no held card of T -> practicality unknown.
   signup_location checked -> presence_once if T in P.presence, else impossible;
   periodic_location_check -> presence_periodic if P.presence[T] == periodic, else impossible;
   local id / phone -> P must have one of T, else impossible;
   billing_address required -> a real address of P in T, else a FALSE DECLARATION;
   account_country_basis self_declared -> declaring T while living in R: a FALSE DECLARATION.
   Missing optional facts are assumed "not required" and listed in assumptions_es.
   available false -> impossible.
5. A route that needs a false declaration is never offered: legality
   requires_misrepresentation and practicality impossible (rank 4), with the reason shown.
6. ToS. A residence clause, or an enforcement event, is a breach; breach_enforced when an
   enforcement event is dated within ENFORCEMENT_WINDOW_DAYS. compliant needs an official
   foreign_offer_allowed claim and no clause. Anything else is unknown.
7. Legality. R and T in the EEA, a service of eu_geo_class eservice_art4_1b and a known barrier
   -> contestable_eu_right (Reg. (EU) 2018/302 Art. 4(1)(b), arguable, untested; EU badge).
   Else lawful.
8. Rank. practicality impossible -> 4 (it is the maximum whatever the unknown axes are); an
   unknown axis -> unknown (never chosen); else the worst axis (haggler.feasibility ranks). A
   derived rank below 2 that rests on assumptions -> unknown: improvements need quotes.
9. Confidence: high = reviewed, every decisive claim official, no assumptions; medium = ToS
   from official sources and no assumptions; low otherwise. Provenance agent_reviewed only when
   every decisive claim is accepted for its current hash, else agent_pending.

The building blocks (Facts, Work, tos_axis, requirement, held_card, obtainable_card, obtainable,
card_country, eea) are public because haggler.bundles.evaluate applies the same rules to a
bundle's own route; they behave exactly as described above.
"""

from __future__ import annotations

from collections.abc import Callable, Iterable
from dataclasses import dataclass, field
from datetime import date

from haggler.feasibility import (
    LABELS,
    LEGALITY_RANK,
    PRACTICAL_RANK,
    TOS_RANK,
    UNKNOWN_LABEL,
    UNKNOWN_RANK,
    Verdict,
    eu_badge,
    find_record,
    record_rank,
)
from haggler.models.catalog import Catalog
from haggler.models.curated import (
    Evidence,
    FeasibilityFile,
    FeasibilityRecord,
    FeasibilityStep,
    LegalBasis,
    PaymentInstrument,
    PaymentInstrumentsFile,
)
from haggler.models.profiles import Profile
from haggler.models.research import Claim, ReviewFile, SubjectFacts, subject_key
from haggler.research.fields import FIELD_SPECS, severity, value_label_es
from haggler.research.review import ReviewIndex

STALE_AFTER_DAYS = 365
ENFORCEMENT_WINDOW_DAYS = 730
UNKNOWN_AXIS = "unknown"

# Values that make a purchase easier: only an official source can establish them.
POSITIVE_VALUES: dict[str, frozenset[str]] = {
    "foreign_offer_allowed": frozenset({"true"}),
    "available": frozenset({"true"}),
    "payment_card_country": frozenset({"any_country"}),
    "gift_cards": frozenset({"remote_purchase"}),
    "signup_location": frozenset({"not_checked"}),
    "periodic_location_check": frozenset({"false"}),
    "billing_address": frozenset({"not_required"}),
    "local_id_required": frozenset({"false"}),
    "local_phone_required": frozenset({"false"}),
    # The bundle twins (research.fields) and the bundle-only requirement fields.
    "bundle_foreign_offer_allowed": frozenset({"true"}),
    "bundle_signup_location": frozenset({"not_checked"}),
    "bundle_billing_address": frozenset({"not_required"}),
    "bundle_local_id_required": frozenset({"false"}),
    "bundle_local_line": frozenset({"none"}),
    "bundle_payment_method": frozenset({"any_card"}),
    "bundle_credit_check": frozenset({"false"}),
}
EU_LEGAL_BASIS = LegalBasis(
    instrument="Reglamento (UE) 2018/302 (geobloqueo)",
    article="Art. 4(1)(b)",
    strength="arguable",
    tested=False,
)
_PRACTICAL_ORDER = ("remote", "presence_once", "presence_periodic", "impossible")


@dataclass(frozen=True)
class Assessment:
    service_id: str
    target_country: str
    residence_country: str
    profile: str
    legality: str
    tos: str
    practical: str
    rank: int
    label: str
    eu_badge: bool
    confidence: str
    # "home" | "curated" | "agent_reviewed" | "agent_pending" | "none"
    provenance: str
    status: str = "unverified"
    steps: tuple[FeasibilityStep, ...] = ()
    legal_basis: tuple[LegalBasis, ...] = ()
    claim_ids: tuple[str, ...] = ()
    evidence: tuple[Evidence, ...] = ()
    assumptions_es: tuple[str, ...] = ()
    reasons_es: tuple[str, ...] = ()
    researched_on: date | None = None
    record_id: str | None = None
    notes_es: str = ""

    def verdict(self) -> Verdict:
        return Verdict(
            label=self.label,
            rank=self.rank,
            eu_badge=self.eu_badge,
            record_id=self.record_id,
            provenance=None if self.provenance == "none" else self.provenance,
        )

    @property
    def evaluated(self) -> bool:
        return self.provenance != "none"


@dataclass
class EvalContext:
    catalog: Catalog
    feasibility: FeasibilityFile
    instruments: PaymentInstrumentsFile
    facts: dict[str, SubjectFacts]
    review: ReviewIndex
    today: date

    @classmethod
    def build(
        cls,
        *,
        catalog: Catalog,
        feasibility: FeasibilityFile,
        instruments: PaymentInstrumentsFile,
        subjects: Iterable[SubjectFacts] = (),
        review: ReviewFile | None = None,
        today: date,
    ) -> EvalContext:
        return cls(
            catalog=catalog,
            feasibility=feasibility,
            instruments=instruments,
            facts={s.key: s for s in subjects},
            review=ReviewIndex(review),
            today=today,
        )


@dataclass
class _Resolved:
    value: str
    claims: list[Claim]


@dataclass
class Work:
    """Accumulates one derived assessment."""

    steps: list[FeasibilityStep] = field(default_factory=list)
    practical: list[str] = field(default_factory=list)
    claims: dict[str, Claim] = field(default_factory=dict)
    assumptions: list[str] = field(default_factory=list)
    reasons: list[str] = field(default_factory=list)
    false_declaration: bool = False
    payment_unknown: bool = False
    subjects: set[str] = field(default_factory=set)

    def use(self, claims: Iterable[Claim], subject: str) -> None:
        for c in claims:
            self.claims.setdefault(c.id, c)
            self.subjects.add(subject)

    def step(self, type_: str, value: str, **kw: bool) -> None:
        s = FeasibilityStep(type=type_, value=value, **kw)  # type: ignore[arg-type]
        if s not in self.steps:
            self.steps.append(s)

    def practical_level(self) -> str:
        if not self.practical:
            return "remote"
        return max(self.practical, key=_PRACTICAL_ORDER.index)


class Facts:
    """Usable claims of fresh subjects for one evaluation."""

    def __init__(self, ctx: EvalContext, *, include_pending: bool) -> None:
        self.ctx = ctx
        self.include_pending = include_pending

    def subject(self, key: str) -> SubjectFacts | None:
        s = self.ctx.facts.get(key)
        if s is None or (self.ctx.today - s.researched_on).days > STALE_AFTER_DAYS:
            return None
        return s

    def usable(self, key: str, field_name: str) -> list[Claim]:
        s = self.subject(key)
        if s is None:
            return []
        out: list[Claim] = []
        for c in s.claims:
            if c.field != field_name:
                continue
            status = self.ctx.review.status(c)
            if status == "rejected" or (status == "pending" and not self.include_pending):
                continue
            seen = c.check.retrieved_at.date() if c.check.retrieved_at else c.first_seen
            if (self.ctx.today - seen).days > STALE_AFTER_DAYS:
                continue  # its page has not confirmed it for a year (e.g. no longer loads)
            cls = c.check.source_class
            if cls == "forum":
                continue
            if cls != "official" and (
                s.kind == "instrument"
                or c.value in POSITIVE_VALUES.get(field_name, frozenset())
                or (field_name in FIELD_SPECS and FIELD_SPECS[field_name].official_only)
            ):
                continue
            out.append(c)
        return out

    def resolve(self, key: str, field_name: str) -> _Resolved | None:
        claims = self.usable(key, field_name)
        if not claims:
            return None
        spec = FIELD_SPECS[field_name]
        if not spec.values and len({c.value for c in claims}) > 1:
            return None  # e.g. two card issuing countries: no order to pick one, so unknown
        worst = max(claims, key=lambda c: severity(spec, c.value)).value
        return _Resolved(worst, [c for c in claims if c.value == worst])

    def values(self, key: str, field_name: str) -> dict[str, list[Claim]]:
        out: dict[str, list[Claim]] = {}
        for c in self.usable(key, field_name):
            out.setdefault(c.value, []).append(c)
        return out

    def pending(self, claims: Iterable[Claim]) -> bool:
        return any(self.ctx.review.status(c) != "accepted" for c in claims)


# --------------------------------------------------------------------- instruments


def eea(ctx: EvalContext, cc: str) -> bool:
    try:
        return ctx.catalog.country(cc).eu_eea
    except KeyError:
        return False


def card_country(inst: PaymentInstrument, facts: Facts, w: Work) -> str | None:
    if inst.card_bin_country_observed.value:
        return inst.card_bin_country_observed.value
    key = subject_key("instrument", inst.id)
    res = facts.resolve(key, "card_issuing_country")
    if res is None:
        return None
    w.use(res.claims, key)
    return res.value


def obtainable(inst: PaymentInstrument, profile: Profile, facts: Facts, w: Work) -> bool:
    """Curated values first; a verified official claim can only make an instrument harder to
    obtain (negative facts), and every fact it rests on without a claim is an assumption."""
    ctx = facts.ctx
    key = subject_key("instrument", inst.id)
    remote = facts.resolve(key, "onboarding_remote")
    if not (profile.obtain_remote_instruments and inst.onboarding_remote):
        return False
    if remote is not None and remote.value == "false":
        w.use(remote.claims, key)
        return False
    r = profile.residence_country

    def eligible(codes: Iterable[str]) -> bool:
        return any(e in ("ANY", r) or (e == "EEA" and eea(ctx, r)) for e in codes)

    if not eligible(inst.eligible_residence):
        return False
    claimed = facts.values(key, "eligible_residence")
    if claimed and not eligible(claimed):
        w.use([c for cs in claimed.values() for c in cs], key)
        return False
    docs = profile.documents()
    if inst.required_id_countries and not set(inst.required_id_countries) & docs:
        return False
    accepted_docs = facts.values(key, "id_documents")
    if accepted_docs:
        ok = {
            "any_passport": bool(docs),
            "eea_id": any(eea(ctx, cc) for cc in docs),
            "residence_permit": r in docs,
            "issuer_country_id": inst.issuer_country in docs,
        }
        usable = [v for v in accepted_docs if ok.get(v)]
        if not usable:
            return False
        w.use(accepted_docs[usable[0]], key)
    elif not inst.required_id_countries:
        w.assumptions.append(f"se asume que {inst.name} acepta los documentos del perfil")
    local = facts.resolve(key, "requires_local_address")
    if local is None:
        w.assumptions.append(f"se asume que {inst.name} no exige una dirección en su país")
    elif local.value == "true":
        w.use(local.claims, key)
        if inst.issuer_country not in profile.addresses():
            return False
    return True


def held_card(target: str, profile: Profile, facts: Facts, w: Work) -> str | None:
    """Id of a held instrument whose card is issued in `target`."""
    held = set(profile.instruments_held)
    for inst in facts.ctx.instruments.instruments:
        if inst.id in held and card_country(inst, facts, w) == target:
            return inst.id
    return None


def obtainable_card(target: str, profile: Profile, facts: Facts, w: Work) -> str | None:
    """Id of an instrument the profile could open online whose card is issued in `target`."""
    held = set(profile.instruments_held)
    for inst in facts.ctx.instruments.instruments:
        if inst.id in held:
            continue
        probe = Work()
        if card_country(inst, facts, probe) == target and obtainable(inst, profile, facts, probe):
            w.use(probe.claims.values(), subject_key("instrument", inst.id))
            w.assumptions.extend(probe.assumptions)
            return inst.id
    return None


# ------------------------------------------------------------------------- axes


def _payment(t: str, profile: Profile, facts: Facts, w: Work, svc_key: str, pair_key_: str) -> None:
    pcc = facts.resolve(pair_key_, "payment_card_country")
    basis = facts.values(svc_key, "account_country_basis")
    must_match = pcc is not None and pcc.value == "must_match"
    if "store_region" in basis:
        w.step("store_region", t)
    held = held_card(t, profile, facts, w)
    if held is not None:
        # A card of T satisfies the payment step whatever the rule is.
        w.step("payment_country", t)
        return
    if pcc is not None:
        w.use(pcc.claims, pair_key_)
    for b in ("payment_method_country", "store_region"):
        if b in basis:
            must_match = True
            w.use(basis[b], svc_key)
    if pcc is not None and not must_match:
        return  # any card works
    gift = facts.resolve(pair_key_, "gift_cards")
    remote_gift = gift is not None and gift.value == "remote_purchase"
    if not must_match:
        if remote_gift:
            w.use(gift.claims, pair_key_)  # type: ignore[union-attr]
            w.step("gift_card", t)
            return
        w.payment_unknown = True
        w.reasons.append(f"no se sabe si el pago exige una tarjeta de {t}")
        return
    obtain = obtainable_card(t, profile, facts, w)
    if obtain is not None:
        w.step("obtain_instrument", obtain)
        w.step("payment_country", t)
        return
    if remote_gift:
        w.use(gift.claims, pair_key_)  # type: ignore[union-attr]
        w.step("gift_card", t)
        return
    w.practical.append("impossible")
    w.reasons.append(
        f"exige un medio de pago de {t} y el perfil no tiene ni puede obtener en línea uno"
    )


def requirement(
    facts: Facts,
    w: Work,
    key: str,
    field_name: str,
    *,
    assumed: str,
    required: str,
    assumption_es: str,
    implied: list[Claim] | None = None,
    implied_key: str = "",
) -> bool:
    """Whether the requirement holds: from the pair claim, from an account_country_basis claim
    that implies it (`implied`), or else assumed `assumed` (recorded as an assumption)."""
    res = facts.resolve(key, field_name)
    if res is not None:
        w.use(res.claims, key)
        if res.value == required:
            return True
    if implied:
        w.use(implied, implied_key)
        return True
    if res is None:
        w.assumptions.append(assumption_es)
        return assumed == required
    return False


def _practical(
    t: str, profile: Profile, facts: Facts, w: Work, svc_key: str, pair_key_: str
) -> None:
    avail = facts.resolve(pair_key_, "available")
    if avail is not None and avail.value == "false":
        w.use(avail.claims, pair_key_)
        w.practical.append("impossible")
        w.reasons.append(f"el servicio no se vende en {t}")
        return

    _payment(t, profile, facts, w, svc_key, pair_key_)
    basis = facts.values(svc_key, "account_country_basis")

    if requirement(
        facts,
        w,
        pair_key_,
        "signup_location",
        assumed="not_checked",
        required="checked",
        assumption_es=f"se asume que no se verifica la ubicación al registrarse en {t}",
        implied=basis.get("signup_location"),
        implied_key=svc_key,
    ):
        if t in profile.presence:
            w.practical.append("presence_once")
            w.step("presence", t, requires_presence=True)
        else:
            w.practical.append("impossible")
            w.reasons.append(f"exige estar en {t} al registrarse y el perfil no viaja a {t}")

    if requirement(
        facts,
        w,
        pair_key_,
        "periodic_location_check",
        assumed="false",
        required="true",
        assumption_es=f"se asume que no hay verificaciones periódicas de ubicación en {t}",
    ):
        if profile.presence.get(t) == "periodic":
            w.practical.append("presence_periodic")
            w.step("presence", t, requires_presence=True)
        else:
            w.practical.append("impossible")
            w.reasons.append(f"exige volver a {t} periódicamente")

    if requirement(
        facts,
        w,
        pair_key_,
        "local_id_required",
        assumed="false",
        required="true",
        assumption_es=f"se asume que no se pide un documento de {t}",
    ):
        if t in profile.documents():
            w.step("local_id", t)
        else:
            w.practical.append("impossible")
            w.reasons.append(f"exige un documento de {t}")

    if requirement(
        facts,
        w,
        pair_key_,
        "local_phone_required",
        assumed="false",
        required="true",
        assumption_es=f"se asume que no se pide un teléfono de {t}",
        implied=basis.get("phone_number"),
        implied_key=svc_key,
    ):
        if t in profile.phones():
            w.step("local_phone", t)
        else:
            w.practical.append("impossible")
            w.reasons.append(f"exige un número de teléfono de {t}")

    if requirement(
        facts,
        w,
        pair_key_,
        "billing_address",
        assumed="not_required",
        required="required",
        assumption_es=f"se asume que no se pide una dirección de facturación en {t}",
        implied=basis.get("billing_address"),
        implied_key=svc_key,
    ):
        if t in profile.addresses():
            w.step("billing_address", t)
        else:
            w.false_declaration = True
            w.step("billing_address", t, requires_false_declaration=True)
            w.reasons.append(
                f"exige una dirección de facturación en {t} y el perfil no tiene una real"
            )

    if "self_declared" in basis and t != profile.residence_country:
        w.use(basis["self_declared"], svc_key)
        w.false_declaration = True
        w.step("declare_country", t, requires_false_declaration=True)
        w.reasons.append(f"exige declarar {t} como país propio sin vivir allí")


def tos_axis(
    facts: Facts,
    w: Work,
    svc_key: str,
    *,
    clause_field: str = "residence_clause",
    event_field: str = "enforcement_event",
    allowed_field: str = "foreign_offer_allowed",
) -> tuple[str, list[Claim]]:
    """The ToS axis of one subject (rule 6). The field names are parameters so a bundle subject
    reads its bundle_ twins (bundle_residence_clause ...) with the same rule."""
    clause = facts.resolve(svc_key, clause_field)
    today = facts.ctx.today
    # Any documented event shows the rule exists; only recent ones make it "enforced".
    events = [
        c
        for c in facts.usable(svc_key, event_field)
        if c.event_date is not None and c.event_date <= today
    ]
    recent = [c for c in events if (today - c.event_date).days <= ENFORCEMENT_WINDOW_DAYS]  # type: ignore[operator]
    if clause is not None or events:
        used = [*(clause.claims if clause else []), *events]
        w.use(used, svc_key)
        if clause is not None:
            w.reasons.append("los términos exigen: " + value_label_es(clause_field, clause.value))
        if recent:
            kinds = sorted({value_label_es(event_field, c.value) for c in recent})
            w.reasons.append("control documentado: " + ", ".join(kinds))
            return "breach_enforced", used
        if events:
            w.reasons.append("hubo controles, pero hace más de dos años")
        return "breach_unenforced", used
    allowed = facts.resolve(svc_key, allowed_field)
    if allowed is not None:
        w.use(allowed.claims, svc_key)
        return "compliant", allowed.claims
    w.reasons.append("no se encontró en los términos una regla sobre el país de residencia")
    return UNKNOWN_AXIS, []


# ------------------------------------------------------------------------ public


def _evidence(claims: Iterable[Claim]) -> tuple[Evidence, ...]:
    out: list[Evidence] = []
    for c in sorted(claims, key=lambda c: c.id):
        out.append(
            Evidence(
                url=c.url,
                kind=c.check.source_class,
                retrieved_at=c.check.retrieved_at.date() if c.check.retrieved_at else None,
                quote=c.quote,
                verified=c.check.status == "verified",
            )
        )
    return tuple(out)


def _curated(record: FeasibilityRecord) -> Assessment:
    rank = record_rank(record)
    evidence = list(record.evidence)
    if record.tos_clause is not None:
        evidence.insert(
            0, Evidence(url=record.tos_clause.url, kind="official", quote=record.tos_clause.quote)
        )
    return Assessment(
        service_id=record.service_id,
        target_country=record.target_country,
        residence_country=record.residence_country,
        profile=record.profile,
        legality=record.legality,
        tos=record.tos,
        practical=record.practical,
        rank=rank,
        label=LABELS[rank],
        eu_badge=eu_badge(record),
        confidence=record.confidence,
        provenance="curated",
        status=record.status,
        steps=tuple(record.steps),
        legal_basis=tuple(record.legal_basis),
        evidence=tuple(evidence),
        researched_on=record.last_verified,
        record_id=record.id,
        notes_es=" ".join(record.notes_es.split()),
    )


def assess(
    service_id: str,
    target: str,
    profile: Profile,
    ctx: EvalContext,
    *,
    include_pending: bool = False,
) -> Assessment:
    r = profile.residence_country
    base = dict(service_id=service_id, target_country=target, residence_country=r)
    if target == r:
        return Assessment(
            **base,
            profile=profile.id,
            legality="lawful",
            tos="compliant",
            practical="remote",
            rank=0,
            label=LABELS[0],
            eu_badge=False,
            confidence="high",
            provenance="home",
            status="verified",
        )
    record = find_record(
        ctx.feasibility, service_id, target, residence_country=r, profile=profile.id
    )
    if record is not None:
        return _curated(record)

    facts = Facts(ctx, include_pending=include_pending)
    svc_key = subject_key("service", service_id)
    pair_key_ = subject_key("pair", service_id, target)
    unknown = Assessment(
        **base,
        profile=profile.id,
        legality=UNKNOWN_AXIS,
        tos=UNKNOWN_AXIS,
        practical=UNKNOWN_AXIS,
        rank=UNKNOWN_RANK,
        label=UNKNOWN_LABEL,
        eu_badge=False,
        confidence="low",
        provenance="none",
    )
    if facts.subject(svc_key) is None and facts.subject(pair_key_) is None:
        return unknown

    w = Work()
    tos, tos_claims = tos_axis(facts, w, svc_key)
    _practical(target, profile, facts, w, svc_key, pair_key_)
    if not w.claims:
        return unknown  # researched, but nothing usable for this profile (or all pending)

    practical = w.practical_level()
    if w.false_declaration:
        practical = "impossible"
    elif practical != "impossible" and w.payment_unknown:
        practical = UNKNOWN_AXIS

    legal_basis: tuple[LegalBasis, ...] = ()
    try:
        svc = ctx.catalog.service(service_id)
        eservice = svc.eu_geo_class == "eservice_art4_1b"
    except KeyError:
        eservice = False
    barrier = tos.startswith("breach") or practical not in ("remote", UNKNOWN_AXIS)
    if w.false_declaration:
        legality = "requires_misrepresentation"
    elif eservice and eea(ctx, r) and eea(ctx, target) and barrier:
        legality = "contestable_eu_right"
        legal_basis = (EU_LEGAL_BASIS,)
    else:
        legality = "lawful"

    if practical == "impossible":
        rank = 4
    elif UNKNOWN_AXIS in (tos, practical):
        rank = UNKNOWN_RANK
    else:
        rank = max(LEGALITY_RANK[legality], TOS_RANK[tos], PRACTICAL_RANK[practical])
        if rank < 2 and w.assumptions:
            rank = UNKNOWN_RANK
            w.reasons.append("faltan hechos con fuente oficial para descartar riesgos")

    claims = list(w.claims.values())
    pending = facts.pending(claims)
    no_assumptions = not w.assumptions
    if not pending and all(c.official for c in claims) and no_assumptions:
        confidence = "high"
    elif tos_claims and all(c.official for c in tos_claims) and no_assumptions:
        confidence = "medium"
    else:
        confidence = "low"
    researched = [
        facts.ctx.facts[k].researched_on for k in sorted(w.subjects) if k in facts.ctx.facts
    ]
    return Assessment(
        **base,
        profile=profile.id,
        legality=legality,
        tos=tos,
        practical=practical,
        rank=rank,
        label=LABELS[rank] if rank < len(LABELS) else UNKNOWN_LABEL,
        eu_badge=bool(legal_basis),
        confidence=confidence,
        provenance="agent_pending" if pending else "agent_reviewed",
        status="unverified" if pending else "verified",
        steps=tuple(w.steps),
        legal_basis=legal_basis,
        claim_ids=tuple(sorted(w.claims)),
        evidence=_evidence(claims),
        assumptions_es=tuple(dict.fromkeys(w.assumptions)),
        reasons_es=tuple(dict.fromkeys(w.reasons)),
        researched_on=min(researched) if researched else None,
    )


def verdict_fn(
    profile: Profile, ctx: EvalContext, *, include_pending: bool = False
) -> Callable[[str, str], Verdict]:
    """compare.cheapest(verdicts=...) for one profile (memoized per (service, country))."""
    cache: dict[tuple[str, str], Verdict] = {}

    def lookup(service_id: str, country: str) -> Verdict:
        key = (service_id, country)
        if key not in cache:
            cache[key] = assess(
                service_id, country, profile, ctx, include_pending=include_pending
            ).verdict()
        return cache[key]

    return lookup

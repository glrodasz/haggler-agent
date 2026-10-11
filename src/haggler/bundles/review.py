"""What `haggler research review` shows for bundles (read-only; no network, never writes).

For one bundle (`review --subject <bundle_id>`): its derived state, the watcher's pages and
anchors (a superseded versioned PDF with the newest edition's URL, and for each anchor of it
whether that edition still contains it), its open proposals with the value before -> after
(haggler.bundles.proposals: watcher hints and agent value findings), its feasibility findings
with their review state, the verdict of every public profile without and with the findings
still pending review
(haggler.bundles.evaluate.assess_bundle, include_pending), and a YAML snippet per proposal:
bundles.yaml is the only source of a bundle's values, so a value is accepted by pasting the
snippet into the YAML (in a PR), never by a review decision. A superseded versioned PDF gets its
own snippet (proposals.edition_snippets): the entry with the new edition's url and the anchors
that edition still contains.

For every bundle (`review --bundles --summary`): one line of counts for the publish.yml job
summary.
"""

from __future__ import annotations

from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from datetime import date

from haggler.bundles.evaluate import BundleAssessment, assess_bundle
from haggler.bundles.proposals import (
    SOURCE_ES,
    Proposal,
    bundle_proposals,
    edition_snippets,
    pending_changes,
    snippet,
)
from haggler.bundles.status import BundleState, anchor_checks, bundle_state, watched
from haggler.feasibility import LABEL_INFO
from haggler.models.bundles import BundleWatchState, PageCheck, WatchFile, anchor_hash
from haggler.models.curated import Bundle, BundlesFile
from haggler.models.profiles import Profile
from haggler.models.research import Claim, SubjectFacts, subject_key
from haggler.research.evaluate import EvalContext
from haggler.research.evidence import canonical_url
from haggler.research.fields import FIELD_SPECS
from haggler.research.review import STATUS_ES, ClaimReview, ReviewIndex, claim_lines

DISPLAY_ES = {
    "verified": "verificado",
    "needs_review": "requiere revisión",
    "manual": "verificación manual",
    "unverified": "sin verificar",
    "expired": "vencida",
}
ANCHOR_ES = {
    "verified": "encontrada",
    "missing": "no encontrada",
    "unreadable": "página ilegible",
    "embedded_only": "solo en datos embebidos",
    "outdated_document": "documento desactualizado",
    "not_checked": "sin comprobar",
}
CHECK_ES = {"ok": "chequeo de valor superado", "unchecked": "sin chequeo de valor"}


@dataclass
class BundleReview:
    bundle: Bundle
    watch: BundleWatchState | None
    proposals: list[Proposal]
    # The bundle subject's feasibility findings (every review state).
    feasibility: list[tuple[Claim, ClaimReview]]
    state: BundleState

    @property
    def pending_feasibility(self) -> int:
        return sum(1 for _c, st in self.feasibility if st == "pending")

    @property
    def needs_review(self) -> bool:
        return (
            bool(self.proposals)
            or self.pending_feasibility > 0
            or self.state.display_status == "needs_review"
        )


def review_bundle(
    bundle: Bundle,
    *,
    watch: BundleWatchState | None,
    facts: SubjectFacts | None,
    review: ReviewIndex,
    today: date,
    service_names: dict[str, str] | None = None,
) -> BundleReview:
    proposals = bundle_proposals(bundle, watch=watch, facts=facts, review=review, today=today)
    feasibility = [
        (c, review.status(c))
        for c in (facts.claims if facts else [])
        if not (spec := FIELD_SPECS.get(c.field)) or not spec.yaml_value
    ]
    state = bundle_state(
        bundle,
        today=today,
        watch=watch,
        service_names=service_names,
        pending_changes=pending_changes(proposals),
    )
    return BundleReview(bundle, watch, proposals, feasibility, state)


def review_all(
    bundles: BundlesFile, *, watch: WatchFile | None, ctx: EvalContext
) -> list[BundleReview]:
    """Every bundle of bundles.yaml, in its order."""
    states = {s.bundle_id: s for s in watch.bundles} if watch is not None else {}
    names = {s.id: s.name for s in ctx.catalog.services}
    return [
        review_bundle(
            b,
            watch=states.get(b.bundle_id),
            facts=ctx.facts.get(subject_key("bundle", b.bundle_id)),
            review=ctx.review,
            today=ctx.today,
            service_names=names,
        )
        for b in bundles.bundles
    ]


# ------------------------------------------------------------------ report


def _label(a: BundleAssessment | None) -> str:
    if a is None:
        return "sin veredicto (oferta vencida)"
    return f"{LABEL_INFO.get(a.label, (a.label, ''))[0]} (nivel {a.rank})"


def proposal_lines(p: Proposal) -> list[str]:
    tag = SOURCE_ES[p.source] if p.source == "watcher" else STATUS_ES[p.review]
    flags = [SOURCE_ES[p.source], CHECK_ES.get(p.value_check, p.value_check)]
    if not p.official:
        flags.append("fuente no oficial")
    if p.big_change:
        flags.append("cambio grande")
    seen = p.retrieved_at.isoformat() if p.retrieved_at else "—"
    since = f", desde el {p.first_seen.isoformat()}" if p.first_seen else ""
    return [
        f"  [{tag}] {p.id}  hash={p.hash}",
        f"      {p.label_es}: {p.current or '—'} -> {p.proposed}  ({'; '.join(flags)})",
        f"      consultada el {seen}{since}: {p.url}",
        f"      “{' '.join(p.quote.split())}”",
    ]


def report_lines(
    r: BundleReview,
    ctx: EvalContext,
    profiles: Sequence[Profile],
    *,
    bundles: BundlesFile | None = None,
    show_all: bool = False,
) -> list[str]:
    """The report of one bundle; `bundles` (bundles.yaml) for the snippets' offline check."""
    b = r.bundle
    out = [
        f"Paquete {b.bundle_id} — {b.name} ({b.country}) · vigilancia {b.watch.mode} · "
        f"estado: {DISPLAY_ES.get(r.state.display_status, r.state.display_status)}"
    ]
    out += [f"  · {reason}" for reason in r.state.reasons_es]

    w = watched(b, r.watch)
    out.append("")
    if r.watch is None:
        out.append("Vigilancia: el vigilante aún no ha leído este paquete.")
    else:
        when = r.watch.checked_at.isoformat() if r.watch.checked_at else "—"
        counted = "" if w is not None else " (no cuenta: el paquete no está en vigilancia auto)"
        out.append(f"Vigilancia: última lectura {when}, resultado {r.watch.outcome}{counted}")
        for page in r.watch.pages:
            extra = f", edición {page.edition}" if page.edition else ""
            if page.newest_edition and page.newest_edition != page.edition:
                extra += f", la más nueva {page.newest_edition}"
                if page.newest_url:
                    extra += f" ({page.newest_url})"
            out.append(f"  página {page.url}: {page.status}{extra}")
    superseded = {
        canonical_url(p.url): p for p in (r.watch.pages if r.watch else []) if _superseded(p)
    }
    for anchor, check in anchor_checks(b, r.watch):
        what = anchor.fact + (f" {anchor.service_id}" if anchor.service_id else "")
        status = check.status if check else "not_checked"
        verified = (
            f", verificada el {check.last_verified_at.isoformat()}"
            if check and check.last_verified_at
            else ""
        )
        newer = ""
        if (page := superseded.get(canonical_url(str(anchor.url)))) is not None:
            if page.newest_status != "ok":
                newer = f"; la edición {page.newest_edition} no se pudo leer"
            elif anchor_hash(anchor) in page.newest_anchors:
                newer = f"; la edición {page.newest_edition} la contiene"
            else:
                newer = f"; no está en la edición {page.newest_edition}"
        out.append(f"  ancla {what}: {ANCHOR_ES.get(status, status)}{verified}{newer}")
    if not b.watch.anchors:
        out.append("  sin anclas en bundles.yaml")

    out.append("")
    out.append("Propuestas de valor (antes -> después; se aceptan editando bundles.yaml):")
    if not r.proposals:
        out.append("  ninguna")
    for p in r.proposals:
        out += proposal_lines(p)

    out.append("")
    out.append("Hallazgos de factibilidad (se aceptan por id: haggler research accept <id>):")
    shown = [(c, st) for c, st in r.feasibility if show_all or st == "pending"]
    if not shown:
        out.append("  sin hallazgos pendientes")
    for c, st in shown:
        out += claim_lines(c, st)

    out.append("")
    out.append("Impacto por perfil (sin pendientes → aceptando los pendientes):")
    for profile in profiles:
        before = assess_bundle(b, profile, ctx, state=r.state)
        after = assess_bundle(b, profile, ctx, include_pending=True, state=r.state)
        same = (before is None and after is None) or (
            before is not None and after is not None and before.same_verdict(after)
        )
        change = ", sin cambio" if same else f" → {_label(after)}"
        out.append(f"  {profile.id}: {_label(before)}{change}")

    editions = edition_snippets(b, r.watch)
    if r.proposals or editions:
        out.append("")
        out.append(
            "Snippets para data/curated/bundles.yaml (pégalos en la fila del paquete en un PR; "
            "validate-catalog comprueba el ancla):"
        )
        for e in editions:
            out.append("")
            out.append("# edición nueva de la lista de precios")
            out += e.text().splitlines()
        # One snippet per value and evidence: the watcher and the agent may read the same line.
        for group in same_evidence(r.proposals):
            p = group[0]
            ids = ", ".join(x.id for x in group)
            out.append("")
            out.append(f"# {p.label_es}: {p.current or '—'} -> {p.proposed} ({ids})")
            out += snippet(b, p, bundles=bundles, catalog=ctx.catalog).text().splitlines()
    return out


def same_evidence(proposals: Iterable[Proposal]) -> list[list[Proposal]]:
    """Proposals grouped by (field, proposed value, hash), in their order: the same value on
    the same evidence from the watcher and the agent is one change to paste (a rejection of
    one hides the others, proposals.bundle_proposals)."""
    groups: dict[tuple[str, str, str], list[Proposal]] = {}
    for p in proposals:
        groups.setdefault((p.field, p.proposed, p.hash), []).append(p)
    return list(groups.values())


def _superseded(page: PageCheck) -> bool:
    return page.status == "superseded" and bool(page.newest_url)


# ------------------------------------------------------------------ summary


def _plural(n: int, one: str, many: str) -> str:
    return f"{n} {one if n == 1 else many}"


def summary_line(reviews: Iterable[BundleReview]) -> str:
    """One line for the publish.yml job summary."""
    reviews = list(reviews)
    # One per (field, value) of a bundle, as bundles.json pending_changes counts them.
    values = sum(len({(p.field, p.proposed) for p in r.proposals}) for r in reviews)
    feasibility = sum(r.pending_feasibility for r in reviews)
    flagged = [r.bundle.bundle_id for r in reviews if r.needs_review]
    if not flagged:
        return "Paquetes: nada pendiente de revisión."
    counts = [
        _plural(values, "propuesta de valor pendiente", "propuestas de valor pendientes"),
        _plural(
            feasibility,
            "hallazgo de factibilidad pendiente",
            "hallazgos de factibilidad pendientes",
        ),
    ]
    bundles = _plural(len(flagged), "paquete por revisar", "paquetes por revisar")
    return f"Paquetes: {', '.join(counts)}; {bundles}: {', '.join(flagged)}."

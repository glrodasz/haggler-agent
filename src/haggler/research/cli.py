"""`haggler research review | accept | reject`: review research findings (no ADK, no network).

    haggler research review [--subject netflix] [--profile P] [--all]
        read-only: the findings in data/research with their review state and, per profile, how
        each affected verdict changes when the pending findings are accepted
    haggler research review --subject <bundle_id> | --bundles [--summary]
        read-only, per bundle (haggler.bundles.review): its state, the watcher's anchors and
        hints, the value proposals (before -> after), the feasibility findings, the verdict of
        each profile without and with the pending findings, and a ready-to-paste bundles.yaml
        snippet per proposal. --bundles: every bundle with something to review; --summary: one
        line of counts (the publish.yml job summary)
    haggler research accept SELECTOR... [--note TEXT] [--by NAME] [--date YYYY-MM-DD]
    haggler research reject SELECTOR... [--note TEXT] [--by NAME] [--date YYYY-MM-DD]
        append one decision per matching claim to data/curated/research_review.yaml (the file is
        appended to, never re-dumped, so comments and history stay)
    haggler research propose [--body FILE] [--date YYYY-MM-DD]
        append a proposed decision for every pending finding (accept when the source is
        official, reject otherwise) and write FILE, a Markdown review of each one with its quote:
        publish.yml puts both in a pull request, so merging it is the review

SELECTOR: a claim id, a subject key ("pair:netflix:CO"), or a bare service/instrument/bundle id
("netflix" = the service's subject and every pair of that service; see research.kinds), or the
id of a watcher proposal ("watch:<bundle_id>:<field>:<value>", haggler.bundles.proposals).

Bundles: bundles.yaml is the only source of a bundle's values and nothing here edits it.
* accept of a bundle VALUE (a value finding, research.fields yaml_value, or a watcher proposal)
  is refused with exit 2 and prints the snippet to paste into the YAML instead;
* accept of a bundle's FEASIBILITY findings takes their claim ids: a bare bundle id or a
  "bundle:<id>" key is refused (exit 2);
* reject works for every bundle finding and proposal (by id, or the whole bundle by its id): it
  hides the proposal until new evidence (a new hash) brings it back.
A refused accept writes nothing, not even the other selectors' decisions.
"""

from __future__ import annotations

import argparse
import re
import sys
from datetime import UTC, date, datetime
from pathlib import Path

import yaml
from pydantic import ValidationError

from haggler.bundles.proposals import Proposal, bundle_proposals, resolved, snippet
from haggler.bundles.review import report_lines, review_all, summary_line
from haggler.catalog import (
    load_bundles,
    load_catalog,
    load_feasibility,
    load_payment_instruments,
    load_profiles,
    load_review,
    synthesize_profiles,
)
from haggler.config import repo_root
from haggler.feasibility import LABEL_INFO
from haggler.models.bundles import WatchFile
from haggler.models.curated import Bundle, BundlesFile
from haggler.models.research import ReviewDecision, ReviewFile, SubjectFacts, subject_key
from haggler.research.evaluate import EvalContext, assess
from haggler.research.fields import FIELD_SPECS
from haggler.research.io import load_data_dir, load_data_watch
from haggler.research.kinds import KINDS, parse_key
from haggler.research.review import ReviewIndex, claim_lines, select_claims

REVIEW_FILE = "research_review.yaml"


def _err(message: str) -> None:
    print(f"haggler research: {message}", file=sys.stderr)


def _label(label: str) -> str:
    return LABEL_INFO.get(label, (label, ""))[0]


def _affected_pairs(subjects: list[SubjectFacts], ctx: EvalContext) -> set[tuple[str, str]]:
    """(service, country) pairs whose verdict can depend on these subjects (the pair_scope of
    their kind in research.kinds)."""
    pairs: set[tuple[str, str]] = set()
    # kinds._check: a "self" kind's ids are (service_id, country), a "service" kind's start with
    # service_id. Malformed keys are skipped where their ids cannot name a pair.
    researched = [ref.ids for k in ctx.facts if (ref := KINDS["pair"].parse(k)) is not None]
    for s in subjects:
        spec, ref = KINDS[s.kind], s.ref
        if spec.pair_scope == "self":
            if len(ref.ids) == len(spec.parts):
                pairs.add((ref.ids[0], ref.ids[1]))
        elif spec.pair_scope == "service":
            if ref.owner is not None:
                pairs |= {(p[0], p[1]) for p in researched if p[0] == ref.owner}
        elif spec.pair_scope == "all":
            pairs |= {(p[0], p[1]) for p in researched}
    return pairs


def _context(args: argparse.Namespace, review: ReviewFile) -> tuple[EvalContext, list]:
    catalog = load_catalog(args.catalog_dir)
    feasibility = load_feasibility(args.curated_dir)
    profiles = load_profiles(args.curated_dir) or synthesize_profiles(
        feasibility, residence="SE", profile="se_resident_co_citizen"
    )
    subjects, _run, errors = load_data_dir(args.data_dir)
    for e in errors:
        _err(f"aviso: {e}")
    ctx = EvalContext.build(
        catalog=catalog,
        feasibility=feasibility,
        instruments=load_payment_instruments(args.curated_dir, missing_ok=True),
        subjects=subjects,
        review=review,
        today=args.date or datetime.now(UTC).date(),
    )
    return ctx, [p for p in profiles.public()]


def _load_bundles(args: argparse.Namespace) -> BundlesFile:
    """bundles.yaml (none when the curated directory has no bundles file)."""
    base = args.curated_dir or repo_root() / "data" / "curated"
    if not (base / "bundles.yaml").exists():
        return BundlesFile(bundles=[])
    return load_bundles(args.curated_dir)


def _load_watch(args: argparse.Namespace) -> WatchFile | None:
    watch, errors = load_data_watch(args.data_dir)
    for e in errors:
        _err(f"aviso: {e}")
    return watch


def _bundle_named(selector: str, bundles: BundlesFile, subjects: list[SubjectFacts]) -> str | None:
    """The bundle a selector names: its id, its subject key, a claim id of its subject or a
    watcher proposal id ("watch:<bundle_id>:..."); None for anything else."""
    ids = {b.bundle_id for b in bundles.bundles}
    if selector in ids:
        return selector
    ref = parse_key(selector)
    if ref is not None and ref.kind == "bundle":
        return ref.owner
    if selector.startswith("watch:"):
        return selector.split(":")[1] or None
    for subject, _claim in select_claims(subjects, selector):
        if subject.kind == "bundle":
            return subject.ref.owner
    return None


def _review_bundles(
    args: argparse.Namespace,
    ctx: EvalContext,
    profiles: list,
    bundles: BundlesFile,
    bundle_id: str | None,
) -> int:
    reviews = review_all(bundles, watch=_load_watch(args), ctx=ctx)
    if args.summary:
        print(summary_line(reviews))
        return 0
    if bundle_id is not None:
        chosen = [r for r in reviews if r.bundle.bundle_id == bundle_id]
        if not chosen:
            _err(f"el paquete {bundle_id} no está en bundles.yaml")
            return 2
    else:
        chosen = [r for r in reviews if r.needs_review]
        if not chosen:
            print("Ningún paquete tiene nada pendiente de revisión.")
            return 0
    for i, r in enumerate(chosen):
        if i:
            print()
        print("\n".join(report_lines(r, ctx, profiles, bundles=bundles, show_all=args.all)))
    return 0


def _bundle_pointer(args: argparse.Namespace, ctx: EvalContext, bundles: BundlesFile) -> None:
    """After the general review: the bundles with something to review (their own report)."""
    reviews = review_all(bundles, watch=_load_watch(args), ctx=ctx) if bundles.bundles else []
    flagged = [r.bundle.bundle_id for r in reviews if r.needs_review]
    if flagged:
        print(
            f"Paquetes por revisar: {', '.join(flagged)} (haggler research review --subject <id>)."
        )


def _is_bundle_value(field_name: str) -> bool:
    spec = FIELD_SPECS.get(field_name)
    return spec is not None and spec.yaml_value


def cmd_review(args: argparse.Namespace) -> int:
    if args.summary and not args.bundles:
        _err("--summary va con --bundles")
        return 2
    review = load_review(args.curated_dir)
    ctx, profiles = _context(args, review)
    index = ReviewIndex(review)
    subjects = sorted(ctx.facts.values(), key=lambda s: s.key)
    if args.profile:
        profiles = [p for p in profiles if p.id == args.profile]
        if not profiles:
            _err(f"perfil desconocido: {args.profile}")
            return 2
    bundles = _load_bundles(args)
    bundle_id = _bundle_named(args.subject, bundles, subjects) if args.subject else None
    if args.bundles or bundle_id is not None:
        return _review_bundles(args, ctx, profiles, bundles, bundle_id)
    if args.subject:
        keys = {s.key for s, _ in select_claims(subjects, args.subject)}
        subjects = [s for s in subjects if s.key in keys or s.key == args.subject]
    if not subjects:
        print("No hay hallazgos de investigación en data/research para esa selección.")
        if not args.subject:
            _bundle_pointer(args, ctx, bundles)
        return 0
    pending_total = 0
    for s in subjects:
        # A bundle's VALUE findings are proposals, never decided here: the bundle's own report
        # lists the open ones (the YAML may already hold the value) and the pointer below
        # names the bundles that have any.
        values = {c.id for c in s.claims if s.kind == "bundle" and _is_bundle_value(c.field)}
        claims = [(c, index.status(c)) for c in s.claims if c.id not in values]
        shown = [(c, st) for c, st in claims if args.all or st == "pending"]
        pending_total += sum(1 for _, st in claims if st == "pending")
        print(f"{s.key} — investigado el {s.researched_on.isoformat()} ({s.status})")
        if not shown:
            print("  sin hallazgos pendientes")
        for c, st in shown:
            print("\n".join(claim_lines(c, st)))
        if values:
            print(
                f"  valores del paquete: propuestas en haggler research review --subject "
                f"{s.ref.owner}"
            )
        for q in s.open_questions:
            print(f"  ? {q}")
    print()
    print("Impacto en los veredictos (sin pendientes → aceptando los pendientes):")
    changed = 0
    for p in profiles:
        for service_id, cc in sorted(_affected_pairs(subjects, ctx)):
            before = assess(service_id, cc, p, ctx)
            after = assess(service_id, cc, p, ctx, include_pending=True)
            if (before.label, before.rank) == (after.label, after.rank):
                continue
            changed += 1
            print(f"  {p.id} · {service_id} {cc}: {_label(before.label)} → {_label(after.label)}")
    if not changed:
        print("  ningún veredicto cambia")
    print(f"\n{pending_total} hallazgo(s) pendiente(s).")
    if not args.subject:
        _bundle_pointer(args, ctx, bundles)
    return 0


_EMPTY_LIST = re.compile(r"^decisions:\s*\[\s*\]\s*(#.*)?$")
_ITEM = re.compile(r"^(\s*)- ")


def _yaml_item(decision: ReviewDecision, indent: str = "  ") -> str:
    item = decision.model_dump(mode="json")
    if not item.get("by"):
        item.pop("by")
    if not item.get("note"):
        item.pop("note")
    dumped = yaml.safe_dump([item], sort_keys=False, allow_unicode=True, width=1000)
    return "".join(f"{indent}{line}\n" for line in dumped.splitlines())


def append_decisions(path: Path, decisions: list[ReviewDecision]) -> None:
    """Append to the YAML list `decisions:` without rewriting the rest of the file; the result is
    re-validated and the original restored if it would not load."""
    original = path.read_text(encoding="utf-8") if path.exists() else "decisions: []\n"
    lines = original.splitlines()
    for i, line in enumerate(lines):
        m = _EMPTY_LIST.match(line.strip())
        if m:
            lines[i] = "decisions:" + (f"  {m.group(1)}" if m.group(1) else "")
    text = "\n".join(lines).rstrip("\n") + "\n"
    if not any(line.startswith("decisions:") for line in lines):
        text += "decisions:\n"
    # Follow the file's own list layout ("  - id:" or PyYAML's unindented "- id:").
    start = next(i for i, line in enumerate(text.splitlines()) if line.startswith("decisions:"))
    indent = next(
        (m.group(1) for line in text.splitlines()[start + 1 :] if (m := _ITEM.match(line))),
        "  ",
    )
    text += "".join(_yaml_item(d, indent) for d in decisions)
    path.write_text(text, encoding="utf-8")
    try:
        loaded = ReviewFile.model_validate(yaml.safe_load(text) or {})
    except (ValidationError, yaml.YAMLError) as exc:
        path.write_text(original, encoding="utf-8")
        raise ValueError(f"{path} quedaría inválido; no se modificó ({exc})") from exc
    if loaded.decisions[-len(decisions) :] != decisions:
        path.write_text(original, encoding="utf-8")
        raise ValueError(f"{path}: las decisiones no quedaron al final de la lista; no se modificó")


class _BundleSide:
    """bundles.yaml, the watch state and the bundles' proposals, loaded on first use (a
    decision on Phase 2 findings never reads them)."""

    def __init__(
        self,
        args: argparse.Namespace,
        subjects: list[SubjectFacts],
        index: ReviewIndex,
        today: date,
    ) -> None:
        self.args, self.subjects, self.index, self.today = args, subjects, index, today
        self._bundles: BundlesFile | None = None
        self._watch: WatchFile | None = None
        self._props: dict[str, list[Proposal]] = {}

    @property
    def bundles(self) -> BundlesFile:
        if self._bundles is None:
            self._bundles = _load_bundles(self.args)
            self._watch = _load_watch(self.args)
        return self._bundles

    def bundle(self, bundle_id: str | None) -> Bundle | None:
        return next((b for b in self.bundles.bundles if b.bundle_id == bundle_id), None)

    def proposals(self, bundle_id: str | None) -> list[Proposal]:
        """Open proposals of a bundle, rejected ones included (to say "ya estaba")."""
        b = self.bundle(bundle_id)
        if b is None:
            return []
        if b.bundle_id not in self._props:
            facts = {s.key: s for s in self.subjects}.get(subject_key("bundle", b.bundle_id))
            self._props[b.bundle_id] = bundle_proposals(
                b,
                watch=self._watch.get(b.bundle_id) if self._watch else None,
                facts=facts,
                review=self.index,
                today=self.today,
                include_rejected=True,
            )
        return self._props[b.bundle_id]

    def value_refusal(
        self, bundle_id: str | None, id_: str, hash_: str, field: str, value: str
    ) -> str:
        head = (
            f"no se acepta aquí: {id_} es un valor del paquete {bundle_id}; se acepta editando "
            "data/curated/bundles.yaml (pega este snippet en la fila del paquete, en un PR):"
        )
        b = self.bundle(bundle_id)
        if b is None:
            return f"{head}\n  el paquete {bundle_id} no está en bundles.yaml"
        if resolved(b, field, value, self.today):
            return (
                f"no se acepta aquí: {id_} es un valor del paquete {bundle_id}, y "
                "data/curated/bundles.yaml ya lo tiene: no hay nada que aceptar"
            )
        p = next((p for p in self.proposals(bundle_id) if (p.id, p.hash) == (id_, hash_)), None)
        if p is None:
            return f"{head}\n  sin propuesta abierta para {id_}"
        text = snippet(b, p, bundles=self.bundles).text()
        return head + "\n" + "\n".join(f"    {line}" for line in text.splitlines())

    def whole_refusal(self, selector: str, bundle_id: str | None) -> str:
        key = subject_key("bundle", bundle_id or "")
        pending = [
            c.id
            for s in self.subjects
            if s.key == key
            for c in s.claims
            if not (spec := FIELD_SPECS.get(c.field)) or not spec.yaml_value
            if self.index.status(c) == "pending"
        ]
        lines = [
            f"no se acepta {selector!r} entero: en un paquete, cada hallazgo de factibilidad se "
            "acepta por su id y cada valor editando data/curated/bundles.yaml (haggler research "
            f"review --subject {bundle_id})"
        ]
        lines += [f"  hallazgo de factibilidad pendiente: {cid}" for cid in pending]
        return "\n".join(lines)


def _whole_bundle(selector: str, matched: list, side: _BundleSide) -> str | None:
    """The bundle id when the selector names a whole bundle (its id or its subject key)."""
    ref = parse_key(selector)
    if ref is not None and ref.kind == "bundle":
        return ref.owner
    if any(s.kind == "bundle" and selector == s.ref.owner for s, _c in matched):
        return selector
    if (
        not matched
        and not selector.startswith("watch:")
        and any(b.bundle_id == selector for b in side.bundles.bundles)
    ):
        return selector
    return None


def cmd_decide(args: argparse.Namespace, choice: str) -> int:
    path = (args.curated_dir or repo_root() / "data" / "curated") / REVIEW_FILE
    review = load_review(args.curated_dir)
    index = ReviewIndex(review)
    subjects, _run, errors = load_data_dir(args.data_dir)
    for e in errors:
        _err(f"aviso: {e}")
    on = args.date or datetime.now(UTC).date()
    side = _BundleSide(args, subjects, index, on)
    decisions: list[ReviewDecision] = []
    refusals: list[str] = []
    seen: set[tuple[str, str]] = set()

    def decide(id_: str, hash_: str, exact: bool) -> None:
        current = index.lookup(id_, hash_)
        if current is not None and current.decision == choice:
            print(f"ya estaba: {choice} {id_}")
            return
        if current is not None and not exact:
            # A subject-wide selector only decides pending findings; changing an earlier
            # decision takes the exact claim id.
            print(f"ya revisado ({current.decision}), se deja igual: {id_}")
            return
        decisions.append(
            ReviewDecision(
                id=id_,
                hash=hash_,
                decision=choice,  # type: ignore[arg-type]
                decided_on=on,
                by=args.by or "",
                note=args.note or "",
            )
        )

    for selector in args.selectors:
        matched = select_claims(subjects, selector)
        whole = _whole_bundle(selector, matched, side)
        if whole is not None and choice == "accept":
            refusals.append(side.whole_refusal(selector, whole))
            continue
        hints: list[Proposal] = []
        if selector.startswith("watch:"):
            bundle_id = selector.split(":")[1]
            hints = [p for p in side.proposals(bundle_id) if p.id == selector]
        elif whole is not None:
            hints = [p for p in side.proposals(whole) if p.source == "watcher"]
        if not matched and not hints:
            _err(f"ningún hallazgo coincide con {selector!r}")
            return 2
        for subject, claim in matched:
            key = (claim.id, claim.hash)
            if key in seen:
                continue
            seen.add(key)
            spec = FIELD_SPECS.get(claim.field)
            if choice == "accept" and spec is not None and spec.yaml_value:
                refusals.append(
                    side.value_refusal(
                        subject.ref.owner, claim.id, claim.hash, claim.field, claim.value
                    )
                )
                continue
            decide(claim.id, claim.hash, exact=selector == claim.id)
        for p in hints:
            if (p.id, p.hash) in seen:
                continue
            seen.add((p.id, p.hash))
            if choice == "accept":
                refusals.append(side.value_refusal(p.bundle_id, p.id, p.hash, p.field, p.proposed))
                continue
            decide(p.id, p.hash, exact=selector == p.id)
    if refusals:
        for r in refusals:
            print(r)
        print("No se añadió ninguna decisión.")
        return 2
    if not decisions:
        print("Sin cambios.")
        return 0
    try:
        append_decisions(path, decisions)
    except ValueError as exc:
        _err(str(exc))
        return 1
    verb = "aceptado" if choice == "accept" else "rechazado"
    for d in decisions:
        print(f"{verb}: {d.id} ({d.hash})")
    print(f"{len(decisions)} decisión(es) añadida(s) a {path}")
    return 0


PROPOSER = "automatic proposal"
_MAX_BODY = 60_000  # GitHub caps a pull request body at 65 536 characters


def _md_cell(text: str) -> str:
    return " ".join(text.split()).replace("|", "\\|")


def cmd_propose(args: argparse.Namespace) -> int:
    """One decision per pending finding, so a pull request carries the whole review: an official
    source is proposed for acceptance and anything else for rejection, and the reviewer flips
    the ones the quote does not support. Bundle values are left out: they go in bundles.yaml."""
    path = (args.curated_dir or repo_root() / "data" / "curated") / REVIEW_FILE
    index = ReviewIndex(load_review(args.curated_dir))
    subjects, _run, errors = load_data_dir(args.data_dir)
    for e in errors:
        _err(f"aviso: {e}")
    on = args.date or datetime.now(UTC).date()
    decisions: list[ReviewDecision] = []
    rows: list[str] = []
    for s in sorted(subjects, key=lambda s: s.key):
        for c in s.claims:
            if index.status(c) != "pending":
                continue
            if s.kind == "bundle" and _is_bundle_value(c.field):
                continue
            choice = "accept" if c.official else "reject"
            decisions.append(
                ReviewDecision(
                    id=c.id,
                    hash=c.hash,
                    decision=choice,  # type: ignore[arg-type]
                    decided_on=on,
                    by=PROPOSER,
                    note=f"{c.check.source_class} source",
                )
            )
            fact = f"{c.field.replace('_', ' ')}: {c.value.replace('_', ' ')}"
            rows.append(
                f"| `{c.id}` | {_md_cell(fact)} | [{c.check.source_class}]({c.url}) | "
                f"{_md_cell(c.quote)} | **{choice}** |"
            )
    if not decisions:
        print("Sin hallazgos pendientes.")
        return 0
    append_decisions(path, decisions)
    if args.body is not None:
        accepted = sum(d.decision == "accept" for d in decisions)
        head = [
            f"{len(decisions)} research findings are waiting for review. This PR proposes "
            f"accepting {accepted} (official source) and rejecting {len(decisions) - accepted} "
            "(press or forum). **Merging it applies these decisions** in "
            f"`data/curated/{REVIEW_FILE}`.",
            "",
            "How to review: read each quote and check that it says what the fact claims. To "
            "change a proposal, edit its `decision:` in the diff (`accept` ↔ `reject`) with a "
            "suggestion or a commit on this branch; publish does not rewrite the branch while "
            "it has commits from someone else.",
            "",
            "| Finding | Fact | Source | Quote | Proposal |",
            "| --- | --- | --- | --- | --- |",
        ]
        body = "\n".join(head)
        for i, row in enumerate(rows):
            if len(body) + len(row) > _MAX_BODY:
                body += f"\n\n… and {len(rows) - i} more: `uv run haggler research review`."
                break
            body += "\n" + row
        args.body.write_text(body + "\n", encoding="utf-8")
    print(f"{len(decisions)} propuesta(s) añadida(s) a {path}")
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="haggler research", description=__doc__.split("\n")[0])
    sub = parser.add_subparsers(dest="action", required=True)

    def common(p: argparse.ArgumentParser) -> None:
        p.add_argument("--data-dir", type=Path, default=Path("data"))
        p.add_argument("--curated-dir", type=Path, default=None)
        p.add_argument("--catalog-dir", type=Path, default=None, help=argparse.SUPPRESS)
        p.add_argument("--date", type=date.fromisoformat, default=None, help="YYYY-MM-DD")

    review = sub.add_parser("review", help="lista los hallazgos y su impacto (solo lectura)")
    common(review)
    review.add_argument("--subject", default=None, help="id de servicio, instrumento o sujeto")
    review.add_argument("--profile", default=None)
    review.add_argument("--all", action="store_true", help="incluye los ya revisados")
    review.add_argument(
        "--bundles", action="store_true", help="todos los paquetes con algo por revisar"
    )
    review.add_argument(
        "--summary", action="store_true", help="con --bundles: una línea de recuentos"
    )
    propose = sub.add_parser("propose", help="propone una decisión por hallazgo pendiente")
    common(propose)
    propose.add_argument("--body", type=Path, default=None, help="Markdown para el PR")
    for name in ("accept", "reject"):
        p = sub.add_parser(name, help=f"{name} hallazgos (añade a {REVIEW_FILE})")
        common(p)
        p.add_argument("selectors", nargs="+", metavar="SELECTOR")
        p.add_argument("--note", default="")
        p.add_argument("--by", default="")
    return parser


def main(argv: list[str]) -> int:
    args = build_parser().parse_args(argv)
    try:
        if args.action == "review":
            return cmd_review(args)
        if args.action == "propose":
            return cmd_propose(args)
        return cmd_decide(args, args.action)
    except (ValidationError, ValueError, OSError, yaml.YAMLError) as exc:
        _err(f"{type(exc).__name__}: {exc}")
        return 1

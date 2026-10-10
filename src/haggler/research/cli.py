"""`haggler research review | accept | reject`: review research findings (no ADK, no network).

    haggler research review [--subject netflix] [--profile P] [--all]
        read-only: the findings in data/research with their review state and, per profile, how
        each affected verdict changes when the pending findings are accepted
    haggler research accept SELECTOR... [--note TEXT] [--by NAME] [--date YYYY-MM-DD]
    haggler research reject SELECTOR... [--note TEXT] [--by NAME] [--date YYYY-MM-DD]
        append one decision per matching claim to data/curated/research_review.yaml (the file is
        appended to, never re-dumped, so comments and history stay)

SELECTOR: a claim id, a subject key ("pair:netflix:CO"), or a bare service/instrument id
("netflix" = the service's subject and every pair of that service; see research.kinds).
"""

from __future__ import annotations

import argparse
import re
import sys
from datetime import UTC, date, datetime
from pathlib import Path

import yaml
from pydantic import ValidationError

from haggler.catalog import (
    load_catalog,
    load_feasibility,
    load_payment_instruments,
    load_profiles,
    load_review,
    synthesize_profiles,
)
from haggler.config import repo_root
from haggler.feasibility import LABEL_INFO
from haggler.models.research import Claim, ReviewDecision, ReviewFile, SubjectFacts
from haggler.research.evaluate import EvalContext, assess
from haggler.research.fields import FIELD_SPECS, value_label_es
from haggler.research.io import load_data_dir
from haggler.research.kinds import KINDS
from haggler.research.review import ReviewIndex, select_claims

REVIEW_FILE = "research_review.yaml"
STATUS_ES = {"accepted": "aceptada", "rejected": "rechazada", "pending": "pendiente"}
CLASS_ES = {"official": "oficial", "press": "prensa", "forum": "foro"}


def _err(message: str) -> None:
    print(f"haggler research: {message}", file=sys.stderr)


def _label(label: str) -> str:
    return LABEL_INFO.get(label, (label, ""))[0]


def _claim_line(claim: Claim, status: str) -> list[str]:
    spec = FIELD_SPECS.get(claim.field)
    field_es = spec.label_es if spec else claim.field
    when = f" ({claim.event_date.isoformat()})" if claim.event_date else ""
    retrieved = claim.check.retrieved_at.date().isoformat() if claim.check.retrieved_at else "—"
    return [
        f"  [{STATUS_ES[status]}] {claim.id}  hash={claim.hash}",
        f"      {field_es}: {value_label_es(claim.field, claim.value)}{when}",
        f"      fuente {CLASS_ES.get(claim.check.source_class, claim.check.source_class)}, "
        f"consultada el {retrieved}: {claim.url}",
        f"      “{claim.quote}”",
    ]


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


def cmd_review(args: argparse.Namespace) -> int:
    review = load_review(args.curated_dir)
    ctx, profiles = _context(args, review)
    index = ReviewIndex(review)
    subjects = sorted(ctx.facts.values(), key=lambda s: s.key)
    if args.subject:
        keys = {s.key for s, _ in select_claims(subjects, args.subject)}
        subjects = [s for s in subjects if s.key in keys or s.key == args.subject]
    if args.profile:
        profiles = [p for p in profiles if p.id == args.profile]
        if not profiles:
            _err(f"perfil desconocido: {args.profile}")
            return 2
    if not subjects:
        print("No hay hallazgos de investigación en data/research para esa selección.")
        return 0
    pending_total = 0
    for s in subjects:
        claims = [(c, index.status(c)) for c in s.claims]
        shown = [(c, st) for c, st in claims if args.all or st == "pending"]
        pending_total += sum(1 for _, st in claims if st == "pending")
        print(f"{s.key} — investigado el {s.researched_on.isoformat()} ({s.status})")
        if not shown:
            print("  sin hallazgos pendientes")
        for c, st in shown:
            print("\n".join(_claim_line(c, st)))
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


def cmd_decide(args: argparse.Namespace, choice: str) -> int:
    path = (args.curated_dir or repo_root() / "data" / "curated") / REVIEW_FILE
    review = load_review(args.curated_dir)
    index = ReviewIndex(review)
    subjects, _run, errors = load_data_dir(args.data_dir)
    for e in errors:
        _err(f"aviso: {e}")
    on = args.date or datetime.now(UTC).date()
    decisions: list[ReviewDecision] = []
    seen: set[tuple[str, str]] = set()
    for selector in args.selectors:
        matched = select_claims(subjects, selector)
        if not matched:
            _err(f"ningún hallazgo coincide con {selector!r}")
            return 2
        for _subject, claim in matched:
            key = (claim.id, claim.hash)
            if key in seen:
                continue
            seen.add(key)
            current = index.decision(claim)
            if current is not None and current.decision == choice:
                print(f"ya estaba: {choice} {claim.id}")
                continue
            if current is not None and selector != claim.id:
                # A subject-wide selector only decides pending findings; changing an earlier
                # decision takes the exact claim id.
                print(f"ya revisado ({current.decision}), se deja igual: {claim.id}")
                continue
            decisions.append(
                ReviewDecision(
                    id=claim.id,
                    hash=claim.hash,
                    decision=choice,  # type: ignore[arg-type]
                    decided_on=on,
                    by=args.by or "",
                    note=args.note or "",
                )
            )
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
        return cmd_decide(args, args.action)
    except (ValidationError, ValueError, OSError, yaml.YAMLError) as exc:
        _err(f"{type(exc).__name__}: {exc}")
        return 1

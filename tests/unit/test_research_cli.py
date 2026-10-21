"""haggler research review | accept | reject."""

from __future__ import annotations

import shutil
from datetime import UTC, date, datetime
from decimal import Decimal
from pathlib import Path

import pytest
import yaml

from conftest import FROZEN_CURATED
from haggler import cli
from haggler.catalog import load_review
from haggler.config import repo_root
from haggler.models.bundles import BundleWatchState, PriceHint, WatchFile
from haggler.models.research import Claim, SourceCheck, SubjectFacts, claim_hash, claim_id
from haggler.research import io as rio
from haggler.research.review import select_claims

TODAY = date(2026, 10, 9)


def claim(key: str, field: str, value: str) -> Claim:
    url = "https://help.netflix.com/legal/termsofuse"
    quote = f"Literal quote stating {field} {value} on the page."
    return Claim(
        id=claim_id(key, field, value),
        hash=claim_hash(field, value, None, quote, url),
        field=field,
        value=value,
        quote=quote,
        check=SourceCheck(
            cited_url=url,
            final_url=url,
            source_class="official",
            status="verified",
            retrieved_at=datetime(2026, 10, 9, tzinfo=UTC),
        ),
        first_seen=TODAY,
    )


def subjects() -> list[SubjectFacts]:
    return [
        SubjectFacts(
            key="service:netflix",
            kind="service",
            researched_on=TODAY,
            run_id="r1",
            status="researched",
            claims=[claim("service:netflix", "residence_clause", "primary_use_in_account_country")],
            open_questions=["Is the location checked at sign-up?"],
        ),
        SubjectFacts(
            key="pair:netflix:CO",
            kind="pair",
            researched_on=TODAY,
            run_id="r1",
            status="researched",
            claims=[claim("pair:netflix:CO", "payment_card_country", "must_match")],
        ),
        SubjectFacts(
            key="pair:spotify:CO",
            kind="pair",
            researched_on=TODAY,
            run_id="r1",
            status="researched",
            claims=[claim("pair:spotify:CO", "payment_card_country", "any_country")],
        ),
    ]


@pytest.fixture
def dirs(tmp_path: Path) -> tuple[Path, Path]:
    data = tmp_path / "data"
    (data / "research").mkdir(parents=True)
    for name, blob in rio.subject_files(subjects()).items():
        (data / "research" / name).write_bytes(blob)
    curated = tmp_path / "curated"
    shutil.copytree(repo_root() / "data" / "curated", curated)
    # The live file's header without the live decisions: these tests decide synthetic findings.
    review = curated / "research_review.yaml"
    header = review.read_text(encoding="utf-8").split("decisions:")[0]
    review.write_text(header + "decisions: []\n", encoding="utf-8")
    # The frozen bundles.yaml (tests/conftest.py), not the live one: Meli+ Total at 24900, so
    # the agent's 21900 and the watcher's 22900 below stay changes to review.
    shutil.copy(FROZEN_CURATED / "bundles.yaml", curated / "bundles.yaml")
    return data, curated


def run(*args: str, dirs: tuple[Path, Path]) -> int:
    data, curated = dirs
    return cli.main(
        [
            "research",
            *args,
            "--data-dir",
            str(data),
            "--curated-dir",
            str(curated),
            "--date",
            TODAY.isoformat(),
        ]
    )


def test_select_claims():
    subs = subjects()
    assert {c.id.split(":")[0] for _, c in select_claims(subs, "netflix")} == {"service", "pair"}
    assert len(select_claims(subs, "netflix")) == 2
    assert [s.key for s, _ in select_claims(subs, "pair:netflix:CO")] == ["pair:netflix:CO"]
    one = subs[0].claims[0].id
    assert [c.id for _, c in select_claims(subs, one)] == [one]
    assert select_claims(subs, "disney_plus") == []


def test_review_shows_pending_findings_and_their_impact(dirs, capsys):
    assert run("review", dirs=dirs) == 0
    out = capsys.readouterr().out
    assert "[pendiente] service:netflix:residence_clause:primary_use_in_account_country" in out
    assert "Cláusula de residencia en los términos: uso principal en el país de la cuenta" in out
    assert "? Is the location checked at sign-up?" in out
    # The generic profile has no curated Netflix CO record: accepting changes its verdict.
    assert "se_resident_eu_generic · netflix CO: Sin evaluar → No factible" in out
    assert "3 hallazgo(s) pendiente(s)." in out


def test_accept_appends_and_is_idempotent(dirs, capsys):
    _data, curated = dirs
    before = (curated / "research_review.yaml").read_text(encoding="utf-8")
    assert run("accept", "netflix", "--note", "revisado a mano", "--by", "gl", dirs=dirs) == 0
    text = (curated / "research_review.yaml").read_text(encoding="utf-8")
    # Appended, not re-dumped: the header comments are still there.
    assert text.startswith(before.split("decisions:")[0])
    review = load_review(curated)
    assert [d.decision for d in review.decisions] == ["accept", "accept"]
    assert {d.id for d in review.decisions} == {
        c.id for _, c in select_claims(subjects(), "netflix")
    }
    assert all(d.decided_on == TODAY and d.by == "gl" for d in review.decisions)
    capsys.readouterr()

    assert run("accept", "netflix", dirs=dirs) == 0
    assert "ya estaba" in capsys.readouterr().out
    assert len(load_review(curated).decisions) == 2

    assert run("reject", "pair:spotify:CO", dirs=dirs) == 0
    review = load_review(curated)
    assert [d.decision for d in review.decisions] == ["accept", "accept", "reject"]

    capsys.readouterr()
    assert run("review", dirs=dirs) == 0
    assert "0 hallazgo(s) pendiente(s)." in capsys.readouterr().out


def test_unknown_selector_fails(dirs, capsys):
    assert run("accept", "nope", dirs=dirs) == 2
    assert "ningún hallazgo coincide" in capsys.readouterr().err


def test_review_without_research(tmp_path, capsys):
    assert cli.main(["research", "review", "--data-dir", str(tmp_path)]) == 0
    assert "No hay hallazgos" in capsys.readouterr().out


def test_bulk_accept_never_overrides_an_explicit_decision(dirs, capsys):
    _data, curated = dirs
    clause = subjects()[0].claims[0]
    assert run("reject", clause.id, dirs=dirs) == 0
    assert run("accept", "netflix", dirs=dirs) == 0
    assert "ya revisado (reject)" in capsys.readouterr().out
    latest = load_review(curated).latest()
    assert latest[(clause.id, clause.hash)].decision == "reject"
    # The exact claim id may flip it.
    assert run("accept", clause.id, dirs=dirs) == 0
    assert load_review(curated).latest()[(clause.id, clause.hash)].decision == "accept"


@pytest.mark.parametrize(
    "content",
    [
        "decisions: []  # empty for now\n",
        "# header\ndecisions:\n- id: x\n  hash: 0123456789ab\n  decision: accept\n"
        "  decided_on: '2026-10-01'\n",
        "decisions:\n  - id: x\n    hash: 0123456789ab\n    decision: reject\n"
        "    decided_on: '2026-10-01'\n",
    ],
)
def test_append_follows_the_file_layout(dirs, content):
    from haggler.catalog import integrity_errors, load_catalog

    _data, curated = dirs
    (curated / "research_review.yaml").write_text(content, encoding="utf-8")
    assert run("accept", "pair:netflix:CO", dirs=dirs) == 0
    review = load_review(curated)
    assert review.decisions[-1].id == "pair:netflix:CO:payment_card_country:must_match"
    assert integrity_errors(load_catalog(), review=review) == []
    text = (curated / "research_review.yaml").read_text(encoding="utf-8")
    assert text.startswith(content.split("decisions:")[0])


# --------------------------------------------------------------- bundle findings (H8)

BUNDLE = "meli_plus_co_total"
BUNDLE_KEY = f"bundle:{BUNDLE}"
PLANES = "https://www.mercadolibre.com.co/suscripciones/melimas/planes"
PRICE_QUOTE = "Meli+ Total cuesta $ 21.900 por mes."
CLAUSE_QUOTE = "Meli+ está disponible únicamente para residentes en Colombia."
HINT_LINE = "Plan Total: $ 22.900 por mes."


def bundle_claim(field: str, value: str, quote: str) -> Claim:
    return Claim(
        id=claim_id(BUNDLE_KEY, field, value),
        hash=claim_hash(field, value, None, quote, PLANES),
        field=field,
        value=value,
        quote=quote,
        check=SourceCheck(
            cited_url=PLANES,
            final_url=PLANES,
            source_class="official",
            status="verified",
            retrieved_at=datetime(2026, 10, 6, tzinfo=UTC),
        ),
        first_seen=date(2026, 10, 6),
    )


PRICE = bundle_claim("bundle_price", "21900 COP/month", PRICE_QUOTE)
CLAUSE = bundle_claim("bundle_residence_clause", "must_reside", CLAUSE_QUOTE)


def bundle_subjects() -> list[SubjectFacts]:
    return [
        SubjectFacts(
            key=BUNDLE_KEY,
            kind="bundle",
            researched_on=date(2026, 10, 6),
            run_id="r1",
            status="researched",
            claims=[PRICE, CLAUSE],
        )
    ]


@pytest.fixture
def bundle_dirs(dirs: tuple[Path, Path]) -> tuple[Path, Path]:
    """The Phase 2 findings plus a researched bundle (a value and a feasibility finding) and a
    watcher hint for it; the bundle is put in auto watch so the hint counts."""
    data, curated = dirs
    for name, blob in rio.subject_files([*subjects(), *bundle_subjects()]).items():
        (data / "research" / name).write_bytes(blob)
    watch = WatchFile(
        bundles=[
            BundleWatchState(
                bundle_id=BUNDLE,
                outcome="ok",
                checked_at=date(2026, 10, 5),
                hints=[
                    PriceHint(
                        url=PLANES,
                        amount=Decimal("22900"),
                        currency="COP",
                        period="month",
                        role="regular",
                        line=HINT_LINE,
                        first_seen=date(2026, 9, 28),
                    )
                ],
            )
        ]
    )
    (data / "research" / "bundle_watch.json").write_text(watch.model_dump_json(), "utf-8")
    raw = yaml.safe_load((curated / "bundles.yaml").read_text(encoding="utf-8"))
    row = next(b for b in raw["bundles"] if b["bundle_id"] == BUNDLE)
    row["watch"]["mode"] = "auto"
    (curated / "bundles.yaml").write_text(yaml.safe_dump(raw, allow_unicode=True), "utf-8")
    return data, curated


def snippets(out: str) -> list[dict]:
    """The YAML snippets in a CLI output: each starts at its "# data/curated/bundles.yaml"
    comment and runs while the lines keep its indentation."""
    blocks: list[list[str]] = []
    pad: str | None = None
    for line in out.splitlines():
        stripped = line.lstrip()
        if stripped.startswith("# data/curated/bundles.yaml, paquete"):
            pad = line[: len(line) - len(stripped)]
            blocks.append([])
        elif pad is not None and line.strip() and line.startswith(pad):
            if not stripped.startswith("#"):
                blocks[-1].append(line[len(pad) :])
        else:
            pad = None
    return [yaml.safe_load("\n".join(b)) for b in blocks]


def test_review_of_a_bundle_shows_proposals_impact_and_snippets(bundle_dirs, capsys):
    yaml_before = (bundle_dirs[1] / "bundles.yaml").read_text(encoding="utf-8")
    assert run("review", "--subject", BUNDLE, dirs=bundle_dirs) == 0
    out = capsys.readouterr().out
    assert out.startswith(f"Paquete {BUNDLE} — Meli+ Total (CO) · vigilancia auto")
    assert "estado: requiere revisión" in out
    # The watcher's hint first, then the agent's value finding, each before -> after.
    assert f"[vigilante] watch:{BUNDLE}:bundle_price:22900 COP/month" in out
    assert "Precio del paquete: 24900 COP/month -> 22900 COP/month" in out
    assert f"[pendiente] {PRICE.id}  hash={PRICE.hash}" in out
    assert "Precio del paquete: 24900 COP/month -> 21900 COP/month" in out
    # The feasibility finding, accepted by id; the verdict per profile.
    assert f"[pendiente] {CLAUSE.id}" in out
    assert "Impacto por perfil (sin pendientes → aceptando los pendientes):" in out
    assert "  se_resident_co_citizen: " in out and "  se_resident_eu_generic: " in out
    # One snippet per proposal: the value at its path and the literal quote as the anchor.
    hint_snippet, price_snippet = snippets(out)
    assert price_snippet == {
        "price": {"amount": "21900"},
        "watch": {"anchors": [{"fact": "price", "url": PLANES, "text": PRICE_QUOTE}]},
    }
    assert hint_snippet["price"] == {"amount": "22900"}
    assert hint_snippet["watch"]["anchors"][0]["text"] == HINT_LINE
    # Read-only: bundles.yaml is never edited.
    assert (bundle_dirs[1] / "bundles.yaml").read_text(encoding="utf-8") == yaml_before
    # Any selector of the bundle opens its report: its subject key or a proposal id.
    for selector in (BUNDLE_KEY, PRICE.id, f"watch:{BUNDLE}:bundle_price:22900 COP/month"):
        assert run("review", "--subject", selector, dirs=bundle_dirs) == 0
        assert capsys.readouterr().out.startswith(f"Paquete {BUNDLE} —")


def test_accept_of_a_bundle_value_is_refused_with_the_snippet(bundle_dirs, capsys):
    _data, curated = bundle_dirs
    before = (curated / "research_review.yaml").read_text(encoding="utf-8")
    for selector in (PRICE.id, f"watch:{BUNDLE}:bundle_price:22900 COP/month"):
        assert run("accept", selector, dirs=bundle_dirs) == 2
        out = capsys.readouterr().out
        assert f"no se acepta aquí: {selector} es un valor del paquete" in out
        assert "No se añadió ninguna decisión." in out
        (snip,) = snippets(out)
        assert snip["watch"]["anchors"][0]["url"] == PLANES
    # A refused selector refuses the whole command: the Phase 2 id next to it is not decided.
    phase2 = subjects()[0].claims[0].id
    assert run("accept", phase2, PRICE.id, dirs=bundle_dirs) == 2
    assert (curated / "research_review.yaml").read_text(encoding="utf-8") == before


def test_bundle_feasibility_is_accepted_by_explicit_id_only(bundle_dirs, capsys):
    _data, curated = bundle_dirs
    for selector in (BUNDLE, BUNDLE_KEY):
        assert run("accept", selector, dirs=bundle_dirs) == 2
        out = capsys.readouterr().out
        assert f"no se acepta {selector!r} entero" in out
        assert f"hallazgo de factibilidad pendiente: {CLAUSE.id}" in out
    assert load_review(curated).decisions == []
    assert run("accept", CLAUSE.id, dirs=bundle_dirs) == 0
    assert [(d.id, d.hash, d.decision) for d in load_review(curated).decisions] == [
        (CLAUSE.id, CLAUSE.hash, "accept")
    ]


def test_reject_hides_value_proposals_of_both_sources(bundle_dirs, capsys):
    _data, curated = bundle_dirs
    hint_id = f"watch:{BUNDLE}:bundle_price:22900 COP/month"
    assert run("reject", hint_id, PRICE.id, "--note", "no es este nivel", dirs=bundle_dirs) == 0
    decisions = load_review(curated).decisions
    assert [(d.id, d.decision, d.note) for d in decisions] == [
        (hint_id, "reject", "no es este nivel"),
        (PRICE.id, "reject", "no es este nivel"),
    ]
    capsys.readouterr()
    assert run("reject", hint_id, dirs=bundle_dirs) == 0
    assert f"ya estaba: reject {hint_id}" in capsys.readouterr().out
    assert run("review", "--subject", BUNDLE, dirs=bundle_dirs) == 0
    out = capsys.readouterr().out
    assert (
        "Propuestas de valor (antes -> después; se aceptan editando bundles.yaml):\n  ninguna"
        in (out)
    )
    assert snippets(out) == []
    # The feasibility finding is still pending; a whole-bundle reject decides it.
    assert run("reject", BUNDLE, dirs=bundle_dirs) == 0
    assert load_review(curated).decisions[-1].id == CLAUSE.id


def test_bundles_summary_line(bundle_dirs, capsys):
    assert run("review", "--bundles", "--summary", dirs=bundle_dirs) == 0
    assert capsys.readouterr().out == (
        "Paquetes: 2 propuestas de valor pendientes, 1 hallazgo de factibilidad pendiente; "
        f"1 paquete por revisar: {BUNDLE}.\n"
    )
    assert run("reject", BUNDLE, dirs=bundle_dirs) == 0
    capsys.readouterr()
    assert run("review", "--bundles", "--summary", dirs=bundle_dirs) == 0
    assert capsys.readouterr().out == "Paquetes: nada pendiente de revisión.\n"
    assert run("review", "--summary", dirs=bundle_dirs) == 2


def test_general_review_points_to_the_bundles(bundle_dirs, capsys):
    assert run("review", dirs=bundle_dirs) == 0
    out = capsys.readouterr().out
    assert out.rstrip().endswith(
        f"Paquetes por revisar: {BUNDLE} (haggler research review --subject <id>)."
    )
    # The Phase 2 part is unchanged.
    assert "se_resident_eu_generic · netflix CO: Sin evaluar → No factible" in out
    # The bundle's feasibility finding is listed and counted; its value finding is a proposal
    # of the bundle's own report (never accepted here, maybe already in the YAML): neither.
    assert f"[pendiente] {CLAUSE.id}" in out and PRICE.id not in out
    assert f"valores del paquete: propuestas en haggler research review --subject {BUNDLE}" in out
    assert "4 hallazgo(s) pendiente(s)." in out  # the 3 Phase 2 ones and the clause


def test_a_value_finding_the_yaml_holds_is_not_pending_anywhere(dirs, capsys):
    data, _curated = dirs
    # The agent confirmed the YAML's own price: nothing to review in any listing.
    same = bundle_claim("bundle_price", "24900 COP/month", "Meli+ Total cuesta $ 24.900 por mes.")
    facts = bundle_subjects()[0].model_copy(update={"claims": [same]})
    for name, blob in rio.subject_files([*subjects(), facts]).items():
        (data / "research" / name).write_bytes(blob)
    assert run("review", dirs=dirs) == 0
    out = capsys.readouterr().out
    assert same.id not in out and "3 hallazgo(s) pendiente(s)." in out
    assert "Paquetes por revisar" not in out
    assert run("review", "--bundles", "--summary", dirs=dirs) == 0
    assert capsys.readouterr().out == "Paquetes: nada pendiente de revisión.\n"


def test_propose_decides_every_pending_finding_once_and_writes_the_pr_body(dirs, capsys):
    data, curated = dirs
    press = claim("pair:disney_plus:CO", "payment_card_country", "must_match")
    press = press.model_copy(
        update={"check": press.check.model_copy(update={"source_class": "press"})}
    )
    facts = [
        *subjects(),
        SubjectFacts(
            key="pair:disney_plus:CO",
            kind="pair",
            researched_on=TODAY,
            run_id="r1",
            status="researched",
            claims=[press],
        ),
    ]
    for name, blob in rio.subject_files(facts).items():
        (data / "research" / name).write_bytes(blob)
    spotify = subjects()[2].claims[0]
    assert run("reject", spotify.id, dirs=dirs) == 0
    body = data / "body.md"

    assert run("propose", "--body", str(body), dirs=dirs) == 0
    latest = load_review(curated).latest()
    assert latest[(spotify.id, spotify.hash)].decision == "reject"  # decided before: untouched
    assert latest[(press.id, press.hash)].decision == "reject"  # not an official source
    for c in subjects()[0].claims + subjects()[1].claims:
        assert latest[(c.id, c.hash)].decision == "accept"
        assert latest[(c.id, c.hash)].by == "automatic proposal"
    text = body.read_text(encoding="utf-8")
    assert "3 research findings are waiting for review" in text
    assert "accepting 2 (official source) and rejecting 1" in text
    assert f"| `{press.id}` |" in text and "[press](" in text
    assert spotify.id not in text
    capsys.readouterr()

    assert run("propose", "--body", str(body), dirs=dirs) == 0
    assert "Sin hallazgos pendientes." in capsys.readouterr().out

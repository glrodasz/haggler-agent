"""haggler research review | accept | reject."""

from __future__ import annotations

import shutil
from datetime import UTC, date, datetime
from pathlib import Path

import pytest

from haggler import cli
from haggler.catalog import load_review
from haggler.config import repo_root
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

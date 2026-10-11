"""haggler sync: hash-verified copy of the latest run into data/, with an idempotent change log."""

from __future__ import annotations

import hashlib
import json
from datetime import date
from decimal import Decimal
from pathlib import Path

import pytest

from haggler.models.fx import FxRate, FxTable
from haggler.models.observations import PERIOD_MONTHS, PublishedPrice, obs_key
from haggler.models.run import CoverageRow
from haggler.publish import (
    SyncError,
    append_changes,
    diff_prices,
    format_value,
    load_published,
    sync,
)
from haggler.store import LocalStore, dumps, dumps_lines

FIXTURES = Path(__file__).parent / "fixtures"
DAY1 = date(2026, 10, 7)
DAY2 = date(2026, 10, 8)


def price(
    svc: str,
    plan: str,
    cc: str,
    amount: str,
    currency: str,
    *,
    period: str = "month",
    channel: str = "web",
    status: str = "accepted",
    flags: list[str] | None = None,
    url: str | None = None,
    confidence: float = 0.95,
    first_seen: date = DAY1,
    last_changed: date = DAY1,
) -> PublishedPrice:
    value = Decimal(amount)
    return PublishedPrice(
        obs_key=obs_key(svc, plan, cc, channel, period),
        service_id=svc,
        plan_id=plan,
        plan_label_raw=plan,
        country=cc,
        currency=currency,
        amount=value,
        billing_period=period,
        monthly_equiv=(value / PERIOD_MONTHS[period]).quantize(Decimal("0.01")),
        vat_included=True,
        channel=channel,
        source_url=url or f"https://example.com/{svc}/{cc.lower()}",
        source_kind="official",
        adapter_id="test",
        extraction="parser",
        confidence=confidence,
        status=status,
        flags=flags or [],
        first_seen=first_seen,
        last_changed=last_changed,
    )


def fx_table(as_of: date) -> FxTable:
    return FxTable(
        as_of=as_of,
        rates={
            "SEK": FxRate(currency="SEK", per_eur=Decimal("11.2"), source="ecb", as_of=as_of),
            "COP": FxRate(currency="COP", per_eur=Decimal("4500"), source="ecb", as_of=as_of),
        },
    )


def write_source(
    root: Path,
    prices: list[PublishedPrice],
    *,
    run_id: str,
    as_of: date,
    coverage: list[CoverageRow] | None = None,
    review: list[dict] | None = None,
    extra_run: dict | None = None,
) -> LocalStore:
    """A store laid out like the pipeline writes it (run.json last, with sha256 of each file)."""
    store = LocalStore(root)
    files = {
        "prices.json": dumps_lines(sorted(prices, key=lambda p: p.obs_key)),
        # The pipeline's own JSON style (indent=1, unsorted keys): sync must normalise it.
        "fx.json": dumps(fx_table(as_of)),
        "coverage.json": dumps(coverage or []),
        "review_queue.json": dumps(review or []),
        "state.json": dumps({"schema_version": 1, "run_id": run_id}),
    }
    for name, data in files.items():
        store.write_bytes(f"latest/{name}", data)
    run = {
        "schema_version": 1,
        "run_id": run_id,
        "as_of": as_of.isoformat(),
        "finished_at": f"{as_of.isoformat()}T04:30:00Z",
        "files": {name: hashlib.sha256(data).hexdigest() for name, data in files.items()},
        "report": {"tasks_total": 3, "tasks_by_status": {"ok": 3}},
        **(extra_run or {}),
    }
    store.write_bytes("latest/run.json", json.dumps(run).encode())
    return store


def tree(root: Path) -> dict[str, bytes]:
    if not root.exists():
        return {}
    return {p.relative_to(root).as_posix(): p.read_bytes() for p in root.rglob("*") if p.is_file()}


DAY1_PRICES = [
    price("netflix", "standard", "SE", "139", "SEK"),
    price("netflix", "premium", "SE", "199", "SEK"),
    price("netflix", "basic", "CO", "16900", "COP"),
    price("spotify", "individual", "SE", "129.00", "SEK"),
    price("spotify", "family", "SE", "209", "SEK", flags=["x"]),
    price("icloud_plus", "200gb", "LT", "2.99", "EUR"),
]
DAY2_PRICES = [
    # changed amount
    price("netflix", "standard", "SE", "149", "SEK", last_changed=DAY2),
    # unchanged
    price("netflix", "premium", "SE", "199", "SEK"),
    # netflix:basic:CO removed; added:
    price("netflix", "standard", "CO", "26900", "COP", first_seen=DAY2, last_changed=DAY2),
    # same value written differently (129.00 vs 129) -> no change
    price("spotify", "individual", "SE", "129", "SEK"),
    # metadata only: flags, status, source_url, confidence -> no change record
    price(
        "spotify",
        "family",
        "SE",
        "209",
        "SEK",
        flags=["quarantine_hold"],
        status="carried_forward",
        url="https://example.com/moved",
        confidence=0.5,
    ),
    # currency changed -> changed without pct
    price("icloud_plus", "200gb", "LT", "34", "SEK"),
]


@pytest.fixture
def day1(tmp_path: Path) -> LocalStore:
    return write_source(tmp_path / "src1", DAY1_PRICES, run_id="run-day1", as_of=DAY1)


@pytest.fixture
def day2(tmp_path: Path) -> LocalStore:
    return write_source(tmp_path / "src2", DAY2_PRICES, run_id="run-day2", as_of=DAY2)


# --------------------------------------------------------------------------- first sync


def test_first_sync_logs_every_price_as_added(tmp_path: Path, day1: LocalStore) -> None:
    data = tmp_path / "data"
    result = sync(day1, data, today=DAY1)

    assert result.run_id == "run-day1"
    assert result.as_of == DAY1
    assert result.previous_run_id is None
    assert result.prices == len(DAY1_PRICES)
    assert result.counts() == {"added": 6, "changed": 0, "removed": 0}
    assert result.appended == 6
    assert result.changes_file == data / "changes" / "2026-10.jsonl"
    assert result.warnings == []

    lines = (data / "changes" / "2026-10.jsonl").read_text().splitlines()
    records = [json.loads(line) for line in lines]
    assert [r["obs_key"] for r in records] == sorted(p.obs_key for p in DAY1_PRICES)
    first = next(r for r in records if r["obs_key"] == "netflix:standard:SE:web:month")
    assert first == {
        "date": "2026-10-07",
        "run_id": "run-day1",
        "obs_key": "netflix:standard:SE:web:month",
        "service_id": "netflix",
        "plan_id": "standard",
        "country": "SE",
        "channel": "web",
        "kind": "added",
        "old": None,
        "new": "139 SEK/month",
        "pct": None,
        "source_url": "https://example.com/netflix/se",
        "extraction": "parser",
    }
    # Compact, key-sorted JSON lines (stable git diffs).
    assert lines[0] == json.dumps(records[0], sort_keys=True, separators=(",", ":"))


def test_latest_files_are_normalised_and_self_consistent(tmp_path: Path, day1: LocalStore) -> None:
    data = tmp_path / "data"
    sync(day1, data, today=DAY1)
    latest = data / "latest"

    assert sorted(p.name for p in latest.iterdir()) == [
        "coverage.json",
        "fx.json",
        "prices.json",
        "review_queue.json",
        "run.json",
    ]
    prices_bytes = (latest / "prices.json").read_bytes()
    assert prices_bytes == dumps_lines(sorted(DAY1_PRICES, key=lambda p: p.obs_key))
    assert prices_bytes.decode().splitlines()[1].startswith('{"adapter_id":')

    fx_text = (latest / "fx.json").read_text()
    fx = json.loads(fx_text)
    assert fx_text == json.dumps(fx, indent=2, sort_keys=True, ensure_ascii=False) + "\n"
    assert FxTable.model_validate(fx) == fx_table(DAY1)

    run = json.loads((latest / "run.json").read_text())
    assert run["run_id"] == "run-day1"
    assert run["as_of"] == "2026-10-07"
    assert run["report"] == {"tasks_total": 3, "tasks_by_status": {"ok": 3}}
    # Hashes describe the files as written in data/latest, so data/ is itself a valid source.
    assert sorted(run["files"]) == ["coverage.json", "fx.json", "prices.json", "review_queue.json"]
    for name, digest in run["files"].items():
        assert hashlib.sha256((latest / name).read_bytes()).hexdigest() == digest

    mirror = tmp_path / "mirror"
    again = sync(LocalStore(data), mirror, today=DAY1)
    assert again.run_id == "run-day1"
    assert (mirror / "latest" / "prices.json").read_bytes() == prices_bytes


def test_coverage_is_sorted(tmp_path: Path) -> None:
    rows = [
        CoverageRow(service_id="spotify", country="SE", channel="web", status="ok", adapter_id="s"),
        CoverageRow(service_id="netflix", country="SE", channel="web", status="ok", adapter_id="n"),
        CoverageRow(
            service_id="netflix",
            country="CO",
            channel="web",
            status="blocked",
            adapter_id="n",
            failing_since=DAY1,
        ),
    ]
    store = write_source(tmp_path / "src", [], run_id="r", as_of=DAY1, coverage=rows)
    sync(store, tmp_path / "data", today=DAY1)
    out = json.loads((tmp_path / "data" / "latest" / "coverage.json").read_text())
    assert [(r["service_id"], r["country"]) for r in out] == [
        ("netflix", "CO"),
        ("netflix", "SE"),
        ("spotify", "SE"),
    ]
    assert out[0]["failing_since"] == "2026-10-07"


# --------------------------------------------------------------------------- diff


def test_second_sync_logs_only_value_changes(
    tmp_path: Path, day1: LocalStore, day2: LocalStore
) -> None:
    data = tmp_path / "data"
    sync(day1, data, today=DAY1)
    result = sync(day2, data, today=DAY2)

    assert result.previous_run_id == "run-day1"
    by_key = {c["obs_key"]: c for c in result.changes}
    assert sorted(by_key) == [
        "icloud_plus:200gb:LT:web:month",
        "netflix:basic:CO:web:month",
        "netflix:standard:CO:web:month",
        "netflix:standard:SE:web:month",
    ]
    changed = by_key["netflix:standard:SE:web:month"]
    assert (changed["kind"], changed["old"], changed["new"], changed["pct"]) == (
        "changed",
        "139 SEK/month",
        "149 SEK/month",
        7.19,
    )
    assert changed["date"] == "2026-10-08"
    assert changed["run_id"] == "run-day2"
    removed = by_key["netflix:basic:CO:web:month"]
    assert (removed["kind"], removed["old"], removed["new"], removed["pct"]) == (
        "removed",
        "16900 COP/month",
        None,
        None,
    )
    added = by_key["netflix:standard:CO:web:month"]
    assert (added["kind"], added["old"], added["new"]) == ("added", None, "26900 COP/month")
    currency = by_key["icloud_plus:200gb:LT:web:month"]
    assert (currency["kind"], currency["old"], currency["new"], currency["pct"]) == (
        "changed",
        "2.99 EUR/month",
        "34 SEK/month",
        None,
    )
    assert result.counts() == {"added": 1, "changed": 2, "removed": 1}

    # The log holds day 1 (6 added) + day 2 (4 records).
    log = [json.loads(x) for x in (data / "changes" / "2026-10.jsonl").read_text().splitlines()]
    assert len(log) == 10
    # And data/latest now holds day 2 exactly, metadata included.
    assert load_published(data) == sorted(DAY2_PRICES, key=lambda p: p.obs_key)


def test_diff_prices_pct_and_formatting() -> None:
    old = [price("spotify", "individual", "CO", "16900", "COP")]
    new = [price("spotify", "individual", "CO", "18900", "COP")]
    [change] = diff_prices(old, new, run_id="r", as_of=DAY2)
    assert change["pct"] == 11.83
    assert diff_prices(new, new, run_id="r", as_of=DAY2) == []
    down = diff_prices(new, old, run_id="r", as_of=DAY2)[0]
    assert down["pct"] == -10.58
    yearly = price("apple_tv", "standard", "US", "99.90", "USD", period="year")
    assert format_value(yearly) == "99.90 USD/year"
    assert format_value(price("x", "y", "SE", "129.00", "SEK")) == "129 SEK/month"


# --------------------------------------------------------------------------- idempotency


def test_same_run_twice_appends_nothing(tmp_path: Path, day1: LocalStore, day2: LocalStore) -> None:
    data = tmp_path / "data"
    sync(day1, data, today=DAY1)
    sync(day2, data, today=DAY2)
    before = tree(data)

    again = sync(day2, data, today=DAY2)
    assert again.changes == []
    assert again.appended == 0
    assert again.changes_file is None
    assert tree(data) == before


def test_resync_after_crash_does_not_duplicate_records(
    tmp_path: Path, day1: LocalStore, day2: LocalStore
) -> None:
    """A crash after the log append but before data/latest was replaced: the same diff is
    computed again and every record is already present for that run_id + obs_key + kind."""
    data = tmp_path / "data"
    sync(day1, data, today=DAY1)
    day1_latest = {
        name: (data / "latest" / name).read_bytes() for name in ("prices.json", "run.json")
    }
    sync(day2, data, today=DAY2)
    log_before = (data / "changes" / "2026-10.jsonl").read_bytes()
    for name, content in day1_latest.items():  # simulate the crash: latest/ still at day 1
        (data / "latest" / name).write_bytes(content)

    result = sync(day2, data, today=DAY2)
    assert len(result.changes) == 4
    assert result.appended == 0
    assert (data / "changes" / "2026-10.jsonl").read_bytes() == log_before


def test_append_changes_dedupes_and_repairs_missing_newline(tmp_path: Path) -> None:
    path = tmp_path / "2026-10.jsonl"
    path.write_text('{"date":"2026-10-08","kind":"added","obs_key":"a","run_id":"r1"}')
    records = [
        {"date": "2026-10-08", "kind": "added", "obs_key": "a", "run_id": "r1"},
        {"date": "2026-10-08", "kind": "added", "obs_key": "b", "run_id": "r1"},
        {"date": "2026-10-08", "kind": "added", "obs_key": "b", "run_id": "r1"},
        {"date": "2026-11-01", "kind": "removed", "obs_key": "a", "run_id": "r2"},
    ]
    appended, last = append_changes(tmp_path, records)
    assert appended == 2
    assert last == tmp_path / "2026-11.jsonl"
    lines = path.read_text().splitlines()
    assert [json.loads(x)["obs_key"] for x in lines] == ["a", "b"]
    assert len((tmp_path / "2026-11.jsonl").read_text().splitlines()) == 1


# --------------------------------------------------------------------------- refusals


def test_hash_mismatch_aborts_without_writing(tmp_path: Path, day1: LocalStore) -> None:
    data = tmp_path / "data"
    sync(day1, data, today=DAY1)
    before = tree(data)

    store = write_source(tmp_path / "src2", DAY2_PRICES, run_id="run-day2", as_of=DAY2)
    # The job is writing the next run: prices.json no longer matches run.json.
    store.write_bytes("latest/prices.json", dumps_lines(DAY1_PRICES[:1]))

    with pytest.raises(SyncError) as err:
        sync(store, data, today=DAY2)
    message = str(err.value)
    assert "prices.json" in message
    assert "no coincide" in message
    assert "run-day2" in message
    assert tree(data) == before


def test_hash_mismatch_on_first_sync_creates_nothing(tmp_path: Path) -> None:
    store = write_source(tmp_path / "src", DAY1_PRICES, run_id="r", as_of=DAY1)
    store.write_bytes("latest/fx.json", b"{}")
    with pytest.raises(SyncError, match=r"fx\.json"):
        sync(store, tmp_path / "data", today=DAY1)
    assert not (tmp_path / "data").exists()


def test_missing_run_json(tmp_path: Path) -> None:
    with pytest.raises(SyncError, match=r"latest/run\.json no existe"):
        sync(LocalStore(tmp_path / "empty"), tmp_path / "data", today=DAY1)


@pytest.mark.parametrize(
    ("patch", "message"),
    [
        ({"schema_version": 2}, "schema_version"),
        ({"run_id": ""}, "run_id"),
        ({"as_of": "yesterday"}, "as_of"),
        ({"files": {}}, "no lista archivos"),
    ],
)
def test_malformed_run_json(tmp_path: Path, patch: dict, message: str) -> None:
    store = write_source(tmp_path / "src", DAY1_PRICES, run_id="r", as_of=DAY1, extra_run=patch)
    with pytest.raises(SyncError, match=message):
        sync(store, tmp_path / "data", today=DAY1)
    assert not (tmp_path / "data").exists()


def test_listed_file_missing_or_required_file_unlisted(tmp_path: Path) -> None:
    store = write_source(tmp_path / "src", DAY1_PRICES, run_id="r", as_of=DAY1)
    (tmp_path / "src" / "latest" / "review_queue.json").unlink()
    with pytest.raises(SyncError, match=r"review_queue\.json: listado en run\.json pero no existe"):
        sync(store, tmp_path / "data", today=DAY1)

    run = json.loads((tmp_path / "src" / "latest" / "run.json").read_text())
    run["files"] = {k: v for k, v in run["files"].items() if k == "prices.json"}
    store.write_bytes("latest/run.json", json.dumps(run).encode())
    with pytest.raises(SyncError) as err:
        sync(store, tmp_path / "data", today=DAY1)
    assert "fx.json: no aparece en run.json" in str(err.value)
    assert "coverage.json: no aparece en run.json" in str(err.value)


def test_invalid_prices_content_aborts(tmp_path: Path) -> None:
    store = LocalStore(tmp_path / "src")
    files = {
        "prices.json": b'[{"obs_key": "x"}]\n',
        "fx.json": dumps(fx_table(DAY1)),
        "coverage.json": b"[]",
    }
    for name, data in files.items():
        store.write_bytes(f"latest/{name}", data)
    run = {
        "schema_version": 1,
        "run_id": "r",
        "as_of": "2026-10-07",
        "files": {n: hashlib.sha256(d).hexdigest() for n, d in files.items()},
    }
    store.write_bytes("latest/run.json", json.dumps(run).encode())
    with pytest.raises(SyncError, match="formato inesperado"):
        sync(store, tmp_path / "data", today=DAY1)
    assert not (tmp_path / "data").exists()


def test_older_run_is_refused_unless_allowed(
    tmp_path: Path, day1: LocalStore, day2: LocalStore
) -> None:
    data = tmp_path / "data"
    sync(day2, data, today=DAY2)
    before = tree(data)
    with pytest.raises(SyncError, match="más antigua"):
        sync(day1, data, today=DAY2)
    assert tree(data) == before

    result = sync(day1, data, today=DAY2, allow_older=True)
    assert result.run_id == "run-day1"
    assert load_published(data) == sorted(DAY1_PRICES, key=lambda p: p.obs_key)


def test_stale_run_warns(tmp_path: Path, day1: LocalStore) -> None:
    result = sync(day1, tmp_path / "data", today=date(2026, 10, 12))
    assert len(result.warnings) == 1
    assert "hace 5 días" in result.warnings[0]


def test_invalid_local_prices_aborts(tmp_path: Path, day1: LocalStore) -> None:
    data = tmp_path / "data"
    (data / "latest").mkdir(parents=True)
    (data / "latest" / "prices.json").write_text('[{"broken": true}]')
    with pytest.raises(SyncError, match="no es válido"):
        sync(day1, data, today=DAY1)
    assert not (data / "changes").exists()


# --------------------------------------------------------------------------- committed fixture


def test_committed_fixture_store_round_trips_byte_for_byte(tmp_path: Path) -> None:
    """tests/publish/fixtures/data is a data/ dir written by sync: syncing from it reproduces it."""
    source = FIXTURES / "data"
    result = sync(LocalStore(source), tmp_path / "data", today=DAY2)
    assert result.run_id == "20261008T041700Z-ab12cd"
    for name in ("prices.json", "fx.json", "coverage.json", "review_queue.json", "run.json"):
        assert (tmp_path / "data" / "latest" / name).read_bytes() == (
            source / "latest" / name
        ).read_bytes(), name
    assert result.counts()["added"] == result.prices == 23


def test_corrupt_change_log_aborts_before_writing_latest(tmp_path: Path, day1: LocalStore) -> None:
    data = tmp_path / "data"
    (data / "changes").mkdir(parents=True)
    (data / "changes" / "2026-10.jsonl").write_text("{not json\n")
    with pytest.raises(SyncError, match="línea 1 no es JSON válido"):
        sync(day1, data, today=DAY1)
    assert not (data / "latest").exists()


# ------------------------------------------------------------------------- research


def _facts(key: str, on: date):
    from haggler.models.research import SubjectFacts

    return SubjectFacts(
        key=key,
        kind=key.split(":")[0],
        researched_on=on,
        run_id=f"r-{on.isoformat()}",
        status="nothing_found",
    )


def write_research(store: LocalStore, *, run_id: str, as_of: date, keys: list[str]) -> None:
    from haggler.models.research import ResearchState
    from haggler.research import io as rio

    rio.write_latest(
        store,
        subjects=[_facts(k, as_of) for k in keys],
        state=ResearchState(run_id=run_id),
        run_id=run_id,
        as_of=as_of.isoformat(),
        finished_at=None,
        report_summary={"planned": len(keys)},
    )


def test_sync_copies_research_with_its_own_manifest(tmp_path: Path, day1: LocalStore) -> None:
    write_research(day1, run_id="r1", as_of=DAY1, keys=["service:netflix", "pair:netflix:CO"])
    data = tmp_path / "data"
    result = sync(day1, data, today=DAY1)
    assert result.research_run_id == "r1" and result.research_subjects == 2
    files = tree(data / "research")
    assert set(files) == {"services.json", "pairs.json", "instruments.json", "run.json"}
    run = json.loads(files["run.json"])
    assert run["run_id"] == "r1" and run["report"] == {"planned": 2}
    for name in ("services.json", "pairs.json", "instruments.json"):
        assert run["files"][name] == hashlib.sha256(files[name]).hexdigest()
    # state.json (backoff counters) stays in the store.
    assert "state.json" not in files
    # data/research is itself a valid source for site-data.
    from haggler.research.io import load_data_dir

    subjects, _manifest, errors = load_data_dir(data)
    assert {s.key for s in subjects} == {"service:netflix", "pair:netflix:CO"} and not errors


def test_sync_without_research_is_unchanged(tmp_path: Path, day1: LocalStore) -> None:
    result = sync(day1, tmp_path / "data", today=DAY1)
    assert result.research_run_id is None and not (tmp_path / "data" / "research").exists()
    assert not any("investigación" in w for w in result.warnings)


def test_broken_research_warns_but_prices_sync(tmp_path: Path, day1: LocalStore) -> None:
    write_research(day1, run_id="r1", as_of=DAY1, keys=["service:netflix"])
    day1.write_bytes("research/latest/services.json", b"[]\n")  # hash no longer matches
    result = sync(day1, tmp_path / "data", today=DAY1)
    assert result.prices == len(DAY1_PRICES) and result.research_run_id is None
    assert any("sha256 no coincide" in w for w in result.warnings)
    assert not (tmp_path / "data" / "research").exists()


def test_older_research_is_not_copied(tmp_path: Path, day1: LocalStore, day2: LocalStore) -> None:
    write_research(day2, run_id="r2", as_of=DAY2, keys=["service:netflix"])
    write_research(day1, run_id="r1", as_of=DAY1, keys=["service:spotify"])
    data = tmp_path / "data"
    sync(day2, data, today=DAY2)
    result = sync(day1, data, today=DAY2, allow_older=True)
    assert any("más antigua" in w for w in result.warnings)
    assert json.loads((data / "research" / "run.json").read_text())["run_id"] == "r2"


def _watch_file(run_id: str):
    from haggler.models.bundles import BundleWatchState, WatchFile

    return WatchFile(
        run_id=run_id,
        bundles=[BundleWatchState(bundle_id="meli_plus_co_total", outcome="manual")],
    )


def test_an_old_research_run_without_the_optional_files_still_syncs(
    tmp_path: Path, day1: LocalStore
) -> None:
    # A run.json written before phase 3 lists neither bundle_watch.json nor bundles.json.
    write_research(day1, run_id="r1", as_of=DAY1, keys=["service:netflix"])
    run = json.loads(day1.read_bytes("research/latest/run.json"))
    assert set(run["files"]) == {"services.json", "pairs.json", "instruments.json", "state.json"}
    data = tmp_path / "data"
    result = sync(day1, data, today=DAY1)
    assert result.research_run_id == "r1"
    assert not any("investigación" in w for w in result.warnings)
    assert not (data / "research" / "bundle_watch.json").exists()


def test_bundle_watch_is_copied_when_the_manifest_lists_it(
    tmp_path: Path, day1: LocalStore, day2: LocalStore
) -> None:
    from haggler.models.research import ResearchState
    from haggler.research import io as rio

    rio.write_latest(
        day1,
        subjects=[_facts("service:netflix", DAY1)],
        state=ResearchState(run_id="r1"),
        run_id="r1",
        as_of=DAY1.isoformat(),
        finished_at=None,
        report_summary={},
        watch=_watch_file("r1"),
    )
    data = tmp_path / "data"
    result = sync(day1, data, today=DAY1)
    copied = data / "research" / "bundle_watch.json"
    assert copied in result.written
    assert copied.read_bytes() == day1.read_bytes("research/latest/bundle_watch.json")
    manifest = json.loads((data / "research" / "run.json").read_text())
    assert manifest["files"]["bundle_watch.json"] == hashlib.sha256(copied.read_bytes()).hexdigest()
    watch, errors = rio.load_data_watch(data)
    assert not errors and watch is not None and watch.bundles[0].outcome == "manual"

    # A later run that does not list it: the stale copy goes, data/research matches its manifest.
    write_research(day2, run_id="r2", as_of=DAY2, keys=["service:netflix"])
    sync(day2, data, today=DAY2)
    assert not copied.exists()
    assert (
        "bundle_watch.json" not in json.loads((data / "research" / "run.json").read_text())["files"]
    )


@pytest.mark.parametrize("problem", ["hash", "missing", "invalid"])
def test_a_broken_listed_watch_file_is_a_research_sync_error(
    tmp_path: Path, day1: LocalStore, problem: str
) -> None:
    from haggler.models.research import ResearchState
    from haggler.research import io as rio

    rio.write_latest(
        day1,
        subjects=[],
        state=ResearchState(run_id="r1"),
        run_id="r1",
        as_of=DAY1.isoformat(),
        finished_at=None,
        report_summary={},
        watch=_watch_file("r1"),
    )
    key = "research/latest/bundle_watch.json"
    if problem == "hash":
        day1.write_bytes(key, b'{"schema_version": 1, "bundles": []}')
    elif problem == "missing":
        (Path(day1.root) / key).unlink()
    else:
        bad = b'{"schema_version": 1, "bundles": [{"bundle_id": "x"}]}'
        day1.write_bytes(key, bad)
        run = json.loads(day1.read_bytes("research/latest/run.json"))
        run["files"]["bundle_watch.json"] = hashlib.sha256(bad).hexdigest()
        day1.write_bytes("research/latest/run.json", json.dumps(run).encode())
    result = sync(day1, tmp_path / "data", today=DAY1)
    # Research is optional for the price sync: a warning, nothing of research written.
    assert result.prices == len(DAY1_PRICES) and result.research_run_id is None
    assert any("bundle_watch.json" in w for w in result.warnings)
    assert not (tmp_path / "data" / "research").exists()


def test_bundle_facts_are_copied_when_the_manifest_lists_them(
    tmp_path: Path, day1: LocalStore, day2: LocalStore
) -> None:
    # H8: the bundle kind's facts live in the optional bundles.json, written only when a run
    # has bundle subjects.
    from haggler.research import io as rio

    write_research(
        day1, run_id="r1", as_of=DAY1, keys=["service:netflix", "bundle:meli_plus_co_total"]
    )
    run = json.loads(day1.read_bytes("research/latest/run.json"))
    assert set(run["files"]) == {
        "services.json",
        "pairs.json",
        "instruments.json",
        "state.json",
        "bundles.json",
    }
    data = tmp_path / "data"
    result = sync(day1, data, today=DAY1)
    assert result.research_subjects == 2
    copied = data / "research" / "bundles.json"
    assert copied.read_bytes() == day1.read_bytes("research/latest/bundles.json")
    manifest = json.loads((data / "research" / "run.json").read_text())
    assert manifest["files"]["bundles.json"] == hashlib.sha256(copied.read_bytes()).hexdigest()
    subjects, _manifest, errors = rio.load_data_dir(data)
    assert {s.key for s in subjects} == {"service:netflix", "bundle:meli_plus_co_total"}
    assert not errors

    # A later run without bundle subjects: the stale copy goes.
    write_research(day2, run_id="r2", as_of=DAY2, keys=["service:netflix"])
    sync(day2, data, today=DAY2)
    assert not copied.exists()


def test_a_broken_listed_bundles_file_is_a_research_sync_error(
    tmp_path: Path, day1: LocalStore
) -> None:
    write_research(day1, run_id="r1", as_of=DAY1, keys=["bundle:meli_plus_co_total"])
    bad = b'[{"key": "bundle:x"}]\n'
    day1.write_bytes("research/latest/bundles.json", bad)
    run = json.loads(day1.read_bytes("research/latest/run.json"))
    run["files"]["bundles.json"] = hashlib.sha256(bad).hexdigest()
    day1.write_bytes("research/latest/run.json", json.dumps(run).encode())
    result = sync(day1, tmp_path / "data", today=DAY1)
    assert result.research_run_id is None
    assert any("bundles.json: formato inesperado" in w for w in result.warnings)
    assert not (tmp_path / "data" / "research").exists()

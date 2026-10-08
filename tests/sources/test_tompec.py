import json
from decimal import Decimal
from pathlib import Path

from haggler.fetch import RawResponse
from haggler.sources.tompec import TompecNetflixAdapter

FIX = Path(__file__).parent / "fixtures" / "tompec_netflix"
ADAPTER = TompecNetflixAdapter()


def _task(catalog):
    svc = catalog.service("netflix")
    src = next(s for s in svc.sources if s.adapter == ADAPTER.id)
    (task,) = ADAPTER.plan(svc, src, catalog)
    return task


def _parse(catalog, text, task=None):
    task = task or _task(catalog)
    resp = RawResponse(
        url=task.url,
        final_url=task.url,
        status_code=200,
        text=text,
        content_type="application/json",
    )
    return ADAPTER.parse(task, resp, catalog)


def _fixture_text():
    return (FIX / "latest.json").read_text(encoding="utf-8")


def _by_key(result):
    return {(o.country, o.plan_id): o for o in result.observations}


def test_plan_is_one_json_task_for_all_countries(catalog):
    task = _task(catalog)
    assert task.task_id == "tompec_netflix:netflix:all"
    assert (
        task.url == "https://raw.githubusercontent.com/tompec/netflix-prices/main/data/latest.json"
    )
    assert task.host == "raw.githubusercontent.com"
    assert task.params["accept"] == "application/json"
    assert task.kind == "secondary"
    assert task.countries == catalog.country_codes()
    assert task.service_ids == ["netflix"]


def test_fixture_is_trimmed_to_catalog_countries(catalog):
    rows = json.loads(_fixture_text())
    assert sorted(r["country_code"] for r in rows) == sorted(catalog.country_codes())


def test_parse_dataset(catalog):
    result = _parse(catalog, _fixture_text())
    assert result.status == "ok"
    obs = _by_key(result)
    expected = sum(
        1 for row in json.loads(_fixture_text()) for p in row["plans"] if p["price"] is not None
    )
    assert len(result.observations) == expected

    se = obs[("SE", "standard")]
    assert (se.amount, se.currency, se.billing_period) == (Decimal("169"), "SEK", "month")
    assert str(se.amount) == "169"  # not "169.0": same text as the help-center parse
    assert se.evidence_snippet == "tompec latest.json: SE standard 169 SEK"
    assert se.service_id == "netflix"
    assert obs[("CO", "premium")].amount == Decimal("44900")
    assert str(obs[("CH", "basic")].amount) == "14.90"
    assert obs[("US", "standard_with_ads")].amount == Decimal("8.99")
    # price: null means "not offered there" -> no draft.
    assert ("SE", "standard_with_ads") not in obs
    assert ("SE", "mobile") not in obs
    # Every catalog country has the default plan in this snapshot.
    assert {cc for cc, plan in obs if plan == "standard"} == set(catalog.country_codes())


def test_currency_that_differs_from_catalog_is_noted(catalog):
    unpinned = _task(catalog)
    unpinned = unpinned.model_copy(update={"params": {**unpinned.params, "currency_overrides": {}}})
    result = _parse(catalog, _fixture_text(), unpinned)
    # Netflix bills Romania in EUR; the validator decides, the adapter reports what it saw.
    assert _by_key(result)[("RO", "standard")].currency == "EUR"
    assert any(n.startswith("RO: dataset currency EUR") for n in result.notes)
    # The catalog pins RO to EUR, so the default task has nothing to report.
    assert _task(catalog).params["currency_overrides"] == {"RO": "EUR"}
    assert not any(n.startswith("RO:") for n in _parse(catalog, _fixture_text()).notes)


def test_missing_country_unknown_plan_and_wrapper(catalog):
    rows = json.loads(_fixture_text())
    rows = [r for r in rows if r["country_code"] != "SE"]
    rows[0]["plans"].append({"name": "ultra", "price": 99.0, "price_usd": 9.9})
    result = _parse(catalog, json.dumps({"updated": "2026-09-28", "data": rows}))
    assert result.status == "ok"
    assert "SE: not in dataset" in result.notes
    assert any("unknown plan 'ultra'" in n for n in result.notes)
    assert all(o.country != "SE" for o in result.observations)


def test_not_json(catalog):
    result = _parse(catalog, "<html><body>404: Not Found</body></html>")
    assert result.status == "no_prices"
    assert result.observations == []
    assert result.notes


def test_empty_list(catalog):
    assert _parse(catalog, "[]").status == "no_prices"

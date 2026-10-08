"""Unit tests for the pure helpers of haggler.pipeline.nodes and the Deps wiring."""

from __future__ import annotations

import base64
import json
from datetime import UTC, date, datetime
from decimal import Decimal

import pytest

from haggler.config import Settings
from haggler.models.catalog import Catalog
from haggler.models.run import LlmExtraction, LlmJob, LlmPlanHint, Task, TaskResult
from haggler.pipeline import deps as deps_mod
from haggler.pipeline import nodes
from haggler.pipeline.agents import INSTRUCTION, build_price_extractor, instruction

# ----------------------------------------------------------------------------- filters


@pytest.mark.parametrize("text", ["", "go", "  run now ", "daily"])
def test_free_text_means_defaults(text: str) -> None:
    f = nodes.parse_filters(text)
    assert f == nodes.Filters() and not f.filtered


def test_json_filters() -> None:
    f = nodes.parse_filters(
        json.dumps(
            {
                "date": "2026-10-08",
                "services": "netflix, spotify",
                "countries": ["se", "CO", "*"],
                "adapters": ["netflix_help"],
                "dry_run": "true",
            }
        )
    )
    assert f.date == date(2026, 10, 8)
    assert f.services == ["netflix", "spotify"] and f.countries == ["SE", "CO"]
    assert f.adapters == ["netflix_help"] and f.dry_run and f.filtered


def test_pubsub_envelopes() -> None:
    inner = {"services": ["netflix"], "dry_run": True}
    plain = nodes.parse_filters(json.dumps({"data": inner, "attributes": {}}))
    as_str = nodes.parse_filters(json.dumps({"data": json.dumps(inner)}))
    b64 = base64.b64encode(json.dumps(inner).encode()).decode()
    as_b64 = nodes.parse_filters(json.dumps({"data": b64, "attributes": {"k": "v"}}))
    assert plain == as_str == as_b64
    assert plain.services == ["netflix"] and plain.dry_run
    assert nodes.parse_filters(json.dumps({"data": ""})) == nodes.Filters()


def test_invalid_json_request_is_an_error() -> None:
    with pytest.raises(ValueError, match="not valid JSON"):
        nodes.parse_filters('{"services": [')


def test_run_id_is_date_plus_short_hash() -> None:
    started = datetime(2026, 10, 8, 4, 17, tzinfo=UTC)
    a = nodes.make_run_id(date(2026, 10, 8), started, nodes.Filters())
    b = nodes.make_run_id(date(2026, 10, 8), started, nodes.Filters(services=["netflix"]))
    assert a.startswith("2026-10-08-") and len(a) == len("2026-10-08-") + 6 and a != b


# ----------------------------------------------------------------------------- planning


def _task(task_id: str, host: str) -> Task:
    return Task(
        task_id=task_id,
        adapter_id="x",
        service_ids=["s"],
        countries=["SE"],
        channel="web",
        kind="official",
        url=f"https://{host}/{task_id}",
        host=host,
    )


def test_interleave_by_host_round_robins() -> None:
    tasks = [_task(f"a{i}", "a.com") for i in range(3)] + [_task("b0", "b.com"), _task("c0", "")]
    order = [t.task_id for t in nodes.interleave_by_host(tasks)]
    assert order == ["a0", "b0", "c0", "a1", "a2"]


def test_plan_all_respects_filters(catalog: Catalog) -> None:
    d = deps_mod.Deps(
        settings=Settings(),
        catalog=catalog,
        fetcher=None,  # type: ignore[arg-type]
        store=None,  # type: ignore[arg-type]
        fx=None,  # type: ignore[arg-type]
        model=None,
    )
    errors: list[str] = []
    tasks = nodes.plan_all(
        d,
        nodes.Filters(services=["icloud_plus", "netflix", "nope"], countries=["SE", "CO"]),
        errors,
    )
    ids = {t.task_id: t for t in tasks}
    assert set(ids) == {
        "icloud_support:icloud_plus:ALL",
        "netflix_help:netflix:SE",
        "netflix_help:netflix:CO",
        "tompec_netflix:netflix:all",
    }
    # Multi-country tasks are narrowed to the requested countries (task id unchanged).
    assert ids["icloud_support:icloud_plus:ALL"].countries == ["SE", "CO"]
    assert ids["tompec_netflix:netflix:all"].params["accept"] == "application/json"
    assert errors == ["filter: unknown service nope"]
    only = nodes.plan_all(d, nodes.Filters(adapters=["tompec_netflix"]), [])
    assert [t.task_id for t in only] == ["tompec_netflix:netflix:all"]


def test_full_catalog_plans_unique_tasks(catalog: Catalog) -> None:
    d = deps_mod.Deps(
        settings=Settings(),
        catalog=catalog,
        fetcher=None,  # type: ignore[arg-type]
        store=None,  # type: ignore[arg-type]
        fx=None,  # type: ignore[arg-type]
        model=None,
    )
    errors: list[str] = []
    tasks = nodes.plan_all(d, nodes.Filters(), errors)
    assert errors == []
    assert len({t.task_id for t in tasks}) == len(tasks) > 300
    assert any(t.params.get("not_configured") for t in tasks)


def test_safe_names() -> None:
    assert nodes.raw_key("2026-10-08", "netflix_help", "netflix_help:netflix:SE", "html") == (
        "raw/2026-10-08/netflix_help/netflix_help__netflix__SE.html.gz"
    )
    assert nodes.safe_id("netflix_help:netflix:SE.parser_failed") == (
        "netflix_help_netflix_SE_parser_failed"
    )
    assert nodes.job_id_for("t:1", "parser_failed", "svc", "SE", scoped=True).endswith("svc_SE")


# ----------------------------------------------------------------------------- LLM post-checks


def _job(page_text: str, expected: list[str] | None = None) -> LlmJob:
    return LlmJob(
        job_id="j",
        task_id="t",
        reason="parser_failed",
        service_id="netflix",
        country="SE",
        expected_currencies=expected or ["SEK"],
        plans=[
            LlmPlanHint(id="standard", label="Standard"),
            LlmPlanHint(id="basic", label="Basic"),
        ],
        channel="web",
        source_url="https://x",
        page_text=page_text,
    )


def _extraction(*items: dict) -> LlmExtraction:
    return LlmExtraction.model_validate({"page_status": "ok", "items": list(items), "notes": ""})


def _item(plan: str, price: str, evidence: str, currency: str = "SEK", period: str = "month"):
    return {
        "plan_id": plan,
        "plan_label_raw": plan,
        "price_text": price,
        "currency": currency,
        "billing_period": period,
        "evidence_snippet": evidence,
    }


def test_post_check_accepts_verbatim_quotes_modulo_whitespace() -> None:
    job = _job("Standard\n169 kr / month\nBasic: 129 kr/month")
    accepted, rejected = nodes.post_check(
        job,
        _extraction(
            _item("standard", "169 kr", "Standard 169 kr / month"),
            _item("basic", "129 kr", "Basic: 129 kr/month", currency="sek"),
        ),
    )
    assert rejected == []
    assert [(i.plan_id, m.amount, i.currency) for i, m in accepted] == [
        ("standard", Decimal("169"), "SEK"),
        ("basic", Decimal("129"), "SEK"),
    ]


@pytest.mark.parametrize(
    ("item", "reason"),
    [
        (_item("premium", "169 kr", "Standard 169 kr"), "plan_not_allowed"),
        (_item("other", "169 kr", "Standard 169 kr"), "plan_not_allowed"),
        (_item("standard", "169 kr", "Standard 169 kr", currency="NOK"), "unexpected_currency"),
        (_item("standard", "about one hundred", "Standard 169 kr"), "price_unparseable"),
        (_item("standard", "0 kr", "Standard 0 kr"), "non_positive"),
        (_item("standard", "179 kr", "Standard 179 kr"), "price_not_on_page"),
        (_item("standard", "169 kr", "The Standard plan is 169 kr"), "evidence_not_on_page"),
        (_item("standard", "169 kr", ""), "evidence_not_on_page"),
        (_item("standard", "169 kr", "Basic: 129 kr/month"), "price_not_in_evidence"),
    ],
)
def test_post_check_rejections(item: dict, reason: str) -> None:
    job = _job("Standard 169 kr\nBasic: 129 kr/month\nStandard 0 kr")
    accepted, rejected = nodes.post_check(job, _extraction(item))
    assert accepted == [] and [r["reason"] for r in rejected] == [reason]


def test_post_check_currency_must_match_the_marker() -> None:
    job = _job("Standard US$ 16.99", expected=["SEK", "USD"])
    _, rejected = nodes.post_check(
        job, _extraction(_item("standard", "US$ 16.99", "Standard US$ 16.99"))
    )
    assert [r["reason"] for r in rejected] == ["currency_mismatch"]


def test_post_check_keeps_one_price_per_plan_and_period() -> None:
    job = _job("Standard 169 kr\nStandard 179 kr\nStandard 1690 kr per year")
    accepted, rejected = nodes.post_check(
        job,
        _extraction(
            _item("standard", "169 kr", "Standard 169 kr"),
            _item("standard", "179 kr", "Standard 179 kr"),
            _item("standard", "1690 kr", "Standard 1690 kr per year", period="year"),
        ),
    )
    assert [(i.billing_period, m.amount) for i, m in accepted] == [
        ("month", Decimal("169")),
        ("year", Decimal("1690")),
    ]
    assert [r["reason"] for r in rejected] == ["duplicate_plan"]


def test_focus_text_for_multi_country_pages(catalog: Catalog) -> None:
    lines = [
        "Intro",
        "Sweden (SEK)",
        "50 GB: 12 kr",
        "200 GB: 39 kr",
        "Norway (NOK)",
        "50 GB: 15 kr",
    ]
    lines += [f"filler {i}" for i in range(30)] + ["Colombia (COP)", "50 GB: COP 3.900"]
    se = nodes.focus_text(lines, catalog.country("SE"), ["SEK"], multi=True)
    assert "Sweden (SEK)" in se and "39 kr" in se and "Colombia" not in se
    assert nodes.focus_text(lines, catalog.country("PK"), ["PKR"], multi=True) == ""
    single = nodes.focus_text(lines, catalog.country("SE"), ["SEK"], multi=False)
    assert "Colombia (COP)" in single and "39 kr" in single  # single-country: whole page


def test_coverage_status_upgrades_only_parser_failures(catalog: Catalog) -> None:
    base = {"task_id": "t", "adapter_id": "a", "service_ids": ["netflix"], "countries": ["SE"]}
    assert nodes.coverage_status(TaskResult(**base, status="blocked"), catalog) == "blocked"
    failed = TaskResult(**base, status="failed", error="transient: 503")
    assert nodes.coverage_status(failed, catalog) == "failed"


def test_expected_currencies_for_apple_billing(catalog: Catalog) -> None:
    assert nodes.expected_currencies(catalog, "apple_one", "AR", "web") == ["ARS", "USD"]
    assert nodes.expected_currencies(catalog, "netflix", "AR", "web") == ["ARS"]
    assert nodes.expected_currencies(catalog, "youtube_premium", "AR", "ios_iap") == ["USD"]
    assert nodes.llm_flags("icloud_plus", "web") == ["apple_billing"]
    assert nodes.llm_flags("hbo_max", "ios_iap") == ["iap_maybe_marked_up"]


# ----------------------------------------------------------------------------- agent + deps


def test_price_extractor_definition() -> None:
    from conftest import ScriptedLlm

    agent = build_price_extractor(ScriptedLlm())
    assert agent.name == "price_extractor" and agent.output_schema is LlmExtraction
    assert agent.tools == [] and callable(agent.instruction)
    assert agent.generate_content_config.temperature == 0
    assert agent.retry_config.max_attempts == 2
    assert instruction(None) == INSTRUCTION  # type: ignore[arg-type]
    assert "{" not in INSTRUCTION and "}" not in INSTRUCTION


def test_build_model_needs_credentials(monkeypatch: pytest.MonkeyPatch) -> None:
    for var in ("GOOGLE_API_KEY", "GEMINI_API_KEY", "GOOGLE_GENAI_USE_ENTERPRISE"):
        monkeypatch.delenv(var, raising=False)
    monkeypatch.delenv("GOOGLE_GENAI_USE_VERTEXAI", raising=False)
    assert deps_mod.build_model(Settings()) is None
    monkeypatch.setenv("GOOGLE_API_KEY", "test-key")
    model = deps_mod.build_model(Settings(extract_model="gemini-test"))
    assert model is not None and model.model == "gemini-test"
    assert model.retry_options.attempts == 4
    assert model.retry_options.http_status_codes == [429, 500, 503]
    monkeypatch.setenv("HAGGLER_LLM", "off")
    assert deps_mod.build_model(Settings()) is None


async def test_default_deps_are_lazy(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("HAGGLER_STORE", "gcs")
    monkeypatch.delenv("HAGGLER_GCS_BUCKET", raising=False)
    d = deps_mod.build_default_deps()
    assert isinstance(d.fetcher, deps_mod.LazyFetcher) and d.fetcher._inner is None
    assert isinstance(d.store, deps_mod.LazyStore) and d.store._inner is None
    assert isinstance(d.fx, deps_mod.LazyFxService) and d.fx._client is None
    with pytest.raises(ValueError, match="HAGGLER_GCS_BUCKET"):
        d.store.read_bytes("latest/run.json")  # created on first use only
    await d.aclose()

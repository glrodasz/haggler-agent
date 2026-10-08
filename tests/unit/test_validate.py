"""Validation rules: reject, quarantine/release, secondary rows and carry-forward across runs."""

from __future__ import annotations

import json
import random
from datetime import UTC, date, datetime
from decimal import Decimal

import pytest

from haggler.config import Settings
from haggler.models.catalog import Catalog
from haggler.models.fx import FxRate, FxTable
from haggler.models.observations import ObservationDraft, PriceObservation, PublishedPrice
from haggler.models.run import KeyState, PipelineState, TaskResult
from haggler.validate import ValidationOutcome, publishable, validate

D0 = date(2026, 10, 7)
D1 = date(2026, 10, 8)
D2 = date(2026, 10, 9)
D3 = date(2026, 10, 10)
NOW = datetime(2026, 10, 8, 4, 17, tzinfo=UTC)
SETTINGS = Settings(quarantine_change_pct=30.0)
RATES = {"SEK": "11", "COP": "4500", "USD": "1.10", "ARS": "1400"}


def fx_table(rates: dict[str, str] = RATES) -> FxTable:
    return FxTable(
        as_of=D1,
        rates={
            cur: FxRate(currency=cur, per_eur=Decimal(v), source="ecb", as_of=D1)
            for cur, v in rates.items()
        },
    )


FX = fx_table()
ADAPTERS = {
    ("netflix", "official"): "netflix_help",
    ("netflix", "secondary"): "tompec_netflix",
    ("spotify", "official"): "spotify_premium",
    ("icloud_plus", "official"): "icloud_support",
    ("youtube_premium", "official"): "appstore_iap",
    ("disney_plus", "official"): "disney_web",
}


def obs(
    service: str,
    plan: str,
    cc: str,
    amount: str,
    currency: str,
    *,
    channel: str = "web",
    period: str = "month",
    kind: str = "official",
    extraction: str = "parser",
    flags: tuple[str, ...] = (),
    status: str = "accepted",
    confidence: float = 0.95,
) -> PriceObservation:
    draft = ObservationDraft(
        service_id=service,
        plan_id=plan,
        plan_label_raw=plan.replace("_", " ").title(),
        country=cc,
        currency=currency,
        amount=Decimal(amount),
        billing_period=period,
        flags=list(flags),
    )
    row = PriceObservation.from_draft(
        draft,
        channel=channel,
        source_url=f"https://example.test/{service}/{cc.lower()}",
        source_kind=kind,
        adapter_id=ADAPTERS.get((service, kind), "test_adapter"),
        observed_at=NOW,
        content_hash="0123456789abcdef",
        extraction=extraction,
        vat_included=True,
        confidence=confidence,
    )
    return row.model_copy(update={"status": status}) if status != "accepted" else row


def task(
    adapter: str, service: str, countries: list[str], status: str = "ok", channel: str = "web"
) -> TaskResult:
    return TaskResult(
        task_id=f"{adapter}:{service}:{'_'.join(countries)}",
        adapter_id=adapter,
        status=status,
        service_ids=[service],
        countries=countries,
        channel=channel,
    )


def ok_tasks(rows: list[PriceObservation]) -> list[TaskResult]:
    """One successful task per (adapter, service, country, channel) seen in `rows`."""
    seen = sorted({(r.adapter_id, r.service_id, r.country, r.channel) for r in rows})
    return [task(a, s, [cc], channel=ch) for a, s, cc, ch in seen]


def check(
    current: list[PriceObservation],
    catalog,
    *,
    previous: dict[str, PublishedPrice] | None = None,
    state: PipelineState | None = None,
    tasks: list[TaskResult] | None = None,
    run_date: date = D1,
    fx: FxTable = FX,
    settings: Settings = SETTINGS,
) -> ValidationOutcome:
    return validate(
        current,
        previous=previous or {},
        state=state or PipelineState(),
        task_results=ok_tasks(current) if tasks is None else tasks,
        catalog=catalog,
        fx=fx,
        run_date=run_date,
        settings=settings,
    )


def publish(
    outcome: ValidationOutcome, previous: dict[str, PublishedPrice], run_date: date
) -> dict[str, PublishedPrice]:
    """What the persist step does with publishable(): keep first_seen, bump last_changed."""
    out: dict[str, PublishedPrice] = {}
    for row in publishable(outcome):
        prev = previous.get(row.obs_key)
        changed = prev is None or (prev.amount, prev.currency) != (row.amount, row.currency)
        data = {k: v for k, v in row.model_dump().items() if k in PublishedPrice.model_fields}
        out[row.obs_key] = PublishedPrice(
            **data,
            first_seen=prev.first_seen if prev else run_date,
            last_changed=run_date if changed or prev is None else prev.last_changed,
        )
    return out


def published(*rows: PriceObservation, run_date: date = D0) -> dict[str, PublishedPrice]:
    return publish(ValidationOutcome(observations=list(rows)), {}, run_date)


def live(outcome: ValidationOutcome) -> dict[str, PriceObservation]:
    return {r.obs_key: r for r in outcome.observations if r.status != "rejected"}


def rules(outcome: ValidationOutcome) -> list[tuple[str, str]]:
    return [(a.obs_key, a.rule) for a in outcome.anomalies]


SE_STD = "netflix:standard:SE:web:month"
SE_BASIC = "netflix:basic:SE:web:month"
SE_PREMIUM = "netflix:premium:SE:web:month"
CO_STD = "netflix:standard:CO:web:month"


# ------------------------------------------------------------------------------- reject


def test_valid_rows_are_accepted_with_monthly_eur(catalog):
    out = check(
        [
            obs("netflix", "standard", "SE", "169", "SEK"),
            obs("spotify", "individual", "SE", "1668", "SEK", period="year"),
        ],
        catalog,
    )
    rows = live(out)
    assert {r.status for r in rows.values()} == {"accepted"}
    assert rows[SE_STD].monthly_eur == Decimal("15.3636")
    assert rows["spotify:individual:SE:web:year"].monthly_eur == Decimal("12.6364")  # 139 SEK/mo
    assert out.anomalies == [] and out.review_queue == []
    assert out.key_state == {k: KeyState() for k in sorted(rows)}


def test_currency_mismatch_is_rejected_and_never_published(catalog):
    out = check([obs("netflix", "standard", "SE", "15.99", "EUR")], catalog)
    (row,) = out.observations
    assert row.status == "rejected"
    assert row.flags == ["currency_mismatch"]
    assert rules(out) == [(SE_STD, "currency_mismatch")]
    assert "EUR not in SEK" in out.anomalies[0].detail
    assert publishable(out) == []
    assert out.key_state == {}


def test_apple_billing_allows_the_storefront_currency(catalog):
    key = "icloud_plus:200gb:AR:web:month"
    usd = obs("icloud_plus", "200gb", "AR", "2.99", "USD", flags=("apple_billing",))
    assert check([usd], catalog).observations[0].status == "accepted"
    # The local currency stays valid for Apple-billed services.
    ars = obs("icloud_plus", "200gb", "AR", "3000", "ARS", flags=("apple_billing",))
    assert check([ars], catalog).observations[0].status == "accepted"
    # Without the flag USD is not an Argentine web currency.
    plain = obs("icloud_plus", "200gb", "AR", "2.99", "USD")
    out = check([plain], catalog)
    assert out.observations[0].status == "rejected"
    assert rules(out) == [(key, "currency_mismatch")]
    # The flag only adds the storefront currency, not any currency.
    eur = obs("icloud_plus", "200gb", "AR", "2.99", "EUR", flags=("apple_billing",))
    assert check([eur], catalog).observations[0].status == "rejected"
    # Countries without an appstore_currency gain nothing.
    se_usd = obs("icloud_plus", "200gb", "SE", "2.99", "USD", flags=("apple_billing",))
    assert check([se_usd], catalog).observations[0].flags == ["apple_billing", "currency_mismatch"]


def test_argentina_app_store_bills_in_usd(catalog):
    usd = obs("youtube_premium", "individual", "AR", "7.99", "USD", channel="ios_iap")
    ars = obs("youtube_premium", "individual", "AR", "9999", "ARS", channel="ios_iap")
    assert check([usd], catalog).observations[0].status == "accepted"
    rejected = check([ars], catalog).observations[0]
    assert (rejected.status, rejected.flags) == ("rejected", ["currency_mismatch"])


def with_currency_overrides(catalog, adapter: str, overrides: dict) -> Catalog:
    """Copy of the catalog whose `adapter` sources pin params.currency_overrides."""
    services = []
    for svc in catalog.services:
        sources = [
            src.model_copy(update={"params": {**src.params, "currency_overrides": overrides}})
            if src.adapter == adapter
            else src
            for src in svc.sources
        ]
        services.append(svc.model_copy(update={"sources": sources}))
    return catalog.model_copy(update={"services": services})


def test_source_currency_overrides_are_allowed(catalog):
    # tompec lists Romania in EUR; the source pins it with params.currency_overrides.
    pinned = with_currency_overrides(catalog, "tompec_netflix", {"RO": "EUR"})
    eur = obs("netflix", "standard", "RO", "10.99", "EUR", kind="secondary")
    out = check([eur], catalog)
    assert (out.observations[0].status, rules(out)) == (
        "rejected",
        [("netflix:standard:RO:web:month", "currency_mismatch")],
    )
    out = check([eur], pinned)
    assert (out.observations[0].status, out.observations[0].flags) == ("accepted", [])
    assert [p.currency for p in publishable(out)] == ["EUR"]
    # The pin adds a currency; the country's own one stays valid.
    ron = obs("netflix", "standard", "RO", "55", "RON", kind="secondary")
    assert check([ron], pinned).observations[0].status == "accepted"
    # It belongs to that source and that country only.
    official = obs("netflix", "standard", "RO", "10.99", "EUR")
    assert check([official], pinned).observations[0].status == "rejected"
    se = obs("netflix", "standard", "SE", "15.99", "EUR", kind="secondary")
    assert check([se], pinned).observations[0].status == "rejected"
    # A list of currencies works too (the adapters accept both forms).
    listed = with_currency_overrides(catalog, "netflix_help", {"RO": ["EUR"]})
    assert check([official], listed).observations[0].status == "accepted"


@pytest.mark.parametrize(
    ("amount", "status"),
    [("16900", "rejected"), ("1.69", "rejected"), ("330", "accepted"), ("16.50", "accepted")],
)
def test_plausible_monthly_eur_bounds(catalog, amount, status):
    # netflix standard: [1.5, 30] EUR/month, inclusive; 330 SEK = 30.00 EUR, 16.50 SEK = 1.50.
    out = check([obs("netflix", "standard", "SE", amount, "SEK")], catalog)
    (row,) = out.observations
    assert row.status == status
    if status == "rejected":
        assert row.flags == ["implausible"]
        assert rules(out) == [(SE_STD, "implausible")]


def test_annual_price_is_checked_per_month(catalog):
    # 1690 SEK/year = 12.80 EUR/month: plausible, although 1690 SEK/month would not be.
    row = check([obs("netflix", "standard", "SE", "1690", "SEK", period="year")], catalog)
    assert row.observations[0].status == "accepted"


def test_missing_fx_skips_plausibility_with_flag(catalog):
    fx = fx_table({"SEK": "11"})
    out = check(
        [
            obs("netflix", "standard", "CO", "38900", "COP"),
            obs("spotify", "individual", "CO", "9", "COP"),  # absurd, but cannot be checked
            obs("netflix", "standard", "SE", "169", "SEK"),
        ],
        catalog,
        fx=fx,
    )
    rows = live(out)
    for key in (CO_STD, "spotify:individual:CO:web:month"):
        assert rows[key].status == "accepted"
        assert rows[key].flags == ["no_fx"]
        assert rows[key].monthly_eur is None
    assert rows[SE_STD].flags == []
    (anomaly,) = out.anomalies
    assert (anomaly.rule, anomaly.obs_key) == ("no_fx", CO_STD)
    assert "COP" in anomaly.detail and "2 row(s)" in anomaly.detail


def test_non_positive_amount(catalog):
    out = check([obs("netflix", "standard", "SE", "0", "SEK")], catalog)
    assert out.observations[0].flags == ["non_positive"]
    assert rules(out) == [(SE_STD, "non_positive")]


def test_unknown_plan_service_and_country(catalog):
    out = check(
        [
            obs("netflix", "ultra", "SE", "199", "SEK"),
            obs("hulu", "basic", "US", "9.99", "USD"),
            obs("netflix", "standard", "XX", "9.99", "USD"),
        ],
        catalog,
    )
    assert {r.status for r in out.observations} == {"rejected"}
    assert rules(out) == [
        ("hulu:basic:US:web:month", "unknown_plan"),
        ("netflix:standard:XX:web:month", "unknown_country"),
        ("netflix:ultra:SE:web:month", "unknown_plan"),
    ]
    assert publishable(out) == []


def test_rows_rejected_upstream_stay_rejected(catalog):
    row = obs("netflix", "standard", "SE", "169", "SEK", status="rejected", flags=("llm_check",))
    out = check([row], catalog)
    (result,) = out.observations
    assert (result.status, result.flags) == ("rejected", ["llm_check"])
    assert out.anomalies == []
    assert publishable(out) == []


# --------------------------------------------------------------------------- quarantine


def test_big_change_is_quarantined_and_previous_value_held(catalog):
    previous = published(obs("netflix", "standard", "SE", "169", "SEK"))
    out = check([obs("netflix", "standard", "SE", "229", "SEK")], catalog, previous=previous)
    (row,) = out.observations
    assert (row.status, row.flags) == ("quarantined", ["big_change"])
    assert out.key_state[SE_STD] == KeyState(pending_value="229|SEK|month", pending_since=D1)
    (entry,) = out.review_queue
    assert set(entry) == {"obs_key", "rule", "rules", "previous", "current"}
    assert (entry["obs_key"], entry["rule"], entry["rules"]) == (
        SE_STD,
        "big_change",
        ["big_change"],
    )
    assert entry["previous"]["amount"] == "169" and entry["current"]["amount"] == "229"
    json.dumps(out.review_queue)  # plain JSON
    (anomaly,) = out.anomalies
    assert anomaly.rule == "big_change"
    assert (anomaly.previous, anomaly.current) == ("169 SEK/month", "229 SEK/month")
    assert "35.5%" in anomaly.detail and "quarantined" in anomaly.detail

    (held,) = publishable(out)
    assert (held.amount, held.status, held.flags) == (
        Decimal("169"),
        "carried_forward",
        ["quarantine_hold"],
    )
    assert held.monthly_eur == Decimal("15.3636")


@pytest.mark.parametrize(
    ("amount", "pct", "status"),
    [
        ("130", 30.0, "accepted"),  # exactly 30% is not "> 30%"
        ("131", 30.0, "quarantined"),
        ("69", 30.0, "quarantined"),  # drops count too
        ("131", 50.0, "accepted"),  # threshold comes from Settings
    ],
)
def test_big_change_threshold(catalog, amount, pct, status):
    previous = published(obs("netflix", "standard", "SE", "100", "SEK"))
    out = check(
        [obs("netflix", "standard", "SE", amount, "SEK")],
        catalog,
        previous=previous,
        settings=Settings(quarantine_change_pct=pct),
    )
    assert out.observations[0].status == status


def test_big_change_needs_the_same_currency(catalog):
    flags = ("apple_billing",)
    previous = published(obs("icloud_plus", "200gb", "AR", "2.99", "USD", flags=flags))
    out = check(
        [obs("icloud_plus", "200gb", "AR", "4500", "ARS", flags=flags)], catalog, previous=previous
    )
    assert out.observations[0].status == "accepted"


def test_pending_value_seen_again_is_released(catalog):
    previous = published(obs("netflix", "standard", "SE", "169", "SEK"))
    for token in ("229|SEK|month", "229.00|SEK|month"):
        state = PipelineState(keys={SE_STD: KeyState(pending_value=token, pending_since=D0)})
        out = check(
            [obs("netflix", "standard", "SE", "229", "SEK")],
            catalog,
            previous=previous,
            state=state,
        )
        (row,) = out.observations
        assert (row.status, row.flags) == ("accepted", [])
        assert out.review_queue == []
        assert out.key_state[SE_STD] == KeyState()
        (anomaly,) = out.anomalies
        assert anomaly.rule == "big_change" and "released" in anomaly.detail
        assert [p.amount for p in publishable(out)] == [Decimal("229")]


def test_different_pending_value_stays_quarantined(catalog):
    previous = published(obs("netflix", "standard", "SE", "169", "SEK"))
    state = PipelineState(keys={SE_STD: KeyState(pending_value="239|SEK|month", pending_since=D0)})
    out = check(
        [obs("netflix", "standard", "SE", "229", "SEK")], catalog, previous=previous, state=state
    )
    assert out.observations[0].status == "quarantined"
    assert out.key_state[SE_STD] == KeyState(pending_value="229|SEK|month", pending_since=D1)
    # Same amount in another period is another value.
    state = PipelineState(keys={SE_STD: KeyState(pending_value="229|SEK|year")})
    out = check(
        [obs("netflix", "standard", "SE", "229", "SEK")], catalog, previous=previous, state=state
    )
    assert out.observations[0].status == "quarantined"


def test_second_opinion_releases(catalog):
    previous = published(obs("netflix", "standard", "SE", "169", "SEK"))
    row = obs("netflix", "standard", "SE", "229", "SEK", flags=("second_opinion_agrees",))
    out = check([row], catalog, previous=previous)
    (result,) = out.observations
    assert (result.status, result.flags) == ("accepted", ["second_opinion_agrees"])
    assert out.review_queue == []
    assert "second opinion" in out.anomalies[0].detail


def test_adjudicate_rerun_releases_a_quarantined_row(catalog):
    """The adjudicate node re-runs validate() with second_opinion_agrees on the stored rows."""
    previous = published(obs("netflix", "standard", "SE", "169", "SEK"))
    first = check([obs("netflix", "standard", "SE", "229", "SEK")], catalog, previous=previous)
    (quarantined,) = first.observations
    agreed = quarantined.model_copy(update={"flags": [*quarantined.flags, "second_opinion_agrees"]})
    second = check([agreed], catalog, previous=previous)
    (row,) = second.observations
    assert (row.status, row.flags) == ("accepted", ["second_opinion_agrees"])


def test_tier_order_quarantines_only_the_changed_row(catalog):
    previous = published(
        obs("netflix", "basic", "SE", "129", "SEK"),
        obs("netflix", "standard", "SE", "169", "SEK"),
        obs("netflix", "premium", "SE", "229", "SEK"),
    )
    out = check(
        [
            obs("netflix", "basic", "SE", "129", "SEK"),
            obs("netflix", "standard", "SE", "119", "SEK"),  # -29.6%: below big_change
            obs("netflix", "premium", "SE", "229", "SEK"),
        ],
        catalog,
        previous=previous,
    )
    rows = live(out)
    assert rows[SE_STD].status == "quarantined"
    assert rows[SE_STD].flags == ["tier_order"]
    assert rows[SE_BASIC].status == rows[SE_PREMIUM].status == "accepted"
    assert rules(out) == [(SE_STD, "tier_order")]
    assert "standard (rank 3) 119 < basic (rank 1) 129" in out.anomalies[0].detail
    assert out.review_queue[0]["rule"] == "tier_order"
    assert {p.obs_key: p.amount for p in publishable(out)}[SE_STD] == Decimal("169")


def test_tier_order_ignores_ads_plans_and_other_groups(catalog):
    out = check(
        [
            obs("netflix", "basic_with_ads", "SE", "49", "SEK"),
            obs("netflix", "standard_with_ads", "SE", "39", "SEK"),  # ads plans: not ranked
            obs("netflix", "basic", "SE", "129", "SEK"),
            obs("netflix", "standard", "SE", "169", "SEK"),
            obs("netflix", "premium", "SE", "1500", "SEK", period="year"),  # other period
            obs("netflix", "premium", "CO", "26900", "COP"),  # other country
        ],
        catalog,
    )
    assert {r.status for r in out.observations} == {"accepted"}


def test_tier_order_on_new_keys_holds_both_rows_until_confirmed(catalog):
    rows = [
        obs("netflix", "basic", "SE", "169", "SEK"),
        obs("netflix", "standard", "SE", "129", "SEK"),
    ]
    first = check(rows, catalog)
    assert {r.status for r in first.observations} == {"quarantined"}
    assert [e["previous"] for e in first.review_queue] == [None, None]
    assert publishable(first) == []  # nothing was published before: nothing to hold
    second = check(rows, catalog, state=PipelineState(keys=first.key_state), run_date=D2)
    assert {r.status for r in second.observations} == {"accepted"}
    assert len(publishable(second)) == 2


def test_tier_order_already_published_is_not_reflagged(catalog):
    rows = [
        obs("netflix", "basic", "SE", "169", "SEK"),
        obs("netflix", "standard", "SE", "129", "SEK"),
    ]
    out = check(rows, catalog, previous=published(*rows))
    assert {r.status for r in out.observations} == {"accepted"}
    assert out.anomalies == []


def test_llm_new_key_is_quarantined(catalog):
    llm = obs("netflix", "standard", "CO", "38900", "COP", extraction="llm", confidence=0.7)
    out = check([llm], catalog)
    assert (out.observations[0].status, out.observations[0].flags) == (
        "quarantined",
        ["llm_new_key"],
    )
    assert out.review_queue[0]["previous"] is None
    assert publishable(out) == []
    # A published key is not new.
    previous = published(obs("netflix", "standard", "CO", "38900", "COP"))
    assert check([llm], catalog, previous=previous).observations[0].status == "accepted"


def test_llm_new_key_confirmed_by_secondary(catalog):
    llm = obs("netflix", "standard", "CO", "38900", "COP", extraction="llm", confidence=0.7)
    agree = obs("netflix", "standard", "CO", "38900", "COP", kind="secondary")
    out = check([llm, agree], catalog)
    (row,) = out.observations
    assert (row.status, row.extraction, row.source_kind) == ("accepted", "llm", "official")

    disagree = obs("netflix", "standard", "CO", "44900", "COP", kind="secondary")
    out = check([llm, disagree], catalog)
    (row,) = out.observations
    assert row.status == "quarantined"
    assert row.flags == ["secondary_disagrees", "llm_new_key"]


# ------------------------------------------------------------------------ secondary rows


@pytest.mark.parametrize(
    ("secondary", "flags"),
    [
        ("38900", []),
        ("39678", []),
        ("39900", ["secondary_disagrees"]),
        ("37900", ["secondary_disagrees"]),
    ],
)
def test_secondary_is_compared_then_dropped(catalog, secondary, flags):
    out = check(
        [
            obs("netflix", "standard", "CO", "38900", "COP"),
            obs("netflix", "standard", "CO", secondary, "COP", kind="secondary"),
        ],
        catalog,
    )
    (row,) = out.observations  # the secondary row is not in the output
    assert (row.source_kind, row.adapter_id, row.status) == ("official", "netflix_help", "accepted")
    assert row.flags == flags
    assert [a.rule for a in out.anomalies] == flags


def test_secondary_becomes_the_row_when_official_is_missing_or_rejected(catalog):
    sec = obs("netflix", "standard", "CO", "38900", "COP", kind="secondary")
    tasks = [
        task("netflix_help", "netflix", ["CO"], status="blocked"),
        task("tompec_netflix", "netflix", ["CO", "SE"]),
    ]
    out = check([sec], catalog, tasks=tasks)
    (row,) = out.observations
    assert (row.source_kind, row.status) == ("secondary", "accepted")

    wrong = obs("netflix", "standard", "CO", "8.99", "USD")
    out = check([wrong, sec], catalog)
    assert [(r.source_kind, r.status) for r in out.observations] == [
        ("secondary", "accepted"),
        ("official", "rejected"),
    ]
    assert [p.source_kind for p in publishable(out)] == ["secondary"]


def test_quarantined_official_row_is_not_replaced_by_secondary(catalog):
    previous = published(obs("netflix", "standard", "CO", "26900", "COP"))
    out = check(
        [
            obs("netflix", "standard", "CO", "38900", "COP"),  # +44.6%
            obs("netflix", "standard", "CO", "26900", "COP", kind="secondary"),  # stale dataset
        ],
        catalog,
        previous=previous,
    )
    (row,) = out.observations
    assert (row.source_kind, row.status) == ("official", "quarantined")
    assert set(row.flags) == {"secondary_disagrees", "big_change"}
    (held,) = publishable(out)
    assert (held.amount, held.flags) == (Decimal("26900"), ["quarantine_hold"])


def test_duplicate_official_rows_prefer_the_parser(catalog):
    out = check(
        [
            obs("netflix", "standard", "SE", "179", "SEK", extraction="llm", confidence=0.99),
            obs("netflix", "standard", "SE", "169", "SEK"),
        ],
        catalog,
        previous=published(obs("netflix", "standard", "SE", "169", "SEK")),
    )
    (row,) = out.observations
    assert (row.extraction, row.amount) == ("parser", Decimal("169"))


def _official_down(catalog, runs: int, secondary: str | None, *, official_status="blocked"):
    """Netflix SE standard published by netflix_help at 169; then `runs` runs where the official
    task has `official_status` and tompec (secondary) says `secondary`."""
    previous = published(obs("netflix", "standard", "SE", "169", "SEK"))
    state = PipelineState()
    history = []
    for _ in range(runs):
        current = []
        if secondary is not None:
            current.append(obs("netflix", "standard", "SE", secondary, "SEK", kind="secondary"))
        tasks = [
            task("netflix_help", "netflix", ["SE"], status=official_status),
            task("tompec_netflix", "netflix", ["CO", "SE"]),
        ]
        out = check(current, catalog, previous=previous, state=state, tasks=tasks)
        history.append(out)
        previous = publish(out, previous, D1)
        state = PipelineState(keys=out.key_state)
    return history, previous


def test_secondary_does_not_replace_an_official_row_while_its_task_fails(catalog):
    (out,), pub = _official_down(catalog, 1, "149")
    (row,) = out.observations  # the secondary row is held back, not output
    assert (row.status, row.source_kind, row.adapter_id, row.amount) == (
        "carried_forward",
        "official",
        "netflix_help",
        Decimal("169"),
    )
    assert row.flags == ["secondary_disagrees"]
    assert out.key_state[SE_STD] == KeyState(missing_runs=1)
    assert rules(out) == [(SE_STD, "secondary_disagrees")]
    assert out.anomalies[0].previous == "149 SEK/month"
    assert out.review_queue == []
    assert [(p.amount, p.source_kind) for p in publishable(out)] == [(Decimal("169"), "official")]
    assert pub[SE_STD].last_changed == D0  # no false "changed" entry

    # An agreeing secondary row adds no flag.
    (out,), _ = _official_down(catalog, 1, "169")
    assert [(r.status, r.flags) for r in out.observations] == [("carried_forward", [])]


def test_secondary_takes_over_once_the_official_row_ages_out(catalog):
    history, pub = _official_down(catalog, 8, "149")
    for n, out in enumerate(history[:7], start=1):
        (row,) = out.observations
        assert (row.status, row.source_kind, row.amount) == (
            "carried_forward",
            "official",
            Decimal("169"),
        )
        assert out.key_state[SE_STD] == KeyState(missing_runs=n)
    (row,) = history[7].observations
    assert (row.status, row.source_kind, row.adapter_id, row.amount) == (
        "accepted",
        "secondary",
        "tompec_netflix",
        Decimal("149"),
    )
    assert history[7].key_state[SE_STD] == KeyState()
    assert (pub[SE_STD].source_kind, pub[SE_STD].amount) == ("secondary", Decimal("149"))


def test_plan_gone_from_the_official_page_is_not_kept_alive_by_the_secondary(catalog):
    history, _ = _official_down(catalog, 4, "149", official_status="ok")
    for n, out in enumerate(history[:3], start=1):
        assert [(r.status, r.source_kind) for r in out.observations] == [
            ("carried_forward", "official")
        ]
        assert out.key_state[SE_STD] == KeyState(absent_ok_runs=n)
    assert [(r.status, r.source_kind) for r in history[3].observations] == [
        ("accepted", "secondary")
    ]


def test_secondary_fallback_with_a_big_change_is_quarantined_then_released(catalog):
    history, pub = _official_down(catalog, 9, "99")  # -41% vs the official 169
    fallback = history[7]
    (row,) = fallback.observations
    assert (row.status, row.source_kind, row.flags) == ("quarantined", "secondary", ["big_change"])
    # The counters of the aged-out official row survive the quarantine: no second carry cycle.
    assert fallback.key_state[SE_STD] == KeyState(
        missing_runs=8, pending_value="99|SEK|month", pending_since=D1
    )
    (held,) = publishable(fallback)
    assert (held.amount, held.source_kind, held.flags) == (
        Decimal("169"),
        "official",
        ["secondary_disagrees", "quarantine_hold"],
    )
    (row,) = history[8].observations
    assert (row.status, row.source_kind, row.amount) == ("accepted", "secondary", Decimal("99"))
    assert (pub[SE_STD].source_kind, pub[SE_STD].amount) == ("secondary", Decimal("99"))


def test_rejected_official_row_keeps_the_previous_official_value(catalog):
    previous = published(obs("netflix", "standard", "SE", "169", "SEK"))
    out = check(
        [
            obs("netflix", "standard", "SE", "16900", "SEK"),  # implausible
            obs("netflix", "standard", "SE", "149", "SEK", kind="secondary"),
        ],
        catalog,
        previous=previous,
    )
    assert [(r.status, r.source_kind, r.amount) for r in out.observations] == [
        ("carried_forward", "official", Decimal("169")),
        ("rejected", "official", Decimal("16900")),
    ]
    assert out.observations[0].flags == ["secondary_disagrees"]
    assert out.key_state[SE_STD] == KeyState(missing_runs=1)
    assert sorted(rules(out)) == [(SE_STD, "implausible"), (SE_STD, "secondary_disagrees")]
    assert [(p.amount, p.source_kind) for p in publishable(out)] == [(Decimal("169"), "official")]


def test_secondary_only_run_leaves_official_rows_untouched(catalog):
    """A run filtered to the secondary adapter (--adapters tompec_netflix) never attempted the
    official page: the official row and its state stay as they were."""
    previous = published(obs("netflix", "standard", "SE", "169", "SEK"))
    state = PipelineState(keys={SE_STD: KeyState(missing_runs=2)})
    out = check(
        [obs("netflix", "standard", "SE", "149", "SEK", kind="secondary")],
        catalog,
        previous=previous,
        state=state,
        tasks=[task("tompec_netflix", "netflix", ["CO", "SE"])],
    )
    (row,) = out.observations
    assert (row.status, row.source_kind, row.amount) == ("accepted", "official", Decimal("169"))
    assert row.flags == ["secondary_disagrees"]
    assert out.key_state[SE_STD] == KeyState(missing_runs=2)


def test_secondary_published_key_keeps_using_the_secondary(catalog):
    previous = published(obs("netflix", "standard", "SE", "149", "SEK", kind="secondary"))
    out = check(
        [obs("netflix", "standard", "SE", "155", "SEK", kind="secondary")],
        catalog,
        previous=previous,
        tasks=[
            task("netflix_help", "netflix", ["SE"], status="blocked"),
            task("tompec_netflix", "netflix", ["CO", "SE"]),
        ],
    )
    (row,) = out.observations
    assert (row.status, row.source_kind, row.amount) == ("accepted", "secondary", Decimal("155"))


# ------------------------------------------------------------------------- carry forward


def _netflix_se(*plans_amounts: tuple[str, str]) -> list[PriceObservation]:
    return [obs("netflix", plan, "SE", amount, "SEK") for plan, amount in plans_amounts]


def test_carry_forward_lifecycle_across_three_runs(catalog):
    # Run 1: everything found.
    run1 = _netflix_se(("basic", "129"), ("standard", "169"), ("premium", "229"))
    out1 = check(run1, catalog, run_date=D1)
    pub1 = publish(out1, {}, D1)
    assert {k: p.status for k, p in pub1.items()} == {
        SE_BASIC: "accepted",
        SE_PREMIUM: "accepted",
        SE_STD: "accepted",
    }
    state1 = PipelineState(keys=out1.key_state)

    # Run 2: the page fetch failed -> every key carried forward.
    failed = [task("netflix_help", "netflix", ["SE"], status="failed")]
    out2 = check([], catalog, previous=pub1, state=state1, tasks=failed, run_date=D2)
    assert {r.obs_key: r.status for r in out2.observations} == {
        SE_BASIC: "carried_forward",
        SE_PREMIUM: "carried_forward",
        SE_STD: "carried_forward",
    }
    assert out2.key_state[SE_STD] == KeyState(missing_runs=1)
    assert live(out2)[SE_STD].monthly_eur == Decimal("15.3636")
    pub2 = publish(out2, pub1, D2)
    assert {p.amount for p in pub2.values()} == {Decimal("129"), Decimal("169"), Decimal("229")}
    assert {p.last_changed for p in pub2.values()} == {D1}
    state2 = PipelineState(keys=out2.key_state)

    # Run 3: page back, standard moved a little, premium gone from the page.
    run3 = _netflix_se(("basic", "129"), ("standard", "179"))
    out3 = check(run3, catalog, previous=pub2, state=state2, run_date=D3)
    rows = live(out3)
    assert (rows[SE_BASIC].status, rows[SE_STD].status) == ("accepted", "accepted")
    assert (rows[SE_PREMIUM].status, rows[SE_PREMIUM].amount) == (
        "carried_forward",
        Decimal("229"),
    )
    assert out3.key_state[SE_STD] == KeyState()
    # Counters reset only when the key is seen again.
    assert out3.key_state[SE_PREMIUM] == KeyState(missing_runs=1, absent_ok_runs=1)
    pub3 = publish(out3, pub2, D3)
    assert (pub3[SE_STD].amount, pub3[SE_STD].last_changed) == (Decimal("179"), D3)
    assert pub3[SE_PREMIUM].status == "carried_forward"


def test_quarantine_lifecycle_across_three_runs(catalog):
    pub0 = published(obs("netflix", "standard", "SE", "169", "SEK"))
    out1 = check(_netflix_se(("standard", "169")), catalog, previous=pub0, run_date=D1)
    pub1 = publish(out1, pub0, D1)

    out2 = check(
        _netflix_se(("standard", "249")),
        catalog,
        previous=pub1,
        state=PipelineState(keys=out1.key_state),
        run_date=D2,
    )
    pub2 = publish(out2, pub1, D2)
    assert out2.observations[0].status == "quarantined"
    assert (pub2[SE_STD].amount, pub2[SE_STD].flags) == (Decimal("169"), ["quarantine_hold"])

    out3 = check(
        _netflix_se(("standard", "249")),
        catalog,
        previous=pub2,
        state=PipelineState(keys=out2.key_state),
        run_date=D3,
    )
    pub3 = publish(out3, pub2, D3)
    assert out3.observations[0].status == "accepted"
    assert (pub3[SE_STD].amount, pub3[SE_STD].status, pub3[SE_STD].flags) == (
        Decimal("249"),
        "accepted",
        [],
    )
    assert out3.key_state[SE_STD] == KeyState()


def test_pending_value_survives_a_failed_run(catalog):
    previous = published(obs("netflix", "standard", "SE", "169", "SEK"))
    state = PipelineState(keys={SE_STD: KeyState(pending_value="249|SEK|month", pending_since=D1)})
    failed = [task("netflix_help", "netflix", ["SE"], status="blocked")]
    out = check([], catalog, previous=previous, state=state, tasks=failed, run_date=D2)
    assert out.key_state[SE_STD] == KeyState(
        missing_runs=1, pending_value="249|SEK|month", pending_since=D1
    )
    out = check(
        _netflix_se(("standard", "249")),
        catalog,
        previous=previous,
        state=PipelineState(keys=out.key_state),
        run_date=D3,
    )
    assert out.observations[0].status == "accepted"


def _repeat(catalog, runs: int, task_status: str, current=()):
    previous = published(obs("netflix", "standard", "SE", "169", "SEK", flags=("x",)))
    state = PipelineState()
    history = []
    for _ in range(runs):
        tasks = [task("netflix_help", "netflix", ["SE"], status=task_status)]
        out = check(list(current), catalog, previous=previous, state=state, tasks=tasks)
        history.append(out)
        previous = publish(out, previous, D1)
        state = PipelineState(keys=out.key_state)
    return history


def test_failed_task_carries_seven_runs_then_drops(catalog):
    history = _repeat(catalog, 8, "failed")
    for n, out in enumerate(history[:7], start=1):
        (row,) = out.observations
        assert (row.status, row.amount, row.flags) == ("carried_forward", Decimal("169"), ["x"])
        assert out.key_state[SE_STD].missing_runs == n
    assert history[7].observations == []
    assert history[7].key_state == {}


@pytest.mark.parametrize("status", ["ok", "partial", "not_available"])
def test_plan_absent_from_a_good_page_carries_three_runs_then_drops(catalog, status):
    other = [obs("netflix", "basic", "SE", "129", "SEK")]
    history = _repeat(catalog, 4, status, current=other)
    for n, out in enumerate(history[:3], start=1):
        assert live(out)[SE_STD].status == "carried_forward"
        assert out.key_state[SE_STD] == KeyState(absent_ok_runs=n)
    assert SE_STD not in live(history[3])
    assert SE_STD not in history[3].key_state


def test_failure_is_judged_by_the_previous_rows_adapter(catalog):
    tasks = [
        task("netflix_help", "netflix", ["CO"], status="failed"),
        task("tompec_netflix", "netflix", ["CO", "SE"]),  # ok, but without this plan
    ]
    previous = published(obs("netflix", "standard", "CO", "38900", "COP"))
    out = check([], catalog, previous=previous, tasks=tasks)
    assert out.key_state[CO_STD] == KeyState(missing_runs=1)

    from_secondary = published(obs("netflix", "standard", "CO", "38900", "COP", kind="secondary"))
    out = check([], catalog, previous=from_secondary, tasks=tasks)
    assert out.key_state[CO_STD] == KeyState(absent_ok_runs=1)


def test_rejected_today_counts_as_missing(catalog):
    previous = published(obs("netflix", "standard", "SE", "169", "SEK"))
    out = check([obs("netflix", "standard", "SE", "16900", "SEK")], catalog, previous=previous)
    assert [(r.status, r.amount) for r in out.observations] == [
        ("carried_forward", Decimal("169")),
        ("rejected", Decimal("16900")),
    ]
    assert out.key_state[SE_STD] == KeyState(missing_runs=1)
    assert [(p.status, p.amount) for p in publishable(out)] == [("carried_forward", Decimal("169"))]


def test_keys_outside_this_runs_tasks_are_untouched(catalog):
    spotify = obs("spotify", "individual", "SE", "139", "SEK")
    previous = published(spotify, obs("netflix", "standard", "SE", "169", "SEK"))
    key = "spotify:individual:SE:web:month"
    state = PipelineState(keys={key: KeyState(missing_runs=2)})
    out = check(_netflix_se(("standard", "169")), catalog, previous=previous, state=state)
    row = live(out)[key]
    assert (row.status, row.amount) == ("accepted", Decimal("139"))
    assert out.key_state[key] == KeyState(missing_runs=2)


def test_keys_that_left_the_catalog_are_dropped(catalog):
    gone = [
        obs("disney_plus", "standard", "IN", "299", "INR"),  # disney_web excludes IN
        obs("netflix", "ultra", "SE", "199", "SEK"),  # plan no longer exists
        obs("hulu", "basic", "US", "9.99", "USD"),  # service no longer exists
    ]
    previous = published(*gone)
    state = PipelineState(keys={k: KeyState(missing_runs=1) for k in previous})
    tasks = [task("disney_web", "disney_plus", ["IN"], status="not_configured")]
    out = check([], catalog, previous=previous, state=state, tasks=tasks)
    assert out.observations == []
    assert out.key_state == {}


def test_state_only_keys_age_out(catalog):
    # A quarantined new key (never published) whose plan disappears from the page.
    key = "netflix:standard:CO:web:month"
    state = PipelineState(keys={key: KeyState(pending_value="38900|COP|month", absent_ok_runs=3)})
    tasks = [task("netflix_help", "netflix", ["CO"])]
    assert check([], catalog, state=state, tasks=tasks).key_state == {}
    state = PipelineState(keys={key: KeyState(pending_value="38900|COP|month")})
    out = check([], catalog, state=state, tasks=tasks)
    assert out.observations == []
    assert out.key_state[key].absent_ok_runs == 1


# --------------------------------------------------------------------------- determinism


def _mixed_rows():
    return [
        obs("netflix", "standard", "SE", "229", "SEK"),  # big change
        obs("netflix", "basic", "SE", "129", "SEK"),
        obs("netflix", "premium", "SE", "15.99", "EUR"),  # rejected
        obs("netflix", "standard", "CO", "38900", "COP", extraction="llm"),  # llm new key
        obs("spotify", "individual", "SE", "139", "SEK"),
        obs("icloud_plus", "200gb", "AR", "2.99", "USD", flags=("apple_billing",)),
        obs("youtube_premium", "individual", "AR", "7.99", "USD", channel="ios_iap"),
    ]


def _mixed_previous():
    return published(
        obs("netflix", "standard", "SE", "169", "SEK"),
        obs("netflix", "premium", "SE", "229", "SEK"),
        obs("netflix", "mobile", "SE", "59", "SEK"),  # task ok, plan absent today
    )


def _dump(out: ValidationOutcome) -> dict:
    return {
        "observations": [r.model_dump(mode="json") for r in out.observations],
        "anomalies": [a.model_dump(mode="json") for a in out.anomalies],
        "review_queue": out.review_queue,
        "key_state": {k: v.model_dump(mode="json") for k, v in out.key_state.items()},
    }


def test_output_is_sorted_and_independent_of_input_order(catalog):
    rows = _mixed_rows()
    tasks = ok_tasks(rows)
    expected = check(rows, catalog, previous=_mixed_previous(), tasks=tasks)
    keys = [r.obs_key for r in expected.observations]
    assert keys == sorted(keys)
    assert [e["obs_key"] for e in expected.review_queue] == sorted(
        e["obs_key"] for e in expected.review_queue
    )
    for seed in range(5):
        shuffled = rows[:]
        random.Random(seed).shuffle(shuffled)
        got = check(shuffled, catalog, previous=_mixed_previous(), tasks=tasks[::-1])
        assert _dump(got) == _dump(expected)


def test_revalidating_the_output_is_stable(catalog):
    rows = _mixed_rows()
    tasks = ok_tasks(rows)
    first = check(rows, catalog, previous=_mixed_previous(), tasks=tasks)
    statuses = {r.obs_key: r.status for r in first.observations}
    assert statuses["netflix:standard:SE:web:month"] == "quarantined"
    assert statuses["netflix:standard:CO:web:month"] == "quarantined"
    assert statuses["netflix:mobile:SE:web:month"] == "carried_forward"
    again = check(first.observations, catalog, previous=_mixed_previous(), tasks=tasks)
    assert _dump(again) == _dump(first)


def test_publishable_has_one_row_per_key_and_no_rejected_rows(catalog):
    out = check(_mixed_rows(), catalog, previous=_mixed_previous())
    rows = publishable(out)
    keys = [r.obs_key for r in rows]
    assert keys == sorted(set(keys))
    assert {r.status for r in rows} <= {"accepted", "carried_forward"}
    by_key = {r.obs_key: r for r in rows}
    assert "netflix:standard:CO:web:month" not in by_key  # new quarantined key: omitted
    assert by_key[SE_STD].flags == ["quarantine_hold"]
    assert by_key[SE_PREMIUM].amount == Decimal("229")  # rejected today, carried forward
    assert by_key["youtube_premium:individual:AR:ios_iap:month"].currency == "USD"

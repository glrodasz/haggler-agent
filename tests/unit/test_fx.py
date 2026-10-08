"""FX providers (ECB XML, Frankfurter v2, open.er-api) and the DefaultFxService fallback chain."""

from __future__ import annotations

from datetime import date
from decimal import Decimal

import httpx
import pytest
import respx

from haggler.fx.ecb import ECB_DAILY_URL, fetch_ecb, parse_ecb_daily
from haggler.fx.frankfurter import fetch_rates, parse_rates
from haggler.fx.open_er_api import parse_latest
from haggler.fx.service import (
    DefaultFxService,
    StaticFxService,
    business_days_between,
)
from haggler.models.fx import FxRate, FxTable

AS_OF = date(2026, 10, 8)  # a Thursday
FF_RATES = "https://api.frankfurter.dev/v2/rates"
OPEN_ER = "https://open.er-api.com/v6/latest/EUR"
PINS = {"COP": "BANREP", "ARS": "BCRA", "CLP": "BCCH", "EGP": "CBE", "NGN": "CBN"}

# Same envelope, namespaces and single-quoted attributes as the real eurofxref-daily.xml
# (values are illustrative). BGN is gone since Bulgaria adopted the euro on 2026-01-01.
ECB_XML = b"""<?xml version="1.0" encoding="UTF-8"?>
<gesmes:Envelope xmlns:gesmes="http://www.gesmes.org/xml/2002-08-01" xmlns="http://www.ecb.int/vocabulary/2002-08-01/eurofxref">
\t<gesmes:subject>Reference rates</gesmes:subject>
\t<gesmes:Sender>
\t\t<gesmes:name>European Central Bank</gesmes:name>
\t</gesmes:Sender>
\t<Cube>
\t\t<Cube time='2026-10-07'>
\t\t\t<Cube currency='USD' rate='1.1652'/>
\t\t\t<Cube currency='JPY' rate='176.45'/>
\t\t\t<Cube currency='CZK' rate='24.335'/>
\t\t\t<Cube currency='DKK' rate='7.4655'/>
\t\t\t<Cube currency='GBP' rate='0.86915'/>
\t\t\t<Cube currency='HUF' rate='391.30'/>
\t\t\t<Cube currency='PLN' rate='4.2645'/>
\t\t\t<Cube currency='RON' rate='5.0865'/>
\t\t\t<Cube currency='SEK' rate='10.9875'/>
\t\t\t<Cube currency='CHF' rate='0.9305'/>
\t\t\t<Cube currency='ISK' rate='142.20'/>
\t\t\t<Cube currency='NOK' rate='11.6640'/>
\t\t\t<Cube currency='TRY' rate='48.6520'/>
\t\t\t<Cube currency='AUD' rate='1.7690'/>
\t\t\t<Cube currency='BRL' rate='6.2140'/>
\t\t\t<Cube currency='CAD' rate='1.6250'/>
\t\t\t<Cube currency='CNY' rate='8.3010'/>
\t\t\t<Cube currency='HKD' rate='9.0650'/>
\t\t\t<Cube currency='IDR' rate='19312.45'/>
\t\t\t<Cube currency='ILS' rate='3.8540'/>
\t\t\t<Cube currency='INR' rate='103.4200'/>
\t\t\t<Cube currency='KRW' rate='1645.30'/>
\t\t\t<Cube currency='MXN' rate='21.4560'/>
\t\t\t<Cube currency='MYR' rate='4.9120'/>
\t\t\t<Cube currency='NZD' rate='2.0150'/>
\t\t\t<Cube currency='PHP' rate='67.890'/>
\t\t\t<Cube currency='SGD' rate='1.5030'/>
\t\t\t<Cube currency='THB' rate='37.850'/>
\t\t\t<Cube currency='ZAR' rate='20.1450'/>
\t\t</Cube>
\t</Cube>
</gesmes:Envelope>
"""

# Trimmed copies of the examples in Frankfurter's lib/public/v2/openapi.json (v2.1.1).
OPENAPI_RATES_EXAMPLE = [
    {"date": "2024-01-15", "base": "EUR", "quote": "EUR", "rate": 1.0},
    {"date": "2024-01-15", "base": "EUR", "quote": "GBP", "rate": 0.8623},
    {"date": "2024-01-15", "base": "EUR", "quote": "USD", "rate": 1.089},
]
OPENAPI_RATE_EXAMPLE = {"date": "2026-03-25", "base": "EUR", "quote": "USD", "rate": 1.1568}
OPENAPI_NDJSON_LINE = '{ "date": "2024-01-15", "base": "EUR", "quote": "USD", "rate": 1.089 }'
# Error body shape from Versions::V2's error handler.
UNPROCESSABLE = {"status": 422, "message": "invalid currency: XXX"}

# Shape of https://open.er-api.com/v6/latest/EUR (values illustrative).
OPEN_ER_PAYLOAD = {
    "result": "success",
    "provider": "https://www.exchangerate-api.com",
    "documentation": "https://www.exchangerate-api.com/docs/free",
    "terms_of_use": "https://www.exchangerate-api.com/terms",
    "time_last_update_unix": 1791417601,
    "time_last_update_utc": "Thu, 08 Oct 2026 00:00:01 +0000",
    "time_next_update_unix": 1791505601,
    "time_next_update_utc": "Fri, 09 Oct 2026 00:26:41 +0000",
    "time_eol_unix": 0,
    "base_code": "EUR",
    "rates": {
        "EUR": 1,
        "USD": 1.1648,
        "SEK": 10.9912,
        "COP": 4561.2817,
        "ARS": 1618.0412,
        "PEN": 3.9871,
        "PKR": 328.1203,
        "VND": 30650.4472,
        "NGN": 1712.5521,
    },
}


class FakeFrankfurter:
    """Answers /v2/rates like the real server, from per-(provider, base) tables."""

    def __init__(
        self,
        blended: dict[str, tuple[float, str]] | None = None,
        providers: dict[tuple[str, str], dict[str, tuple[float, str]]] | None = None,
    ) -> None:
        self.blended = blended or {}
        self.providers = providers or {}
        self.requests: list[dict[str, str]] = []

    def __call__(self, request: httpx.Request) -> httpx.Response:
        params = dict(request.url.params)
        self.requests.append(params)
        base = params.get("base", "EUR")
        quotes = params.get("quotes", "").split(",")
        provider = params.get("providers")
        if provider:
            table = self.providers.get((provider, base), {})
        else:
            table = self.blended if base == "EUR" else {}
        records = [
            {"date": day, "base": base, "quote": q, "rate": rate}
            for q, (rate, day) in sorted(table.items())
            if q in quotes
        ]
        return httpx.Response(200, json=records)


def default_frankfurter() -> FakeFrankfurter:
    return FakeFrankfurter(
        blended={
            "USD": (1.1650, "2026-10-07"),
            "SEK": (10.9901, "2026-10-07"),
            "PEN": (3.9855, "2026-10-07"),
            "COP": (4560.12, "2026-10-07"),
            "ARS": (1619.75, "2026-10-07"),
        },
        providers={
            # BANREP publishes only USD->COP, so base=EUR has nothing (as on the real server).
            ("BANREP", "USD"): {"COP": (3912.45, "2026-10-08")},
            ("BCRA", "EUR"): {"ARS": (1620.5, "2026-10-07")},
        },
    )


@pytest.fixture
def router():
    with respx.mock(assert_all_called=False, assert_all_mocked=True) as r:
        yield r


# ------------------------------------------------------------------ ECB


def test_parse_ecb_daily():
    day, rates = parse_ecb_daily(ECB_XML)
    assert day == date(2026, 10, 7)
    assert len(rates) == 29 and "EUR" not in rates and "BGN" not in rates
    assert rates["SEK"] == Decimal("10.9875")
    assert rates["HUF"] == Decimal("391.30")
    assert parse_ecb_daily(ECB_XML.decode()) == (day, rates)


def test_parse_ecb_takes_newest_day_of_a_multi_day_file():
    xml = ECB_XML.replace(
        b"\t</Cube>\n</gesmes",
        b"\t\t<Cube time='2026-10-06'><Cube currency='USD' rate='1.1600'/></Cube>\n\t</Cube>\n"
        b"</gesmes",
    )
    day, rates = parse_ecb_daily(xml)
    assert day == date(2026, 10, 7) and rates["USD"] == Decimal("1.1652")


@pytest.mark.parametrize(
    "bad",
    [
        b"<?xml version='1.0'?><Envelope><Cube><Cube/></Cube></Envelope>",
        b"<?xml version='1.0'?><!DOCTYPE x [<!ENTITY a 'b'>]><Envelope/>",
        b"<html><body>Maintenance</body></html>",
    ],
)
def test_parse_ecb_rejects_malformed(bad):
    with pytest.raises(ValueError):
        parse_ecb_daily(bad)


async def test_fetch_ecb(router):
    route = router.get(ECB_DAILY_URL).mock(return_value=httpx.Response(200, content=ECB_XML))
    async with httpx.AsyncClient() as client:
        day, rates = await fetch_ecb(client, headers={"User-Agent": "haggler-test"})
    assert day == date(2026, 10, 7) and rates["USD"] == Decimal("1.1652")
    assert route.calls.last.request.headers["user-agent"] == "haggler-test"


# ------------------------------------------------------------------ Frankfurter


def test_parse_openapi_examples():
    rates = parse_rates(OPENAPI_RATES_EXAMPLE)
    assert set(rates) == {"GBP", "USD"}  # identity record dropped
    assert rates["USD"].rate == Decimal("1.089") and rates["USD"].date == date(2024, 1, 15)
    single = parse_rates(OPENAPI_RATE_EXAMPLE)
    assert single["USD"].rate == Decimal("1.1568") and single["USD"].date == date(2026, 3, 25)
    import json

    ndjson = parse_rates([json.loads(OPENAPI_NDJSON_LINE)])
    assert ndjson["USD"].rate == Decimal("1.089")


def test_parse_rates_tolerates_v1_shape_and_skips_junk():
    v1 = {"amount": 1.0, "base": "EUR", "date": "2026-10-07", "rates": {"USD": 1.165, "SEK": 0}}
    assert set(parse_rates(v1)) == {"USD"}  # zero rate rejected
    junk = [
        {"date": "2026-10-07", "base": "USD", "quote": "COP", "rate": 3900},  # other base
        {"date": "not-a-date", "base": "EUR", "quote": "SEK", "rate": 11},
        {"date": "2026-10-07", "base": "EUR", "quote": "NOK", "rate": "abc"},
        "garbage",
    ]
    assert parse_rates(junk) == {}
    with pytest.raises(ValueError):
        parse_rates({"status": 404, "message": "not found"})


def test_parse_rates_keeps_newest_record_per_quote():
    recs = [
        {"date": "2026-10-06", "base": "EUR", "quote": "USD", "rate": 1.16},
        {"date": "2026-10-07", "base": "EUR", "quote": "USD", "rate": 1.17},
    ]
    assert parse_rates(recs)["USD"].rate == Decimal("1.17")


async def test_fetch_rates_query_and_decimal_precision(router):
    fake = default_frankfurter()
    router.get(FF_RATES).mock(side_effect=fake)
    async with httpx.AsyncClient() as client:
        got = await fetch_rates(client, ["ARS", "eur"], providers="BCRA")
        usd = await fetch_rates(client, {"COP"}, base="USD", providers=["banrep"])
        none = await fetch_rates(client, ["EUR"])
    assert fake.requests == [
        {"base": "EUR", "quotes": "ARS", "providers": "BCRA"},
        {"base": "USD", "quotes": "COP", "providers": "BANREP"},
    ]
    assert got["ARS"].rate == Decimal("1620.5")
    assert usd["COP"].base == "USD" and usd["COP"].date == date(2026, 10, 8)
    assert none == {}  # nothing to ask for: no request made


async def test_fetch_rates_404_is_empty_and_422_raises(router):
    route = router.get(FF_RATES)
    async with httpx.AsyncClient() as client:
        route.mock(return_value=httpx.Response(404, json={"status": 404, "message": "not found"}))
        assert await fetch_rates(client, ["COP"], providers="BANREP") == {}
        route.mock(return_value=httpx.Response(422, json=UNPROCESSABLE))
        with pytest.raises(httpx.HTTPStatusError):
            await fetch_rates(client, ["XXX"])


# ------------------------------------------------------------------ open.er-api


def test_parse_open_er_api():
    day, rates = parse_latest(OPEN_ER_PAYLOAD)
    assert day == date(2026, 10, 8)
    assert "EUR" not in rates and rates["PKR"] == Decimal("328.1203")


def test_parse_open_er_api_rebases_and_rejects_errors():
    usd_based = {**OPEN_ER_PAYLOAD, "base_code": "USD", "rates": {"USD": 1, "EUR": 0.8, "SEK": 8.8}}
    _, rates = parse_latest(usd_based)
    assert rates["USD"] == Decimal("1.25") and rates["SEK"] == Decimal("11")
    with pytest.raises(ValueError, match="unsupported-code"):
        parse_latest({"result": "error", "error-type": "unsupported-code"})
    with pytest.raises(ValueError):
        parse_latest({**OPEN_ER_PAYLOAD, "time_last_update_unix": None})


# ------------------------------------------------------------------ business days


@pytest.mark.parametrize(
    ("start", "end", "expected"),
    [
        (date(2026, 10, 1), date(2026, 10, 8), 5),  # Thu -> Thu
        (date(2026, 9, 30), date(2026, 10, 8), 6),  # Wed -> Thu
        (date(2026, 10, 2), date(2026, 10, 5), 1),  # Fri -> Mon
        (date(2026, 10, 3), date(2026, 10, 4), 0),  # Sat -> Sun
        (date(2026, 10, 8), date(2026, 10, 8), 0),
        (date(2026, 10, 9), date(2026, 10, 8), 0),  # future rate (replays)
        (date(2026, 9, 8), date(2026, 10, 8), 22),
    ],
)
def test_business_days_between(start, end, expected):
    assert business_days_between(start, end) == expected


# ------------------------------------------------------------------ DefaultFxService


WANTED = {"EUR", "SEK", "USD", "COP", "ARS", "PEN", "PKR"}


async def build(router, *, previous=None, currencies=WANTED, pins=PINS, **kwargs) -> tuple:
    async with httpx.AsyncClient() as client:
        svc = DefaultFxService(client, pins, **kwargs)
        table = await svc.build_table(currencies, as_of=AS_OF, previous=previous)
    return svc, table


def summary(table: FxTable) -> dict[str, tuple[str, str | None, bool]]:
    return {c: (r.source, r.provider, r.stale) for c, r in table.rates.items()}


async def test_chain_happy_path(router):
    fake = default_frankfurter()
    router.get(ECB_DAILY_URL).mock(return_value=httpx.Response(200, content=ECB_XML))
    router.get(FF_RATES).mock(side_effect=fake)
    open_er = router.get(OPEN_ER).mock(return_value=httpx.Response(200, json=OPEN_ER_PAYLOAD))

    svc, table = await build(router, user_agent="haggler-test/1")

    assert table.base == "EUR" and table.as_of == AS_OF
    assert summary(table) == {
        "ARS": ("frankfurter", "BCRA", False),  # pinned, EUR-based record
        "COP": ("frankfurter", "BANREP", False),  # pinned, USD record bridged with ECB USD
        "PEN": ("frankfurter", None, False),  # blended
        "PKR": ("open_er_api", None, False),  # nobody else has it
        "SEK": ("ecb", "ECB", False),
        "USD": ("ecb", "ECB", False),
    }
    assert "EUR" not in table.rates
    assert table.rates["ARS"].per_eur == Decimal("1620.5")
    assert table.rates["COP"].per_eur == Decimal("3912.45") * Decimal("1.1652")
    assert table.rates["COP"].as_of == date(2026, 10, 7)  # older of TRM and bridge dates
    assert table.rates["SEK"].per_eur == Decimal("10.9875")
    assert table.rates["PKR"].as_of == date(2026, 10, 8)
    assert svc.errors == []
    assert open_er.call_count == 1
    # Pinned EUR attempt, USD retry for BANREP only, then one blended call for what is left.
    assert fake.requests == [
        {"base": "EUR", "quotes": "COP", "providers": "BANREP"},
        {"base": "USD", "quotes": "COP", "providers": "BANREP"},
        {"base": "EUR", "quotes": "ARS", "providers": "BCRA"},
        {"base": "EUR", "quotes": "PEN,PKR"},
    ]
    assert all(c.request.headers["user-agent"] == "haggler-test/1" for c in router.calls)


async def test_ecb_down_falls_back_to_frankfurter_blend(router):
    router.get(ECB_DAILY_URL).mock(return_value=httpx.Response(503, text="<html>busy</html>"))
    router.get(FF_RATES).mock(side_effect=default_frankfurter())
    router.get(OPEN_ER).mock(return_value=httpx.Response(200, json=OPEN_ER_PAYLOAD))

    svc, table = await build(router)

    assert summary(table)["SEK"] == ("frankfurter", None, False)
    assert summary(table)["USD"] == ("frankfurter", None, False)
    # BANREP bridge used Frankfurter's blended USD instead of the ECB one.
    assert table.rates["COP"].per_eur == Decimal("3912.45") * Decimal("1.1650")
    assert table.rates["COP"].provider == "BANREP"
    assert any("ecb" in e and "HTTPStatusError" in e for e in svc.errors)


async def test_ecb_and_frankfurter_down_fall_back_to_open_er_api(router):
    router.get(ECB_DAILY_URL).mock(side_effect=httpx.ConnectTimeout("timeout"))
    router.get(FF_RATES).mock(return_value=httpx.Response(200, text="<html>not json</html>"))
    router.get(OPEN_ER).mock(return_value=httpx.Response(200, json=OPEN_ER_PAYLOAD))

    svc, table = await build(router)

    assert {r.source for r in table.rates.values()} == {"open_er_api"}
    assert set(table.rates) == WANTED - {"EUR"}
    assert table.rates["COP"].per_eur == Decimal("4561.2817")
    assert table.rates["COP"].provider is None
    assert svc.errors  # each failure recorded, none raised


async def test_everything_down_uses_previous_marked_stale(router):
    router.get(ECB_DAILY_URL).mock(return_value=httpx.Response(500))
    router.get(FF_RATES).mock(side_effect=httpx.ConnectError("refused"))
    router.get(OPEN_ER).mock(return_value=httpx.Response(200, json={"result": "error"}))
    previous = FxTable(
        as_of=date(2026, 10, 7),
        rates={
            "SEK": FxRate(
                currency="SEK", per_eur=Decimal("10.98"), source="ecb", as_of=date(2026, 10, 6)
            ),
            "COP": FxRate(
                currency="COP",
                per_eur=Decimal("4550.1"),
                source="frankfurter",
                provider="BANREP",
                as_of=date(2026, 10, 6),
            ),
        },
    )

    svc, table = await build(router, previous=previous, currencies={"SEK", "COP", "PEN"})

    assert summary(table) == {
        "COP": ("previous", "BANREP", True),
        "SEK": ("previous", None, True),
    }
    assert table.rates["SEK"].per_eur == Decimal("10.98")
    assert table.rates["SEK"].as_of == date(2026, 10, 6)  # original date kept
    assert any("PEN" in e for e in svc.errors)


async def test_no_previous_and_no_sources_returns_empty_table(router):
    router.get(ECB_DAILY_URL).mock(return_value=httpx.Response(500))
    router.get(FF_RATES).mock(return_value=httpx.Response(500))
    router.get(OPEN_ER).mock(return_value=httpx.Response(500))
    svc, table = await build(router, previous=None, currencies={"SEK"})
    assert table.rates == {} and table.as_of == AS_OF
    assert any("no rate at all for SEK" in e for e in svc.errors)


async def test_stale_pinned_rate_yields_to_a_fresh_one(router):
    fake = default_frankfurter()
    fake.providers[("BCRA", "EUR")] = {"ARS": (1500.0, "2026-09-21")}  # BCRA feed stopped
    router.get(ECB_DAILY_URL).mock(return_value=httpx.Response(200, content=ECB_XML))
    router.get(FF_RATES).mock(side_effect=fake)
    router.get(OPEN_ER).mock(return_value=httpx.Response(200, json=OPEN_ER_PAYLOAD))

    _, table = await build(router, currencies={"ARS"})

    assert summary(table)["ARS"] == ("frankfurter", None, False)
    assert table.rates["ARS"].per_eur == Decimal("1619.75")


async def test_when_every_source_is_old_the_newest_stale_rate_wins(router):
    fake = FakeFrankfurter(
        blended={"ARS": (1590.0, "2026-09-25")},
        providers={("BCRA", "EUR"): {"ARS": (1500.0, "2026-09-21")}},
    )
    router.get(ECB_DAILY_URL).mock(return_value=httpx.Response(200, content=ECB_XML))
    router.get(FF_RATES).mock(side_effect=fake)
    router.get(OPEN_ER).mock(return_value=httpx.Response(500))

    _, table = await build(router, currencies={"ARS"})

    rate = table.rates["ARS"]
    assert (rate.source, rate.provider, rate.stale) == ("frankfurter", None, True)
    assert rate.as_of == date(2026, 9, 25)


async def test_ecb_friday_rates_are_fresh_on_monday(router):
    friday = ECB_XML.replace(b"2026-10-07", b"2026-10-02")
    router.get(ECB_DAILY_URL).mock(return_value=httpx.Response(200, content=friday))
    async with httpx.AsyncClient() as client:
        table = await DefaultFxService(client, {}).build_table(
            {"SEK"}, as_of=date(2026, 10, 5), previous=None
        )
    assert summary(table) == {"SEK": ("ecb", "ECB", False)}


async def test_unknown_pinned_provider_error_falls_through(router):
    def unknown_provider(request: httpx.Request) -> httpx.Response:
        if "providers" in request.url.params:
            return httpx.Response(404, json={"status": 404, "message": "not found"})
        return default_frankfurter()(request)

    router.get(ECB_DAILY_URL).mock(return_value=httpx.Response(200, content=ECB_XML))
    router.get(FF_RATES).mock(side_effect=unknown_provider)

    _, table = await build(router, currencies={"COP"}, pins={"COP": "NOPE"})

    assert summary(table) == {"COP": ("frankfurter", None, False)}


async def test_static_service_returns_its_table():
    table = FxTable(as_of=AS_OF)
    assert await StaticFxService(table).build_table({"SEK"}, as_of=AS_OF, previous=None) is table

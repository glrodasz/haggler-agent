from decimal import Decimal

from haggler.fetch import RawResponse
from haggler.sources.amazon_prime import AmazonPrimeAdapter
from haggler.sources.registry import get_adapter

ADAPTER = AmazonPrimeAdapter()

# Lines as the probe quoted them from the real pages (2026-10-27).
US = """
<p>Under $10</p>
<p>Prime is only $14.99/month (plus tax). Cancel anytime.</p>
<p>Free Same-Day Delivery on $25+ grocery orders in selected areas.</p>
<p>Enjoy your free 30-day trial of Prime. After, Prime is just $14.99 per month. Cancel anytime.</p>
<p>Enjoy your free 30-day trial of Prime. After, Prime is just $139 per year. Cancel anytime.</p>
<p>Enjoy a 6-month trial of Prime for Young Adults. Then get Prime at half the price, just
$14.99 $7.49 per month. Cancel anytime.</p>
<p>Prime is just $14.99 $6.99/month for eligible government assistance recipients.</p>
"""
ES = """
<p>Only €4.99/month after free trial. Ongoing subscription until cancelled</p>
<p>Enjoy your free 30-days trial of Prime. After, Prime is just €4.99 per month. Cancel anytime.</p>
<p>Enjoy your free 30-days trial of Prime. After, Prime is just €49.90 per year. Cancel anytime.</p>
<p>90 days trial courtesy of Microsoft Surface, for new members only. After your trial, Prime
Student is just €2.49/month.</p>
"""
PL = """
<p>Ciesz się darmowym okresem próbnym Prime. Po nim Prime kosztuje 15,50 zł za miesiąc.</p>
<p>Ciesz się darmowym okresem próbnym Prime. Po nim Prime kosztuje 69 zł za rok.</p>
"""
BR = """
<p>Aproveite 30 dias GRÁTIS de Amazon Prime. Após esse período, o Amazon Prime custará R$ 19,90
por mês. Cancele a qualquer momento.</p>
<p>Aproveite 30 dias GRÁTIS de Amazon Prime. Após esse período, o Amazon Prime custará R$ 166,80
por ano. Cancele a qualquer momento.</p>
"""
TR = "<p>İlk 30 günden sonra Prime sadece 69,90₺/ay. İstediğin zaman iptal edebilirsin.</p>"


def _task(catalog, cc):
    svc = catalog.service("prime_video")
    src = next(s for s in svc.sources if s.adapter == ADAPTER.id)
    return next(t for t in ADAPTER.plan(svc, src, catalog) if t.countries == [cc])


def _parse(catalog, cc, text, *, final_url=None):
    task = _task(catalog, cc)
    resp = RawResponse(url=task.url, final_url=final_url or task.url, status_code=200, text=text)
    return ADAPTER.parse(task, resp, catalog)


def _prices(result):
    return {o.billing_period: (o.amount, o.currency) for o in result.observations}


def test_registered():
    assert isinstance(get_adapter("amazon_prime"), AmazonPrimeAdapter)


def test_plans_one_task_per_amazon_store(catalog):
    svc = catalog.service("prime_video")
    src = svc.sources[0]
    tasks = {t.countries[0]: t for t in ADAPTER.plan(svc, src, catalog)}
    assert "CO" not in tasks
    assert tasks["DE"].url == "https://www.amazon.de/amazonprime"
    assert tasks["GB"].host == "www.amazon.co.uk"
    assert tasks["TR"].url == "https://www.amazon.com.tr/prime"


def test_regular_prices_skip_discounted_memberships(catalog):
    result = _parse(catalog, "US", US)
    assert result.status == "ok"
    assert _prices(result) == {
        "month": (Decimal("14.99"), "USD"),
        "year": (Decimal("139"), "USD"),
    }
    assert {o.plan_id for o in result.observations} == {"prime"}


def test_student_line_is_ignored(catalog):
    assert _prices(_parse(catalog, "ES", ES)) == {
        "month": (Decimal("4.99"), "EUR"),
        "year": (Decimal("49.90"), "EUR"),
    }


def test_local_language_pages(catalog):
    assert _prices(_parse(catalog, "PL", PL)) == {
        "month": (Decimal("15.50"), "PLN"),
        "year": (Decimal("69"), "PLN"),
    }
    assert _prices(_parse(catalog, "BR", BR)) == {
        "month": (Decimal("19.90"), "BRL"),
        "year": (Decimal("166.80"), "BRL"),
    }
    assert _prices(_parse(catalog, "TR", TR)) == {"month": (Decimal("69.90"), "TRY")}


def test_redirect_to_another_store_is_wrong_country(catalog):
    result = _parse(catalog, "SE", ES, final_url="https://www.amazon.com/amazonprime")
    assert result.status == "wrong_country"
    assert not result.observations


def test_same_store_redirect_is_fine(catalog):
    result = _parse(catalog, "PL", PL, final_url="https://www.amazon.pl:443/prime")
    assert result.status == "ok"


def test_no_price_sentence(catalog):
    result = _parse(catalog, "DE", "<p>Amazon Prime</p><p>Watch movies</p>" * 50)
    assert result.status == "no_prices"

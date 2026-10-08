from haggler.catalog import (
    integrity_errors,
    load_bundles,
    load_catalog,
    load_feasibility,
    load_payment_instruments,
)


def test_catalog_and_curated_files_are_consistent():
    cat = load_catalog()
    errors = integrity_errors(cat, load_feasibility(), load_bundles(), load_payment_instruments())
    assert errors == []


def test_catalog_shape():
    cat = load_catalog()
    assert len(cat.countries) == 35
    priced = [s for s in cat.services if s.sources]
    assert len(priced) == 11
    assert {"SE", "LT", "CO", "US"} <= set(cat.country_codes())


def test_country_lookup_by_vendor_spelling():
    cat = load_catalog()
    assert cat.country_by_name("Türkiye").cc == "TR"
    assert cat.country_by_name("Viet Nam").cc == "VN"
    assert cat.country_by_name("The Netherlands").cc == "NL"
    assert cat.country_by_name("Atlantis") is None


def test_expected_currency_uses_appstore_override():
    cat = load_catalog()
    ar = cat.country("AR")
    assert ar.expected_currencies("ios_iap") == ["USD"]
    assert ar.expected_currencies("web") == ["ARS"]

from haggler.catalog import (
    integrity_errors,
    load_bundles,
    load_catalog,
    load_feasibility,
    load_payment_instruments,
    load_profiles,
    load_review,
)
from haggler.sources.base import match_iap_plan, match_plan


def test_catalog_and_curated_files_are_consistent():
    cat = load_catalog()
    errors = integrity_errors(
        cat,
        load_feasibility(),
        load_bundles(),
        load_payment_instruments(),
        load_profiles(),
        load_review(),
    )
    assert errors == []


def test_every_researchable_subject_has_official_domains():
    cat = load_catalog()
    assert all(s.official_domains for s in cat.services)
    held = {i for p in load_profiles().profiles for i in p.instruments_held}
    for inst in load_payment_instruments().instruments:
        # Instruments the user already holds are not researched.
        assert inst.official_domains or inst.id in held, inst.id


def test_catalog_shape():
    cat = load_catalog()
    assert len(cat.countries) == 67
    priced = [s for s in cat.services if s.sources]
    assert len(priced) == 11
    assert {"SE", "LT", "CO", "US"} <= set(cat.country_codes())
    # Dollarized markets accept their own currency and the one services bill in there.
    assert cat.country("BO").expected_currencies("web") == ["BOB", "USD"]
    assert cat.country("EC").expected_currencies("web") == ["USD"]


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


def test_sources_reflect_the_2026_10_probe():
    cat = load_catalog()
    # disneyplus.com/robots.txt disallows us: Disney+ comes from the App Store listing.
    disney = cat.service("disney_plus")
    assert [(s.adapter, s.channel, s.params["app_id"]) for s in disney.sources] == [
        ("appstore_iap", "ios_iap", 1446075923)
    ]
    assert "robots.txt" in disney.notes_es
    # Netflix bills Romania in EUR, on both Netflix sources.
    for src in cat.service("netflix").sources:
        assert src.params["currency_overrides"] == {"RO": "EUR"}
    # Spotify's UK market path.
    [spotify] = cat.service("spotify").sources
    assert "{market}" in spotify.params["url"] and spotify.params["path_overrides"] == {"GB": "uk"}


def test_localized_plan_names():
    cat = load_catalog()
    for service_id in ("apple_one", "apple_music"):
        svc = cat.service(service_id)
        assert match_plan(svc, "Personligt") == "individual"  # apple.com/se
        assert match_plan(svc, "Personlig") == "individual"
        assert match_plan(svc, "Det personliga abonnemanget") is None
        assert match_plan(svc, "Familj") == "family"
    yt = cat.service("youtube_premium")
    for name in ("YouTube Premium Aile", "Premium Rodzinny", "Premium Családi", "Premium Perhe"):
        assert match_iap_plan(yt, name) == ["family"], name
    assert match_iap_plan(yt, "YouTube Premium") == ["individual"]
    assert match_iap_plan(cat.service("google_one"), "Google AI Premium") == ["ai_pro"]
    disney = cat.service("disney_plus")
    for name, plan in [
        ("Disney+ Standard med reklam", "standard_with_ads"),
        ("Disney+ Estándar con anuncios", "standard_with_ads"),
        ("Disney+ Padrão com anúncios", "standard_with_ads"),
        ("Disney+ Standard mit Werbung", "standard_with_ads"),
        ("Disney+ Standard z reklamami", "standard_with_ads"),
        ("Disney+ Standard", "standard"),
        ("Disney+ Estándar mensual", "standard"),
        ("Disney+ Premium anual", "premium"),
    ]:
        assert match_iap_plan(disney, name) == [plan], name

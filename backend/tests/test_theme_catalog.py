"""TOP402 정적 테마 catalog 불변식."""
from app.theme_filter.catalog import filter_new_entry_symbols, get_theme_catalog
from app.universe.default_universe import FALLBACK_MARKET_CAP_TOP402


def test_catalog_covers_top402_exactly():
    catalog = get_theme_catalog()
    assert set(catalog.symbols) == set(FALLBACK_MARKET_CAP_TOP402)
    assert len(catalog.symbols) == 402


def test_core_manual_multi_theme_mappings():
    catalog = get_theme_catalog()
    assert "semiconductor" in catalog.themes_for("005930")
    assert "semiconductor" in catalog.themes_for("000660")
    assert {"energy_chemical", "secondary_battery"} <= set(catalog.themes_for("051910"))


def test_filter_is_any_tag_match_and_unknown_fail_open():
    out = filter_new_entry_symbols(
        ["005930", "051910", "005380", "999999"],
        {"semiconductor", "secondary_battery"},
    )
    assert out == ["005380", "999999"]

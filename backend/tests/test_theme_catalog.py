"""TOP402 정적 테마 catalog 불변식."""
from collections import Counter

from app.theme_filter.catalog import filter_new_entry_symbols, get_theme_catalog
from app.universe.default_universe import FALLBACK_MARKET_CAP_TOP402


EXPECTED_TOP300_COUNTS = {
    "semiconductor": 43,
    "bio": 34,
    "finance": 32,
    "secondary_battery": 24,
    "energy_chemical": 22,
    "retail_consumer": 21,
    "power_nuclear": 19,
    "internet": 17,
    "defense": 15,
    "automobile": 14,
    "construction_infra": 13,
    "shipbuilding": 12,
    "food_beverage": 10,
    "steel_materials": 10,
    "transport_logistics": 10,
    "entertainment_media": 8,
    "cosmetics": 8,
    "robot_ai": 8,
    "telecom_network": 6,
    "gaming": 6,
    "other": 5,
}


def test_catalog_covers_top402_exactly():
    catalog = get_theme_catalog()
    assert set(catalog.symbols) == set(FALLBACK_MARKET_CAP_TOP402)
    assert len(catalog.symbols) == 402
    assert catalog.taxonomy_version == "kr-theme-v2"


def test_top300_taxonomy_counts_are_locked():
    catalog = get_theme_catalog()
    top300 = FALLBACK_MARKET_CAP_TOP402[:300]
    counts = Counter(
        theme_id
        for symbol in top300
        for theme_id in catalog.themes_for(symbol)
    )
    assert counts == EXPECTED_TOP300_COUNTS
    assert sum(len(catalog.themes_for(symbol)) > 1 for symbol in top300) == 35
    assert [
        symbol for symbol in top300 if catalog.themes_for(symbol) == ("other",)
    ] == ["003550", "034730", "000150", "004990", "030530"]
    assert len([theme for theme in catalog.themes if theme.id != "other"]) == 20


def test_core_manual_multi_theme_mappings():
    catalog = get_theme_catalog()
    assert "semiconductor" in catalog.themes_for("005930")
    assert "semiconductor" in catalog.themes_for("000660")
    assert {"energy_chemical", "secondary_battery"} <= set(catalog.themes_for("051910"))
    assert "defense" in catalog.themes_for("012450")
    assert "secondary_battery" in catalog.themes_for("082920")
    assert "bio" not in catalog.themes_for("082920")
    assert "semiconductor" in catalog.themes_for("166090")
    assert "bio" not in catalog.themes_for("166090")
    assert catalog.symbols["402340"].name == "SK스퀘어"


def test_filter_is_any_tag_match_and_unknown_fail_open():
    out = filter_new_entry_symbols(
        ["005930", "051910", "005380", "999999"],
        {"semiconductor", "secondary_battery"},
    )
    assert out == ["005380", "999999"]


def test_multi_tag_symbol_is_blocked_when_any_one_theme_is_off():
    catalog = get_theme_catalog()
    assert {"energy_chemical", "secondary_battery"} <= set(catalog.themes_for("051910"))
    assert filter_new_entry_symbols(["051910"], {"secondary_battery"}, catalog=catalog) == []
    assert filter_new_entry_symbols(["051910"], {"energy_chemical"}, catalog=catalog) == []
    assert filter_new_entry_symbols(["051910"], set(), catalog=catalog) == ["051910"]

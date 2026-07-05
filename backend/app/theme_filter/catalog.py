"""TOP402 정적 테마 catalog.

동적 ThemeSignal과 분리된 운영 SSOT다. 이 모듈은 주문/리스크 계층을 import하지
않고 종목 목록만 좁힌다.
"""
from __future__ import annotations

import json
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import Iterable

from app.universe.default_universe import FALLBACK_MARKET_CAP_TOP402


@dataclass(frozen=True)
class ThemeDefinition:
    id: str
    label: str
    order: int


@dataclass(frozen=True)
class SymbolTheme:
    symbol: str
    name: str
    primary_theme: str
    themes: tuple[str, ...]
    krx_industry: str
    source: str


@dataclass(frozen=True)
class ThemeCatalog:
    taxonomy_version: str
    themes: tuple[ThemeDefinition, ...]
    symbols: dict[str, SymbolTheme]

    @property
    def theme_ids(self) -> frozenset[str]:
        return frozenset(t.id for t in self.themes)

    def themes_for(self, symbol: str) -> tuple[str, ...]:
        item = self.symbols.get(str(symbol))
        return item.themes if item else ()

    def mapped_count(self, theme_id: str) -> int:
        return sum(theme_id in item.themes for item in self.symbols.values())


def _catalog_path() -> Path:
    return Path(__file__).resolve().parents[3] / "data" / "market" / "theme_catalog.json"


@lru_cache(maxsize=1)
def get_theme_catalog() -> ThemeCatalog:
    raw = json.loads(_catalog_path().read_text(encoding="utf-8"))
    definitions = tuple(
        ThemeDefinition(id=str(v["id"]), label=str(v["label"]), order=int(v["order"]))
        for v in raw.get("themes", [])
    )
    theme_ids = {t.id for t in definitions}
    symbols = {
        str(code): SymbolTheme(
            symbol=str(code),
            name=str(v.get("name") or code),
            primary_theme=str(v["primary_theme"]),
            themes=tuple(str(t) for t in v.get("themes", [])),
            krx_industry=str(v.get("krx_industry") or ""),
            source=str(v.get("source") or ""),
        )
        for code, v in raw.get("symbols", {}).items()
    }

    expected = set(FALLBACK_MARKET_CAP_TOP402)
    actual = set(symbols)
    if actual != expected:
        raise ValueError(
            f"theme catalog must cover TOP402 exactly: missing={sorted(expected-actual)}, "
            f"extra={sorted(actual-expected)}"
        )
    if len(definitions) != len(theme_ids):
        raise ValueError("theme catalog has duplicate theme ids")
    for item in symbols.values():
        if not item.themes or item.primary_theme not in item.themes:
            raise ValueError(f"invalid theme mapping: {item.symbol}")
        if set(item.themes) - theme_ids:
            raise ValueError(f"unknown theme id in mapping: {item.symbol}")
    return ThemeCatalog(
        taxonomy_version=str(raw.get("taxonomy_version") or ""),
        themes=tuple(sorted(definitions, key=lambda t: (t.order, t.id))),
        symbols=symbols,
    )


def filter_new_entry_symbols(
    symbols: Iterable[str],
    disabled_theme_ids: Iterable[str],
    *,
    catalog: ThemeCatalog | None = None,
) -> list[str]:
    """OFF 테마 종목만 신규 후보에서 제거. 미분류 종목은 fail-open."""
    cat = catalog or get_theme_catalog()
    disabled = frozenset(str(v) for v in disabled_theme_ids)
    if not disabled:
        return [str(s) for s in symbols]
    return [
        str(symbol)
        for symbol in symbols
        if not (set(cat.themes_for(str(symbol))) & disabled)
    ]

"""기본 Universe(fallback) 해결 — 단위 + 정적 가드 테스트.

검증 항목 (사용자 요청서 §5):
- watchlist 비어 있으면 FALLBACK_MARKET_CAP_TOP50 (정확히 50종목)
- user_symbols 가 있으면 USER_DEFINED (fallback 미사용)
- UniverseResolution invariant (is_order_signal / is_investment_advice = False)
- 정적 import 가드 (broker / OrderExecutor / route_order / 외부 HTTP 0건)
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

from app.universe.default_universe import (
    FALLBACK_MARKET_CAP_TOP50,
    UniverseResolution,
    UniverseSource,
    get_default_universe,
)


_MODULE_PATH = (
    Path(__file__).resolve().parents[1] / "app" / "universe" / "default_universe.py"
)


def test_fallback_universe_is_exactly_50_unique():
    assert len(FALLBACK_MARKET_CAP_TOP50) == 50
    assert len(set(FALLBACK_MARKET_CAP_TOP50)) == 50
    # 모두 비어있지 않은 종목 코드.
    assert all(isinstance(c, str) and c.strip() for c in FALLBACK_MARKET_CAP_TOP50)


def test_empty_watchlist_uses_fallback_50():
    r = get_default_universe(user_symbols=None)
    assert r.source == UniverseSource.FALLBACK_MARKET_CAP_TOP50
    assert r.count == 50
    assert r.fallback_used is True
    assert "투자 추천" in r.warning_ko or "추천" in r.warning_ko
    assert len(r.symbols) == 50


def test_empty_list_also_uses_fallback():
    r = get_default_universe(user_symbols=[])
    assert r.source == UniverseSource.FALLBACK_MARKET_CAP_TOP50
    assert r.count == 50


def test_user_symbols_take_priority_over_fallback():
    r = get_default_universe(user_symbols=["005930", "000660"])
    assert r.source == UniverseSource.USER_DEFINED
    assert r.count == 2
    assert r.fallback_used is False
    assert r.symbols == ("005930", "000660")
    assert r.warning_ko == ""


def test_user_symbols_dedup_and_strip():
    r = get_default_universe(user_symbols=[" 005930 ", "005930", "", None, "000660"])
    assert r.symbols == ("005930", "000660")
    assert r.count == 2


def test_resolution_invariants_locked():
    r = get_default_universe(None)
    assert r.is_order_signal is False
    assert r.is_investment_advice is False
    d = r.to_dict()
    assert d["is_order_signal"] is False
    assert d["is_investment_advice"] is False
    assert d["count"] == len(d["symbols"])


def test_resolution_rejects_order_signal_true():
    with pytest.raises(ValueError):
        UniverseResolution(
            source=UniverseSource.USER_DEFINED, symbols=("005930",), count=1,
            fallback_used=False, warning_ko="", is_order_signal=True,
        )


def test_resolution_rejects_count_mismatch():
    with pytest.raises(ValueError):
        UniverseResolution(
            source=UniverseSource.USER_DEFINED, symbols=("005930", "000660"),
            count=1, fallback_used=False, warning_ko="",
        )


def test_static_no_forbidden_imports():
    """broker / OrderExecutor / route_order / 외부 HTTP / AI SDK import 0건."""
    src = _MODULE_PATH.read_text(encoding="utf-8")
    tree = ast.parse(src)
    forbidden = (
        "app.brokers", "app.execution.executor", "app.execution.order_router",
        "kis_client", "mock_broker", "anthropic", "openai", "httpx", "requests",
    )
    imported: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported += [a.name for a in node.names]
        elif isinstance(node, ast.ImportFrom):
            imported.append(node.module or "")
    for mod in imported:
        for bad in forbidden:
            assert bad not in (mod or ""), f"forbidden import: {mod}"
    # 실제 *호출/사용* 패턴 0건 (docstring 의 설명 언급은 허용).
    assert "route_order(" not in src
    assert ".place_order(" not in src
    assert "OrderExecutor(" not in src

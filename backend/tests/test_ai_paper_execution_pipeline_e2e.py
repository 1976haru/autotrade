"""AI Paper 자동매매 실행 파이프라인 — E2E + reason_code 검증.

사용자 요청서 §8 핵심: 강제 진단 run-once 가 universe → 시세 → 전략 → sizing →
현금 → 권한 → 가상 후보 전 단계를 통과하며, 어느 단계에서 멈추든 *정확한
reason_code* 를 남기고 ledger 에 기록한다. broker / route_order / OrderExecutor
호출 0건.

검증:
- 정상 종목 75,000원 → quantity=13, notional=975,000 (PAPER_DRY_RUN_OK /
  VIRTUAL_ORDER_CANDIDATE_CREATED)
- 고가주 → MIN_LOT_NOT_AFFORDABLE
- 현금 부족 → INSUFFICIENT_PAPER_CASH
- permission 차단 → BLOCKED_BY_PERMISSION_GATE / PAPER_EXECUTION_DISABLED
- 시세 없음 → NO_MARKET_DATA, stale → PRICE_STALE, 0 → INVALID_PRICE,
  급등락 → ABNORMAL_PRICE_MOVE
- 신호 없음 → NO_STRATEGY_SIGNAL, 엔진 미연동 → STRATEGY_ENGINE_NOT_CONNECTED
- broker / live 호출 0회 (invariant flag + 정적 grep)
- 시장 세션 checker: 08:59 / 09:00 / 09:05 / 15:20 / 15:31 / 주말
- API endpoint: run-readiness / run-once-diagnostic
- "기록 0건 불가" — run-once 가 ledger 에 기록
"""

from __future__ import annotations

import ast
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from app.auto_paper.ledger import get_ledger, reset_ledger_for_tests
from app.auto_paper.run_once import (
    RunOnceResultCode,
    run_paper_pipeline_once,
)
from app.scheduler.market_clock import MarketPhase, current_market_phase


_RUN_ONCE_PATH = (
    Path(__file__).resolve().parents[1] / "app" / "auto_paper" / "run_once.py"
)


@pytest.fixture(autouse=True)
def _clean_ledger():
    reset_ledger_for_tests()
    yield
    reset_ledger_for_tests()


# ────────────────────────────────────────────────────────────────────────────
# 정상 흐름 — 75,000원 → quantity 13
# ────────────────────────────────────────────────────────────────────────────


def test_normal_dry_run_quantity_13():
    r = run_paper_pipeline_once(
        symbol="005930", price=75_000, dry_run=True,
        per_symbol_cap_krw=1_000_000, available_cash_krw=10_000_000,
    )
    assert r.result_code == RunOnceResultCode.PAPER_DRY_RUN_OK
    assert r.ok is True
    assert r.quantity == 13
    assert r.notional_krw == 975_000


def test_normal_not_dry_run_creates_virtual_candidate():
    r = run_paper_pipeline_once(
        symbol="005930", price=75_000, dry_run=False,
        per_symbol_cap_krw=1_000_000, available_cash_krw=10_000_000,
    )
    assert r.result_code == RunOnceResultCode.VIRTUAL_ORDER_CANDIDATE_CREATED
    assert r.ok is True
    assert r.quantity == 13
    assert r.notional_krw == 975_000
    assert r.metadata["virtual_order_candidate"]["quantity"] == 13


def test_force_mock_generates_price_and_passes():
    r = run_paper_pipeline_once(
        symbol="005930", force_mock_market_data=True, dry_run=True,
        per_symbol_cap_krw=1_000_000, available_cash_krw=10_000_000,
    )
    assert r.market_data_is_mock is True
    assert r.price is not None and r.price > 0
    assert r.result_code == RunOnceResultCode.PAPER_DRY_RUN_OK


# ────────────────────────────────────────────────────────────────────────────
# 차단 흐름 — 각 단계 reason_code
# ────────────────────────────────────────────────────────────────────────────


def test_high_price_min_lot_not_affordable():
    r = run_paper_pipeline_once(
        symbol="005930", price=2_000_000,
        per_symbol_cap_krw=1_000_000, available_cash_krw=10_000_000,
    )
    assert r.result_code == RunOnceResultCode.MIN_LOT_NOT_AFFORDABLE
    assert r.ok is False
    assert r.quantity == 0


def test_insufficient_paper_cash():
    r = run_paper_pipeline_once(
        symbol="005930", price=75_000,
        per_symbol_cap_krw=1_000_000, available_cash_krw=100_000,
    )
    assert r.result_code == RunOnceResultCode.INSUFFICIENT_PAPER_CASH
    assert r.ok is False
    # 수량은 계산되었으나(13) 현금 부족으로 차단.
    assert r.quantity == 13
    assert r.metadata["shortfall_krw"] > 0


def test_permission_gate_block():
    r = run_paper_pipeline_once(
        symbol="005930", price=75_000,
        per_symbol_cap_krw=1_000_000, available_cash_krw=10_000_000,
        force_block_permission=True,
    )
    assert r.result_code == RunOnceResultCode.BLOCKED_BY_PERMISSION_GATE
    assert r.ok is False


def test_paper_execution_disabled():
    r = run_paper_pipeline_once(
        symbol="005930", price=75_000,
        per_symbol_cap_krw=1_000_000, available_cash_krw=10_000_000,
        paper_virtual_execution_enabled=False,
    )
    assert r.result_code == RunOnceResultCode.PAPER_EXECUTION_DISABLED
    assert r.ok is False


def test_no_market_data_when_not_forced():
    r = run_paper_pipeline_once(
        symbol="005930", price=None, force_mock_market_data=False,
    )
    assert r.result_code == RunOnceResultCode.NO_MARKET_DATA
    assert r.ok is False


def test_invalid_price():
    r = run_paper_pipeline_once(symbol="005930", price=0)
    assert r.result_code == RunOnceResultCode.INVALID_PRICE
    assert r.ok is False


def test_price_stale_with_old_timestamp():
    old = (datetime.now(timezone.utc) - timedelta(seconds=600)).isoformat()
    r = run_paper_pipeline_once(
        symbol="005930", price=75_000, price_timestamp=old,
        per_symbol_cap_krw=1_000_000, available_cash_krw=10_000_000,
    )
    assert r.result_code == RunOnceResultCode.PRICE_STALE
    assert r.ok is False


def test_abnormal_price_move():
    r = run_paper_pipeline_once(
        symbol="005930", price=75_000, reference_price=50_000,
        per_symbol_cap_krw=1_000_000, available_cash_krw=10_000_000,
    )
    assert r.result_code == RunOnceResultCode.ABNORMAL_PRICE_MOVE
    assert r.ok is False


def test_no_strategy_signal():
    r = run_paper_pipeline_once(symbol="005930", price=75_000, signal_present=False)
    assert r.result_code == RunOnceResultCode.NO_STRATEGY_SIGNAL
    assert r.ok is False


def test_strategy_engine_not_connected():
    r = run_paper_pipeline_once(
        symbol="005930", price=75_000, strategy_engine_connected=False,
    )
    assert r.result_code == RunOnceResultCode.STRATEGY_ENGINE_NOT_CONNECTED
    assert r.ok is False


def test_no_candidate_when_no_symbol_and_user_symbols_empty():
    # symbol=None + user_symbols 없음 → fallback universe 가 후보를 채우므로
    # NO_CANDIDATE 가 아니라 정상 진행 (fallback 50 의 첫 종목 사용).
    r = run_paper_pipeline_once(
        symbol=None, force_mock_market_data=True, dry_run=True,
        per_symbol_cap_krw=1_000_000, available_cash_krw=10_000_000,
    )
    assert r.universe_count == 50
    assert r.symbol is not None
    assert r.result_code == RunOnceResultCode.PAPER_DRY_RUN_OK


# ────────────────────────────────────────────────────────────────────────────
# 기록 — "거래 0건은 가능, 기록 0건은 불가"
# ────────────────────────────────────────────────────────────────────────────


def test_run_once_always_records_to_ledger():
    reset_ledger_for_tests()
    # 차단 케이스라도 ledger 에 NO_OP heartbeat 기록.
    r = run_paper_pipeline_once(
        symbol="005930", price=2_000_000, per_symbol_cap_krw=1_000_000,
        available_cash_krw=10_000_000, record=True,
    )
    assert r.recorded_event_id is not None
    events = get_ledger().recent(limit=10)
    assert len(events) >= 1
    last = events[-1]
    # 기록은 NO_OP (거래성 아님) + result_code carry.
    assert last.decision_action.value == "NO_OP"
    assert last.metadata.get("result_code") == RunOnceResultCode.MIN_LOT_NOT_AFFORDABLE.value
    assert last.metadata.get("broker_order_sent") is False


def test_run_once_record_false_skips_ledger():
    reset_ledger_for_tests()
    run_paper_pipeline_once(
        symbol="005930", price=75_000, per_symbol_cap_krw=1_000_000,
        available_cash_krw=10_000_000, record=False,
    )
    assert len(get_ledger().recent(limit=10)) == 0


# ────────────────────────────────────────────────────────────────────────────
# broker / live 호출 0회 — invariant flags + 정적 grep
# ────────────────────────────────────────────────────────────────────────────


def test_result_invariants_no_live_no_broker():
    for kwargs in (
        dict(symbol="005930", price=75_000, per_symbol_cap_krw=1_000_000,
             available_cash_krw=10_000_000),
        dict(symbol="005930", price=2_000_000, per_symbol_cap_krw=1_000_000),
        dict(symbol="005930", price=None, force_mock_market_data=False),
    ):
        r = run_paper_pipeline_once(**kwargs)
        assert r.is_order_signal is False
        assert r.is_live_authorization is False
        assert r.auto_apply_allowed is False
        assert r.broker_order_sent is False
        d = r.to_dict()
        assert d["broker_order_sent"] is False
        assert d["is_live_authorization"] is False


def test_static_run_once_has_no_broker_or_route_order_imports():
    src = _RUN_ONCE_PATH.read_text(encoding="utf-8")
    tree = ast.parse(src)
    imported: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported += [a.name for a in node.names]
        elif isinstance(node, ast.ImportFrom):
            imported.append(node.module or "")
    forbidden = (
        "app.brokers", "app.execution.executor", "app.execution.order_router",
        "kis_client", "anthropic", "openai", "httpx", "requests",
    )
    for mod in imported:
        for bad in forbidden:
            assert bad not in (mod or ""), f"forbidden import: {mod}"
    # 호출 흔적도 0건 (docstring 제외 — 실제 호출 패턴).
    assert "broker.place_order(" not in src
    assert "route_order(" not in src
    assert ".place_order(" not in src


# ────────────────────────────────────────────────────────────────────────────
# 시장 세션 checker — 08:59 / 09:00 / 09:05 / 15:20 / 15:31 / 주말
# ────────────────────────────────────────────────────────────────────────────


def _kst(y, m, d, hh, mm):
    """KST 시각을 UTC datetime 으로 변환 (KST = UTC+9)."""
    kst = timezone(timedelta(hours=9))
    return datetime(y, m, d, hh, mm, tzinfo=kst).astimezone(timezone.utc)


def test_market_phase_0859_pre_open():
    # 2026-05-22 는 금요일 (평일).
    assert current_market_phase(_kst(2026, 5, 22, 8, 59)) == MarketPhase.PRE_OPEN


def test_market_phase_0900_open():
    assert current_market_phase(_kst(2026, 5, 22, 9, 0)) == MarketPhase.OPEN


def test_market_phase_0905_open():
    assert current_market_phase(_kst(2026, 5, 22, 9, 5)) == MarketPhase.OPEN


def test_market_phase_1520_open():
    assert current_market_phase(_kst(2026, 5, 22, 15, 20)) == MarketPhase.OPEN


def test_market_phase_1531_closed():
    assert current_market_phase(_kst(2026, 5, 22, 15, 31)) == MarketPhase.CLOSED


def test_market_phase_weekend():
    # 2026-05-23 은 토요일.
    assert current_market_phase(_kst(2026, 5, 23, 10, 0)) == MarketPhase.WEEKEND
    # 2026-05-24 은 일요일.
    assert current_market_phase(_kst(2026, 5, 24, 10, 0)) == MarketPhase.WEEKEND


# ────────────────────────────────────────────────────────────────────────────
# API endpoint — run-readiness / run-once-diagnostic
# ────────────────────────────────────────────────────────────────────────────


def test_api_run_readiness(client):
    res = client.get("/api/auto-paper/run-readiness")
    assert res.status_code == 200
    j = res.json()
    assert j["universe"]["count"] == 50
    assert j["universe"]["source"] == "FALLBACK_MARKET_CAP_TOP50"
    assert j["permission"]["live_execution_blocked"] is True
    assert j["permission"]["paper_virtual_execution_allowed"] is True
    assert j["can_run_once_diagnostic"] is True
    assert j["is_live_authorization"] is False
    assert "phase" in j["market_session"]
    assert "health_code" in j["loop"]


def test_api_run_once_diagnostic_normal(client):
    res = client.post("/api/auto-paper/run-once-diagnostic", json={
        "symbol": "005930", "price": 75_000,
        "per_symbol_cap_krw": 1_000_000, "available_cash_krw": 10_000_000,
        "dry_run": True,
    })
    assert res.status_code == 200
    j = res.json()
    assert j["result_code"] == "PAPER_DRY_RUN_OK"
    assert j["quantity"] == 13
    assert j["notional_krw"] == 975_000
    assert j["broker_order_sent"] is False
    assert j["is_live_authorization"] is False


def test_api_run_once_diagnostic_high_price(client):
    res = client.post("/api/auto-paper/run-once-diagnostic", json={
        "symbol": "005930", "price": 2_000_000,
        "per_symbol_cap_krw": 1_000_000, "available_cash_krw": 10_000_000,
    })
    assert res.status_code == 200
    assert res.json()["result_code"] == "MIN_LOT_NOT_AFFORDABLE"


def test_api_run_once_diagnostic_permission_block(client):
    res = client.post("/api/auto-paper/run-once-diagnostic", json={
        "symbol": "005930", "price": 75_000,
        "per_symbol_cap_krw": 1_000_000, "available_cash_krw": 10_000_000,
        "paper_virtual_execution_enabled": False,
    })
    assert res.status_code == 200
    assert res.json()["result_code"] == "PAPER_EXECUTION_DISABLED"


def test_api_run_once_diagnostic_default_body(client):
    # body 없이도 동작 (force_mock 기본 True).
    res = client.post("/api/auto-paper/run-once-diagnostic", json={})
    assert res.status_code == 200
    j = res.json()
    assert j["result_code"] in (
        "PAPER_DRY_RUN_OK", "VIRTUAL_ORDER_CANDIDATE_CREATED",
    )
    assert j["broker_order_sent"] is False

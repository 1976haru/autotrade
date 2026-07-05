"""PART1-4: SELL 보유 가드 + net 보유 계산 회귀 테스트.

2026-06-01 첫 실전 모의 버그:
  - held_symbols 가 "오늘 BUY 한 종목" 전부를 잡고 SELL 청산을 빼지 않아,
    청산된 005380 이 계속 보유로 잡혀 SELL 신호가 broker 로 전송 → KIS
    "잔고부족" 거부 3건.

본 테스트가 보장:
  1. _today_kis_paper_buy_state 가 net(BUY−SELL) 보유만 반환 (REJECTED 제외).
  2. SELL 신호인데 net 보유 0 이면 broker 로 가지 않고 SELL_NO_HELD_POSITION skip.
"""

from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

import app.kis_paper.driver_bridge as _bridge
from app.agents.agent_council import StrategyMarketInput
from app.db.base import Base
from app.db.models import OrderAuditLog
from app.kis_paper.driver_bridge import (
    _kis_held_map,
    _kis_held_symbols,
    _today_kis_paper_buy_state,
    kis_paper_realtime_scan_tick,
)
from app.market_data.kis_realtime import KIS_PRICE_OK, KisRealtimeQuote
from app.risk.risk_manager import RiskDecision

OPEN_TIME = datetime(2026, 5, 27, 5, 0, 0, tzinfo=timezone.utc)  # 14:00 KST Wed


@pytest.fixture(autouse=True)
def _reset_held_snapshot():
    # R-A: 보유스냅샷은 모듈 전역 — 테스트 간 누수 방지로 매 테스트 초기화.
    _bridge._HELD_SNAPSHOT = {}
    _bridge._HELD_SNAPSHOT_AT = None
    yield
    _bridge._HELD_SNAPSHOT = {}
    _bridge._HELD_SNAPSHOT_AT = None


@pytest.fixture
def engine():
    eng = create_engine("sqlite://", connect_args={"check_same_thread": False},
                        poolclass=StaticPool)
    Base.metadata.create_all(eng)
    return eng


def _add_order(db, *, symbol, side, qty, broker_status="RECEIVED", price=10000):
    db.add(OrderAuditLog(
        created_at=OPEN_TIME, mode="PAPER", requested_by_ai=False,
        symbol=symbol, side=side, quantity=qty, order_type="MARKET",
        decision="APPROVED", executed=True, broker_order_id="X",
        broker_status=broker_status, filled_quantity=0,
        limit_price=price, latest_price=price,
        trade_reason="kis_paper_auto", strategy="VWAP",
    ))


# ───────────────────────── net holding 계산 ─────────────────────────

class _FakePosBroker:
    def __init__(self, positions=None, raise_error=None):
        self._positions = positions or []
        self._raise = raise_error

    async def get_positions(self):
        if self._raise is not None:
            raise self._raise
        return self._positions


def test_kis_held_symbols_from_broker_positions():
    # 보유 = KIS 잔고(브로커 진실). 전일 캐리오버(DB 무관)도 잡는다. qty<=0 제외.
    broker = _FakePosBroker([
        SimpleNamespace(symbol="071050", quantity=1),   # orphan/캐리오버
        SimpleNamespace(symbol="005935", quantity=4),
        SimpleNamespace(symbol="000270", quantity=0),    # 0 → 제외
    ])
    held = asyncio.run(_kis_held_symbols(broker, fallback=set()))
    assert held == {"071050", "005935"}


def test_kis_held_symbols_trusts_empty_kis():
    # 조회 성공 + 빈 결과 → 실제 무보유로 신뢰 (fallback 무시).
    assert asyncio.run(_kis_held_symbols(_FakePosBroker([]), fallback={"005930"})) == set()


def test_kis_held_symbols_fallback_on_failure():
    # 조회 실패 → 보수적으로 fallback(오늘 DB net) 사용.
    broker = _FakePosBroker(raise_error=RuntimeError("no creds"))
    assert asyncio.run(_kis_held_symbols(broker, fallback={"005930"})) == {"005930"}


def test_kis_held_symbols_no_broker_uses_fallback():
    assert asyncio.run(_kis_held_symbols(None, fallback={"x"})) == {"x"}


def test_net_holding_excludes_fully_sold_symbol(engine):
    Session = sessionmaker(bind=engine)
    db = Session()
    # 005380: BUY 1 → SELL 1 (전량 청산) → net 0 → held 에 없어야.
    _add_order(db, symbol="005380", side="BUY", qty=1)
    _add_order(db, symbol="005380", side="SELL", qty=1)
    # 035420: BUY 4, SELL 없음 → net 4 → held.
    _add_order(db, symbol="035420", side="BUY", qty=4)
    db.commit()
    held, used = _today_kis_paper_buy_state(db, OPEN_TIME)
    assert "005380" not in held       # 청산됨 → 보유 아님
    assert "035420" in held           # 보유 중


def test_net_holding_ignores_rejected_sell(engine):
    Session = sessionmaker(bind=engine)
    db = Session()
    # BUY 5 정상, SELL 5 는 REJECTED(잔고부족 시뮬) → net 은 여전히 5 (보유).
    _add_order(db, symbol="000270", side="BUY", qty=5)
    _add_order(db, symbol="000270", side="SELL", qty=5, broker_status="REJECTED")
    db.commit()
    held, _ = _today_kis_paper_buy_state(db, OPEN_TIME)
    assert "000270" in held           # REJECTED SELL 은 보유를 줄이지 않음


def test_net_holding_partial_sell_still_held(engine):
    Session = sessionmaker(bind=engine)
    db = Session()
    _add_order(db, symbol="068270", side="BUY", qty=5)
    _add_order(db, symbol="068270", side="SELL", qty=2)   # 일부 청산 → net 3
    db.commit()
    held, _ = _today_kis_paper_buy_state(db, OPEN_TIME)
    assert "068270" in held


# ───────────────────────── SELL 가드 (보유 0 차단) ─────────────────────────

def _sell_input(symbol):
    # 보유 청산 신호를 유도하는 약세 input (council 이 SELL 쪽으로).
    return StrategyMarketInput(
        symbol=symbol, current_price=9200.0, prev_close=10000.0, open_price=9900.0,
        vwap=9800.0, opening_range_high=10100.0, opening_range_low=9500.0,
        recent_closes=(10000.0, 9800.0, 9600.0, 9400.0, 9200.0),
        current_volume=200.0, avg_volume=100.0,
        market_regime="TREND_DOWN", regime_decision="ALLOW",
    )


async def _sell_input_fn(symbol, *, client, now, market_is_open, **kw):
    return _sell_input(symbol), KisRealtimeQuote(
        symbol=symbol, status=KIS_PRICE_OK, price=9200.0, is_stale=False)


def _route_should_not_be_called():
    async def _fn(**kw):
        raise AssertionError("route_order must NOT be called for SELL with no holding")
    return _fn


def _scan_settings(**kw):
    base = dict(
        market_data_provider="kis", enable_kis_paper_auto_trading=True,
        kis_paper_auto_order_dry_run=False, kis_is_paper=True, enable_live_trading=False,
        kis_paper_auto_max_order_notional=1_000_000, kis_paper_auto_max_orders_per_day=10,
        kis_paper_auto_order_window_start="09:05", kis_paper_auto_order_window_end="14:50",
        kis_paper_auto_min_confidence=0.6, kis_paper_auto_min_quality_score=60,
        kis_paper_smoke_mode=False, kis_paper_smoke_symbol="005930", kis_paper_smoke_qty=1,
        kis_paper_max_concurrent_positions=5, kis_paper_per_symbol_notional_krw=1_000_000,
        kis_paper_daily_buy_limit_krw=3_000_000, kis_paper_max_new_positions_per_tick=1,
        kis_paper_scan_max_symbols=10,
        kis_app_key="FAKE-KEY", kis_app_secret="FAKE-SECRET", kis_account_no="00000000",
        kis_product_code="01",
    )
    base.update(kw)
    return SimpleNamespace(**base)


def test_sell_with_no_holding_is_skipped_not_sent(engine):
    Session = sessionmaker(bind=engine)
    # KIS 잔고 비어있음(get_positions=[]) → held_symbols 빈 set → 어떤 SELL 도 보유 0.
    out = asyncio.run(kis_paper_realtime_scan_tick(
        session_factory=Session, broker=_FakePosBroker([]), risk=object(),
        route_order_fn=_route_should_not_be_called(), settings=_scan_settings(),
        market_input_fn=_sell_input_fn, universe_symbols=["005380"],
        client=object(), now=OPEN_TIME,
    ))
    # SELL 신호가 났더라도 보유 0 이면 broker 로 가지 않는다.
    assert out["orders_attempted"] == 0
    assert out["broker_order_sent"] is False
    codes = {s["reason_code"] for s in out["skipped"]}
    # SELL 후보였다면 SELL_NO_HELD_POSITION, council 이 HOLD 였다면 HOLD_NO_SIGNAL.
    assert codes <= {"SELL_NO_HELD_POSITION", "HOLD_NO_SIGNAL"}


def test_carryover_kis_holding_not_blocked_as_no_holding(engine):
    # ★캐리오버 회귀(2026-06-05): 오늘 DB 주문 0건이어도 KIS 잔고에 005935 보유
    #   → held 로 인식 → SELL 이 'SELL_NO_HELD_POSITION' 으로 막히지 않는다(청산 가능).
    Session = sessionmaker(bind=engine)

    async def _permissive_route(**kw):
        return SimpleNamespace(
            decision=RiskDecision.APPROVED, reasons=[],
            audit=SimpleNamespace(id=1, broker_order_id="ODNO-1",
                                  broker_status="FILLED", filled_quantity=4, executed=True))

    broker = _FakePosBroker([SimpleNamespace(symbol="005935", quantity=4)])
    out = asyncio.run(kis_paper_realtime_scan_tick(
        session_factory=Session, broker=broker, risk=object(),
        route_order_fn=_permissive_route, settings=_scan_settings(),
        market_input_fn=_sell_input_fn, universe_symbols=["005935"],
        client=object(), now=OPEN_TIME,
    ))
    codes = {s.get("reason_code") for s in out["skipped"]}
    assert "SELL_NO_HELD_POSITION" not in codes  # KIS 보유 → '보유없음'으로 안 막힘.


# ── S1: _kis_held_map — 보유 *수량* 보존 (SELL 수량 캡용) ──────────────────────

def test_held_map_preserves_hldg_and_ord_psbl():
    # sellable_quantity(ord_psbl) 가 hldg 보다 작으면 그대로 보존(미결제분).
    broker = _FakePosBroker([
        SimpleNamespace(symbol="005935", quantity=4, sellable_quantity=4),
        SimpleNamespace(symbol="005380", quantity=10, sellable_quantity=3),  # 7주 미결제
        SimpleNamespace(symbol="000270", quantity=0, sellable_quantity=0),   # 0 → 제외
    ])
    m = asyncio.run(_kis_held_map(broker, fallback=set()))
    assert m["005935"]["hldg"] == 4 and m["005935"]["ord_psbl"] == 4
    assert m["005380"]["hldg"] == 10 and m["005380"]["ord_psbl"] == 3   # 주문가능 3 만 보존
    assert "000270" not in m


def test_held_map_ord_psbl_none_defaults_to_hldg():
    # sellable_quantity 미상(None) → hldg 로 간주(비-KIS broker 호환).
    broker = _FakePosBroker([SimpleNamespace(symbol="005930", quantity=3, sellable_quantity=None)])
    m = asyncio.run(_kis_held_map(broker, fallback=set()))
    assert m["005930"]["hldg"] == 3 and m["005930"]["ord_psbl"] == 3


def test_held_map_fallback_on_failure_has_no_quantities():
    # 조회 실패 + *스냅샷 없음* → fallback 심볼만, 수량 미상(빈 dict) → SELL qty 0 → skip.
    broker = _FakePosBroker(raise_error=RuntimeError("kis down"))
    m = asyncio.run(_kis_held_map(broker, fallback={"005930"}))
    assert m == {"005930": {}}
    # 빈 info → SELL qty = min(0,0) = 0 → SELL_NOT_ORDERABLE skip (헛주문 방지).
    info = m["005930"]
    assert min(int(info.get("hldg", 0) or 0), int(info.get("ord_psbl", 0) or 0)) == 0


# ── R-A(2026-06-12): 보유스냅샷 fallback — 레이트리밋(EGW00201) 순간에도 청산 유지 ──

def test_held_map_fallback_uses_fresh_snapshot_quantities():
    # 직전 *성공* 조회로 스냅샷 적재 → 다음 조회 *실패* 시 수량까지 살려 청산 가능.
    ok = _FakePosBroker([SimpleNamespace(symbol="035420", quantity=12, sellable_quantity=12)])
    m1 = asyncio.run(_kis_held_map(ok, fallback=set(), now=OPEN_TIME))
    assert m1["035420"]["hldg"] == 12 and m1["035420"]["ord_psbl"] == 12
    # EGW00201 시뮬: 조회 실패 + 스냅샷 신선(30초 후) → 보유 수량 보존
    down = _FakePosBroker(raise_error=RuntimeError("EGW00201"))
    m2 = asyncio.run(_kis_held_map(down, fallback={"035420"}, now=OPEN_TIME + timedelta(seconds=30)))
    assert m2["035420"]["hldg"] == 12 and m2["035420"]["ord_psbl"] == 12     # ★수량 살아있음
    assert min(m2["035420"]["hldg"], m2["035420"]["ord_psbl"]) == 12   # SELL_NOT_ORDERABLE 아님


def test_held_map_fallback_ignores_stale_snapshot():
    # 스냅샷이 TTL 초과(오래됨)면 수량 fallback 안 함 → 종전 보수적(빈 dict).
    ok = _FakePosBroker([SimpleNamespace(symbol="035420", quantity=12, sellable_quantity=12)])
    asyncio.run(_kis_held_map(ok, fallback=set(), now=OPEN_TIME))
    down = _FakePosBroker(raise_error=RuntimeError("EGW00201"))
    stale = OPEN_TIME + timedelta(seconds=_bridge._HELD_SNAPSHOT_TTL_SECONDS + 1)
    m = asyncio.run(_kis_held_map(down, fallback={"035420"}, now=stale))
    assert m == {"035420": {}}     # 오래된 스냅샷 → 수량 미상(보수적)


def test_held_map_success_empty_does_not_fabricate_holdings():
    # 조회 성공+빈 결과(실제 무보유)는 스냅샷을 비워, 이후 실패 시 보유를 날조하지 않음.
    asyncio.run(_kis_held_map(
        _FakePosBroker([SimpleNamespace(symbol="035420", quantity=12)]), fallback=set(), now=OPEN_TIME))
    asyncio.run(_kis_held_map(_FakePosBroker([]), fallback=set(), now=OPEN_TIME))  # 무보유 성공
    m = asyncio.run(_kis_held_map(
        _FakePosBroker(raise_error=RuntimeError("x")), fallback=set(), now=OPEN_TIME))
    assert m == {}     # 빈 스냅샷 → 수량 fallback 없음


def test_scan_recognizes_held_when_positions_fetch_fails(engine):
    """R-A E2E: 직전 틱 성공으로 스냅샷 적재 → 이번 틱 get_positions 실패해도 035420 을
    *보유*로 계속 인식 → 청산 시 SELL_NO_HELD_POSITION/SELL_NOT_ORDERABLE 로 끊기지 않는다.
    (council 의 SELL 강제 여부는 판단부 — 본 테스트는 '보유 인식·수량 유지'만 가드.)"""
    Session = sessionmaker(bind=engine)

    async def _route(**kw):
        return SimpleNamespace(
            decision=RiskDecision.APPROVED, reasons=[],
            audit=SimpleNamespace(id=1, broker_order_id="ODNO", broker_status="FILLED",
                                  filled_quantity=12, executed=True))

    # 1) 성공 틱: 035420 보유 스냅샷 적재 (fake route → DB 주문 안 쌓임 → DB fallback 비어있음)
    ok = _FakePosBroker([SimpleNamespace(symbol="035420", quantity=12, sellable_quantity=12)])
    asyncio.run(kis_paper_realtime_scan_tick(
        session_factory=Session, broker=ok, risk=object(), route_order_fn=_route,
        settings=_scan_settings(), market_input_fn=_sell_input_fn,
        universe_symbols=["035420"], client=object(), now=OPEN_TIME))

    # 2) 실패 틱: get_positions raise(EGW00201) → 스냅샷 fallback 으로 보유/수량 유지
    down = _FakePosBroker(raise_error=RuntimeError("EGW00201"))
    out = asyncio.run(kis_paper_realtime_scan_tick(
        session_factory=Session, broker=down, risk=object(), route_order_fn=_route,
        settings=_scan_settings(), market_input_fn=_sell_input_fn,
        universe_symbols=["035420"], client=object(), now=OPEN_TIME + timedelta(seconds=30)))

    codes = {s.get("reason_code") for s in out["skipped"]}
    # ★핵심: get_positions 실패에도 '보유없음/주문불가'로 청산이 차단되지 않는다.
    assert "SELL_NO_HELD_POSITION" not in codes
    assert "SELL_NOT_ORDERABLE" not in codes


# ── S2: SELL 주문수량이 보유 기준으로 캡되는지 (스캔 → route_order spy) ──────────

def test_sell_skipped_when_orderable_qty_zero(engine):
    """주문가능수량 0(미결제 전량) → 헛주문 대신 SELL_NOT_ORDERABLE skip."""
    Session = sessionmaker(bind=engine)

    async def _route_must_not_call(**kw):
        raise AssertionError("ord_psbl=0 이면 route_order 호출 0건이어야 함")

    broker = _FakePosBroker([SimpleNamespace(symbol="005935", quantity=4, sellable_quantity=0)])
    out = asyncio.run(kis_paper_realtime_scan_tick(
        session_factory=Session, broker=broker, risk=object(),
        route_order_fn=_route_must_not_call, settings=_scan_settings(),
        market_input_fn=_sell_input_fn, universe_symbols=["005935"],
        client=object(), now=OPEN_TIME,
        _bot_owned_override=frozenset({"005935"}),
    ))
    codes = {s.get("reason_code") for s in out["skipped"]}
    assert "SELL_NOT_ORDERABLE" in codes or "HOLD_NO_SIGNAL" in codes
    assert out["broker_order_sent"] is False


# ── ③: 일일 주문 횟수 카운트는 BUY 만 (SELL 이 BUY 예산 소진 방지) ──────────────

def test_order_count_buy_only_excludes_sell(engine):
    """★카운트 버그 수정: SELL(실패 포함)은 일일 *횟수* 한도 카운트에서 제외.

    예전엔 BUY+SELL 전부 세어, 실패 SELL 250건이 카운트를 부풀려 BUY 횟수한도(10)를
    소진→BUY 전량 차단(2026-06-09). 이제 BUY 만 센다(A수정 정신과 정합).
    """
    from app.kis_paper.auto_executor import _today_kis_paper_order_count
    Session = sessionmaker(bind=engine)
    db = Session()
    _add_order(db, symbol="005930", side="BUY", qty=1)
    _add_order(db, symbol="000270", side="BUY", qty=1)
    _add_order(db, symbol="005935", side="SELL", qty=4)                          # SELL → 제외
    _add_order(db, symbol="005935", side="SELL", qty=4, broker_status="REJECTED")  # 실패 SELL → 제외
    db.commit()
    n = _today_kis_paper_order_count(db, OPEN_TIME)
    assert n == 2   # BUY 2건만 (SELL 2건은 횟수 예산 소진 안 함)


# ── T1(2026-06-12): 회전 스캔 — 풀 100 을 틱당 슬라이스로 회전 커버 ──────────────

def test_scan_universe_rotates_offset_window():
    import app.kis_paper.driver_bridge as b
    from types import SimpleNamespace
    b.reset_scan_rotation_for_tests()
    st = SimpleNamespace(kis_paper_smoke_mode=False, kis_paper_scan_max_symbols=10)
    w1 = b._scan_universe_symbols(st, override=None)
    w2 = b._scan_universe_symbols(st, override=None)
    assert len(w1) == 10 and len(w2) == 10
    assert w1 != w2                      # 다음 틱은 다른 슬라이스(회전)
    assert set(w1).isdisjoint(w2)        # 10+10 겹침 0 (오프셋 +10)
    b.reset_scan_rotation_for_tests()


def test_scan_rotation_covers_full_pool_in_ten_ticks():
    import app.kis_paper.driver_bridge as b
    from types import SimpleNamespace
    b.reset_scan_rotation_for_tests()
    st = SimpleNamespace(kis_paper_smoke_mode=False, kis_paper_scan_max_symbols=10)
    seen = set()
    for _ in range(10):                  # 100/10 = 10 틱
        seen.update(b._scan_universe_symbols(st, override=None))
    assert len(seen) == 100              # 10 틱에 100 전체 커버
    b.reset_scan_rotation_for_tests()


def test_theme_off_removes_semiconductor_from_new_entry_scan(monkeypatch):
    """dry-run (a): 반도체 OFF면 미보유 반도체는 신규 스캔 후보에서 빠진다."""
    import app.core.runtime_config as rc
    import app.kis_paper.driver_bridge as b

    monkeypatch.setattr(rc, "effective_disabled_theme_ids", lambda now=None: {"semiconductor"})
    st = _scan_settings()
    out = b._scan_universe_symbols(
        st, override=["005930", "000660", "005380"], now=OPEN_TIME
    )
    assert out == ["005380"]


def test_theme_on_restores_semiconductor_to_new_entry_scan(monkeypatch):
    """dry-run (c): 반도체 ON 복귀 시 다시 신규 스캔 후보가 된다."""
    import app.core.runtime_config as rc
    import app.kis_paper.driver_bridge as b

    monkeypatch.setattr(rc, "effective_disabled_theme_ids", lambda now=None: set())
    st = _scan_settings()
    out = b._scan_universe_symbols(
        st, override=["005930", "000660", "005380"], now=OPEN_TIME
    )
    assert out == ["005930", "000660", "005380"]


def test_held_symbols_always_scanned_despite_rotation(engine):
    """T1: 회전 윈도우에 없는 보유 종목도 매 틱 스캔 목록에 union(청산 보장)."""
    import app.kis_paper.driver_bridge as b
    b.reset_scan_rotation_for_tests()
    Session = sessionmaker(bind=engine)
    scanned: list[str] = []

    async def _spy_input_fn(symbol, *, client, now, market_is_open, **kw):
        scanned.append(symbol)
        return None, KisRealtimeQuote(symbol=symbol, status="X", price=0.0, is_stale=False)

    # 035420 보유(회전 첫 윈도우 앞 10 안에 없음 — NAVER 는 풀 index 9 라 윈도우0 에 포함될 수
    #   있어, 오프셋을 풀 끝으로 돌려 윈도우에서 확실히 제외).
    b._scan_rotation_offset = 50
    broker = _FakePosBroker([SimpleNamespace(symbol="035420", quantity=12, sellable_quantity=12)])
    asyncio.run(kis_paper_realtime_scan_tick(
        session_factory=Session, broker=broker, risk=object(),
        route_order_fn=_route_should_not_be_called(), settings=_scan_settings(),
        market_input_fn=_spy_input_fn, client=object(), now=OPEN_TIME,
    ))
    assert "035420" in scanned           # 보유는 회전과 무관하게 항상 스캔됨
    b.reset_scan_rotation_for_tests()


# ── S1/S2(2026-06-15): 익절/손절 강제청산 = position 전달로 활성화 ──────────────
#   ★역방향 안전(밴드 내 정상 보유는 안 팔림)이 최대 위험 — 가장 빡빡하게 검증.

def _neutral_quote_fn(price):
    # 전략 투표가 전부 중립(HOLD)이 되도록 평평한 input — 강제청산만 SELL 을 유발.
    async def _fn(symbol, *, client, now, market_is_open, **kw):
        mi = StrategyMarketInput(
            symbol=symbol, current_price=float(price), prev_close=float(price),
            open_price=float(price), vwap=float(price),
            opening_range_high=float(price) * 1.002, opening_range_low=float(price) * 0.998,
            recent_closes=(float(price),) * 5, current_volume=100.0, avg_volume=100.0,
            market_regime="SIDEWAYS", regime_decision="ALLOW")
        return mi, KisRealtimeQuote(symbol=symbol, status=KIS_PRICE_OK, price=float(price), is_stale=False)
    return _fn


def _route_capture():
    routed = []
    async def _fn(**kw):
        routed.append(kw)
        return SimpleNamespace(decision=RiskDecision.APPROVED, reasons=[],
            audit=SimpleNamespace(id=1, broker_order_id="ODNO", broker_status="FILLED",
                                  filled_quantity=10, executed=True))
    return _fn, routed


def _set_thresholds(monkeypatch, *, tp=3.5, sl=2.0):
    import app.core.runtime_config as rc
    monkeypatch.setattr(rc, "effective_take_profit_pct", lambda: tp)
    monkeypatch.setattr(rc, "effective_stop_loss_pct", lambda: sl)


# db8fd55 게이트가 *면제해야* 하는 품질/확신 차단 코드(이 코드로 막히면 면제 실패).
_QUALITY_BLOCKS = {"KIS_PAPER_LOW_QUALITY_SCORE", "KIS_PAPER_LOW_CONFIDENCE",
                   "LOW_QUALITY_SCORE", "LOW_CONFIDENCE"}


def _scan(engine, broker, price):
    # ★FakeBroker 는 KIS paper 어댑터가 아니라 PaperTrader/게이트 broker 검증에서 멈춘다.
    #   그래서 *게이트 결과(reason_code)* 로 검증: 위험청산 SELL 이 품질/확신에 안 막히고
    #   (db8fd55 면제) broker-type 체크(KIS_PAPER_MODE_REQUIRED)에서만 멈추면 = 라이브선 통과.
    Session = sessionmaker(bind=engine)
    route, routed = _route_capture()
    out = asyncio.run(kis_paper_realtime_scan_tick(
        session_factory=Session, broker=broker, risk=object(), route_order_fn=route,
        settings=_scan_settings(), market_input_fn=_neutral_quote_fn(price),
        universe_symbols=["005930"], client=object(), now=OPEN_TIME,
        _bot_owned_override=frozenset({"005930"})))
    return out, routed


def _held(avg):
    return _FakePosBroker([SimpleNamespace(symbol="005930", quantity=10, sellable_quantity=10, avg_price=avg)])


def test_take_profit_forced_exit_generated_and_gate_exempt(engine, monkeypatch):
    # 보유 avg 100,000, 현재가 104,000 = +4% > 익절 +3.5% → 강제 SELL 생성 + 품질게이트 면제.
    _set_thresholds(monkeypatch, tp=3.5, sl=2.0)
    out, _ = _scan(engine, _held(100000), 104000)
    assert out["candidates_found"] == 1, "익절선 초과인데 강제청산 SELL 후보가 안 생김"
    o = out["orders"][0]
    # ★db8fd55: 위험청산 SELL 이 저품질(conf 0.3/qual 30)이어도 품질/확신에 안 막힘.
    assert o["reason_code"] not in _QUALITY_BLOCKS, f"db8fd55 면제 실패 — {o['reason_code']}"
    assert o["reason_code"] == "KIS_PAPER_MODE_REQUIRED"   # broker-type 체크만(라이브선 통과)


def test_stop_loss_forced_exit_generated_and_gate_exempt(engine, monkeypatch):
    # 보유 avg 100,000, 현재가 97,000 = -3% < 손절 -2% → 강제 SELL_STOP_LOSS + 면제.
    _set_thresholds(monkeypatch, tp=3.5, sl=2.0)
    out, _ = _scan(engine, _held(100000), 97000)
    assert out["candidates_found"] == 1, "손절선 하회인데 강제청산 SELL 후보가 안 생김"
    o = out["orders"][0]
    assert o["reason_code"] not in _QUALITY_BLOCKS
    assert o["reason_code"] == "KIS_PAPER_MODE_REQUIRED"


def test_theme_off_held_semiconductor_still_stop_loss_exits(engine, monkeypatch):
    """dry-run (b): OFF 필터 뒤 held union으로 삼성전자 -2% 손절 평가가 유지된다."""
    import app.core.runtime_config as rc

    monkeypatch.setattr(rc, "effective_disabled_theme_ids", lambda now=None: {"semiconductor"})
    _set_thresholds(monkeypatch, tp=3.5, sl=2.0)
    out, _ = _scan(engine, _held(100000), 97000)
    assert out["symbols_scanned"] == 1
    assert out["candidates_found"] == 1
    assert out["orders"][0]["reason_code"] == "KIS_PAPER_MODE_REQUIRED"
    assert out["orders"][0]["reason_code"] not in _QUALITY_BLOCKS


def test_within_bands_not_force_sold(engine, monkeypatch):
    # ★역방향 안전(최대 위험): avg 100,000, 현재가 101,000 = +1% (익절 +3.5%/손절 -2% 사이)
    #   → 강제청산 트리거 없음 + 전략 중립 → SELL 후보 0 (멀쩡한 보유를 잘못 팔지 않는다).
    _set_thresholds(monkeypatch, tp=3.5, sl=2.0)
    out, routed = _scan(engine, _held(100000), 101000)
    assert out["candidates_found"] == 0, "밴드 내 정상 보유가 잘못 청산 후보가 됨(역방향 안전 위반)"
    assert routed == []


def test_no_avg_price_no_force_exit(engine, monkeypatch):
    # 진입가 미상(avg_price=0) → position=None → 강제청산 안 함(보수적 안전).
    _set_thresholds(monkeypatch, tp=3.5, sl=2.0)
    out, routed = _scan(engine, _held(0), 104000)
    assert out["candidates_found"] == 0
    assert routed == []


def test_held_map_carries_avg_price_for_ra_snapshot():
    # R-A 상호작용: _kis_held_map 이 avg_price 를 담아 스냅샷 fallback 에도 진입가 보존.
    import app.kis_paper.driver_bridge as b
    b.reset_scan_rotation_for_tests()
    ok = _FakePosBroker([SimpleNamespace(symbol="005930", quantity=10, sellable_quantity=10, avg_price=100000)])
    m = asyncio.run(_kis_held_map(ok, fallback=set(), now=OPEN_TIME))
    assert m["005930"]["avg_price"] == 100000

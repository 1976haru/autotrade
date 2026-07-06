"""소유권 원장 필터 dry-run 테스트 — 2026-07-02.

dry-run 3종 (커밋 전 필수):
  (a) 봇 포지션(원장 내) + 현재가 -2% → STOP_LOSS 여전히 발동
  (b) 수동 보유(원장 밖) + 현재가 -5% → 청산 평가 제외, SELL 0건, SELL_NOT_BOT_OWNED
  (c) 원장 조회 실패(_bot_owned=None) → fail-open → 손절 발동 유지

★보호 4계층(RiskManager/PermissionGate/OrderExecutor/route_order) 미접촉.
  broker.place_order 호출 0건. 손절 로직(PositionContext/council/sell_reason) 무변경.
"""
from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from types import SimpleNamespace

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.db.base import Base
from app.kis_paper import driver_bridge
from app.kis_paper.driver_bridge import kis_paper_realtime_scan_tick
from app.market_data.kis_realtime import KIS_PRICE_OK, KisRealtimeQuote
from app.risk.risk_manager import RiskDecision


# ── fixtures ─────────────────────────────────────────────────────────────────
_NOW = datetime(2026, 7, 2, 1, 0, 0, tzinfo=timezone.utc)   # KST 10:00 장 OPEN
_BOT_SYM  = "000990"   # 봇이 산 종목
_MAN_SYM  = "005930"   # 수동으로 산 종목 (DB에 BUY 레코드 없음)
_AVG_BOT  = 162_800
_AVG_MAN  = 350_000


@pytest.fixture(autouse=True)
def _patch_runtime(monkeypatch):
    import app.core.runtime_config as rc
    monkeypatch.setattr(rc, "effective_stop_loss_pct",  lambda: 1.0)
    monkeypatch.setattr(rc, "effective_take_profit_pct", lambda: 2.0)
    monkeypatch.setattr(rc, "effective_max_concurrent_positions", lambda: 5)
    monkeypatch.setattr(rc, "effective_per_stock_budget",  lambda: 1_000_000)
    monkeypatch.setattr(rc, "effective_daily_buy_limit",   lambda: 3_000_000)


def _settings():
    return SimpleNamespace(
        market_data_provider="kis", enable_kis_paper_auto_trading=True,
        kis_paper_auto_order_dry_run=False, kis_is_paper=True, enable_live_trading=False,
        kis_paper_auto_max_order_notional=100_000_000, kis_paper_auto_max_orders_per_day=10,
        kis_paper_auto_order_window_start="09:05", kis_paper_auto_order_window_end="14:50",
        kis_paper_auto_min_confidence=0.6, kis_paper_auto_min_quality_score=60,
        kis_paper_smoke_mode=False, kis_paper_smoke_symbol="005930", kis_paper_smoke_qty=1,
        kis_paper_max_concurrent_positions=5, kis_paper_per_symbol_notional_krw=1_000_000,
        kis_paper_daily_buy_limit_krw=3_000_000, kis_paper_max_new_positions_per_tick=1,
        kis_paper_scan_max_symbols=10,
        kis_app_key="FAKE", kis_app_secret="FAKE", kis_account_no="0000000000",
        kis_product_code="01",
    )


def _broker(positions: list):
    from app.brokers.kis import KisBrokerAdapter
    b = KisBrokerAdapter(is_paper=True)
    async def _pos():
        return positions
    b.get_positions = _pos
    return b


def _pos(symbol, avg, qty=10):
    return SimpleNamespace(symbol=symbol, quantity=qty, avg_price=avg, sellable_quantity=qty)


def _exit_input(symbol, avg, ret_pct):
    """현재가 = avg × (1 + ret_pct), mi=None → exit-only 모드."""
    price = round(avg * (1 + ret_pct))
    async def _fn(sym, *, client, now, market_is_open, **kw):
        return None, KisRealtimeQuote(symbol=sym, status=KIS_PRICE_OK,
                                      price=price, is_stale=False)
    return _fn


def _recording_route(calls):
    async def _fn(**kw):
        calls.append(kw.get("order"))
        audit = SimpleNamespace(id=1, broker_order_id="SELL-1",
                                broker_status="FILLED", filled_quantity=10, executed=True)
        return SimpleNamespace(decision=RiskDecision.APPROVED, reasons=[], audit=audit)
    return _fn


def _make_session():
    eng = create_engine("sqlite://", connect_args={"check_same_thread": False},
                        poolclass=StaticPool)
    Base.metadata.create_all(eng)
    return sessionmaker(bind=eng)


def _reset():
    driver_bridge._HELD_SNAPSHOT = {}
    driver_bridge._HELD_SNAPSHOT_AT = None


def _run(symbol, avg, ret_pct, *, positions, bot_owned_override, universe=None):
    _reset()
    driver_bridge._HELD_SNAPSHOT = {symbol: {"hldg": 10, "ord_psbl": 10, "avg_price": avg}}
    driver_bridge._HELD_SNAPSHOT_AT = _NOW
    calls = []
    out = asyncio.run(kis_paper_realtime_scan_tick(
        session_factory=_make_session(),
        broker=_broker(positions),
        risk=object(),
        route_order_fn=_recording_route(calls),
        settings=_settings(),
        market_input_fn=_exit_input(symbol, avg, ret_pct),
        universe_symbols=universe or [symbol],
        client=object(),
        now=_NOW,
        _bot_owned_override=bot_owned_override,
    ))
    sells = [o for o in calls if str(getattr(o, "side", "")).upper().endswith("SELL")]
    return out, sells


# ════════════════════════════════════════════════════════════════════
# DRY-RUN (a): 봇 포지션(원장 내) + 현재가 -2% → STOP_LOSS 발동
# ════════════════════════════════════════════════════════════════════

class TestDryRunA_BotOwnedStopLoss:
    """(a): 봇 소유 종목은 손절 1.0% 에서 여전히 청산된다."""

    def test_a1_stoploss_fires_on_bot_owned(self):
        """봇 원장에 있는 종목, 현재가 -2% → STOP_LOSS SELL 전송."""
        out, sells = _run(
            _BOT_SYM, _AVG_BOT, ret_pct=-0.02,
            positions=[_pos(_BOT_SYM, _AVG_BOT)],
            bot_owned_override=frozenset([_BOT_SYM]),
        )
        assert out["orders_submitted"] == 1, \
            f"봇 소유 손절 발동해야 함. submitted={out['orders_submitted']}, skipped={out.get('skipped',[])}"
        assert len(sells) == 1

    def test_a2_no_misfire_when_above_stop(self):
        """봇 원장 종목이라도 현재가 손절선 위(+0.5%) → SELL 없음."""
        out, sells = _run(
            _BOT_SYM, _AVG_BOT, ret_pct=+0.005,
            positions=[_pos(_BOT_SYM, _AVG_BOT)],
            bot_owned_override=frozenset([_BOT_SYM]),
        )
        assert out["orders_submitted"] == 0 and len(sells) == 0

    def test_a3_stoploss_fires_at_1pct_threshold(self):
        """손절 정확히 1.0% — 경계값 발동."""
        out, sells = _run(
            _BOT_SYM, _AVG_BOT, ret_pct=-0.011,   # 1.1% 하락 → 손절선(1.0%) 돌파
            positions=[_pos(_BOT_SYM, _AVG_BOT)],
            bot_owned_override=frozenset([_BOT_SYM]),
        )
        assert out["orders_submitted"] == 1 and len(sells) == 1


# ════════════════════════════════════════════════════════════════════
# DRY-RUN (b): 수동 보유(원장 밖) → SELL 0건, SELL_NOT_BOT_OWNED
# ════════════════════════════════════════════════════════════════════

class TestDryRunB_ManualNotSold:
    """(b): 원장 밖 수동 보유 종목은 현재가 -5% 여도 청산 평가 제외."""

    def test_b1_manual_not_sold_when_below_stop(self):
        """수동 보유 종목, 현재가 -5% → SELL 0건."""
        out, sells = _run(
            _MAN_SYM, _AVG_MAN, ret_pct=-0.05,
            positions=[_pos(_MAN_SYM, _AVG_MAN)],
            bot_owned_override=frozenset(),   # 원장이 비어 있음 = 수동 보유
        )
        assert out["orders_submitted"] == 0, \
            f"수동 보유는 SELL 0건이어야 함. submitted={out['orders_submitted']}"
        assert len(sells) == 0

    def test_b2_sell_not_bot_owned_in_skipped(self, monkeypatch):
        """수동 보유 SELL → skipped list에 SELL_NOT_BOT_OWNED.

        Filter B 직접 테스트: council을 SELL로 강제 → Filter B가 차단해야 함.
        (mi=None + PositionContext 없으면 council이 HOLD → EXIT_ONLY_NO_SELL로 선착
         해 Filter B에 도달하지 않으므로 monkeypatch로 council을 우회한다.)
        """
        from types import SimpleNamespace as NS
        from app.agents.agent_council import CouncilAction

        # council → 항상 SELL 반환(Filter B 도달 보장)
        def _fake_council(*a, **kw):
            return NS(final_action=CouncilAction.SELL)

        monkeypatch.setattr("app.agents.agent_council.run_agent_council", _fake_council)

        _reset()
        driver_bridge._HELD_SNAPSHOT = {
            _MAN_SYM: {"hldg": 10, "ord_psbl": 10, "avg_price": _AVG_MAN}}
        driver_bridge._HELD_SNAPSHOT_AT = _NOW
        calls = []
        out = asyncio.run(kis_paper_realtime_scan_tick(
            session_factory=_make_session(),
            broker=_broker([_pos(_MAN_SYM, _AVG_MAN)]),
            risk=object(),
            route_order_fn=_recording_route(calls),
            settings=_settings(),
            market_input_fn=_exit_input(_MAN_SYM, _AVG_MAN, -0.05),
            universe_symbols=[_MAN_SYM],
            client=object(),
            now=_NOW,
            _bot_owned_override=frozenset(),   # 원장 비어 있음 = 수동 보유
        ))
        skip_codes = [s.get("reason_code") for s in out.get("skipped", [])]
        sells = [o for o in calls if str(getattr(o, "side", "")).upper().endswith("SELL")]

        assert out["orders_submitted"] == 0, "수동 보유 SELL 0건이어야 함"
        assert len(sells) == 0
        assert "SELL_NOT_BOT_OWNED" in skip_codes, \
            f"SELL_NOT_BOT_OWNED 가 skipped 에 있어야 함. codes={skip_codes}"

    def test_b3_only_manual_sym_skipped_when_bot_sym_fires(self):
        """봇 종목(STOP_LOSS 발동) + 수동 종목(진입게이트 통과 못함) 동시 존재.

        현실적 시나리오(council 모킹 없음):
        - _BOT_SYM: PositionContext 있음 → real council이 STOP_LOSS SELL → 통과
        - _MAN_SYM: PositionContext 없음, mi=None → council HOLD → EXIT_ONLY_NO_SELL
          (Filter B 이전에 걸림 — 이것도 정상: 수동 종목 SELL 0건 보장)
        """
        _reset()
        driver_bridge._HELD_SNAPSHOT = {
            _BOT_SYM: {"hldg": 10, "ord_psbl": 10, "avg_price": _AVG_BOT},
            _MAN_SYM: {"hldg": 10, "ord_psbl": 10, "avg_price": _AVG_MAN},
        }
        driver_bridge._HELD_SNAPSHOT_AT = _NOW
        price_bot = round(_AVG_BOT * 0.98)   # -2% → stop-loss(1%) 돌파
        price_man = round(_AVG_MAN * 0.95)
        calls = []

        async def _multi_input(sym, *, client, now, market_is_open, **kw):
            px = price_bot if sym == _BOT_SYM else price_man
            return None, KisRealtimeQuote(symbol=sym, status=KIS_PRICE_OK,
                                          price=px, is_stale=False)

        out = asyncio.run(kis_paper_realtime_scan_tick(
            session_factory=_make_session(),
            broker=_broker([_pos(_BOT_SYM, _AVG_BOT), _pos(_MAN_SYM, _AVG_MAN)]),
            risk=object(),
            route_order_fn=_recording_route(calls),
            settings=_settings(),
            market_input_fn=_multi_input,
            universe_symbols=[_BOT_SYM, _MAN_SYM],
            client=object(),
            now=_NOW,
            _bot_owned_override=frozenset([_BOT_SYM]),   # 봇=000990, 수동=005930
        ))
        sells = [o for o in calls if str(getattr(o, "side", "")).upper().endswith("SELL")]
        skip_codes = [s.get("reason_code") for s in out.get("skipped", [])]

        # 봇 종목 손절 1건
        assert out["orders_submitted"] == 1, f"봇 종목 손절 1건 전송해야 함. codes={skip_codes}"
        assert len(sells) == 1
        # 수동 종목은 어떤 경로로든 SELL 차단
        assert all(o.symbol != _MAN_SYM for o in sells), "수동 종목 SELL 없어야 함"


# ════════════════════════════════════════════════════════════════════
# DRY-RUN (c): 원장 조회 실패(_bot_owned=None) → fail-open → 손절 유지
# ════════════════════════════════════════════════════════════════════

class TestDryRunC_FailOpen:
    """(c): 원장 None(조회 실패) → 전부 봇소유 간주 → 손절 발동 유지."""

    def test_c1_failopen_stoploss_still_fires(self):
        """_bot_owned=None(조회 실패) 시에도 봇 손절이 발동돼야 함 — 06-25 재발 방지."""
        out, sells = _run(
            _BOT_SYM, _AVG_BOT, ret_pct=-0.02,
            positions=[_pos(_BOT_SYM, _AVG_BOT)],
            bot_owned_override=None,   # None = fail-open 시뮬레이션
        )
        assert out["orders_submitted"] == 1, \
            f"fail-open: 원장 None 이어도 손절 발동해야 함. submitted={out['orders_submitted']}"
        assert len(sells) == 1

    def test_c2_failopen_also_evaluates_manual_sym(self):
        """fail-open 시 수동 종목도 봇소유로 간주 → SELL 평가됨 (의도된 trade-off)."""
        out, sells = _run(
            _MAN_SYM, _AVG_MAN, ret_pct=-0.05,
            positions=[_pos(_MAN_SYM, _AVG_MAN)],
            bot_owned_override=None,   # fail-open
        )
        # fail-open에서는 수동 종목도 손절 평가 대상
        # → 이건 의도된 동작: 원장 실패 시 수동 보호보다 손절 유지 우선
        assert "SELL_NOT_BOT_OWNED" not in \
            [s.get("reason_code") for s in out.get("skipped", [])], \
            "fail-open에서는 SELL_NOT_BOT_OWNED 차단 없어야 함"


# ════════════════════════════════════════════════════════════════════
# _bot_owned_symbols 헬퍼 유닛 테스트
# ════════════════════════════════════════════════════════════════════

class TestBotOwnedSymbolsHelper:
    """_bot_owned_symbols DB 쿼리 헬퍼 직접 검증."""

    def _make_db(self):
        eng = create_engine("sqlite://", connect_args={"check_same_thread": False},
                            poolclass=StaticPool)
        Base.metadata.create_all(eng)
        Session = sessionmaker(bind=eng)
        return Session()

    def _add_order(self, db, symbol, side, qty, *, trade_reason="kis_paper_auto",
                   decision="APPROVED"):
        from app.db.models import OrderAuditLog
        from datetime import datetime, timezone
        db.add(OrderAuditLog(
            symbol=symbol, side=side, trade_reason=trade_reason,
            decision=decision, executed=True,
            filled_quantity=qty, avg_fill_price=100_000,
            created_at=datetime.now(timezone.utc),
            # NOT NULL 필드 최솟값
            mode="PAPER", quantity=qty, order_type="MARKET", latest_price=100_000,
        ))

    def _make_db_with_buys(self, symbols):
        db = self._make_db()
        for sym in symbols:
            self._add_order(db, sym, "BUY", 10)
        db.commit()
        return db

    def test_returns_bot_owned_symbols(self):
        db = self._make_db_with_buys(["000990", "005930"])
        result = driver_bridge._bot_owned_symbols(db)
        assert result == frozenset(["000990", "005930"])

    def test_returns_empty_when_no_buys(self):
        db = self._make_db()
        result = driver_bridge._bot_owned_symbols(db)
        assert result == frozenset()

    def test_returns_none_on_error(self):
        class _BrokenDb:
            def query(self, *a, **k): raise RuntimeError("db error")
        result = driver_bridge._bot_owned_symbols(_BrokenDb())
        assert result is None

    # ────────────────────────────────────────────────────────────────
    # ★기아(000270) 사고 재현 — 순보유(net) 반영 회귀 테스트 (2026-07-06)
    # ────────────────────────────────────────────────────────────────

    def test_fully_closed_symbol_excluded_from_ledger(self):
        """봇이 과거 BUY+SELL로 전량 청산(net=0)한 종목은 원장에서 제외돼야 함.

        기아 사고 재현: 06-05 BUY 6주, 06-12 BUY 18주 + SELL 18주(전량청산).
        net = 6(06-05 몫, 실제로는 이후 다른 SELL로 청산됨) — 여기서는 단순화해
        전체 BUY=24, SELL=24로 net=0 케이스를 직접 구성한다.
        """
        db = self._make_db()
        self._add_order(db, "000270", "BUY", 6)
        self._add_order(db, "000270", "BUY", 18)
        self._add_order(db, "000270", "SELL", 24)
        db.commit()
        result = driver_bridge._bot_owned_symbols(db)
        assert "000270" not in result, \
            "전량 청산(net=0)된 종목은 원장에 남아있으면 안 됨 (기아 사고 재현)"

    def test_partially_closed_symbol_still_included(self):
        """봇이 일부만 매도(net>0)한 종목은 여전히 원장에 남아 손절 대상이어야 함."""
        db = self._make_db()
        self._add_order(db, "005930", "BUY", 10)
        self._add_order(db, "005930", "SELL", 4)
        db.commit()
        result = driver_bridge._bot_owned_symbols(db)
        assert "005930" in result, "순보유(net=6>0) 종목은 원장에 남아있어야 함"

    def test_manual_buy_never_counted_even_alone(self):
        """manual_buy 태그는 trade_reason 필터에서 애초에 제외 — 이번 버그의 원인 아님을 확인."""
        db = self._make_db()
        self._add_order(db, "000270", "BUY", 1, trade_reason="manual_buy")
        db.commit()
        result = driver_bridge._bot_owned_symbols(db)
        assert result == frozenset(), \
            "manual_buy 단독으로는 원장에 절대 들어가면 안 됨 (trade_reason 필터는 원래부터 정상)"


# ════════════════════════════════════════════════════════════════════
# DRY-RUN (d): 같은 종목을 봇(과거, 전량청산)+수동(오늘) 재사용 — 기아 사고 재현
# ════════════════════════════════════════════════════════════════════

class TestDryRunD_GhostOwnershipReclaim:
    """(d): 봇이 과거 전량 청산한 종목을 사용자가 오늘 재매수 → 손절 오발동 안 됨.

    실제 사고(2026-07-06 기아/000270) end-to-end 재현:
    - _bot_owned_symbols는 override 없이 *실제 DB 쿼리* 사용
    - DB에는 봇의 과거 BUY+SELL(net=0, 전량청산) 이력만 있음
    - held_map(현재 KIS 잔고)에는 사용자의 오늘자 수동매수 1주만 존재
    - 기대: net-quantity 수정 후 SELL_NOT_BOT_OWNED로 청산 제외 (수정 전엔 오발동했음)
    """

    def test_d1_ghost_history_does_not_trigger_forced_stoploss(self):
        _MAN = "000270"
        _AVG = 159_950  # 실제 사고의 수동매수 체결가

        db = TestBotOwnedSymbolsHelper()._make_db()
        TestBotOwnedSymbolsHelper()._add_order(db, _MAN, "BUY", 6)
        TestBotOwnedSymbolsHelper()._add_order(db, _MAN, "BUY", 18)
        TestBotOwnedSymbolsHelper()._add_order(db, _MAN, "SELL", 24)
        db.commit()

        def _session_factory():
            return db

        _reset()
        driver_bridge._HELD_SNAPSHOT = {_MAN: {"hldg": 1, "ord_psbl": 1, "avg_price": _AVG}}
        driver_bridge._HELD_SNAPSHOT_AT = _NOW
        calls = []
        out = asyncio.run(kis_paper_realtime_scan_tick(
            session_factory=_session_factory,
            broker=_broker([_pos(_MAN, _AVG, qty=1)]),
            risk=object(),
            route_order_fn=_recording_route(calls),
            settings=_settings(),
            market_input_fn=_exit_input(_MAN, _AVG, -0.02),  # 실사고와 동일 -2% 하락
            universe_symbols=[_MAN],
            client=object(),
            now=_NOW,
            # ★override 미주입 — 실제 _bot_owned_symbols(db) 경로를 그대로 태운다.
        ))
        sells = [o for o in calls if str(getattr(o, "side", "")).upper().endswith("SELL")]
        skip_codes = [s.get("reason_code") for s in out.get("skipped", [])]

        assert out["orders_submitted"] == 0, \
            f"전량청산 이력만 있는 종목의 신규 수동매수는 손절 오발동 안 돼야 함. codes={skip_codes}"
        assert len(sells) == 0
        # position=None(원장 밖 판정) → mi=None exit-only 입력에서는 council이 HOLD로
        # 떨어져 EXIT_ONLY_NO_SELL로 먼저 걸린다(Filter B 도달 전 선착, test_b3와 동일 패턴).
        # 핵심 불변식은 "SELL 0건" — 이 사고 재현에서 실제로 막혔는지가 중요.
        assert skip_codes == ["EXIT_ONLY_NO_SELL"], \
            f"수정 전(버그)에는 orders_submitted==1 이었어야 할 자리 — 지금은 안전하게 skip. codes={skip_codes}"

    def test_d2_filter_b_explicitly_blocks_with_real_ledger(self, monkeypatch):
        """council SELL 강제 + override 미주입 → Filter B가 실제 DB 원장으로 SELL_NOT_BOT_OWNED 반환."""
        from types import SimpleNamespace as NS
        from app.agents.agent_council import CouncilAction

        def _fake_council(*a, **kw):
            return NS(final_action=CouncilAction.SELL)

        monkeypatch.setattr("app.agents.agent_council.run_agent_council", _fake_council)

        _MAN = "000270"
        _AVG = 159_950
        db = TestBotOwnedSymbolsHelper()._make_db()
        TestBotOwnedSymbolsHelper()._add_order(db, _MAN, "BUY", 6)
        TestBotOwnedSymbolsHelper()._add_order(db, _MAN, "BUY", 18)
        TestBotOwnedSymbolsHelper()._add_order(db, _MAN, "SELL", 24)
        db.commit()

        _reset()
        driver_bridge._HELD_SNAPSHOT = {_MAN: {"hldg": 1, "ord_psbl": 1, "avg_price": _AVG}}
        driver_bridge._HELD_SNAPSHOT_AT = _NOW
        calls = []
        out = asyncio.run(kis_paper_realtime_scan_tick(
            session_factory=lambda: db,
            broker=_broker([_pos(_MAN, _AVG, qty=1)]),
            risk=object(),
            route_order_fn=_recording_route(calls),
            settings=_settings(),
            market_input_fn=_exit_input(_MAN, _AVG, -0.02),
            universe_symbols=[_MAN],
            client=object(),
            now=_NOW,
        ))
        sells = [o for o in calls if str(getattr(o, "side", "")).upper().endswith("SELL")]
        skip_codes = [s.get("reason_code") for s in out.get("skipped", [])]

        assert out["orders_submitted"] == 0
        assert len(sells) == 0
        assert "SELL_NOT_BOT_OWNED" in skip_codes, \
            f"실제 DB 원장(net=0)으로도 Filter B가 SELL_NOT_BOT_OWNED를 반환해야 함. codes={skip_codes}"

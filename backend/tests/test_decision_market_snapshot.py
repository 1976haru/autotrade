"""P-22: Decision Episode 시장 스냅샷 테스트.

검증:
 - market data 있으면 snapshot OK + symbol/price/OHLC/volume/trading_value/vwap/
   rsi/macd/moving_averages/gap_pct/minutes_since_open/market_time_phase/
   market_regime/price_age_seconds
 - market data 없으면 NO_MARKET_DATA
 - stale 시세면 PRICE_STALE
 - 없는 지표는 null
 - episode 에 snapshot 저장 (HOLD 포함) + market_summary
 - secret 0건, is_live_authorization=False
"""

from __future__ import annotations

import re
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

import app.agents.market_snapshot as ms_mod
from app.agents.agent_council import StrategyMarketInput
from app.agents.decision_episode import get_episode, new_episode_id, record_episode
from app.agents.market_snapshot import (
    DATA_STATUS_NO_MARKET_DATA,
    DATA_STATUS_OK,
    DATA_STATUS_PRICE_STALE,
    build_market_snapshot,
    compute_macd,
    compute_rsi,
    market_time_phase,
    minutes_since_open,
)
from app.db.base import Base

_MODULE = Path(ms_mod.__file__).resolve()
# 2026-05-27 (수) 02:30 UTC = 11:30 KST → MIDDAY, 장 시작 후 150분.
NOW = datetime(2026, 5, 27, 2, 30, 0, tzinfo=timezone.utc)


def _market_input(closes_n=30):
    return StrategyMarketInput(
        symbol="005930", current_price=75000, prev_close=74000, open_price=74500,
        vwap=75200, opening_range_high=75300, opening_range_low=74400,
        recent_closes=tuple(74000 + 100 * i for i in range(closes_n)),
        current_volume=120000, avg_volume=100000,
        market_regime="TREND_UP", regime_decision="ALLOW",
    )


class TestSnapshotFields:
    def test_ok_with_market_data(self):
        s = build_market_snapshot(market_input=_market_input(), now=NOW,
                                  market_regime="TREND_UP").to_dict()
        assert s["data_status"] == DATA_STATUS_OK
        assert s["symbol"] == "005930"
        assert s["price"] == 75000.0

    def test_ohlc_and_volume(self):
        s = build_market_snapshot(market_input=_market_input(), now=NOW).to_dict()
        assert s["open"] == 74500.0
        assert s["high"] == 75300.0
        assert s["low"] == 74400.0
        assert s["previous_close"] == 74000.0
        assert s["volume"] == 120000.0
        assert s["avg_volume"] == 100000.0
        assert s["trading_value"] == 75000.0 * 120000.0

    def test_vwap_and_moving_averages(self):
        s = build_market_snapshot(market_input=_market_input(), now=NOW).to_dict()
        assert s["vwap"] == 75200.0
        assert s["moving_averages"]["sma_5"] is not None
        assert s["moving_averages"]["sma_20"] is not None

    def test_rsi_computed_when_enough_data(self):
        s = build_market_snapshot(market_input=_market_input(30), now=NOW).to_dict()
        assert s["rsi"] is not None  # 30 closes ≥ 15

    def test_rsi_null_when_insufficient(self):
        s = build_market_snapshot(market_input=_market_input(5), now=NOW).to_dict()
        assert s["rsi"] is None  # 5 closes < 15

    def test_macd_computed_when_enough_data(self):
        s = build_market_snapshot(market_input=_market_input(40), now=NOW).to_dict()
        assert s["macd"] is not None and "histogram" in s["macd"]

    def test_macd_null_when_insufficient(self):
        s = build_market_snapshot(market_input=_market_input(10), now=NOW).to_dict()
        assert s["macd"] is None

    def test_gap_pct(self):
        s = build_market_snapshot(market_input=_market_input(), now=NOW).to_dict()
        # (74500 - 74000)/74000*100 = 0.6757
        assert abs(s["gap_pct"] - 0.6757) < 0.01

    def test_minutes_since_open_and_phase(self):
        s = build_market_snapshot(market_input=_market_input(), now=NOW).to_dict()
        assert s["minutes_since_open"] == 150
        assert s["market_time_phase"] == "MIDDAY"

    def test_market_regime_carried(self):
        s = build_market_snapshot(market_input=_market_input(), now=NOW,
                                  market_regime="VOLATILE").to_dict()
        assert s["market_regime"] == "VOLATILE"

    def test_price_age_and_ok(self):
        s = build_market_snapshot(market_input=_market_input(), now=NOW,
                                  price_timestamp=NOW - timedelta(seconds=5)).to_dict()
        assert s["price_age_seconds"] == 5.0
        assert s["data_status"] == DATA_STATUS_OK

    def test_invariants(self):
        s = build_market_snapshot(market_input=_market_input(), now=NOW).to_dict()
        assert s["contains_secret"] is False
        assert s["is_live_authorization"] is False


class TestDataStatus:
    def test_no_market_data(self):
        s = build_market_snapshot(market_input=None, now=NOW).to_dict()
        assert s["data_status"] == DATA_STATUS_NO_MARKET_DATA
        assert s["reason_code"] == DATA_STATUS_NO_MARKET_DATA
        assert s["price"] is None

    def test_price_stale(self):
        s = build_market_snapshot(
            market_input=_market_input(), now=NOW,
            price_timestamp=NOW - timedelta(seconds=300), max_age_seconds=60,
        ).to_dict()
        assert s["data_status"] == DATA_STATUS_PRICE_STALE
        assert s["reason_code"] == DATA_STATUS_PRICE_STALE
        assert s["price_age_seconds"] == 300.0


class TestPhaseHelpers:
    @pytest.mark.parametrize("utc_hour,utc_min,expected", [
        (23, 0, "PRE_MARKET"),   # 08:00 KST (전일 23:00 UTC → 다음날 08:00)
        (0, 15, "OPENING_RANGE"),  # 09:15 KST
        (1, 0, "MORNING"),         # 10:00 KST
        (3, 0, "MIDDAY"),          # 12:00 KST
        (5, 30, "CLOSING"),        # 14:30 KST
        (7, 0, "AFTER_MARKET"),    # 16:00 KST
    ])
    def test_market_time_phase(self, utc_hour, utc_min, expected):
        # 2026-05-27 수요일 기준.
        now = datetime(2026, 5, 27, utc_hour, utc_min, tzinfo=timezone.utc)
        assert market_time_phase(now) == expected

    def test_weekend_after_market(self):
        sat = datetime(2026, 5, 30, 3, 0, tzinfo=timezone.utc)  # 토 12:00 KST
        assert market_time_phase(sat) == "AFTER_MARKET"
        assert minutes_since_open(sat) is None

    def test_rsi_macd_pure_helpers(self):
        assert compute_rsi([1, 2, 3], period=14) is None
        assert compute_macd([1, 2, 3]) is None


class TestEpisodeIntegration:
    @pytest.fixture
    def db(self):
        eng = create_engine("sqlite://", connect_args={"check_same_thread": False},
                            poolclass=StaticPool)
        Base.metadata.create_all(eng)
        s = sessionmaker(bind=eng)()
        yield s
        s.close()

    def test_episode_stores_snapshot_for_buy(self, db):
        eid = new_episode_id()
        snap = build_market_snapshot(market_input=_market_input(), now=NOW,
                                     market_regime="TREND_UP").to_dict()
        record_episode(db, episode_id=eid, final_action="BUY", symbol="005930",
                       market_snapshot=snap)
        db.commit()
        ep = get_episode(db, eid)
        assert ep["market_snapshot"]["data_status"] == "OK"
        assert ep["market_snapshot"]["price"] == 75000.0
        # market_summary 추출 확인.
        assert ep["market_summary"]["market_regime"] == "TREND_UP"
        assert ep["market_summary"]["vwap"] == 75200.0

    def test_episode_stores_snapshot_for_hold(self, db):
        eid = new_episode_id()
        snap = build_market_snapshot(market_input=None, now=NOW).to_dict()
        record_episode(db, episode_id=eid, final_action="HOLD", symbol="X",
                       market_snapshot=snap)
        db.commit()
        ep = get_episode(db, eid)
        assert ep["final_action"] == "HOLD"
        assert ep["market_snapshot"]["data_status"] == "NO_MARKET_DATA"
        assert ep["market_summary"]["data_status"] == "NO_MARKET_DATA"


class TestStaticGuards:
    def test_module_no_broker_executor_import(self):
        text = _MODULE.read_text(encoding="utf-8")
        for pat in (r"from app\.brokers", r"from app\.execution",
                    r"route_order\s*\(", r"OrderExecutor\s*\(",
                    r"\bbroker\.place_order\s*\(", r"import httpx", r"import requests"):
            assert not re.search(pat, text), f"market_snapshot.py 금지 패턴: /{pat}/"

    def test_module_no_secret_pattern(self):
        text = _MODULE.read_text(encoding="utf-8")
        assert not re.search(r"sk-[A-Za-z0-9]{16}|\b\d{6,}-\d{2,}\b", text)

"""AI agent feedback loop tests (163, MUST)."""

from datetime import datetime, timedelta, timezone

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.ai.feedback import (
    adjust_confidence,
    compute_historical_accuracy,
)
from app.ai.virtual_agent import AiProposal, VirtualAiAgent
from app.brokers.base import OrderSide
from app.db.base import Base
from app.db.models import OrderAuditLog


def _session():
    eng = create_engine("sqlite://", connect_args={"check_same_thread": False},
                        poolclass=StaticPool)
    Base.metadata.create_all(bind=eng)
    return sessionmaker(bind=eng, autoflush=False, autocommit=False, expire_on_commit=False)


def _ai_filled(db, *, side, qty, price, symbol="005930", strategy="ai_virtual"):
    """체결된 AI 주문 audit row — feedback 모듈이 보는 입력."""
    row = OrderAuditLog(
        mode="VIRTUAL_AI_EXECUTION", symbol=symbol, side=side, quantity=qty,
        order_type="MARKET", latest_price=price,
        decision="APPROVED", reasons=[],
        requested_by_ai=True,
        strategy=strategy,
        executed=True,
        broker_status="FILLED",
        filled_quantity=qty, avg_fill_price=price,
    )
    db.add(row)
    db.flush()
    return row


# ---------- adjust_confidence ----------

def test_adjust_confidence_clamps_top():
    assert adjust_confidence(80, 1.5) == 100  # 120 → 100


def test_adjust_confidence_clamps_bottom():
    assert adjust_confidence(50, -0.5) == 0   # -25 → 0


def test_adjust_confidence_zero_raw_stays_zero():
    assert adjust_confidence(0, 1.5) == 0


def test_adjust_confidence_factor_one_unchanged():
    assert adjust_confidence(70, 1.0) == 70


def test_adjust_confidence_typical_scaling():
    assert adjust_confidence(80, 0.7) == 56  # 80*0.7 = 56
    assert adjust_confidence(60, 1.2) == 72


# ---------- compute_historical_accuracy ----------

def test_empty_db_returns_neutral_factor():
    Session = _session()
    with Session() as db:
        acc = compute_historical_accuracy(db, strategy="ai_virtual")
    assert acc.trades_realized == 0
    assert acc.recommended_confidence_factor == 1.0


def test_below_min_samples_returns_neutral_factor():
    """샘플이 적으면 (< MIN_SAMPLE_TRADES) factor=1.0 — 보수적."""
    Session = _session()
    with Session() as db:
        # 5 BUY-SELL 사이클 (10건 미만).
        for _ in range(5):
            _ai_filled(db, side="BUY",  qty=1, price=100)
            _ai_filled(db, side="SELL", qty=1, price=110)
        db.commit()
        acc = compute_historical_accuracy(db, strategy="ai_virtual")
    assert acc.trades_realized == 5
    assert acc.recommended_confidence_factor == 1.0


def test_high_win_rate_returns_boost_factor():
    """win_rate >= 0.7이면 factor 1.2."""
    Session = _session()
    with Session() as db:
        # 12 거래, 9 승 (win_rate=0.75).
        for _ in range(9):
            _ai_filled(db, side="BUY",  qty=1, price=100)
            _ai_filled(db, side="SELL", qty=1, price=110)
        for _ in range(3):
            _ai_filled(db, side="BUY",  qty=1, price=100)
            _ai_filled(db, side="SELL", qty=1, price=90)
        db.commit()
        acc = compute_historical_accuracy(db, strategy="ai_virtual")
    assert acc.trades_realized == 12
    assert acc.wins == 9
    assert abs(acc.win_rate - 0.75) < 0.001
    assert acc.recommended_confidence_factor == 1.2


def test_low_win_rate_returns_penalty_factor():
    """win_rate < 0.4이면 factor 0.5 (절반)."""
    Session = _session()
    with Session() as db:
        # 10 거래, 3 승 (win_rate=0.3).
        for _ in range(3):
            _ai_filled(db, side="BUY",  qty=1, price=100)
            _ai_filled(db, side="SELL", qty=1, price=110)
        for _ in range(7):
            _ai_filled(db, side="BUY",  qty=1, price=100)
            _ai_filled(db, side="SELL", qty=1, price=80)
        db.commit()
        acc = compute_historical_accuracy(db, strategy="ai_virtual")
    assert acc.trades_realized == 10
    assert acc.wins == 3
    assert acc.recommended_confidence_factor == 0.5


def test_neutral_win_rate_returns_one():
    """0.5 ≤ win_rate < 0.6이면 factor 1.0."""
    Session = _session()
    with Session() as db:
        # 10 거래, 5 승 (win_rate=0.5).
        for _ in range(5):
            _ai_filled(db, side="BUY",  qty=1, price=100)
            _ai_filled(db, side="SELL", qty=1, price=110)
        for _ in range(5):
            _ai_filled(db, side="BUY",  qty=1, price=100)
            _ai_filled(db, side="SELL", qty=1, price=90)
        db.commit()
        acc = compute_historical_accuracy(db, strategy="ai_virtual")
    assert acc.trades_realized == 10
    assert acc.wins == 5
    assert acc.recommended_confidence_factor == 1.0


def test_accuracy_separates_by_strategy():
    """다른 strategy의 거래는 보지 않는다."""
    Session = _session()
    with Session() as db:
        # ai_a: 10 승.
        for _ in range(10):
            _ai_filled(db, side="BUY",  qty=1, price=100, strategy="ai_a")
            _ai_filled(db, side="SELL", qty=1, price=110, strategy="ai_a")
        # ai_b: 10 패.
        for _ in range(10):
            _ai_filled(db, side="BUY",  qty=1, price=100, strategy="ai_b")
            _ai_filled(db, side="SELL", qty=1, price=80, strategy="ai_b")
        db.commit()
        acc_a = compute_historical_accuracy(db, strategy="ai_a")
        acc_b = compute_historical_accuracy(db, strategy="ai_b")
    assert acc_a.recommended_confidence_factor == 1.2  # 100% wins
    assert acc_b.recommended_confidence_factor == 0.5  # 0% wins


def test_accuracy_excludes_non_ai_rows():
    """requested_by_ai=False audit 행은 무시 — 운영자 수동 거래로 AI 평가가
    오염되지 않는다."""
    Session = _session()
    with Session() as db:
        # 모두 비-AI 거래로 시드.
        for _ in range(15):
            row = OrderAuditLog(
                mode="SIMULATION", symbol="005930", side="BUY", quantity=1,
                order_type="MARKET", latest_price=100,
                decision="APPROVED", reasons=[],
                requested_by_ai=False,
                strategy="ai_virtual",
                executed=True, broker_status="FILLED",
                filled_quantity=1, avg_fill_price=100,
            )
            db.add(row)
        db.commit()
        acc = compute_historical_accuracy(db, strategy="ai_virtual")
    assert acc.trades_realized == 0
    assert acc.recommended_confidence_factor == 1.0


def test_lookback_excludes_old_rows():
    """lookback_days 윈도우 밖 거래는 미카운트."""
    Session = _session()
    now = datetime.now(timezone.utc)
    with Session() as db:
        # 60일 전 손실 10건 (lookback 30일 밖).
        for _ in range(10):
            row = OrderAuditLog(
                mode="VIRTUAL_AI_EXECUTION", symbol="005930",
                side="BUY", quantity=1, order_type="MARKET", latest_price=100,
                decision="APPROVED", reasons=[],
                requested_by_ai=True, strategy="ai_virtual",
                executed=True, broker_status="FILLED",
                filled_quantity=1, avg_fill_price=100,
                created_at=now - timedelta(days=60),
            )
            db.add(row)
            row = OrderAuditLog(
                mode="VIRTUAL_AI_EXECUTION", symbol="005930",
                side="SELL", quantity=1, order_type="MARKET", latest_price=80,
                decision="APPROVED", reasons=[],
                requested_by_ai=True, strategy="ai_virtual",
                executed=True, broker_status="FILLED",
                filled_quantity=1, avg_fill_price=80,
                created_at=now - timedelta(days=60),
            )
            db.add(row)
        db.commit()
        acc = compute_historical_accuracy(db, strategy="ai_virtual",
                                            lookback_days=30, now=now)
    assert acc.trades_realized == 0  # 모두 윈도우 밖.


# ---------- VirtualAiAgent.calibrate_with_feedback ----------

def test_calibrate_no_history_returns_unchanged_proposal():
    Session = _session()
    agent = VirtualAiAgent()
    raw = AiProposal(symbol="005930", side=OrderSide.BUY, quantity=1,
                     confidence=70, reasons=["test"])
    with Session() as db:
        adjusted = agent.calibrate_with_feedback(raw, db)
    # 표본 없으니 factor=1.0, confidence 변동 X.
    assert adjusted.confidence == 70
    assert adjusted.extra_meta["historical_factor"] == 1.0
    assert adjusted.extra_meta["raw_confidence"]    == 70


def test_calibrate_high_win_rate_boosts_confidence():
    Session = _session()
    with Session() as db:
        # 12 거래 모두 승.
        for _ in range(12):
            _ai_filled(db, side="BUY",  qty=1, price=100, strategy="ai_virtual")
            _ai_filled(db, side="SELL", qty=1, price=110, strategy="ai_virtual")
        db.commit()
    agent = VirtualAiAgent()
    raw = AiProposal(symbol="005930", side=OrderSide.BUY, quantity=1,
                     confidence=60, reasons=["test"])
    with Session() as db2:
        # 위 시드는 분리 세션이라 같은 in-memory DB 미공유 — 별도 세션에서
        # 다시 시드해 검증.
        for _ in range(12):
            _ai_filled(db2, side="BUY",  qty=1, price=100, strategy="ai_virtual")
            _ai_filled(db2, side="SELL", qty=1, price=110, strategy="ai_virtual")
        db2.commit()
        adjusted = agent.calibrate_with_feedback(raw, db2)
    # factor 1.2 → 60 * 1.2 = 72.
    assert adjusted.confidence == 72
    assert adjusted.extra_meta["historical_factor"] == 1.2


def test_calibrate_low_win_rate_penalizes_confidence():
    Session = _session()
    with Session() as db:
        for _ in range(2):
            _ai_filled(db, side="BUY",  qty=1, price=100, strategy="ai_virtual")
            _ai_filled(db, side="SELL", qty=1, price=110, strategy="ai_virtual")
        for _ in range(8):
            _ai_filled(db, side="BUY",  qty=1, price=100, strategy="ai_virtual")
            _ai_filled(db, side="SELL", qty=1, price=80, strategy="ai_virtual")
        db.commit()
        agent = VirtualAiAgent()
        raw = AiProposal(symbol="005930", side=OrderSide.BUY, quantity=1,
                         confidence=80, reasons=["test"])
        adjusted = agent.calibrate_with_feedback(raw, db)
    # win_rate=0.2 → factor 0.5 → 80 * 0.5 = 40.
    assert adjusted.confidence == 40
    assert adjusted.extra_meta["historical_factor"] == 0.5
    assert adjusted.extra_meta["historical_win_rate"] == 0.2


def test_calibrated_proposal_carries_to_order_request():
    """calibrated proposal을 to_order_request 했을 때 ai_decision_meta에
    historical_factor / raw_confidence가 들어가는지 — audit row까지 carry."""
    raw = AiProposal(symbol="005930", side=OrderSide.BUY, quantity=1,
                     confidence=70, reasons=["test"],
                     extra_meta={"raw_confidence": 80,
                                 "historical_factor": 0.875,
                                 "historical_trades": 12,
                                 "historical_win_rate": 0.5})
    req = raw.to_order_request()
    assert req.signal_confidence == 70  # 보정된 값
    meta = req.ai_decision_meta
    assert meta["raw_confidence"]      == 80
    assert meta["historical_factor"]   == 0.875
    assert meta["historical_trades"]   == 12
    assert meta["historical_win_rate"] == 0.5

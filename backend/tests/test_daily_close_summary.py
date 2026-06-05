"""PART3: 하루 마감 1장 요약 테스트."""

from __future__ import annotations

from datetime import date, datetime, timedelta, timezone

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.agents.daily_close_summary import (
    DailyCloseSummary,
    build_daily_close_summary,
    render_markdown,
)
from app.db.base import Base
from app.db.models import AgentDecisionLog, OrderAuditLog

_KST = timezone(timedelta(hours=9))
REPORT_DATE = date(2026, 6, 1)
# 2026-06-01 10:00 KST = 01:00 UTC (그날 윈도우 안).
T = datetime(2026, 6, 1, 1, 0, 0)


@pytest.fixture
def db():
    eng = create_engine("sqlite://", connect_args={"check_same_thread": False},
                        poolclass=StaticPool)
    Base.metadata.create_all(eng)
    Session = sessionmaker(bind=eng)
    s = Session()
    yield s
    s.close()


def _order(db, *, side, bstat="RECEIVED", filled=0, mode="PAPER",
           msg="", symbol="005930"):
    db.add(OrderAuditLog(
        created_at=T, mode=mode, requested_by_ai=False, symbol=symbol,
        side=side, quantity=1, order_type="MARKET", decision="APPROVED",
        executed=True, broker_order_id="X", broker_status=bstat,
        filled_quantity=filled, limit_price=10000, latest_price=10000,
        message=msg, trade_reason="kis_paper_auto", strategy="VWAP",
    ))


def _decision(db, *, decision, symbol="005930"):
    db.add(AgentDecisionLog(
        created_at=T, agent_name="kis_paper_auto_executor", symbol=symbol,
        mode="PAPER", decision=decision, confidence=70, reasons="[]",
        meta="{}", chain_id="c1",
    ))


def test_summary_counts_orders_and_rejections(db):
    _order(db, side="BUY")
    _order(db, side="BUY")
    _order(db, side="SELL")
    _order(db, side="SELL", bstat="REJECTED", msg="모의투자 잔고내역이 없습니다.")
    db.commit()
    s = build_daily_close_summary(db, REPORT_DATE)
    assert s.buy_orders == 2
    assert s.sell_orders == 2
    assert s.rejected_orders == 1
    assert s.rejection_reasons.get("모의투자 잔고내역이 없습니다.") == 1


def test_summary_decision_distribution(db):
    _decision(db, decision="BUY")
    _decision(db, decision="SELL")
    _decision(db, decision="SELL")
    _decision(db, decision="HOLD")
    db.commit()
    s = build_daily_close_summary(db, REPORT_DATE)
    assert s.decision_buy == 1
    assert s.decision_sell == 2
    assert s.decision_hold == 1
    assert s.decision_total == 4


def test_pnl_not_measurable_without_fills(db):
    _order(db, side="BUY", filled=0)
    db.commit()
    s = build_daily_close_summary(db, REPORT_DATE)
    assert s.pnl_measurable is False
    assert s.filled_orders == 0


def test_pnl_measurable_with_fills(db):
    _order(db, side="BUY", filled=4)
    db.commit()
    s = build_daily_close_summary(db, REPORT_DATE)
    assert s.filled_orders == 1
    assert s.pnl_measurable is True


def test_live_orders_counted_zero_for_paper(db):
    _order(db, side="BUY", mode="PAPER")
    db.commit()
    s = build_daily_close_summary(db, REPORT_DATE)
    assert s.live_orders_sent == 0


def test_safety_invariants_locked():
    # 위험 플래그를 True 로 만들려는 시도는 ValueError.
    with pytest.raises(ValueError):
        DailyCloseSummary(report_date="2026-06-01", is_live_authorization=True)
    with pytest.raises(ValueError):
        DailyCloseSummary(report_date="2026-06-01", is_order_signal=True)


def test_render_markdown_has_safety_and_no_advice(db):
    _order(db, side="BUY")
    db.commit()
    s = build_daily_close_summary(db, REPORT_DATE)
    md = render_markdown(s)
    assert "안전 상태" in md
    assert "투자 조언" in md           # disclaimer 존재
    # 매수/매도 *추천* 문구가 없어야 (요약일 뿐).
    assert "지금 매수" not in md
    assert "추천 종목" not in md

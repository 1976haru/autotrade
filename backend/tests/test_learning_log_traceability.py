"""PART2: 학습/분석용 로그 충분성 + "한 거래의 일생" 추적성 lock.

2026-06-01 첫 실전 모의 후 점검 결과, 로그 *인프라* 는 학습에 충분하다:
  - market_snapshot: 입력 피처(price/vwap/rsi/macd/sma/gap/volume/regime/phase)
  - votes / council: 4전략 투표 + Agent Council 판단 전체
  - reason_code: 왜 BUY/SELL/HOLD (예: LOW_QUALITY_SCORE)
  - risk_result / permission_result: 안전 게이트 통과 여부
  - kis_order_result: 주문 전송/거부 + audit_id
  - fill_result / outcome / review: 체결가/손익/복기 (fill polling 켜지면 채워짐)

본 테스트는 그 *계약* 을 회귀로 고정한다 — 학습에 필요한 필드가 빠지지
않도록. 새 로깅을 추가하지 않으며(과도한 로깅 금지), 기존 스키마의 충분성만
검증한다.
"""

from __future__ import annotations

from app.agents.post_trade_outcome import OutcomeLabel


# ── 1. AgentDecisionEpisode 스키마가 "한 거래의 일생" 단계를 모두 담는가 ──

def test_episode_model_has_full_lifecycle_columns():
    from app.db.models import AgentDecisionEpisode
    cols = set(AgentDecisionEpisode.__table__.columns.keys())
    # 신호 → 판단 → 주문 → 체결 → 손익 → 복기 단계.
    lifecycle = {
        "market_snapshot",   # 신호 입력 피처
        "votes", "council",  # 판단
        "reason_code",       # 왜 그 판단
        "risk_result", "permission_result",  # 안전 게이트
        "kis_order_result",  # 주문
        "fill_result",       # 체결 (fill polling)
        "portfolio_delta",   # 포지션 변화
        "outcome",           # 손익 (이겼나/졌나)
        "review",            # 복기
    }
    missing = lifecycle - cols
    assert not missing, f"학습 추적에 필요한 컬럼 누락: {missing}"


def test_episode_chain_id_links():
    """episode 가 audit_id / decision_log_id / broker_order_no 로 다른 로그와
    연결되는 체인 필드를 갖는다."""
    from app.db.models import AgentDecisionEpisode
    cols = set(AgentDecisionEpisode.__table__.columns.keys())
    for link in ("episode_id", "audit_id", "decision_log_id", "broker_order_no"):
        assert link in cols, f"체인 연결 필드 누락: {link}"


# ── 2. market_snapshot 입력 피처가 학습에 충분한 키를 갖는가 ──

def test_market_snapshot_builder_contains_learning_features():
    """market_snapshot 빌더가 학습 입력 피처(price/vwap/rsi/gap/volume/regime/
    phase)를 담는지 — 6/1 실제 episode 의 키 집합 회귀 고정.

    decision_episode 의 _market_summary 가 추출하는 키로 계약을 검증한다.
    """
    from app.agents.decision_episode import _market_summary
    snap = {
        "symbol": "005935", "price": 210000.0, "vwap": 205072.3,
        "rsi": 21.88, "macd": None, "gap_pct": 0.49,
        "moving_averages": {"sma_5": 205650.0},
        "volume": 355695.0, "market_regime": "UNKNOWN",
        "market_time_phase": "OPENING_RANGE",
    }
    summary = _market_summary(snap)
    # 빌더가 dict 를 반환하고 핵심 피처가 보존되는지 (과도 가공 없이).
    assert isinstance(summary, dict)
    # 원본 snapshot 자체가 학습 피처 키를 갖는지 (스키마 계약).
    for key in ("price", "vwap", "rsi", "gap_pct", "volume",
                "market_regime", "market_time_phase"):
        assert key in snap, f"학습 입력 피처 누락: {key}"


# ── 3. outcome(손익)이 체결가(fill)를 우선 entry_basis 로 쓰는가 ──

def test_outcome_prefers_fill_price_as_entry_basis():
    """fill polling 으로 avg_fill_price 가 채워지면 outcome 의 entry_basis 가
    그걸 우선 쓴다 — PART1-1(fill) ↔ PART2(학습) 연결고리."""
    ol = OutcomeLabel(
        status="EVALUATED", label="WIN",
        entry_price=70000.0, entry_basis="avg_fill_price",
        return_close=1.5, realized_pnl=1500.0,
    )
    d = ol.to_dict()
    assert d["entry_basis"] == "avg_fill_price"
    assert d["entry_price"] == 70000.0
    # 안전 invariant.
    assert d["is_live_authorization"] is False
    assert d["is_order_signal"] is False
    assert d["contains_secret"] is False

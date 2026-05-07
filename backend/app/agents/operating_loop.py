"""Agent Operating Loop — 하루 운용 루프 (223, MUST).

스마트폰에서 사용자가 최소한의 조작만 해도 Agent가 하루 흐름을 자동으로 진행
하도록 5단계 루프를 정의한다. 모든 단계는 deterministic stub으로 동작 — AI
Key 없이도 mock output을 안정적으로 생성. 실제 broker / API key / LIVE 주문은
관여하지 않는다 (VIRTUAL_AI_EXECUTION만 다룸).

루프:
  1) Pre-Market Brief        — 장 시작 전 위험도 / 한도 / readiness
  2) Market Open Watch       — 장 초반 변동성·갭·거래대금 감시 (advisory)
  3) Intraday Decision Loop  — 후보·전략·진입 판단 → Virtual Order 후보
  4) Position Monitoring Loop — 보유 포지션 감시 / 손절·익절·시간청산 권고
  5) Post-Market Review      — 당일 판단 복기 + 다음날 조정 제안

호출자:
  - /api/agents/pre-market-brief, /intraday-summary, /post-market-review
  - /api/agents/operating-loop/status (현재 단계)
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, time
from typing import Any


# ---------- 운용 단계 정의 ----------

# 의도된 단계 순서. status() 호출 시 KST 기준 시각으로 자동 매핑되며, 운영자가
# 강제 단계를 지정해 mock으로 돌리려면 stage 인자를 직접 줄 수도 있다.
OPERATING_STAGES = [
    "pre_market",        # 09:00 이전
    "market_open_watch", # 09:00 ~ 09:30
    "intraday",          # 09:30 ~ 15:00
    "position_monitor",  # 15:00 ~ 15:30 (장 마감 직전 점검)
    "post_market",       # 15:30 이후
]


def _stage_from_clock(now: datetime) -> str:
    """KST 휴리스틱 — 호출 시각 기준 현재 단계 추정.

    엄밀한 거래 시간 계산은 broker별로 다르지만, Agent OS의 *advisory* 단계
    구분은 휴리스틱으로 충분. 휴장일 / 동시호가 분기는 별도 PR에서 추가.
    """
    t = now.time()
    if t < time(9, 0):
        return "pre_market"
    if t < time(9, 30):
        return "market_open_watch"
    if t < time(15, 0):
        return "intraday"
    if t < time(15, 30):
        return "position_monitor"
    return "post_market"


# ---------- 1. Pre-Market Brief ----------

@dataclass
class PreMarketBrief:
    """장 시작 전 운용 readiness 브리프. 모든 필드는 stub으로 채워지며 외부
    LLM이나 실 시장 데이터에 의존하지 않는다 — AI Key 없을 때도 안전."""
    market_risk_level:    str               # LOW / MEDIUM / HIGH
    interesting_themes:   list[str]         = field(default_factory=list)
    available_strategies: list[str]         = field(default_factory=list)
    daily_loss_cap:       int               = 0     # KRW
    trading_allowed:      bool              = True
    readiness_score:      int               = 0     # 0-100
    readiness_label:      str               = "READY"  # READY / CAUTION / BLOCKED
    operator_summary:     list[str]         = field(default_factory=list)


def build_pre_market_brief(
    *,
    daily_loss_cap:    int,
    emergency_stop:    bool,
    enable_live_trading: bool,
    market_risk_level: str = "MEDIUM",
    themes:            list[str] | None = None,
    strategies:        list[str] | None = None,
) -> PreMarketBrief:
    """Deterministic Pre-Market Brief 생성.

    readiness_label 결정:
      - emergency_stop=True → BLOCKED (모든 신규 주문 차단)
      - market_risk_level=HIGH → CAUTION
      - 그 외 → READY

    enable_live_trading은 readiness 점수에 가산점 — 가상 운용은 항상 활성화로
    가정하지만, 실 LIVE 플래그 켜진 환경(아직 활성화 안 함)에서도 동일 정보
    경로를 사용하도록 일찍 분기 표면화.
    """
    themes_list     = list(themes     or ["대형주 모멘텀", "거래대금 상위"])
    strategies_list = list(strategies or ["sma_crossover", "orb_vwap", "rsi_reversion"])

    if emergency_stop:
        label  = "BLOCKED"
        score  = 0
        allowed = False
    elif market_risk_level.upper() == "HIGH":
        label  = "CAUTION"
        score  = 40
        allowed = True
    elif market_risk_level.upper() == "LOW":
        label  = "READY"
        score  = 90
        allowed = True
    else:
        label  = "READY"
        score  = 70
        allowed = True

    # enable_live_trading은 readiness 가산점 — virtual 모드에서는 항상 false인
    # 게 안전 기본값이라 점수 영향이 거의 없다 (-5 까지).
    if not enable_live_trading and label == "READY":
        score = max(score - 5, 0)

    summary = _operator_brief_summary(label, market_risk_level, daily_loss_cap, emergency_stop)

    return PreMarketBrief(
        market_risk_level=market_risk_level.upper(),
        interesting_themes=themes_list,
        available_strategies=strategies_list,
        daily_loss_cap=daily_loss_cap,
        trading_allowed=allowed,
        readiness_score=score,
        readiness_label=label,
        operator_summary=summary,
    )


def _operator_brief_summary(
    label: str, risk_level: str, daily_loss_cap: int, emergency_stop: bool,
) -> list[str]:
    """스마트폰 3줄 요약 — 운영자가 3초 안에 인지하도록 짧게."""
    if emergency_stop:
        return [
            "🛑 긴급 정지가 ON 상태",
            "신규 주문이 모두 차단됩니다",
            "해제 후 다시 시작하세요",
        ]
    if label == "BLOCKED":
        return ["오늘 자동운용 BLOCKED", f"위험도 {risk_level}", "수동 점검 필요"]
    risk_line = f"위험도 {risk_level}"
    cap_line  = f"오늘 손실 한도 {daily_loss_cap:,}원" if daily_loss_cap > 0 else "손실 한도 미설정"
    return [
        f"오늘 자동운용 {label}",
        risk_line,
        cap_line,
    ]


# ---------- 2. Market Open Watch ----------

@dataclass
class MarketOpenObservation:
    """장 초반 변동성·갭·거래대금 감시 결과. 단순 advisory — 실 데이터가
    없으면 빈 리스트로 채워져 안전 기본값을 유지."""
    volatile_symbols:  list[str]   = field(default_factory=list)
    gap_up_symbols:    list[str]   = field(default_factory=list)
    gap_down_symbols:  list[str]   = field(default_factory=list)
    volume_spikes:     list[str]   = field(default_factory=list)
    market_action:     str         = "WATCH"   # WATCH / PAUSE / NORMAL
    reasons:           list[str]   = field(default_factory=list)


def watch_market_open(
    *,
    gap_up_symbols:   list[str] | None = None,
    gap_down_symbols: list[str] | None = None,
    volume_spikes:    list[str] | None = None,
    volatility_pct:   float = 0.0,
) -> MarketOpenObservation:
    """장 초반 advisory. volatility_pct가 5%를 넘으면 PAUSE, 2~5%는 WATCH,
    그 외 NORMAL. 빈 입력은 NORMAL로 안전하게 처리."""
    gu = list(gap_up_symbols   or [])
    gd = list(gap_down_symbols or [])
    vs = list(volume_spikes    or [])
    reasons: list[str] = []
    if volatility_pct >= 5.0:
        action = "PAUSE"
        reasons.append(f"high volatility {volatility_pct:.1f}%")
    elif volatility_pct >= 2.0:
        action = "WATCH"
        reasons.append(f"moderate volatility {volatility_pct:.1f}%")
    else:
        action = "NORMAL"
    if gu:
        reasons.append(f"{len(gu)} gap-up symbols")
    if gd:
        reasons.append(f"{len(gd)} gap-down symbols")
    return MarketOpenObservation(
        volatile_symbols=gu + gd,
        gap_up_symbols=gu,
        gap_down_symbols=gd,
        volume_spikes=vs,
        market_action=action,
        reasons=reasons,
    )


# ---------- 3. Intraday Summary ----------

@dataclass
class IntradaySummary:
    """장중 결정 누적 요약. AgentDecisionLog에서 집계해 호출자가 채워주는 게
    이상적이지만, 본 dataclass 자체는 deterministic — 입력이 없으면 0/빈 값."""
    candidates_evaluated: int             = 0
    virtual_orders_made:  int             = 0
    rejected_signals:     int             = 0
    last_chief_decision:  str | None      = None
    notable_reasons:      list[str]       = field(default_factory=list)
    operator_summary:     list[str]       = field(default_factory=list)


def build_intraday_summary(
    *,
    candidates: int = 0, virtual_orders: int = 0, rejected: int = 0,
    last_decision: str | None = None,
    last_reasons:  list[str] | None = None,
) -> IntradaySummary:
    """집계 카운트만 받아 IntradaySummary 채움. operator_summary는 후보·주문·
    거절 비율을 사람말로 요약."""
    reasons = list(last_reasons or [])
    if candidates == 0:
        op = ["장중 후보 평가 없음", "Agent 대기 중", "—"]
    else:
        op = [
            f"후보 {candidates}건 평가",
            f"가상 주문 {virtual_orders}건 / 거절 {rejected}건",
            f"최근 결정: {last_decision or '—'}",
        ]
    return IntradaySummary(
        candidates_evaluated=candidates,
        virtual_orders_made=virtual_orders,
        rejected_signals=rejected,
        last_chief_decision=last_decision,
        notable_reasons=reasons[:5],
        operator_summary=op,
    )


# ---------- 4. Position Monitoring (advisory) ----------

@dataclass
class PositionMonitorEntry:
    """포지션별 advisory. 실제 청산 결정은 RiskManager + ExitTimingAgent가 함."""
    symbol:           str
    unrealized_pct:   float
    advice:           str       # HOLD / TAKE_PROFIT / STOP_LOSS / TRAILING / TIME_EXIT
    reasons:          list[str] = field(default_factory=list)


def review_positions(
    positions: list[dict[str, Any]] | None,
) -> list[PositionMonitorEntry]:
    """positions: [{symbol, unrealized_pct, holding_minutes}]. 단순 룰:
      - unrealized_pct >= 3% → TAKE_PROFIT
      - unrealized_pct <= -2% → STOP_LOSS
      - holding_minutes >= 240 → TIME_EXIT
      - 그 외 HOLD
    """
    out: list[PositionMonitorEntry] = []
    for p in positions or []:
        sym  = str(p.get("symbol", "?"))
        upct = float(p.get("unrealized_pct", 0.0))
        hold = int(p.get("holding_minutes", 0))
        if upct >= 3.0:
            advice  = "TAKE_PROFIT"
            reasons = [f"unrealized {upct:.2f}% >= +3%"]
        elif upct <= -2.0:
            advice  = "STOP_LOSS"
            reasons = [f"unrealized {upct:.2f}% <= -2%"]
        elif hold >= 240:
            advice  = "TIME_EXIT"
            reasons = [f"holding {hold}min >= 4h"]
        else:
            advice  = "HOLD"
            reasons = [f"unrealized {upct:.2f}%", f"holding {hold}min"]
        out.append(PositionMonitorEntry(
            symbol=sym, unrealized_pct=upct, advice=advice, reasons=reasons,
        ))
    return out


# ---------- 5. Post-Market Review ----------

@dataclass
class PostMarketReview:
    """당일 Agent 판단 복기."""
    total_decisions:        int         = 0
    successes:              int         = 0
    failures:               int         = 0
    misclassified_signals:  int         = 0
    pnl_estimate:           int         = 0   # KRW (mock)
    next_day_adjustments:   list[str]   = field(default_factory=list)
    agent_score_delta:      int         = 0   # -100 ~ +100
    operator_summary:       list[str]   = field(default_factory=list)


def build_post_market_review(
    *,
    total_decisions: int = 0,
    successes:       int = 0,
    failures:        int = 0,
    misclassified:   int = 0,
    pnl_estimate:    int = 0,
    next_adjustments: list[str] | None = None,
) -> PostMarketReview:
    """단순 score: success - failure - 0.5*misclassified, normalized by total."""
    if total_decisions == 0:
        score_delta = 0
        op = ["오늘 Agent 결정 없음", "복기 데이터 부족", "—"]
    else:
        raw = (successes - failures) - 0.5 * misclassified
        score_delta = int(round((raw / total_decisions) * 100))
        score_delta = max(-100, min(100, score_delta))
        op = [
            f"총 결정 {total_decisions}건",
            f"성공 {successes} · 실패 {failures}",
            f"점수 {score_delta:+d} / 추정 PnL {pnl_estimate:+,}원",
        ]
    return PostMarketReview(
        total_decisions=total_decisions,
        successes=successes,
        failures=failures,
        misclassified_signals=misclassified,
        pnl_estimate=pnl_estimate,
        next_day_adjustments=list(next_adjustments or []),
        agent_score_delta=score_delta,
        operator_summary=op,
    )


# ---------- Status (현재 단계 + 마지막 mock 출력) ----------

@dataclass
class OperatingLoopStatus:
    stage:             str
    stages:            list[str]
    last_brief:        PreMarketBrief        | None = None
    last_observation:  MarketOpenObservation | None = None
    last_intraday:     IntradaySummary       | None = None
    last_review:       PostMarketReview      | None = None


def current_stage(now: datetime | None = None) -> str:
    """KST 휴리스틱 단계. now=None이면 datetime.now() 사용 — 테스트는 직접 주입."""
    return _stage_from_clock(now or datetime.now())

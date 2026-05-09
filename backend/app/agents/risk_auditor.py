"""#54: Risk Auditor Agent.

장중 리스크 위반 / 데이터 지연 / 주문 거절 / 손실 원인 / 중복 주문 / AI 과신 /
선물 증거금 위험 등을 *감시*하고 위험 이벤트 발생 시 알림/리포트를 생성하는
read-only Agent. **주문 신호를 만들지 *않으며*, 위험 감지 시 *중지 권고*만**
한다 — 실제 emergency_stop 토글은 운영자/기존 Kill Switch UI가 수행.

## 핵심 invariant (절대 원칙)

1. **주문 신호 0건** — `is_order_signal=False` 불변, BUY/SELL/HOLD 반환 X
2. **broker 호출 0건** — 본 모듈은 broker / OrderExecutor / route_order /
   permission.gate 어떤 모듈도 import하지 않는다 (정적 grep 가드)
3. **emergency_stop 직접 토글 0건** — `RiskManager.set_emergency_stop` 호출 X,
   POST /api/risk/emergency-stop 호출 X. 본 Agent는 *권고*만 한다
4. **DB read-only** — INSERT / UPDATE / DELETE 0건 (정적 grep 가드)
5. **외부 AI / HTTP 호출 0건** — anthropic / openai / httpx / requests import 0건
6. **중지권한 우선 원칙**: `risk_score` ≥ critical 임계 도달 시
   `EMERGENCY_STOP_RECOMMENDED` 권고 — caller(운영자)가 실제 토글
7. **AI 자동실행 모드에서도 중단 권고 가능** — 본 Agent는 mode 무관하게
   동작, LIVE_AI_EXECUTION에서 위험 누적 시에도 PAUSE/STOP 권고

## 다른 Agent와의 관계

| Agent | 본 RiskAuditor를 어떻게 사용 |
|---|---|
| MarketObserverAgent (#52) | 시장 상태와 별도 — 본 Auditor는 운영 감사 (audit log 기반) |
| NewsTrendAgent (#53) | 뉴스 정보와 무관 — 본 Auditor는 실 주문 / 손실 데이터 분석 |
| ChiefTradingAgent | `EMERGENCY_STOP_RECOMMENDED=True`이면 *모든* 신규 진입 중단 |
| RiskAuditorAgent (#51 stock skeleton) | 본 모듈은 #51의 풍부한 확장 — DB-backed 감사 |

자세한 정책: [`docs/risk_auditor_agent.md`](../../../docs/risk_auditor_agent.md).
"""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from enum import StrEnum
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.agents.base import (
    AgentBase,
    AgentContext,
    AgentDecision,
    AgentMetadata,
    AgentOutput,
    AgentRole,
)
from app.db.models import (
    AgentDecisionLog,
    EmergencyStopEvent,
    OrderAuditLog,
)
from app.risk.emergency_reasons import EmergencyStopReason


# ====================================================================
# Enums
# ====================================================================


class AuditLevel(StrEnum):
    """리스크 감사 단계.

    - GREEN  : 정상
    - YELLOW : 경고 (개별 risk event 발생)
    - ORANGE : 주의 (PAUSE_TRADING 권고)
    - RED    : 긴급 (EMERGENCY_STOP 권고)
    """
    GREEN  = "GREEN"
    YELLOW = "YELLOW"
    ORANGE = "ORANGE"
    RED    = "RED"


class RiskEventType(StrEnum):
    """본 Agent가 감지하는 위험 이벤트 카테고리.

    `EmergencyStopReason`(#153)과 일관 — 운영자가 본 이벤트를 보고 emergency
    stop을 수동 토글할 때 reason_code로 그대로 carry 가능.
    """
    DAILY_LOSS_BREACH          = "daily_loss_breach"
    REPEATED_ORDER_FAILURE     = "repeated_order_failure"
    DUPLICATE_ORDER_BURST      = "duplicate_order_burst"
    DATA_STALE                 = "data_stale"
    AI_OVERCONFIDENCE          = "ai_overconfidence"      # 높은 conf로 거부 누적
    AI_LOW_CONFIDENCE_BURST    = "ai_low_confidence_burst"
    EMERGENCY_STOP_FLAPPING    = "emergency_stop_flapping" # ON/OFF 빠른 반복
    AGENT_WARN_BURST           = "agent_warn_burst"
    MARGIN_RISK                = "margin_risk"
    FUTURES_LIQUIDATION_RISK   = "futures_liquidation_risk"
    BROKER_ERROR_BURST         = "broker_error_burst"
    ABNORMAL_REJECTION_RATE    = "abnormal_rejection_rate"


class RiskEventSeverity(StrEnum):
    INFO     = "INFO"      # advisory
    WARN     = "WARN"      # YELLOW로 escalate
    HIGH     = "HIGH"      # ORANGE 권고
    CRITICAL = "CRITICAL"  # RED 권고


# ====================================================================
# Dataclasses
# ====================================================================


@dataclass(frozen=True)
class RiskEvent:
    """단일 위험 이벤트."""
    type:               RiskEventType
    severity:           RiskEventSeverity
    summary:            str
    evidence:           dict
    symbol:             str | None = None
    strategy:           str | None = None
    recommended_action: str | None = None


@dataclass(frozen=True)
class RiskAuditorReport:
    """장중 리스크 감사 리포트.

    절대 invariant:
    - `is_order_signal = False` 불변 (`__post_init__` 가드)
    - `pause_trading_recommended` / `emergency_stop_recommended`는 *권고만* —
      caller가 실제 emergency stop을 토글
    - 응답 dict에 BUY/SELL/HOLD 키 없음
    """

    audit_level:                  AuditLevel
    risk_score:                   int                  # 0~100
    summary_lines:                list[str]            # 운영자용 자연어 (3~5줄)
    events:                       list[RiskEvent]
    pause_trading_recommended:    bool
    emergency_stop_recommended:   bool
    recommended_stop_reason:      EmergencyStopReason | None  # 운영자가 토글 시 carry할 reason 코드
    window_seconds:               int | None = None
    total_audit_rows_inspected:   int        = 0
    total_emergency_events_inspected: int    = 0
    is_order_signal:              bool       = False
    created_at:                   datetime   = field(
        default_factory=lambda: datetime.now(timezone.utc)
    )

    def __post_init__(self) -> None:
        if self.is_order_signal:
            raise ValueError(
                "RiskAuditorReport.is_order_signal must be False — "
                "Risk Auditor is context-only (CLAUDE.md 절대 원칙 1, 2). "
                "BUY/SELL/HOLD는 RiskManager + PermissionGate + OrderExecutor "
                "흐름에서만 만들어진다."
            )
        if not (0 <= self.risk_score <= 100):
            raise ValueError(
                f"risk_score must be in [0, 100], got {self.risk_score}"
            )

    def to_dict(self) -> dict[str, Any]:
        return {
            "audit_level":                  self.audit_level.value,
            "risk_score":                   self.risk_score,
            "summary_lines":                list(self.summary_lines),
            "events":                       [
                {
                    "type":               e.type.value,
                    "severity":           e.severity.value,
                    "summary":            e.summary,
                    "evidence":           dict(e.evidence),
                    "symbol":             e.symbol,
                    "strategy":           e.strategy,
                    "recommended_action": e.recommended_action,
                }
                for e in self.events
            ],
            "pause_trading_recommended":    self.pause_trading_recommended,
            "emergency_stop_recommended":   self.emergency_stop_recommended,
            "recommended_stop_reason":     (
                self.recommended_stop_reason.value
                if self.recommended_stop_reason else None
            ),
            "window_seconds":               self.window_seconds,
            "total_audit_rows_inspected":   self.total_audit_rows_inspected,
            "total_emergency_events_inspected": self.total_emergency_events_inspected,
            "is_order_signal":              self.is_order_signal,
            "created_at":                   self.created_at.isoformat(),
        }


@dataclass(frozen=True)
class RiskAuditorInput:
    """`audit_risk()` 입력. caller가 미리 조회한 row를 dataclass로 주입."""

    audit_rows:           list[OrderAuditLog]
    emergency_events:     list[EmergencyStopEvent]
    agent_decisions:      list[AgentDecisionLog]
    daily_realized_pnl:   int   = 0
    max_daily_loss:       int   = 0    # 0 = 검사 비활성
    window_seconds:       int   = 3600  # 분석 윈도우 (default 1h)
    margin_risk_pct:      float | None = None  # 선물 증거금 위험 % (있으면)
    futures_liquidation_pct: float | None = None  # 강제청산 거리 %
    now:                  datetime | None = None


# ====================================================================
# Thresholds (조정 가능)
# ====================================================================


_DAILY_LOSS_HIGH_PCT          = 80   # daily_realized_pnl 절대값 / max_daily_loss
_DAILY_LOSS_CRITICAL_PCT      = 100
_REJECTED_BURST_THRESHOLD     = 5    # 윈도우 내 REJECTED row 수
_REJECTED_HIGH_THRESHOLD      = 10
_DUPLICATE_BURST_THRESHOLD    = 3
_BROKER_ERROR_THRESHOLD       = 3
_AGENT_WARN_THRESHOLD         = 5
_AI_LOW_CONF_THRESHOLD        = 30   # signal_confidence < 이 값
_AI_LOW_CONF_BURST            = 5
_AI_HIGH_CONF_REJECTED        = 80   # signal_confidence >= 이 값인데 REJECTED
_AI_HIGH_CONF_BURST           = 3
_EMERGENCY_FLAPPING_THRESHOLD = 4    # 윈도우 내 emergency toggle 수
_DATA_STALE_KEYWORDS          = ("stale", "old quote", "freshness")
_BROKER_ERR_KEYWORDS          = ("broker error", "broker_status",
                                  "connection", "timeout", "broker timeout")
_MARGIN_RISK_PCT_HIGH         = 30.0  # margin_risk_pct >= 30% → HIGH
_MARGIN_RISK_PCT_CRITICAL     = 50.0
_LIQUIDATION_DISTANCE_HIGH    = 7.0   # distance_pct <= 7% → HIGH
_LIQUIDATION_DISTANCE_CRITICAL = 3.0


# ====================================================================
# DB read-only helpers
# ====================================================================


def load_recent_audit_rows(
    db: Session, *, since: datetime, limit: int = 500,
) -> list[OrderAuditLog]:
    """최근 OrderAuditLog 조회 — read-only SELECT only."""
    if limit <= 0:
        return []
    stmt = (
        select(OrderAuditLog)
        .where(OrderAuditLog.created_at >= since)
        .order_by(OrderAuditLog.id.desc())
        .limit(int(limit))
    )
    return list(db.execute(stmt).scalars().all())


def load_recent_emergency_events(
    db: Session, *, since: datetime, limit: int = 100,
) -> list[EmergencyStopEvent]:
    if limit <= 0:
        return []
    stmt = (
        select(EmergencyStopEvent)
        .where(EmergencyStopEvent.created_at >= since)
        .order_by(EmergencyStopEvent.id.desc())
        .limit(int(limit))
    )
    return list(db.execute(stmt).scalars().all())


def load_recent_agent_decisions(
    db: Session, *, since: datetime, limit: int = 200,
) -> list[AgentDecisionLog]:
    if limit <= 0:
        return []
    stmt = (
        select(AgentDecisionLog)
        .where(AgentDecisionLog.created_at >= since)
        .order_by(AgentDecisionLog.id.desc())
        .limit(int(limit))
    )
    return list(db.execute(stmt).scalars().all())


# ====================================================================
# Pure analyzer
# ====================================================================


def audit_risk(inp: RiskAuditorInput) -> RiskAuditorReport:
    """순수 함수 — 입력 dataclass → RiskAuditorReport.

    DB / broker / 외부 호출 0건. caller가 미리 조회한 row를 전달해야 한다.
    데이터 부족 시 GREEN + summary "no events" friendly fallback (예외 X).
    """
    events: list[RiskEvent] = []

    # 1. DAILY_LOSS_BREACH — daily_realized_pnl 절대값 비율 검사
    pct = _daily_loss_pct(inp.daily_realized_pnl, inp.max_daily_loss)
    if pct is not None:
        if pct >= _DAILY_LOSS_CRITICAL_PCT:
            events.append(RiskEvent(
                type=RiskEventType.DAILY_LOSS_BREACH,
                severity=RiskEventSeverity.CRITICAL,
                summary=f"일일 손실 한도 100% 초과 도달 ({pct:.0f}%)",
                evidence={"pct": pct,
                           "daily_realized_pnl": inp.daily_realized_pnl,
                           "max_daily_loss": inp.max_daily_loss},
                recommended_action="EMERGENCY_STOP_RECOMMENDED + reason=daily_loss_limit",
            ))
        elif pct >= _DAILY_LOSS_HIGH_PCT:
            events.append(RiskEvent(
                type=RiskEventType.DAILY_LOSS_BREACH,
                severity=RiskEventSeverity.HIGH,
                summary=f"일일 손실 한도 {pct:.0f}% 도달 — 곧 한도 초과",
                evidence={"pct": pct,
                           "daily_realized_pnl": inp.daily_realized_pnl,
                           "max_daily_loss": inp.max_daily_loss},
                recommended_action="PAUSE_TRADING_RECOMMENDED",
            ))

    # 2. Audit row 분석 — REJECTED burst, duplicates, stale, broker error,
    #    AI overconfidence, AI low-confidence burst.
    rejected = [r for r in inp.audit_rows if r.decision == "REJECTED"]
    rej_count = len(rejected)
    if rej_count >= _REJECTED_HIGH_THRESHOLD:
        events.append(RiskEvent(
            type=RiskEventType.ABNORMAL_REJECTION_RATE,
            severity=RiskEventSeverity.HIGH,
            summary=f"윈도우 내 REJECTED 주문 {rej_count}건 — 비정상 거절률",
            evidence={"rejected_count": rej_count,
                       "total_audit_rows": len(inp.audit_rows)},
            recommended_action="PAUSE_TRADING_RECOMMENDED",
        ))
    elif rej_count >= _REJECTED_BURST_THRESHOLD:
        events.append(RiskEvent(
            type=RiskEventType.REPEATED_ORDER_FAILURE,
            severity=RiskEventSeverity.WARN,
            summary=f"윈도우 내 REJECTED 주문 {rej_count}건",
            evidence={"rejected_count": rej_count},
        ))

    # 2a. duplicates — reasons에 "duplicate" 포함
    dup_count = sum(
        1 for r in rejected
        if any("duplicate" in str(reason).lower()
               for reason in (r.reasons or []))
    )
    if dup_count >= _DUPLICATE_BURST_THRESHOLD:
        events.append(RiskEvent(
            type=RiskEventType.DUPLICATE_ORDER_BURST,
            severity=RiskEventSeverity.WARN,
            summary=f"중복 주문 거절 {dup_count}건 — 호출자 idempotency 점검 권장",
            evidence={"duplicate_count": dup_count},
        ))

    # 2b. stale data — reasons에 stale keyword 포함
    stale_count = sum(
        1 for r in rejected
        if any(any(kw in str(reason).lower() for kw in _DATA_STALE_KEYWORDS)
               for reason in (r.reasons or []))
    )
    if stale_count > 0:
        # >=3건이면 CRITICAL → EMERGENCY_STOP_RECOMMENDED, 그 미만이면 HIGH → PAUSE.
        is_critical = stale_count >= 3
        events.append(RiskEvent(
            type=RiskEventType.DATA_STALE,
            severity=(
                RiskEventSeverity.CRITICAL if is_critical
                else RiskEventSeverity.HIGH
            ),
            summary=f"시세 stale 검출 {stale_count}건",
            evidence={"stale_count": stale_count},
            recommended_action=(
                "EMERGENCY_STOP_RECOMMENDED + reason=data_stale"
                if is_critical else "PAUSE_TRADING_RECOMMENDED"
            ),
        ))

    # 2c. broker errors — reasons / message에 broker error keyword
    broker_err_count = sum(
        1 for r in rejected
        if any(kw in (str(r.message) + " "
                       + " ".join(str(x) for x in (r.reasons or []))).lower()
               for kw in _BROKER_ERR_KEYWORDS)
    )
    if broker_err_count >= _BROKER_ERROR_THRESHOLD:
        events.append(RiskEvent(
            type=RiskEventType.BROKER_ERROR_BURST,
            severity=RiskEventSeverity.HIGH,
            summary=f"broker error 누적 {broker_err_count}건",
            evidence={"broker_error_count": broker_err_count},
            recommended_action="PAUSE_TRADING_RECOMMENDED",
        ))

    # 2d. AI overconfidence — high conf인데 REJECTED 누적
    ai_overconf = [
        r for r in rejected
        if (r.requested_by_ai
            and (r.signal_confidence or 0) >= _AI_HIGH_CONF_REJECTED)
    ]
    if len(ai_overconf) >= _AI_HIGH_CONF_BURST:
        events.append(RiskEvent(
            type=RiskEventType.AI_OVERCONFIDENCE,
            severity=RiskEventSeverity.WARN,
            summary=(
                f"AI 고확신({_AI_HIGH_CONF_REJECTED}+) 주문이 거절된 건 "
                f"{len(ai_overconf)}건 — AI 판단 품질 점검 권장"
            ),
            evidence={"ai_high_conf_rejected_count": len(ai_overconf)},
        ))

    # 2e. AI low-conf burst — low confidence에서 신호 폭증
    ai_low_conf = [
        r for r in inp.audit_rows
        if (r.requested_by_ai
            and (r.signal_confidence or 0) > 0
            and (r.signal_confidence or 0) < _AI_LOW_CONF_THRESHOLD)
    ]
    if len(ai_low_conf) >= _AI_LOW_CONF_BURST:
        events.append(RiskEvent(
            type=RiskEventType.AI_LOW_CONFIDENCE_BURST,
            severity=RiskEventSeverity.INFO,
            summary=(
                f"낮은 confidence(<{_AI_LOW_CONF_THRESHOLD}) AI 신호 "
                f"{len(ai_low_conf)}건 — 신호 품질 검토"
            ),
            evidence={"ai_low_conf_count": len(ai_low_conf)},
        ))

    # 3. Emergency stop flapping — 짧은 시간 안 다수 토글
    if len(inp.emergency_events) >= _EMERGENCY_FLAPPING_THRESHOLD:
        events.append(RiskEvent(
            type=RiskEventType.EMERGENCY_STOP_FLAPPING,
            severity=RiskEventSeverity.HIGH,
            summary=(
                f"긴급정지 토글이 윈도우 내 {len(inp.emergency_events)}회 발생 — "
                "운영 절차 점검 권장"
            ),
            evidence={"toggle_count": len(inp.emergency_events)},
        ))

    # 4. Agent WARN burst — agent_decisions에서 WARN/REJECT 누적
    agent_warn_decisions = [
        d for d in inp.agent_decisions
        if str(getattr(d, "decision", "")).upper() in ("WARN", "REJECT")
    ]
    if len(agent_warn_decisions) >= _AGENT_WARN_THRESHOLD:
        events.append(RiskEvent(
            type=RiskEventType.AGENT_WARN_BURST,
            severity=RiskEventSeverity.WARN,
            summary=f"Agent WARN/REJECT 결정 {len(agent_warn_decisions)}건",
            evidence={"agent_warn_count": len(agent_warn_decisions)},
        ))

    # 5. Margin risk (선물 — 옵션 입력)
    if inp.margin_risk_pct is not None:
        if inp.margin_risk_pct >= _MARGIN_RISK_PCT_CRITICAL:
            events.append(RiskEvent(
                type=RiskEventType.MARGIN_RISK,
                severity=RiskEventSeverity.CRITICAL,
                summary=f"선물 증거금 위험 {inp.margin_risk_pct:.1f}%",
                evidence={"margin_risk_pct": inp.margin_risk_pct},
                recommended_action="EMERGENCY_STOP_RECOMMENDED + reason=margin_risk",
            ))
        elif inp.margin_risk_pct >= _MARGIN_RISK_PCT_HIGH:
            events.append(RiskEvent(
                type=RiskEventType.MARGIN_RISK,
                severity=RiskEventSeverity.HIGH,
                summary=f"선물 증거금 위험 {inp.margin_risk_pct:.1f}%",
                evidence={"margin_risk_pct": inp.margin_risk_pct},
                recommended_action="PAUSE_TRADING_RECOMMENDED",
            ))

    # 6. Futures liquidation distance (옵션)
    if inp.futures_liquidation_pct is not None:
        if inp.futures_liquidation_pct <= _LIQUIDATION_DISTANCE_CRITICAL:
            events.append(RiskEvent(
                type=RiskEventType.FUTURES_LIQUIDATION_RISK,
                severity=RiskEventSeverity.CRITICAL,
                summary=(
                    f"강제청산 거리 {inp.futures_liquidation_pct:.1f}% — "
                    "임박 위험"
                ),
                evidence={"distance_pct": inp.futures_liquidation_pct},
                recommended_action=(
                    "EMERGENCY_STOP_RECOMMENDED + reason=futures_liquidation_risk"
                ),
            ))
        elif inp.futures_liquidation_pct <= _LIQUIDATION_DISTANCE_HIGH:
            events.append(RiskEvent(
                type=RiskEventType.FUTURES_LIQUIDATION_RISK,
                severity=RiskEventSeverity.HIGH,
                summary=f"강제청산 거리 {inp.futures_liquidation_pct:.1f}% — 주의",
                evidence={"distance_pct": inp.futures_liquidation_pct},
                recommended_action="PAUSE_TRADING_RECOMMENDED",
            ))

    # 7. risk_score 산출 + audit_level / 권고 결정
    risk_score = _compute_risk_score(events)
    audit_level, pause_recommended, stop_recommended = (
        _derive_level_and_recommendation(events, risk_score)
    )
    stop_reason = _derive_stop_reason(events) if stop_recommended else None

    summary_lines = _build_summary_lines(
        audit_level=audit_level,
        events=events,
        risk_score=risk_score,
        pause=pause_recommended,
        stop=stop_recommended,
    )

    return RiskAuditorReport(
        audit_level=audit_level,
        risk_score=risk_score,
        summary_lines=summary_lines,
        events=events,
        pause_trading_recommended=pause_recommended,
        emergency_stop_recommended=stop_recommended,
        recommended_stop_reason=stop_reason,
        window_seconds=inp.window_seconds,
        total_audit_rows_inspected=len(inp.audit_rows),
        total_emergency_events_inspected=len(inp.emergency_events),
    )


# ====================================================================
# Helpers
# ====================================================================


def _daily_loss_pct(realized_pnl: int, max_loss: int) -> float | None:
    """negative realized_pnl이 max_loss 절대값에 차지하는 비율(%) 산출.

    realized_pnl이 양수(이익)이거나 max_loss=0이면 None (검사 비활성).
    """
    if max_loss <= 0:
        return None
    if realized_pnl >= 0:
        return 0.0
    return min(200.0, abs(realized_pnl) / abs(max_loss) * 100.0)


def _compute_risk_score(events: list[RiskEvent]) -> int:
    """이벤트별 severity 기반 score 누적. 0~100으로 clamp.

    INFO=2, WARN=8, HIGH=20, CRITICAL=40 — 합산 후 100으로 자름. 같은 type
    이 여러 번 등장하면 모두 합산 (중복 위험은 점수 가중).
    """
    weights = {
        RiskEventSeverity.INFO:     2,
        RiskEventSeverity.WARN:     8,
        RiskEventSeverity.HIGH:     20,
        RiskEventSeverity.CRITICAL: 40,
    }
    score = sum(weights[e.severity] for e in events)
    return max(0, min(100, score))


def _derive_level_and_recommendation(
    events: list[RiskEvent], risk_score: int,
) -> tuple[AuditLevel, bool, bool]:
    """events + risk_score → (audit_level, pause_recommended, stop_recommended).

    중지권한 우선:
    - CRITICAL 이벤트 1건 또는 risk_score >= 70 → RED + STOP
    - HIGH 이벤트 1건 또는 risk_score >= 40 → ORANGE + PAUSE
    - WARN/INFO만 → YELLOW
    - 이벤트 0건 → GREEN
    """
    has_critical = any(e.severity == RiskEventSeverity.CRITICAL for e in events)
    has_high     = any(e.severity == RiskEventSeverity.HIGH     for e in events)

    if has_critical or risk_score >= 70:
        return (AuditLevel.RED, True, True)
    if has_high or risk_score >= 40:
        return (AuditLevel.ORANGE, True, False)
    if events:
        return (AuditLevel.YELLOW, False, False)
    return (AuditLevel.GREEN, False, False)


def _derive_stop_reason(events: list[RiskEvent]) -> EmergencyStopReason | None:
    """STOP 권고 시 가장 적절한 EmergencyStopReason 매핑 (#153).

    CRITICAL 이벤트가 우선. 없으면 None — caller(운영자)가 reason 직접 선택.
    """
    type_to_reason = {
        RiskEventType.DAILY_LOSS_BREACH:        EmergencyStopReason.DAILY_LOSS_LIMIT,
        RiskEventType.DATA_STALE:                EmergencyStopReason.DATA_STALE,
        RiskEventType.BROKER_ERROR_BURST:        EmergencyStopReason.BROKER_ERROR,
        RiskEventType.REPEATED_ORDER_FAILURE:    EmergencyStopReason.REPEATED_ORDER_FAILURE,
        RiskEventType.AGENT_WARN_BURST:          EmergencyStopReason.AGENT_WARNING,
        RiskEventType.MARGIN_RISK:               EmergencyStopReason.MARGIN_RISK,
        RiskEventType.FUTURES_LIQUIDATION_RISK:  EmergencyStopReason.FUTURES_LIQUIDATION_RISK,
    }
    # severity 순으로 정렬해서 가장 critical한 매핑부터 검사.
    critical_first = sorted(
        events,
        key=lambda e: 0 if e.severity == RiskEventSeverity.CRITICAL else 1,
    )
    for e in critical_first:
        if e.type in type_to_reason:
            return type_to_reason[e.type]
    return None


def _build_summary_lines(
    *,
    audit_level: AuditLevel,
    events:      list[RiskEvent],
    risk_score:  int,
    pause:       bool,
    stop:        bool,
) -> list[str]:
    head = {
        AuditLevel.GREEN:  "리스크 감사: 정상.",
        AuditLevel.YELLOW: "리스크 감사: 경고 — 일부 이벤트 발생.",
        AuditLevel.ORANGE: "리스크 감사: 주의 — PAUSE_TRADING 권고.",
        AuditLevel.RED:    "리스크 감사: 긴급 — EMERGENCY_STOP 권고.",
    }[audit_level]
    lines = [head, f"위험 점수 {risk_score} / 100, 이벤트 {len(events)}건."]
    # 가장 심각한 이벤트 1~2개 surface.
    severity_order = {
        RiskEventSeverity.CRITICAL: 0,
        RiskEventSeverity.HIGH:     1,
        RiskEventSeverity.WARN:     2,
        RiskEventSeverity.INFO:     3,
    }
    sorted_events = sorted(events, key=lambda e: severity_order[e.severity])
    for e in sorted_events[:2]:
        lines.append(f"- [{e.severity.value}] {e.summary}")
    if stop:
        lines.append(
            "⚠ 긴급정지 권고 — 운영자가 Kill Switch UI에서 수동 토글 필요."
        )
    elif pause:
        lines.append(
            "⚠ 거래 일시 중단 권고 — 신규 진입 회피, 보유 포지션 모니터링."
        )
    lines.append(
        "본 리포트는 *주문 신호가 아닙니다*. 안전 감독 advisory 전용."
    )
    return lines


# ====================================================================
# AgentBase implementation (#51 호환)
# ====================================================================


class RiskAuditorAgent(AgentBase):
    """`AgentBase` 호환 implementation.

    `context.extra["risk_auditor_input"]`가 RiskAuditorInput이면 그대로 사용.
    그렇지 않으면 데이터 부족 → GREEN report. caller가 풍부한 입력을 사용
    하려면 `audit_risk(input)`을 직접 호출하는 패턴 권장.
    """

    @property
    def metadata(self) -> AgentMetadata:
        return AgentMetadata(
            name="risk_auditor_agent",
            role=AgentRole.RISK_AUDITOR,
            description=(
                "장중 리스크 위반 / 데이터 지연 / 주문 거절 / 손실 원인 / "
                "중복 주문 / AI 과신 / 선물 증거금 위험 감시. 위험 감지 시 "
                "PAUSE_TRADING / EMERGENCY_STOP *권고*만 — 실제 토글 X."
            ),
            inputs=[
                "audit_rows (OrderAuditLog)",
                "emergency_events (EmergencyStopEvent)",
                "agent_decisions (AgentDecisionLog)",
                "daily_realized_pnl, max_daily_loss",
                "margin_risk_pct, futures_liquidation_pct (선물 옵션)",
            ],
            outputs=["RiskAuditorReport (is_order_signal=False)"],
            forbidden=[
                "BUY / SELL / HOLD 주문 신호 반환 금지",
                "approval queue 등록 금지",
                "broker / OrderExecutor / route_order 호출 금지",
                "RiskManager.set_emergency_stop 직접 호출 금지 — 권고만",
                "DB INSERT / UPDATE / DELETE 금지 (read-only SELECT만)",
                "외부 AI / HTTP 호출 금지",
            ],
            can_execute_order=False,
        )

    def run(self, context: AgentContext) -> AgentOutput:
        inp = (context.extra or {}).get("risk_auditor_input")
        if inp is None or not isinstance(inp, RiskAuditorInput):
            inp = RiskAuditorInput(
                audit_rows=[], emergency_events=[], agent_decisions=[],
            )
        report = audit_risk(inp)
        risk_flags = []
        if report.audit_level == AuditLevel.RED:
            risk_flags.append("emergency_stop_recommended")
        elif report.audit_level == AuditLevel.ORANGE:
            risk_flags.append("pause_trading_recommended")
        if report.events:
            risk_flags.extend(
                f"event:{e.type.value}" for e in report.events[:3]
            )
        return AgentOutput(
            role=AgentRole.RISK_AUDITOR,
            decision=(
                AgentDecision.REJECT if report.emergency_stop_recommended
                else AgentDecision.WARN if report.pause_trading_recommended
                else AgentDecision.OBSERVE
            ),
            summary=(
                report.summary_lines[0] if report.summary_lines else "no events"
            ),
            reasons=list(report.summary_lines[1:4])
                if len(report.summary_lines) > 1 else [],
            confidence=None,
            risk_flags=risk_flags,
            metadata=report.to_dict(),
        )


# ====================================================================
# Module invariants
# ====================================================================
#
# - 본 모듈은 broker / OrderExecutor / route_order / KIS / mock_broker /
#   permission.gate / RiskManager.set_emergency_stop 어떤 모듈/함수도 호출
#   하지 않는다 (정적 grep 가드).
# - 외부 HTTP client / AI provider import 0건.
# - DB INSERT / UPDATE / DELETE 0건 — SELECT만 (정적 grep 가드).
# - `RiskAuditorReport.is_order_signal = False` 불변 (__post_init__ 가드).
# - PAUSE / STOP 권고는 *advisory* — caller가 실제 토글.

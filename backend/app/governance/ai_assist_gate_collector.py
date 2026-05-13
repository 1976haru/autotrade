"""AI Assist Gate collector — read-only DB → AIAssistGateInput.

CLAUDE.md 절대 원칙:
- 본 모듈은 *조회만* 한다 — INSERT/UPDATE/DELETE 0건 (정적 grep 가드).
- broker / OrderExecutor / route_order / 외부 HTTP / AI SDK import 0건.
- 안전 플래그 변경 / settings 직접 mutate 0건.

수집 대상:
- `OrderAuditLog`     : `trade_reason='ai_assist'` 또는 `requested_by_ai=True`
                       제안 row — proposal_count / decision / executed.
- `PendingApproval`   : 같은 audit_id 의 큐 상태 — operator approve/reject,
                       expired/cancelled.
- `EmergencyStopEvent`: 기간 내 긴급정지 발생 건수.
- `AgentDecisionLog`  : (확장) AI 결정 추적 — 본 PR에서는 보조 신호로만.

수익 메트릭(approved_expectancy / winning_pnl_sum / losing_pnl_sum)은 별도
trade ledger 또는 운영자가 산출 — collector는 호출자에게 그대로 위임.
"""

from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.models import (
    EmergencyStopEvent,
    OrderAuditLog,
    PendingApproval,
)
from app.governance.ai_assist_gate import AIAssistGateInput, AIAssistFailureReason


_AI_ASSIST_TRADE_REASON = "ai_assist"


def _utc(ts: datetime) -> datetime:
    if ts.tzinfo is None:
        return ts.replace(tzinfo=timezone.utc)
    return ts.astimezone(timezone.utc)


def _is_ai_assist_row(row: OrderAuditLog) -> bool:
    """OrderAuditLog row가 AI Assist 흐름에서 만들어졌는지 판별.

    `trade_reason='ai_assist'` (#44 sentinel) 또는 `requested_by_ai=True`
    + `ai_decision_meta` 둘 중 하나 만족.
    """
    tr = (row.trade_reason or "").lower()
    if tr == _AI_ASSIST_TRADE_REASON:
        return True
    if row.requested_by_ai and row.ai_decision_meta:
        # ai_decision_meta.source 가 AI 계열인지 추가 확인.
        meta = row.ai_decision_meta or {}
        src = str(meta.get("source", "")).upper()
        return src.startswith("AI")
    return False


def _classify_failure_reason(
    row: OrderAuditLog,
    pa: PendingApproval | None,
) -> AIAssistFailureReason | None:
    """단일 row의 실패 사유 태깅 — advisory only.

    BUY/SELL/HOLD 같은 *주문 신호*는 만들지 않는다. 본 함수는 reasons 텍스트의
    keyword 매칭만 한다.
    """
    decision = str(row.decision).upper()
    reasons_text = " ".join(str(r).lower() for r in (row.reasons or []))

    # 1) 운영자 거절 / 만료.
    if pa is not None:
        status = str(pa.status).upper()
        if status == "REJECTED":
            return AIAssistFailureReason.OPERATOR_REJECTED
        if status == "EXPIRED":
            return AIAssistFailureReason.APPROVAL_EXPIRED
        if status == "CANCELLED":
            return AIAssistFailureReason.APPROVAL_EXPIRED

    # 2) Risk reasons keyword.
    if decision == "REJECTED" or reasons_text:
        if "stale" in reasons_text or "freshness" in reasons_text:
            return AIAssistFailureReason.DATA_STALE
        if "emergency" in reasons_text or "stop" in reasons_text:
            return AIAssistFailureReason.EMERGENCY_STOP
        if "duplicate" in reasons_text or "cooldown" in reasons_text:
            return AIAssistFailureReason.DUPLICATE_OR_COOLDOWN
        if "max_daily_loss" in reasons_text or "notional" in reasons_text \
                or "max_positions" in reasons_text or "exposure" in reasons_text:
            return AIAssistFailureReason.RISK_LIMIT
        if "liquidity" in reasons_text or "volume" in reasons_text:
            return AIAssistFailureReason.LIQUIDITY
        if "regime" in reasons_text:
            return AIAssistFailureReason.REGIME_MISMATCH
        if "theme" in reasons_text or "news" in reasons_text or "overheated" in reasons_text:
            return AIAssistFailureReason.NEWS_OR_THEME_OVERHEATED
        if "price" in reasons_text and "gap" in reasons_text:
            return AIAssistFailureReason.PRICE_GAP
        if "confidence" in reasons_text or "low_quality" in reasons_text:
            return AIAssistFailureReason.LOW_CONFIDENCE

    # 3) AI meta confidence.
    meta = row.ai_decision_meta or {}
    if decision == "REJECTED":
        try:
            conf = float(meta.get("confidence", 100))
        except (TypeError, ValueError):
            conf = 100.0
        if conf < 50:
            return AIAssistFailureReason.LOW_CONFIDENCE
        return AIAssistFailureReason.UNCATEGORIZED

    return None


def _confidence_calibration(
    rows: list[OrderAuditLog],
    *,
    approved_pnls:  dict[int, int] | None = None,
) -> tuple[float, float | None]:
    """confidence ↔ approved row 결과 일치도 (0~1) + 평균 confidence.

    `approved_pnls`: audit.id → pnl(절대 정수). 미제공 시 calibration=0 반환
    (입력 부족) — 호출자가 운영자 입력으로 보강.

    단순 휴리스틱:
    - 각 승인 row의 confidence (0~100) 와 pnl 부호를 비교.
    - confidence ≥ 70 + pnl > 0 → 일치
    - confidence ≥ 70 + pnl ≤ 0 → 불일치
    - confidence <  70 + pnl ≤ 0 → 일치 (보수적 판단 옳음)
    - confidence <  70 + pnl > 0 → 불일치
    """
    confs: list[float] = []
    matches = 0
    total = 0
    for row in rows:
        meta = row.ai_decision_meta or {}
        try:
            c = float(meta.get("confidence", -1))
        except (TypeError, ValueError):
            c = -1.0
        if c < 0:
            continue
        confs.append(c)
        if approved_pnls is not None and row.id in approved_pnls:
            pnl = approved_pnls[row.id]
            won = pnl > 0
            high_conf = c >= 70.0
            agree = (won and high_conf) or ((not won) and (not high_conf))
            matches += 1 if agree else 0
            total   += 1
    avg = (sum(confs) / len(confs)) if confs else None
    calibration = (matches / total) if total > 0 else 0.0
    return calibration, avg


def build_ai_assist_gate_input(
    db: Session,
    *,
    strategy: str | None,
    period_start: datetime,
    period_end: datetime,
    approved_expectancy:        float | None = None,
    approved_winning_pnl_sum:   int   | None = None,
    approved_losing_pnl_sum:    int   | None = None,
    approved_win_count:         int   | None = None,
    approved_loss_count:        int   | None = None,
    approved_pnls:              dict[int, int] | None = None,
    rejected_but_would_have_won: int   = 0,
    active_days_override:       int   | None = None,
    ai_decision_audit_drift:    int   = 0,
) -> AIAssistGateInput:
    """OrderAuditLog + PendingApproval 에서 AI Assist 통계 산출.

    수익 메트릭은 별도 trade ledger / 운영자 입력으로만 채울 수 있다 (`approved_*`
    인자 명시 권장). 미제공 시 0 — 평가 결과는 보수적으로 FAIL 처리될 가능성.
    """
    start = _utc(period_start)
    end   = _utc(period_end)

    # ---- 1) audit rows ----
    stmt = select(OrderAuditLog).where(
        OrderAuditLog.created_at >= start,
        OrderAuditLog.created_at <= end,
    )
    if strategy:
        stmt = stmt.where(OrderAuditLog.strategy == strategy)
    audit_rows = [r for r in db.execute(stmt).scalars() if _is_ai_assist_row(r)]

    # ---- 2) PendingApproval rows for audit_ids ----
    audit_ids = [r.id for r in audit_rows]
    pa_by_audit: dict[int, PendingApproval] = {}
    if audit_ids:
        pa_stmt = select(PendingApproval).where(
            PendingApproval.audit_id.in_(audit_ids),
        )
        for pa in db.execute(pa_stmt).scalars():
            pa_by_audit[pa.audit_id] = pa

    proposal_count          = len(audit_rows)
    approved_proposals      = 0
    risk_rejected           = 0
    operator_rejected       = 0
    expired_or_cancelled    = 0
    failure_reason_counts: dict[str, int] = {}

    approved_rows: list[OrderAuditLog] = []
    for row in audit_rows:
        decision = str(row.decision).upper()
        pa = pa_by_audit.get(row.id)
        # 운영자가 결정한 경우.
        if pa is not None:
            status = str(pa.status).upper()
            if status == "APPROVED":
                approved_proposals += 1
                approved_rows.append(row)
            elif status == "REJECTED":
                operator_rejected += 1
            elif status in ("EXPIRED", "CANCELLED"):
                expired_or_cancelled += 1
            else:
                # 아직 PENDING.
                pass
        else:
            # PA 없음 — RiskManager가 사전 거절했거나, 직접 승인 흐름.
            if decision == "REJECTED":
                risk_rejected += 1
            elif decision == "APPROVED" and row.executed:
                # 매우 드문 경우 (사전 자동 승인) — approved 로 카운트.
                approved_proposals += 1
                approved_rows.append(row)

        tag = _classify_failure_reason(row, pa)
        if tag is not None:
            failure_reason_counts[tag.value] = (
                failure_reason_counts.get(tag.value, 0) + 1
            )

    # ---- 3) confidence calibration ----
    calibration, avg_conf = _confidence_calibration(
        approved_rows, approved_pnls=approved_pnls,
    )

    # ---- 4) 긴급정지 ----
    es_stmt = select(EmergencyStopEvent).where(
        EmergencyStopEvent.created_at >= start,
        EmergencyStopEvent.created_at <= end,
    )
    emergency_count = sum(1 for _ in db.execute(es_stmt).scalars())

    # ---- 5) active days ----
    if active_days_override is not None:
        active_days = int(active_days_override)
    else:
        active_days = len({_utc(r.created_at).date() for r in audit_rows})

    return AIAssistGateInput(
        strategy_name=strategy or "(all AI Assist strategies)",
        period_start=start,
        period_end=end,
        proposal_count=proposal_count,
        approved_proposals=approved_proposals,
        risk_rejected_proposals=risk_rejected,
        operator_rejected_proposals=operator_rejected,
        expired_or_cancelled=expired_or_cancelled,
        approved_expectancy=float(approved_expectancy or 0.0),
        approved_winning_pnl_sum=int(approved_winning_pnl_sum or 0),
        approved_losing_pnl_sum=int(approved_losing_pnl_sum or 0),
        approved_win_count=int(approved_win_count or 0),
        approved_loss_count=int(approved_loss_count or 0),
        confidence_calibration=float(calibration),
        avg_confidence=avg_conf,
        rejected_but_would_have_won=int(rejected_but_would_have_won),
        ai_decision_audit_drift=int(ai_decision_audit_drift),
        emergency_stops_in_period=int(emergency_count),
        active_days=active_days,
        failure_reason_counts=failure_reason_counts,
    )


def list_ai_assist_strategies(
    db: Session,
    *,
    period_start: datetime,
    period_end: datetime,
) -> list[str]:
    """기간 내 AI Assist row 가 발견된 strategy 이름 목록."""
    start = _utc(period_start)
    end   = _utc(period_end)
    stmt = select(OrderAuditLog).where(
        OrderAuditLog.created_at >= start,
        OrderAuditLog.created_at <= end,
    )
    names = set()
    for r in db.execute(stmt).scalars():
        if _is_ai_assist_row(r):
            names.add(r.strategy or "(unnamed)")
    return sorted(names)

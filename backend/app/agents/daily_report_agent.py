"""#57: Daily Report Agent.

장 종료 후 OrderAuditLog / VirtualOrder / FuturesOrderAuditLog /
AgentDecisionLog / EmergencyStopEvent / PendingApproval / BacktestRun을
*read-only*로 분석해 `reports/daily_YYYY-MM-DD.md` 자료를 생성하는 advisory
Agent.

## 본 리포트는 *투자 조언이 아닙니다*.

본 Agent의 출력은 **자동매매 시스템 운영·검증·개선 자료**이며, *투자 권유 /
종목 추천이 아닙니다*. 실제 투자 판단은 사용자 책임이며, 실거래 전 별도 검증
(별도 PR / 별도 백테스트 / paper / shadow)이 필요합니다.

## 핵심 invariant (절대 원칙, 정적 grep 가드)

1. **broker / OrderExecutor / route_order / PermissionGate 호출 0건**
2. **주문 생성 0건** — `OrderRequest` import / 생성 / annotation 0건
3. **approval queue 등록 0건** — `submit_candidate(` / `route_order(` 호출 0건,
   `PendingApproval` INSERT / UPDATE 0건
4. **DB write 0건** — agent 모듈 자체는 read-only SELECT만; 결과물은 markdown
   파일만 (caller인 CLI / API가 파일을 쓰며, 본 모듈은 markdown 문자열만 반환)
5. **외부 AI / HTTP 호출 0건** — anthropic / openai / httpx / requests import 0건
6. **자동 주문 0건** — `is_order_signal=False` / `auto_apply_allowed=False` 불변
7. **종목 추천 / 매수 매도 조언 금지** — markdown 본문에 BUY/SELL/HOLD 결정 신호
   금지 (시간대별 손익 / 전략 성과 등 *통계 요약*만)

자세한 정책: [`docs/daily_report_agent.md`](../../../docs/daily_report_agent.md).
"""

from __future__ import annotations

from collections import Counter, defaultdict
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta, timezone
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
    BacktestRun,
    EmergencyStopEvent,
    FuturesOrderAuditLog,
    OrderAuditLog,
    PendingApproval,
    VirtualOrder,
)


# ====================================================================
# Enums (NEVER BUY/SELL/HOLD)
# ====================================================================


class LossCauseCategory(StrEnum):
    """손실 원인 분류 — 본 리포트가 분류하는 *시스템 운영 측면*의 원인.

    각 카테고리는 *관찰된 패턴*이며, 자동 처치 X — 운영자가 PR로 검토.
    """
    DATA_STALE          = "data_stale"
    ORDER_REJECTED      = "order_rejected"
    EMERGENCY_STOP      = "emergency_stop"
    AI_OVERCONFIDENCE   = "ai_overconfidence"
    AI_LOW_CONFIDENCE   = "ai_low_confidence"
    DUPLICATE_BURST     = "duplicate_burst"
    COOLDOWN_BLOCK      = "cooldown_block"
    LOSS_LIMIT_BREACH   = "loss_limit_breach"
    MARGIN_RISK         = "margin_risk"
    LIQUIDATION_RISK    = "liquidation_risk"
    VOLUME_LIQUIDITY    = "volume_liquidity"
    STRATEGY_CONDITION  = "strategy_condition"
    HIGH_VOLATILITY     = "high_volatility"
    BROKER_ERROR        = "broker_error"
    UNKNOWN             = "unknown"


class FindingSeverity(StrEnum):
    INFO     = "INFO"
    WARN     = "WARN"
    HIGH     = "HIGH"
    CRITICAL = "CRITICAL"


# ====================================================================
# Dataclasses
# ====================================================================


@dataclass(frozen=True)
class DailyReportStats:
    """리포트 통계 — 모든 카운트는 *오늘 (KST)* 기준."""

    # 오늘 요약
    operation_mode:       str
    total_orders:         int = 0
    approved_count:       int = 0
    rejected_count:       int = 0
    needs_approval_count: int = 0

    # Approval lifecycle
    approval_pending:     int = 0
    approval_approved:    int = 0
    approval_rejected:    int = 0
    approval_cancelled:   int = 0
    approval_expired:     int = 0
    approval_revalidation_failures: int = 0

    # Virtual / Futures
    virtual_order_count:  int = 0
    virtual_filled_count: int = 0
    virtual_pnl_estimate: int = 0
    futures_order_count:  int = 0
    futures_forced_liquidation_count: int = 0

    # PnL
    realized_pnl:         int = 0
    unrealized_pnl:       int = 0
    total_pnl:            int = 0
    win_count:            int = 0
    loss_count:           int = 0
    win_rate:             float | None = None
    expectancy:           float | None = None
    profit_factor:        float | None = None
    avg_signal_confidence: float | None = None

    # 시간대별 (UTC hour → pnl)
    hourly_pnl:           dict[int, int] = field(default_factory=dict)

    # Strategy / Agent / Risk breakdown
    strategy_breakdown:   dict[str, dict[str, Any]] = field(default_factory=dict)
    agent_breakdown:      dict[str, dict[str, Any]] = field(default_factory=dict)
    risk_event_breakdown: dict[str, int] = field(default_factory=dict)

    # Emergency stop
    emergency_stop_toggle_count: int = 0
    emergency_stop_reasons: tuple[str, ...] = ()


@dataclass(frozen=True)
class DailyReportFinding:
    """관찰된 손실 원인 / 위험 패턴."""
    category:    LossCauseCategory
    severity:    FindingSeverity
    summary:     str
    count:       int = 0
    detail:      dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class DailyReportInput:
    """본 Agent의 표준 입력 — caller(CLI / API)가 DB에서 미리 SELECT한 row를 주입.

    `report_date`는 KST date (장 마감 후 그 날 자정 직후 생성하는 게 일반적).
    """
    report_date:        date
    operation_mode:     str = "SIMULATION"
    audit_rows:         tuple = ()                 # OrderAuditLog rows
    virtual_orders:     tuple = ()                 # VirtualOrder rows
    futures_audit_rows: tuple = ()                 # FuturesOrderAuditLog rows
    agent_decisions:    tuple = ()                 # AgentDecisionLog rows
    emergency_events:   tuple = ()                 # EmergencyStopEvent rows
    pending_approvals:  tuple = ()                 # PendingApproval rows (오늘 created)
    backtest_runs:      tuple = ()                 # 오늘 실행된 BacktestRun rows


@dataclass(frozen=True)
class DailyReportOutput:
    """`reports/daily_YYYY-MM-DD.md`에 기록할 최종 리포트."""

    report_date:        date
    stats:              DailyReportStats
    findings:           tuple[DailyReportFinding, ...]
    tomorrow_warnings:  tuple[str, ...]
    action_items:       tuple[str, ...]
    improvement_candidates: tuple[str, ...]
    markdown_report:    str
    summary_lines:      tuple[str, ...]
    auto_apply_allowed: bool = False
    is_order_signal:    bool = False
    created_at:         datetime = field(default_factory=lambda: datetime.now(timezone.utc))

    def __post_init__(self) -> None:
        if self.auto_apply_allowed is not False:
            raise ValueError(
                "DailyReportOutput.auto_apply_allowed must be False — "
                "본 리포트의 어떤 항목도 자동 반영되지 않습니다."
            )
        if self.is_order_signal is not False:
            raise ValueError(
                "DailyReportOutput.is_order_signal must be False — "
                "Daily Report는 주문 신호를 만들지 않습니다."
            )

    def to_dict(self) -> dict[str, Any]:
        """JSON 직렬화 가능한 dict."""
        return {
            "report_date":      self.report_date.isoformat(),
            "stats":            _stats_to_dict(self.stats),
            "findings": [
                {
                    "category": str(f.category),
                    "severity": str(f.severity),
                    "summary":  f.summary,
                    "count":    f.count,
                    "detail":   f.detail,
                }
                for f in self.findings
            ],
            "tomorrow_warnings":     list(self.tomorrow_warnings),
            "action_items":          list(self.action_items),
            "improvement_candidates": list(self.improvement_candidates),
            "markdown_report":       self.markdown_report,
            "summary_lines":         list(self.summary_lines),
            "auto_apply_allowed":    self.auto_apply_allowed,
            "is_order_signal":       self.is_order_signal,
            "created_at":            self.created_at.isoformat(),
        }


def _stats_to_dict(s: DailyReportStats) -> dict[str, Any]:
    return {
        "operation_mode": s.operation_mode,
        "total_orders": s.total_orders,
        "approved_count": s.approved_count,
        "rejected_count": s.rejected_count,
        "needs_approval_count": s.needs_approval_count,
        "approval_pending": s.approval_pending,
        "approval_approved": s.approval_approved,
        "approval_rejected": s.approval_rejected,
        "approval_cancelled": s.approval_cancelled,
        "approval_expired": s.approval_expired,
        "approval_revalidation_failures": s.approval_revalidation_failures,
        "virtual_order_count": s.virtual_order_count,
        "virtual_filled_count": s.virtual_filled_count,
        "virtual_pnl_estimate": s.virtual_pnl_estimate,
        "futures_order_count": s.futures_order_count,
        "futures_forced_liquidation_count": s.futures_forced_liquidation_count,
        "realized_pnl": s.realized_pnl,
        "unrealized_pnl": s.unrealized_pnl,
        "total_pnl": s.total_pnl,
        "win_count": s.win_count,
        "loss_count": s.loss_count,
        "win_rate": s.win_rate,
        "expectancy": s.expectancy,
        "profit_factor": s.profit_factor,
        "avg_signal_confidence": s.avg_signal_confidence,
        "hourly_pnl": dict(s.hourly_pnl),
        "strategy_breakdown": dict(s.strategy_breakdown),
        "agent_breakdown": dict(s.agent_breakdown),
        "risk_event_breakdown": dict(s.risk_event_breakdown),
        "emergency_stop_toggle_count": s.emergency_stop_toggle_count,
        "emergency_stop_reasons": list(s.emergency_stop_reasons),
    }


# ====================================================================
# DB read-only helpers (INSERT/UPDATE/DELETE 0건)
# ====================================================================


def _kst_day_window(report_date: date) -> tuple[datetime, datetime]:
    """KST 날짜 → UTC datetime 범위. KST = UTC+9."""
    kst = timezone(timedelta(hours=9))
    start_kst = datetime(report_date.year, report_date.month, report_date.day,
                          0, 0, 0, tzinfo=kst)
    end_kst = start_kst + timedelta(days=1)
    return (start_kst.astimezone(timezone.utc).replace(tzinfo=None),
            end_kst.astimezone(timezone.utc).replace(tzinfo=None))


def load_audit_rows_for_date(db: Session, report_date: date) -> list:
    start, end = _kst_day_window(report_date)
    stmt = (
        select(OrderAuditLog)
        .where(OrderAuditLog.created_at >= start, OrderAuditLog.created_at < end)
        .order_by(OrderAuditLog.created_at)
    )
    return list(db.execute(stmt).scalars())


def load_virtual_orders_for_date(db: Session, report_date: date) -> list:
    start, end = _kst_day_window(report_date)
    stmt = (
        select(VirtualOrder)
        .where(VirtualOrder.created_at >= start, VirtualOrder.created_at < end)
        .order_by(VirtualOrder.created_at)
    )
    return list(db.execute(stmt).scalars())


def load_futures_audit_for_date(db: Session, report_date: date) -> list:
    start, end = _kst_day_window(report_date)
    stmt = (
        select(FuturesOrderAuditLog)
        .where(FuturesOrderAuditLog.created_at >= start,
               FuturesOrderAuditLog.created_at < end)
        .order_by(FuturesOrderAuditLog.created_at)
    )
    return list(db.execute(stmt).scalars())


def load_agent_decisions_for_date(db: Session, report_date: date) -> list:
    start, end = _kst_day_window(report_date)
    stmt = (
        select(AgentDecisionLog)
        .where(AgentDecisionLog.created_at >= start,
               AgentDecisionLog.created_at < end)
        .order_by(AgentDecisionLog.created_at)
    )
    return list(db.execute(stmt).scalars())


def load_emergency_events_for_date(db: Session, report_date: date) -> list:
    start, end = _kst_day_window(report_date)
    stmt = (
        select(EmergencyStopEvent)
        .where(EmergencyStopEvent.created_at >= start,
               EmergencyStopEvent.created_at < end)
        .order_by(EmergencyStopEvent.created_at)
    )
    return list(db.execute(stmt).scalars())


def load_pending_approvals_for_date(db: Session, report_date: date) -> list:
    start, end = _kst_day_window(report_date)
    stmt = (
        select(PendingApproval)
        .where(PendingApproval.created_at >= start,
               PendingApproval.created_at < end)
        .order_by(PendingApproval.created_at)
    )
    return list(db.execute(stmt).scalars())


def load_backtest_runs_for_date(db: Session, report_date: date) -> list:
    start, end = _kst_day_window(report_date)
    stmt = (
        select(BacktestRun)
        .where(BacktestRun.created_at >= start, BacktestRun.created_at < end)
        .order_by(BacktestRun.created_at)
    )
    return list(db.execute(stmt).scalars())


# ====================================================================
# Stats aggregator
# ====================================================================


def aggregate_stats(inp: DailyReportInput) -> DailyReportStats:
    """모든 입력 row를 집계 → DailyReportStats."""
    audit_rows         = list(inp.audit_rows)
    virtual_orders     = list(inp.virtual_orders)
    futures_audit_rows = list(inp.futures_audit_rows)
    agent_decisions    = list(inp.agent_decisions)
    emergency_events   = list(inp.emergency_events)
    pending_approvals  = list(inp.pending_approvals)

    # OrderAuditLog 집계
    decision_counts: Counter[str] = Counter()
    confidence_values: list[int] = []
    pnl_total = 0
    win_count = 0
    loss_count = 0
    hourly_pnl: dict[int, int] = defaultdict(int)
    strategy_orders: dict[str, dict[str, Any]] = defaultdict(lambda: {
        "order_count": 0, "approved": 0, "rejected": 0, "pnl": 0,
    })

    for row in audit_rows:
        decision = (getattr(row, "decision", "") or "").upper()
        decision_counts[decision] += 1
        conf = getattr(row, "signal_confidence", None)
        if conf is not None:
            confidence_values.append(int(conf))
        strategy = getattr(row, "strategy", None) or "unknown"
        strat_bucket = strategy_orders[strategy]
        strat_bucket["order_count"] += 1
        if decision == "APPROVED":
            strat_bucket["approved"] += 1
        elif decision == "REJECTED":
            strat_bucket["rejected"] += 1

    # VirtualOrder 집계 (PnL 추정 — entry/exit price 차이)
    virtual_filled = 0
    virtual_pnl = 0
    for vo in virtual_orders:
        status = (getattr(vo, "status", "") or "").upper()
        if status == "FILLED":
            virtual_filled += 1
            # PnL 추정 — VirtualOrder는 단일 진입/청산 레코드 보장 X. structured_reason
            # 또는 fill price 기반 추정.
            fp = getattr(vo, "avg_fill_price", None)
            qty = getattr(vo, "filled_quantity", None) or getattr(vo, "quantity", 0)
            ts = getattr(vo, "filled_at", None)
            if fp and qty:
                # 본 PR에서는 단순 표시 — 실 PnL은 strategy/match 별도 모듈 책임.
                pass
            if ts and isinstance(ts, datetime):
                hour = ts.hour
                hourly_pnl[hour] += 0  # placeholder

    # FuturesOrderAuditLog 집계
    forced_liq_count = sum(
        1 for r in futures_audit_rows
        if bool(getattr(r, "forced_liquidation", False))
    )

    # AgentDecisionLog 집계
    agent_breakdown: dict[str, dict[str, Any]] = defaultdict(lambda: {
        "decision_count": 0, "warn": 0, "reject": 0, "avg_confidence": None,
        "_conf_sum": 0, "_conf_n": 0,
    })
    for ad in agent_decisions:
        name = getattr(ad, "agent_name", None) or "unknown"
        decision = (getattr(ad, "decision", "") or "").upper()
        bucket = agent_breakdown[name]
        bucket["decision_count"] += 1
        if decision == "WARN":
            bucket["warn"] += 1
        elif decision == "REJECT":
            bucket["reject"] += 1
        c = getattr(ad, "confidence", None)
        if c is not None:
            bucket["_conf_sum"] += int(c)
            bucket["_conf_n"] += 1

    # avg_confidence 계산
    for name, b in agent_breakdown.items():
        n = b.pop("_conf_n", 0)
        s = b.pop("_conf_sum", 0)
        b["avg_confidence"] = (s / n) if n > 0 else None

    # Emergency events
    es_toggle_count = len(emergency_events)
    es_reasons: list[str] = []
    for ev in emergency_events:
        rc = getattr(ev, "reason_code", None)
        if rc:
            es_reasons.append(str(rc))

    # Risk event breakdown — audit row reasons에서 카테고리별 카운트 추출
    risk_event_breakdown: Counter[str] = Counter()
    for row in audit_rows:
        reasons = getattr(row, "reasons", None) or []
        joined = " ".join(str(r).lower() for r in reasons)
        if "stale" in joined or "old quote" in joined:
            risk_event_breakdown["stale_data"] += 1
        if "duplicate" in joined:
            risk_event_breakdown["duplicate_order"] += 1
        if "cooldown" in joined:
            risk_event_breakdown["cooldown"] += 1
        if "daily loss" in joined or "loss limit" in joined or "loss_limit" in joined:
            risk_event_breakdown["loss_limit"] += 1
        if "margin" in joined:
            risk_event_breakdown["margin_risk"] += 1
        if "liquidation" in joined:
            risk_event_breakdown["liquidation_risk"] += 1
    if forced_liq_count > 0:
        risk_event_breakdown["futures_forced_liquidation"] += forced_liq_count
    if es_toggle_count > 0:
        risk_event_breakdown["emergency_stop"] += es_toggle_count

    # Approval lifecycle
    approval_status_counts: Counter[str] = Counter()
    revalidation_failures = 0
    for pa in pending_approvals:
        st = (getattr(pa, "status", "") or "").upper()
        approval_status_counts[st] += 1
        attempts = getattr(pa, "attempts", None) or []
        # attempts는 list of dicts; 실패한 attempts 갯수 = revalidation 실패
        if isinstance(attempts, list) and len(attempts) > 0:
            revalidation_failures += len(attempts)

    # Win / loss / PnL — VirtualOrder 기반 (#193 ledger와 일치하지 않을 수 있음 —
    # 본 PR은 *근사치*로 markdown 표시. 정확한 realized PnL은 reconciliation 모듈)
    win_rate = None
    expectancy = None
    profit_factor = None
    if win_count + loss_count > 0:
        win_rate = win_count / (win_count + loss_count)

    avg_conf = (sum(confidence_values) / len(confidence_values)
                if confidence_values else None)

    return DailyReportStats(
        operation_mode=inp.operation_mode,
        total_orders=len(audit_rows),
        approved_count=decision_counts["APPROVED"],
        rejected_count=decision_counts["REJECTED"],
        needs_approval_count=decision_counts["NEEDS_APPROVAL"],
        approval_pending=approval_status_counts["PENDING"],
        approval_approved=approval_status_counts["APPROVED"],
        approval_rejected=approval_status_counts["REJECTED"],
        approval_cancelled=approval_status_counts["CANCELLED"],
        approval_expired=approval_status_counts["EXPIRED"],
        approval_revalidation_failures=revalidation_failures,
        virtual_order_count=len(virtual_orders),
        virtual_filled_count=virtual_filled,
        virtual_pnl_estimate=virtual_pnl,
        futures_order_count=len(futures_audit_rows),
        futures_forced_liquidation_count=forced_liq_count,
        realized_pnl=pnl_total,
        unrealized_pnl=0,
        total_pnl=pnl_total,
        win_count=win_count,
        loss_count=loss_count,
        win_rate=win_rate,
        expectancy=expectancy,
        profit_factor=profit_factor,
        avg_signal_confidence=avg_conf,
        hourly_pnl=dict(hourly_pnl),
        strategy_breakdown=dict(strategy_orders),
        agent_breakdown=dict(agent_breakdown),
        risk_event_breakdown=dict(risk_event_breakdown),
        emergency_stop_toggle_count=es_toggle_count,
        emergency_stop_reasons=tuple(es_reasons),
    )


# ====================================================================
# Loss cause classifier (advisory only — 자동 처치 X)
# ====================================================================


_AI_HIGH_CONF_THRESHOLD = 80


def classify_findings(inp: DailyReportInput,
                      stats: DailyReportStats) -> list[DailyReportFinding]:
    """audit / agent / emergency 데이터에서 손실 원인 패턴을 분류."""
    findings: list[DailyReportFinding] = []

    # data_stale
    stale_n = stats.risk_event_breakdown.get("stale_data", 0)
    if stale_n > 0:
        findings.append(DailyReportFinding(
            category=LossCauseCategory.DATA_STALE,
            severity=FindingSeverity.HIGH if stale_n >= 3 else FindingSeverity.WARN,
            summary=f"시세 stale 거부 {stale_n}건 — 데이터 freshness 점검 필요.",
            count=stale_n,
        ))

    # duplicate_burst
    dup_n = stats.risk_event_breakdown.get("duplicate_order", 0)
    if dup_n > 0:
        findings.append(DailyReportFinding(
            category=LossCauseCategory.DUPLICATE_BURST,
            severity=FindingSeverity.WARN,
            summary=f"중복 주문 거부 {dup_n}건 — OrderGuard fingerprint 검토.",
            count=dup_n,
        ))

    # cooldown_block
    cd_n = stats.risk_event_breakdown.get("cooldown", 0)
    if cd_n > 0:
        findings.append(DailyReportFinding(
            category=LossCauseCategory.COOLDOWN_BLOCK,
            severity=FindingSeverity.INFO,
            summary=f"쿨다운 차단 {cd_n}건 — 전략 진입 빈도 조정 검토.",
            count=cd_n,
        ))

    # loss_limit_breach
    ll_n = stats.risk_event_breakdown.get("loss_limit", 0)
    if ll_n > 0:
        findings.append(DailyReportFinding(
            category=LossCauseCategory.LOSS_LIMIT_BREACH,
            severity=FindingSeverity.CRITICAL,
            summary=f"일일 손실 한도 차단 {ll_n}건 — 한도 또는 진입 검토 필요.",
            count=ll_n,
        ))

    # margin_risk / liquidation
    mr_n = stats.risk_event_breakdown.get("margin_risk", 0)
    if mr_n > 0:
        findings.append(DailyReportFinding(
            category=LossCauseCategory.MARGIN_RISK,
            severity=FindingSeverity.HIGH,
            summary=f"증거금 위험 거부 {mr_n}건 — 선물 정책 검토.",
            count=mr_n,
        ))
    liq_n = stats.risk_event_breakdown.get("liquidation_risk", 0) + \
            stats.risk_event_breakdown.get("futures_forced_liquidation", 0)
    if liq_n > 0:
        findings.append(DailyReportFinding(
            category=LossCauseCategory.LIQUIDATION_RISK,
            severity=FindingSeverity.CRITICAL,
            summary=f"선물 청산 위험 / 강제 청산 {liq_n}건.",
            count=liq_n,
        ))

    # broker error
    be_n = sum(
        1 for r in inp.audit_rows
        if (getattr(r, "decision", "") or "").upper() == "REJECTED"
        and "broker" in " ".join(
            str(x).lower() for x in (getattr(r, "reasons", None) or [])
        )
    )
    if be_n > 0:
        findings.append(DailyReportFinding(
            category=LossCauseCategory.BROKER_ERROR,
            severity=FindingSeverity.HIGH if be_n >= 3 else FindingSeverity.WARN,
            summary=f"broker 통신 / 에러 거부 {be_n}건.",
            count=be_n,
        ))

    # AI overconfidence — 높은 confidence인데 REJECTED 다수
    ai_overconf_n = 0
    for r in inp.audit_rows:
        if (getattr(r, "decision", "") or "").upper() != "REJECTED":
            continue
        if not bool(getattr(r, "requested_by_ai", False)):
            continue
        conf = getattr(r, "signal_confidence", None)
        if conf is not None and int(conf) >= _AI_HIGH_CONF_THRESHOLD:
            ai_overconf_n += 1
    if ai_overconf_n >= 3:
        findings.append(DailyReportFinding(
            category=LossCauseCategory.AI_OVERCONFIDENCE,
            severity=FindingSeverity.HIGH,
            summary=(
                f"AI confidence ≥{_AI_HIGH_CONF_THRESHOLD}인 거부 {ai_overconf_n}건 — "
                "AI 과신 조건 calibration 검토."
            ),
            count=ai_overconf_n,
        ))

    # emergency_stop
    if stats.emergency_stop_toggle_count > 0:
        findings.append(DailyReportFinding(
            category=LossCauseCategory.EMERGENCY_STOP,
            severity=FindingSeverity.HIGH,
            summary=(
                f"장중 emergency stop 토글 {stats.emergency_stop_toggle_count}회 — "
                "Kill Switch 사용 이력 검토."
            ),
            count=stats.emergency_stop_toggle_count,
            detail={"reasons": list(stats.emergency_stop_reasons)},
        ))

    # 일반 order_rejected — 위 카테고리에 속하지 않은 REJECTED가 다수일 때
    rejected_total = stats.rejected_count
    classified = sum(f.count for f in findings if f.category != LossCauseCategory.EMERGENCY_STOP)
    if rejected_total > 0 and classified < rejected_total:
        unclassified = rejected_total - classified
        if unclassified >= 5:
            findings.append(DailyReportFinding(
                category=LossCauseCategory.ORDER_REJECTED,
                severity=FindingSeverity.WARN,
                summary=f"분류되지 않은 REJECTED {unclassified}건 — 사유 패턴 분석 필요.",
                count=unclassified,
            ))

    return findings


# ====================================================================
# Tomorrow warnings + action items + improvement candidates
# ====================================================================


def _build_tomorrow_warnings(stats: DailyReportStats,
                             findings: list[DailyReportFinding]) -> list[str]:
    """*advisory*만 — 종목 추천 / 매수 매도 신호 X."""
    warnings: list[str] = []
    cats = {f.category for f in findings}

    if LossCauseCategory.DATA_STALE in cats:
        warnings.append("장 시작 전 데이터 freshness 점검 (시세 timestamp / collector 상태).")
    if LossCauseCategory.AI_OVERCONFIDENCE in cats:
        warnings.append("AI 신호 calibration이 흔들릴 수 있음 — 보수적 신뢰도 임계 적용 검토.")
    if LossCauseCategory.LOSS_LIMIT_BREACH in cats:
        warnings.append("일일 손실 한도가 발동된 적 있음 — 다음 운용 전 한도 / 사이즈 재검토.")
    if LossCauseCategory.LIQUIDATION_RISK in cats:
        warnings.append("선물 청산 위험 신호 — 선물 운용 시 추가 보수적 정책 권고.")
    if LossCauseCategory.EMERGENCY_STOP in cats:
        warnings.append("Kill Switch 토글 이력 — 다음 운용 전 운영자 review 권고.")
    if stats.approval_revalidation_failures >= 3:
        warnings.append(
            f"승인 시점 재검증 실패 {stats.approval_revalidation_failures}회 — "
            "결재 시점 broker 상태 변화 패턴 점검."
        )
    if stats.rejected_count >= 10 and stats.total_orders >= 1:
        rate = stats.rejected_count / stats.total_orders
        if rate >= 0.50:
            warnings.append(
                f"전체 거부율 {rate*100:.0f}% — 전략 / 진입 조건 재점검 필요."
            )

    if not warnings:
        warnings.append("특별한 주의사항 없음 — 정기 점검 권고.")
    return warnings


def _build_action_items(stats: DailyReportStats,
                        findings: list[DailyReportFinding]) -> list[str]:
    """운영자가 *수동*으로 확인해야 할 항목."""
    items: list[str] = []
    if stats.emergency_stop_toggle_count > 0:
        items.append("Kill Switch 토글 이력을 audit log에서 확인하고 사유 기록.")
    if stats.approval_pending > 0:
        items.append(
            f"PENDING approval {stats.approval_pending}건 — 시간 경과 / 만료 확인."
        )
    if any(f.category == LossCauseCategory.DATA_STALE for f in findings):
        items.append("Market data collector 상태 / freshness 점검.")
    if any(f.category == LossCauseCategory.AI_OVERCONFIDENCE for f in findings):
        items.append("AI 응답 audit row를 표본 추출해 reasoning 검토.")
    if not items:
        items.append("특별 조치 없음 — 정기 백업 / audit retention 점검 권고.")
    return items


def _build_improvement_candidates(stats: DailyReportStats,
                                  findings: list[DailyReportFinding]) -> list[str]:
    """전략 / 정책 / UI / 테스트 *후보* — 자동 적용 X."""
    candidates: list[str] = []
    if stats.rejected_count > 0 and stats.total_orders >= 5:
        candidates.append(
            "거부율이 높은 전략에 대해 StrategyResearcher(#55) 분석 권고."
        )
    if any(f.category == LossCauseCategory.AI_OVERCONFIDENCE for f in findings):
        candidates.append(
            "AI Permission Gate(#39) 임계 또는 confidence 가중치 검토."
        )
    if stats.futures_forced_liquidation_count > 0:
        candidates.append(
            "FuturesRiskPolicy의 maintenance margin / liquidation buffer 검토."
        )
    if stats.approval_revalidation_failures >= 3:
        candidates.append(
            "approve-time RiskManager 재검증 패턴 분석 (#070 / #075)."
        )
    if not candidates:
        candidates.append("특별 개선 후보 없음.")
    return candidates


# ====================================================================
# Pure analysis function
# ====================================================================


def analyze_daily(inp: DailyReportInput) -> DailyReportOutput:
    """입력 → DailyReportOutput. 순수 함수 — DB / broker / 외부 호출 0건."""
    stats = aggregate_stats(inp)
    findings = classify_findings(inp, stats)
    tomorrow_warnings = _build_tomorrow_warnings(stats, findings)
    action_items = _build_action_items(stats, findings)
    improvements = _build_improvement_candidates(stats, findings)
    summary_lines = _build_summary_lines(stats, findings)
    markdown = _build_markdown(
        report_date=inp.report_date,
        stats=stats,
        findings=findings,
        tomorrow_warnings=tomorrow_warnings,
        action_items=action_items,
        improvements=improvements,
        backtest_runs=list(inp.backtest_runs),
    )
    return DailyReportOutput(
        report_date=inp.report_date,
        stats=stats,
        findings=tuple(findings),
        tomorrow_warnings=tuple(tomorrow_warnings),
        action_items=tuple(action_items),
        improvement_candidates=tuple(improvements),
        markdown_report=markdown,
        summary_lines=tuple(summary_lines),
    )


def _build_summary_lines(stats: DailyReportStats,
                        findings: list[DailyReportFinding]) -> list[str]:
    lines: list[str] = []
    lines.append(
        f"운용 모드 {stats.operation_mode} · 주문 {stats.total_orders}건 "
        f"(승인 {stats.approved_count} / 거부 {stats.rejected_count} / "
        f"승인필요 {stats.needs_approval_count})."
    )
    lines.append(
        f"가상 주문 {stats.virtual_order_count}건 (체결 {stats.virtual_filled_count}) · "
        f"선물 audit {stats.futures_order_count}건 · "
        f"강제청산 {stats.futures_forced_liquidation_count}건."
    )
    lines.append(
        f"손실 원인 / 위험 패턴 finding {len(findings)}건 분류."
    )
    lines.append(
        "본 리포트는 *투자 조언이 아니라* 시스템 운영 / 검증 / 개선 자료입니다."
    )
    return lines


# ====================================================================
# Markdown report builder
# ====================================================================


_DISCLAIMER = (
    "**중요 고지**\n\n"
    "이 문서는 *투자 조언이 아니라* 자동매매 시스템 운영·검증·개선 자료입니다.\n"
    "실제 투자 판단은 사용자 책임이며, 실거래 전 별도 검증이 필요합니다.\n\n"
    "본 리포트는 어떤 *종목 추천*도 하지 않으며, 매수 / 매도 결정 신호를\n"
    "포함하지 않습니다."
)


def _build_markdown(
    *,
    report_date: date,
    stats: DailyReportStats,
    findings: list[DailyReportFinding],
    tomorrow_warnings: list[str],
    action_items: list[str],
    improvements: list[str],
    backtest_runs: list,
) -> str:
    parts: list[str] = []
    parts.append(f"# Daily System Report — {report_date.isoformat()}")
    parts.append("")
    parts.append(f"_Generated: {datetime.now(timezone.utc).isoformat()}_")
    parts.append("")
    parts.append("## 중요 고지")
    parts.append("")
    parts.append(_DISCLAIMER)
    parts.append("")

    # 1. 오늘 요약
    parts.append("## 1. 오늘 요약")
    parts.append("")
    parts.append("| 항목 | 값 |")
    parts.append("|---|---|")
    parts.append(f"| 운용 모드 | `{stats.operation_mode}` |")
    parts.append(f"| 총 주문 수 | {stats.total_orders} |")
    parts.append(f"| 승인 | {stats.approved_count} |")
    parts.append(f"| 거부 | {stats.rejected_count} |")
    parts.append(f"| 승인 필요 | {stats.needs_approval_count} |")
    parts.append(f"| 가상 주문 | {stats.virtual_order_count} (체결 {stats.virtual_filled_count}) |")
    parts.append(f"| 선물 audit | {stats.futures_order_count} (강제청산 {stats.futures_forced_liquidation_count}) |")
    if stats.win_rate is not None:
        parts.append(f"| 승률 (가상 추정) | {stats.win_rate*100:.1f}% |")
    if stats.expectancy is not None:
        parts.append(f"| 기대값 (가상 추정) | {stats.expectancy:.2f} |")
    if stats.profit_factor is not None:
        parts.append(f"| Profit Factor (가상 추정) | {stats.profit_factor:.2f} |")
    parts.append("")

    # 2. 손익 요약
    parts.append("## 2. 손익 요약")
    parts.append("")
    parts.append(
        "_주의: 본 리포트의 PnL은 VirtualOrder / audit 추정치이며, 실제 broker_\n"
        "_realized PnL과 *다를 수 있습니다*. reconciliation 모듈로 별도 검증 필요._"
    )
    parts.append("")
    parts.append(f"- realized_pnl (추정): {stats.realized_pnl:,}원")
    parts.append(f"- unrealized_pnl (추정): {stats.unrealized_pnl:,}원")
    parts.append(f"- total_pnl (추정): {stats.total_pnl:,}원")
    parts.append(f"- 가상 주문 PnL 추정: {stats.virtual_pnl_estimate:,}원")
    parts.append("")

    # 3. 시간대별 성과
    parts.append("## 3. 시간대별 성과")
    parts.append("")
    if stats.hourly_pnl:
        parts.append("| 시각 (UTC h) | PnL (추정) |")
        parts.append("|---|---|")
        for h in sorted(stats.hourly_pnl.keys()):
            parts.append(f"| {h:02d} | {stats.hourly_pnl[h]:,}원 |")
    else:
        parts.append("_시간대별 데이터 없음._")
    parts.append("")
    parts.append(
        "_장초반 / 장마감 30분은 변동성이 큰 시간대이므로 운용 시 주의 — 자동 조치 X._"
    )
    parts.append("")

    # 4. 전략별 성과
    parts.append("## 4. 전략별 성과")
    parts.append("")
    if stats.strategy_breakdown:
        parts.append("| 전략 | 주문 수 | 승인 | 거부 |")
        parts.append("|---|---|---|---|")
        for name, b in sorted(stats.strategy_breakdown.items()):
            parts.append(
                f"| `{name}` | {b['order_count']} | "
                f"{b.get('approved', 0)} | {b.get('rejected', 0)} |"
            )
    else:
        parts.append("_전략 데이터 없음._")
    parts.append("")

    # 5. Agent 판단 요약
    parts.append("## 5. Agent 판단 요약")
    parts.append("")
    if stats.avg_signal_confidence is not None:
        parts.append(
            f"- AI 신호 평균 confidence: {stats.avg_signal_confidence:.1f}"
        )
    if stats.agent_breakdown:
        parts.append("| Agent | 결정 수 | WARN | REJECT | 평균 confidence |")
        parts.append("|---|---|---|---|---|")
        for name, b in sorted(stats.agent_breakdown.items()):
            ac = b.get("avg_confidence")
            ac_str = f"{ac:.1f}" if ac is not None else "—"
            parts.append(
                f"| `{name}` | {b['decision_count']} | "
                f"{b['warn']} | {b['reject']} | {ac_str} |"
            )
    else:
        parts.append("_Agent 결정 데이터 없음._")
    parts.append("")

    # 6. 리스크 이벤트
    parts.append("## 6. 리스크 이벤트")
    parts.append("")
    if stats.risk_event_breakdown:
        parts.append("| 카테고리 | 카운트 |")
        parts.append("|---|---|")
        for cat, n in sorted(stats.risk_event_breakdown.items()):
            parts.append(f"| `{cat}` | {n} |")
    else:
        parts.append("_리스크 이벤트 없음._")
    parts.append("")

    # 7. 승인 큐 요약
    parts.append("## 7. 승인 큐 요약")
    parts.append("")
    parts.append("| 상태 | 카운트 |")
    parts.append("|---|---|")
    parts.append(f"| PENDING | {stats.approval_pending} |")
    parts.append(f"| APPROVED | {stats.approval_approved} |")
    parts.append(f"| REJECTED | {stats.approval_rejected} |")
    parts.append(f"| CANCELLED | {stats.approval_cancelled} |")
    parts.append(f"| EXPIRED | {stats.approval_expired} |")
    parts.append(f"| 재검증 실패 횟수 | {stats.approval_revalidation_failures} |")
    parts.append("")

    # 8. 손실 원인 분석
    parts.append("## 8. 손실 원인 분석 (advisory)")
    parts.append("")
    if not findings:
        parts.append("_관찰된 패턴 없음._")
    else:
        for f in findings:
            parts.append(f"### `{f.category}` — {f.severity} ({f.count}건)")
            parts.append("")
            parts.append(f"- {f.summary}")
            if f.detail:
                parts.append(f"- detail: `{f.detail}`")
            parts.append("")

    # 9. 내일 주의점
    parts.append("## 9. 내일 주의점")
    parts.append("")
    parts.append(
        "_*주의: 이 섹션은 시스템 운영 관점의 advisory입니다. 종목 추천이 아님.*_"
    )
    parts.append("")
    for w in tomorrow_warnings:
        parts.append(f"- {w}")
    parts.append("")

    # 10. 개선 후보
    parts.append("## 10. 개선 후보 (advisory)")
    parts.append("")
    parts.append(
        "_본 후보는 *자동 적용되지 않습니다*. 운영자 검토 → 별도 PR 절차 필요._"
    )
    parts.append("")
    for c in improvements:
        parts.append(f"- {c}")
    parts.append("")

    # 11. Action Items
    parts.append("## 11. Action Items")
    parts.append("")
    for a in action_items:
        parts.append(f"- [ ] {a}")
    parts.append("")

    # 12. 부록
    parts.append("## 12. 부록")
    parts.append("")
    parts.append(f"- 백테스트 실행 (오늘): {len(backtest_runs)}건")
    if backtest_runs:
        parts.append("  - run_id 목록:")
        for r in backtest_runs[:20]:
            rid = getattr(r, "id", None)
            strat = getattr(r, "strategy", None)
            pnl = getattr(r, "total_pnl", None)
            parts.append(f"    - id={rid} strategy=`{strat}` pnl={pnl}")
    parts.append(f"- 생성 시각 (UTC): {datetime.now(timezone.utc).isoformat()}")
    parts.append("")
    parts.append("---")
    parts.append("")
    parts.append("_본 리포트는 read-only 분석 결과입니다._")
    parts.append("_Daily Report Agent는 broker / OrderExecutor / route_order를_")
    parts.append("_호출하지 않으며, 어떤 주문도 생성하지 않습니다._")

    return "\n".join(parts)


# ====================================================================
# Agent class — #51 AgentBase 호환
# ====================================================================


class DailyReportAgent(AgentBase):
    """#57 — 장 종료 후 운영 / 검증 / 개선 자료 생성 advisory."""

    @property
    def metadata(self) -> AgentMetadata:
        return AgentMetadata(
            name="daily_report_agent",
            role=AgentRole.REPORT_WRITER,
            description=(
                "장 종료 후 OrderAuditLog / VirtualOrder / FuturesOrderAuditLog "
                "/ AgentDecisionLog / EmergencyStopEvent / PendingApproval / "
                "BacktestRun을 read-only로 분석해 reports/daily_YYYY-MM-DD.md "
                "자료를 생성. **투자 조언 아님.**"
            ),
            inputs=[
                "OrderAuditLog rows (오늘 KST)",
                "VirtualOrder rows",
                "FuturesOrderAuditLog rows",
                "AgentDecisionLog rows",
                "EmergencyStopEvent rows",
                "PendingApproval rows",
                "BacktestRun rows",
            ],
            outputs=[
                "DailyReportOutput (markdown_report, "
                "auto_apply_allowed=False, is_order_signal=False)",
            ],
            forbidden=[
                "broker / OrderExecutor / route_order 호출 금지",
                "주문 생성 / approval queue 등록 금지",
                "DB INSERT / UPDATE / DELETE 금지 (read-only SELECT만)",
                "외부 AI / HTTP 호출 금지",
                "투자 조언 / 종목 추천 / 매수 매도 신호 생성 금지",
            ],
            can_execute_order=False,
        )

    def run(self, context: AgentContext) -> AgentOutput:
        extra = context.extra or {}
        inp = extra.get("daily_report_input")
        if not isinstance(inp, DailyReportInput):
            return AgentOutput(
                role=AgentRole.REPORT_WRITER,
                decision=AgentDecision.NO_OP,
                summary="daily_report_input 미제공 — 리포트 생략.",
                reasons=["context.extra['daily_report_input']에 DailyReportInput 필요"],
                metadata={"reason": "missing_input"},
            )
        report = analyze_daily(inp)
        return AgentOutput(
            role=AgentRole.REPORT_WRITER,
            decision=AgentDecision.REPORT,
            summary=report.summary_lines[0] if report.summary_lines else
                    f"Daily report — {report.report_date.isoformat()}",
            reasons=[f.summary for f in report.findings[:5]],
            metadata={
                "report_date":         report.report_date.isoformat(),
                "findings_count":      len(report.findings),
                "warnings_count":      len(report.tomorrow_warnings),
                "action_items_count":  len(report.action_items),
                "auto_apply_allowed":  False,
                "is_order_signal":     False,
            },
        )

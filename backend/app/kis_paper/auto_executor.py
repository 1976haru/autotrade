"""KIS Paper Auto Trading executor — AI/전략 결정 → KIS 모의투자 API 주문.

AI Agent + 4가지 전략 조합이 만든 BUY/SELL 결정을 받아, KIS 모의 자동주문
권한 게이트를 통과한 뒤 **기존 sanctioned 경로**(`route_order` → RiskManager →
PermissionGate → OrderExecutor → KisBrokerAdapter[place_order, is_paper=True])
로 위임한다. 본 모듈은 *broker 주문 메서드를 직접 호출하지 않는다* — 단일
진입점(OrderExecutor)을 우회하지 않으며, RiskManager / PermissionGate 도
우회하지 않는다.

**실거래가 아니다.** KIS_IS_PAPER=true + ENABLE_LIVE_TRADING=false 가 게이트에서
강제되며, 주문 직전 `assert_paper_broker` 로 live broker 를 한 번 더 차단한다
(`NotPaperBrokerError`). 모든 결과는 `is_live_authorization=False`, broker_order_type
="KIS_PAPER".

dry_run 정책:
- dry_run=True → 게이트만 통과 검증, KIS API 호출 0건 (KIS_PAPER_DRY_RUN_OK).
- dry_run=False → route_order 로 실제 KIS 모의투자 주문 전송.
"""

from __future__ import annotations

import logging
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Callable

from sqlalchemy.orm import Session

from app.auto_paper.events import DecisionAction, PaperFillStatus
from app.auto_paper.ledger import record_paper_event
from app.db.models import AgentDecisionLog, OrderAuditLog
from app.execution.paper_trader import assert_paper_broker
from app.kis_paper.auto_permission import (
    KisPaperOrderPermissionInput,
    evaluate_kis_paper_order_permission,
)


_log = logging.getLogger("autotrade.kis_paper.auto")

# route_order 와 동일 시그니처의 콜러블 — 테스트는 mock 주입.
RouteOrderFn = Callable[..., Any]

# 추가 결과 reason codes.
KIS_PAPER_DRY_RUN_OK   = "KIS_PAPER_DRY_RUN_OK"
KIS_PAPER_SUBMITTED    = "KIS_PAPER_SUBMITTED"
KIS_PAPER_REJECTED     = "KIS_PAPER_REJECTED"
KIS_PAPER_NEEDS_APPROVAL = "KIS_PAPER_NEEDS_APPROVAL"
KIS_PAPER_ERROR        = "KIS_PAPER_ERROR"
BLOCKED_BY_RISK_MANAGER = "BLOCKED_BY_RISK_MANAGER"
BLOCKED_BY_PERMISSION_GATE = "BLOCKED_BY_PERMISSION_GATE"


@dataclass(frozen=True)
class KisPaperAutoDecision:
    """AI Agent + 4전략 조합이 만든 *결정* — 주문 권한이 아님."""

    symbol:              str
    side:                str               # BUY / SELL / HOLD
    quantity:            int
    price:               int
    selected_strategies: list[str]         = field(default_factory=list)
    confidence:          float             = 0.0   # 0~1
    quality_score:       int               = 0     # 0~100
    entry_reason:        str               = ""
    has_exit_plan:       bool              = False
    exit_plan:           dict[str, Any]    = field(default_factory=dict)
    # 2-11: Agent Council carry — 사후 추적/감사용 (주문 동작에는 영향 없음).
    reason_code:          str | None        = None
    risk_profile:         str | None        = None
    risk_veto_result:     dict[str, Any]    = field(default_factory=dict)
    exit_plan_validation: dict[str, Any]    = field(default_factory=dict)
    sell_reason_code:     str | None        = None
    sell_reason_category: str | None        = None
    # 2-12: 4전략 vote 상세 + risk_flags carry (AgentDecisionLog 판단 근거 보존).
    votes:                list[dict[str, Any]] = field(default_factory=list)
    risk_flags:           list[str]         = field(default_factory=list)
    # 3-02: 보유 포지션 청산(SELL) context — SELL 은 보유 청산만, 숏 진입 아님.
    held_position:        bool              = False
    position_quantity:    int               = 0
    is_short_entry:       bool              = False
    short_position:       bool              = False
    # V2: 판단에 쓰인 시세 출처 — "kis"(실시간) / "mock" / "yfinance". 실제
    # KIS 모의주문 전송(not dry_run)은 price_source="kis" 에서만 허용된다.
    price_source:         str               = "kis"
    price_is_stale:       bool              = False

    def __post_init__(self) -> None:
        if self.is_short_entry is not False:
            raise ValueError("KisPaperAutoDecision.is_short_entry must be False (no short entry)")
        if self.short_position is not False:
            raise ValueError("KisPaperAutoDecision.short_position must be False")

    @property
    def notional_krw(self) -> int:
        return int(self.price) * int(self.quantity)


def build_kis_paper_decision_from_council(
    council_decision, *, quantity: int, price: int,
):
    """2-11: Agent Council 결정 → KisPaperAutoDecision (BUY/SELL 만, HOLD → None).

    council 의 `to_kis_paper_decision` 에 위임 — final_action 이 HOLD(또는 veto /
    exit_plan 검증 실패로 HOLD 강등) 이면 None 을 반환해 주문이 생성되지 않는다.
    selected_strategies / confidence / quality_score / exit_plan / risk_profile /
    risk_veto_result / exit_plan_validation / sell_reason 을 carry.
    """
    if council_decision is None:
        return None
    return council_decision.to_kis_paper_decision(
        quantity=int(quantity), price=int(price))


@dataclass(frozen=True)
class KisPaperAutoResult:
    """KIS 모의 자동주문 결과 — *advisory*. is_live_authorization=False 영구."""

    reason_code:     str
    reason_message:  str
    submitted:       bool                  # 실제 KIS API 주문 전송됨
    dry_run:         bool
    symbol:          str | None
    side:            str
    quantity:        int
    notional_krw:    int
    broker_order_no: str | None = None
    order_status:    str | None = None
    fill_status:     str | None = None
    audit_id:        int | None = None
    decision_log_id: int | None = None
    # P-24: 체결 품질 산출용 (slippage/부분체결 계산). 기본 0/None.
    filled_quantity: int = 0
    avg_fill_price:  int | None = None
    metadata:        dict[str, Any] = field(default_factory=dict)

    # 절대 invariant.
    broker_order_type:     str  = "KIS_PAPER"
    broker_order_sent:     bool = False     # 실제 broker 주문 전송 여부 (paper)
    is_live_authorization: bool = False
    is_order_signal:       bool = False

    def __post_init__(self) -> None:
        if self.is_live_authorization is not False:
            raise ValueError("KisPaperAutoResult.is_live_authorization must be False")
        if self.is_order_signal is not False:
            raise ValueError("KisPaperAutoResult.is_order_signal must be False")
        if self.broker_order_type != "KIS_PAPER":
            raise ValueError("KisPaperAutoResult.broker_order_type must be KIS_PAPER")

    def to_dict(self) -> dict[str, Any]:
        return {
            "reason_code":     self.reason_code,
            "reason_message":  self.reason_message,
            "submitted":       bool(self.submitted),
            "dry_run":         bool(self.dry_run),
            "symbol":          self.symbol,
            "side":            self.side,
            "quantity":        int(self.quantity),
            "notional_krw":    int(self.notional_krw),
            "broker_order_no": self.broker_order_no,
            "order_status":    self.order_status,
            "fill_status":     self.fill_status,
            "audit_id":        self.audit_id,
            "decision_log_id": self.decision_log_id,
            "filled_quantity": int(self.filled_quantity),
            "avg_fill_price":  self.avg_fill_price,
            "metadata":        dict(self.metadata),
            "broker_order_type":     self.broker_order_type,
            "broker_order_sent":     self.broker_order_sent,
            "is_live_authorization": self.is_live_authorization,
            "is_order_signal":       self.is_order_signal,
            "advisory_disclaimer": (
                "한투 모의투자 API 주문 — 실제 돈이 나가지 않습니다. 실거래 OFF · "
                "KIS_IS_PAPER=true. broker_order_type=KIS_PAPER, "
                "is_live_authorization=false."
            ),
        }


def _today_kis_paper_order_count(db: Session, now: datetime) -> int:
    """오늘(UTC date) KIS 모의 자동 *BUY* 주문(trade_reason='kis_paper_auto') 카운트.

    ★일일 주문 *횟수* 한도(max_orders_per_day)는 *신규 진입(BUY)에만* 적용된다
    (auto_permission 게이트가 side==BUY 에서만 차단). 따라서 카운트도 BUY 만 세야
    한다 — 예전엔 BUY+SELL 전부 세어, 실패/청산 SELL 이 BUY 횟수 예산을 소진시켜
    BUY 를 전량 차단했다(2026-06-09: 실패 SELL 250건이 카운트를 10 초과로 부풀려
    BUY 1,046건 차단). A수정(SELL 한도 면제) 정신과 정합 — SELL 은 횟수 예산을
    소진하지 않는다.
    """
    start = now.replace(hour=0, minute=0, second=0, microsecond=0)
    try:
        return (
            db.query(OrderAuditLog)
            .filter(OrderAuditLog.trade_reason == "kis_paper_auto")
            .filter(OrderAuditLog.created_at >= start)
            .filter(OrderAuditLog.executed.is_(True))
            .filter(OrderAuditLog.side == "BUY")   # ★BUY 만 — SELL 은 횟수 면제(A수정 정합)
            .count()
        )
    except Exception:  # noqa: BLE001
        return 0


def build_permission_input(
    *,
    settings: Any,
    decision: KisPaperAutoDecision,
    broker_is_kis_paper: bool,
    credentials_present: bool,
    emergency_stop: bool,
    daily_order_count: int,
    now: datetime,
) -> KisPaperOrderPermissionInput:
    """settings + decision → 게이트 입력 DTO (secret 값 없음)."""
    return KisPaperOrderPermissionInput(
        enable_kis_paper_auto_trading=bool(getattr(settings, "enable_kis_paper_auto_trading", False)),
        dry_run=bool(getattr(settings, "kis_paper_auto_order_dry_run", True)),
        kis_is_paper=bool(getattr(settings, "kis_is_paper", True)),
        enable_live_trading=bool(getattr(settings, "enable_live_trading", False)),
        broker_is_kis_paper=bool(broker_is_kis_paper),
        credentials_present=bool(credentials_present),
        emergency_stop=bool(emergency_stop),
        side=decision.side,
        notional_krw=decision.notional_krw,
        confidence=float(decision.confidence),
        quality_score=int(decision.quality_score),
        has_exit_plan=bool(decision.has_exit_plan),
        max_order_notional=int(getattr(settings, "kis_paper_auto_max_order_notional", 1_000_000)),
        daily_order_count=int(daily_order_count),
        max_orders_per_day=int(getattr(settings, "kis_paper_auto_max_orders_per_day", 10)),
        window_start=str(getattr(settings, "kis_paper_auto_order_window_start", "09:05")),
        window_end=str(getattr(settings, "kis_paper_auto_order_window_end", "14:50")),
        min_confidence=float(getattr(settings, "kis_paper_auto_min_confidence", 0.6)),
        min_quality_score=int(getattr(settings, "kis_paper_auto_min_quality_score", 60)),
        price_source=str(getattr(decision, "price_source", "kis") or "kis"),
        price_is_stale=bool(getattr(decision, "price_is_stale", False)),
        now=now,
    )


async def execute_kis_paper_auto_order(
    db: Session,
    *,
    decision: KisPaperAutoDecision,
    settings: Any,
    broker: Any,
    risk: Any,
    broker_is_kis_paper: bool,
    credentials_present: bool,
    emergency_stop: bool = False,
    route_order_fn: RouteOrderFn,
    now: datetime | None = None,
    record: bool = True,
    chain_id: str | None = None,
) -> KisPaperAutoResult:
    """KIS 모의 자동주문 1건 실행 — 게이트 통과 시 route_order 로 위임.

    broker.place_order 직접 호출 0건. RiskManager / PermissionGate 우회 0건.
    매 호출마다 AgentDecisionLog + ledger 기록 (거래 0건이어도 기록 0건 불가).
    """
    if now is None:
        now = datetime.now(timezone.utc)
    chain_id = chain_id or f"kis-paper-{uuid.uuid4().hex[:12]}"

    daily_count = _today_kis_paper_order_count(db, now)
    perm = evaluate_kis_paper_order_permission(build_permission_input(
        settings=settings, decision=decision,
        broker_is_kis_paper=broker_is_kis_paper,
        credentials_present=credentials_present,
        emergency_stop=emergency_stop, daily_order_count=daily_count, now=now,
    ))

    # P-26: SELL 이면 매도 사유 reason_code 를 AgentDecisionLog / ledger 에 carry.
    # 2-11: Agent Council 이 sell_reason 을 이미 전달했으면(decision) 그 값을 우선,
    #       아니면 infer_sell_reason 으로 산출.
    sell_meta: dict[str, Any] = {}
    if decision.side == "SELL":
        try:
            if getattr(decision, "sell_reason_code", None):
                sell_meta = {"sell_reason_code": decision.sell_reason_code,
                             "sell_reason_category": getattr(decision, "sell_reason_category", None)}
            else:
                from app.agents.sell_reason import infer_sell_reason
                _sr = infer_sell_reason(
                    decision=decision, selected_strategies=decision.selected_strategies,
                    exit_plan=decision.exit_plan,
                )
                sell_meta = {"sell_reason_code": _sr.reason_code,
                             "sell_reason_category": _sr.category}
        except Exception:  # noqa: BLE001 — sell_reason 실패는 주문 흐름을 막지 않음.
            sell_meta = {}

    # 2-12: AgentDecisionLog meta 표준 빌더 (votes/판단근거/주문결과) — secret-safe.
    #       Agent Council 판단 근거(2-11 carry)는 helper 가 decision 에서 추출.
    def _decision_log_meta(*, reason_code, broker_order_no, submitted, dry_run,
                           broker_order_sent, audit_id, extra_meta) -> dict[str, Any]:
        from app.agents.decision_log_meta import build_agent_decision_log_meta
        meta = build_agent_decision_log_meta(
            decision=decision, reason_code=reason_code, broker_order_no=broker_order_no,
            submitted=submitted, dry_run=dry_run, broker_order_sent=broker_order_sent,
            order_created=bool(submitted), audit_id=audit_id, episode_id=chain_id,
            extra=extra_meta,
        )
        # SELL sell_reason 추론 fallback override (council 이 carry 안 한 경우).
        if sell_meta.get("sell_reason_code") and not meta.get("sell_reason_code"):
            meta["sell_reason_code"] = sell_meta.get("sell_reason_code")
            meta["sell_reason_category"] = sell_meta.get("sell_reason_category")
        return meta

    def _finish(reason_code: str, reason_message: str, *,
                submitted: bool = False, broker_order_no=None, order_status=None,
                fill_status=None, audit_id=None, broker_order_sent=False,
                filled_quantity: int = 0, avg_fill_price=None,
                extra_meta=None) -> KisPaperAutoResult:
        log_id: int | None = None
        if record:
            try:
                row = AgentDecisionLog(
                    agent_name="kis_paper_auto_executor",
                    symbol=decision.symbol, mode="PAPER",
                    decision=(decision.side if decision.side in ("BUY", "SELL") else "HOLD"),
                    confidence=int(decision.confidence * 100),
                    reasons=[reason_code, reason_message,
                             *(decision.selected_strategies or [])],
                    meta=_decision_log_meta(
                        reason_code=reason_code, broker_order_no=broker_order_no,
                        submitted=submitted, dry_run=bool(perm.dry_run),
                        broker_order_sent=broker_order_sent, audit_id=audit_id,
                        extra_meta=extra_meta,
                    ),
                    chain_id=chain_id,
                )
                db.add(row)
                db.flush()
                log_id = row.id
            except Exception as exc:  # noqa: BLE001
                _log.warning("[kis-paper-auto] AgentDecisionLog write failed: %s", exc)
            try:
                action = (
                    DecisionAction.BUY if (submitted and decision.side == "BUY")
                    else DecisionAction.SELL if (submitted and decision.side == "SELL")
                    else DecisionAction.NO_OP
                )
                fill = (
                    PaperFillStatus.PAPER_FILLED if fill_status == "FILLED"
                    else PaperFillStatus.PAPER_PENDING if submitted
                    else PaperFillStatus.NA
                )
                record_paper_event(
                    loop_state="RUNNING", strategy="kis_paper_auto",
                    symbol=decision.symbol,
                    decision_action=action, confidence=decision.confidence,
                    reason=f"[kis-paper-auto] {reason_code}: {reason_message}",
                    paper_order_id=str(broker_order_no or ""),
                    paper_fill_status=fill,
                    metadata={"broker_order_type": "KIS_PAPER",
                              "broker_order_no": broker_order_no,
                              "broker_order_sent": bool(broker_order_sent),
                              "reason_code": reason_code,
                              **sell_meta},
                )
            except Exception:  # noqa: BLE001
                pass
        return KisPaperAutoResult(
            reason_code=reason_code, reason_message=reason_message,
            submitted=submitted, dry_run=bool(perm.dry_run),
            symbol=decision.symbol, side=decision.side,
            quantity=int(decision.quantity), notional_krw=decision.notional_krw,
            broker_order_no=broker_order_no, order_status=order_status,
            fill_status=fill_status, audit_id=audit_id, decision_log_id=log_id,
            filled_quantity=int(filled_quantity or 0), avg_fill_price=avg_fill_price,
            broker_order_sent=broker_order_sent, metadata=extra_meta or {},
        )

    # 1. 게이트 차단.
    if not perm.allowed:
        return _finish(perm.reason_code, perm.reason_message)

    # 2. dry_run → KIS API 호출 없이 통과 검증만.
    if perm.dry_run:
        return _finish(
            KIS_PAPER_DRY_RUN_OK,
            "KIS 모의 자동주문 권한 통과 — dry-run 이라 주문 전송 없이 검증만.",
        )

    # 3. 주문 직전 paper-safety 백스톱 (live broker 차단).
    assert_paper_broker(broker)

    # 4. sanctioned 경로 위임 — route_order (RiskManager → PermissionGate →
    #    OrderExecutor → KisBrokerAdapter[place_order, is_paper=True]).
    from app.brokers.base import OrderRequest, OrderSide, OrderType
    side_enum = OrderSide.BUY if decision.side == "BUY" else OrderSide.SELL
    order = OrderRequest(
        symbol=decision.symbol, side=side_enum, quantity=int(decision.quantity),
        order_type=OrderType.MARKET, trade_reason="kis_paper_auto",
        strategy=",".join(decision.selected_strategies or []) or "kis_paper_auto",
        signal_confidence=int(decision.confidence * 100),
        signal_strength=int(decision.quality_score),
        client_order_id=chain_id,
        ai_decision_meta={
            "source": "KIS_PAPER_AUTO",
            "selected_strategies": list(decision.selected_strategies or []),
            "reasons": [decision.entry_reason or "kis_paper_auto"],
            "exit_plan": dict(decision.exit_plan or {}),
        },
    )
    try:
        # requested_by_ai=False — RiskManager 의 LIVE AI 실행 게이트(현재 모드
        # 에서 차단)를 트리거하지 않고 표준 위험 한도만 평가. AI provenance 는
        # ai_decision_meta + AgentDecisionLog 에 기록.
        from app.core.modes import OperationMode
        from app.risk.risk_manager import RiskDecision
        routing = await route_order_fn(
            order=order, requested_by_ai=False, mode=OperationMode.PAPER,
            broker=broker, risk=risk, db=db,
        )
    except Exception as exc:  # noqa: BLE001 — 주문 흐름이 죽지 않게.
        _log.warning("[kis-paper-auto] route_order raised: %s: %s",
                     type(exc).__name__, exc)
        return _finish(KIS_PAPER_ERROR, f"{type(exc).__name__}: {exc}")

    audit = routing.audit
    broker_order_no = getattr(audit, "broker_order_id", None)
    order_status = getattr(audit, "broker_status", None)
    filled_qty = int(getattr(audit, "filled_quantity", 0) or 0)
    avg_fill_price = getattr(audit, "avg_fill_price", None) or getattr(
        audit, "broker_avg_price", None)
    fill_status = (
        "FILLED" if order_status == "FILLED"
        else "PARTIALLY_FILLED" if filled_qty > 0
        else None
    )
    decision_val = routing.decision
    if decision_val == RiskDecision.APPROVED:
        executed = bool(getattr(audit, "executed", False))
        return _finish(
            KIS_PAPER_SUBMITTED if executed else KIS_PAPER_ERROR,
            ("KIS 모의투자 주문 전송 완료 (실제 돈 0원)." if executed
             else "주문 승인되었으나 전송이 확인되지 않았습니다."),
            submitted=executed, broker_order_no=broker_order_no,
            order_status=order_status, fill_status=fill_status,
            audit_id=getattr(audit, "id", None), broker_order_sent=executed,
            filled_quantity=filled_qty, avg_fill_price=avg_fill_price,
            extra_meta={"routing_reasons": list(routing.reasons or [])},
        )
    if decision_val == RiskDecision.NEEDS_APPROVAL:
        return _finish(
            BLOCKED_BY_PERMISSION_GATE,
            "KIS 모의 주문이 승인 대기 큐로 이동했습니다 (운영자 승인 필요).",
            audit_id=getattr(audit, "id", None),
            extra_meta={"routing_reasons": list(routing.reasons or [])},
        )
    # REJECTED / BLOCKED.
    return _finish(
        BLOCKED_BY_RISK_MANAGER,
        "; ".join(routing.reasons or []) or "RiskManager 가 주문을 차단했습니다.",
        audit_id=getattr(audit, "id", None),
        extra_meta={"routing_reasons": list(routing.reasons or [])},
    )


__all__ = [
    "KisPaperAutoDecision",
    "KisPaperAutoResult",
    "RouteOrderFn",
    "build_permission_input",
    "build_kis_paper_decision_from_council",
    "execute_kis_paper_auto_order",
    "KIS_PAPER_DRY_RUN_OK",
    "KIS_PAPER_SUBMITTED",
    "KIS_PAPER_REJECTED",
    "KIS_PAPER_NEEDS_APPROVAL",
    "KIS_PAPER_ERROR",
]

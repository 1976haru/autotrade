"""AI Paper 모의매매 *전체 흐름* — run-once 진단을 실제 Paper 체결까지 연결.

진단(`run_paper_pipeline_once`)이 *왜 매수/보류* 인지만 답한다면, 본 모듈은 그
판단을 받아 **실제 Paper VirtualOrder 생성 → Paper 체결 시뮬레이션 →
capital_state(현금) 반영 → ledger / AgentDecisionLog 기록** 까지 이어 붙인다.

목표 흐름:
    run-once 파이프라인(시세→전략→sizing→cash→permission)
      → 중복보유 / 일일한도 / 종목비중 / RiskManager(주입) paper 가드
      → VirtualOrder 생성(NEW → ACCEPTED → FILLED)
      → capital_state.commit_buy (현금 차감)
      → AgentDecisionLog + Paper ledger 기록
      → PortfolioCard / Ledger 가 같은 source 로 표시

**실거래가 아니다.** broker / OrderExecutor / route_order 를 *어떤 경로로도
호출하지 않는다* — Paper 체결은 `order_ledger.transition` + `mock` 단가로만
시뮬레이션. `broker_order_sent=False` / `is_live_authorization=False` 영구.

dry_run 정책:
- dry_run=True (기본) 또는 allow_simulated_fills=False → VirtualOrder *미생성*.
  판단/사유만 기록(진단과 동일). "거래 0건은 가능하나 기록 0건은 불가".
- dry_run=False + allow_simulated_fills=True → VirtualOrder 생성 + 체결 + 현금
  반영. 그래도 *Paper 가상* — 실거래 불가.

절대 금지 (CLAUDE.md 절대 원칙 + 사용자 요청서):
- `app.brokers.kis` / `app.brokers.mock_broker` / `app.execution.executor` /
  `app.execution.order_router` / `OrderExecutor` / `route_order` import·호출 0건
  (정적 grep 가드).
- RiskManager / PermissionGate 우회 0건 — RiskManager 는 caller 가 `risk_check`
  콜러블로 주입(read-only 평가), permission/cash/sizing 은 run-once 가 적용.
"""

from __future__ import annotations

import logging
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Callable, Optional

from sqlalchemy.orm import Session

from app.auto_paper.capital_state import (
    check_duplicate_position_buy,
    get_capital_state,
)
from app.auto_paper.capital_config import (
    DEFAULT_ALLOW_ADDITIONAL_BUY,
    get_paper_capital_config,
    resolve_daily_buy_limit,
    resolve_symbol_weight_limit_pct,
)
from app.auto_paper.events import DecisionAction, PaperFillStatus
from app.auto_paper.ledger import record_paper_event
from app.auto_paper.run_once import (
    RunOnceResult,
    RunOnceResultCode,
    run_paper_pipeline_once,
)
from app.db.models import AgentDecisionLog, VirtualOrder
from app.risk.loss_limits import check_daily_buy_limit
from app.risk.position_limits import check_symbol_weight_limit
from app.virtual.order_ledger import (
    STATUS_ACCEPTED,
    STATUS_FILLED,
    create_order,
    transition,
)
from app.virtual.position_engine import compute_open_positions


_log = logging.getLogger("autotrade.auto_paper.trade_flow")

# RiskManager 주입 콜러블: (symbol, side, quantity, price) -> (allowed, reason_or_None).
RiskCheck = Callable[[str, str, int, float], "tuple[bool, Optional[str]]"]


# 추가 paper 가드 reason codes (run-once reason codes 와 합쳐 사용자 요청서 §8 충족).
PAPER_GUARD_DUPLICATE        = "DUPLICATE_POSITION_BUY_BLOCKED"
PAPER_GUARD_DAILY_LIMIT      = "DAILY_BUY_LIMIT_EXCEEDED"
PAPER_GUARD_SYMBOL_WEIGHT    = "SYMBOL_WEIGHT_LIMIT_EXCEEDED"
PAPER_GUARD_RISK_MANAGER     = "BLOCKED_BY_RISK_MANAGER"


@dataclass(frozen=True)
class PaperTradeFlowResult:
    """Paper 모의매매 흐름 결과 — *advisory*, broker 호출 0건.

    `is_order_signal=False` / `is_live_authorization=False` /
    `broker_order_sent=False` 영구 (dataclass `__post_init__` ValueError 가드).
    """

    reason_code:     str
    reason_message:  str
    order_created:   bool                # VirtualOrder 가 생성되었는지
    filled:          bool                # Paper 체결까지 갔는지
    symbol:          str | None
    side:            str
    quantity:        int
    price:           float | None
    notional_krw:    int
    dry_run:         bool
    run_once_result_code: str

    order_id:        int | None  = None
    order_status:    str | None  = None
    filled_quantity: int         = 0
    fill_price:      int | None  = None
    fill_notional_krw: int       = 0
    filled_at:       str | None  = None
    fill_source:     str | None  = None
    cash_before:     int | None  = None
    cash_after:      int | None  = None
    decision_log_id: int | None  = None
    chain_id:        str | None  = None
    metadata:        dict[str, Any] = field(default_factory=dict)

    # 절대 invariant.
    requested_by_ai:       bool = True
    mode:                  str  = "PAPER"
    is_order_signal:       bool = False
    is_live_authorization: bool = False
    broker_order_sent:     bool = False

    def __post_init__(self) -> None:
        if self.is_order_signal is not False:
            raise ValueError("PaperTradeFlowResult.is_order_signal must be False")
        if self.is_live_authorization is not False:
            raise ValueError("PaperTradeFlowResult.is_live_authorization must be False")
        if self.broker_order_sent is not False:
            raise ValueError("PaperTradeFlowResult.broker_order_sent must be False")

    def to_dict(self) -> dict[str, Any]:
        return {
            "reason_code":          self.reason_code,
            "reason_message":       self.reason_message,
            "order_created":        bool(self.order_created),
            "filled":               bool(self.filled),
            "symbol":               self.symbol,
            "side":                 self.side,
            "quantity":             int(self.quantity),
            "price":                self.price,
            "notional_krw":         int(self.notional_krw),
            "dry_run":              bool(self.dry_run),
            "run_once_result_code": self.run_once_result_code,
            "order_id":             self.order_id,
            "order_status":         self.order_status,
            "filled_quantity":      int(self.filled_quantity),
            "fill_price":           self.fill_price,
            "fill_notional_krw":    int(self.fill_notional_krw),
            "filled_at":            self.filled_at,
            "fill_source":          self.fill_source,
            "cash_before":          self.cash_before,
            "cash_after":           self.cash_after,
            "decision_log_id":      self.decision_log_id,
            "chain_id":             self.chain_id,
            "metadata":             dict(self.metadata),
            "requested_by_ai":       self.requested_by_ai,
            "mode":                  self.mode,
            "is_order_signal":       self.is_order_signal,
            "is_live_authorization": self.is_live_authorization,
            "broker_order_sent":     self.broker_order_sent,
            "advisory_disclaimer": (
                "본 결과는 PAPER 모의매매 — broker / route_order / OrderExecutor "
                "호출 0건. VirtualOrder + 가상 체결 시뮬레이션이며 실거래는 어떤 "
                "경로로도 진행되지 않습니다."
            ),
        }


# ─────────────────────────────────────────────────────────────────────────────
# DB-aware helpers
# ─────────────────────────────────────────────────────────────────────────────


def _current_symbol_qty(db: Session, symbol: str, now: datetime) -> int:
    """현재 해당 symbol 의 Paper open 수량 (FIFO 포지션 엔진)."""
    try:
        positions = compute_open_positions(db, now=now)
    except Exception:  # noqa: BLE001 — 빈 DB / 미초기화 안전.
        return 0
    return sum(int(p.quantity) for p in positions if p.symbol == symbol)


def _symbol_exposure_krw(db: Session, symbol: str, now: datetime) -> int:
    """해당 symbol 현재 보유 평가금액 (cost basis 기준)."""
    try:
        positions = compute_open_positions(db, now=now)
    except Exception:  # noqa: BLE001
        return 0
    return sum(int(p.quantity) * int(p.avg_price)
               for p in positions if p.symbol == symbol)


def _today_buy_used_krw(db: Session, now: datetime) -> int:
    """오늘(UTC date) 생성된 BUY VirtualOrder 의 누적 명목 (요청가 기준)."""
    start = now.replace(hour=0, minute=0, second=0, microsecond=0)
    try:
        rows = (
            db.query(VirtualOrder)
            .filter(VirtualOrder.side == "BUY")
            .filter(VirtualOrder.created_at >= start)
            .all()
        )
    except Exception:  # noqa: BLE001
        return 0
    total = 0
    for r in rows:
        px = r.avg_fill_price or r.requested_price or 0
        qty = r.filled_quantity if r.filled_quantity else r.quantity
        total += int(px) * int(qty)
    return total


# ─────────────────────────────────────────────────────────────────────────────
# Main entry
# ─────────────────────────────────────────────────────────────────────────────


def execute_paper_trade_flow(
    db: Session,
    *,
    symbol:                 str | None             = None,
    price:                  float | int | None     = None,
    quantity:               int | None             = None,
    force_mock_market_data: bool                   = True,
    dry_run:                bool                   = True,
    allow_simulated_fills:  bool                   = False,
    per_symbol_cap_krw:     int | None             = None,
    available_cash_krw:     int | None             = None,
    market_data_provider:   str                    = "mock",
    reference_price:        float | int | None     = None,
    price_timestamp:        datetime | str | None  = None,
    strategy:               str                    = "diagnostic",
    strategy_engine_connected: bool                = True,
    signal_present:         bool                   = True,
    confidence:             float | None           = 0.75,
    paper_virtual_execution_enabled: bool          = True,
    slippage_bps:           float                  = 0.0,
    risk_check:             RiskCheck | None        = None,
    now:                    datetime | None        = None,
    chain_id:               str | None             = None,
) -> PaperTradeFlowResult:
    """run-once 진단을 받아 Paper VirtualOrder 생성 + 체결 + 현금 반영까지.

    broker / route_order / OrderExecutor 호출 0건. 매 호출마다 AgentDecisionLog
    1행 기록(거래 0건이어도 기록 0건 불가). dry_run=True 또는
    allow_simulated_fills=False 면 주문 미생성(판단/사유만 기록).
    """
    if now is None:
        now = datetime.now(timezone.utc)
    chain_id = chain_id or f"paper-flow-{uuid.uuid4().hex[:12]}"
    cap = (
        int(per_symbol_cap_krw) if per_symbol_cap_krw is not None
        else int(get_paper_capital_config().effective_per_symbol_cap_krw)
    )
    cash_state = get_capital_state()
    avail = (
        int(available_cash_krw) if available_cash_krw is not None
        else int(cash_state.snapshot().available_cash_krw)
    )

    # 1. run-once 파이프라인 재사용 — 시세/전략/sizing/cash/permission.
    ro: RunOnceResult = run_paper_pipeline_once(
        symbol=symbol, price=price, quantity=quantity,
        force_mock_market_data=force_mock_market_data, dry_run=dry_run,
        per_symbol_cap_krw=cap, available_cash_krw=avail,
        market_data_provider=market_data_provider,
        reference_price=reference_price, price_timestamp=price_timestamp,
        strategy=strategy, strategy_engine_connected=strategy_engine_connected,
        signal_present=signal_present, confidence=confidence,
        paper_virtual_execution_enabled=paper_virtual_execution_enabled,
        record=True, now=now,
    )
    resolved_symbol = ro.symbol
    eff_price = ro.price
    eff_qty = ro.quantity

    def _finish(reason_code: str, reason_message: str, *, order=None,
                filled=False, cash_before=None, cash_after=None,
                extra_meta=None) -> PaperTradeFlowResult:
        # AgentDecisionLog 1행 — 거래/차단 모두 기록.
        decision = "BUY" if (order is not None and filled) else "HOLD"
        log_id: int | None = None
        try:
            row = AgentDecisionLog(
                agent_name="ai_paper_trade_flow",
                symbol=resolved_symbol,
                mode="PAPER",
                decision=decision,
                confidence=int(confidence * 100) if confidence is not None else None,
                reasons=[reason_code, reason_message],
                meta={
                    "run_once_result_code": ro.result_code.value,
                    "reason_code": reason_code,
                    "order_id": getattr(order, "id", None),
                    "filled": bool(filled),
                    "dry_run": bool(dry_run),
                    "broker_order_sent": False,
                    "is_live_authorization": False,
                    **(extra_meta or {}),
                },
                chain_id=chain_id,
            )
            db.add(row)
            db.flush()
            log_id = row.id
        except Exception as exc:  # noqa: BLE001 — 기록 실패가 흐름을 깨지 않게.
            _log.warning("[paper-flow] AgentDecisionLog write failed: %s", exc)
        # Paper ledger heartbeat — 실행 BUY 면 PAPER_FILLED, 아니면 NO_OP.
        try:
            if order is not None and filled:
                record_paper_event(
                    loop_state="RUNNING", strategy=strategy,
                    symbol=resolved_symbol or "PAPER",
                    decision_action=DecisionAction.BUY,
                    confidence=confidence,
                    reason=f"[paper-flow] {reason_code}: {reason_message}",
                    paper_order_id=str(getattr(order, "id", "")),
                    paper_fill_status=PaperFillStatus.PAPER_FILLED,
                    virtual_position_delta=int(getattr(order, "filled_quantity", 0)),
                    metadata={"run_once_result_code": ro.result_code.value,
                              "broker_order_sent": False},
                )
            else:
                record_paper_event(
                    loop_state="RUNNING", strategy=strategy,
                    symbol=resolved_symbol or "PAPER",
                    decision_action=DecisionAction.NO_OP,
                    confidence=confidence,
                    reason=f"[paper-flow] {reason_code}: {reason_message}",
                    risk_flags=[reason_code] if not filled else [],
                    metadata={"run_once_result_code": ro.result_code.value,
                              "broker_order_sent": False},
                )
        except Exception:  # noqa: BLE001
            pass
        return PaperTradeFlowResult(
            reason_code=reason_code, reason_message=reason_message,
            order_created=order is not None, filled=filled,
            symbol=resolved_symbol, side="BUY",
            quantity=int(eff_qty), price=eff_price,
            notional_krw=int(ro.notional_krw), dry_run=bool(dry_run),
            run_once_result_code=ro.result_code.value,
            order_id=getattr(order, "id", None),
            order_status=getattr(order, "status", None),
            filled_quantity=int(getattr(order, "filled_quantity", 0) or 0),
            fill_price=getattr(order, "avg_fill_price", None),
            fill_notional_krw=(
                int(getattr(order, "avg_fill_price", 0) or 0)
                * int(getattr(order, "filled_quantity", 0) or 0)
            ),
            filled_at=(order.filled_at.isoformat()
                       if order is not None and order.filled_at else None),
            fill_source=("PAPER_SIMULATOR" if filled else None),
            cash_before=cash_before, cash_after=cash_after,
            decision_log_id=log_id, chain_id=chain_id,
            metadata=extra_meta or {},
        )

    # 2. run-once 가 통과시키지 못했으면 그 사유로 종료 (주문 미생성).
    if not ro.ok:
        return _finish(ro.result_code.value, ro.reason_message)

    # 3. dry_run / 체결 비활성 → 주문 미생성, 판단만 기록.
    if dry_run or not allow_simulated_fills:
        return _finish(
            RunOnceResultCode.PAPER_DRY_RUN_OK.value,
            "dry-run / 체결 비활성 — 판단만 기록하고 가상 주문은 생성하지 않았습니다.",
        )

    # 여기부터는 dry_run=False + allow_simulated_fills=True + run-once OK.
    if resolved_symbol is None or eff_price is None or eff_qty < 1:
        return _finish(RunOnceResultCode.NO_CANDIDATE.value,
                       "유효한 후보 / 가격 / 수량이 없어 주문을 생성하지 않았습니다.")

    # 4. 중복 보유 가드.
    cfg = get_paper_capital_config()
    allow_additional = bool(getattr(cfg, "allow_additional_buy",
                                    DEFAULT_ALLOW_ADDITIONAL_BUY))
    held_qty = _current_symbol_qty(db, resolved_symbol, now)
    dup = check_duplicate_position_buy(
        side="BUY", symbol=resolved_symbol,
        current_position_quantity=held_qty,
        allow_additional_buy=allow_additional,
    )
    if not dup.allowed:
        return _finish(PAPER_GUARD_DUPLICATE, dup.reason_message)

    # 5. 일일 매수 한도 가드.
    daily_max, _src = resolve_daily_buy_limit(
        total_paper_capital_krw=int(cfg.initial_cash),
    )
    today_used = _today_buy_used_krw(db, now)
    daily = check_daily_buy_limit(
        side="BUY", symbol=resolved_symbol, price=eff_price, quantity=eff_qty,
        today_buy_used_amount=today_used, max_daily_buy_amount=daily_max,
    )
    if not daily.allowed and daily.reason_code != "DAILY_BUY_LIMIT_NOT_APPLICABLE":
        return _finish(PAPER_GUARD_DAILY_LIMIT, daily.reason_message)

    # 6. 종목 비중 한도 가드.
    pct, _ws = resolve_symbol_weight_limit_pct()
    total_equity = avail + _symbol_exposure_krw(db, resolved_symbol, now)
    weight = check_symbol_weight_limit(
        side="BUY", symbol=resolved_symbol, price=eff_price, quantity=eff_qty,
        total_paper_equity=max(total_equity, 1),
        current_symbol_exposure_amount=_symbol_exposure_krw(db, resolved_symbol, now),
        max_symbol_weight_pct=pct,
    )
    if not weight.allowed and weight.reason_code != "SYMBOL_WEIGHT_LIMIT_NOT_APPLICABLE":
        return _finish(PAPER_GUARD_SYMBOL_WEIGHT, weight.reason_message)

    # 7. RiskManager 가드 (주입 — read-only 평가, broker 미접촉).
    if risk_check is not None:
        try:
            allowed, rreason = risk_check(resolved_symbol, "BUY", int(eff_qty),
                                          float(eff_price))
        except Exception as exc:  # noqa: BLE001 — risk_check 실패는 보수적으로 차단.
            allowed, rreason = False, f"risk_check_error: {exc}"
        if not allowed:
            return _finish(PAPER_GUARD_RISK_MANAGER,
                           rreason or "RiskManager 가 주문을 차단했습니다.")

    # 8. VirtualOrder 생성 → ACCEPTED → FILLED (Paper 체결 시뮬레이션).
    cash_before = int(cash_state.snapshot().available_cash_krw)
    order = create_order(
        db, symbol=resolved_symbol, side="BUY", quantity=int(eff_qty),
        order_type="MARKET", requested_price=int(eff_price),
        strategy=strategy, mode="PAPER",
    )
    transition(db, order, to_status=STATUS_ACCEPTED, reason="paper_accepted")
    # 슬리피지 적용 체결가 (bps). 0 이면 현재가 그대로.
    fill_price = int(round(float(eff_price) * (1.0 + float(slippage_bps) / 10_000.0)))
    transition(
        db, order, to_status=STATUS_FILLED,
        filled_delta=int(eff_qty), avg_fill_price=fill_price,
        reason="paper_simulated_fill", note="fill_source=PAPER_SIMULATOR",
    )

    # 9. capital_state 반영 (현금 차감). precheck 통과 가정 — backstop 으로 try.
    cash_after = cash_before
    try:
        snap = cash_state.commit_buy(symbol=resolved_symbol, price=fill_price,
                                     quantity=int(eff_qty), event_at=now.isoformat())
        cash_after = int(snap.available_cash_krw)
    except Exception as exc:  # noqa: BLE001 — 현금 부족 backstop (이미 P-07 통과).
        _log.warning("[paper-flow] commit_buy backstop: %s", exc)
        return _finish(RunOnceResultCode.INSUFFICIENT_PAPER_CASH.value,
                       f"체결 직전 현금 backstop 차단: {exc}",
                       order=order, filled=True,
                       cash_before=cash_before, cash_after=cash_before)

    return _finish(
        RunOnceResultCode.VIRTUAL_ORDER_CANDIDATE_CREATED.value,
        f"Paper 가상 주문 체결 완료: {resolved_symbol} {eff_qty}주 @ {fill_price:,}원 (실거래 아님).",
        order=order, filled=True,
        cash_before=cash_before, cash_after=cash_after,
        extra_meta={"slippage_bps": float(slippage_bps),
                    "fill_source": "PAPER_SIMULATOR"},
    )


__all__ = [
    "PaperTradeFlowResult",
    "RiskCheck",
    "execute_paper_trade_flow",
    "PAPER_GUARD_DUPLICATE",
    "PAPER_GUARD_DAILY_LIMIT",
    "PAPER_GUARD_SYMBOL_WEIGHT",
    "PAPER_GUARD_RISK_MANAGER",
]

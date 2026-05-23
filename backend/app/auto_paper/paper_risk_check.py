"""Paper 모의매매용 RiskManager risk_check 빌더 (read-only 평가).

`execute_paper_trade_flow` 의 `risk_check` 콜러블을 *주입* 받는 RiskManager 로
구성한다. RiskManager.check_order 는 *평가만* 수행 — broker 주문을 발신하지
않으며, 본 모듈도 broker adapter / OrderExecutor / route_order 를 호출하지
않는다.

중요 — `requested_by_ai=False`:
  RiskContext.requested_by_ai=True 는 RiskManager 의 *LIVE AI 실행 권한* 게이트를
  트리거해 PAPER 모드에서 BLOCKED 된다. 본 paper 시뮬레이션은 AI 실행 권한을
  요청하는 것이 *아니라* 표준 위험 한도(notional / cash / exposure / positions)만
  검사한다. AI 판단 성격은 별도 AgentDecisionLog / ledger 에 기록된다 — 실거래
  AI 실행 권한 부여 0건.
"""

from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy.orm import Session

from app.brokers.base import (
    Balance,
    OrderRequest,
    OrderSide,
    OrderType,
    Position,
)
from app.core.modes import OperationMode
from app.risk.risk_manager import RiskContext, RiskDecision, RiskManager
from app.virtual.position_engine import compute_open_positions


# (symbol, side, quantity, price) -> (allowed, reason_or_None)
RiskCheck = "Callable[[str, str, int, float], tuple[bool, Optional[str]]]"


def build_paper_risk_check(
    risk_manager: RiskManager,
    db: Session,
    available_cash_krw: int,
):
    """주입된 RiskManager 로 paper risk_check 콜러블 생성 — read-only.

    broker / OrderExecutor / route_order 호출 0건. RiskContext 는 capital_state
    현금 + VirtualOrder FIFO 포지션으로 구성하고, RiskManager.check_order 의
    decision 이 APPROVED / NEEDS_APPROVAL 이면 허용.
    """

    def _risk_check(symbol: str, side: str, quantity: int, price: float):
        try:
            order = OrderRequest(
                symbol=symbol, side=OrderSide.BUY, quantity=int(quantity),
                order_type=OrderType.MARKET, trade_reason="ai_paper_trade_flow",
                strategy="ai_paper",
            )
            pos_objs: list[Position] = []
            try:
                for p in compute_open_positions(db):
                    pos_objs.append(Position(
                        symbol=p.symbol, quantity=int(p.quantity),
                        avg_price=int(p.avg_price), market_price=int(p.avg_price),
                    ))
            except Exception:  # noqa: BLE001
                pos_objs = []
            bal = Balance(
                cash=int(available_cash_krw), equity=int(available_cash_krw),
                buying_power=int(available_cash_krw),
            )
            ctx = RiskContext(
                mode=OperationMode.PAPER, balance=bal, positions=pos_objs,
                latest_price=int(price), requested_by_ai=False,
                latest_price_timestamp=datetime.now(timezone.utc),
            )
            res = risk_manager.check_order(order, ctx)
            allowed = res.decision in (
                RiskDecision.APPROVED, RiskDecision.NEEDS_APPROVAL,
            )
            reason = (
                None if allowed
                else ("; ".join(res.reasons) or res.decision.value)
            )
            return allowed, reason
        except Exception as exc:  # noqa: BLE001 — 평가 실패는 보수적으로 차단.
            return False, f"risk_check_error: {type(exc).__name__}: {exc}"

    return _risk_check


__all__ = ["RiskCheck", "build_paper_risk_check"]

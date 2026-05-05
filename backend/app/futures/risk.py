from dataclasses import dataclass, field
from enum import StrEnum

from app.futures.types import FuturesOrderRequest, FuturesPosition


class FuturesRiskDecision(StrEnum):
    APPROVED       = "APPROVED"
    REJECTED       = "REJECTED"
    NEEDS_APPROVAL = "NEEDS_APPROVAL"


@dataclass
class FuturesRiskPolicy:
    """Conservative defaults — futures positions carry leverage and overnight
    risk, so limits are tighter than stock RiskPolicy by design."""

    max_contracts:                int  = 1
    max_margin_used:              int  = 1_000_000
    max_daily_loss:               int  = 200_000
    enable_futures_live_trading:  bool = False  # FUTURES_LIVE master flag


@dataclass
class FuturesRiskCheckResult:
    decision: FuturesRiskDecision
    reasons:  list[str] = field(default_factory=list)


class FuturesRiskManager:
    """선물 RiskManager — 현재 단계는 stub.

    절대 원칙:
    - `enable_futures_live_trading=False`이면 모든 선물 주문 REJECTED.
    - True로 설정해도 본 평가 로직(증거금/등락률/만기/리스크 한도)은
      별도 PR에서 구현하므로 그 전까지는 NotImplementedError.

    이 두 단계 가드는 선물 주문이 실수로 broker에 도달하는 일을 막는다.
    """

    def __init__(self, policy: FuturesRiskPolicy | None = None):
        self.policy = policy or FuturesRiskPolicy()

    def evaluate_order(
        self,
        *,
        order:            FuturesOrderRequest,
        positions:        list[FuturesPosition],
        margin_used:      int,
        margin_available: int,
    ) -> FuturesRiskCheckResult:
        if not self.policy.enable_futures_live_trading:
            return FuturesRiskCheckResult(
                decision=FuturesRiskDecision.REJECTED,
                reasons=["ENABLE_FUTURES_LIVE_TRADING is disabled"],
            )
        raise NotImplementedError(
            "FuturesRiskManager.evaluate_order is a stub. Real evaluation "
            "(margin requirements, contract caps, daily loss, expiry checks) "
            "lands in a follow-up PR."
        )

from dataclasses import dataclass, field
from enum import StrEnum

from app.futures.simulation import compute_initial_margin
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
    max_leverage:                 float = 10.0   # 151
    enable_futures_live_trading:  bool = False  # FUTURES_LIVE master flag


@dataclass
class FuturesRiskCheckResult:
    decision: FuturesRiskDecision
    reasons:  list[str] = field(default_factory=list)


class FuturesRiskManager:
    """선물 RiskManager.

    절대 원칙:
    - `enable_futures_live_trading=False`이면 live 평가 경로(`evaluate_order`)는
      모든 주문 REJECTED.
    - 가상 환경(MockFuturesBroker + FuturesSimulationEngine)은 별도 경로
      `evaluate_virtual_order`로 평가 — live 플래그와 무관하게 작동.

    이 두 경로 분리로 선물 주문이 실수로 broker live endpoint에 도달하는
    일을 막는다. CLAUDE.md 절대 원칙 6 / 운영 가드.
    """

    def __init__(
        self,
        policy: FuturesRiskPolicy | None = None,
        *,
        daily_realized_pnl: int = 0,
    ):
        self.policy = policy or FuturesRiskPolicy()
        self.daily_realized_pnl = daily_realized_pnl

    # ---------- live evaluation (still stubbed) ----------

    def evaluate_order(
        self,
        *,
        order:            FuturesOrderRequest,
        positions:        list[FuturesPosition],
        margin_used:      int,
        margin_available: int,
    ) -> FuturesRiskCheckResult:
        """라이브 선물 주문 평가. 본 PR에서는 여전히 비활성 — `enable_futures_live_trading`
        이 False면 무조건 REJECTED. True여도 실제 라이브 평가 로직은 추후 PR."""
        if not self.policy.enable_futures_live_trading:
            return FuturesRiskCheckResult(
                decision=FuturesRiskDecision.REJECTED,
                reasons=["ENABLE_FUTURES_LIVE_TRADING is disabled"],
            )
        # live 활성화 후의 실제 평가는 별도 PR (CLAUDE.md 원칙 6).
        return FuturesRiskCheckResult(
            decision=FuturesRiskDecision.REJECTED,
            reasons=["live futures evaluation not implemented yet"],
        )

    # ---------- virtual evaluation (151) ----------

    def evaluate_virtual_order(
        self,
        *,
        order:            FuturesOrderRequest,
        positions:        list[FuturesPosition],
        margin_used:      int,
        margin_available: int,
        mark_price:       int,
        leverage:         float,
    ) -> FuturesRiskCheckResult:
        """MockFuturesBroker + FuturesSimulationEngine 경로 전용.

        강제 invariant:
        - leverage ≤ policy.max_leverage
        - 신규 계약 추가 후 contracts ≤ policy.max_contracts
        - margin_used + 추가 initial_margin ≤ policy.max_margin_used
        - margin_available ≥ 추가 initial_margin (잔고 부족 차단)
        - daily_realized_pnl > -max_daily_loss

        모두 통과하면 APPROVED. 본 함수는 `enable_futures_live_trading`을
        보지 않는다 — 가상 경로이므로 라이브 플래그와 무관.
        """
        result = FuturesRiskCheckResult(decision=FuturesRiskDecision.APPROVED)

        if leverage <= 0:
            result.reasons.append("leverage must be positive")
        elif leverage > self.policy.max_leverage:
            result.reasons.append(
                f"leverage {leverage} exceeds max_leverage {self.policy.max_leverage}"
            )

        # 신규 계약 추가 후 총 보유 계약 수 — 동일 contract code의 같은 side는 누적.
        # 다른 side는 청산 의도 (caller가 결정), 본 가드는 단순 합산으로 보수적.
        existing_qty = sum(p.quantity for p in positions if p.contract == order.contract)
        new_total = existing_qty + order.quantity
        if new_total > self.policy.max_contracts:
            result.reasons.append(
                f"contracts {new_total} exceeds max_contracts {self.policy.max_contracts}"
            )

        if mark_price <= 0:
            result.reasons.append("mark_price must be positive")
            mark_price = 1  # 산식 보호용 안전값

        # initial margin 산출 → 잔고/한도 검증.
        notional = mark_price * order.quantity
        if leverage > 0:
            init_margin = compute_initial_margin(notional=notional, leverage=leverage)
        else:
            init_margin = notional

        if margin_available < init_margin:
            result.reasons.append(
                f"margin_available {margin_available} < required {init_margin}"
            )
        if margin_used + init_margin > self.policy.max_margin_used:
            result.reasons.append(
                f"margin_used {margin_used + init_margin} exceeds "
                f"max_margin_used {self.policy.max_margin_used}"
            )

        if self.daily_realized_pnl <= -abs(self.policy.max_daily_loss):
            result.reasons.append("daily futures loss limit reached")

        if result.reasons:
            result.decision = FuturesRiskDecision.REJECTED
        return result

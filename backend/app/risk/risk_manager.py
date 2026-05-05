from dataclasses import dataclass, field
from enum import StrEnum

from app.brokers.base import Balance, OrderRequest, OrderSide, Position
from app.core.modes import OperationMode, can_ai_execute, can_place_live_order


class RiskDecision(StrEnum):
    APPROVED = "APPROVED"
    REJECTED = "REJECTED"
    NEEDS_APPROVAL = "NEEDS_APPROVAL"


@dataclass
class RiskPolicy:
    max_order_notional: int = 1_000_000
    max_daily_loss: int = 200_000
    max_positions: int = 5
    max_symbol_exposure: int = 1_500_000
    enable_live_trading: bool = False
    enable_ai_execution: bool = False

    @classmethod
    def from_settings(cls, settings) -> "RiskPolicy":
        """Build a policy from app.core.config.Settings.

        Wires the four operator-tunable thresholds (RISK_MAX_*) plus the global
        safety flags (ENABLE_LIVE_TRADING / ENABLE_AI_EXECUTION) into the
        runtime policy. Direct instantiation `RiskPolicy(max_order_notional=...)`
        is preserved for tests that need targeted overrides.
        """
        return cls(
            max_order_notional  = settings.risk_max_order_notional,
            max_daily_loss      = settings.risk_max_daily_loss,
            max_positions       = settings.risk_max_positions,
            max_symbol_exposure = settings.risk_max_symbol_exposure,
            enable_live_trading = settings.enable_live_trading,
            enable_ai_execution = settings.enable_ai_execution,
        )


@dataclass
class RiskCheckResult:
    decision: RiskDecision
    reasons: list[str] = field(default_factory=list)
    passed: list[str] = field(default_factory=list)

    @property
    def allowed(self) -> bool:
        return self.decision == RiskDecision.APPROVED


class RiskManager:
    def __init__(self, policy: RiskPolicy | None = None) -> None:
        self.policy = policy or RiskPolicy()
        self.daily_realized_pnl = 0
        self.emergency_stop = False

    def set_emergency_stop(self, enabled: bool) -> None:
        self.emergency_stop = enabled

    def evaluate_order(
        self,
        *,
        order: OrderRequest,
        mode: OperationMode,
        balance: Balance,
        positions: list[Position],
        latest_price: int,
        requested_by_ai: bool = False,
    ) -> RiskCheckResult:
        # Hard short-circuit: emergency_stop is the operator's "stop everything"
        # signal. It must REJECT across every mode — including LIVE_MANUAL_APPROVAL
        # and LIVE_AI_ASSIST whose NEEDS_APPROVAL early-return below would
        # otherwise queue the order behind the alarm. Returning here also keeps
        # the audit row's reason list focused on the actual cause rather than
        # piling on incidental violations.
        if self.emergency_stop:
            return RiskCheckResult(
                decision=RiskDecision.REJECTED,
                reasons=["emergency stop is enabled"],
            )

        result = RiskCheckResult(decision=RiskDecision.APPROVED)

        order_notional = latest_price * order.quantity
        if order_notional > self.policy.max_order_notional:
            result.reasons.append("order notional exceeds max_order_notional")
        else:
            result.passed.append("order notional within limit")

        if self.daily_realized_pnl <= -abs(self.policy.max_daily_loss):
            result.reasons.append("daily loss limit reached")
        else:
            result.passed.append("daily loss limit not reached")

        if order.side == OrderSide.BUY and balance.cash < order_notional:
            result.reasons.append("insufficient cash")
        else:
            result.passed.append("cash/position availability preliminarily ok")

        current_symbols = {p.symbol for p in positions if p.quantity > 0}
        if order.side == OrderSide.BUY and order.symbol not in current_symbols and len(current_symbols) >= self.policy.max_positions:
            result.reasons.append("max positions reached")
        else:
            result.passed.append("position count within limit")

        symbol_position = next((p for p in positions if p.symbol == order.symbol), None)
        current_exposure = symbol_position.quantity * symbol_position.market_price if symbol_position else 0
        if order.side == OrderSide.BUY and current_exposure + order_notional > self.policy.max_symbol_exposure:
            result.reasons.append("symbol exposure limit exceeded")
        else:
            result.passed.append("symbol exposure within limit")

        if mode == OperationMode.LIVE_SHADOW:
            result.reasons.append("LIVE_SHADOW records signals only; live orders disabled")

        if mode in {OperationMode.LIVE_MANUAL_APPROVAL, OperationMode.LIVE_AI_ASSIST}:
            result.decision = RiskDecision.NEEDS_APPROVAL
            result.reasons.append("manual approval required by operation mode")
            return result

        if requested_by_ai and not can_ai_execute(mode, enable_ai_execution=self.policy.enable_ai_execution):
            result.reasons.append("AI execution is not allowed in current mode")

        if mode.name.startswith("LIVE") and not can_place_live_order(mode, enable_live_trading=self.policy.enable_live_trading):
            result.reasons.append("live trading is disabled by global safety flag")

        if result.reasons:
            result.decision = RiskDecision.REJECTED
        return result

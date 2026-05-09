from dataclasses import dataclass, field
from enum import StrEnum

from app.futures.margin_rules import (
    FuturesMarginRule,
    LeverageLimitRule,
    LiquidationRiskRule,
    MarginRuleDecision,
)
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

    # #48: maintenance margin / liquidation risk thresholds. default 값은
    # `docs/futures_margin_risk.md` §3에 따라 보수적 설정. 모두 향후 조정 가능.
    maintenance_margin_pct:       float = 10.0   # notional 대비 %
    liquidation_critical_pct:     float = 3.0    # distance ≤ 3% → REJECTED
    liquidation_warning_pct:      float = 7.0    # 3% < distance ≤ 7% → WARN


@dataclass
class FuturesRiskCheckResult:
    decision: FuturesRiskDecision
    reasons:  list[str] = field(default_factory=list)
    # #48: 명시적 Rule들이 누적한 advisory warnings + 산출 metric. 본 필드들은
    # default 빈 값으로 유지돼 기존 callers가 깨지지 않는다.
    warnings: list[str] = field(default_factory=list)
    metrics:  dict      = field(default_factory=dict)


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

    # ---------- virtual evaluation (151, refactored #48) ----------

    def _build_rules(
        self, *, contract_leverage_max: float | None = None,
    ) -> tuple[LeverageLimitRule, FuturesMarginRule, LiquidationRiskRule]:
        """#48: 정책에서 세 개의 Rule을 만든다 — 호출자가 contract spec의
        `leverage_max`를 주입하면 두 한도 중 더 보수적인 값이 효력."""
        leverage_rule = LeverageLimitRule(
            policy_max_leverage=self.policy.max_leverage,
            contract_leverage_max=contract_leverage_max,
        )
        margin_rule = FuturesMarginRule(
            max_margin_used=self.policy.max_margin_used,
            maintenance_margin_pct=self.policy.maintenance_margin_pct,
        )
        liq_rule = LiquidationRiskRule(
            critical_pct=self.policy.liquidation_critical_pct,
            warning_pct=self.policy.liquidation_warning_pct,
            maintenance_margin_pct=self.policy.maintenance_margin_pct,
        )
        return leverage_rule, margin_rule, liq_rule

    def evaluate_virtual_order(
        self,
        *,
        order:            FuturesOrderRequest,
        positions:        list[FuturesPosition],
        margin_used:      int,
        margin_available: int,
        mark_price:       int,
        leverage:         float,
        contract_leverage_max: float | None = None,
    ) -> FuturesRiskCheckResult:
        """MockFuturesBroker + FuturesSimulationEngine 경로 전용.

        강제 invariant:
        - leverage ≤ min(policy.max_leverage, contract.leverage_max)
        - 신규 계약 추가 후 contracts ≤ policy.max_contracts
        - margin_used + 추가 initial_margin ≤ policy.max_margin_used
        - margin_available ≥ 추가 initial_margin (잔고 부족 차단)
        - maintenance margin buffer (advisory WARN)
        - liquidation distance ≤ critical_pct → REJECTED (#48)
        - 3% < distance ≤ warning_pct → WARN (#48)
        - daily_realized_pnl > -max_daily_loss

        모두 통과하면 APPROVED. 본 함수는 `enable_futures_live_trading`을
        보지 않는다 — 가상 경로이므로 라이브 플래그와 무관.

        #48: 기존 inline 가드는 명시적 Rule(`LeverageLimitRule`, `FuturesMarginRule`,
        `LiquidationRiskRule`)로 위임. 기존 reason substring("leverage", "max_leverage",
        "margin_available", "max_margin_used", "contracts", "daily futures loss")은
        그대로 유지 — 기존 테스트 호환.
        """
        result = FuturesRiskCheckResult(decision=FuturesRiskDecision.APPROVED)
        leverage_rule, margin_rule, liq_rule = self._build_rules(
            contract_leverage_max=contract_leverage_max,
        )

        # ---- 1. Leverage ----
        lev_res = leverage_rule.check(leverage)
        if lev_res.decision == MarginRuleDecision.BLOCK:
            result.reasons.extend(lev_res.reasons)
        result.metrics.update(lev_res.metrics)

        # ---- 2. Contract count (계약 수 한도 — 단순 가드, Rule로 분리하지 않음) ----
        existing_qty = sum(p.quantity for p in positions if p.contract == order.contract)
        new_total = existing_qty + order.quantity
        if new_total > self.policy.max_contracts:
            result.reasons.append(
                f"contracts {new_total} exceeds max_contracts {self.policy.max_contracts}"
            )
        result.metrics["contracts_after"] = new_total

        # ---- 3. Mark price 안전 가드 ----
        if mark_price <= 0:
            result.reasons.append("mark_price must be positive")

        # ---- 4. Margin (initial / max_margin_used / maintenance buffer) ----
        # 본 Rule은 mark_price > 0일 때만 의미 있는 검사. mark_price ≤ 0이면 위
        # gate가 이미 reason을 누적했으므로 재시행하지 않는다.
        if mark_price > 0 and leverage > 0:
            margin_res = margin_rule.check(
                order=order,
                margin_used=margin_used,
                margin_available=margin_available,
                mark_price=mark_price,
                leverage=leverage,
            )
            if margin_res.decision == MarginRuleDecision.BLOCK:
                result.reasons.extend(margin_res.reasons)
            elif margin_res.decision == MarginRuleDecision.WARN:
                result.warnings.extend(margin_res.warnings)
            result.metrics.update(margin_res.metrics)

        # ---- 5. Liquidation distance (#48 신규) ----
        if mark_price > 0 and leverage > 0:
            liq_res = liq_rule.check(
                order=order, positions=positions,
                mark_price=mark_price, leverage=leverage,
            )
            if liq_res.decision == MarginRuleDecision.BLOCK:
                result.reasons.extend(liq_res.reasons)
            elif liq_res.decision == MarginRuleDecision.WARN:
                result.warnings.extend(liq_res.warnings)
            # liquidation_price / distance_pct를 metrics에 carry — UI 노출.
            for k, v in liq_res.metrics.items():
                result.metrics.setdefault(k, v)

        # ---- 6. Daily PnL ----
        if self.daily_realized_pnl <= -abs(self.policy.max_daily_loss):
            result.reasons.append("daily futures loss limit reached")

        if result.reasons:
            result.decision = FuturesRiskDecision.REJECTED
        return result

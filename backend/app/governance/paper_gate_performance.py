"""5-04: 28일/100건 Paper 성과 기준 (live promotion 차단 게이트, read-only).

실전 전환 *검토* 전에 Paper/KIS 모의 성과가 충분히 쌓이고 품질 기준을 만족하는지
판정한다. 하나라도 미달이면 live promotion 은 **차단**되며, 모든 기준을 충족해야만
`can_review_live_canary=True`(검토 가능성)가 된다.

**본 모듈은 실전 전환을 승인하지 않는다.** 모든 기준을 충족해도
`auto_live_promotion`/`is_live_authorization` 은 *항상 False* 이며 실전 주문은
생성되지 않는다. 다음 게이트(Live Capital Review #41 + Manual Approval #42 +
Canary #43)가 별도로 필요하다.

#72 `governance/paper_gate.py`(PASS=Live Manual Approval 검토 가능)보다 *더 엄격한*
성과 기준 집합 — win_rate / payoff / 연속손실 / 주문실패율 / drift / event
integrity 까지 포함한다.

CLAUDE.md 절대 원칙: broker / OrderExecutor / route_order / KIS live endpoint /
외부 HTTP / AI SDK import·호출 0건, 안전 flag 변경 0건, Secret/계좌번호 carry 0건,
DB write 0건 (입력 DTO 만 평가).
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Optional

PASS = "PASS"
WARN = "WARN"
FAIL = "FAIL"

# verdict
READY_FOR_LIVE_REVIEW = "READY_FOR_LIVE_REVIEW"   # 모든 기준 충족 — 검토 가능
BLOCKED               = "BLOCKED"                  # 기준 미달 — live promotion 차단
INSUFFICIENT_SAMPLE   = "INSUFFICIENT_SAMPLE"      # 표본 부족 — 평가 불가


@dataclass(frozen=True)
class PaperGatePerformancePolicy:
    """보수적 기준 (운영자가 더 *엄격하게만* 조정 가능 — 완화는 별도 검토)."""

    min_trades: int = 100
    min_trading_days: int = 28
    min_profit_factor: float = 1.3
    min_win_rate: float = 0.50
    min_payoff_ratio: float = 1.0
    min_expectancy: float = 0.0          # > 0 (초과)
    max_drawdown_pct: float = 0.10       # 10%
    max_consecutive_losses: int = 5
    max_order_failure_rate: float = 0.05
    max_rejected_rate: float = 0.03
    warn_partial_fill_rate: float = 0.30
    warn_avg_slippage_bps: float = 50.0


@dataclass(frozen=True)
class PerformanceCriteriaInput:
    """Paper/KIS 모의 성과 표본 + 품질 지표. Secret/계좌번호 필드 없음."""

    evaluated_trades: int = 0
    trading_days: int = 0
    win_rate: float = 0.0
    payoff_ratio: float = 0.0
    profit_factor: Optional[float] = None
    expectancy: float = 0.0
    average_return: float = 0.0
    max_drawdown_pct: float = 0.0
    max_consecutive_losses: int = 0
    order_failure_rate: float = 0.0
    rejected_rate: float = 0.0
    partial_fill_rate: float = 0.0
    avg_slippage_bps: float = 0.0
    daily_loss_limit_breach_count: int = 0
    kill_switch_trigger_count: int = 0
    portfolio_drift_critical_count: int = 0
    event_integrity_high_critical_count: int = 0
    secret_exposure_count: int = 0
    broker_order_type_mismatch_count: int = 0


@dataclass(frozen=True)
class CriterionCheck:
    name: str
    status: str
    threshold: str
    actual: str
    reason_code: Optional[str] = None


@dataclass(frozen=True)
class PaperGatePerformanceResult:
    verdict: str
    checks: list[CriterionCheck]
    pass_count: int
    warn_count: int
    fail_count: int
    reason_codes: list[str]
    can_review_live_canary: bool

    # 불변 — 기준 충족이 실전 승인/자동 전환이 아니다.
    auto_live_promotion: bool = False
    is_live_authorization: bool = False
    contains_secret: bool = False
    is_order_signal: bool = False

    def __post_init__(self) -> None:
        for name in ("auto_live_promotion", "is_live_authorization",
                     "contains_secret", "is_order_signal"):
            if getattr(self, name) is not False:
                raise ValueError(f"{name} must be False (criteria gate never promotes)")
        if self.can_review_live_canary and self.verdict != READY_FOR_LIVE_REVIEW:
            raise ValueError("can_review_live_canary requires READY_FOR_LIVE_REVIEW")

    @property
    def live_promotion_allowed(self) -> bool:
        # 표현상 'allowed' = 검토 가능성. 실제 주문/승인은 별도 게이트.
        return self.verdict == READY_FOR_LIVE_REVIEW

    def to_dict(self) -> dict[str, Any]:
        return {
            "verdict":                self.verdict,
            "checks":                 [c.__dict__ for c in self.checks],
            "pass_count":             self.pass_count,
            "warn_count":             self.warn_count,
            "fail_count":             self.fail_count,
            "reason_codes":           list(self.reason_codes),
            "can_review_live_canary": bool(self.can_review_live_canary),
            "live_promotion_allowed": self.live_promotion_allowed,
            "auto_live_promotion":    False,
            "is_live_authorization":  False,
            "contains_secret":        False,
            "is_order_signal":        False,
        }


def evaluate_paper_gate_performance(
    inp: PerformanceCriteriaInput,
    policy: PaperGatePerformancePolicy | None = None,
) -> PaperGatePerformanceResult:
    """성과 기준 평가. 하나라도 FAIL 이면 live promotion 차단."""
    th = policy or PaperGatePerformancePolicy()
    checks: list[CriterionCheck] = []
    reason_codes: list[str] = []

    def _add(name, ok, threshold, actual, code, *, warn=False):
        if ok:
            checks.append(CriterionCheck(name, PASS, threshold, actual))
        else:
            checks.append(CriterionCheck(name, WARN if warn else FAIL, threshold,
                                        actual, reason_code=code))
            if not warn:
                reason_codes.append(code)

    # ── 1. 최소 표본 (둘 다 요구). ──
    sample_ok = (inp.evaluated_trades >= th.min_trades
                 and inp.trading_days >= th.min_trading_days)
    _add("min_trades", inp.evaluated_trades >= th.min_trades,
         f">= {th.min_trades}", str(inp.evaluated_trades), "INSUFFICIENT_SAMPLE")
    _add("min_trading_days", inp.trading_days >= th.min_trading_days,
         f">= {th.min_trading_days}", str(inp.trading_days), "INSUFFICIENT_DAYS")

    # 표본 부족이면 성과 지표 평가는 신뢰할 수 없음 → INSUFFICIENT_SAMPLE 종결.
    if not sample_ok:
        pass_count = sum(1 for c in checks if c.status == PASS)
        return PaperGatePerformanceResult(
            verdict=INSUFFICIENT_SAMPLE,
            checks=checks,
            pass_count=pass_count,
            warn_count=0,
            fail_count=sum(1 for c in checks if c.status == FAIL),
            reason_codes=sorted(set(reason_codes)),
            can_review_live_canary=False,
        )

    # ── 2. 성과 기준. ──
    pf = inp.profit_factor
    _add("profit_factor", pf is not None and pf >= th.min_profit_factor,
         f">= {th.min_profit_factor}", "n/a" if pf is None else f"{pf:.2f}",
         "LOW_PROFIT_FACTOR")
    _add("win_rate", inp.win_rate >= th.min_win_rate,
         f">= {th.min_win_rate:.0%}", f"{inp.win_rate:.0%}", "LOW_WIN_RATE")
    _add("payoff_ratio", inp.payoff_ratio >= th.min_payoff_ratio,
         f">= {th.min_payoff_ratio}", f"{inp.payoff_ratio:.2f}", "LOW_PAYOFF_RATIO")
    _add("expectancy", inp.expectancy > th.min_expectancy,
         "> 0", f"{inp.expectancy:.2f}", "NON_POSITIVE_EXPECTANCY")
    _add("average_return", inp.average_return > 0,
         "> 0", f"{inp.average_return:.4f}", "NON_POSITIVE_AVERAGE_RETURN")

    # ── 3. 리스크 기준. ──
    _add("max_drawdown", inp.max_drawdown_pct <= th.max_drawdown_pct,
         f"<= {th.max_drawdown_pct:.0%}", f"{inp.max_drawdown_pct:.2%}",
         "HIGH_MAX_DRAWDOWN")
    _add("max_consecutive_losses",
         inp.max_consecutive_losses <= th.max_consecutive_losses,
         f"<= {th.max_consecutive_losses}", str(inp.max_consecutive_losses),
         "HIGH_CONSECUTIVE_LOSSES")
    _add("daily_loss_limit_breach", inp.daily_loss_limit_breach_count == 0,
         "== 0", str(inp.daily_loss_limit_breach_count), "DAILY_LOSS_LIMIT_BREACH")
    _add("kill_switch_trigger", inp.kill_switch_trigger_count == 0,
         "== 0", str(inp.kill_switch_trigger_count), "KILL_SWITCH_TRIGGERED")

    # ── 4. 주문 품질. ──
    _add("order_failure_rate", inp.order_failure_rate <= th.max_order_failure_rate,
         f"<= {th.max_order_failure_rate:.0%}", f"{inp.order_failure_rate:.2%}",
         "HIGH_ORDER_FAILURE_RATE")
    _add("rejected_rate", inp.rejected_rate <= th.max_rejected_rate,
         f"<= {th.max_rejected_rate:.0%}", f"{inp.rejected_rate:.2%}",
         "HIGH_REJECTED_RATE")
    # partial fill / slippage 는 WARN (차단 아님).
    _add("partial_fill_rate", inp.partial_fill_rate <= th.warn_partial_fill_rate,
         f"<= {th.warn_partial_fill_rate:.0%}", f"{inp.partial_fill_rate:.2%}",
         "HIGH_PARTIAL_FILL_RATE", warn=True)
    _add("avg_slippage_bps", inp.avg_slippage_bps <= th.warn_avg_slippage_bps,
         f"<= {th.warn_avg_slippage_bps} bps", f"{inp.avg_slippage_bps:.1f} bps",
         "HIGH_SLIPPAGE", warn=True)

    # ── 5. 무결성 / 정합성 / 보안. ──
    _add("portfolio_drift", inp.portfolio_drift_critical_count == 0,
         "== 0", str(inp.portfolio_drift_critical_count), "PORTFOLIO_DRIFT_CRITICAL")
    _add("event_integrity", inp.event_integrity_high_critical_count == 0,
         "== 0", str(inp.event_integrity_high_critical_count),
         "EVENT_INTEGRITY_CRITICAL")
    _add("secret_exposure", inp.secret_exposure_count == 0,
         "== 0", str(inp.secret_exposure_count), "SECRET_EXPOSURE_DETECTED")
    _add("broker_order_type_mismatch", inp.broker_order_type_mismatch_count == 0,
         "== 0", str(inp.broker_order_type_mismatch_count),
         "BROKER_ORDER_TYPE_MISMATCH")

    pass_count = sum(1 for c in checks if c.status == PASS)
    warn_count = sum(1 for c in checks if c.status == WARN)
    fail_count = sum(1 for c in checks if c.status == FAIL)

    if fail_count > 0:
        reason_codes.append("LIVE_PROMOTION_BLOCKED")
        verdict = BLOCKED
        can_review = False
    else:
        reason_codes.append("READY_FOR_LIVE_REVIEW")
        verdict = READY_FOR_LIVE_REVIEW
        can_review = True

    return PaperGatePerformanceResult(
        verdict=verdict,
        checks=checks,
        pass_count=pass_count,
        warn_count=warn_count,
        fail_count=fail_count,
        reason_codes=sorted(set(reason_codes)),
        can_review_live_canary=can_review,
    )


__all__ = [
    "PASS", "WARN", "FAIL",
    "READY_FOR_LIVE_REVIEW", "BLOCKED", "INSUFFICIENT_SAMPLE",
    "PaperGatePerformancePolicy",
    "PerformanceCriteriaInput",
    "CriterionCheck",
    "PaperGatePerformanceResult",
    "evaluate_paper_gate_performance",
]

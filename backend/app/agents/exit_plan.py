"""2-09: ExitPlan — ChiefTradingAgent/Agent Council BUY 판단의 필수 청산 계획.

BUY 진입에는 반드시 stop_loss / take_profit / exit_strategy 가 포함된 *유효한*
exit_plan 이 있어야 한다. 없거나 비정상이면 안전하지 않은 진입으로 간주하고 BUY 를
HOLD 로 강등한다.

## 절대 invariant (CLAUDE.md)
- 본 모듈은 *계획 수립/검증 전용* — broker / OrderExecutor / route_order / 외부
  HTTP / stop·조건주문 API import 0건. **실제 stop order 를 발주하지 않는다** —
  plan(dict) 만 만들고 저장한다.
- RiskManager / PermissionGate 를 대체/우회하지 않는다 (Agent Council 사전 안전
  필터). 실제 주문은 여전히 sanctioned 경로를 모두 통과해야 한다.
- `ExitPlan` / `ExitPlanValidationResult` 는 주문 신호가 아니다 (advisory).
- 결정적(deterministic).
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Any

# ── 검증 reason_code ──
EXIT_PLAN_OK         = "EXIT_PLAN_OK"
EXIT_PLAN_MISSING    = "EXIT_PLAN_MISSING"
STOP_LOSS_MISSING    = "STOP_LOSS_MISSING"
TAKE_PROFIT_MISSING  = "TAKE_PROFIT_MISSING"
STOP_LOSS_INVALID    = "STOP_LOSS_INVALID"
TAKE_PROFIT_INVALID  = "TAKE_PROFIT_INVALID"
RISK_REWARD_INVALID  = "RISK_REWARD_INVALID"
EXIT_STRATEGY_MISSING = "EXIT_STRATEGY_MISSING"
EXIT_PLAN_INVALID    = "EXIT_PLAN_INVALID"

DEFAULT_EXIT_STRATEGY = "STOP_LOSS_TAKE_PROFIT"

# risk_profile 별 default 손절/익절 % (프로젝트 risk 설정이 있으면 caller 가 우선).
_DEFAULT_PCT_BY_PROFILE: dict[str, tuple[float, float]] = {
    "CONSERVATIVE": (1.5, 2.0),
    "BALANCED":     (2.0, 3.0),
    "AGGRESSIVE":   (2.5, 4.0),
}

# 비정상 임계 — 손절/익절 % 가 이 범위를 벗어나면 invalid.
_MAX_STOP_LOSS_PCT   = 20.0
_MAX_TAKE_PROFIT_PCT = 50.0
_MIN_PCT             = 0.1


def _f(v: Any) -> float | None:
    try:
        if v is None:
            return None
        f = float(v)
        if math.isnan(f) or math.isinf(f):
            return None
        return f
    except (TypeError, ValueError):
        return None


@dataclass(frozen=True)
class ExitPlan:
    """BUY 진입의 청산 계획 — advisory plan(주문 신호 아님)."""

    entry_price:       float
    stop_loss:         float
    take_profit:       float
    stop_loss_pct:     float
    take_profit_pct:   float
    risk_reward_ratio: float
    exit_strategy:     str = DEFAULT_EXIT_STRATEGY
    reason:            str = ""
    created_by:        str = "ChiefTradingAgent"
    trailing_stop:     bool = False
    max_holding_minutes: int | None = 60

    is_order_signal:       bool = False
    is_live_authorization: bool = False

    def __post_init__(self) -> None:
        if self.is_order_signal is not False:
            raise ValueError("ExitPlan.is_order_signal must be False")
        if self.is_live_authorization is not False:
            raise ValueError("ExitPlan.is_live_authorization must be False")

    def to_dict(self) -> dict[str, Any]:
        return {
            "entry_price":       self.entry_price,
            "stop_loss":         self.stop_loss,
            "take_profit":       self.take_profit,
            "stop_loss_pct":     self.stop_loss_pct,
            "take_profit_pct":   self.take_profit_pct,
            "risk_reward_ratio": self.risk_reward_ratio,
            "exit_strategy":     self.exit_strategy,
            "reason":            self.reason,
            "created_by":        self.created_by,
            "trailing_stop":     bool(self.trailing_stop),
            "max_holding_minutes": self.max_holding_minutes,
            "is_order_signal":       False,
            "is_live_authorization": False,
        }


@dataclass(frozen=True)
class ExitPlanValidationResult:
    """exit_plan 검증 결과 — invalid 면 forced_action=HOLD."""

    valid:          bool
    reason_code:    str
    reason:         str = ""
    missing_fields: list[str] = field(default_factory=list)
    forced_action:  str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "valid":          bool(self.valid),
            "reason_code":    self.reason_code,
            "reason":         self.reason,
            "missing_fields": list(self.missing_fields),
            "forced_action":  self.forced_action,
        }


def build_default_exit_plan(
    *,
    entry_price: float | None,
    risk_profile: str = "BALANCED",
    stop_loss_pct: float | None = None,
    take_profit_pct: float | None = None,
    created_by: str = "ChiefTradingAgent",
    exit_strategy: str = DEFAULT_EXIT_STRATEGY,
    trailing_stop: bool = False,
    max_holding_minutes: int | None = 60,
) -> ExitPlan | None:
    """entry_price + risk_profile → 기본 exit_plan (절대 손절/익절가 포함).

    `stop_loss_pct`/`take_profit_pct` 가 주어지면(프로젝트 risk 설정) 우선 사용,
    아니면 profile 기본값. entry_price 가 비정상이면 None(=BUY 차단 유발).
    **실제 stop order 를 발주하지 않는다** — plan dataclass 만 만든다.
    """
    entry = _f(entry_price)
    if entry is None or entry <= 0:
        return None
    profile = str(risk_profile or "BALANCED").upper()
    d_sl, d_tp = _DEFAULT_PCT_BY_PROFILE.get(profile, _DEFAULT_PCT_BY_PROFILE["BALANCED"])
    sl_pct = _f(stop_loss_pct)
    tp_pct = _f(take_profit_pct)
    sl_pct = abs(sl_pct) if sl_pct is not None and sl_pct != 0 else d_sl
    tp_pct = abs(tp_pct) if tp_pct is not None and tp_pct != 0 else d_tp

    stop_loss = round(entry * (1.0 - sl_pct / 100.0), 4)
    take_profit = round(entry * (1.0 + tp_pct / 100.0), 4)
    rr = round(tp_pct / sl_pct, 4) if sl_pct > 0 else 0.0
    return ExitPlan(
        entry_price=entry, stop_loss=stop_loss, take_profit=take_profit,
        stop_loss_pct=round(sl_pct, 4), take_profit_pct=round(tp_pct, 4),
        risk_reward_ratio=rr, exit_strategy=exit_strategy,
        reason=f"진입가 대비 손절 -{sl_pct:.2f}%, 익절 +{tp_pct:.2f}% ({profile})",
        created_by=created_by, trailing_stop=trailing_stop,
        max_holding_minutes=max_holding_minutes,
    )


def validate_exit_plan(
    exit_plan: dict[str, Any] | None,
    *,
    current_price: float | None,
    side: str = "BUY",
) -> ExitPlanValidationResult:
    """exit_plan 검증 (deterministic). BUY 기준 stop<entry / take>entry 등.

    SELL / HOLD 는 본 정책에서 exit_plan 필수 아님 — valid 로 통과.
    """
    s = str(side or "BUY").upper()
    if s != "BUY":
        return ExitPlanValidationResult(
            valid=True, reason_code=EXIT_PLAN_OK,
            reason=f"{s} 는 exit_plan 필수 정책 대상 아님.")

    if not isinstance(exit_plan, dict) or not exit_plan:
        return ExitPlanValidationResult(
            valid=False, reason_code=EXIT_PLAN_MISSING,
            reason="exit_plan 이 없습니다 — 안전하지 않은 진입.",
            missing_fields=["exit_plan"], forced_action="HOLD")

    entry = _f(exit_plan.get("entry_price")) or _f(current_price)
    stop_loss = _f(exit_plan.get("stop_loss"))
    take_profit = _f(exit_plan.get("take_profit"))
    strategy = exit_plan.get("exit_strategy") or exit_plan.get("exit_reason") \
        or exit_plan.get("reason")
    rr = _f(exit_plan.get("risk_reward_ratio"))
    cur = _f(current_price) or entry

    missing: list[str] = []
    if stop_loss is None:
        missing.append("stop_loss")
    if take_profit is None:
        missing.append("take_profit")
    if not strategy:
        missing.append("exit_strategy")

    def _fail(code, reason):
        return ExitPlanValidationResult(valid=False, reason_code=code, reason=reason,
                                        missing_fields=missing, forced_action="HOLD")

    if entry is None or entry <= 0:
        return _fail(EXIT_PLAN_INVALID, "entry_price 가 비정상입니다.")
    if stop_loss is None:
        return _fail(STOP_LOSS_MISSING, "stop_loss 가 없습니다.")
    if take_profit is None:
        return _fail(TAKE_PROFIT_MISSING, "take_profit 이 없습니다.")
    if not strategy:
        return _fail(EXIT_STRATEGY_MISSING, "exit_strategy(또는 exit_reason)가 없습니다.")

    # BUY: 손절은 진입가/현재가보다 *아래*, 익절은 *위*.
    if stop_loss <= 0 or stop_loss >= entry or stop_loss >= cur:
        return _fail(STOP_LOSS_INVALID,
                     f"stop_loss({stop_loss}) 가 진입가({entry})/현재가({cur}) 이상 — 비정상.")
    if take_profit <= entry or take_profit <= cur:
        return _fail(TAKE_PROFIT_INVALID,
                     f"take_profit({take_profit}) 가 진입가({entry})/현재가({cur}) 이하 — 비정상.")

    # 손절/익절 폭이 과도하지 않은지.
    sl_pct = (entry - stop_loss) / entry * 100.0
    tp_pct = (take_profit - entry) / entry * 100.0
    if sl_pct < _MIN_PCT or sl_pct > _MAX_STOP_LOSS_PCT:
        return _fail(STOP_LOSS_INVALID, f"손절 폭 {sl_pct:.2f}% 가 허용 범위를 벗어남.")
    if tp_pct < _MIN_PCT or tp_pct > _MAX_TAKE_PROFIT_PCT:
        return _fail(TAKE_PROFIT_INVALID, f"익절 폭 {tp_pct:.2f}% 가 허용 범위를 벗어남.")

    # risk_reward_ratio — 없으면 계산, 양수여야.
    if rr is None:
        rr = round(tp_pct / sl_pct, 4) if sl_pct > 0 else 0.0
    if rr <= 0:
        return _fail(RISK_REWARD_INVALID, f"risk_reward_ratio({rr}) 가 비정상입니다.")

    return ExitPlanValidationResult(
        valid=True, reason_code=EXIT_PLAN_OK,
        reason="유효한 손절/익절 계획입니다.")


__all__ = [
    "ExitPlan", "ExitPlanValidationResult",
    "build_default_exit_plan", "validate_exit_plan",
    "EXIT_PLAN_OK", "EXIT_PLAN_MISSING", "STOP_LOSS_MISSING", "TAKE_PROFIT_MISSING",
    "STOP_LOSS_INVALID", "TAKE_PROFIT_INVALID", "RISK_REWARD_INVALID",
    "EXIT_STRATEGY_MISSING", "EXIT_PLAN_INVALID", "DEFAULT_EXIT_STRATEGY",
]

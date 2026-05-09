from dataclasses import dataclass, field
from datetime import datetime, time, timedelta, timezone
from enum import StrEnum
from typing import Any

from app.brokers.base import Balance, OrderRequest, OrderSide, Position
from app.core.modes import OperationMode, can_ai_execute, can_place_live_order
from app.risk.loss_limits import (
    ConsecutiveLossRule,
    DailyLossLimitRule,
    WeeklyLossLimitRule,
    evaluate_loss_limits,
)
from app.risk.position_limits import (
    PositionLimitInput,
    PositionLimitRule,
    policy_from_risk_policy,
)


# #43: LIVE_SHADOW 모드에서 RiskManager가 항상 누적하는 reason 문자열. 본 reason
# *외*에 다른 risk reason이 0건이면 다른 가드는 모두 통과한 후보
# (would-have-APPROVED) — route_order가 이 상수를 import해
# ShadowTrade.would_have_decision을 산출한다.
SHADOW_RECORD_ONLY_REASON = "LIVE_SHADOW records signals only; live orders disabled"

# #43: would-have 분석에서 *제외*해야 하는 정책 게이트 reason들. 본 reason들은
# 실제 risk rule이 아니라 운영 단계 게이트(모드/환경 flag)로, 운영자가 다음
# 단계로 승격하면 자연스럽게 사라진다 — would-have-APPROVED 카운트에서 빠진다.
#  - SHADOW_RECORD_ONLY_REASON: LIVE_SHADOW 모드 자체의 read-only 게이트.
#  - "live trading is disabled by global safety flag": ENABLE_LIVE_TRADING=False
#    (기본). LIVE_* 모드가 환경 flag로 차단된 경우.
SHADOW_GATE_REASONS = frozenset({
    SHADOW_RECORD_ONLY_REASON,
    "live trading is disabled by global safety flag",
})


_KST = timezone(timedelta(hours=9))
# 한국 거래소 정규 거래 시간 (KST). 동시호가 / 시간외 거래는 별도 — MVP는 정규만.
_MARKET_OPEN_KST  = time(9, 0)
_MARKET_CLOSE_KST = time(15, 30)


def _is_market_open(now: datetime | None = None) -> bool:
    """KST 평일 09:00–15:30 사이면 True. 토/일은 항상 False."""
    if now is None:
        now = datetime.now(_KST)
    elif now.tzinfo is None:
        now = now.replace(tzinfo=timezone.utc).astimezone(_KST)
    else:
        now = now.astimezone(_KST)
    if now.weekday() >= 5:  # Saturday=5, Sunday=6
        return False
    t = now.time()
    return _MARKET_OPEN_KST <= t < _MARKET_CLOSE_KST


class RiskDecision(StrEnum):
    APPROVED = "APPROVED"
    REJECTED = "REJECTED"
    NEEDS_APPROVAL = "NEEDS_APPROVAL"
    # #34: 보강 enum 값 — 본 PR에서 evaluate_order는 기존 3개만 사용해 backwards
    # compat 유지. check_order는 호출 컨텍스트에서 더 풍부한 의미 (REDUCED:
    # 사이즈 축소 권고, BLOCKED: emergency stop / market regime BLOCK_NEW_BUY
    # / safety flag로 인한 hard 차단)를 noop이 아닌 별도 enum으로 carry 가능.
    # 운영자가 audit row에서 decision=BLOCKED를 보면 "REJECTED와 다른 강제
    # 차단"임을 즉시 인지.
    REDUCED = "REDUCED"
    BLOCKED = "BLOCKED"


@dataclass
class RiskPolicy:
    max_order_notional: int = 1_000_000
    max_daily_loss: int = 200_000
    max_positions: int = 5
    max_symbol_exposure: int = 1_500_000
    enable_live_trading: bool = False
    enable_ai_execution: bool = False
    # 143: 시세 데이터의 최대 허용 age (초). RiskManager가 정책 위반으로 즉시 REJECT.
    # 0 또는 음수는 검사 비활성 — 기존 호출 경로가 timestamp를 안 보내는 경우와 동일.
    stale_price_max_age_seconds: int = 60
    # 158: AI 제안의 최소 confidence 임계 (0-100). requested_by_ai=True인 주문이
    # signal_confidence < 임계이면 REJECTED. 0이면 검사 비활성 — 기존 호출 호환.
    # CLAUDE.md '손실 방어 우선' — 신뢰도 낮은 AI 제안이 자동 체결로 가지 않도록.
    min_ai_confidence: int = 0
    # 159: AI 제안의 explainability invariant. True이면 requested_by_ai=True 주문이
    # ai_decision_meta.reasons (non-empty list)를 가져야 한다. CLAUDE.md '감사 로그
    # 우선' — 사후 분석 시 'AI가 왜 그 주문을 만들었나' 답할 수 있게 강제.
    # 기본 True — 새 에이전트가 reasoning 없이 주문 만들면 거부. 운영자가 backwards
    # compat 위해 끌 수도 있지만 LIVE 단계에서는 절대 끄지 말 것 (운영 가이드).
    enforce_ai_reasoning: bool = True
    # 161: AI 제안 rate limit — (strategy, symbol)별 윈도우 안에서 AI 주문 수
    # 카운트 임계. 0이면 검사 비활성 (기본). LLM bug / 무한 루프 방어.
    ai_rate_limit_window_seconds: int = 60
    ai_rate_limit_max_count:      int = 0
    # 174: equity 대비 단일 주문 명목의 비율 한도 (%). 0이면 검사 비활성 (기본).
    # max_order_notional이 절대값 한도라면 본 가드는 자본 대비 *상대* 한도 —
    # 운영자 자본이 증감해도 자동 스케일. 권장 5~15% (단타 운영 가정).
    max_position_size_pct: float = 0.0
    # 175: 거래 허용 symbol whitelist. 빈 set이면 검사 비활성 (기본 = 모든 symbol
    # 허용). 비어 있지 않으면 미등록 symbol 주문 거부 — 운영자가 검증한 종목만
    # 자동 흐름에 노출.
    symbol_whitelist: frozenset[str] = field(default_factory=frozenset)
    # 176: 한국 시장 시간(09:00-15:30 KST 평일) 외 주문 거부. False면 비활성 (기본).
    # 단타 자동매매가 SIMULATION/PAPER에서 24/7 돌아도 LIVE 단계에서는 명시적
    # 옵트인 권장.
    enforce_market_hours: bool = False
    # 177: 시스템 전체 주문 rate limit (strategy / AI / manual 통합). 161의
    # AI-specific 한도와 별개. 0이면 비활성 (기본).
    global_rate_limit_window_seconds: int = 60
    global_rate_limit_max_count:      int = 0
    # 178: AI 주문 kill-switch. emergency_stop은 모든 주문 차단, 본 toggle은
    # requested_by_ai=True만 차단 — 운영자가 "AI만 멈추고 strategy / manual은
    # 유지"하고 싶을 때. 기본 False (비활성). RiskManager 인스턴스의 in-memory
    # 토글로도 사용 가능 (set_ai_disabled).
    disable_ai_orders: bool = False
    # 179: 모든 보유 포지션 합 노출 한도. max_symbol_exposure가 단일 종목 한도라면
    # 본 항목은 *총 노출* 한도. 0이면 비활성 (기본). 자본 대비 비율 한도는
    # max_total_exposure_pct로 별도.
    max_total_exposure:     int   = 0
    max_total_exposure_pct: float = 0.0
    # 181: 종목별 노출의 자본 대비 % 한도. max_symbol_exposure (절대값)에 보완 —
    # 자본 증감 시 자동 스케일. 0이면 비활성 (기본).
    max_symbol_exposure_pct: float = 0.0
    # 182: 최근 N건의 audit row가 모두 REJECTED이면 자동으로 emergency_stop 토글.
    # 시스템 이상 자동 감지 — LLM bug, broker 장애, stale price 연속 발생 등.
    # 0이면 비활성 (기본). 권장 5~10건.
    auto_stop_consecutive_rejections: int = 0
    # 183: 일일(KST date) 최대 주문 횟수. decision 무관 — 모든 audit row 카운트.
    # 시스템 폭주 / 비용 제어. 0이면 비활성 (기본).
    max_orders_per_day: int = 0

    # ------------------------------------------------------------------
    # #36: Loss Limit Rules
    # ------------------------------------------------------------------
    # 일일 / 주간 / 연속손실 임계. 모두 0이면 비활성 (기존 동작 보존).
    # daily_loss_warn_pct / daily_loss_reduce_pct는 max_daily_loss의 soft
    # 단계 — 50% / 70% 도달 시 WARN/REDUCE_SIZE 권고. 100%(=max_daily_loss)는
    # 기존 hard reject "daily loss limit reached"가 그대로 잡는다.
    weekly_loss_limit:    int   = 0    # 주간 누적 realized PnL 한도 (양수)
    consecutive_loss_limit: int = 0    # 연속 손실 거래 수 임계
    daily_loss_warn_pct:    float = 0.0  # max_daily_loss의 X%; 0 = 비활성
    daily_loss_reduce_pct:  float = 0.0  # max_daily_loss의 Y%; 0 = 비활성
    weekly_loss_warn_pct:   float = 0.0
    weekly_loss_reduce_pct: float = 0.0

    # ------------------------------------------------------------------
    # #38: Order Guard — duplicate / cooldown / pending pre-trade guard.
    # ------------------------------------------------------------------
    # 모든 필드 default = 검사 비활성 (기존 호환). 운영자가 명시 활성화 시
    # route_order이 OrderGuard를 호출해 RiskManager 평가 *전*에 흐름 차원
    # 가드를 적용한다.
    order_guard_duplicate_window_seconds:           int   = 0
    order_guard_symbol_cooldown_seconds:            int   = 0
    order_guard_strategy_symbol_cooldown_seconds:   int   = 0
    order_guard_post_exit_cooldown_seconds:         int   = 0
    order_guard_ai_extra_cooldown_seconds:          int   = 0
    order_guard_block_when_pending_same_side:       bool  = False
    order_guard_price_bucket_pct:                   float = 0.5

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
            stale_price_max_age_seconds = settings.stale_price_max_age_seconds,
            min_ai_confidence   = settings.min_ai_confidence,
            enforce_ai_reasoning = settings.enforce_ai_reasoning,
            ai_rate_limit_window_seconds = settings.ai_rate_limit_window_seconds,
            ai_rate_limit_max_count      = settings.ai_rate_limit_max_count,
            max_position_size_pct        = settings.max_position_size_pct,
            symbol_whitelist             = frozenset(settings.symbol_whitelist_set()),
            enforce_market_hours         = settings.enforce_market_hours,
            global_rate_limit_window_seconds = settings.global_rate_limit_window_seconds,
            global_rate_limit_max_count      = settings.global_rate_limit_max_count,
            disable_ai_orders                = settings.disable_ai_orders,
            max_total_exposure               = settings.max_total_exposure,
            max_total_exposure_pct           = settings.max_total_exposure_pct,
            max_symbol_exposure_pct          = settings.max_symbol_exposure_pct,
            auto_stop_consecutive_rejections = settings.auto_stop_consecutive_rejections,
            max_orders_per_day               = settings.max_orders_per_day,
        )


@dataclass
class RiskCheckResult:
    decision: RiskDecision
    reasons: list[str] = field(default_factory=list)
    passed: list[str] = field(default_factory=list)
    # #34: 표준 진입점 (`check_order`)이 채우는 보강 필드들. evaluate_order
    # 호출 경로는 모두 default 값을 두고 — backwards compat 유지.
    warnings:         list[str]           = field(default_factory=list)
    risk_score:       int | None          = None
    blocked_by:       str | None          = None
    required_action:  str | None          = None
    normalized_order: OrderRequest | None = None
    evaluated_at:     datetime            = field(
        default_factory=lambda: datetime.now(timezone.utc)
    )

    @property
    def allowed(self) -> bool:
        return self.decision == RiskDecision.APPROVED

    @property
    def status(self) -> str:
        """decision의 string mirror — audit/UI/API 직렬화에 사용."""
        return self.decision.value

    def to_dict(self) -> dict:
        return {
            "decision":         self.decision.value,
            "status":           self.status,
            "allowed":          self.allowed,
            "reasons":          list(self.reasons),
            "passed":           list(self.passed),
            "warnings":         list(self.warnings),
            "risk_score":       self.risk_score,
            "blocked_by":       self.blocked_by,
            "required_action":  self.required_action,
            "normalized_order": (
                self.normalized_order.model_dump()
                if self.normalized_order is not None else None
            ),
            "evaluated_at":     self.evaluated_at.isoformat(),
        }


@dataclass
class RiskContext:
    """`check_order`의 표준 입력 컨텍스트 (#34).

    `evaluate_order`가 keyword args를 받는 대신, 본 dataclass에 평가 시점의
    모든 상태를 담는다. 호출자(route_order)가 broker / DB로부터 수집한
    스냅샷을 한 객체로 전달 — 추후 stage가 추가되어도 시그니처가 안정적.

    필드:
    - mode: 운용모드 (SIMULATION / PAPER / LIVE_*)
    - balance / positions / latest_price / latest_price_timestamp: broker 스냅샷
    - requested_by_ai: AI 경로 여부
    - market_regime / market_regime_decision: 32 MarketRegimeFilter 출력
      (advisory — `BLOCK_NEW_BUY`이면 신규 BUY hard reject)
    - emergency_stop_override: True면 RiskManager 내부 토글을 무시하고 강제
      차단. None이면 기본 `risk.emergency_stop` 그대로 사용.
    - operator_id / metadata: 자유 carry — audit 보강용
    """
    mode:                   OperationMode
    balance:                Balance
    positions:              list[Position]
    latest_price:           int
    latest_price_timestamp: datetime | None = None
    requested_by_ai:        bool = False
    market_regime:          str | None = None
    market_regime_decision: str | None = None  # ALLOW/REDUCE_SIZE/WATCH_ONLY/BLOCK_NEW_BUY
    emergency_stop_override: bool | None = None
    operator_id:            str | None = None
    metadata:               dict[str, Any] | None = None


class RiskManager:
    def __init__(self, policy: RiskPolicy | None = None) -> None:
        self.policy = policy or RiskPolicy()
        self.daily_realized_pnl = 0
        self.emergency_stop = False

    def set_emergency_stop(self, enabled: bool) -> None:
        self.emergency_stop = enabled

    def set_ai_disabled(self, enabled: bool) -> None:
        """178: 운영자 in-memory 토글. RiskPolicy의 default 외에도 런타임 변경
        가능 — emergency_stop과 동일 패턴."""
        self.policy.disable_ai_orders = enabled

    def evaluate_order(
        self,
        *,
        order: OrderRequest,
        mode: OperationMode,
        balance: Balance,
        positions: list[Position],
        latest_price: int,
        requested_by_ai: bool = False,
        latest_price_timestamp: datetime | None = None,
        # #36: 호출자(route_order)가 주입할 수 있는 누적 손실 카운터. 미주입
        # (None)이면 본 rule들은 검사 대상 데이터 부재로 ALLOW. 기존 호출자
        # 시그니처 호환 — keyword-only + default None.
        weekly_realized_pnl:    int | None = None,
        consecutive_loss_count: int | None = None,
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

        # 178: AI kill-switch — emergency_stop과 같은 hard short-circuit이지만
        # requested_by_ai=True인 주문에만 적용. 운영자가 strategy/manual은 두고
        # AI만 정지하고 싶을 때.
        if requested_by_ai and self.policy.disable_ai_orders:
            return RiskCheckResult(
                decision=RiskDecision.REJECTED,
                reasons=["AI orders are disabled by operator (kill-switch)"],
            )

        # 143: stale price도 emergency_stop과 같은 hard-reject — broker 응답이
        # 너무 오래되면 RiskManager가 사이즈/포지션을 평가할 수 있는 근거가 없다.
        # threshold ≤ 0 또는 timestamp 미제공이면 검사 우회 (기존 호출 경로 호환).
        threshold = self.policy.stale_price_max_age_seconds
        if latest_price_timestamp is not None and threshold > 0:
            now = datetime.now(timezone.utc)
            ts  = latest_price_timestamp
            if ts.tzinfo is None:
                # naive timestamp는 UTC로 가정 — broker는 UTC isoformat을 약속.
                ts = ts.replace(tzinfo=timezone.utc)
            age = (now - ts).total_seconds()
            if age > threshold:
                return RiskCheckResult(
                    decision=RiskDecision.REJECTED,
                    reasons=[
                        f"latest price is stale ({age:.0f}s > {threshold}s threshold)"
                    ],
                )

        result = RiskCheckResult(decision=RiskDecision.APPROVED)

        # 175: symbol whitelist. 비어있지 않으면 미등록 symbol 거부.
        if self.policy.symbol_whitelist and order.symbol not in self.policy.symbol_whitelist:
            result.reasons.append(
                f"symbol '{order.symbol}' not in whitelist"
            )
        elif self.policy.symbol_whitelist:
            result.passed.append("symbol in whitelist")

        # 176: 한국 시장 시간 외 거부 — 옵트인.
        if self.policy.enforce_market_hours and not _is_market_open():
            result.reasons.append(
                "market is closed (KST weekday 09:00–15:30 only)"
            )
        elif self.policy.enforce_market_hours:
            result.passed.append("market is open")

        # 158: AI confidence threshold. requested_by_ai=True인 주문에 한해
        # signal_confidence가 임계 미달이면 거부. 임계 ≤ 0이면 검사 비활성.
        # confidence가 None이면 (AI 외 경로) 검사 적용 안 함.
        min_conf = self.policy.min_ai_confidence
        if requested_by_ai and min_conf > 0:
            conf = order.signal_confidence
            if conf is None or conf < min_conf:
                result.reasons.append(
                    f"AI signal confidence {conf if conf is not None else 'missing'} "
                    f"< min_ai_confidence {min_conf}"
                )
            else:
                result.passed.append("AI signal confidence above threshold")

        # 159: AI proposal explainability — requested_by_ai=True 주문은 audit
        # 가능한 reasoning을 동반해야 한다. ai_decision_meta가 None이거나
        # reasons가 비어있으면 거부. 운영자가 enforce_ai_reasoning=False로
        # 끄지 않는 한 invariant.
        if requested_by_ai and self.policy.enforce_ai_reasoning:
            meta = order.ai_decision_meta
            if not meta or not meta.get("reasons"):
                result.reasons.append("AI proposal missing reasoning (ai_decision_meta.reasons required)")
            else:
                result.passed.append("AI proposal includes reasoning")

        # #35: position-limit 검사들을 PositionLimitRule에 위임 — single source
        # of truth. evaluate_order의 inline 로직 대신 본 rule이 담당. 기존
        # reason/passed 문자열은 그대로 유지 (기존 26+ 테스트 호환).
        order_notional = latest_price * order.quantity
        pl_rule = PositionLimitRule(policy_from_risk_policy(self.policy))
        pl_input = PositionLimitInput(
            order=order, balance=balance, positions=positions, latest_price=latest_price,
        )

        def _merge(passed_and_reasons):
            _p, _r = passed_and_reasons
            result.passed.extend(_p)
            result.reasons.extend(_r)

        # max_order_notional + max_position_size_pct (1회 주문 한도)
        _merge(pl_rule.check_order_notional(pl_input))
        _merge(pl_rule.check_equity_relative_order_size(pl_input))

        # daily loss / cash availability — position-limit이 아니므로 RiskManager
        # 본체에 그대로 둔다.
        if self.daily_realized_pnl <= -abs(self.policy.max_daily_loss):
            result.reasons.append("daily loss limit reached")
        else:
            result.passed.append("daily loss limit not reached")

        if order.side == OrderSide.BUY and balance.cash < order_notional:
            result.reasons.append("insufficient cash")
        else:
            result.passed.append("cash/position availability preliminarily ok")

        # #36: Loss Limit Rules — daily / weekly / consecutive. 임계 0이면
        # rule 인스턴스 자체를 None으로 두어 skip. 기존 max_daily_loss hard
        # reject은 위에서 이미 처리됨. 본 단계는 *soft 단계 + weekly +
        # consecutive*를 추가.
        daily_rule = None
        if self.policy.max_daily_loss > 0 and (
            self.policy.daily_loss_warn_pct > 0 or self.policy.daily_loss_reduce_pct > 0
        ):
            daily_rule = DailyLossLimitRule(
                limit=abs(self.policy.max_daily_loss),
                warn_pct=self.policy.daily_loss_warn_pct,
                reduce_pct=self.policy.daily_loss_reduce_pct,
            )
        weekly_rule = None
        if self.policy.weekly_loss_limit > 0:
            weekly_rule = WeeklyLossLimitRule(
                limit=self.policy.weekly_loss_limit,
                warn_pct=self.policy.weekly_loss_warn_pct,
                reduce_pct=self.policy.weekly_loss_reduce_pct,
            )
        consecutive_rule = None
        if self.policy.consecutive_loss_limit > 0:
            consecutive_rule = ConsecutiveLossRule(limit=self.policy.consecutive_loss_limit)
        # 호출자가 주입하지 않은 카운터는 0으로 처리 (검사 대상 미달 → ALLOW).
        loss_merged = evaluate_loss_limits(
            order=order,
            daily_rule=daily_rule,
            weekly_rule=weekly_rule,
            consecutive_rule=consecutive_rule,
            daily_pnl=self.daily_realized_pnl,
            weekly_pnl=weekly_realized_pnl if weekly_realized_pnl is not None else 0,
            consecutive_loss_count=(
                consecutive_loss_count if consecutive_loss_count is not None else 0
            ),
        )
        result.passed.extend(loss_merged.passed)
        result.warnings.extend(loss_merged.warnings)
        result.reasons.extend(loss_merged.reasons)

        # 보유 종목 수 + 종목별 노출 + 종목별 % + 총 노출 + 총 노출 % (모두 rule 위임)
        _merge(pl_rule.check_max_positions(pl_input))
        _merge(pl_rule.check_symbol_exposure(pl_input))
        _merge(pl_rule.check_symbol_exposure_pct(pl_input))
        _merge(pl_rule.check_total_exposure(pl_input))
        _merge(pl_rule.check_total_exposure_pct(pl_input))

        if mode == OperationMode.LIVE_SHADOW:
            result.reasons.append(SHADOW_RECORD_ONLY_REASON)

        if mode in {OperationMode.LIVE_MANUAL_APPROVAL, OperationMode.LIVE_AI_ASSIST}:
            # 061 hardening: the global ENABLE_LIVE_TRADING flag must gate the
            # queue itself, not just downstream execution. Otherwise the queue
            # would fill even with live trading disabled, leaving only the
            # broker-layer guard between operator approval and a real order
            # once LIVE routing wires KIS in. Clean-slate REJECTED keeps the
            # reason list focused on the missing flag.
            if not can_place_live_order(mode, enable_live_trading=self.policy.enable_live_trading):
                return RiskCheckResult(
                    decision=RiskDecision.REJECTED,
                    reasons=["live trading is disabled by global safety flag"],
                )
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

    # ------------------------------------------------------------------
    # #34: 표준 진입점 — check_order(order, context)
    # ------------------------------------------------------------------
    #
    # 모든 신규 호출자(route_order, Strategy/Agent/operator)는 본 메서드를
    # 사용한다. evaluate_order는 backwards compat alias로 유지 — 기존 테스트
    # / 호출자가 깨지지 않는다. check_order는:
    #
    # 1. RiskContext에서 args 추출해 evaluate_order에 위임 (단일 진실)
    # 2. emergency_stop_override / market_regime BLOCK_NEW_BUY / hard 차단
    #    조건들을 추가 검증
    # 3. 결과에 blocked_by / required_action / evaluated_at 등 풍부한 필드를
    #    채워 audit / UI surface 가능하게 한다
    #
    # 직접 주문 우회 방지: 본 메서드는 broker.place_order를 호출하지 않는다.
    # OrderExecutor가 place_order를 호출하기 전 audit.decision == APPROVED를
    # 검증 (executor.py 가드).

    def check_order(
        self,
        order: OrderRequest,
        context: RiskContext,
    ) -> RiskCheckResult:
        """모든 주문성 요청의 표준 진입점.

        호출 흐름: `route_order` → `RiskManager.check_order` → PermissionGate
        → OrderExecutor → BrokerAdapter. 어떤 caller도 BrokerAdapter.place_order
        를 직접 호출해서는 안 된다 (CLAUDE.md 절대 원칙 2).
        """
        # 0. emergency_stop_override가 명시되면 즉시 BLOCKED.
        if context.emergency_stop_override is True:
            r = RiskCheckResult(
                decision=RiskDecision.BLOCKED,
                reasons=["emergency_stop_override is set in context"],
                blocked_by="emergency_stop_override",
                required_action="OPERATOR_RESET",
            )
            return r

        # 1. evaluate_order에 위임 — 기존 26+ 가드 모두 그대로 동작.
        result = self.evaluate_order(
            order=order,
            mode=context.mode,
            balance=context.balance,
            positions=context.positions,
            latest_price=context.latest_price,
            requested_by_ai=context.requested_by_ai,
            latest_price_timestamp=context.latest_price_timestamp,
        )

        # 2. evaluate_order이 REJECTED면 hard 차단의 의미를 강조하기 위해
        #    blocked_by를 추정 (첫 reason 키워드 매핑). 의미 분기:
        #    - emergency stop / disable_ai_orders / stale → BLOCKED (hard)
        #    - 다른 정책 위반 (notional/positions/exposure) → REJECTED (그대로)
        if result.decision == RiskDecision.REJECTED:
            joined = " | ".join(result.reasons).lower()
            if "emergency stop" in joined:
                result.decision  = RiskDecision.BLOCKED
                result.blocked_by = "emergency_stop"
                result.required_action = "OPERATOR_RESET"
            elif "ai orders are disabled" in joined:
                result.decision  = RiskDecision.BLOCKED
                result.blocked_by = "ai_kill_switch"
                result.required_action = "OPERATOR_RESET"
            elif "stale" in joined:
                result.decision  = RiskDecision.BLOCKED
                result.blocked_by = "stale_price"
                result.required_action = "WAIT_FOR_FRESH_DATA"
            elif "live trading is disabled" in joined:
                result.decision  = RiskDecision.BLOCKED
                result.blocked_by = "live_trading_disabled"
                result.required_action = "ENABLE_LIVE_TRADING_FLAG"
            elif "ai execution is not allowed" in joined:
                result.decision  = RiskDecision.BLOCKED
                result.blocked_by = "ai_execution_disabled"
                result.required_action = "ENABLE_AI_EXECUTION_FLAG"
            else:
                result.blocked_by = result.blocked_by or "policy_violation"

        # 3. 32 MarketRegimeFilter 결정 — 신규 BUY는 BLOCK_NEW_BUY에서 차단.
        #    SELL은 리스크 축소 목적이라 그대로 통과시킨다 (CLAUDE.md '손실
        #    방어 우선' 원칙).
        regime_decision = (context.market_regime_decision or "").upper()
        if order.side == OrderSide.BUY and regime_decision == "BLOCK_NEW_BUY":
            if result.decision == RiskDecision.APPROVED:
                result.decision = RiskDecision.BLOCKED
            result.reasons.append(
                f"market regime {context.market_regime or '?'} → "
                f"BLOCK_NEW_BUY (#32 filter)"
            )
            result.blocked_by = result.blocked_by or "market_regime"
            result.required_action = result.required_action or "WAIT_FOR_REGIME_CHANGE"
        elif order.side == OrderSide.BUY and regime_decision == "REDUCE_SIZE":
            # advisory only — 사이즈 축소는 호출자(PositionSizingAgent /
            # Strategy.calculate_size)의 책임. 본 가드는 warning만 추가.
            result.warnings.append(
                f"market regime {context.market_regime or '?'} → "
                f"REDUCE_SIZE 권고 (#32 filter) — size 축소 권장"
            )
        elif order.side == OrderSide.BUY and regime_decision == "WATCH_ONLY":
            if result.decision == RiskDecision.APPROVED:
                result.decision = RiskDecision.BLOCKED
            result.reasons.append(
                f"market regime {context.market_regime or '?'} → "
                f"WATCH_ONLY (#32 filter)"
            )
            result.blocked_by = result.blocked_by or "market_regime"
            result.required_action = result.required_action or "OPERATOR_REVIEW"

        # 4. NEEDS_APPROVAL이면 required_action 명시.
        if result.decision == RiskDecision.NEEDS_APPROVAL and result.required_action is None:
            result.required_action = "MANUAL_APPROVAL"

        # 5. evaluated_at은 dataclass default로 이미 채워졌지만, evaluate_order
        #    경로에서 만들어진 객체 유지를 위해 명시 갱신.
        result.evaluated_at = datetime.now(timezone.utc)
        return result

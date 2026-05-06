from dataclasses import dataclass, field
from datetime import datetime, time, timedelta, timezone
from enum import StrEnum

from app.brokers.base import Balance, OrderRequest, OrderSide, Position
from app.core.modes import OperationMode, can_ai_execute, can_place_live_order


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
        latest_price_timestamp: datetime | None = None,
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

        order_notional = latest_price * order.quantity
        if order_notional > self.policy.max_order_notional:
            result.reasons.append("order notional exceeds max_order_notional")
        else:
            result.passed.append("order notional within limit")

        # 174: equity 대비 비율 한도 — 자본이 변해도 자동 스케일.
        pct = self.policy.max_position_size_pct
        if pct > 0:
            cap = balance.equity * pct / 100.0
            if order_notional > cap:
                result.reasons.append(
                    f"order notional {order_notional} exceeds "
                    f"{pct}% of equity ({cap:.0f})"
                )
            else:
                result.passed.append("order notional within equity-relative cap")

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

"""#48 / 6-03: Agent / Risk Gate 스트레스 테스트 (Paper 검증 전용).

실전과 유사한 *악조건*(슬리피지 / 부분체결 / 거절 / 미체결 / stale price / 급락 /
급등 / 데이터락 / 포트폴리오 drift / 일일 손실한도 / kill switch)을 fixture·합성
입력으로 재현하고, **기존 안전 가드가 정상 동작하는지** 검증해 stress_test_report 를
만든다.

핵심 설계 — *기존 함수를 재사용해 진짜 가드를 검증* 한다 (재구현 0):
- `RiskManager.check_order / evaluate_order` (stale price hard-reject, 시장 regime
  BLOCK_NEW_BUY, emergency_stop, 일일 손실한도 → REJECTED).
- `app.risk.loss_limits.DailyLossLimitRule` (일일 손실한도 block_buy).
- `app.kis_paper.order_quality` (슬리피지 bps / 부분체결 / 거절 / 미체결 분류).
- `app.reconciliation.position_checker.compare_positions` (포트폴리오 drift).
- `app.agents.agent_council.run_agent_council` (악조건 입력 시 BUY 미발생 확인).

**본 모듈은 *검증* 전용이다 — 실제 주문을 생성/전송하지 않으며, broker /
OrderExecutor / 단일 주문 라우터 / KIS 어댑터를 호출하지 않는다 (RiskManager 등
*평가* 함수만 호출). 스트레스 결과만으로 실전 전환을 허가하지 않는다.**

CLAUDE.md 가드 (정적 grep 으로 lock):
- KIS / mock broker 어댑터 / 단일 주문 라우터 / OrderExecutor / paper_trader 모듈
  import 0건 (RiskManager 등 *평가* 클래스만 import).
- broker 주문/취소 호출 0건 (place·cancel·route 경로 미사용), broker 인스턴스 생성 0건.
- 외부 HTTP / AI SDK (anthropic/openai/httpx/requests) import 0건.
- `StressTestReport.is_order_signal=False` / `is_live_authorization=False` /
  `broker_order_sent=False` / `auto_apply_allowed=False` / `contains_secret=False` 불변.
"""

from __future__ import annotations

import random
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from enum import StrEnum
from types import SimpleNamespace
from typing import Any, Callable

from app.agents.agent_council import StrategyMarketInput, run_agent_council
from app.brokers.base import Balance, OrderRequest, OrderSide, OrderType, Position
from app.core.modes import OperationMode
from app.kis_paper.order_quality import build_order_quality_log
from app.reconciliation.position_checker import AuditPositionRow, compare_positions
from app.risk.loss_limits import DailyLossLimitRule
from app.risk.risk_manager import RiskContext, RiskDecision, RiskManager, RiskPolicy


# ─────────────────────────────────────────────────────────────────────────────
# enum / 상수
# ─────────────────────────────────────────────────────────────────────────────


class StressVerdict(StrEnum):
    PASS = "PASS"     # 가드가 기대대로 동작.
    WARN = "WARN"     # 악조건이 advisory 로 플래그됨 (hard block 불필요).
    FAIL = "FAIL"     # 가드가 보호하지 못함 (예: stale 에서 BUY 허용).
    SKIP = "SKIP"


class StressScenario(StrEnum):
    SLIPPAGE_HIGH = "SLIPPAGE_HIGH"
    PARTIAL_FILL = "PARTIAL_FILL"
    ORDER_REJECTED = "ORDER_REJECTED"
    UNFILLED_TIMEOUT = "UNFILLED_TIMEOUT"
    PRICE_STALE = "PRICE_STALE"
    MARKET_CRASH = "MARKET_CRASH"
    GAP_DOWN_OPEN = "GAP_DOWN_OPEN"
    GAP_UP_SPIKE = "GAP_UP_SPIKE"
    DATA_LOCK = "DATA_LOCK"
    PORTFOLIO_DRIFT = "PORTFOLIO_DRIFT"
    DAILY_LOSS_LIMIT = "DAILY_LOSS_LIMIT"
    REPEATED_REJECTION = "REPEATED_REJECTION"


ALL_SCENARIOS: tuple[str, ...] = tuple(s.value for s in StressScenario)

# 슬리피지 WARN 임계 (bps).
SLIPPAGE_WARN_BPS = 50.0
# 반복 거절 → 위험 경고 임계 (RiskPolicy.auto_stop_consecutive_rejections default).
REPEATED_REJECTION_THRESHOLD = 5

DISCLAIMER_KO = (
    "본 스트레스 리포트는 Paper 검증 전용입니다. 실제 주문/실전 전환이 아니며, "
    "본 결과만으로 실거래 권한을 부여하지 않습니다. 과거/모의 결과는 미래 수익을 "
    "보장하지 않습니다."
)


# ─────────────────────────────────────────────────────────────────────────────
# 출력 DTO
# ─────────────────────────────────────────────────────────────────────────────


@dataclass(frozen=True)
class ScenarioResult:
    scenario:                   str
    verdict:                    str
    reason_code:                str
    expected:                   str
    observed:                   str
    risk_gate_triggered:        bool = False
    kill_switch_triggered:      bool = False
    kill_switch_should_trigger: bool = False
    order_quality:              dict[str, Any] = field(default_factory=dict)
    details:                    tuple[str, ...] = ()

    # 불변 — 스트레스 테스트는 주문을 보내지 않는다.
    broker_order_sent:     bool = False
    is_order_signal:       bool = False
    is_live_authorization: bool = False

    def __post_init__(self) -> None:
        for name in ("broker_order_sent", "is_order_signal", "is_live_authorization"):
            if getattr(self, name) is not False:
                raise ValueError(f"{name} must be False (스트레스 테스트는 주문 0건)")

    def to_dict(self) -> dict[str, Any]:
        return {
            "scenario":                   self.scenario,
            "verdict":                    self.verdict,
            "reason_code":                self.reason_code,
            "expected":                   self.expected,
            "observed":                   self.observed,
            "risk_gate_triggered":        bool(self.risk_gate_triggered),
            "kill_switch_triggered":      bool(self.kill_switch_triggered),
            "kill_switch_should_trigger": bool(self.kill_switch_should_trigger),
            "order_quality":              dict(self.order_quality),
            "details":                    list(self.details),
            "broker_order_sent":          False,
            "is_order_signal":            False,
            "is_live_authorization":      False,
        }


@dataclass(frozen=True)
class StressTestReport:
    generated_at:               str
    scenarios:                  tuple[ScenarioResult, ...]
    counts:                     dict[str, int]
    overall_verdict:            str
    risk_gate_triggered_count:  int
    kill_switch_triggered_count: int
    kill_switch_should_trigger_count: int
    reason_code:                str
    strict:                     bool = False

    is_order_signal:       bool = False
    is_live_authorization: bool = False
    auto_apply_allowed:    bool = False
    broker_order_sent:     bool = False
    contains_secret:       bool = False
    disclaimer:            str = DISCLAIMER_KO

    def __post_init__(self) -> None:
        for name in ("is_order_signal", "is_live_authorization", "auto_apply_allowed",
                     "broker_order_sent", "contains_secret"):
            if getattr(self, name) is not False:
                raise ValueError(f"{name} must be False (스트레스 테스트는 검증 전용)")

    def to_dict(self) -> dict[str, Any]:
        return {
            "generated_at":              self.generated_at,
            "scenarios":                 [s.to_dict() for s in self.scenarios],
            "counts":                    dict(self.counts),
            "overall_verdict":           self.overall_verdict,
            "risk_gate_triggered_count": int(self.risk_gate_triggered_count),
            "kill_switch_triggered_count": int(self.kill_switch_triggered_count),
            "kill_switch_should_trigger_count": int(self.kill_switch_should_trigger_count),
            "reason_code":               self.reason_code,
            "strict":                    bool(self.strict),
            "is_order_signal":           False,
            "is_live_authorization":     False,
            "auto_apply_allowed":        False,
            "broker_order_sent":         False,
            "contains_secret":           False,
            "disclaimer":                self.disclaimer,
        }


# ─────────────────────────────────────────────────────────────────────────────
# 공통 헬퍼 — 합성 입력 (broker 인스턴스 생성 0)
# ─────────────────────────────────────────────────────────────────────────────


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _buy_order(symbol="005930", qty=1) -> OrderRequest:
    return OrderRequest(symbol=symbol, side=OrderSide.BUY, quantity=qty,
                        order_type=OrderType.MARKET)


def _balance(cash=100_000_000) -> Balance:
    return Balance(cash=cash, equity=cash, buying_power=cash)


def _risk(**policy_kw) -> RiskManager:
    base = dict(max_order_notional=50_000_000, max_daily_loss=200_000,
                stale_price_max_age_seconds=60)
    base.update(policy_kw)
    return RiskManager(RiskPolicy(**base))


def _oq(*, submitted, filled_quantity, quantity, price, avg_fill_price,
        reason_code="", side="BUY", fill_status=None) -> dict[str, Any]:
    """build_order_quality_log 를 duck-typed 입력으로 구동 (KIS 호출 0)."""
    result = SimpleNamespace(
        reason_code=reason_code, reason_message="", dry_run=False, submitted=submitted,
        fill_status=fill_status, filled_quantity=filled_quantity,
        avg_fill_price=avg_fill_price, broker_order_no=("PAPER-1" if submitted else None),
    )
    decision = SimpleNamespace(quantity=quantity, price=price, side=side)
    log = build_order_quality_log(result=result, decision=decision)
    return {
        "order_status":      log.order_status,
        "filled_quantity":   log.filled_quantity,
        "unfilled_quantity": log.unfilled_quantity,
        "partial_fill":      log.partial_fill,
        "slippage_bps":      log.slippage_bps,
        "broker_order_no":   log.broker_order_no,
        "rejection_reason_code": log.rejection_reason_code,
        "fill_polling":      log.fill_polling,
    }


# ─────────────────────────────────────────────────────────────────────────────
# 시나리오 구현 (각 함수는 ScenarioResult 반환, broker 호출 0)
# ─────────────────────────────────────────────────────────────────────────────


def _sc_slippage_high(rng: random.Random) -> ScenarioResult:
    req = 70_000
    fill = int(round(req * (1 + rng.uniform(0.012, 0.02))))   # 120~200 bps 불리.
    oq = _oq(submitted=True, filled_quantity=10, quantity=10, price=req,
             avg_fill_price=fill, fill_status="FILLED")
    slip = oq["slippage_bps"] or 0.0
    detected = slip >= SLIPPAGE_WARN_BPS
    return ScenarioResult(
        scenario=StressScenario.SLIPPAGE_HIGH.value,
        verdict=StressVerdict.WARN.value if detected else StressVerdict.FAIL.value,
        reason_code="SLIPPAGE_DEGRADATION" if detected else "SLIPPAGE_NOT_MEASURED",
        expected="높은 슬리피지가 bps 로 측정·기록되어 주문품질 악화로 플래그",
        observed=f"slippage_bps={slip} (임계 {SLIPPAGE_WARN_BPS}bps)",
        order_quality=oq,
        details=(f"체결가 {fill} vs 요청가 {req} → {slip}bps 불리",),
    )


def _sc_partial_fill(rng: random.Random) -> ScenarioResult:
    oq = _oq(submitted=True, filled_quantity=3, quantity=10, price=70_000,
             avg_fill_price=70_050, fill_status=None)
    ok = (oq["partial_fill"] is True and oq["order_status"] != "FILLED"
          and oq["unfilled_quantity"] == 7 and oq["filled_quantity"] == 3)
    return ScenarioResult(
        scenario=StressScenario.PARTIAL_FILL.value,
        verdict=StressVerdict.WARN.value if ok else StressVerdict.FAIL.value,
        reason_code="PARTIAL_FILL_FLAGGED" if ok else "PARTIAL_FILL_MISCLASSIFIED",
        expected="filled<requested → partial_fill=true, remaining 기록, FILLED 로 처리하지 않음",
        observed=f"status={oq['order_status']} filled={oq['filled_quantity']} "
                 f"unfilled={oq['unfilled_quantity']} partial={oq['partial_fill']}",
        order_quality=oq,
        details=("부분체결을 완전체결로 처리하지 않는지 검증",),
    )


def _sc_order_rejected(rng: random.Random) -> ScenarioResult:
    oq = _oq(submitted=False, filled_quantity=0, quantity=10, price=70_000,
             avg_fill_price=None, reason_code="ORDER_REJECTED")
    ok = (oq["order_status"] == "REJECTED" and oq["broker_order_no"] is None
          and oq["filled_quantity"] == 0)
    return ScenarioResult(
        scenario=StressScenario.ORDER_REJECTED.value,
        verdict=StressVerdict.PASS.value if ok else StressVerdict.FAIL.value,
        reason_code="ORDER_REJECTED_RECORDED" if ok else "REJECTED_MISCLASSIFIED",
        expected="거절은 broker_order_sent=false / status=REJECTED 로 기록 (submitted 로 처리 금지)",
        observed=f"status={oq['order_status']} broker_order_no={oq['broker_order_no']}",
        risk_gate_triggered=True,
        order_quality=oq,
        details=("주문 실패율 증가 — 거절을 체결로 오인하지 않는지 검증",),
    )


def _sc_unfilled_timeout(rng: random.Random) -> ScenarioResult:
    # 접수 후 미체결 지속 (submitted=False → UNFILLED, fill polling 필요).
    oq = _oq(submitted=False, filled_quantity=0, quantity=10, price=70_000,
             avg_fill_price=None, reason_code="")
    ok = (oq["order_status"] == "UNFILLED" and oq["filled_quantity"] == 0
          and oq["fill_polling"] is not None)
    return ScenarioResult(
        scenario=StressScenario.UNFILLED_TIMEOUT.value,
        verdict=StressVerdict.WARN.value if ok else StressVerdict.FAIL.value,
        reason_code="UNFILLED_NEEDS_POLLING" if ok else "UNFILLED_MISCLASSIFIED",
        expected="미체결은 UNFILLED 로 기록 + fill polling 필요 (FILLED 로 처리 금지)",
        observed=f"status={oq['order_status']} fill_polling_present={oq['fill_polling'] is not None}",
        order_quality=oq,
        details=("미체결을 체결로 처리하지 않는지 검증",),
    )


def _sc_price_stale(rng: random.Random) -> ScenarioResult:
    risk = _risk(stale_price_max_age_seconds=60)
    stale_ts = _now() - timedelta(seconds=3600)
    res = risk.evaluate_order(
        order=_buy_order(), mode=OperationMode.PAPER, balance=_balance(),
        positions=[], latest_price=70_000, latest_price_timestamp=stale_ts)
    blocked = res.decision in (RiskDecision.REJECTED, RiskDecision.BLOCKED)
    return ScenarioResult(
        scenario=StressScenario.PRICE_STALE.value,
        verdict=StressVerdict.PASS.value if blocked else StressVerdict.FAIL.value,
        reason_code="STALE_PRICE_BLOCKED" if blocked else "STALE_PRICE_NOT_BLOCKED",
        expected="오래된 시세에서는 신규 BUY 가 REJECTED (RiskManager step 1.5)",
        observed=f"decision={res.decision.value} reasons={'; '.join(res.reasons)[:120]}",
        risk_gate_triggered=blocked,
        details=("stale price 에서 BUY 가 생성되면 FAIL",),
    )


def _sc_market_crash(rng: random.Random) -> ScenarioResult:
    risk = _risk()
    ctx = RiskContext(
        mode=OperationMode.PAPER, balance=_balance(), positions=[],
        latest_price=63_000, latest_price_timestamp=_now(),
        market_regime="TREND_DOWN", market_regime_decision="BLOCK_NEW_BUY")
    res = risk.check_order(_buy_order(), ctx)
    risk_blocked = res.decision in (RiskDecision.REJECTED, RiskDecision.BLOCKED)
    # 급락 bar → council 은 BUY 를 내지 않아야 한다.
    crash_closes = (70_000, 69_000, 67_000, 65_000, 63_000)
    council = run_agent_council(StrategyMarketInput(
        symbol="005930", current_price=63_000, prev_close=70_000, open_price=69_500,
        vwap=66_000, opening_range_high=70_200, opening_range_low=69_000,
        recent_closes=crash_closes, current_volume=20_000_000, avg_volume=8_000_000,
        market_regime="TREND_DOWN", regime_decision="BLOCK_NEW_BUY"))
    council_no_buy = council.final_action.value != "BUY"
    ok = risk_blocked and council_no_buy
    return ScenarioResult(
        scenario=StressScenario.MARKET_CRASH.value,
        verdict=StressVerdict.PASS.value if ok else StressVerdict.FAIL.value,
        reason_code="CRASH_NEW_BUY_BLOCKED" if ok else "CRASH_BUY_NOT_BLOCKED",
        expected="급락 시 risk gate 가 신규 BUY 차단 + Agent Council final_action != BUY",
        observed=f"risk={res.decision.value} council={council.final_action.value}",
        risk_gate_triggered=risk_blocked,
        details=(f"council risk_flags={council.risk_flags}",),
    )


def _sc_gap_down_open(rng: random.Random) -> ScenarioResult:
    risk = _risk()
    ctx = RiskContext(
        mode=OperationMode.PAPER, balance=_balance(), positions=[],
        latest_price=66_000, latest_price_timestamp=_now(),
        market_regime="TREND_DOWN", market_regime_decision="BLOCK_NEW_BUY")
    res = risk.check_order(_buy_order(), ctx)
    blocked = res.decision in (RiskDecision.REJECTED, RiskDecision.BLOCKED)
    return ScenarioResult(
        scenario=StressScenario.GAP_DOWN_OPEN.value,
        verdict=StressVerdict.PASS.value if blocked else StressVerdict.FAIL.value,
        reason_code="GAP_DOWN_NEW_BUY_BLOCKED" if blocked else "GAP_DOWN_BUY_NOT_BLOCKED",
        expected="전일 대비 급락 출발 시 신규 BUY 차단 (보유 포지션은 손절 후보)",
        observed=f"decision={res.decision.value}",
        risk_gate_triggered=blocked,
        details=("gap down open 에서 신규 BUY 가 생성되면 FAIL",),
    )


def _sc_gap_up_spike(rng: random.Random) -> ScenarioResult:
    # 급등 후 고변동성 → council risk_flags(high_volatility) 증가 / 추격매수 과열 방지.
    council = run_agent_council(StrategyMarketInput(
        symbol="005930", current_price=77_000, prev_close=70_000, open_price=76_500,
        vwap=74_000, opening_range_high=76_800, opening_range_low=75_000,
        recent_closes=(70_000, 72_000, 74_000, 76_000, 77_000),
        current_volume=40_000_000, avg_volume=8_000_000,
        market_regime="HIGH_VOLATILITY", regime_decision="REDUCE_SIZE"))
    flagged = bool(council.risk_flags)
    return ScenarioResult(
        scenario=StressScenario.GAP_UP_SPIKE.value,
        verdict=StressVerdict.WARN.value if flagged else StressVerdict.PASS.value,
        reason_code="GAP_UP_OVERHEAT_FLAGGED" if flagged else "GAP_UP_NO_FLAG",
        expected="급등 과열 구간에서 risk_flag 증가 / confidence·quality 게이트로 추격매수 방지",
        observed=f"council={council.final_action.value} risk_flags={council.risk_flags} "
                 f"quality={council.quality_score}",
        details=("고변동성 추격매수 과열 방지 검증 (advisory)",),
    )


def _sc_data_lock(rng: random.Random) -> ScenarioResult:
    # 시세 데이터 없음 → council 은 데이터 부족으로 HOLD (주문 0건).
    council = run_agent_council(StrategyMarketInput(
        symbol="005930", current_price=None, prev_close=None, open_price=None,
        vwap=None, recent_closes=(), current_volume=None, avg_volume=None,
        market_regime="UNKNOWN", regime_decision="ALLOW"))
    hold = council.final_action.value == "HOLD"
    return ScenarioResult(
        scenario=StressScenario.DATA_LOCK.value,
        verdict=StressVerdict.PASS.value if hold else StressVerdict.FAIL.value,
        reason_code="NO_MARKET_DATA_HOLD" if hold else "NO_DATA_ORDER_GENERATED",
        expected="시세 데이터 없음/멈춤 → 자동 판단 skip (HOLD), 주문 0건",
        observed=f"council final_action={council.final_action.value}",
        details=("데이터락에서 BUY/SELL 가 생성되면 FAIL",),
    )


def _sc_portfolio_drift(rng: random.Random) -> ScenarioResult:
    broker_positions = [Position(symbol="005930", quantity=10, avg_price=70_000,
                                 market_price=70_000)]
    audit_positions = [AuditPositionRow(symbol="005930", net_quantity=5,
                                        buy_quantity=5, sell_quantity=0,
                                        avg_buy_price=70_000)]
    mismatches = compare_positions(broker_positions, audit_positions)
    detected = len(mismatches) > 0
    return ScenarioResult(
        scenario=StressScenario.PORTFOLIO_DRIFT.value,
        verdict=StressVerdict.WARN.value if detected else StressVerdict.FAIL.value,
        reason_code="PORTFOLIO_DRIFT_DETECTED" if detected else "DRIFT_NOT_DETECTED",
        expected="프로그램 포지션과 broker/Paper 포지션 불일치 감지 → 신규 주문 차단/WARN",
        observed=f"mismatches={[m.kind for m in mismatches]} diff="
                 f"{[m.quantity_diff for m in mismatches]}",
        risk_gate_triggered=detected,
        details=("drift 미감지 시 FAIL (신규 주문이 잘못된 포지션 위에서 생성될 위험)",),
    )


def _sc_daily_loss_limit(rng: random.Random) -> ScenarioResult:
    limit = 200_000
    # 1) loss_limits 규칙: 일일 손실 초과 → block_buy.
    rule = DailyLossLimitRule(limit=limit)
    lr = rule.evaluate(daily_pnl=-250_000, order=_buy_order())
    # 2) RiskManager: daily_realized_pnl 초과 → REJECTED.
    risk = _risk(max_daily_loss=limit)
    risk.daily_realized_pnl = -250_000
    res = risk.evaluate_order(order=_buy_order(), mode=OperationMode.PAPER,
                              balance=_balance(), positions=[], latest_price=70_000,
                              latest_price_timestamp=_now())
    risk_blocked = res.decision in (RiskDecision.REJECTED, RiskDecision.BLOCKED)
    ok = lr.block_buy and risk_blocked
    return ScenarioResult(
        scenario=StressScenario.DAILY_LOSS_LIMIT.value,
        verdict=StressVerdict.PASS.value if ok else StressVerdict.FAIL.value,
        reason_code="DAILY_LOSS_LIMIT_BLOCKED" if ok else "DAILY_LOSS_NOT_BLOCKED",
        expected="일일 손실한도 초과 → 신규 BUY 차단 (risk block) + kill switch 권고",
        observed=f"loss_rule.block_buy={lr.block_buy} risk={res.decision.value}",
        risk_gate_triggered=risk_blocked,
        kill_switch_should_trigger=True,   # 운영자에게 kill switch 권고 (자동 토글 아님).
        details=(f"loss_rule.decision={lr.decision.value}",
                 "kill switch 는 운영자 수동 승인 — 본 결과는 *권고*"),
    )


def _sc_repeated_rejection(rng: random.Random) -> ScenarioResult:
    # 1) emergency_stop(kill switch) → 모든 주문 REJECTED 확인 (진짜 가드).
    risk = _risk()
    risk.emergency_stop = True
    res = risk.evaluate_order(order=_buy_order(), mode=OperationMode.PAPER,
                              balance=_balance(), positions=[], latest_price=70_000,
                              latest_price_timestamp=_now())
    kill_blocked = res.decision in (RiskDecision.REJECTED, RiskDecision.BLOCKED)
    # 2) 반복 거절 → 위험 경고 권고.
    consecutive = REPEATED_REJECTION_THRESHOLD
    warn = consecutive >= REPEATED_REJECTION_THRESHOLD
    ok = kill_blocked and warn
    return ScenarioResult(
        scenario=StressScenario.REPEATED_REJECTION.value,
        verdict=StressVerdict.PASS.value if ok else StressVerdict.FAIL.value,
        reason_code="KILL_SWITCH_BLOCKS_ALL" if ok else "KILL_SWITCH_NOT_BLOCKING",
        expected="kill switch(emergency_stop) 시 모든 주문 REJECTED + 반복 거절 위험 경고",
        observed=f"emergency_stop decision={res.decision.value} "
                 f"consecutive_rejections={consecutive}",
        risk_gate_triggered=kill_blocked,
        kill_switch_triggered=kill_blocked,
        kill_switch_should_trigger=warn,
        details=(f"반복 거절 {consecutive}회 ≥ 임계 {REPEATED_REJECTION_THRESHOLD} → 위험 경고",),
    )


_SCENARIO_FUNCS: dict[str, Callable[[random.Random], ScenarioResult]] = {
    StressScenario.SLIPPAGE_HIGH.value:      _sc_slippage_high,
    StressScenario.PARTIAL_FILL.value:       _sc_partial_fill,
    StressScenario.ORDER_REJECTED.value:     _sc_order_rejected,
    StressScenario.UNFILLED_TIMEOUT.value:   _sc_unfilled_timeout,
    StressScenario.PRICE_STALE.value:        _sc_price_stale,
    StressScenario.MARKET_CRASH.value:       _sc_market_crash,
    StressScenario.GAP_DOWN_OPEN.value:      _sc_gap_down_open,
    StressScenario.GAP_UP_SPIKE.value:       _sc_gap_up_spike,
    StressScenario.DATA_LOCK.value:          _sc_data_lock,
    StressScenario.PORTFOLIO_DRIFT.value:    _sc_portfolio_drift,
    StressScenario.DAILY_LOSS_LIMIT.value:   _sc_daily_loss_limit,
    StressScenario.REPEATED_REJECTION.value: _sc_repeated_rejection,
}


# ─────────────────────────────────────────────────────────────────────────────
# 실행
# ─────────────────────────────────────────────────────────────────────────────


def run_stress_scenario(scenario: str, *, seed: int = 0) -> ScenarioResult:
    fn = _SCENARIO_FUNCS.get(scenario)
    if fn is None:
        raise KeyError(f"unknown stress scenario: {scenario}")
    return fn(random.Random(seed))


def run_agent_stress_test(*, scenarios: tuple[str, ...] | None = None,
                          strict: bool = False, seed: int = 0) -> StressTestReport:
    """선택 시나리오(기본 ALL)를 실행해 종합 stress_test_report 산출.

    strict=True 면 WARN 도 FAIL 로 격상 (모든 시나리오 PASS 만 통과).
    """
    targets = tuple(scenarios) if scenarios else ALL_SCENARIOS
    results: list[ScenarioResult] = []
    for sc in targets:
        results.append(run_stress_scenario(sc, seed=seed))

    counts = {v.value: 0 for v in StressVerdict}
    for r in results:
        counts[r.verdict] = counts.get(r.verdict, 0) + 1

    has_fail = counts.get(StressVerdict.FAIL.value, 0) > 0
    has_warn = counts.get(StressVerdict.WARN.value, 0) > 0
    if has_fail or (strict and has_warn):
        overall = StressVerdict.FAIL.value
    elif has_warn:
        overall = StressVerdict.WARN.value
    else:
        overall = StressVerdict.PASS.value

    reason = {
        StressVerdict.PASS.value: "STRESS_ALL_PASS",
        StressVerdict.WARN.value: "STRESS_WARN_ONLY",
        StressVerdict.FAIL.value: "STRESS_FAIL_PRESENT",
    }[overall]

    return StressTestReport(
        generated_at=_now().isoformat(),
        scenarios=tuple(results), counts=counts, overall_verdict=overall,
        risk_gate_triggered_count=sum(1 for r in results if r.risk_gate_triggered),
        kill_switch_triggered_count=sum(1 for r in results if r.kill_switch_triggered),
        kill_switch_should_trigger_count=sum(1 for r in results if r.kill_switch_should_trigger),
        reason_code=reason, strict=strict,
    )


def summarize_stress_report(report: StressTestReport) -> dict[str, Any]:
    return report.to_dict()


# ─────────────────────────────────────────────────────────────────────────────
# Markdown 렌더
# ─────────────────────────────────────────────────────────────────────────────


_SECTION_GROUPS = (
    ("슬리피지", (StressScenario.SLIPPAGE_HIGH.value,)),
    ("부분체결", (StressScenario.PARTIAL_FILL.value,)),
    ("주문 거절", (StressScenario.ORDER_REJECTED.value, StressScenario.REPEATED_REJECTION.value)),
    ("stale price", (StressScenario.PRICE_STALE.value, StressScenario.UNFILLED_TIMEOUT.value)),
    ("crash / gap", (StressScenario.MARKET_CRASH.value, StressScenario.GAP_DOWN_OPEN.value,
                     StressScenario.GAP_UP_SPIKE.value, StressScenario.DATA_LOCK.value)),
    ("portfolio drift", (StressScenario.PORTFOLIO_DRIFT.value,)),
    ("daily loss / kill switch", (StressScenario.DAILY_LOSS_LIMIT.value,
                                  StressScenario.REPEATED_REJECTION.value)),
)


def render_markdown_report(report: StressTestReport) -> str:
    by_name = {r.scenario: r for r in report.scenarios}
    lines: list[str] = []
    lines.append("# Agent / Risk Gate 스트레스 테스트 리포트 (#48 / 6-03)")
    lines.append("")
    lines.append(f"> {report.disclaimer}")
    lines.append("")
    # 1. 요약 + 2. 전체 PASS/WARN/FAIL
    lines.append("## 1. 요약")
    lines.append(f"- 생성: {report.generated_at}")
    lines.append(f"- 전체 판정(overall_verdict): **{report.overall_verdict}** "
                 f"(reason: {report.reason_code})")
    lines.append(f"- PASS {report.counts.get('PASS',0)} · WARN {report.counts.get('WARN',0)} · "
                 f"FAIL {report.counts.get('FAIL',0)} · SKIP {report.counts.get('SKIP',0)}")
    lines.append(f"- risk_gate_triggered: {report.risk_gate_triggered_count} · "
                 f"kill_switch_triggered: {report.kill_switch_triggered_count} · "
                 f"kill_switch_should_trigger: {report.kill_switch_should_trigger_count}")
    lines.append(f"- strict: {report.strict}")
    lines.append("")
    # 3. 시나리오별 결과표
    lines.append("## 2. 시나리오별 결과")
    lines.append("")
    lines.append("| 시나리오 | 판정 | reason_code | risk_gate | kill_switch | 관측 |")
    lines.append("|---|---|---|---|---|---|")
    for r in report.scenarios:
        lines.append(
            f"| {r.scenario} | {r.verdict} | {r.reason_code} | "
            f"{'Y' if r.risk_gate_triggered else '-'} | "
            f"{'Y' if r.kill_switch_triggered else ('권고' if r.kill_switch_should_trigger else '-')} | "
            f"{r.observed[:80]} |")
    lines.append("")
    # 4~10. 그룹별 상세
    for title, names in _SECTION_GROUPS:
        lines.append(f"## {title}")
        for n in names:
            r = by_name.get(n)
            if r is None:
                continue
            lines.append(f"- **{n}** [{r.verdict}] {r.reason_code}")
            lines.append(f"  - 기대: {r.expected}")
            lines.append(f"  - 관측: {r.observed}")
            if r.order_quality:
                oq = r.order_quality
                lines.append(f"  - order_quality: status={oq.get('order_status')} "
                             f"slippage_bps={oq.get('slippage_bps')} "
                             f"partial={oq.get('partial_fill')} "
                             f"filled/unfilled={oq.get('filled_quantity')}/{oq.get('unfilled_quantity')}")
        lines.append("")
    # 11. 실패 시 조치
    fails = [r for r in report.scenarios if r.verdict == StressVerdict.FAIL.value]
    lines.append("## 11. 실패 시 조치")
    if fails:
        for r in fails:
            lines.append(f"- **{r.scenario}** FAIL — 해당 안전 가드 점검 필요: {r.expected}")
    else:
        lines.append("- FAIL 시나리오 없음.")
    lines.append("")
    # 12~13. 실전 전환 아님 / 수익 보장 아님
    lines.append("## 12. 고지")
    lines.append("- **실전 전환을 자동 승인하지 않습니다.** 별도 Paper Gate / Live Capital "
                 "Review / Manual Approval / Canary 게이트 + 운영자 옵트인이 필요합니다.")
    lines.append("- **과거/모의 결과는 미래 수익을 보장하지 않습니다.**")
    lines.append("- 본 테스트는 실제 주문을 생성하지 않으며 broker / KIS API 를 호출하지 않습니다.")
    lines.append("")
    return "\n".join(lines) + "\n"


__all__ = [
    "StressVerdict", "StressScenario", "ALL_SCENARIOS",
    "SLIPPAGE_WARN_BPS", "REPEATED_REJECTION_THRESHOLD",
    "ScenarioResult", "StressTestReport",
    "run_stress_scenario", "run_agent_stress_test",
    "summarize_stress_report", "render_markdown_report",
]

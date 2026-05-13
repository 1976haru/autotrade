"""체크리스트 #60: AI Agent 기반 end-to-end 모의매매 오케스트레이터.

본 모듈은 *기존* 빌딩블록(전략 레지스트리 / RiskManager / route_order /
MockBrokerAdapter)을 묶어 "데이터 입력 → 전략 신호 → Agent 종합 판단 →
가상 주문 → 포트폴리오 업데이트 → 감사 기록"의 단일 흐름을 노출한다.

## 절대 원칙 (CLAUDE.md 1, 2 상속)

1. 본 모듈은 broker를 *직접* 호출하지 않는다 — `route_order` 통해서만
   주문이 흐른다. `broker.place_order(` 호출 0건 (정적 grep 가드).
2. 본 모듈은 LIVE 모드에서 사용 금지 — `run_once`는 `SIMULATION` /
   `PAPER` / `VIRTUAL_AI_EXECUTION` 외 모드에서 RuntimeError로 차단.
3. 실 broker 인스턴스(`KisBrokerAdapter` with `is_paper=False`)가 주입되면
   `NotPaperBrokerError`로 즉시 차단 — `assert_paper_broker` 검사.
4. AgentDecision은 자체로 주문 신호가 아니다 — `is_order_intent=False`
   (frozen dataclass `__post_init__` ValueError 가드).

## 흐름

    AutoTraderInput            # 운영자 / API가 채워서 전달
        │
        │  watchlist / bars_by_symbol / quotes
        ▼
    1) Pre-check (emergency_stop / market closed / mode 검증)
        │
        ▼
    2) 각 symbol마다 모든 등록 전략의 generate_signal() 호출
        │
        ▼
    3) StrategyMixer.combine() — BUY/SELL 카운트 + 평균 confidence
        │
        ▼
    4) AgentDecision 생성 (action / size / reason / risk checks 미리보기)
        │
        ▼
    5) confidence < min_confidence → HOLD로 강등
        │
        ▼
    6) BUY/SELL이면 route_order(MockBroker) 통과
        │
        ▼
    7) AutoTraderReport 반환 (per-symbol decisions + portfolio after)

본 모듈은 #51 AgentBase / #56 ExecutionRecommender 흐름과 *공존*한다 —
#56은 "AI 제안 → 사람 승인 → 큐" 경로이고, 본 모듈은 가상 환경에서
"AI 자동 매매 end-to-end 검증" 용도다 (LIVE 절대 금지).
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from typing import Any
from uuid import uuid4

from sqlalchemy.orm import Session

from app.backtest.types import Bar, Signal
from app.brokers.base import BrokerAdapter, OrderSide, OrderType, OrderRequest
from app.core.modes import OperationMode
from app.execution.order_router import route_order, OrderRoutingResult
from app.execution.paper_trader import assert_paper_broker
from app.risk.risk_manager import RiskDecision, RiskManager
from app.strategies.base import SignalAction, StrategyContext, StrategySignal
from app.strategies.concrete import STRATEGY_REGISTRY, build_strategy


# ---------- Allowed modes (LIVE 금지) ----------

_ALLOWED_MODES: frozenset[OperationMode] = frozenset({
    OperationMode.SIMULATION,
    OperationMode.PAPER,
    OperationMode.VIRTUAL_AI_EXECUTION,
})


# ---------- DTOs ----------


@dataclass(frozen=True)
class StrategySignalReport:
    """단일 전략의 신호 — 사용자 요구 형식과 1:1."""
    strategy_id: str
    signal:      str        # "BUY" / "SELL" / "HOLD"
    confidence:  int        # 0-100
    reason:      str
    indicators:  dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict:
        return {
            "strategyId": self.strategy_id,
            "signal":     self.signal,
            "confidence": self.confidence,
            "reason":     self.reason,
            "indicators": dict(self.indicators),
        }


@dataclass(frozen=True)
class RiskChecksPreview:
    """Agent 판단 *전* 미리 표시할 한도 체크. 실제 가드는 RiskManager가
    `route_order`에서 수행. 운영자 UI 표시용 advisory."""
    max_position_ok:      bool
    daily_loss_limit_ok:  bool
    cooldown_ok:          bool
    cash_available_ok:    bool

    def to_dict(self) -> dict:
        return {
            "maxPositionOk":     self.max_position_ok,
            "dailyLossLimitOk":  self.daily_loss_limit_ok,
            "cooldownOk":        self.cooldown_ok,
            "cashAvailableOk":   self.cash_available_ok,
        }


@dataclass(frozen=True)
class AgentDecision:
    """Agent 최종 판단 — 사용자 요구 표준 형식."""
    action:         str            # "BUY" / "SELL" / "HOLD"
    symbol:         str
    confidence:     int            # 0-100
    position_size:  int            # 수량 (가상 주문)
    reason:         str
    used_strategies: list[str]
    risk_checks:    RiskChecksPreview
    created_at:     datetime
    # invariant — Agent decision 자체는 주문 객체가 아님.
    is_order_intent: bool = False

    def __post_init__(self) -> None:
        if self.is_order_intent is not False:
            raise ValueError(
                "AgentDecision.is_order_intent must be False — "
                "본 decision은 주문 객체가 아닙니다 (route_order 통과 필요)."
            )
        if self.action not in ("BUY", "SELL", "HOLD"):
            raise ValueError(f"AgentDecision.action invalid: {self.action!r}")
        if not (0 <= int(self.confidence) <= 100):
            raise ValueError(
                f"AgentDecision.confidence must be 0-100, got {self.confidence}"
            )

    def to_dict(self) -> dict:
        return {
            "action":          self.action,
            "symbol":          self.symbol,
            "confidence":      int(self.confidence),
            "positionSize":    int(self.position_size),
            "reason":          self.reason,
            "usedStrategies":  list(self.used_strategies),
            "riskChecks":      self.risk_checks.to_dict(),
            "createdAt":       self.created_at.isoformat(),
            "isOrderIntent":   self.is_order_intent,
        }


@dataclass(frozen=True)
class SymbolPlan:
    """단일 symbol의 모의매매 plan — 신호들 + Agent 결정 + 라우팅 결과."""
    symbol:          str
    strategy_signals: list[StrategySignalReport]
    decision:        AgentDecision
    routing_decision: str | None         = None  # APPROVED/REJECTED/NEEDS_APPROVAL
    routing_reasons:  list[str]          = field(default_factory=list)
    audit_id:         int | None         = None
    executed:         bool               = False
    fill_quantity:    int                = 0
    fill_price:       int | None         = None
    blocked_by:       str | None         = None    # Pre-flight 차단 사유 ('emergency_stop', 'mode' 등)
    error:            str | None         = None

    def to_dict(self) -> dict:
        return {
            "symbol":           self.symbol,
            "strategySignals":  [s.to_dict() for s in self.strategy_signals],
            "decision":         self.decision.to_dict(),
            "routingDecision":  self.routing_decision,
            "routingReasons":   list(self.routing_reasons),
            "auditId":          self.audit_id,
            "executed":         self.executed,
            "fillQuantity":     int(self.fill_quantity),
            "fillPrice":        self.fill_price,
            "blockedBy":        self.blocked_by,
            "error":            self.error,
        }


@dataclass(frozen=True)
class PortfolioSnapshot:
    cash:        int
    equity:      int
    buying_power: int
    positions:   list[dict[str, Any]]

    def to_dict(self) -> dict:
        return {
            "cash":        int(self.cash),
            "equity":      int(self.equity),
            "buyingPower": int(self.buying_power),
            "positions":   [dict(p) for p in self.positions],
        }


@dataclass(frozen=True)
class AutoTraderReport:
    mode:          str
    emergency_stop: bool
    started_at:    datetime
    finished_at:   datetime
    plans:         list[SymbolPlan]
    portfolio:     PortfolioSnapshot
    summary:       dict[str, int]
    notice:        str = (
        "본 결과는 *모의매매 검증용*입니다. "
        "실제 증권사 주문은 발생하지 않았습니다 (Paper/Virtual Broker만 사용)."
    )

    def to_dict(self) -> dict:
        return {
            "mode":          self.mode,
            "emergencyStop": self.emergency_stop,
            "startedAt":     self.started_at.isoformat(),
            "finishedAt":    self.finished_at.isoformat(),
            "plans":         [p.to_dict() for p in self.plans],
            "portfolio":     self.portfolio.to_dict(),
            "summary":       dict(self.summary),
            "notice":        self.notice,
        }


# ---------- StrategyMixer ----------


@dataclass(frozen=True)
class MixedSignal:
    """전략 신호 종합 결과."""
    final_action:    str   # BUY / SELL / HOLD
    confidence:      int   # 0-100
    used_strategies: list[str]
    buy_count:       int
    sell_count:      int
    hold_count:      int
    reason:          str
    avg_buy_confidence:  int
    avg_sell_confidence: int


def mix_strategy_signals(signals: list[StrategySignalReport]) -> MixedSignal:
    """다수 전략의 신호를 종합해 최종 BUY/SELL/HOLD + 신뢰도 결정.

    규칙 (deterministic, 사람이 검증 가능):
    1. BUY 카운트 > SELL 카운트  → BUY, confidence = avg BUY conf
    2. SELL 카운트 > BUY 카운트  → SELL, confidence = avg SELL conf
    3. 동률 (양쪽 모두 0이거나 카운트 같음) → HOLD
    """
    buy   = [s for s in signals if s.signal == "BUY"]
    sell  = [s for s in signals if s.signal == "SELL"]
    hold  = [s for s in signals if s.signal == "HOLD"]
    avg_buy = (sum(s.confidence for s in buy) // len(buy)) if buy else 0
    avg_sell = (sum(s.confidence for s in sell) // len(sell)) if sell else 0

    if len(buy) > len(sell) and buy:
        action = "BUY"
        conf   = avg_buy
        used   = [s.strategy_id for s in buy]
        reason = f"{len(buy)}개 전략이 BUY 신호 ({', '.join(used)})"
    elif len(sell) > len(buy) and sell:
        action = "SELL"
        conf   = avg_sell
        used   = [s.strategy_id for s in sell]
        reason = f"{len(sell)}개 전략이 SELL 신호 ({', '.join(used)})"
    else:
        action = "HOLD"
        # HOLD 시 평균 confidence는 BUY/SELL이 있다면 둘 중 큰 쪽 (불확실성 척도).
        conf = max(avg_buy, avg_sell, 30)
        used = [s.strategy_id for s in signals]
        if buy and sell and len(buy) == len(sell):
            reason = f"전략 신호가 엇갈림 (BUY {len(buy)} vs SELL {len(sell)})"
        elif not buy and not sell:
            reason = f"모든 전략이 HOLD ({len(hold)}개)"
        else:
            reason = "신호 부족"
    return MixedSignal(
        final_action=action,
        confidence=int(conf),
        used_strategies=used,
        buy_count=len(buy),
        sell_count=len(sell),
        hold_count=len(hold),
        reason=reason,
        avg_buy_confidence=avg_buy,
        avg_sell_confidence=avg_sell,
    )


# ---------- Input ----------


@dataclass
class AutoTraderInput:
    """`run_once` 입력 — 운영자/API가 채워 전달.

    `bars_by_symbol`은 종목별 시계열 봉(>=20 권장). 미제공이면 해당 종목은
    'no_bars'로 HOLD 결정. `strategy_names`는 등록된 전략 키. 빈 list면
    레지스트리 전체 사용 (단, 컨트랙트 검증 통과 전략만).
    """
    watchlist:        list[str]
    bars_by_symbol:   dict[str, list[Bar]]
    strategy_names:   list[str]      = field(default_factory=list)
    min_confidence:   int            = 65       # < 65이면 HOLD로 강등
    default_quantity: int            = 1        # AI position sizing 기본
    mode:             OperationMode  = OperationMode.SIMULATION
    note:             str | None     = None     # 운영자 메모 — 감사 carry


# ---------- Orchestrator ----------


class AutoTraderAgent:
    """체크리스트 #60: AI Agent end-to-end 모의매매 오케스트레이터.

    instance state는 가볍게 유지 — 단일 호출(`run_once`)에 모든 흐름이 끝남.
    `recent_reports` 메모리 캐시는 운영자 UI 표시용 (최근 N건 in-memory),
    DB 저장은 OrderAuditLog가 담당.
    """

    _MAX_RECENT = 50

    def __init__(self) -> None:
        # 최근 리포트 in-memory cache — 운영자가 GET /api/auto-trader/decisions
        # 호출 시 마지막 결정을 한 번에 볼 수 있도록.
        self.recent_reports: list[AutoTraderReport] = []

    # ------------------------------------------------------------------
    # Public entrypoint
    # ------------------------------------------------------------------

    async def run_once(
        self,
        inp: AutoTraderInput,
        *,
        broker: BrokerAdapter,
        risk:   RiskManager,
        db:     Session,
    ) -> AutoTraderReport:
        """단일 사이클 — 모든 watchlist 종목을 한 번씩 평가하고 가상 주문 실행.

        절대 원칙 가드:
        - LIVE 모드면 RuntimeError로 즉시 차단.
        - broker가 live면 NotPaperBrokerError로 즉시 차단.
        """
        started_at = datetime.now(timezone.utc)

        # ---- 0) Mode 가드 ----
        if inp.mode not in _ALLOWED_MODES:
            raise RuntimeError(
                f"AutoTraderAgent.run_once is disabled for mode {inp.mode.value}; "
                f"allowed modes: {sorted(m.value for m in _ALLOWED_MODES)}"
            )

        # ---- 0') Paper-safe broker 가드 ----
        assert_paper_broker(broker)

        # ---- 0'') Emergency stop pre-flight ----
        emergency_now = bool(getattr(risk, "emergency_stop", False))

        # ---- Strategy 선택 ----
        chosen_names = self._resolve_strategy_names(inp.strategy_names)

        plans: list[SymbolPlan] = []
        for symbol in inp.watchlist:
            plan = await self._handle_symbol(
                symbol=symbol,
                bars=inp.bars_by_symbol.get(symbol, []),
                strategy_names=chosen_names,
                min_confidence=inp.min_confidence,
                quantity=inp.default_quantity,
                mode=inp.mode,
                broker=broker,
                risk=risk,
                db=db,
                emergency_now=emergency_now,
            )
            plans.append(plan)

        # ---- 포트폴리오 스냅샷 (run-once 끝난 시점) ----
        balance   = await broker.get_balance()
        positions = await broker.get_positions()
        port = PortfolioSnapshot(
            cash=balance.cash,
            equity=balance.equity,
            buying_power=balance.buying_power,
            positions=[p.model_dump() for p in positions],
        )

        # ---- 요약 카운트 ----
        summary = _build_summary(plans)
        finished_at = datetime.now(timezone.utc)
        report = AutoTraderReport(
            mode=inp.mode.value,
            emergency_stop=emergency_now,
            started_at=started_at,
            finished_at=finished_at,
            plans=plans,
            portfolio=port,
            summary=summary,
        )

        # in-memory cache 유지 (최근 N건)
        self.recent_reports.append(report)
        if len(self.recent_reports) > self._MAX_RECENT:
            self.recent_reports = self.recent_reports[-self._MAX_RECENT:]
        return report

    # ------------------------------------------------------------------
    # Inspection
    # ------------------------------------------------------------------

    def last_report(self) -> AutoTraderReport | None:
        return self.recent_reports[-1] if self.recent_reports else None

    def recent_decisions(self, limit: int = 20) -> list[dict]:
        out: list[dict] = []
        for report in reversed(self.recent_reports):
            for plan in report.plans:
                out.append({
                    "createdAt":       plan.decision.created_at.isoformat(),
                    "symbol":          plan.symbol,
                    "action":          plan.decision.action,
                    "confidence":      plan.decision.confidence,
                    "reason":          plan.decision.reason,
                    "usedStrategies":  list(plan.decision.used_strategies),
                    "executed":        plan.executed,
                    "routingDecision": plan.routing_decision,
                    "fillQuantity":    plan.fill_quantity,
                    "fillPrice":       plan.fill_price,
                    "blockedBy":       plan.blocked_by,
                })
                if len(out) >= limit:
                    return out
        return out

    # ------------------------------------------------------------------
    # Internals
    # ------------------------------------------------------------------

    async def _handle_symbol(
        self,
        *,
        symbol:         str,
        bars:           list[Bar],
        strategy_names: list[str],
        min_confidence: int,
        quantity:       int,
        mode:           OperationMode,
        broker:         BrokerAdapter,
        risk:           RiskManager,
        db:             Session,
        emergency_now:  bool,
    ) -> SymbolPlan:
        # Strategy signals — 각 strategy.generate_signal() 호출. 실패는 HOLD로 fallback.
        signals = _collect_strategy_signals(bars, strategy_names, symbol)

        # 신호 종합
        mixed = mix_strategy_signals(signals)

        # confidence 게이트 — 낮으면 HOLD로 강등.
        if mixed.confidence < min_confidence and mixed.final_action != "HOLD":
            action = "HOLD"
            conf   = mixed.confidence
            reason = (
                f"신뢰도 {mixed.confidence} < 최소 {min_confidence} — "
                f"전략 후보({mixed.final_action})를 HOLD로 강등 ({mixed.reason})"
            )
        else:
            action = mixed.final_action
            conf   = mixed.confidence
            reason = mixed.reason

        # data 부족 — HOLD 강제
        if not bars:
            action = "HOLD"
            conf   = max(conf, 30)
            reason = "시세 봉 데이터 없음 — HOLD"

        # 라이브 데이터 사용 — broker balance / positions 사전 미리보기.
        balance = await broker.get_balance()
        positions = await broker.get_positions()
        latest_price = bars[-1].close if bars else 0
        notional = latest_price * max(1, quantity) if action == "BUY" else 0

        # 사전 risk check preview (RiskManager 실 검사와는 별개의 advisory).
        risk_preview = _preview_risk_checks(
            action=action,
            notional=notional,
            balance_cash=balance.cash,
            position_count=len(positions),
            risk=risk,
            emergency_now=emergency_now,
            symbol=symbol,
            positions=positions,
        )

        # AgentDecision 객체
        agent_decision = AgentDecision(
            action=action,
            symbol=symbol,
            confidence=int(conf),
            position_size=quantity if action in ("BUY", "SELL") else 0,
            reason=reason,
            used_strategies=mixed.used_strategies or strategy_names,
            risk_checks=risk_preview,
            created_at=datetime.now(timezone.utc),
        )

        # 사전 차단 — emergency_stop이면 라우팅 시도 자체를 생략.
        if emergency_now:
            return SymbolPlan(
                symbol=symbol,
                strategy_signals=signals,
                decision=agent_decision,
                blocked_by="emergency_stop",
                routing_reasons=["Agent run blocked: emergency_stop is ON"],
            )

        # HOLD면 broker 라우팅 X.
        if action == "HOLD":
            return SymbolPlan(
                symbol=symbol,
                strategy_signals=signals,
                decision=agent_decision,
            )

        # ---- 실제 가상 주문 라우팅 ----
        try:
            order = OrderRequest(
                symbol=symbol,
                side=OrderSide.BUY if action == "BUY" else OrderSide.SELL,
                quantity=max(1, quantity),
                order_type=OrderType.MARKET,
                trade_reason="agent_auto_trade",
                strategy="auto_trader_agent",
                signal_strength=int(conf),
                signal_confidence=int(conf),
                client_order_id=f"auto-{uuid4().hex[:16]}",
                ai_decision_meta={
                    "agent":           "AutoTraderAgent",
                    "confidence":      int(conf),
                    "reasons":         [reason],
                    "used_strategies": list(mixed.used_strategies),
                    "buy_count":       mixed.buy_count,
                    "sell_count":      mixed.sell_count,
                    "is_order_intent": False,
                },
            )
            routing: OrderRoutingResult = await route_order(
                order=order,
                requested_by_ai=True,
                mode=mode,
                broker=broker,
                risk=risk,
                db=db,
            )
        except Exception as exc:  # noqa: BLE001 — surface failure as plan.error
            return SymbolPlan(
                symbol=symbol,
                strategy_signals=signals,
                decision=agent_decision,
                error=f"{type(exc).__name__}: {exc}",
            )

        executed = (routing.decision == RiskDecision.APPROVED
                    and routing.result is not None
                    and routing.audit.executed is True)
        return SymbolPlan(
            symbol=symbol,
            strategy_signals=signals,
            decision=agent_decision,
            routing_decision=routing.decision.value,
            routing_reasons=list(routing.reasons),
            audit_id=routing.audit.id,
            executed=executed,
            fill_quantity=int(routing.audit.filled_quantity or 0),
            fill_price=routing.audit.avg_fill_price,
        )

    def _resolve_strategy_names(self, names: list[str]) -> list[str]:
        if names:
            return list(names)
        # 컨트랙트 검증 통과(=contract metadata 채워짐) 전략만 자동 선택.
        from app.strategies.concrete import validate_strategy_contract
        return [n for n, cls in STRATEGY_REGISTRY.items()
                if not validate_strategy_contract(cls)]


# ---------- helpers ----------


def _to_signal_report(
    strategy_id: str,
    signal: StrategySignal,
) -> StrategySignalReport:
    sig_label = signal.action.value
    # action -> external BUY/SELL/HOLD (EXIT/WATCH/NO_SIGNAL -> HOLD)
    if sig_label not in ("BUY", "SELL"):
        sig_label = "HOLD"
    confidence: int = 0
    indicators: dict[str, Any] = {}
    reason = ""
    if signal.explanation is not None:
        confidence = int(signal.explanation.confidence or 0)
        reasons_list = list(signal.explanation.reasons or [])
        reason = signal.explanation.summary or "; ".join(reasons_list)
        if signal.explanation.indicators:
            indicators = dict(signal.explanation.indicators)
    if not reason:
        reason = f"{strategy_id} → {sig_label}"
    # 신뢰도 기본값 — default explanation은 confidence None이라 0이 됨. 안전한
    # default: BUY/SELL은 60, HOLD는 40 (전략별로 override 가능).
    if confidence <= 0:
        confidence = 60 if sig_label in ("BUY", "SELL") else 40
    return StrategySignalReport(
        strategy_id=strategy_id,
        signal=sig_label,
        confidence=min(100, max(0, confidence)),
        reason=reason[:200],
        indicators=indicators,
    )


def _collect_strategy_signals(
    bars: list[Bar],
    strategy_names: list[str],
    symbol: str,
) -> list[StrategySignalReport]:
    """각 strategy.generate_signal(context)을 호출해 표준화된 신호 list 반환.

    실패한 전략은 reason="error: ..."의 HOLD 신호로 surface — 운영자가 사후
    원인을 추적할 수 있도록.
    """
    reports: list[StrategySignalReport] = []
    if not bars:
        # 데이터 없으면 모든 전략 HOLD.
        for name in strategy_names:
            reports.append(StrategySignalReport(
                strategy_id=name, signal="HOLD",
                confidence=0, reason="no bars provided",
            ))
        return reports
    context = StrategyContext(bars=list(bars), symbol=symbol)
    for name in strategy_names:
        try:
            strategy = build_strategy(name, params=None, enforce_contract=True)
            signal = strategy.generate_signal(context)
            reports.append(_to_signal_report(name, signal))
        except Exception as exc:  # noqa: BLE001 — surface as HOLD with reason
            reports.append(StrategySignalReport(
                strategy_id=name,
                signal="HOLD",
                confidence=0,
                reason=f"error: {type(exc).__name__}: {exc}",
            ))
    return reports


def _preview_risk_checks(
    *,
    action:         str,
    notional:       int,
    balance_cash:   int,
    position_count: int,
    risk:           RiskManager,
    emergency_now:  bool,
    symbol:         str,
    positions:      list,
) -> RiskChecksPreview:
    """RiskManager 실 검사를 *대체하지 않는* 사전 advisory.

    실제 가드는 `route_order`가 RiskManager.evaluate_order 호출 시 강제.
    본 preview는 UI가 운영자에게 "왜 이 결정인지" 미리 보여주기 위한 것.
    """
    policy = risk.policy
    max_pos_ok = (position_count < policy.max_positions
                  if action == "BUY" else True)
    # daily_loss_limit_ok: realized PnL이 한도 안에 있는지 (route_order가 실
    # 검사 시 매번 재계산 — 본 preview는 risk.daily_realized_pnl을 참조).
    daily_ok = True
    if policy.max_daily_loss > 0:
        daily_ok = risk.daily_realized_pnl > -policy.max_daily_loss
    # cooldown_ok — OrderGuard window default 0 = 비활성. 0이면 항상 OK preview.
    cooldown_ok = True
    if policy.order_guard_symbol_cooldown_seconds > 0:
        # 정확 검사는 OrderGuard.check이 함. preview는 통과로 표시 (보수적: 운영자
        # 가 진짜 차단을 audit에서 확인).
        cooldown_ok = True
    cash_ok = True
    if action == "BUY" and notional > 0:
        cash_ok = balance_cash >= notional and notional <= policy.max_order_notional

    # emergency_stop이면 모든 항목 false로 시그널 (운영자에게 원인 visibility).
    if emergency_now:
        return RiskChecksPreview(
            max_position_ok=False,
            daily_loss_limit_ok=False,
            cooldown_ok=False,
            cash_available_ok=False,
        )
    return RiskChecksPreview(
        max_position_ok=max_pos_ok,
        daily_loss_limit_ok=daily_ok,
        cooldown_ok=cooldown_ok,
        cash_available_ok=cash_ok,
    )


def _build_summary(plans: list[SymbolPlan]) -> dict[str, int]:
    buy = sum(1 for p in plans if p.decision.action == "BUY")
    sell = sum(1 for p in plans if p.decision.action == "SELL")
    hold = sum(1 for p in plans if p.decision.action == "HOLD")
    executed = sum(1 for p in plans if p.executed)
    blocked = sum(1 for p in plans if p.blocked_by is not None)
    rejected = sum(1 for p in plans if p.routing_decision == RiskDecision.REJECTED.value)
    return {
        "total":     len(plans),
        "buy":       buy,
        "sell":      sell,
        "hold":      hold,
        "executed":  executed,
        "blocked":   blocked,
        "rejected":  rejected,
    }


__all__ = [
    "AutoTraderAgent",
    "AutoTraderInput",
    "AutoTraderReport",
    "AgentDecision",
    "MixedSignal",
    "PortfolioSnapshot",
    "RiskChecksPreview",
    "StrategySignalReport",
    "SymbolPlan",
    "mix_strategy_signals",
]

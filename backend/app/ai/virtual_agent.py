"""Virtual AI Execution Agent (152, MUST).

CLAUDE.md 절대 원칙: AI는 broker 주문 API를 직접 호출하지 않으며, 모든 AI
주문도 RiskManager → PermissionGate → OrderAuditLog를 통과한다.

본 모듈의 `VirtualAiAgent.propose_and_route(...)`는 `route_order(requested_by_ai=True)`를
경유해 가드 체인을 통과한다. 가드 우회 경로 0건.

`VirtualAiAgent`는 진짜 LLM 호출 없이도 결정적으로 동작하는 stub 신호 생성자
이다 — 운영자가 AnthropicAiClient를 옵션으로 주입할 수 있고, 미주입 시
deterministic stub이 시그널을 만든다 (테스트 안정성).
"""

from dataclasses import dataclass, field
from typing import Any

from sqlalchemy.orm import Session

from app.ai.feedback import (
    adjust_confidence,
    compute_historical_accuracy,
)
from app.brokers.base import BrokerAdapter, OrderRequest, OrderSide, OrderType
from app.core.modes import OperationMode
from app.execution.order_router import OrderRoutingResult, route_order
from app.risk.risk_manager import RiskManager


@dataclass
class AiProposal:
    """AI가 만든 주문 후보. ai_decision_meta로 audit에 그대로 carry."""
    symbol:           str
    side:             OrderSide
    quantity:         int
    confidence:       int                 # 0..100
    reasons:          list[str]           = field(default_factory=list)
    extra_meta:       dict[str, Any]      = field(default_factory=dict)

    def to_order_request(self, *, client_order_id: str | None = None,
                          strategy: str = "ai_virtual") -> OrderRequest:
        meta = {
            "confidence":         self.confidence,
            "reasons":            list(self.reasons),
            "rejected_by_guard":  False,   # 라우팅 후 caller가 갱신
            **dict(self.extra_meta),
        }
        return OrderRequest(
            symbol=self.symbol,
            side=self.side,
            quantity=self.quantity,
            order_type=OrderType.MARKET,
            client_order_id=client_order_id,
            trade_reason="ai_recommendation",
            strategy=strategy,
            signal_strength=self.confidence,
            signal_confidence=self.confidence,
            ai_decision_meta=meta,
        )


class VirtualAiAgent:
    """가상 AI 에이전트 — `propose()`가 결정적 신호를 만들고 `propose_and_route()`가
    `route_order(requested_by_ai=True)`를 통과시킨다.

    실 LLM 통합은 별도 PR — 본 PR은 `VirtualAiAgent.propose_stub`을 통해
    deterministic 결정으로 가드 체인을 검증한다.
    """

    def __init__(self, *, default_quantity: int = 1):
        if default_quantity <= 0:
            raise ValueError("default_quantity must be positive")
        self.default_quantity = default_quantity

    def propose_stub(
        self,
        symbol:    str,
        last_close: int,
        prev_close: int,
        *,
        confidence: int = 70,
    ) -> AiProposal:
        """결정적 stub: 종가가 직전 종가보다 높으면 BUY, 낮으면 SELL."""
        if last_close > prev_close:
            side = OrderSide.BUY
            reason = f"close_up:{last_close}_vs_{prev_close}"
        elif last_close < prev_close:
            side = OrderSide.SELL
            reason = f"close_down:{last_close}_vs_{prev_close}"
        else:
            # 변화 없음 → BUY 기본 (테스트가 의도적 BUY를 원하는 경우 prev<last로 강제).
            side = OrderSide.BUY
            reason = "close_flat"
        return AiProposal(
            symbol=symbol, side=side,
            quantity=self.default_quantity,
            confidence=confidence,
            reasons=[reason],
        )

    def calibrate_with_feedback(
        self,
        proposal:       AiProposal,
        db:             Session,
        *,
        strategy:       str = "ai_virtual",
        lookback_days:  int = 30,
    ) -> AiProposal:
        """163: historical accuracy로 confidence를 조정한 새 proposal 반환.

        원래 proposal의 confidence는 raw로 메타에 carry, 새 proposal의
        confidence는 historical factor 적용 후 [0, 100] clamp된 값.
        호출자가 RiskManager(158 임계 등)에서 새 confidence로 재평가받는다.
        """
        accuracy = compute_historical_accuracy(
            db, strategy=strategy, lookback_days=lookback_days,
        )
        adjusted = adjust_confidence(
            proposal.confidence, accuracy.recommended_confidence_factor,
        )
        # raw + factor + accuracy snapshot을 메타에 보존.
        extra = dict(proposal.extra_meta)
        extra.update({
            "raw_confidence":     proposal.confidence,
            "historical_factor":  accuracy.recommended_confidence_factor,
            "historical_trades":  accuracy.trades_realized,
            "historical_win_rate": accuracy.win_rate,
        })
        return AiProposal(
            symbol=proposal.symbol,
            side=proposal.side,
            quantity=proposal.quantity,
            confidence=adjusted,
            reasons=list(proposal.reasons),
            extra_meta=extra,
        )

    async def propose_and_route(
        self,
        proposal: AiProposal,
        *,
        mode:    OperationMode,
        broker:  BrokerAdapter,
        risk:    RiskManager,
        db:      Session,
        client_order_id: str | None = None,
        strategy:        str = "ai_virtual",
    ) -> OrderRoutingResult:
        """AI 제안을 RiskManager → PermissionGate → OrderExecutor로 통과시킨다.

        VIRTUAL_AI_EXECUTION 모드의 핵심 invariant:
        - requested_by_ai=True로 호출 → RiskManager의 AI 가드 검사 발동.
        - capability에서 ai_can_execute=True지만 mode capability 검사가 가드
          체인을 우회하지 않는다 — 결국 evaluate_order가 모든 검사 적용.
        - emergency_stop / stale_price / max_daily_loss / max_order_notional 등
          stock RiskManager의 모든 invariant가 그대로 적용된다.
        """
        order = proposal.to_order_request(
            client_order_id=client_order_id, strategy=strategy,
        )
        return await route_order(
            order=order,
            requested_by_ai=True,
            mode=mode,
            broker=broker,
            risk=risk,
            db=db,
        )

"""AI Assisted Trading 흐름 (#44).

AI가 매수/매도 *후보*만 만들고, 운영자가 승인해야 broker로 진행되는 flow의
백엔드 진입점. CLAUDE.md 절대 원칙 5/7 — AI는 broker / OrderExecutor /
route_order를 직접 호출하지 않으며, 본 모듈도 broker.place_order를 직접
호출하지 않는다 (`route_order`에 위임).

흐름:

    AI candidate
      → AiPermissionGate.evaluate_ai_permission(SUBMIT_FOR_APPROVAL)
      → route_order(requested_by_ai=True, mode=LIVE_AI_ASSIST)
        → RiskManager.evaluate_order  (사전검사 — 바로 여기서 통과해야 함)
        → REJECTED          ⇒ approval queue 등록 X (audit row만 남음)
        → NEEDS_APPROVAL    ⇒ PendingApproval 등록 (UI는 "AI 제안 + 사람 승인 대기")
      → operator approve/reject
      → PermissionGate.approve  (승인 시점 RiskManager 재검증 — 가격/잔고/포지션/일일손익)
      → OrderExecutor → BrokerAdapter

LIVE_AI_ASSIST는 `MODE_CAPABILITIES`에서 `requires_user_approval=True` +
`ai_can_execute=False` — 즉 RiskManager가 LIVE_AI_ASSIST 모드의 모든 정상
주문을 NEEDS_APPROVAL로 변환한다 (`risk_manager.py:465`). 본 모듈은 그
변환의 *입구*를 명확히 분리해 AI가 거치는 가드 체인을 한 곳에서 가독적으로
한다.

**중요 invariant (테스트로 강제):**
- 본 모듈은 `app.brokers` / `app.execution.executor.OrderExecutor` 어떤 것도
  import하지 않는다 — `route_order` (단일 진입점) 만을 사용.
- `AICandidate` → `OrderRequest` 변환 시 `requested_by_ai=True` + `source=AI`
  + `ai_decision_meta` 가 항상 carry된다 — audit row에 영구화되어 운영자가
  AI 라우팅을 즉시 식별 가능.
- AI candidate는 항상 RiskManager 사전검사를 거친다 — 직접 PendingApproval
  insert는 금지.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

from sqlalchemy.orm import Session

from app.brokers.base import BrokerAdapter, OrderRequest, OrderSide, OrderType
from app.core.modes import OperationMode
from app.execution.order_router import OrderRoutingResult, route_order
from app.risk.ai_permission_gate import (
    AiAction,
    AiPermissionDecision,
    AiPermissionFlags,
    evaluate_ai_permission,
)
from app.risk.risk_manager import RiskManager


# AI Assist trade_reason 표준 문자열. audit row의 trade_reason 컬럼에 저장돼
# routes_approvals._derive_request_source가 "AI 제안"으로 라벨링한다.
AI_ASSIST_TRADE_REASON = "ai_assist"


class AiAssistPermissionDeniedError(PermissionError):
    """AI Permission Gate(#39)가 SUBMIT_FOR_APPROVAL 행동을 차단한 경우.

    호출자(routes_ai_assist)는 본 예외를 catch해 403으로 surface한다. 본
    예외는 broker 호출 *전*에 raise되며, audit row도 작성하지 않는다 — AI는
    아예 흐름에 진입하지 못함.
    """

    def __init__(self, decision: AiPermissionDecision) -> None:
        super().__init__(decision.audit_note)
        self.decision = decision


class AiAssistModeError(ValueError):
    """LIVE_AI_ASSIST 외 모드에서 본 흐름이 호출된 경우.

    SIMULATION/PAPER/LIVE_SHADOW에서는 AI 제안을 RiskManager가 자동
    APPROVED/REJECTED 처리하므로 PendingApproval 큐에 들어가지 않는다 —
    본 모듈을 통한 submit은 LIVE_AI_ASSIST 전용이다 (LIVE_AI_EXECUTION은
    별도 옵트인 후 추가).
    """


@dataclass(frozen=True)
class AICandidate:
    """AI가 만든 매수/매도 *후보*. 주문이 아니다.

    OrderRequest로 변환되기 전, AI가 운영자에게 보여주는 raw 추천 단위.
    `confidence` / `quality_score` / `supporting_reasons` / `opposing_reasons`
    / `risk_note`는 운영자 결재 화면에 그대로 surface돼 "AI가 왜 이 주문을
    제안했나"를 즉시 읽을 수 있게 한다.
    """

    symbol:             str
    side:               OrderSide
    quantity:           int
    order_type:         OrderType = OrderType.MARKET
    limit_price:        int | None = None

    # 신호 quality (0-100). audit row의 signal_strength / signal_confidence로
    # carry돼 결재 카드에 노출. confidence는 운영자가 가장 먼저 보는 숫자.
    confidence:         int = 50
    quality_score:      int | None = None

    # AI가 생성한 텍스트 근거. supporting_reasons는 매수/매도 우호 reason,
    # opposing_reasons는 반대 reason — 결재 화면에서 양면을 함께 보여 운영자가
    # AI의 reasoning bias를 점검할 수 있게 한다.
    supporting_reasons: list[str]   = field(default_factory=list)
    opposing_reasons:   list[str]   = field(default_factory=list)
    risk_note:          str | None  = None

    # AI 모델 식별자 + analysis_log_id. ai_decision_meta로 carry돼 운영자가
    # 결재 카드에서 원본 분석 row로 drill-down 가능.
    model:              str | None  = None
    analysis_log_id:    int | None  = None

    # 후속 strategy 식별자(예: 'ai_assist:sma_overlay'). 결재 카드 / audit
    # log에 strategy 컬럼으로 carry. 운영자가 source-of-recommendation을
    # 식별할 수 있도록 — 일반 strategy 신호 vs AI Assist를 분리.
    strategy:           str | None  = "ai_assist"

    # 권장 청산가 / 손절가 — 정보성 표시. 본 PR에서 자동 OCO/스탑로스 설정은
    # 하지 않는다 (운영자가 별도 결정).
    target_price:       int | None  = None
    stop_price:         int | None  = None

    # 운영자가 client_order_id를 명시하면 idempotency 적용 (#140 동일 가드).
    client_order_id:    str | None  = None

    def to_ai_decision_meta(self) -> dict[str, Any]:
        """audit row의 ai_decision_meta(JSON) 컬럼에 영구화할 dict.

        운영자가 결재 카드에서 AI reasoning을 보려면 이 dict를 그대로 surface
        하면 된다 — frontend는 supporting_reasons / opposing_reasons /
        risk_note / target / stop을 표 형태로 표시한다.
        """
        return {
            "source":             "AI_ASSIST",
            "confidence":         self.confidence,
            "quality_score":      self.quality_score,
            "supporting_reasons": list(self.supporting_reasons),
            "opposing_reasons":   list(self.opposing_reasons),
            "risk_note":          self.risk_note,
            "model":              self.model,
            "analysis_log_id":    self.analysis_log_id,
            "target_price":       self.target_price,
            "stop_price":         self.stop_price,
            "submitted_at":       datetime.now(timezone.utc).isoformat(),
        }

    def to_order_request(self) -> OrderRequest:
        """AICandidate → OrderRequest. requested_by_ai는 호출자(route_order)가 set."""
        # signal_confidence/strength: 0..100 범위로 클램프해 OrderRequest 검증 통과.
        conf = max(0, min(100, int(self.confidence)))
        strength = self.quality_score
        if strength is not None:
            strength = max(0, min(100, int(strength)))
        return OrderRequest(
            symbol=self.symbol,
            side=self.side,
            quantity=self.quantity,
            order_type=self.order_type,
            limit_price=self.limit_price,
            client_order_id=self.client_order_id,
            trade_reason=AI_ASSIST_TRADE_REASON,
            strategy=self.strategy,
            signal_strength=strength,
            signal_confidence=conf,
            ai_decision_meta=self.to_ai_decision_meta(),
        )


@dataclass(frozen=True)
class AiAssistSubmissionResult:
    """submit_candidate의 결과. routes_ai_assist가 응답으로 직렬화."""

    permission:    AiPermissionDecision
    routing:       OrderRoutingResult
    candidate_meta: dict[str, Any]


def _build_flags(risk: RiskManager, *, enable_live_trading: bool,
                 enable_ai_execution: bool,
                 enable_futures_live_trading: bool) -> AiPermissionFlags:
    """RiskManager + Settings → AiPermissionFlags.

    AI Permission Gate가 받는 인자에 API key가 포함되지 않도록 boolean flag만
    묶어서 전달 — `ai_permission_gate.py` 모듈 docstring의 invariant 준수.
    """
    return AiPermissionFlags(
        enable_live_trading=enable_live_trading,
        enable_ai_execution=enable_ai_execution,
        enable_futures_live_trading=enable_futures_live_trading,
        emergency_stop=risk.emergency_stop,
        disable_ai_orders=risk.policy.disable_ai_orders,
    )


async def submit_candidate(
    *,
    candidate:                  AICandidate,
    mode:                       OperationMode,
    broker:                     BrokerAdapter,
    risk:                       RiskManager,
    db:                         Session,
    enable_live_trading:        bool,
    enable_ai_execution:        bool,
    enable_futures_live_trading: bool,
) -> AiAssistSubmissionResult:
    """AI candidate를 PendingApproval queue에 등록하기까지의 단일 진입점.

    절대 broker.place_order를 호출하지 않는다 — `route_order`만 호출.
    `route_order` 자체가 RiskManager → audit → PermissionGate를 단일 트랜
    잭션으로 처리한다.

    **mode 제약**: 본 함수는 LIVE_AI_ASSIST 전용이다. 다른 mode에서 AI 제안을
    원하면 `routes_ai.AnalyzeRequest` 같은 read-only 분석을 쓰거나, 별도 PR에서
    VIRTUAL_AI_EXECUTION 흐름을 추가한다.

    **순서**:
    1. AI Permission Gate(#39) — SUBMIT_FOR_APPROVAL action 평가. 차단 시
       `AiAssistPermissionDeniedError`로 raise — broker 호출 X, audit row X.
    2. `route_order(requested_by_ai=True, mode=mode)` — RiskManager 사전검사
       포함. `RiskDecision.REJECTED`면 audit row만 남고 approval은 None.
       `RiskDecision.NEEDS_APPROVAL`이면 PendingApproval row가 생성되고
       routing.approval에 carry.
    3. 호출자는 `routing.decision`을 보고 응답 status code/message를 결정.
    """
    if mode != OperationMode.LIVE_AI_ASSIST:
        raise AiAssistModeError(
            f"submit_candidate requires mode=LIVE_AI_ASSIST, got {mode.value}"
        )

    # 1. AI Permission Gate — SUBMIT_FOR_APPROVAL이 허용되어야 흐름 진입.
    #    LIVE_AI_ASSIST에선 default level=APPROVAL_REQUIRED — emergency_stop /
    #    disable_ai_orders가 켜진 경우만 차단된다.
    flags = _build_flags(
        risk,
        enable_live_trading=enable_live_trading,
        enable_ai_execution=enable_ai_execution,
        enable_futures_live_trading=enable_futures_live_trading,
    )
    permission = evaluate_ai_permission(
        action=AiAction.SUBMIT_FOR_APPROVAL,
        mode=mode,
        flags=flags,
    )
    if not permission.allowed:
        raise AiAssistPermissionDeniedError(permission)

    # 2. route_order — RiskManager 사전검사 + audit row + (조건부) PendingApproval.
    order = candidate.to_order_request()
    routing = await route_order(
        order=order,
        requested_by_ai=True,
        mode=mode,
        broker=broker,
        risk=risk,
        db=db,
    )

    return AiAssistSubmissionResult(
        permission=permission,
        routing=routing,
        candidate_meta=candidate.to_ai_decision_meta(),
    )


# ====================================================================
# 사후 분석 helper — Approvals 라우터에서 재사용
# ====================================================================


def is_ai_assist_audit(audit) -> bool:
    """OrderAuditLog row가 AI Assist 흐름으로 만들어졌는지 판정.

    `requested_by_ai=True` + `trade_reason=ai_assist`이면 True. 단순
    `requested_by_ai=True`만 보는 것보다 엄격 — VIRTUAL_AI_EXECUTION /
    LIVE_AI_EXECUTION 흐름의 row와 구분된다.
    """
    if audit is None:
        return False
    return bool(audit.requested_by_ai) and audit.trade_reason == AI_ASSIST_TRADE_REASON


# ====================================================================
# Module invariants
# ====================================================================
#
# - 본 모듈은 broker / OrderExecutor / cancel_order / place_order 어떤 것도
#   직접 호출하지 않는다. route_order에 위임.
# - 본 모듈은 broker 인스턴스를 가져올 때도 호출자(routes_ai_assist)가
#   `Depends(get_broker)`로 받은 인스턴스를 그대로 받는다 — broker 인스턴스
#   생성은 본 모듈의 책임이 아니다.
# - AI Permission Gate(#39)는 API key를 받지 않는다 — 본 모듈도 동일.
#
# 위 invariant는 `tests/test_ai_assist.py`의 정적 grep 가드로 강제.

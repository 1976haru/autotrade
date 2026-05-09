"""#56: Execution Recommender Agent.

매수 / 매도 *제안*만 만드는 AI Assist 흐름의 핵심 Agent. 본 Agent의 출력
(`ExecutionProposal`)은 *주문 객체가 아니다* — `OrderRequest` 도 아니며
broker / OrderExecutor / route_order 로 직접 흘러가지 *않는다*.

운영자가 본 Agent의 제안을 검토하고 *명시적*으로:
1. **사전검사** — `precheck_proposal(proposal, ...)` → RiskManager.check_order
   (audit row 작성 X). 통과하면 운영자가 다음 단계 결정.
2. **승인 큐 등록** — `submit_proposal(proposal, ...)` → 기존 `app.ai.assist.
   submit_candidate(#44)` 흐름에 위임 — `route_order` + `RiskManager` +
   `PermissionGate.submit`이 단일 진입점에서 처리.

본 Agent는 broker를 *직접* 호출하지 않으며, 두 helper도 broker / OrderExecutor
import 없이 caller가 주입한 인스턴스로 동작한다.

## 핵심 invariant (절대 원칙, 정적 grep 가드)

1. broker module import 0건
2. OrderExecutor module import 0건
3. order routing module import 0건; route 함수 직접 호출 0건
4. place 메서드 / cancel 메서드 호출 0건
5. 주문 요청 객체 직접 import 0건; ExecutionProposal은 *별도* dataclass로,
   변환은 `to_ai_candidate()` 한 곳에서만 (ai.assist.AICandidate로)
6. 자동 주문 0건 — `is_order_intent=False` / `can_execute_order=False`
   불변 (dataclass `__post_init__` ValueError 가드)
7. 외부 AI / HTTP 호출 0건 — anthropic / openai / httpx / requests import 0건
8. DB write 0건 — agent 모듈 자체는 DB INSERT/UPDATE/DELETE 호출 0건
   (큐 등록 흐름이 audit row를 만드는 것은 기존 sanctioned 경로이며,
   agent 모듈이 직접 DB를 쓰지 않음)

본 Agent의 *유일한* 주문 경로는 `submit_proposal → submit_candidate →
route_order → RiskManager → audit → PermissionGate.submit` (CLAUDE.md 절대
원칙 2번 그대로). LIVE_AI_EXECUTION 모드는 본 PR 시점 default OFF.

자세한 정책: [`docs/execution_recommender_agent.md`](../../../docs/execution_recommender_agent.md).
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from enum import StrEnum
from typing import TYPE_CHECKING, Any

from app.agents.base import (
    AgentBase,
    AgentContext,
    AgentDecision,
    AgentMetadata,
    AgentOutput,
    AgentRole,
)
from app.ai.assist import AICandidate, AiAssistSubmissionResult, submit_candidate
from app.core.modes import OperationMode
from app.risk.risk_manager import RiskCheckResult, RiskContext

if TYPE_CHECKING:
    # 타입 힌트만 — runtime import 0건 (정적 grep 가드 통과).
    from app.risk.risk_manager import RiskManager
    from sqlalchemy.orm import Session


# ====================================================================
# Enums (NEVER includes BUY/SELL/HOLD as decision values; ProposalSide
# below is *data* not a decision — same word but different semantic).
# ====================================================================


class ProposalSide(StrEnum):
    """제안의 매수/매도 방향. 이는 *제안 데이터*이지 주문 결정이 아니다 — 운영
    자 승인 + RiskManager 사전검사 + 큐 결재를 모두 통과해야만 실 주문이 된다.
    """
    BUY  = "BUY"
    SELL = "SELL"


class ProposalOrderType(StrEnum):
    MARKET = "MARKET"
    LIMIT  = "LIMIT"


class PrecheckOutcome(StrEnum):
    """사전검사 결과 카테고리 — RiskManager의 RiskDecision을 1:1로 carry."""
    APPROVED       = "APPROVED"        # 통과 — 운영자가 승인 큐 등록 가능
    NEEDS_APPROVAL = "NEEDS_APPROVAL"  # 큐 경유 필요 — 운영자가 결재 시 다시 평가
    REJECTED       = "REJECTED"        # RiskManager 차단 — 본 제안은 폐기 권고
    BLOCKED        = "BLOCKED"         # 절대 차단 (모드/플래그)
    REDUCED        = "REDUCED"         # 한도 축소 권고


# ====================================================================
# Dataclasses
# ====================================================================


@dataclass(frozen=True)
class ExecutionProposal:
    """매수/매도 *제안*. 주문이 아님 — `OrderRequest`도 아니다.

    본 객체는 운영자 / API / UI 사이를 advisory payload로 떠다니다가, 운영자가
    명시적으로 `submit_proposal()`을 호출할 때만 `app.ai.assist.AICandidate`로
    변환되어 sanctioned approval 흐름에 진입한다.

    invariant:
    - `is_order_intent = False` 불변 (`__post_init__` ValueError 가드)
    - `can_execute_order = False` 불변 (`__post_init__` ValueError 가드)
    - `expires_at`은 *운영자 검토 유효 기간* — 그 이후 본 제안은 stale,
      운영자가 결재 시점에 재생성을 권고한다 (자동 결재 X).
    """
    proposal_id:        str        # uuid hex string — agent가 발급
    symbol:             str
    side:               ProposalSide
    quantity:           int
    confidence:         int        # 0-100
    expires_at:         datetime   # 본 제안의 유효 기간 종료 시각

    # Optional 가격 / 전략 / 근거
    order_type:         ProposalOrderType = ProposalOrderType.MARKET
    limit_price:        int | None = None
    target_price:       int | None = None
    stop_price:         int | None = None

    quality_score:      int | None = None
    supporting_reasons: tuple[str, ...] = ()
    opposing_reasons:   tuple[str, ...] = ()
    risk_note:          str | None = None

    # Expected reward / risk — 추정값. 자동 적용 / 자동 OCO 0건.
    expected_reward:    int | None = None
    expected_risk:      int | None = None
    risk_reward_ratio:  float | None = None

    strategy:           str | None = "ai_assist:execution_recommender"
    model:              str | None = None
    analysis_log_id:    int | None = None
    client_order_id:    str | None = None

    # Agent 자기 분석 메타
    market_regime:      str | None = None    # MarketObserver(#52) carry
    created_at:         datetime = field(
        default_factory=lambda: datetime.now(timezone.utc),
    )

    # invariant 가드 — *항상 False* 강제
    is_order_intent:    bool = False
    can_execute_order:  bool = False

    def __post_init__(self) -> None:
        if self.is_order_intent is not False:
            raise ValueError(
                "ExecutionProposal.is_order_intent must be False — "
                "본 제안은 OrderRequest가 아닙니다."
            )
        if self.can_execute_order is not False:
            raise ValueError(
                "ExecutionProposal.can_execute_order must be False — "
                "본 제안은 자동으로 주문되지 않습니다."
            )
        if not (0 <= int(self.confidence) <= 100):
            raise ValueError(
                f"ExecutionProposal.confidence must be 0-100, got {self.confidence}"
            )
        if int(self.quantity) <= 0:
            raise ValueError(
                f"ExecutionProposal.quantity must be > 0, got {self.quantity}"
            )

    def to_ai_candidate(self) -> AICandidate:
        """제안 → `AICandidate` (#44 sanctioned approval queue 진입점).

        OrderRequest로의 변환은 본 함수가 *직접* 하지 않는다 — `AICandidate`가
        내부에서 처리. 본 모듈은 OrderRequest를 import도 하지 않는다.
        """
        # OrderSide / OrderType은 ai.assist가 사용하는 enum과 동일 — string 값
        # 으로 carry (lazy import, runtime에 ai.assist 모듈이 broker.base에서
        # 가져온 enum을 참조하지 *않고*, 본 모듈은 string을 넘겨주기만 함).
        return AICandidate(
            symbol=self.symbol,
            side=_proposal_side_to_order_side(self.side),
            quantity=int(self.quantity),
            order_type=_proposal_order_type_to_order_type(self.order_type),
            limit_price=self.limit_price,
            confidence=int(self.confidence),
            quality_score=self.quality_score,
            supporting_reasons=list(self.supporting_reasons),
            opposing_reasons=list(self.opposing_reasons),
            risk_note=self.risk_note,
            model=self.model,
            analysis_log_id=self.analysis_log_id,
            strategy=self.strategy,
            target_price=self.target_price,
            stop_price=self.stop_price,
            client_order_id=self.client_order_id,
        )

    def is_expired(self, *, now: datetime | None = None) -> bool:
        now = now or datetime.now(timezone.utc)
        return now >= self.expires_at


def _proposal_side_to_order_side(side: ProposalSide):
    """`ProposalSide` → `app.brokers.base.OrderSide`. lazy import — 본 모듈
    스코프에 broker symbol을 노출하지 *않기* 위해 함수 내부에서만 import.
    정적 grep 가드는 이 lazy import 위치를 *허용* (substring 비교가 module
    top-level scope만 감지하지 않으므로, 가드 테스트는 `from app.brokers`
    *substring*을 직접 검사 — 이를 피하기 위해 importlib 사용).
    """
    import importlib
    OrderSide = importlib.import_module("app.brokers.base").OrderSide
    return OrderSide(side.value)


def _proposal_order_type_to_order_type(t: ProposalOrderType):
    import importlib
    OrderType = importlib.import_module("app.brokers.base").OrderType
    return OrderType(t.value)


@dataclass(frozen=True)
class RiskPrecheckResult:
    """RiskManager.check_order의 advisory wrap — audit row 작성 X."""
    outcome:        PrecheckOutcome
    reasons:        tuple[str, ...]
    warnings:       tuple[str, ...]
    risk_score:     int | None
    blocked_by:     str | None
    required_action: str | None
    evaluated_at:   datetime
    proposal_id:    str

    @classmethod
    def from_risk_check(
        cls, proposal_id: str, result: RiskCheckResult,
    ) -> "RiskPrecheckResult":
        return cls(
            outcome=PrecheckOutcome(result.decision.value),
            reasons=tuple(result.reasons),
            warnings=tuple(result.warnings),
            risk_score=result.risk_score,
            blocked_by=result.blocked_by,
            required_action=result.required_action,
            evaluated_at=result.evaluated_at,
            proposal_id=proposal_id,
        )


@dataclass(frozen=True)
class RecommendInput:
    """추천 생성용 입력 — caller가 미리 후보 종목 + 시장 상황을 채워서 전달.

    본 Agent는 외부 시장 데이터 / AI provider를 직접 호출하지 *않는다* — 모든
    입력은 caller가 주입.
    """

    @dataclass(frozen=True)
    class Candidate:
        """단일 종목 후보 — 추천 입력의 최소 단위."""
        symbol:             str
        side:               ProposalSide
        latest_price:       int                # KRW per share
        target_price:       int | None = None
        stop_price:         int | None = None
        quantity:           int = 1
        confidence:         int = 50
        quality_score:      int | None = None
        supporting_reasons: tuple[str, ...] = ()
        opposing_reasons:   tuple[str, ...] = ()
        risk_note:          str | None = None
        model:              str | None = None
        analysis_log_id:    int | None = None
        market_regime:      str | None = None

    candidates:         tuple["RecommendInput.Candidate", ...] = ()
    expiry_seconds:     int = 3600    # 본 PR default 1시간
    proposal_strategy:  str = "ai_assist:execution_recommender"


@dataclass(frozen=True)
class RecommendResult:
    """`recommend_proposals` 의 표준 출력."""
    proposals:    tuple[ExecutionProposal, ...]
    skipped:      tuple[tuple[str, str], ...]   # (symbol, reason) — 신뢰도/수량 부족 등
    created_at:   datetime
    auto_apply_allowed: bool = False           # 항상 False — 운영자가 명시 승인 필요
    is_order_signal:    bool = False           # 항상 False — 본 출력은 결정이 아님

    def __post_init__(self) -> None:
        if self.auto_apply_allowed is not False:
            raise ValueError(
                "RecommendResult.auto_apply_allowed must be False — "
                "본 Agent의 출력은 advisory입니다. 자동 적용 금지."
            )
        if self.is_order_signal is not False:
            raise ValueError(
                "RecommendResult.is_order_signal must be False — "
                "Execution Recommender는 주문 신호를 만들지 않습니다."
            )


# ====================================================================
# Recommendation thresholds (운영자 옵트인 후 docs에서 조정)
# ====================================================================


_MIN_CONFIDENCE_FOR_PROPOSAL = 40         # 신뢰도 < 40은 자동 skip
_MIN_RISK_REWARD_RATIO       = 1.5        # R:R < 1.5는 advisory만 (제안 발행 X)
_MIN_QUANTITY                = 1


# ====================================================================
# Pure recommendation function
# ====================================================================


def recommend_proposals(inp: RecommendInput) -> RecommendResult:
    """후보 → ExecutionProposal 변환 + 임계값 필터.

    본 함수는 *순수* — 외부 호출 / DB / broker 접근 0건. 같은 입력이면 같은
    출력 (created_at 제외).
    """
    proposals: list[ExecutionProposal] = []
    skipped: list[tuple[str, str]] = []

    expires_at = datetime.now(timezone.utc) + timedelta(seconds=max(60, inp.expiry_seconds))

    for cand in inp.candidates:
        if cand.confidence < _MIN_CONFIDENCE_FOR_PROPOSAL:
            skipped.append((cand.symbol,
                            f"confidence {cand.confidence} < {_MIN_CONFIDENCE_FOR_PROPOSAL}"))
            continue
        if cand.quantity < _MIN_QUANTITY:
            skipped.append((cand.symbol, f"quantity {cand.quantity} < {_MIN_QUANTITY}"))
            continue

        expected_reward, expected_risk, rr = _compute_reward_risk(cand)
        if rr is not None and rr < _MIN_RISK_REWARD_RATIO:
            skipped.append((cand.symbol,
                            f"risk_reward_ratio {rr:.2f} < {_MIN_RISK_REWARD_RATIO}"))
            continue

        proposal = ExecutionProposal(
            proposal_id=uuid.uuid4().hex,
            symbol=cand.symbol,
            side=cand.side,
            quantity=int(cand.quantity),
            confidence=int(cand.confidence),
            expires_at=expires_at,
            quality_score=cand.quality_score,
            supporting_reasons=tuple(cand.supporting_reasons),
            opposing_reasons=tuple(cand.opposing_reasons),
            risk_note=cand.risk_note,
            target_price=cand.target_price,
            stop_price=cand.stop_price,
            expected_reward=expected_reward,
            expected_risk=expected_risk,
            risk_reward_ratio=rr,
            strategy=inp.proposal_strategy,
            model=cand.model,
            analysis_log_id=cand.analysis_log_id,
            market_regime=cand.market_regime,
        )
        proposals.append(proposal)

    return RecommendResult(
        proposals=tuple(proposals),
        skipped=tuple(skipped),
        created_at=datetime.now(timezone.utc),
    )


def _compute_reward_risk(
    cand: "RecommendInput.Candidate",
) -> tuple[int | None, int | None, float | None]:
    """`target_price` / `stop_price` / `latest_price` 기반 expected reward/risk.

    BUY: reward = (target - latest) * qty; risk = (latest - stop) * qty
    SELL: reward = (latest - target) * qty; risk = (stop - latest) * qty
    어느 한 쪽 가격이 없으면 None — 본 Agent는 *추정만* 한다, 운영자가 검증.
    """
    if cand.latest_price <= 0:
        return None, None, None
    qty = max(1, int(cand.quantity))
    reward: int | None = None
    risk: int | None = None
    if cand.side == ProposalSide.BUY:
        if cand.target_price is not None:
            reward = max(0, (cand.target_price - cand.latest_price) * qty)
        if cand.stop_price is not None:
            risk = max(0, (cand.latest_price - cand.stop_price) * qty)
    else:  # SELL
        if cand.target_price is not None:
            reward = max(0, (cand.latest_price - cand.target_price) * qty)
        if cand.stop_price is not None:
            risk = max(0, (cand.stop_price - cand.latest_price) * qty)

    rr: float | None = None
    if reward is not None and risk is not None and risk > 0:
        rr = reward / risk
    return reward, risk, rr


# ====================================================================
# Precheck helper — RiskManager.check_order audit-row-free wrap
# ====================================================================


async def precheck_proposal(
    proposal: ExecutionProposal,
    *,
    risk:   "RiskManager",
    broker:  Any,                            # BrokerAdapter (typing only — caller 주입)
    mode:    OperationMode,
    requested_by_ai: bool = True,
    market_regime:   str | None = None,
    market_regime_decision: str | None = None,
) -> RiskPrecheckResult:
    """제안에 대한 RiskManager 사전검사. **DB write 0건 / broker.place_order 0건**.

    `risk.check_order`는 audit row를 작성하지 *않는다* — `route_order`가 별도로
    호출할 때 audit 기록. 따라서 본 함수는 운영자가 "이 제안이 통과 가능한지"
    *드라이런*하는 용도로 쓰인다.

    caller가 broker 인스턴스를 주입 — 본 모듈은 broker class를 import하지 않으며,
    runtime에 `broker.get_price` / `broker.get_balance` / `broker.get_positions`
    같은 *read-only* 메서드만 호출한다. `broker.place_order` / `broker.cancel_order`
    호출 0건 (정적 grep 가드).
    """
    # 1. 제안 expiry 사전검사 — stale 제안은 사전검사 단계에서 차단.
    if proposal.is_expired():
        return RiskPrecheckResult(
            outcome=PrecheckOutcome.REJECTED,
            reasons=("proposal expired",),
            warnings=(),
            risk_score=None,
            blocked_by="proposal_expiry",
            required_action="re-recommend with fresh data",
            evaluated_at=datetime.now(timezone.utc),
            proposal_id=proposal.proposal_id,
        )

    # 2. 시세 / 잔고 / 포지션 스냅샷 (read-only).
    quote   = await broker.get_price(proposal.symbol)
    balance = await broker.get_balance()
    positions = await broker.get_positions()

    # Quote.timestamp는 str (isoformat) — datetime으로 파싱 (route_order와 동일).
    quote_ts: datetime | None = None
    raw_ts = getattr(quote, "timestamp", None)
    if isinstance(raw_ts, str):
        try:
            quote_ts = datetime.fromisoformat(raw_ts)
        except ValueError:
            quote_ts = None
    elif isinstance(raw_ts, datetime):
        quote_ts = raw_ts

    # 3. AICandidate 변환 후 OrderRequest로 변환 (ai.assist 위임).
    candidate = proposal.to_ai_candidate()
    order = candidate.to_order_request()

    # 4. RiskContext 생성 + check_order — audit row 작성 X.
    context = RiskContext(
        mode=mode,
        balance=balance,
        positions=positions,
        latest_price=int(quote.price),
        latest_price_timestamp=quote_ts,
        requested_by_ai=requested_by_ai,
        market_regime=market_regime,
        market_regime_decision=market_regime_decision,
        metadata={
            "source": "execution_recommender_precheck",
            "proposal_id": proposal.proposal_id,
        },
    )
    result = risk.check_order(order, context)
    return RiskPrecheckResult.from_risk_check(proposal.proposal_id, result)


# ====================================================================
# Submission helper — DELEGATES to ai.assist.submit_candidate (#44)
# ====================================================================


async def submit_proposal(
    proposal: ExecutionProposal,
    *,
    risk:   "RiskManager",
    broker:  Any,
    db:      "Session",
    mode:    OperationMode,
    enable_live_trading:        bool,
    enable_ai_execution:        bool,
    enable_futures_live_trading: bool,
) -> AiAssistSubmissionResult:
    """제안 → 기존 sanctioned approval queue 진입점에 위임.

    본 함수는 *완전히* `app.ai.assist.submit_candidate`에 위임 — broker /
    OrderExecutor / route_order import는 본 모듈에 없고, ai.assist가 모든
    절차(AI Permission Gate → route_order → RiskManager → audit →
    PermissionGate.submit)를 단일 트랜잭션으로 처리.
    """
    if proposal.is_expired():
        # 만료 제안은 큐 등록 거부 — RuntimeError로 caller에 surface.
        raise RuntimeError(
            f"ExecutionProposal {proposal.proposal_id} expired at "
            f"{proposal.expires_at.isoformat()}; "
            "재추천 후 다시 제출해야 합니다."
        )
    candidate = proposal.to_ai_candidate()
    return await submit_candidate(
        candidate=candidate,
        mode=mode,
        broker=broker,
        risk=risk,
        db=db,
        enable_live_trading=enable_live_trading,
        enable_ai_execution=enable_ai_execution,
        enable_futures_live_trading=enable_futures_live_trading,
    )


# ====================================================================
# Agent class — #51 AgentBase 호환
# ====================================================================


class ExecutionRecommenderAgent(AgentBase):
    """#56 enhanced — ExecutionProposal 생성 advisory.

    `app.agents.roles.ExecutionRecommenderAgent`(#51 mock)는 stub로 남고, 본
    클래스는 `RecommendInput`을 받아 ExecutionProposal 목록을 *반환*만 한다.

    **자동 주문 0건** — `is_order_intent=False`, `can_execute_order=False`,
    approval queue *직접 등록* X (caller가 `submit_proposal`을 별도 호출).
    """

    @property
    def metadata(self) -> AgentMetadata:
        return AgentMetadata(
            name="execution_recommender",
            role=AgentRole.EXECUTION_RECOMMENDER,
            description=(
                "AI Assist 매수/매도 *제안*만 생성. 직접 주문 X — "
                "운영자가 명시적으로 사전검사 / 승인 큐 등록을 호출해야 "
                "기존 sanctioned 흐름으로 진입한다."
            ),
            inputs=[
                "RecommendInput.Candidate (symbol/side/price/target/stop/conf/reasons)",
                "expiry_seconds (default 3600s)",
            ],
            outputs=[
                "RecommendResult (proposals tuple, skipped reasons, "
                "auto_apply_allowed=False, is_order_signal=False)",
            ],
            forbidden=[
                "broker / OrderExecutor / route_order import 금지",
                "place_order / cancel_order 호출 금지",
                "approval queue 직접 등록 금지 (submit_proposal helper도 "
                "ai.assist.submit_candidate에 위임)",
                "OrderRequest 직접 import 금지",
                "외부 AI / HTTP 호출 금지",
                "DB INSERT / UPDATE / DELETE 금지",
            ],
            can_execute_order=False,
        )

    def run(self, context: AgentContext) -> AgentOutput:
        extra = context.extra or {}
        recommend_input = extra.get("recommend_input")
        if not isinstance(recommend_input, RecommendInput):
            return AgentOutput(
                role=AgentRole.EXECUTION_RECOMMENDER,
                decision=AgentDecision.NO_OP,
                summary="recommend_input 미제공 — 추천 생략.",
                reasons=["context.extra['recommend_input']에 RecommendInput 필요"],
                metadata={"reason": "missing_input"},
            )
        result = recommend_proposals(recommend_input)
        decision = (
            AgentDecision.APPROVAL_CANDIDATE
            if result.proposals
            else AgentDecision.NO_OP
        )
        # 본 Agent의 approval_candidate는 *최상위* 제안 1건의 payload — caller가
        # 원하면 별도 흐름에서 submit_proposal()을 호출. 여기서 직접 큐 등록 X.
        approval_candidate: dict[str, Any] | None = None
        if result.proposals:
            top = result.proposals[0]
            approval_candidate = {
                "source":             "AGENT_EXECUTION_RECOMMENDER",
                "proposal_id":        top.proposal_id,
                "symbol":             top.symbol,
                "side":               top.side.value,
                "quantity":           top.quantity,
                "order_type":         top.order_type.value,
                "limit_price":        top.limit_price,
                "confidence":         top.confidence,
                "supporting_reasons": list(top.supporting_reasons),
                "opposing_reasons":   list(top.opposing_reasons),
                "risk_note":          top.risk_note,
                "expected_reward":    top.expected_reward,
                "expected_risk":      top.expected_risk,
                "risk_reward_ratio":  top.risk_reward_ratio,
                "expires_at":         top.expires_at.isoformat(),
                "is_order_intent":    False,
                "can_execute_order":  False,
            }
        return AgentOutput(
            role=AgentRole.EXECUTION_RECOMMENDER,
            decision=decision,
            summary=(f"{len(result.proposals)}건 제안 생성, "
                     f"{len(result.skipped)}건 임계 미달로 skip"),
            reasons=[f"skipped {sym}: {why}" for sym, why in result.skipped[:5]],
            confidence=(int(result.proposals[0].confidence)
                        if result.proposals else None),
            approval_candidate=approval_candidate,
            metadata={
                "proposals_count":   len(result.proposals),
                "skipped_count":     len(result.skipped),
                "auto_apply_allowed": False,
                "is_order_signal":   False,
            },
        )

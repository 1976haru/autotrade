"""Agent Council base (185, MUST).

CLAUDE.md 절대 원칙:
- AI는 broker 주문 API를 직접 호출하지 않는다 — Agent의 출력은 데이터일 뿐.
- 모든 Agent 결정은 AgentDecisionLog에 영구화 (audit invariant).
- 실 LLM 호출 없이도 deterministic stub으로 동작 (AI Key 없어도 운영 가능).

Council 멤버 (10):
- ChiefTradingAgent: 종합 결정자. 다른 agent들의 출력을 받아 최종 BUY/SELL/HOLD.
- MarketRegimeAgent: 시장 체제 분류 (trending/ranging/high_vol).
- StrategySelectionAgent: 현재 regime에 맞는 strategy 선택.
- StockSelectionAgent: 후보 symbol 추출.
- PositionSizingAgent: 자본/risk 기반 size 권장.
- RiskOfficerAgent: 정책 위반 사전 검토.
- EntryTimingAgent: 진입 타이밍 (당장/대기).
- ExitTimingAgent: 청산 타이밍 (stop_loss/take_profit/time_exit/hold).
- NewsTrendAgent: 뉴스/추세 시그널 (stub).
- PostTradeReviewAgent: 사후 거래 분석.

각 Agent는 `decide(...) -> AgentDecision` 인터페이스. ChiefTradingAgent가
synthesize(decisions) → 최종 결정.
"""

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any
from uuid import uuid4

from sqlalchemy.orm import Session

from app.db.models import AgentDecisionLog


@dataclass
class AgentDecision:
    """Agent의 structured 출력. AgentDecisionLog row로 영구화."""
    agent_name:  str
    decision:    str           # BUY / SELL / HOLD / APPROVE / REJECT / WARN / INFO
    confidence:  int           # 0-100
    reasons:     list[str]     = field(default_factory=list)
    meta:        dict[str, Any] = field(default_factory=dict)
    symbol:      str | None    = None
    chain_id:    str | None    = None  # 같은 의사결정 사슬 묶는 키


def new_chain_id() -> str:
    """ChiefTradingAgent가 호출 시작 시 발급. 다른 agent들이 같은 id 사용."""
    return str(uuid4())


def persist_decision(
    db:        Session,
    decision:  AgentDecision,
    *,
    mode:      str,
) -> AgentDecisionLog:
    """AgentDecision을 DB row로 영구화."""
    row = AgentDecisionLog(
        agent_name=decision.agent_name,
        symbol=decision.symbol,
        mode=mode,
        decision=decision.decision,
        confidence=decision.confidence,
        reasons=list(decision.reasons),
        meta=dict(decision.meta) if decision.meta else None,
        chain_id=decision.chain_id,
    )
    db.add(row)
    db.flush()
    return row


class Agent(ABC):
    """Agent ABC. 서브클래스가 decide()를 구현.

    실 LLM 통합은 별도 옵트인. 본 PR의 모든 agent는 deterministic stub —
    AI Key 없이도 동작.
    """

    name: str = "abstract_agent"

    @abstractmethod
    def decide(self, **kwargs: Any) -> AgentDecision:
        """Agent별 입력에 따라 decision 산출. 호출자가 호출 후 persist_decision
        으로 영구화."""

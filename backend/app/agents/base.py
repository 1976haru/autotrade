"""#51: Agent architecture base classes.

본 모듈은 6개 역할 에이전트의 공식 contract를 정의한다 — Observer, Analyst,
Risk Auditor, Strategy Researcher, Report Writer, Execution Recommender.
주식 / 선물 주문 흐름과 *완전히 분리*된 *분석/추천/리포트 전용* 계층.

## 핵심 원칙 (CLAUDE.md 절대 원칙 1, 2 상속)

1. **Agent는 broker를 모른다.** `BrokerAdapter` / `KisBrokerAdapter` /
   `MockBroker` 어떤 클래스도 import할 수 없다 (정적 grep 가드).
2. **Agent는 OrderExecutor를 호출하지 않는다.** `route_order` /
   `OrderExecutor.execute` 어떤 호출도 발생하지 않는다.
3. **Agent는 분석/추천/리포트만 한다.** 모든 출력은 `AgentOutput` —
   주문 객체가 아닌 *advisory* dataclass.
4. **Execution Recommender도 직접 주문하지 않는다.** approval queue
   *후보 payload*만 생성 가능 — `can_execute_order = False` 불변.
5. **`is_order_intent = False` 불변** — `AgentOutput` 자체 가드 (True 시
   ValueError).

## 역할별 contract 요약

| 역할 | 출력 decision | can_execute_order | 권한 |
|---|---|---|---|
| Observer | `OBSERVE` | False | 시장/데이터 관찰만 |
| Analyst | `ANALYZE` | False | 후보 분석만 |
| Risk Auditor | `WARN` 또는 `REJECT` | False | 한도/duplicate/stale 점검 |
| Strategy Researcher | `REPORT` 또는 `RECOMMEND` | False | 백테스트/개선안 제안 |
| Report Writer | `REPORT` | False | 일/주간 리포트 |
| Execution Recommender | `APPROVAL_CANDIDATE` | False | 승인 큐 후보만 (직접 주문 X) |

자세한 정책: [`docs/agent_architecture.md`](../../../docs/agent_architecture.md).
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import StrEnum
from typing import Any


# ====================================================================
# Enums
# ====================================================================


class AgentRole(StrEnum):
    """6개 표준 역할. 새 역할은 별도 옵트인 PR로 추가."""
    OBSERVER              = "OBSERVER"
    ANALYST               = "ANALYST"
    RISK_AUDITOR          = "RISK_AUDITOR"
    STRATEGY_RESEARCHER   = "STRATEGY_RESEARCHER"
    REPORT_WRITER         = "REPORT_WRITER"
    EXECUTION_RECOMMENDER = "EXECUTION_RECOMMENDER"


class AgentDecision(StrEnum):
    """Agent가 반환할 수 있는 decision 라벨.

    어떤 값도 broker 주문을 의미하지 *않는다*. `APPROVAL_CANDIDATE`도
    승인 큐 후보 payload — 운영자 승인 + RiskManager + PermissionGate +
    OrderExecutor를 거쳐야만 실제 주문이 broker로 진행된다.
    """
    OBSERVE             = "OBSERVE"
    ANALYZE             = "ANALYZE"
    WARN                = "WARN"
    REJECT              = "REJECT"
    REPORT              = "REPORT"
    RECOMMEND           = "RECOMMEND"
    APPROVAL_CANDIDATE  = "APPROVAL_CANDIDATE"
    NO_OP               = "NO_OP"


# ====================================================================
# Output dataclass
# ====================================================================


@dataclass(frozen=True)
class AgentOutput:
    """모든 Agent의 표준 출력. 주문 객체가 아닌 *advisory* dataclass.

    절대 invariant:
    - `is_order_intent = False` — True 시 `__post_init__`에서 ValueError.
      Agent는 *추천*만 한다. 실제 주문은 RiskManager + PermissionGate +
      OrderExecutor 흐름에서만 만들어진다.
    - `can_execute_order = False` — Agent가 broker 호출 권한이 *있다*는
      의미가 아닌, "본 출력이 주문 실행 단계 *직전*까지의 후보인지" 표시.
      ExecutionRecommender도 본 필드를 False로 유지 — approval queue 후보
      *payload*만 생성하며, 큐에 넣는 것은 별도 caller 책임.

    필드:
    - `role`: 출력 생성한 Agent 역할
    - `decision`: 카테고리 라벨
    - `summary`: 사람이 읽을 한 줄
    - `reasons`: 사유 리스트
    - `confidence`: 0~100 (선택)
    - `risk_flags`: WARN/REJECT 사유 키 (선택, 예: "stale_data", "duplicate")
    - `approval_candidate`: ExecutionRecommender가 채우는 후보 payload
      (symbol, side, quantity, ai_decision_meta 등). 다른 역할은 None.
    - `metadata`: 자유 dict — agent별 raw 결과 carry
    - `is_order_intent`: 항상 False (가드)
    - `can_execute_order`: 항상 False (가드)
    - `created_at`: UTC 시각
    """

    role:               AgentRole
    decision:           AgentDecision
    summary:            str
    reasons:            list[str]    = field(default_factory=list)
    confidence:         int | None   = None
    risk_flags:         list[str]    = field(default_factory=list)
    approval_candidate: dict | None  = None
    metadata:           dict         = field(default_factory=dict)
    is_order_intent:    bool         = False
    can_execute_order:  bool         = False
    created_at:         datetime     = field(
        default_factory=lambda: datetime.now(timezone.utc)
    )

    def __post_init__(self) -> None:
        # invariant: Agent는 주문 의도를 만들 수 없다.
        if self.is_order_intent:
            raise ValueError(
                "AgentOutput.is_order_intent must be False — agents are "
                "advisory only (CLAUDE.md 절대 원칙 1, 2). Real orders go "
                "through RiskManager + PermissionGate + OrderExecutor."
            )
        # invariant: Agent는 broker 실행 권한이 없다.
        if self.can_execute_order:
            raise ValueError(
                "AgentOutput.can_execute_order must be False — even "
                "ExecutionRecommender produces approval queue candidate "
                "payloads only, not direct orders."
            )
        if self.confidence is not None and not (0 <= self.confidence <= 100):
            raise ValueError(
                f"confidence must be in [0, 100], got {self.confidence}"
            )

    def to_dict(self) -> dict[str, Any]:
        return {
            "role":               self.role.value,
            "decision":           self.decision.value,
            "summary":            self.summary,
            "reasons":            list(self.reasons),
            "confidence":         self.confidence,
            "risk_flags":         list(self.risk_flags),
            "approval_candidate": (
                dict(self.approval_candidate)
                if self.approval_candidate is not None else None
            ),
            "metadata":           dict(self.metadata),
            "is_order_intent":    self.is_order_intent,
            "can_execute_order":  self.can_execute_order,
            "created_at":         self.created_at.isoformat(),
        }


# ====================================================================
# Context input
# ====================================================================


@dataclass(frozen=True)
class AgentContext:
    """Agent가 받는 표준 입력 컨텍스트. caller(예: operating_loop)가 채워서 전달.

    어떤 Agent도 본 dataclass에서 broker 인스턴스 / API key / Secret을
    받지 않는다 — 본 모듈의 정적 가드는 그런 필드의 *존재 자체*를 차단한다.
    """

    operator_intent:    str | None  = None    # 운영자 의도 (예: "intraday")
    market_state:       dict | None = None    # MarketRegime classifier 결과 등
    watchlist:          list[str] | None = None
    recent_signals:     list[dict] | None = None
    audit_summary:      dict | None = None    # OrderAuditLog 요약 (count / decisions)
    risk_state:         dict | None = None    # RiskManager state snapshot
    extra:              dict | None = None    # 자유 carry


# ====================================================================
# Agent ABC
# ====================================================================


@dataclass(frozen=True)
class AgentMetadata:
    """Agent 자기소개 — registry / API에서 사용."""
    name:           str
    role:           AgentRole
    description:    str
    inputs:         list[str]   = field(default_factory=list)
    outputs:        list[str]   = field(default_factory=list)
    forbidden:      list[str]   = field(default_factory=list)
    can_execute_order: bool     = False  # 본 PR 시점 모든 agent False

    def to_dict(self) -> dict[str, Any]:
        return {
            "name":              self.name,
            "role":              self.role.value,
            "description":       self.description,
            "inputs":            list(self.inputs),
            "outputs":           list(self.outputs),
            "forbidden":         list(self.forbidden),
            "can_execute_order": self.can_execute_order,
        }


class AgentBase(ABC):
    """공식 Agent ABC.

    구현체는 두 메서드를 채운다:
    - `metadata` (property) — 자기소개
    - `run(context)` — `AgentOutput` 반환

    절대 invariant:
    - 본 ABC를 상속한 어떤 클래스도 broker / OrderExecutor / route_order
      를 호출해서는 안 된다 (정적 grep 가드).
    - `run`이 반환하는 `AgentOutput`은 `is_order_intent = False` +
      `can_execute_order = False` (dataclass 자체 가드).
    """

    @property
    @abstractmethod
    def metadata(self) -> AgentMetadata:
        raise NotImplementedError

    @abstractmethod
    def run(self, context: AgentContext) -> AgentOutput:
        raise NotImplementedError


# ====================================================================
# Module invariants
# ====================================================================
#
# - 본 모듈은 broker / OrderExecutor / route_order / KIS / mock_broker /
#   permission.gate 어떤 모듈도 import하지 않는다 (정적 grep 가드).
# - `AgentOutput`은 `is_order_intent` / `can_execute_order` 둘 다 False
#   불변 — `__post_init__` 가드.
# - `AgentContext`에 broker 인스턴스 / API key 필드 0개.
#
# 위 invariant는 `tests/test_agents_architecture.py`로 강제.

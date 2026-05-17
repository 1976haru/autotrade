"""#4-01: Strategy Optimizer Agent — 전략 최적화 결과를 AI Agent 표준 입력으로 변환.

3-02 (real data backtest) / 3-03 (parameter optimization) / 3-04 (walk-forward)
/ 3-05 (stress test) / 3-06 (성과 지표 표준화) / 3-07 (paper 후보 통합) / 3-08
(운영자 리포트) 의 산출물을 *AI Agent 가 읽을 수 있는 표준 입력 구조* 로 변환.

## 핵심 목적

AI Agent (Anthropic / OpenAI LLM 계열) 가 본 입력을 받아 *전략 추천 / 제외
사유를 자연어로 설명* 할 수 있게 한다.

## 절대 invariant (CLAUDE.md 절대 원칙 1~5 상속)

1. **본 입력은 *주문 신호가 아니다*** — `is_order_signal=False` 불변.
2. **자동 적용 0건** — `auto_apply_allowed=False` 불변.
3. **실거래 허가 0건** — `is_live_authorization=False` 불변.
4. **broker / OrderExecutor / route_order 호출 0건** — 정적 grep 가드.
5. **외부 HTTP / AI SDK 호출 0건** — 본 모듈은 *입력 데이터 정규화*만 수행.
   실제 LLM 호출은 별도 모듈에서.
6. **secret / API key / 계좌번호 carry 0건** — 본 schema 에는 그런 필드 자체가
   없다 (테스트로 lock).
7. **DB write 0건** — read-only.

## 출력 schema

`StrategyAgentInput` (top-level):
- `generated_at` / `schema_version` / `items` / `overall_status` /
  `reasons_no_candidate` / `advisory_disclaimer` / `metadata` /
  `is_order_signal=False` / `auto_apply_allowed=False` / `is_live_authorization=False`

`StrategyAgentInputItem` (per-strategy, 14 필수 필드):
- `strategy` / `symbol` / `params` / `backtest_metrics` /
  `optimization_metrics` / `walk_forward_verdict` / `stress_test_verdict` /
  `paper_candidate_status` / `risk_flags` / `exclusion_reasons` /
  `recommendation_context` / `is_order_signal=False` /
  `auto_apply_allowed=False` / `is_live_authorization=False`
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

from app.agents.base import (
    AgentBase,
    AgentContext,
    AgentDecision,
    AgentMetadata,
    AgentOutput,
    AgentRole,
)
from app.analytics.paper_candidate_aggregator import PipelineStage
from app.analytics.strategy_optimization_report import (
    OperatorReport,
    ReportInputs,
    ReportStatus,
    StrategyEntry,
    build_operator_report,
)


SCHEMA_VERSION = "1.0"


_ADVISORY_DISCLAIMER = (
    "본 입력은 AI Agent 가 *전략 추천 / 제외 사유를 설명*하기 위한 read-only "
    "데이터입니다. 본 입력으로 직접 주문(BUY/SELL/HOLD)을 생성하면 안 됩니다. "
    "실거래 활성화는 별도 옵트인 절차 (Paper Gate / Live Manual Gate / "
    "사용자 명시 승인) 가 필요합니다. is_order_signal=False / "
    "auto_apply_allowed=False / is_live_authorization=False."
)


# ─────────────────────────────────────────────────────────────────────────────
# 1. Per-strategy item schema (14 필수 필드 + 3 invariant)
# ─────────────────────────────────────────────────────────────────────────────


@dataclass(frozen=True)
class StrategyAgentInputItem:
    """단일 (strategy, symbol, params) 의 AI Agent 표준 입력 단위.

    AI Agent 가 본 항목을 받아 자연어로 다음을 *설명* 할 수 있다:
    - 이 전략이 왜 Paper 후보로 선정됐는가 (또는 왜 제외됐는가)
    - 어느 단계에서 탈락했는가
    - 운영자가 다음에 무엇을 검토해야 하는가

    AI Agent 는 본 항목으로 *직접 주문을 생성하지 않는다* — `is_order_signal=False`
    불변, `__post_init__` ValueError 가드.
    """

    strategy:                str
    symbol:                  str
    params:                  dict[str, Any]            = field(default_factory=dict)
    backtest_metrics:        dict[str, Any]            = field(default_factory=dict)
    optimization_metrics:    dict[str, Any]            = field(default_factory=dict)
    walk_forward_verdict:    str | None                = None
    stress_test_verdict:     str | None                = None
    paper_candidate_status:  str                       = ReportStatus.NO_CANDIDATE.value
    risk_flags:              list[str]                 = field(default_factory=list)
    exclusion_reasons:       list[str]                 = field(default_factory=list)
    recommendation_context:  dict[str, Any]            = field(default_factory=dict)

    # 절대 invariant — caller 변경 불가.
    is_order_signal:         bool = False
    auto_apply_allowed:      bool = False
    is_live_authorization:   bool = False

    def __post_init__(self) -> None:
        if self.is_order_signal is not False:
            raise ValueError(
                "StrategyAgentInputItem.is_order_signal must be False — "
                "본 입력은 advisory 전용이며 주문 신호가 아닙니다."
            )
        if self.auto_apply_allowed is not False:
            raise ValueError(
                "StrategyAgentInputItem.auto_apply_allowed must be False — "
                "자동 적용 금지."
            )
        if self.is_live_authorization is not False:
            raise ValueError(
                "StrategyAgentInputItem.is_live_authorization must be False — "
                "실거래 활성화 권한 없음."
            )
        if not isinstance(self.paper_candidate_status, str):
            raise ValueError("paper_candidate_status must be a string label.")

    def to_dict(self) -> dict[str, Any]:
        return {
            "strategy":                self.strategy,
            "symbol":                  self.symbol,
            "params":                  dict(self.params),
            "backtest_metrics":        dict(self.backtest_metrics),
            "optimization_metrics":    dict(self.optimization_metrics),
            "walk_forward_verdict":    self.walk_forward_verdict,
            "stress_test_verdict":     self.stress_test_verdict,
            "paper_candidate_status":  self.paper_candidate_status,
            "risk_flags":              list(self.risk_flags),
            "exclusion_reasons":       list(self.exclusion_reasons),
            "recommendation_context":  dict(self.recommendation_context),
            # invariant 명시 carry — JSON consumer 측에서도 안전.
            "is_order_signal":         False,
            "auto_apply_allowed":      False,
            "is_live_authorization":   False,
        }


# ─────────────────────────────────────────────────────────────────────────────
# 2. Top-level wrapper schema
# ─────────────────────────────────────────────────────────────────────────────


@dataclass(frozen=True)
class StrategyAgentInput:
    """AI Agent 가 받는 표준 입력 — N 개 item + pipeline metadata + disclaimer."""

    generated_at:           str
    schema_version:         str
    overall_status:         str
    items:                  list[StrategyAgentInputItem] = field(default_factory=list)
    reasons_no_candidate:   list[str]                    = field(default_factory=list)
    advisory_disclaimer:    str                          = _ADVISORY_DISCLAIMER
    metadata:               dict[str, Any]               = field(default_factory=dict)

    # 절대 invariant — top-level.
    is_order_signal:        bool = False
    auto_apply_allowed:     bool = False
    is_live_authorization:  bool = False

    def __post_init__(self) -> None:
        if self.is_order_signal is not False:
            raise ValueError(
                "StrategyAgentInput.is_order_signal must be False."
            )
        if self.auto_apply_allowed is not False:
            raise ValueError(
                "StrategyAgentInput.auto_apply_allowed must be False."
            )
        if self.is_live_authorization is not False:
            raise ValueError(
                "StrategyAgentInput.is_live_authorization must be False."
            )
        if not isinstance(self.advisory_disclaimer, str) or not self.advisory_disclaimer:
            raise ValueError("advisory_disclaimer must be a non-empty string.")

    @property
    def item_count(self) -> int:
        return len(self.items)

    def to_dict(self) -> dict[str, Any]:
        return {
            "generated_at":           self.generated_at,
            "schema_version":         self.schema_version,
            "overall_status":         self.overall_status,
            "item_count":             self.item_count,
            "items":                  [it.to_dict() for it in self.items],
            "reasons_no_candidate":   list(self.reasons_no_candidate),
            "advisory_disclaimer":    self.advisory_disclaimer,
            "metadata":               dict(self.metadata),
            # 최상위 invariant — JSON consumer 측에서도 안전.
            "is_order_signal":        False,
            "auto_apply_allowed":     False,
            "is_live_authorization":  False,
        }


# ─────────────────────────────────────────────────────────────────────────────
# 3. Verdict / metrics 추출 helper
# ─────────────────────────────────────────────────────────────────────────────


def _stage_verdict(stages: list[PipelineStage], name: str) -> str | None:
    for s in stages:
        if s.name == name:
            return s.verdict
    return None


def _stage_metrics(stages: list[PipelineStage], name: str) -> dict[str, Any]:
    """단일 stage 의 metrics carry — extra.metrics dict 우선."""
    for s in stages:
        if s.name == name:
            m = s.extra.get("metrics")
            if isinstance(m, dict):
                return dict(m)
    return {}


def _build_recommendation_context(entry: StrategyEntry) -> dict[str, Any]:
    """AI Agent 가 자연어 추천을 생성할 때 *참고* 할 안전 컨텍스트.

    - 점수 / 통과 단계 / 상태 라벨 / 핵심 메트릭 carry.
    - secret / 계좌번호 / API key 필드 0건.
    """
    m = entry.risk_metrics
    return {
        "score":                 float(entry.score),
        "passed_stages":         entry.passed_stages(),
        "status_label_ko":       _STATUS_LABEL_KO.get(entry.status, entry.status.value),
        "headline_metrics": {
            "profit_factor":     m.get("profit_factor"),
            "max_drawdown":      m.get("max_drawdown"),
            "expectancy":        m.get("expectancy"),
            "win_rate":          m.get("win_rate"),
            "trade_count":       m.get("trade_count"),
            "loss_streak":       m.get("loss_streak"),
            "fee_adjusted_return":      m.get("fee_adjusted_return"),
            "slippage_adjusted_return": m.get("slippage_adjusted_return"),
        },
        "display_name":          entry.display_name,
    }


# 비개발자용 한국어 라벨 — strategy_optimization_report 에서 carry.
_STATUS_LABEL_KO: dict[ReportStatus, str] = {
    ReportStatus.READY_FOR_PAPER:  "모의투자(Paper)에서 시작 검토 가능",
    ReportStatus.NEED_MORE_DATA:   "데이터 부족 — 더 모은 뒤 재평가",
    ReportStatus.REJECTED_BY_RISK: "위험 한도 위반 — 아직 사용 안 됨",
    ReportStatus.OVERFIT_RISK:     "과최적화 의심 — 아직 사용 안 됨",
    ReportStatus.STRESS_FAILED:    "스트레스 테스트 불합격 — 아직 사용 안 됨",
    ReportStatus.NO_CANDIDATE:     "현재 후보 자격 없음",
}


# ─────────────────────────────────────────────────────────────────────────────
# 4. Builder — OperatorReport 또는 raw paths → StrategyAgentInput
# ─────────────────────────────────────────────────────────────────────────────


def build_strategy_agent_input(
    *,
    operator_report:    OperatorReport | None = None,
    inputs:             ReportInputs | None   = None,
    metadata:           dict[str, Any] | None = None,
    now:                datetime | None       = None,
) -> StrategyAgentInput:
    """5 단계 산출물 또는 기존 OperatorReport (3-08) → 표준 Agent 입력.

    Args:
        operator_report: 3-08 OperatorReport 인스턴스. None 이면 ``inputs`` 로 빌드.
        inputs:           paper_candidate_config + 3-02/3-03/3-04/3-05 경로.
                          ``operator_report`` 가 주어지면 무시.
        metadata:         agent 입력에 carry 할 자유 metadata (예: pipeline 이름).
        now:              테스트용 datetime 주입.

    Returns:
        StrategyAgentInput — 모든 (strategy, symbol, params) 조합의 표준 입력.

    *주의*: 본 함수는 broker / OrderExecutor / route_order / 외부 HTTP / AI SDK
    호출 0건. 운영자가 LLM 에 전달하기 *전에* 본 schema 를 통과시켜 입력을
    정규화한다.
    """
    if operator_report is None:
        operator_report = build_operator_report(
            inputs or ReportInputs(), metadata=metadata, now=now,
        )
    if now is None:
        now = datetime.now(timezone.utc)

    items: list[StrategyAgentInputItem] = []
    for entry in operator_report.entries:
        bt_metrics  = _stage_metrics(entry.pipeline_stages, "3-02")
        opt_metrics = _stage_metrics(entry.pipeline_stages, "3-03")
        wf_verdict  = _stage_verdict(entry.pipeline_stages, "3-04")
        st_verdict  = _stage_verdict(entry.pipeline_stages, "3-05")

        # 3-02 metrics 가 비어있고 3-03 만 있는 경우 → 3-03 metrics 를 backtest_metrics 로 fallback.
        if not bt_metrics and opt_metrics:
            bt_metrics = dict(opt_metrics)
        # 둘 다 비어있으면 entry.risk_metrics 로 fallback (3-07 aggregator 가
        # 합쳐놓은 metrics).
        if not bt_metrics and not opt_metrics and entry.risk_metrics:
            bt_metrics = dict(entry.risk_metrics)

        items.append(StrategyAgentInputItem(
            strategy=entry.strategy_id,
            symbol=entry.symbol,
            params=dict(entry.params),
            backtest_metrics=bt_metrics,
            optimization_metrics=opt_metrics,
            walk_forward_verdict=wf_verdict,
            stress_test_verdict=st_verdict,
            paper_candidate_status=entry.status.value,
            risk_flags=list(entry.risk_signals),
            exclusion_reasons=list(entry.exclusion_reasons),
            recommendation_context=_build_recommendation_context(entry),
        ))

    return StrategyAgentInput(
        generated_at=now.isoformat(),
        schema_version=SCHEMA_VERSION,
        overall_status=operator_report.overall_status.value,
        items=items,
        reasons_no_candidate=list(operator_report.reasons_no_candidate),
        advisory_disclaimer=_ADVISORY_DISCLAIMER,
        metadata={
            "pipeline":                 "step4-01-agent-input-schema",
            "source_overall_status":    operator_report.overall_status.value,
            "source_paper_ready_count": operator_report.paper_ready_count,
            "source_excluded_count":    operator_report.excluded_count,
            **(metadata or {}),
        },
    )


# ─────────────────────────────────────────────────────────────────────────────
# 5. Agent — AgentBase 호환 (#51 architecture)
# ─────────────────────────────────────────────────────────────────────────────


_AGENT_METADATA = AgentMetadata(
    name="strategy_optimizer_agent",
    role=AgentRole.STRATEGY_RESEARCHER,
    description=(
        "Strategy optimization pipeline (3-02..3-08) 결과를 AI 가 읽을 수 있는 "
        "표준 입력으로 변환하고, 단순 요약 AgentOutput 을 반환하는 advisory "
        "agent. 본 agent 는 *주문 신호 / LLM 호출 / broker 호출* 을 수행하지 "
        "않는다 (분석 / 정규화 / 요약 전용)."
    ),
    inputs=[
        "AgentContext.extra['strategy_agent_input'] (StrategyAgentInput) 또는",
        "AgentContext.extra['operator_report'] (OperatorReport) — 둘 중 하나.",
    ],
    outputs=[
        "AgentOutput(decision=REPORT, summary, reasons, risk_flags, "
        "metadata['strategy_agent_input'])",
    ],
    forbidden=[
        "broker.place_order", "route_order", "OrderExecutor",
        "anthropic / openai / httpx / requests",
        "BUY / SELL / HOLD signal generation",
        "auto strategy promotion / parameter mutation",
    ],
    can_execute_order=False,
)


class StrategyOptimizerAgent(AgentBase):
    """Strategy Optimizer Agent — advisory 분석 / 표준 입력 빌더 wrapper.

    `run(context)` 가 받는 입력 우선순위:
    1. `context.extra["strategy_agent_input"]` (`StrategyAgentInput`)
    2. `context.extra["operator_report"]` (`OperatorReport`) — 본 agent 가 builder 호출
    3. 둘 다 없으면 빈 입력으로 NO_OP report 반환.

    출력: `AgentOutput(role=STRATEGY_RESEARCHER, decision=REPORT, ...)`.
    """

    @property
    def metadata(self) -> AgentMetadata:
        return _AGENT_METADATA

    def run(self, context: AgentContext) -> AgentOutput:
        agent_input = self._resolve_input(context)
        return self._build_output(agent_input)

    def _resolve_input(self, context: AgentContext) -> StrategyAgentInput:
        extra = context.extra or {}
        # 1) 직접 주입.
        candidate = extra.get("strategy_agent_input")
        if isinstance(candidate, StrategyAgentInput):
            return candidate
        # 2) OperatorReport 주입.
        report = extra.get("operator_report")
        if isinstance(report, OperatorReport):
            return build_strategy_agent_input(operator_report=report)
        # 3) 빈 입력.
        return build_strategy_agent_input(inputs=ReportInputs())

    def _build_output(self, agent_input: StrategyAgentInput) -> AgentOutput:
        summary, reasons = self._summarize(agent_input)
        # 위험 신호 합집합 carry (중복 제거).
        risk_flags: list[str] = []
        seen: set[str] = set()
        for it in agent_input.items:
            for flag in it.risk_flags:
                base = flag.split(" (")[0]
                if base not in seen:
                    seen.add(base)
                    risk_flags.append(base)
        return AgentOutput(
            role=AgentRole.STRATEGY_RESEARCHER,
            decision=AgentDecision.REPORT,
            summary=summary,
            reasons=reasons,
            risk_flags=risk_flags,
            metadata={
                "strategy_agent_input": agent_input.to_dict(),
                "advisory_only":        True,
                "is_order_signal":      False,
                "auto_apply_allowed":   False,
                "is_live_authorization": False,
            },
        )

    @staticmethod
    def _summarize(agent_input: StrategyAgentInput) -> tuple[str, list[str]]:
        status = agent_input.overall_status
        count_ready = sum(
            1 for it in agent_input.items
            if it.paper_candidate_status == ReportStatus.READY_FOR_PAPER.value
        )
        if count_ready > 0:
            summary = (
                f"{count_ready}개 전략이 모의투자(Paper) 환경에서 시작 검토 가능 — "
                f"전체 판정 {status}. 본 입력은 advisory 전용."
            )
        else:
            summary = (
                f"현재 모의투자 검토 가능한 전략 없음 — 전체 판정 {status}. "
                f"본 입력은 advisory 전용."
            )
        reasons: list[str] = []
        reasons.append(
            f"item_count={agent_input.item_count}, "
            f"paper_ready={count_ready}, schema_version={agent_input.schema_version}"
        )
        if agent_input.reasons_no_candidate:
            for r in agent_input.reasons_no_candidate[:5]:
                reasons.append(f"reason_no_candidate: {r}")
        return summary, reasons

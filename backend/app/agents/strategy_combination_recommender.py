"""#4-02: Strategy Combination Recommender — 오늘 사용할 Paper 전략 *조합* 추천.

4-01 `StrategyAgentInput` (또는 5 단계 산출물 / `OperatorReport`) 을 입력으로
받아, AI Agent 가 *오늘* Paper 모의운용에서 사용할 전략 *조합* 을 advisory 로
*추천* 한다.

## 핵심 목적

- 전략별 3 액션 분류: `RECOMMEND` (추천) / `EXCLUDE` (제외) / `HOLD` (보류).
- Paper 후보가 1개 이상 있으면 *상위 N 조합* 을 단순 휴리스틱으로 선정.
- Paper 후보가 0건이면 "오늘은 자동 운용 후보 없음" 으로 *명확히* 표시.
- 전략 다양성(strategy diversity) + 종목 다양성(symbol diversity) 권장.

## 절대 invariant (CLAUDE.md 절대 원칙 1~5 상속)

1. **본 추천은 *주문 신호가 아니다*** — `is_order_signal=False` 불변.
2. **자동 적용 0건** — `auto_apply_allowed=False` 불변.
3. **실거래 허가 0건** — `is_live_authorization=False` 불변.
4. **자동 Paper trader 시작 0건** — `auto_start_paper_trader=False` 불변.
5. **broker / OrderExecutor / route_order 호출 0건** — 정적 grep 가드.
6. **외부 HTTP / AI SDK 호출 0건** — 본 모듈은 *결정론적 휴리스틱* 만.
7. **DB write 0건** — read-only.
8. **`StrategyAction` enum 에 BUY/SELL/HOLD 같은 *주문 방향* 0개** — 본 모듈은
   *주문 신호* 가 아니라 *advisory 권고* 만 생성.

## 액션 분류 정책

| Action | 조건 | 의미 |
|---|---|---|
| `RECOMMEND` | `paper_candidate_status=READY_FOR_PAPER` AND `len(risk_flags)<=1` | "오늘 모의투자에서 검토 가능" |
| `HOLD` | `READY_FOR_PAPER` AND `len(risk_flags)>=2` | "기준 통과했으나 위험 신호 다수 — 추가 관찰 후 결정" |
| `EXCLUDE` | 위 외 모든 status (NEED_MORE_DATA, OVERFIT_RISK, STRESS_FAILED, REJECTED_BY_RISK, NO_CANDIDATE) | "오늘 사용 안 함" |

## 조합 선정 휴리스틱

- 후보가 1개 → 단일 추천.
- 후보가 2개 이상 → score 내림차순 정렬 후, *strategy 다양성* 우선 → *symbol
  다양성* 차순 으로 최대 N 개 (default 2) 선정.
- 모두 같은 strategy/symbol 이면 점수 1위만 추천 + 나머지는 HOLD (다양성 부족 사유 carry).

자세한 정책: `docs/strategy_combination_recommendation.md`.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import StrEnum
from typing import Any

from app.agents.base import (
    AgentBase,
    AgentContext,
    AgentDecision,
    AgentMetadata,
    AgentOutput,
    AgentRole,
)
from app.agents.strategy_optimizer_agent import (
    SCHEMA_VERSION as INPUT_SCHEMA_VERSION,
    StrategyAgentInput,
    StrategyAgentInputItem,
    build_strategy_agent_input,
)
from app.analytics.strategy_optimization_report import (
    OperatorReport,
    ReportInputs,
    ReportStatus,
)


COMBINATION_SCHEMA_VERSION = "1.0"

DEFAULT_MAX_COMBO_SIZE = 2
DEFAULT_HOLD_RISK_FLAG_THRESHOLD = 2   # risk_flags >= 2 → HOLD


# ─────────────────────────────────────────────────────────────────────────────
# 1. Enum — StrategyAction (3 액션) + OverallRecommendation (4 상태)
# ─────────────────────────────────────────────────────────────────────────────


class StrategyAction(StrEnum):
    """전략 단위 권고 액션 — 주문 신호가 아니라 *advisory 분류*.

    *BUY/SELL/HOLD 값 0개* — 본 enum 은 주문 방향이 아닌 "오늘 Paper 운용에서
    어떻게 다룰지" 만 표현.
    """
    RECOMMEND = "RECOMMEND"   # 오늘 Paper 모의투자에서 검토 가능
    HOLD      = "HOLD"        # 기준 통과했으나 위험 신호 다수 — 보류
    EXCLUDE   = "EXCLUDE"     # 오늘 사용 안 함 (단계 탈락 등)


class OverallRecommendation(StrEnum):
    """전체 상태 라벨 — 비개발자가 한 줄로 판단 가능."""
    HAS_RECOMMENDATIONS    = "HAS_RECOMMENDATIONS"     # 1개 이상 추천 가능
    ALL_HOLD               = "ALL_HOLD"                 # 후보는 있으나 모두 HOLD
    NO_CANDIDATES_TODAY    = "NO_CANDIDATES_TODAY"      # 후보 0건
    NEEDS_OPERATOR_REVIEW  = "NEEDS_OPERATOR_REVIEW"    # 다양성 부족 등 운영자 결정 필요


_OVERALL_LABEL_KO: dict[OverallRecommendation, str] = {
    OverallRecommendation.HAS_RECOMMENDATIONS:
        "오늘 모의투자(Paper) 검토 가능한 추천 조합 있음",
    OverallRecommendation.ALL_HOLD:
        "후보는 있으나 위험 신호 다수 — 모두 보류",
    OverallRecommendation.NO_CANDIDATES_TODAY:
        "오늘은 자동 운용 후보 없음",
    OverallRecommendation.NEEDS_OPERATOR_REVIEW:
        "운영자 판단 필요 — 다양성 부족 또는 자료 결손",
}


_ACTION_LABEL_KO: dict[StrategyAction, str] = {
    StrategyAction.RECOMMEND: "추천 (오늘 Paper 모의투자 검토 가능)",
    StrategyAction.HOLD:      "보류 (위험 신호 다수 — 추가 관찰)",
    StrategyAction.EXCLUDE:   "제외 (오늘 사용 안 함)",
}


# ─────────────────────────────────────────────────────────────────────────────
# 2. Dataclass — StrategyDecision (per-strategy) + StrategyCombinationRecommendation
# ─────────────────────────────────────────────────────────────────────────────


@dataclass(frozen=True)
class StrategyDecision:
    """단일 (strategy, symbol, params) 의 advisory 권고.

    *주문 결정이 아니다* — `is_order_signal=False` 불변 (`__post_init__` 가드).
    """

    strategy:                str
    symbol:                  str
    params:                  dict[str, Any]
    action:                  StrategyAction
    paper_candidate_status:  str
    score:                   float
    risk_flags:              list[str]      = field(default_factory=list)
    reasons:                 list[str]      = field(default_factory=list)

    # 절대 invariant.
    is_order_signal:         bool = False
    auto_apply_allowed:      bool = False
    is_live_authorization:   bool = False

    def __post_init__(self) -> None:
        if self.is_order_signal is not False:
            raise ValueError("StrategyDecision.is_order_signal must be False.")
        if self.auto_apply_allowed is not False:
            raise ValueError("StrategyDecision.auto_apply_allowed must be False.")
        if self.is_live_authorization is not False:
            raise ValueError("StrategyDecision.is_live_authorization must be False.")
        if not isinstance(self.action, StrategyAction):
            raise ValueError("action must be a StrategyAction.")

    def to_dict(self) -> dict[str, Any]:
        return {
            "strategy":                self.strategy,
            "symbol":                  self.symbol,
            "params":                  dict(self.params),
            "action":                  self.action.value,
            "action_label_ko":         _ACTION_LABEL_KO[self.action],
            "paper_candidate_status":  self.paper_candidate_status,
            "score":                   float(self.score),
            "risk_flags":              list(self.risk_flags),
            "reasons":                 list(self.reasons),
            "is_order_signal":         False,
            "auto_apply_allowed":      False,
            "is_live_authorization":   False,
        }


@dataclass(frozen=True)
class StrategyCombinationRecommendation:
    """오늘 Paper 운용 추천 조합 + 분류 결과.

    *주문 신호가 아니다* — `is_order_signal=False` 불변. Paper trader 자동 시작
    *불가*; 운영자가 BotControl / Paper Auto Loop 흐름에서 *수동 시작*.
    """

    generated_at:               str
    schema_version:             str
    overall_recommendation:     OverallRecommendation
    recommended_combo:          list[StrategyDecision]
    held:                       list[StrategyDecision]   = field(default_factory=list)
    excluded:                   list[StrategyDecision]   = field(default_factory=list)
    decisions:                  list[StrategyDecision]   = field(default_factory=list)
    reasons_no_candidate:       list[str]                = field(default_factory=list)
    operator_notes:             list[str]                = field(default_factory=list)
    advisory_disclaimer:        str                      = (
        "본 추천은 *오늘 모의투자(Paper) 검토용 advisory* 입니다. "
        "주문 신호가 아니며 자동 paper trader 시작 / 자동 실거래 활성화를 "
        "수행하지 않습니다. 운영자가 BotControl / Paper Auto Loop 흐름에서 "
        "*명시 시작* 해야 합니다. is_order_signal=False / auto_apply_allowed=False "
        "/ is_live_authorization=False / auto_start_paper_trader=False."
    )
    metadata:                   dict[str, Any]           = field(default_factory=dict)

    # 절대 invariant — 4 다중 가드.
    is_order_signal:            bool = False
    auto_apply_allowed:         bool = False
    is_live_authorization:      bool = False
    auto_start_paper_trader:    bool = False

    def __post_init__(self) -> None:
        if self.is_order_signal is not False:
            raise ValueError(
                "StrategyCombinationRecommendation.is_order_signal must be False."
            )
        if self.auto_apply_allowed is not False:
            raise ValueError(
                "StrategyCombinationRecommendation.auto_apply_allowed must be False."
            )
        if self.is_live_authorization is not False:
            raise ValueError(
                "StrategyCombinationRecommendation.is_live_authorization must be False."
            )
        if self.auto_start_paper_trader is not False:
            raise ValueError(
                "StrategyCombinationRecommendation.auto_start_paper_trader must be False."
            )
        if not isinstance(self.overall_recommendation, OverallRecommendation):
            raise ValueError("overall_recommendation must be OverallRecommendation.")
        if not isinstance(self.advisory_disclaimer, str) or not self.advisory_disclaimer:
            raise ValueError("advisory_disclaimer must be non-empty.")

    @property
    def recommended_count(self) -> int:
        return len(self.recommended_combo)

    @property
    def held_count(self) -> int:
        return len(self.held)

    @property
    def excluded_count(self) -> int:
        return len(self.excluded)

    def to_dict(self) -> dict[str, Any]:
        return {
            "generated_at":             self.generated_at,
            "schema_version":           self.schema_version,
            "overall_recommendation":   self.overall_recommendation.value,
            "overall_label_ko":         _OVERALL_LABEL_KO[self.overall_recommendation],
            "recommended_count":        self.recommended_count,
            "held_count":               self.held_count,
            "excluded_count":           self.excluded_count,
            "recommended_combo":        [d.to_dict() for d in self.recommended_combo],
            "held":                     [d.to_dict() for d in self.held],
            "excluded":                 [d.to_dict() for d in self.excluded],
            "decisions":                [d.to_dict() for d in self.decisions],
            "reasons_no_candidate":     list(self.reasons_no_candidate),
            "operator_notes":           list(self.operator_notes),
            "advisory_disclaimer":      self.advisory_disclaimer,
            "metadata":                 dict(self.metadata),
            # 최상위 invariant (JSON consumer 안전).
            "is_order_signal":          False,
            "auto_apply_allowed":       False,
            "is_live_authorization":    False,
            "auto_start_paper_trader":  False,
        }


# ─────────────────────────────────────────────────────────────────────────────
# 3. Classifier — item → StrategyDecision
# ─────────────────────────────────────────────────────────────────────────────


def _classify_action(
    item: StrategyAgentInputItem,
    *,
    hold_risk_flag_threshold: int = DEFAULT_HOLD_RISK_FLAG_THRESHOLD,
) -> tuple[StrategyAction, list[str]]:
    """단일 item → (action, reasons) — 결정론적 휴리스틱."""
    reasons: list[str] = []
    status = item.paper_candidate_status
    flag_count = len(item.risk_flags)

    if status == ReportStatus.READY_FOR_PAPER.value:
        if flag_count >= hold_risk_flag_threshold:
            reasons.append(
                f"paper_candidate=READY but {flag_count} risk flag(s) "
                f">= {hold_risk_flag_threshold} → HOLD"
            )
            return StrategyAction.HOLD, reasons
        reasons.append("paper_candidate=READY_FOR_PAPER, risk flags within threshold")
        return StrategyAction.RECOMMEND, reasons

    # 그 외 status — EXCLUDE.
    label = {
        ReportStatus.NEED_MORE_DATA.value:    "데이터 부족",
        ReportStatus.OVERFIT_RISK.value:      "과최적화 의심",
        ReportStatus.STRESS_FAILED.value:     "스트레스 테스트 불합격",
        ReportStatus.REJECTED_BY_RISK.value:  "위험 한도 위반",
        ReportStatus.NO_CANDIDATE.value:      "후보 자격 없음",
    }.get(status, f"status={status}")
    reasons.append(f"excluded_because: {label}")
    # 추가 사유 carry — exclusion_reasons 상위 3개.
    for er in item.exclusion_reasons[:3]:
        reasons.append(f"detail: {er}")
    return StrategyAction.EXCLUDE, reasons


# ─────────────────────────────────────────────────────────────────────────────
# 4. Combination selector — diversity + score
# ─────────────────────────────────────────────────────────────────────────────


def _select_combo(
    recommended: list[StrategyDecision],
    *,
    max_combo_size: int,
) -> tuple[list[StrategyDecision], list[StrategyDecision], list[str]]:
    """추천 후보 → (선정된 조합, 보류 후보, operator_notes).

    Heuristic:
    1. score 내림차순 정렬.
    2. 첫 후보 무조건 포함.
    3. 다음 후보부터: 이미 선정된 것과 *strategy* 가 다른 후보 우선 →
       그 다음 *symbol* 이 다른 후보. 동률이면 score 높은 쪽.
    4. max_combo_size 도달 또는 후보 소진 시 종료.
    5. 다양성 부족(모두 같은 strategy 또는 모두 같은 symbol) 시 운영자 노트.
    """
    if not recommended:
        return [], [], []

    sorted_recs = sorted(recommended, key=lambda d: d.score, reverse=True)
    selected: list[StrategyDecision] = [sorted_recs[0]]
    rest: list[StrategyDecision] = list(sorted_recs[1:])
    notes: list[str] = []

    while len(selected) < max_combo_size and rest:
        used_strategies = {d.strategy for d in selected}
        used_symbols    = {d.symbol   for d in selected}

        # 1) strategy 다양성 우선.
        candidates_strat = [d for d in rest if d.strategy not in used_strategies]
        # 2) 그 안에서 symbol 다양성.
        if candidates_strat:
            best = max(
                candidates_strat,
                key=lambda d: (d.symbol not in used_symbols, d.score),
            )
        else:
            # strategy 다양성 불가 — symbol 다양성만이라도.
            candidates_sym = [d for d in rest if d.symbol not in used_symbols]
            if candidates_sym:
                best = max(candidates_sym, key=lambda d: d.score)
            else:
                # 다양성 0 — 다음 후보를 그냥 추가하지 않고 종료
                # (같은 (strategy, symbol) 페어 중복 추천 회피).
                break
        selected.append(best)
        rest.remove(best)

    # 남은 추천 후보는 *조합 미선정* → 보류 분류.
    if rest:
        notes.append(
            f"{len(rest)}건은 추천 가능했으나 조합 상한 (max_combo_size={max_combo_size}) "
            "또는 다양성 우선으로 미선정"
        )

    # 다양성 부족 경고.
    if len(selected) >= 2:
        if len({d.strategy for d in selected}) == 1:
            notes.append("선정된 조합의 전략이 모두 동일 — 운영자 검토 권고")
        if len({d.symbol for d in selected}) == 1:
            notes.append("선정된 조합의 종목이 모두 동일 — 분산 효과 제한")

    return selected, rest, notes


# ─────────────────────────────────────────────────────────────────────────────
# 5. Builder — main entry point
# ─────────────────────────────────────────────────────────────────────────────


def build_combination_recommendation(
    *,
    agent_input:                StrategyAgentInput | None = None,
    operator_report:            OperatorReport     | None = None,
    inputs:                     ReportInputs       | None = None,
    max_combo_size:             int                       = DEFAULT_MAX_COMBO_SIZE,
    hold_risk_flag_threshold:   int                       = DEFAULT_HOLD_RISK_FLAG_THRESHOLD,
    metadata:                   dict[str, Any]     | None = None,
    now:                        datetime           | None = None,
) -> StrategyCombinationRecommendation:
    """4-01 StrategyAgentInput (또는 OperatorReport / raw paths) → 조합 추천.

    입력 우선순위: agent_input → operator_report → inputs → empty.

    Args:
        agent_input:               4-01 표준 입력 (있으면 우선 사용).
        operator_report:           3-08 OperatorReport — agent_input 빌더로 변환.
        inputs:                    raw 5 단계 산출물 경로.
        max_combo_size:            추천 조합 상한 (default 2).
        hold_risk_flag_threshold:  HOLD 임계 risk_flag 수 (default 2).
        metadata:                  자유 carry.
        now:                       테스트용 datetime 주입.

    Returns:
        StrategyCombinationRecommendation — 본 결과는 *advisory*.
    """
    if agent_input is None:
        agent_input = build_strategy_agent_input(
            operator_report=operator_report,
            inputs=inputs or ReportInputs(),
            now=now,
        )
    if now is None:
        now = datetime.now(timezone.utc)

    # 1) 각 item → StrategyDecision.
    decisions: list[StrategyDecision] = []
    for item in agent_input.items:
        action, reasons = _classify_action(
            item, hold_risk_flag_threshold=hold_risk_flag_threshold,
        )
        decisions.append(StrategyDecision(
            strategy=item.strategy,
            symbol=item.symbol,
            params=dict(item.params),
            action=action,
            paper_candidate_status=item.paper_candidate_status,
            score=float(item.recommendation_context.get("score", 0.0)),
            risk_flags=list(item.risk_flags),
            reasons=reasons,
        ))

    # 2) 분류 — RECOMMEND / HOLD / EXCLUDE.
    recommended_pool = [d for d in decisions if d.action == StrategyAction.RECOMMEND]
    held             = [d for d in decisions if d.action == StrategyAction.HOLD]
    excluded         = [d for d in decisions if d.action == StrategyAction.EXCLUDE]

    # 3) 조합 선정 (diversity + score).
    selected, demoted, combo_notes = _select_combo(
        recommended_pool, max_combo_size=max(0, int(max_combo_size)),
    )

    # 4) 조합 미선정 추천 후보 → HOLD 로 *재분류* + reasons carry.
    demoted_decisions: list[StrategyDecision] = []
    for d in demoted:
        demoted_decisions.append(StrategyDecision(
            strategy=d.strategy, symbol=d.symbol, params=dict(d.params),
            action=StrategyAction.HOLD,
            paper_candidate_status=d.paper_candidate_status,
            score=d.score,
            risk_flags=list(d.risk_flags),
            reasons=list(d.reasons) + ["demoted_from_recommend: 조합 다양성 / 상한"],
        ))
    held_combined = held + demoted_decisions

    # 5) Overall 상태.
    overall: OverallRecommendation
    reasons_no_candidate: list[str] = list(agent_input.reasons_no_candidate)
    if selected:
        overall = OverallRecommendation.HAS_RECOMMENDATIONS
    elif recommended_pool and not selected:
        # 후보는 있는데 조합 선정 0 — 보통 일어나지 않지만 안전 fallback.
        overall = OverallRecommendation.NEEDS_OPERATOR_REVIEW
    elif held_combined and not recommended_pool:
        overall = OverallRecommendation.ALL_HOLD
    elif not decisions:
        overall = OverallRecommendation.NO_CANDIDATES_TODAY
        if not reasons_no_candidate:
            reasons_no_candidate.append("no_strategy_input_items")
    else:
        # 모두 EXCLUDE 인 경우.
        overall = OverallRecommendation.NO_CANDIDATES_TODAY
        if not reasons_no_candidate:
            reasons_no_candidate.append("all_strategies_excluded_today")

    operator_notes: list[str] = list(combo_notes)
    if overall == OverallRecommendation.NO_CANDIDATES_TODAY:
        operator_notes.append(
            "오늘은 자동 운용 후보 없음 — 강제로 paper trader 를 시작하지 마세요."
        )
    if overall == OverallRecommendation.ALL_HOLD:
        operator_notes.append(
            "후보는 있으나 위험 신호로 모두 보류 — 위험 신호 완화 후 재평가 권고."
        )

    return StrategyCombinationRecommendation(
        generated_at=now.isoformat(),
        schema_version=COMBINATION_SCHEMA_VERSION,
        overall_recommendation=overall,
        recommended_combo=selected,
        held=held_combined,
        excluded=excluded,
        decisions=decisions,
        reasons_no_candidate=reasons_no_candidate,
        operator_notes=operator_notes,
        metadata={
            "pipeline":                 "step4-02-strategy-combination-recommender",
            "input_schema_version":     INPUT_SCHEMA_VERSION,
            "max_combo_size":           int(max_combo_size),
            "hold_risk_flag_threshold": int(hold_risk_flag_threshold),
            "source_item_count":        agent_input.item_count,
            **(metadata or {}),
        },
    )


# ─────────────────────────────────────────────────────────────────────────────
# 6. Agent — AgentBase 호환
# ─────────────────────────────────────────────────────────────────────────────


_AGENT_METADATA = AgentMetadata(
    name="strategy_combination_recommender",
    role=AgentRole.STRATEGY_RESEARCHER,
    description=(
        "Today's Paper 모의운용 전략 조합을 advisory 로 추천하는 agent. "
        "RECOMMEND / HOLD / EXCLUDE 3 액션 분류 + 다양성 + score 기반 조합 선정. "
        "본 agent 는 *주문 신호 / LLM 호출 / broker 호출 / paper trader 자동 시작*"
        " 을 수행하지 않는다 (advisory only)."
    ),
    inputs=[
        "AgentContext.extra['strategy_agent_input'] (StrategyAgentInput, 4-01) 또는",
        "AgentContext.extra['operator_report'] (OperatorReport, 3-08)",
    ],
    outputs=[
        "AgentOutput(decision=RECOMMEND, summary, reasons, risk_flags, "
        "metadata['combination_recommendation'])",
    ],
    forbidden=[
        "broker.place_order", "route_order", "OrderExecutor",
        "anthropic / openai / httpx / requests",
        "BUY / SELL / HOLD order signal",
        "auto paper trader start", "auto live trading activation",
    ],
    can_execute_order=False,
)


class StrategyCombinationRecommenderAgent(AgentBase):
    """Strategy combination recommender — AgentBase 호환 advisory agent."""

    @property
    def metadata(self) -> AgentMetadata:
        return _AGENT_METADATA

    def run(self, context: AgentContext) -> AgentOutput:
        recommendation = self._resolve_recommendation(context)
        summary, reasons = self._summarize(recommendation)
        # 위험 신호 합집합 carry (모든 decisions 의 risk_flags).
        risk_flags: list[str] = []
        seen: set[str] = set()
        for d in recommendation.decisions:
            for flag in d.risk_flags:
                base = flag.split(" (")[0]
                if base not in seen:
                    seen.add(base)
                    risk_flags.append(base)
        return AgentOutput(
            role=AgentRole.STRATEGY_RESEARCHER,
            decision=AgentDecision.RECOMMEND,
            summary=summary,
            reasons=reasons,
            risk_flags=risk_flags,
            metadata={
                "combination_recommendation": recommendation.to_dict(),
                "advisory_only":              True,
                "is_order_signal":            False,
                "auto_apply_allowed":         False,
                "is_live_authorization":      False,
                "auto_start_paper_trader":    False,
            },
        )

    def _resolve_recommendation(
        self, context: AgentContext,
    ) -> StrategyCombinationRecommendation:
        extra = context.extra or {}
        existing = extra.get("combination_recommendation")
        if isinstance(existing, StrategyCombinationRecommendation):
            return existing
        agent_input = extra.get("strategy_agent_input")
        if isinstance(agent_input, StrategyAgentInput):
            return build_combination_recommendation(agent_input=agent_input)
        report = extra.get("operator_report")
        if isinstance(report, OperatorReport):
            return build_combination_recommendation(operator_report=report)
        # 빈 입력 — 명시적 NO_CANDIDATES_TODAY.
        return build_combination_recommendation(inputs=ReportInputs())

    @staticmethod
    def _summarize(
        rec: StrategyCombinationRecommendation,
    ) -> tuple[str, list[str]]:
        if rec.overall_recommendation == OverallRecommendation.HAS_RECOMMENDATIONS:
            names = ", ".join(
                f"{d.strategy}/{d.symbol}" for d in rec.recommended_combo
            )
            summary = (
                f"오늘 Paper 모의운용 검토 가능: {rec.recommended_count}건 — {names}. "
                "본 추천은 advisory — 운영자가 명시 시작."
            )
        elif rec.overall_recommendation == OverallRecommendation.ALL_HOLD:
            summary = (
                f"후보 {rec.held_count}건 모두 보류 — 위험 신호 다수. "
                "본 추천은 advisory."
            )
        elif rec.overall_recommendation == OverallRecommendation.NO_CANDIDATES_TODAY:
            summary = "오늘은 자동 운용 후보 없음 — paper trader 시작 금지."
        else:
            summary = (
                "운영자 판단 필요 — 다양성 부족 또는 자료 결손. 본 추천은 advisory."
            )
        reasons: list[str] = []
        reasons.append(
            f"recommended={rec.recommended_count}, held={rec.held_count}, "
            f"excluded={rec.excluded_count}"
        )
        for n in rec.operator_notes[:3]:
            reasons.append(f"operator_note: {n}")
        for r in rec.reasons_no_candidate[:3]:
            reasons.append(f"reason_no_candidate: {r}")
        return summary, reasons

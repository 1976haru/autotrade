"""#4-03: Overfit Warning Agent — 과최적화 의심 전략을 추천에서 제외 / 보류.

Walk-forward (3-04) 결과를 *읽고* 과최적화 의심 전략을 식별해 4-02 의
`StrategyCombinationRecommendation` 의 `recommended_combo` 에서 *demote* 한다.

## 핵심 목적

- `walk_forward_verdict == OVERFIT_RISK` 전략은 *추천 제외* (default) 또는
  *운영자 watchlist* 로 이동.
- `walk_forward_verdict == HEALTHY` 이지만 train/validation 성과 차이가 *큰*
  전략은 SUSPECT 로 분류해 *보류*.
- 모든 후보가 OVERFIT_RISK → overall `NO_CANDIDATE` / `ALL_HOLD` 로 surface.
- 운영자에게 "실제 Paper 운용 전 재검증 필요" 메시지 carry.

## 절대 invariant (CLAUDE.md 절대 원칙 1~5 상속)

1. **본 경고는 *주문 신호가 아니다*** — `is_order_signal=False` 불변.
2. **자동 적용 0건** — `auto_apply_allowed=False` 불변.
3. **실거래 허가 0건** — `is_live_authorization=False` 불변.
4. **자동 전략 비활성 0건** — `auto_disable=False` 불변 (#77 alpha decay 와 동일
   정책). 전략 변경은 운영자 *수동 PR* 만.
5. **broker / OrderExecutor / route_order import 0건** — 정적 grep 가드.
6. **외부 HTTP / AI SDK import 0건** — 결정론적 휴리스틱.
7. **DB write 0건** — read-only.

## OverfitVerdict 4단계

| Verdict | 조건 | Action |
|---|---|---|
| `OVERFIT_RISK` | `walk_forward_verdict=="OVERFIT_RISK"` | `EXCLUDE` (default) 또는 `WATCHLIST` |
| `SUSPECT` | `walk_forward_verdict=="HEALTHY"` AND `train_validation_gap >= suspect_threshold` (default 0.5) | `HOLD` |
| `INSUFFICIENT_DATA` | `walk_forward_verdict == "INSUFFICIENT_DATA"` 또는 미존재 | `HOLD` (보수적) |
| `HEALTHY` | 그 외 | 상위 추천 유지 (`KEEP`) |

`train_validation_gap` 정규화: `(train_avg - val_avg) / max(|train_avg|, 1.0)` —
양수일수록 train 대비 validation 성과 저하 (overfit 의심).
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
from app.agents.strategy_combination_recommender import (
    OverallRecommendation,
    StrategyAction,
    StrategyCombinationRecommendation,
    StrategyDecision,
)
from app.agents.strategy_optimizer_agent import (
    StrategyAgentInput,
    StrategyAgentInputItem,
    build_strategy_agent_input,
)
from app.analytics.paper_candidate_aggregator import PipelineStage
from app.analytics.strategy_optimization_report import (
    OperatorReport,
    ReportInputs,
    ReportStatus,
    StrategyEntry,
)


OVERFIT_SCHEMA_VERSION = "1.0"

DEFAULT_SUSPECT_GAP_THRESHOLD = 0.5      # train vs val 차이 비율 임계


# ─────────────────────────────────────────────────────────────────────────────
# 1. Enums
# ─────────────────────────────────────────────────────────────────────────────


class OverfitVerdict(StrEnum):
    """과최적화 의심 분류 — *주문 방향* 0개."""
    HEALTHY            = "HEALTHY"            # 의심 신호 없음
    SUSPECT            = "SUSPECT"            # HEALTHY 이지만 train/val 차이 큼
    OVERFIT_RISK       = "OVERFIT_RISK"       # walk-forward 가 명시 verdict
    INSUFFICIENT_DATA  = "INSUFFICIENT_DATA"  # 데이터 부족 — 보수적 보류


class OverfitAction(StrEnum):
    """본 agent 가 권고하는 후속 action — *주문 방향 아님*."""
    KEEP        = "KEEP"        # 상위 추천 그대로 유지
    HOLD        = "HOLD"        # 보류 — SUSPECT 또는 INSUFFICIENT_DATA
    WATCHLIST   = "WATCHLIST"   # 운영자 관찰 대상 (별도 carry)
    EXCLUDE     = "EXCLUDE"     # 추천 제외 — OVERFIT_RISK


_VERDICT_LABEL_KO: dict[OverfitVerdict, str] = {
    OverfitVerdict.HEALTHY:           "과최적화 의심 없음",
    OverfitVerdict.SUSPECT:           "train 대비 validation 성과 저하 — 추가 검증 권고",
    OverfitVerdict.OVERFIT_RISK:      "과최적화 의심 — 추천 제외, 재검증 필요",
    OverfitVerdict.INSUFFICIENT_DATA: "walk-forward 데이터 부족 — 보수적 보류",
}


_ACTION_LABEL_KO: dict[OverfitAction, str] = {
    OverfitAction.KEEP:      "상위 추천 유지",
    OverfitAction.HOLD:      "보류",
    OverfitAction.WATCHLIST: "운영자 관찰 대상",
    OverfitAction.EXCLUDE:   "추천 제외",
}


_OPERATOR_NOTE_BY_VERDICT: dict[OverfitVerdict, str] = {
    OverfitVerdict.OVERFIT_RISK: (
        "실제 Paper 운용 전 재검증 필요 — 훈련구간에서만 좋고 검증구간에서 성과 저하."
    ),
    OverfitVerdict.SUSPECT: (
        "Walk-forward 추가 검증 권고 — train 대비 validation 성과 저하 의심."
    ),
    OverfitVerdict.INSUFFICIENT_DATA: (
        "Walk-forward 데이터 부족 — 백테스트 기간 확장 후 재평가."
    ),
    OverfitVerdict.HEALTHY: "",
}


# ─────────────────────────────────────────────────────────────────────────────
# 2. Per-strategy warning dataclass (6 필수 필드 + 식별 + invariant)
# ─────────────────────────────────────────────────────────────────────────────


@dataclass(frozen=True)
class OverfitWarning:
    """단일 (strategy, symbol, params) 의 과최적화 경고.

    *주문 결정이 아니다* — `is_order_signal=False` 불변 (`__post_init__` 가드).
    필수 6 필드 (user spec):
    - `overfit_flag`
    - `overfit_reason`
    - `train_validation_gap`
    - `walk_forward_verdict`
    - `recommendation_action`
    - `operator_note`
    """

    strategy:                 str
    symbol:                   str
    params:                   dict[str, Any]
    overfit_flag:             bool
    overfit_reason:           str | None
    train_validation_gap:     float | None
    walk_forward_verdict:     str | None
    recommendation_action:    OverfitAction
    operator_note:            str | None
    overfit_verdict:          OverfitVerdict

    # 절대 invariant.
    is_order_signal:          bool = False
    auto_apply_allowed:       bool = False
    is_live_authorization:    bool = False
    auto_disable:             bool = False     # 전략 자동 비활성 영구 금지.

    def __post_init__(self) -> None:
        if self.is_order_signal is not False:
            raise ValueError("OverfitWarning.is_order_signal must be False.")
        if self.auto_apply_allowed is not False:
            raise ValueError("OverfitWarning.auto_apply_allowed must be False.")
        if self.is_live_authorization is not False:
            raise ValueError("OverfitWarning.is_live_authorization must be False.")
        if self.auto_disable is not False:
            raise ValueError(
                "OverfitWarning.auto_disable must be False — 전략 자동 비활성 금지."
            )
        if not isinstance(self.recommendation_action, OverfitAction):
            raise ValueError("recommendation_action must be OverfitAction.")
        if not isinstance(self.overfit_verdict, OverfitVerdict):
            raise ValueError("overfit_verdict must be OverfitVerdict.")

    def to_dict(self) -> dict[str, Any]:
        return {
            "strategy":               self.strategy,
            "symbol":                 self.symbol,
            "params":                 dict(self.params),
            "overfit_flag":           self.overfit_flag,
            "overfit_reason":         self.overfit_reason,
            "train_validation_gap":   self.train_validation_gap,
            "walk_forward_verdict":   self.walk_forward_verdict,
            "recommendation_action":  self.recommendation_action.value,
            "action_label_ko":        _ACTION_LABEL_KO[self.recommendation_action],
            "operator_note":          self.operator_note,
            "overfit_verdict":        self.overfit_verdict.value,
            "verdict_label_ko":       _VERDICT_LABEL_KO[self.overfit_verdict],
            "is_order_signal":        False,
            "auto_apply_allowed":     False,
            "is_live_authorization":  False,
            "auto_disable":           False,
        }


# ─────────────────────────────────────────────────────────────────────────────
# 3. Top-level report
# ─────────────────────────────────────────────────────────────────────────────


@dataclass(frozen=True)
class OverfitWarningReport:
    """전체 과최적화 경고 — 상위 상태 + per-strategy 분류."""

    generated_at:           str
    schema_version:         str
    overall_status:         OverallRecommendation
    warnings:               list[OverfitWarning]
    overfit_count:          int
    suspect_count:          int
    insufficient_data_count: int
    healthy_count:          int
    operator_notes:         list[str]            = field(default_factory=list)
    advisory_disclaimer:    str                  = (
        "본 경고는 *advisory* 입니다. 전략 자동 비활성 / 자동 paper trader 시작 "
        "/ 자동 실거래 활성화를 수행하지 *않습니다*. OVERFIT_RISK 라벨은 *재검증 "
        "후보 표시* 일 뿐 — 운영자가 별도 PR 로 파라미터 조정 / Strategy Researcher "
        "분석 / 추가 백테스트 후 결정. is_order_signal=False / auto_apply_allowed=False "
        "/ is_live_authorization=False / auto_disable=False."
    )
    metadata:               dict[str, Any]       = field(default_factory=dict)

    # 절대 invariant.
    is_order_signal:        bool = False
    auto_apply_allowed:     bool = False
    is_live_authorization:  bool = False
    auto_disable:           bool = False

    def __post_init__(self) -> None:
        for name, val in (
            ("is_order_signal",       self.is_order_signal),
            ("auto_apply_allowed",    self.auto_apply_allowed),
            ("is_live_authorization", self.is_live_authorization),
            ("auto_disable",          self.auto_disable),
        ):
            if val is not False:
                raise ValueError(f"OverfitWarningReport.{name} must be False.")
        if not isinstance(self.overall_status, OverallRecommendation):
            raise ValueError("overall_status must be OverallRecommendation.")
        if not isinstance(self.advisory_disclaimer, str) or not self.advisory_disclaimer:
            raise ValueError("advisory_disclaimer must be non-empty.")

    def to_dict(self) -> dict[str, Any]:
        return {
            "generated_at":             self.generated_at,
            "schema_version":           self.schema_version,
            "overall_status":           self.overall_status.value,
            "warnings":                 [w.to_dict() for w in self.warnings],
            "overfit_count":            self.overfit_count,
            "suspect_count":            self.suspect_count,
            "insufficient_data_count":  self.insufficient_data_count,
            "healthy_count":            self.healthy_count,
            "operator_notes":           list(self.operator_notes),
            "advisory_disclaimer":      self.advisory_disclaimer,
            "metadata":                 dict(self.metadata),
            "is_order_signal":          False,
            "auto_apply_allowed":       False,
            "is_live_authorization":    False,
            "auto_disable":             False,
        }


# ─────────────────────────────────────────────────────────────────────────────
# 4. Train/validation gap 계산
# ─────────────────────────────────────────────────────────────────────────────


def _compute_train_val_gap(stage: PipelineStage | None) -> float | None:
    """PipelineStage(3-04).extra 에서 train/val expectancy gap 정규화.

    Returns:
        float | None — `(train_avg - val_avg) / max(|train_avg|, 1.0)`.
        양수 = train 우세 (overfit 의심). 데이터 없으면 None.
    """
    if stage is None or stage.name != "3-04":
        return None
    extra = stage.extra or {}
    train = extra.get("train_expectancy_avg")
    val   = extra.get("val_expectancy_avg")
    if not isinstance(train, (int, float)) or not isinstance(val, (int, float)):
        return None
    denom = max(abs(float(train)), 1.0)
    return (float(train) - float(val)) / denom


# ─────────────────────────────────────────────────────────────────────────────
# 5. Per-item 분류 — verdict + action
# ─────────────────────────────────────────────────────────────────────────────


def _classify_overfit(
    item: StrategyAgentInputItem,
    *,
    train_val_gap:           float | None,
    suspect_gap_threshold:   float = DEFAULT_SUSPECT_GAP_THRESHOLD,
    demote_to_watchlist:     bool  = False,
) -> tuple[OverfitVerdict, OverfitAction, str | None]:
    """item → (verdict, action, reason)."""
    wf = item.walk_forward_verdict

    if wf == "OVERFIT_RISK":
        action = OverfitAction.WATCHLIST if demote_to_watchlist else OverfitAction.EXCLUDE
        reason = (
            "walk_forward verdict=OVERFIT_RISK — "
            "훈련구간에서만 좋고 검증구간에서 성과 저하"
        )
        if train_val_gap is not None:
            reason += f" (train/val gap={train_val_gap:.2f})"
        return OverfitVerdict.OVERFIT_RISK, action, reason

    if wf == "INSUFFICIENT_DATA" or wf is None:
        reason = "walk-forward 데이터 부족 — 보수적 보류"
        return OverfitVerdict.INSUFFICIENT_DATA, OverfitAction.HOLD, reason

    if wf == "HEALTHY":
        # SUSPECT 조건 — gap 이 임계 이상.
        if train_val_gap is not None and train_val_gap >= suspect_gap_threshold:
            reason = (
                f"walk_forward verdict=HEALTHY 이지만 train/val gap "
                f"{train_val_gap:.2f} >= 임계 {suspect_gap_threshold:.2f} — "
                "추가 검증 권고"
            )
            return OverfitVerdict.SUSPECT, OverfitAction.HOLD, reason
        return OverfitVerdict.HEALTHY, OverfitAction.KEEP, None

    # 알 수 없는 verdict — 보수적 INSUFFICIENT_DATA.
    return (
        OverfitVerdict.INSUFFICIENT_DATA,
        OverfitAction.HOLD,
        f"unknown walk_forward verdict={wf}",
    )


# ─────────────────────────────────────────────────────────────────────────────
# 6. Builder — agent_input + (선택) OperatorReport 로 gap 계산
# ─────────────────────────────────────────────────────────────────────────────


def _walk_forward_stage_for(
    entry: StrategyEntry | None,
) -> PipelineStage | None:
    if entry is None:
        return None
    for s in entry.pipeline_stages:
        if s.name == "3-04":
            return s
    return None


def _entry_key(entry: StrategyEntry) -> tuple:
    params = entry.params or {}
    return (
        entry.strategy_id,
        entry.symbol,
        tuple(sorted((str(k), str(v)) for k, v in params.items())),
    )


def _item_key(item: StrategyAgentInputItem) -> tuple:
    params = item.params or {}
    return (
        item.strategy,
        item.symbol,
        tuple(sorted((str(k), str(v)) for k, v in params.items())),
    )


def build_overfit_warning_report(
    *,
    agent_input:            StrategyAgentInput | None = None,
    operator_report:        OperatorReport     | None = None,
    inputs:                 ReportInputs       | None = None,
    suspect_gap_threshold:  float                     = DEFAULT_SUSPECT_GAP_THRESHOLD,
    demote_to_watchlist:    bool                      = False,
    metadata:               dict[str, Any]     | None = None,
    now:                    datetime           | None = None,
) -> OverfitWarningReport:
    """과최적화 경고 리포트 생성.

    `operator_report` 가 주어지면 PipelineStage(3-04).extra 에서 train/val gap
    계산. 그렇지 않으면 `train_validation_gap=None` 으로 verdict 만 기반.

    Args:
        agent_input:           4-01 표준 입력 (있으면 우선).
        operator_report:       3-08 OperatorReport (train/val gap 계산용).
        inputs:                raw 5 단계 경로 — fallback.
        suspect_gap_threshold: SUSPECT 분류 임계 (default 0.5).
        demote_to_watchlist:   True 면 OVERFIT_RISK → WATCHLIST, False 면 EXCLUDE.
        metadata:              자유 carry.
        now:                   테스트용 datetime 주입.
    """
    if agent_input is None:
        agent_input = build_strategy_agent_input(
            operator_report=operator_report,
            inputs=inputs or ReportInputs(),
            now=now,
        )
    if now is None:
        now = datetime.now(timezone.utc)

    # operator_report 우선 시도 — entry 매핑으로 train/val gap 계산.
    entries_by_key: dict[tuple, StrategyEntry] = {}
    if operator_report is not None:
        for e in operator_report.entries:
            entries_by_key[_entry_key(e)] = e

    warnings: list[OverfitWarning] = []
    overfit_count = 0
    suspect_count = 0
    insufficient_count = 0
    healthy_count = 0

    for item in agent_input.items:
        entry = entries_by_key.get(_item_key(item))
        wf_stage = _walk_forward_stage_for(entry)
        gap = _compute_train_val_gap(wf_stage)
        verdict, action, reason = _classify_overfit(
            item,
            train_val_gap=gap,
            suspect_gap_threshold=suspect_gap_threshold,
            demote_to_watchlist=demote_to_watchlist,
        )
        if verdict == OverfitVerdict.OVERFIT_RISK:
            overfit_count += 1
        elif verdict == OverfitVerdict.SUSPECT:
            suspect_count += 1
        elif verdict == OverfitVerdict.INSUFFICIENT_DATA:
            insufficient_count += 1
        else:
            healthy_count += 1

        warnings.append(OverfitWarning(
            strategy=item.strategy,
            symbol=item.symbol,
            params=dict(item.params),
            overfit_flag=(verdict == OverfitVerdict.OVERFIT_RISK),
            overfit_reason=reason,
            train_validation_gap=gap,
            walk_forward_verdict=item.walk_forward_verdict,
            recommendation_action=action,
            operator_note=(_OPERATOR_NOTE_BY_VERDICT.get(verdict) or None),
            overfit_verdict=verdict,
        ))

    # Overall 상태 — 모든 후보가 OVERFIT_RISK 면 ALL_HOLD 로 surface.
    total = len(warnings)
    if total == 0:
        overall = OverallRecommendation.NO_CANDIDATES_TODAY
    elif healthy_count == 0 and (overfit_count + suspect_count + insufficient_count) > 0:
        # READY 후보가 0건 — 안전 fallback.
        overall = OverallRecommendation.ALL_HOLD
    else:
        overall = OverallRecommendation.HAS_RECOMMENDATIONS

    operator_notes: list[str] = []
    if overfit_count > 0:
        operator_notes.append(
            f"OVERFIT_RISK {overfit_count}건 발견 — 실제 Paper 운용 전 재검증 필요"
        )
    if suspect_count > 0:
        operator_notes.append(
            f"SUSPECT {suspect_count}건 (train/val gap >= {suspect_gap_threshold}) — "
            "추가 검증 권고"
        )
    if insufficient_count > 0:
        operator_notes.append(
            f"INSUFFICIENT_DATA {insufficient_count}건 — 백테스트 기간 확장 권고"
        )

    return OverfitWarningReport(
        generated_at=now.isoformat(),
        schema_version=OVERFIT_SCHEMA_VERSION,
        overall_status=overall,
        warnings=warnings,
        overfit_count=overfit_count,
        suspect_count=suspect_count,
        insufficient_data_count=insufficient_count,
        healthy_count=healthy_count,
        operator_notes=operator_notes,
        metadata={
            "pipeline":              "step4-03-overfit-warning-agent",
            "suspect_gap_threshold": float(suspect_gap_threshold),
            "demote_to_watchlist":   bool(demote_to_watchlist),
            "source_item_count":     agent_input.item_count,
            **(metadata or {}),
        },
    )


# ─────────────────────────────────────────────────────────────────────────────
# 7. 4-02 combination recommendation 위에 과최적화 필터 적용
# ─────────────────────────────────────────────────────────────────────────────


def apply_overfit_filter(
    recommendation: StrategyCombinationRecommendation,
    warning_report: OverfitWarningReport,
    *,
    demote_to_watchlist: bool = False,
    now:                 datetime | None = None,
) -> StrategyCombinationRecommendation:
    """기존 추천 위에 과최적화 필터 적용 — OVERFIT_RISK 를 recommended_combo
    에서 제거하고 excluded (또는 held) 로 이동.

    *원본 객체 변경 0건* — 새 dataclass 반환 (frozen immutability 보존).
    """
    if now is None:
        now = datetime.now(timezone.utc)

    overfit_keys: set[tuple] = set()
    suspect_keys: set[tuple] = set()
    for w in warning_report.warnings:
        key = (
            w.strategy, w.symbol,
            tuple(sorted((str(k), str(v)) for k, v in (w.params or {}).items())),
        )
        if w.recommendation_action == OverfitAction.EXCLUDE:
            overfit_keys.add(key)
        elif w.recommendation_action in {OverfitAction.HOLD, OverfitAction.WATCHLIST}:
            suspect_keys.add(key)

    def _key(d: StrategyDecision) -> tuple:
        return (
            d.strategy, d.symbol,
            tuple(sorted((str(k), str(v)) for k, v in (d.params or {}).items())),
        )

    def _with_overfit_reason(d: StrategyDecision, action: StrategyAction,
                              extra_reason: str) -> StrategyDecision:
        return StrategyDecision(
            strategy=d.strategy, symbol=d.symbol, params=dict(d.params),
            action=action,
            paper_candidate_status=d.paper_candidate_status,
            score=d.score,
            risk_flags=list(d.risk_flags),
            reasons=list(d.reasons) + [extra_reason],
        )

    new_recommended: list[StrategyDecision] = []
    new_held:        list[StrategyDecision] = list(recommendation.held)
    new_excluded:    list[StrategyDecision] = list(recommendation.excluded)

    for d in recommendation.recommended_combo:
        k = _key(d)
        if k in overfit_keys:
            if demote_to_watchlist:
                new_held.append(_with_overfit_reason(
                    d, StrategyAction.HOLD,
                    "overfit_filter: OVERFIT_RISK → watchlist (Paper 운용 전 재검증)",
                ))
            else:
                new_excluded.append(_with_overfit_reason(
                    d, StrategyAction.EXCLUDE,
                    "overfit_filter: OVERFIT_RISK → 추천 제외 (재검증 필요)",
                ))
        elif k in suspect_keys:
            new_held.append(_with_overfit_reason(
                d, StrategyAction.HOLD,
                "overfit_filter: SUSPECT/INSUFFICIENT — 보류",
            ))
        else:
            new_recommended.append(d)

    # decisions 재구성 — 원본 순서 유지하되 action 갱신.
    new_decisions: list[StrategyDecision] = []
    for d in recommendation.decisions:
        k = _key(d)
        if k in overfit_keys:
            new_decisions.append(_with_overfit_reason(
                d,
                StrategyAction.HOLD if demote_to_watchlist else StrategyAction.EXCLUDE,
                "overfit_filter: OVERFIT_RISK applied",
            ))
        elif k in suspect_keys:
            new_decisions.append(_with_overfit_reason(
                d, StrategyAction.HOLD,
                "overfit_filter: SUSPECT/INSUFFICIENT applied",
            ))
        else:
            new_decisions.append(d)

    # Overall 재계산.
    if new_recommended:
        overall = OverallRecommendation.HAS_RECOMMENDATIONS
    elif new_held:
        overall = OverallRecommendation.ALL_HOLD
    elif new_excluded:
        overall = OverallRecommendation.NO_CANDIDATES_TODAY
    else:
        overall = OverallRecommendation.NO_CANDIDATES_TODAY

    operator_notes = list(recommendation.operator_notes)
    if overfit_keys:
        operator_notes.append(
            f"overfit_filter applied: {len(overfit_keys)}건 OVERFIT_RISK demoted"
        )
    if suspect_keys:
        operator_notes.append(
            f"overfit_filter applied: {len(suspect_keys)}건 SUSPECT/INSUFFICIENT 보류"
        )
    reasons_no_candidate = list(recommendation.reasons_no_candidate)
    if not new_recommended and (overfit_keys or suspect_keys):
        reasons_no_candidate.append(
            "all_candidates_demoted_by_overfit_filter"
        )

    return StrategyCombinationRecommendation(
        generated_at=now.isoformat(),
        schema_version=recommendation.schema_version,
        overall_recommendation=overall,
        recommended_combo=new_recommended,
        held=new_held,
        excluded=new_excluded,
        decisions=new_decisions,
        reasons_no_candidate=reasons_no_candidate,
        operator_notes=operator_notes,
        metadata={
            **dict(recommendation.metadata),
            "overfit_filter_applied":      True,
            "overfit_filter_excluded":     sorted(
                f"{s}/{sym}" for (s, sym, _) in overfit_keys
            ),
            "overfit_filter_held":         sorted(
                f"{s}/{sym}" for (s, sym, _) in suspect_keys
            ),
            "overfit_filter_demote_mode":  "watchlist" if demote_to_watchlist else "exclude",
        },
    )


# ─────────────────────────────────────────────────────────────────────────────
# 8. Agent (AgentBase) — 본 모듈의 high-level wrapper
# ─────────────────────────────────────────────────────────────────────────────


_AGENT_METADATA = AgentMetadata(
    name="overfit_warning_agent",
    role=AgentRole.RISK_AUDITOR,
    description=(
        "Walk-forward 결과를 읽고 과최적화 의심 전략을 advisory 로 경고 / "
        "추천에서 제외. 본 agent 는 *주문 신호 / LLM 호출 / broker 호출 / "
        "전략 자동 비활성* 을 수행하지 않는다 (advisory only)."
    ),
    inputs=[
        "AgentContext.extra['strategy_agent_input'] (StrategyAgentInput, 4-01) 또는",
        "AgentContext.extra['operator_report'] (OperatorReport, 3-08)",
    ],
    outputs=[
        "AgentOutput(decision=WARN, summary, reasons, risk_flags, "
        "metadata['overfit_warning_report'])",
    ],
    forbidden=[
        "broker.place_order", "route_order", "OrderExecutor",
        "anthropic / openai / httpx / requests",
        "BUY / SELL / HOLD signal generation",
        "auto strategy disable / auto parameter mutation",
    ],
    can_execute_order=False,
)


class OverfitWarningAgent(AgentBase):
    """Overfit Warning Agent — AgentBase 호환 advisory agent."""

    @property
    def metadata(self) -> AgentMetadata:
        return _AGENT_METADATA

    def run(self, context: AgentContext) -> AgentOutput:
        report = self._resolve_report(context)
        summary = self._summarize(report)
        reasons: list[str] = []
        reasons.append(
            f"overfit={report.overfit_count}, suspect={report.suspect_count}, "
            f"insufficient={report.insufficient_data_count}, "
            f"healthy={report.healthy_count}"
        )
        for n in report.operator_notes[:3]:
            reasons.append(f"operator_note: {n}")
        # 위험 신호 — overfit 인 전략 이름들.
        risk_flags: list[str] = []
        for w in report.warnings:
            if w.overfit_flag:
                risk_flags.append(f"overfit_risk:{w.strategy}/{w.symbol}")
            elif w.overfit_verdict == OverfitVerdict.SUSPECT:
                risk_flags.append(f"overfit_suspect:{w.strategy}/{w.symbol}")
        return AgentOutput(
            role=AgentRole.RISK_AUDITOR,
            decision=AgentDecision.WARN if report.overfit_count > 0 else AgentDecision.REPORT,
            summary=summary,
            reasons=reasons,
            risk_flags=risk_flags,
            metadata={
                "overfit_warning_report": report.to_dict(),
                "advisory_only":          True,
                "is_order_signal":        False,
                "auto_apply_allowed":     False,
                "is_live_authorization":  False,
                "auto_disable":           False,
            },
        )

    def _resolve_report(self, context: AgentContext) -> OverfitWarningReport:
        extra = context.extra or {}
        existing = extra.get("overfit_warning_report")
        if isinstance(existing, OverfitWarningReport):
            return existing
        agent_input = extra.get("strategy_agent_input")
        operator_report = extra.get("operator_report")
        if isinstance(agent_input, StrategyAgentInput) and \
                isinstance(operator_report, OperatorReport):
            return build_overfit_warning_report(
                agent_input=agent_input, operator_report=operator_report,
            )
        if isinstance(operator_report, OperatorReport):
            return build_overfit_warning_report(operator_report=operator_report)
        if isinstance(agent_input, StrategyAgentInput):
            return build_overfit_warning_report(agent_input=agent_input)
        return build_overfit_warning_report(inputs=ReportInputs())

    @staticmethod
    def _summarize(report: OverfitWarningReport) -> str:
        if report.overfit_count > 0:
            return (
                f"과최적화 의심 전략 {report.overfit_count}건 발견 — 추천 제외 권고. "
                "본 경고는 advisory."
            )
        if report.suspect_count > 0:
            return (
                f"train/val gap 의심 {report.suspect_count}건 — 보류 권고. "
                "본 경고는 advisory."
            )
        if report.insufficient_data_count > 0:
            return (
                f"Walk-forward 데이터 부족 {report.insufficient_data_count}건 — "
                "보수적 보류 권고."
            )
        if report.healthy_count > 0:
            return (
                f"과최적화 의심 신호 없음 — {report.healthy_count}건 모두 HEALTHY. "
                "본 경고는 advisory."
            )
        return "분석 대상 전략 없음 — 오늘은 자동 운용 후보 없음."


# avoid F401: ReportStatus is intentionally imported for tests/docs cross-ref.
_REPORT_STATUS_REF = ReportStatus.NO_CANDIDATE

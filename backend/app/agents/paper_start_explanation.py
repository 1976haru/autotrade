"""#4-05: Paper 실행 전 최종 설명 — 시작 버튼 누르기 전 카드.

운영자가 *[시작]* 버튼을 누르기 전, AI Agent 가 *왜* 이 전략을 추천했는지 /
*왜* 어떤 전략은 제외/보류했는지 한 화면에서 보여주는 *advisory* 설명을 생성.

4-01 ~ 4-04 의 결과를 *통합* :
- 4-01 `StrategyAgentInput` — 표준 입력
- 4-02 `PaperStrategyCombination` (v2) — 추천/제외/보류 분류
- 4-03 `OverfitWarningReport` — 과최적화 경고 (uses_overfit_filter)
- 4-04 `MarketRegimeReport` — 장세 분류
- (옵션) Pre-market check (#80) — `start_allowed` / `blocking_reasons`

## 핵심 원칙

1. **본 설명은 *advisory*** — 실제 주문 / 자동 시작 0건.
2. **추천 사유 / 제외 사유 / 보류 사유를 *한국어*** 로 명확히.
3. **운영자 친화** — 비개발자가 5분 안에 이해 가능.
4. **can_start_paper=False 시 blocking_reasons carry** — Pre-market BLOCK /
   LOW_LIQUIDITY / UNKNOWN regime / overfit 차단 등.
5. **OVERFIT_RISK 전략은 *추천 사유가 아니라 제외 사유*** 에 표시 (4-03 우선).

## 절대 invariant (CLAUDE.md 절대 원칙 1~5 상속)

1. `is_order_signal=False` / `auto_apply_allowed=False` /
   `is_live_authorization=False` / `can_start_paper` 는 운영자 *수동 결정* 용
   advisory bool (자동 시작 *불가*).
2. broker / OrderExecutor / route_order import 0건 (정적 grep).
3. 외부 HTTP / AI SDK / LLM import 0건 — 결정론적 aggregator.
4. DB write 0건.
5. secret / API key / 계좌번호 carry 0건.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import StrEnum
from typing import Any

from app.agents.market_regime_agent import (
    MarketRegime,
    MarketRegimeReport,
    MarketStateInput,
    apply_regime_filter,
    classify_market_regime,
)
from app.agents.overfit_warning_agent import (
    OverfitVerdict,
    OverfitWarningReport,
    apply_overfit_filter,
    build_overfit_warning_report,
)
from app.agents.strategy_combination_recommender import (
    PaperCombinationStatus,
    PaperStrategyCombination,
    PaperStrategyEntry,
    StrategyCombinationRecommendation,
    build_combination_recommendation,
    build_paper_combination_recommendation,
)
from app.agents.strategy_optimizer_agent import (
    StrategyAgentInput,
    build_strategy_agent_input,
)
from app.analytics.strategy_optimization_report import (
    OperatorReport,
    ReportInputs,
)


EXPLANATION_SCHEMA_VERSION = "1.0"


class ExplanationVerdict(StrEnum):
    """전체 시작 가능 여부 — 운영자 친화 라벨.

    *주문 방향* 0개 — BUY/SELL/PLACE_ORDER 값 없음.
    """
    READY_TO_REVIEW    = "READY_TO_REVIEW"     # 추천 1개 이상, 시작 검토 가능
    REVIEW_WITH_WARNING = "REVIEW_WITH_WARNING"  # 추천 있으나 경고 다수
    HOLD               = "HOLD"                 # 모두 보류 — 시작 권고 안 함
    DO_NOT_START       = "DO_NOT_START"         # Pre-market BLOCK / LOW_LIQUIDITY / UNKNOWN / 후보 0
    INSUFFICIENT_DATA  = "INSUFFICIENT_DATA"    # 분석 입력 부족


_VERDICT_LABEL_KO: dict[ExplanationVerdict, str] = {
    ExplanationVerdict.READY_TO_REVIEW:
        "AI Paper 검토 가능 — 추천 조합을 운영자가 검토 후 시작 결정",
    ExplanationVerdict.REVIEW_WITH_WARNING:
        "AI Paper 검토 가능하지만 경고 다수 — 신중 시작 권고",
    ExplanationVerdict.HOLD:
        "오늘은 모두 보류 — 시작 권고 안 함",
    ExplanationVerdict.DO_NOT_START:
        "오늘 자동 시작 금지 — 차단 조건 발생",
    ExplanationVerdict.INSUFFICIENT_DATA:
        "분석 입력 부족 — 파이프라인 결과 재확인 필요",
}


@dataclass(frozen=True)
class PreMarketSummary:
    """Pre-market check (#80) 압축 carry — 본 모듈이 직접 의존하지 않음."""
    start_allowed:     bool
    verdict:           str
    blocking_reasons:  list[str]       = field(default_factory=list)
    warnings:          list[str]       = field(default_factory=list)


@dataclass(frozen=True)
class StrategyExplanation:
    """단일 전략의 추천 / 제외 / 보류 *사유* 설명."""

    strategy:                str
    symbol:                  str
    bucket:                  str                  # "recommended" / "watchlist" / "excluded"
    paper_candidate_status:  str
    rationale_lines:         list[str]            = field(default_factory=list)
    risk_flags:              list[str]            = field(default_factory=list)
    overfit_verdict:         str | None           = None
    overfit_reason:          str | None           = None
    train_validation_gap:    float | None         = None
    regime_policy_role:      str | None           = None   # "preferred"/"watchlist"/"blocked"/None

    # 절대 invariant.
    is_order_signal:         bool = False
    auto_apply_allowed:      bool = False
    is_live_authorization:   bool = False

    def __post_init__(self) -> None:
        if self.is_order_signal is not False:
            raise ValueError("StrategyExplanation.is_order_signal must be False.")
        if self.auto_apply_allowed is not False:
            raise ValueError("StrategyExplanation.auto_apply_allowed must be False.")
        if self.is_live_authorization is not False:
            raise ValueError("StrategyExplanation.is_live_authorization must be False.")
        if self.bucket not in ("recommended", "watchlist", "excluded"):
            raise ValueError(f"bucket must be one of recommended/watchlist/excluded, got {self.bucket!r}")

    def to_dict(self) -> dict[str, Any]:
        return {
            "strategy":                self.strategy,
            "symbol":                  self.symbol,
            "bucket":                  self.bucket,
            "paper_candidate_status":  self.paper_candidate_status,
            "rationale_lines":         list(self.rationale_lines),
            "risk_flags":              list(self.risk_flags),
            "overfit_verdict":         self.overfit_verdict,
            "overfit_reason":          self.overfit_reason,
            "train_validation_gap":    self.train_validation_gap,
            "regime_policy_role":      self.regime_policy_role,
            "is_order_signal":         False,
            "auto_apply_allowed":      False,
            "is_live_authorization":   False,
        }


@dataclass(frozen=True)
class PaperStartExplanation:
    """#4-05 — 시작 버튼 누르기 전 통합 설명 카드."""

    generated_at:            str
    schema_version:          str
    verdict:                 ExplanationVerdict

    # 4-02 v2 추천 결과 carry (서로 다른 bucket).
    recommended_explanations: list[StrategyExplanation]
    watchlist_explanations:   list[StrategyExplanation]
    excluded_explanations:    list[StrategyExplanation]

    # 4-04 장세 정보.
    market_regime:           str                       # MarketRegime.value
    regime_confidence:       float                     # 0~1
    regime_reasons:          list[str]                 = field(default_factory=list)
    regime_risk_flags:       list[str]                 = field(default_factory=list)
    regime_allowed_tactics:  list[str]                 = field(default_factory=list)
    regime_blocked_tactics:  list[str]                 = field(default_factory=list)

    # 4-03 과최적화 요약.
    overfit_count:           int                       = 0
    overfit_strategies:      list[str]                 = field(default_factory=list)

    # 운영자 친화.
    headline:                str                       = ""
    risk_summary:            list[str]                 = field(default_factory=list)
    operator_note:           str                       = ""
    next_actions:            list[str]                 = field(default_factory=list)

    # Pre-market gate.
    can_start_paper:         bool                      = False
    blocking_reasons:        list[str]                 = field(default_factory=list)

    # 절대 invariant — 시작 *허가가 아니다*.
    is_order_signal:         bool = False
    auto_apply_allowed:      bool = False
    is_live_authorization:   bool = False
    advisory_disclaimer:     str = (
        "본 설명은 *advisory* — 실거래 주문이 아니며 자동 paper trader 시작 / "
        "자동 실거래 활성화를 수행하지 않습니다. 운영자가 BotControl / Paper "
        "Auto Loop 흐름에서 *명시 시작* 해야 합니다. "
        "is_order_signal=False / auto_apply_allowed=False / is_live_authorization=False."
    )
    metadata:                dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        for name, val in (
            ("is_order_signal",       self.is_order_signal),
            ("auto_apply_allowed",    self.auto_apply_allowed),
            ("is_live_authorization", self.is_live_authorization),
        ):
            if val is not False:
                raise ValueError(f"PaperStartExplanation.{name} must be False.")
        if not isinstance(self.verdict, ExplanationVerdict):
            raise ValueError("verdict must be ExplanationVerdict.")
        if not (0.0 <= self.regime_confidence <= 1.0):
            raise ValueError(
                f"regime_confidence must be in [0,1], got {self.regime_confidence}"
            )
        if not isinstance(self.advisory_disclaimer, str) or not self.advisory_disclaimer:
            raise ValueError("advisory_disclaimer must be non-empty.")

    def to_dict(self) -> dict[str, Any]:
        return {
            "generated_at":             self.generated_at,
            "schema_version":           self.schema_version,
            "verdict":                  self.verdict.value,
            "verdict_label_ko":         _VERDICT_LABEL_KO[self.verdict],
            "recommended_explanations": [e.to_dict() for e in self.recommended_explanations],
            "watchlist_explanations":   [e.to_dict() for e in self.watchlist_explanations],
            "excluded_explanations":    [e.to_dict() for e in self.excluded_explanations],
            "market_regime":            self.market_regime,
            "regime_confidence":        float(self.regime_confidence),
            "regime_reasons":           list(self.regime_reasons),
            "regime_risk_flags":        list(self.regime_risk_flags),
            "regime_allowed_tactics":   list(self.regime_allowed_tactics),
            "regime_blocked_tactics":   list(self.regime_blocked_tactics),
            "overfit_count":            int(self.overfit_count),
            "overfit_strategies":       list(self.overfit_strategies),
            "headline":                 self.headline,
            "risk_summary":             list(self.risk_summary),
            "operator_note":            self.operator_note,
            "next_actions":             list(self.next_actions),
            "can_start_paper":          bool(self.can_start_paper),
            "blocking_reasons":         list(self.blocking_reasons),
            "advisory_disclaimer":      self.advisory_disclaimer,
            "metadata":                 dict(self.metadata),
            # 최상위 invariant — JSON consumer 안전.
            "is_order_signal":          False,
            "auto_apply_allowed":       False,
            "is_live_authorization":    False,
        }


# ─────────────────────────────────────────────────────────────────────────────
# Helpers
# ─────────────────────────────────────────────────────────────────────────────


def _entry_key(e: PaperStrategyEntry) -> tuple:
    """(strategy, symbol, params) 식별 키 — 4-03 / 4-04 cross-reference."""
    return (
        e.strategy, e.symbol,
        tuple(sorted((str(k), str(v)) for k, v in (e.params or {}).items())),
    )


def _build_strategy_explanation(
    *,
    entry:           PaperStrategyEntry,
    bucket:          str,
    overfit_report:  OverfitWarningReport | None,
    regime_report:   MarketRegimeReport,
) -> StrategyExplanation:
    """단일 전략의 추천/제외/보류 사유 lines 생성."""
    lines: list[str] = []

    # 기본 사유 — 4-02 의 rationale.
    if entry.rationale:
        lines.append(entry.rationale)

    # 4-03 overfit 정보.
    overfit_verdict: str | None = None
    overfit_reason: str | None = None
    train_val_gap: float | None = None
    if overfit_report is not None:
        for w in overfit_report.warnings:
            if (w.strategy, w.symbol) == (entry.strategy, entry.symbol):
                overfit_verdict = w.overfit_verdict.value
                overfit_reason = w.overfit_reason
                train_val_gap = w.train_validation_gap
                # OVERFIT_RISK → 제외 사유에 명시 (추천이 아니라).
                if w.overfit_verdict == OverfitVerdict.OVERFIT_RISK:
                    base = "⚠ 과최적화 의심 — 훈련구간에서만 좋고 검증구간에서 성과 저하"
                    if train_val_gap is not None:
                        base += f" (train/val gap={train_val_gap:.2f})"
                    lines.append(base)
                elif w.overfit_verdict == OverfitVerdict.SUSPECT:
                    lines.append(
                        "⚠ train/val 성과 차이 큼 — 추가 검증 권고"
                    )
                elif w.overfit_verdict == OverfitVerdict.INSUFFICIENT_DATA:
                    lines.append(
                        "walk-forward 데이터 부족 — 백테스트 기간 확장 권고"
                    )
                break

    # 4-04 regime 정보.
    regime_policy_role: str | None = None
    policy = regime_report.allowed_strategies
    blocked = regime_report.blocked_strategies
    watchlist = regime_report.watchlist_strategies
    if entry.strategy in policy:
        regime_policy_role = "preferred"
        lines.append(f"장세 {regime_report.regime.value} 에서 *우선 검토* 전략군")
    elif entry.strategy in blocked:
        regime_policy_role = "blocked"
        lines.append(f"장세 {regime_report.regime.value} 에서 *차단* 권고 전략군")
    elif entry.strategy in watchlist:
        regime_policy_role = "watchlist"
        lines.append(f"장세 {regime_report.regime.value} 에서 *보류* 권고 전략군")

    return StrategyExplanation(
        strategy=entry.strategy,
        symbol=entry.symbol,
        bucket=bucket,
        paper_candidate_status=entry.paper_candidate_status,
        rationale_lines=lines,
        risk_flags=list(entry.risk_flags),
        overfit_verdict=overfit_verdict,
        overfit_reason=overfit_reason,
        train_validation_gap=train_val_gap,
        regime_policy_role=regime_policy_role,
    )


def _compute_verdict(
    *,
    pre_market:    PreMarketSummary | None,
    regime:        MarketRegimeReport,
    combo:         PaperStrategyCombination,
    overfit_count: int,
) -> tuple[ExplanationVerdict, bool, list[str]]:
    """전체 verdict + can_start_paper + blocking_reasons.

    우선순위:
    1. Pre-market BLOCK → DO_NOT_START
    2. LOW_LIQUIDITY regime → DO_NOT_START
    3. UNKNOWN regime → DO_NOT_START (자동 시작 금지)
    4. 후보 0건 (combo.status == NO_CANDIDATE) → DO_NOT_START
    5. 모두 차단/보류 (REJECTED_BY_RISK / WATCH_ONLY) → HOLD
    6. NEED_MORE_DATA → INSUFFICIENT_DATA
    7. 추천 1+ AND 경고 다수 → REVIEW_WITH_WARNING
    8. 추천 1+ → READY_TO_REVIEW
    """
    blocking: list[str] = []

    # 1. Pre-market.
    if pre_market is not None and not pre_market.start_allowed:
        blocking.append(
            f"pre_market_block: verdict={pre_market.verdict}, "
            f"reasons={pre_market.blocking_reasons}"
        )
        return ExplanationVerdict.DO_NOT_START, False, blocking

    # 2. LOW_LIQUIDITY.
    if regime.regime == MarketRegime.LOW_LIQUIDITY:
        blocking.append(
            "regime_low_liquidity: 거래대금 부족 — 슬리피지 위험으로 자동 시작 금지"
        )
        return ExplanationVerdict.DO_NOT_START, False, blocking

    # 3. UNKNOWN.
    if regime.regime == MarketRegime.UNKNOWN:
        blocking.append(
            "regime_unknown: 장세 분류 불가 — Paper 자동 시작 금지"
        )
        return ExplanationVerdict.DO_NOT_START, False, blocking

    # 4. NO_CANDIDATE.
    if combo.status == PaperCombinationStatus.NO_CANDIDATE:
        blocking.append("no_candidate: 분석 가능한 후보 0건")
        return ExplanationVerdict.DO_NOT_START, False, blocking

    # 5. NEED_MORE_DATA.
    if combo.status == PaperCombinationStatus.NEED_MORE_DATA:
        blocking.append(
            "need_more_data: 모든 후보가 검증 데이터 부족"
        )
        return ExplanationVerdict.INSUFFICIENT_DATA, False, blocking

    # 6. REJECTED_BY_RISK / WATCH_ONLY.
    if combo.status in (
        PaperCombinationStatus.REJECTED_BY_RISK,
        PaperCombinationStatus.WATCH_ONLY,
    ):
        blocking.append(
            f"all_hold_or_rejected: combo.status={combo.status.value} — "
            "모두 차단/보류로 시작 권고 안 함"
        )
        return ExplanationVerdict.HOLD, False, blocking

    # 7~8. 추천 있음.
    has_warnings = overfit_count > 0 or len(regime.risk_flags) > 0 \
        or len(combo.risk_summary) >= 2
    if has_warnings:
        return ExplanationVerdict.REVIEW_WITH_WARNING, True, blocking
    return ExplanationVerdict.READY_TO_REVIEW, True, blocking


def _build_headline(verdict: ExplanationVerdict, combo: PaperStrategyCombination) -> str:
    if verdict == ExplanationVerdict.READY_TO_REVIEW:
        names = ", ".join(
            f"{e.strategy}/{e.symbol}" for e in combo.recommended_strategies
        )
        return (
            f"오늘 AI Paper 검토 가능: {len(combo.recommended_strategies)}건 — {names}. "
            "본 추천은 advisory."
        )
    if verdict == ExplanationVerdict.REVIEW_WITH_WARNING:
        return (
            f"AI Paper 추천 {len(combo.recommended_strategies)}건 있으나 *경고 다수* — "
            "신중 검토 후 시작 결정"
        )
    if verdict == ExplanationVerdict.HOLD:
        return "오늘은 모두 보류 — 시작 권고 안 함"
    if verdict == ExplanationVerdict.INSUFFICIENT_DATA:
        return "분석 입력 부족 — 검증 데이터 확보 후 재평가"
    return "오늘 자동 시작 금지 — 차단 조건 발생"


def _build_next_actions(
    verdict: ExplanationVerdict, blocking: list[str],
) -> list[str]:
    actions: list[str] = []
    if verdict in (ExplanationVerdict.READY_TO_REVIEW,
                    ExplanationVerdict.REVIEW_WITH_WARNING):
        actions.append("추천 전략을 *수동* 으로 Paper Auto Loop 에 입력")
        actions.append("AI Agent 가 표시한 위험 신호 / 제외 사유 확인 후 시작 결정")
        if verdict == ExplanationVerdict.REVIEW_WITH_WARNING:
            actions.append("경고가 많으므로 sizing 축소 또는 일부 전략만 선택 권고")
    elif verdict == ExplanationVerdict.HOLD:
        actions.append("모든 후보의 위험 신호 / 보류 사유 검토")
        actions.append("필터 정책 / 파라미터 재조정 후 재평가")
    elif verdict == ExplanationVerdict.INSUFFICIENT_DATA:
        actions.append("백테스트 기간 확장 / Walk-forward fold 수 증가")
        actions.append("검증 통과 후 재평가")
    else:  # DO_NOT_START
        actions.append("아래 blocking_reasons 확인 후 차단 조건 해소")
        actions.append("Paper 자동 시작 금지 상태 — 수동 시작도 권고 안 함")
    actions.append(
        "본 설명은 *advisory* — 실거래 활성화는 별도 옵트인 절차 필요"
    )
    return actions


# ─────────────────────────────────────────────────────────────────────────────
# Builder — main entry
# ─────────────────────────────────────────────────────────────────────────────


def build_paper_start_explanation(
    *,
    operator_report:    OperatorReport | None = None,
    inputs:             ReportInputs   | None = None,
    market_state:       MarketStateInput | None = None,
    pre_market:         PreMarketSummary | None = None,
    demote_to_watchlist: bool                 = False,
    now:                datetime | None       = None,
) -> PaperStartExplanation:
    """4-01 ~ 4-04 결과를 통합 → 시작 전 설명 카드.

    *broker 호출 0건* — read-only aggregator.

    호출 순서 (정책 우선순위):
    1. 4-01 `build_strategy_agent_input`
    2. 4-02 v1 (`build_combination_recommendation`) + 4-03 `apply_overfit_filter`
       + 4-04 `apply_regime_filter` — 추천 필터링 적용
    3. 4-02 v2 (`build_paper_combination_recommendation`) — 운영자 친화 출력
    4. verdict 계산 + blocking_reasons (pre_market > liquidity > unknown > 후보 0 …)
    """
    if now is None:
        now = datetime.now(timezone.utc)

    # 1) 4-01 표준 입력.
    if operator_report is None and inputs is None:
        inputs = ReportInputs()
    agent_input: StrategyAgentInput = build_strategy_agent_input(
        operator_report=operator_report,
        inputs=inputs,
        now=now,
    )

    # 2) 4-04 장세 분류.
    regime_report: MarketRegimeReport = classify_market_regime(market_state, now=now)

    # 3) 4-02 v1 + 4-03 + 4-04 (filter chain) — 권장 순서 lock.
    v1_combo: StrategyCombinationRecommendation = build_combination_recommendation(
        agent_input=agent_input, now=now,
    )
    # 4-03 OVERFIT 필터 우선.
    overfit_report: OverfitWarningReport = build_overfit_warning_report(
        agent_input=agent_input, operator_report=operator_report,
        demote_to_watchlist=demote_to_watchlist,
    )
    v1_after_overfit = apply_overfit_filter(
        v1_combo, overfit_report,
        demote_to_watchlist=demote_to_watchlist, now=now,
    )
    # 4-04 regime 필터.
    v1_after_regime = apply_regime_filter(v1_after_overfit, regime_report, now=now)

    # 4) 4-02 v2 — 운영자 친화 출력 (recommended / excluded / watchlist).
    v2_combo: PaperStrategyCombination = build_paper_combination_recommendation(
        agent_input=agent_input, now=now,
    )

    # 5) 4-04 regime filter 의 demote 결과를 v2 entries 에 *재반영* — 4-04 가
    #    blocked 으로 분류한 전략은 v2 excluded 로 이동 (4-04 우선).
    regime_blocked_set = set(regime_report.blocked_strategies)
    regime_watchlist_set = set(regime_report.watchlist_strategies)
    # 새 bucket 으로 redistribute.
    recommended_after: list[PaperStrategyEntry] = []
    watchlist_after:   list[PaperStrategyEntry] = list(v2_combo.watchlist_strategies)
    excluded_after:    list[PaperStrategyEntry] = list(v2_combo.excluded_strategies)
    overfit_keys: set[tuple] = set()
    for w in overfit_report.warnings:
        if w.overfit_flag:
            overfit_keys.add((
                w.strategy, w.symbol,
                tuple(sorted((str(k), str(v)) for k, v in (w.params or {}).items())),
            ))
    for e in v2_combo.recommended_strategies:
        key = _entry_key(e)
        # 4-03 OVERFIT_RISK → 무조건 excluded (4-03 우선).
        if key in overfit_keys:
            excluded_after.append(e)
            continue
        # 4-04 regime blocked → excluded.
        if e.strategy in regime_blocked_set:
            excluded_after.append(e)
            continue
        # 4-04 regime watchlist → watchlist.
        if e.strategy in regime_watchlist_set:
            watchlist_after.append(e)
            continue
        recommended_after.append(e)

    # 6) 전략별 explanation 생성.
    recommended_exps = [
        _build_strategy_explanation(
            entry=e, bucket="recommended",
            overfit_report=overfit_report, regime_report=regime_report,
        )
        for e in recommended_after
    ]
    watchlist_exps = [
        _build_strategy_explanation(
            entry=e, bucket="watchlist",
            overfit_report=overfit_report, regime_report=regime_report,
        )
        for e in watchlist_after
    ]
    excluded_exps = [
        _build_strategy_explanation(
            entry=e, bucket="excluded",
            overfit_report=overfit_report, regime_report=regime_report,
        )
        for e in excluded_after
    ]

    # 7) verdict 계산 — *redistribute 결과 반영*.
    overfit_count = overfit_report.overfit_count

    # combo_for_verdict 는 redistribute 결과 반영.
    # 빈 recommended 이면 모두 보류/차단 → 상태 재계산.
    if not recommended_after and (watchlist_after or excluded_after):
        # 4-02 v2 의 status 를 재해석 — recommended 0 이지만 watchlist 있으면 WATCH_ONLY
        if watchlist_after:
            effective_status = PaperCombinationStatus.WATCH_ONLY
        else:
            effective_status = PaperCombinationStatus.REJECTED_BY_RISK
    else:
        effective_status = v2_combo.status
    # frozen replace
    combo_for_verdict = PaperStrategyCombination(
        generated_at=v2_combo.generated_at,
        status=effective_status,
        recommended_strategies=recommended_after,
        excluded_strategies=excluded_after,
        watchlist_strategies=watchlist_after,
        no_candidate_reason=v2_combo.no_candidate_reason,
        risk_summary=list(v2_combo.risk_summary),
        agent_rationale=v2_combo.agent_rationale,
        operator_next_action=list(v2_combo.operator_next_action),
        metadata=dict(v2_combo.metadata),
    )

    verdict, can_start, blocking_reasons = _compute_verdict(
        pre_market=pre_market,
        regime=regime_report,
        combo=combo_for_verdict,
        overfit_count=overfit_count,
    )

    # 8) Headline + risk_summary + next_actions.
    headline = _build_headline(verdict, combo_for_verdict)
    next_actions = _build_next_actions(verdict, blocking_reasons)

    # risk_summary — 추천 entry 의 risk_flags + regime risk_flags + overfit 카운트.
    risk_summary: list[str] = []
    seen: set[str] = set()
    for exp in recommended_exps + watchlist_exps:
        for flag in exp.risk_flags:
            base = flag.split(" (")[0]
            if base not in seen:
                seen.add(base)
                risk_summary.append(base)
    for flag in regime_report.risk_flags:
        if flag not in seen:
            seen.add(flag)
            risk_summary.append(flag)
    if overfit_count > 0:
        risk_summary.append(f"overfit_risk_count={overfit_count}")

    operator_note = (
        regime_report.operator_note or
        "AI Paper 모의매매 advisory — broker 호출 / 자동 시작 0건."
    )

    overfit_strategies = sorted({
        f"{w.strategy}/{w.symbol}"
        for w in overfit_report.warnings if w.overfit_flag
    })

    return PaperStartExplanation(
        generated_at=now.isoformat(),
        schema_version=EXPLANATION_SCHEMA_VERSION,
        verdict=verdict,
        recommended_explanations=recommended_exps,
        watchlist_explanations=watchlist_exps,
        excluded_explanations=excluded_exps,
        market_regime=regime_report.regime.value,
        regime_confidence=float(regime_report.confidence),
        regime_reasons=list(regime_report.reasons),
        regime_risk_flags=list(regime_report.risk_flags),
        regime_allowed_tactics=list(regime_report.allowed_strategies),
        regime_blocked_tactics=list(regime_report.blocked_strategies),
        overfit_count=overfit_count,
        overfit_strategies=overfit_strategies,
        headline=headline,
        risk_summary=risk_summary,
        operator_note=operator_note,
        next_actions=next_actions,
        can_start_paper=can_start,
        blocking_reasons=blocking_reasons,
        metadata={
            "pipeline":                   "step4-05-paper-start-explanation",
            "v1_recommended_count":       len(v1_after_regime.recommended_combo),
            "v1_held_count":              v1_after_regime.held_count,
            "v1_excluded_count":          v1_after_regime.excluded_count,
            "v2_recommended_count":       len(recommended_after),
            "v2_watchlist_count":         len(watchlist_after),
            "v2_excluded_count":          len(excluded_after),
            "source_item_count":          agent_input.item_count,
        },
    )


__all__ = [
    "EXPLANATION_SCHEMA_VERSION",
    "ExplanationVerdict",
    "PaperStartExplanation",
    "PreMarketSummary",
    "StrategyExplanation",
    "build_paper_start_explanation",
]

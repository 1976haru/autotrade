"""#4-09: Risk veto priority — AI 추천보다 *위험 거절* 이 항상 우선.

본 모듈은 4-07 `bridge_explanation_to_paper_decisions` 가 PaperDecision 을
만들기 *전*에 호출되는 *결정론적 veto 평가기*. RiskOfficer / Pre-market /
risk_flags 가 거절하면 AI 추천이 아무리 좋아도 BUY/SELL/EXIT 가 *생성되지
않는다* — 대신 HOLD/NO_OP audit log 만 남긴다.

## 우선순위 (위에서 아래로 — 첫 trigger 가 즉시 veto 결정)

```
1. EMERGENCY_STOP                  → 모든 trade action 차단 (EXIT 포함)
2. Pre-market BLOCK / DO_NOT_START → 모든 trade action 차단 (EXIT 포함)
3. RiskOfficer REJECT              → BUY/SELL 차단, EXIT 는 보유 시 허용
4. risk_flag : stale_data          → BUY/SELL 차단, EXIT 는 보유 시 허용
   risk_flag : duplicate_signal    → 위와 동일
   risk_flag : high_correlation    → 위와 동일
   risk_flag : overfit_risk        → 위와 동일
   risk_flag : low_liquidity       → 위와 동일
5. (veto 없음) → AI 추천 진행
```

`BLOCK` (1~2) 와 `BLOCK_NEW_ENTRY` (3~4) 두 단계로 분리한다 — EXIT 은 *위험
축소* 목적이라 RiskOfficer / risk_flags 만 있을 때는 보유 포지션에 한해 허용,
다만 EMERGENCY_STOP / Pre-market BLOCK 일 때는 신규 EXIT 도 차단 (운영자가
명시 청산 흐름을 거치도록).

## 절대 invariant (CLAUDE.md 절대 원칙 1~5 상속)

1. **본 모듈은 *advisory veto evaluator*** — broker / OrderExecutor /
   route_order 호출 0건.
2. `RiskVetoDecision.is_order_signal=False` / `auto_apply_allowed=False` /
   `is_live_authorization=False` 불변.
3. 외부 HTTP / AI SDK / LLM import 0건.
4. DB write 0건.
5. **AI 추천이 강해도 veto 가 활성이면 BUY/SELL 0건** — 영구 lock.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import StrEnum
from typing import Any

from app.agents.paper_start_explanation import (
    ExplanationVerdict,
    PaperStartExplanation,
    StrategyExplanation,
)


RISK_VETO_SCHEMA_VERSION = "1.0"


# ─────────────────────────────────────────────────────────────────────────────
# Reason / Severity enums — *주문 방향 0개*
# ─────────────────────────────────────────────────────────────────────────────


class RiskVetoReason(StrEnum):
    """위험 거절 사유 — *주문 방향 (BUY/SELL/HOLD) 0개*."""
    EMERGENCY_STOP       = "EMERGENCY_STOP"
    PRE_MARKET_BLOCK     = "PRE_MARKET_BLOCK"
    RISK_OFFICER_REJECT  = "RISK_OFFICER_REJECT"
    STALE_DATA           = "STALE_DATA"
    DUPLICATE_SIGNAL     = "DUPLICATE_SIGNAL"
    HIGH_CORRELATION     = "HIGH_CORRELATION"
    OVERFIT_RISK         = "OVERFIT_RISK"
    LOW_LIQUIDITY        = "LOW_LIQUIDITY"


class RiskVetoSeverity(StrEnum):
    """veto 강도 — EXIT 허용 여부 결정."""
    NONE              = "NONE"
    BLOCK_NEW_ENTRY   = "BLOCK_NEW_ENTRY"   # BUY/SELL 차단, EXIT 는 보유 시 허용
    BLOCK             = "BLOCK"             # BUY/SELL/EXIT 모두 차단


# `risk_flags` 문자열 → RiskVetoReason 매핑 (대소문자 무시).
_RISK_FLAG_REASON_MAP: dict[str, RiskVetoReason] = {
    "stale_data":         RiskVetoReason.STALE_DATA,
    "stale":              RiskVetoReason.STALE_DATA,
    "duplicate_signal":   RiskVetoReason.DUPLICATE_SIGNAL,
    "duplicate":          RiskVetoReason.DUPLICATE_SIGNAL,
    "high_correlation":   RiskVetoReason.HIGH_CORRELATION,
    "correlation":        RiskVetoReason.HIGH_CORRELATION,
    "overfit_risk":       RiskVetoReason.OVERFIT_RISK,
    "overfit":            RiskVetoReason.OVERFIT_RISK,
    "low_liquidity":      RiskVetoReason.LOW_LIQUIDITY,
    "liquidity":          RiskVetoReason.LOW_LIQUIDITY,
    "risk_officer_reject": RiskVetoReason.RISK_OFFICER_REJECT,
    "risk_officer":       RiskVetoReason.RISK_OFFICER_REJECT,
}


_REASON_LABEL_KO: dict[RiskVetoReason, str] = {
    RiskVetoReason.EMERGENCY_STOP:      "긴급정지 활성 — 모든 신규 주문 차단",
    RiskVetoReason.PRE_MARKET_BLOCK:    "장 시작 전 점검 차단 — DO_NOT_START",
    RiskVetoReason.RISK_OFFICER_REJECT: "RiskOfficer 거절",
    RiskVetoReason.STALE_DATA:          "시세 데이터 오래됨 (stale data)",
    RiskVetoReason.DUPLICATE_SIGNAL:    "중복 신호 (duplicate)",
    RiskVetoReason.HIGH_CORRELATION:    "포트폴리오 상관관계 과다",
    RiskVetoReason.OVERFIT_RISK:        "과최적화 의심 (overfit)",
    RiskVetoReason.LOW_LIQUIDITY:       "거래대금 부족 (low liquidity)",
}


# 우선순위 — 인덱스가 작을수록 강함.
_REASON_PRIORITY: list[RiskVetoReason] = [
    RiskVetoReason.EMERGENCY_STOP,
    RiskVetoReason.PRE_MARKET_BLOCK,
    RiskVetoReason.RISK_OFFICER_REJECT,
    RiskVetoReason.STALE_DATA,
    RiskVetoReason.DUPLICATE_SIGNAL,
    RiskVetoReason.HIGH_CORRELATION,
    RiskVetoReason.OVERFIT_RISK,
    RiskVetoReason.LOW_LIQUIDITY,
]


_BLOCK_ALL_REASONS: set[RiskVetoReason] = {
    RiskVetoReason.EMERGENCY_STOP,
    RiskVetoReason.PRE_MARKET_BLOCK,
}


# #4-RiskProfileApply: 항상 차단되는 reasons — risk_profile threshold 와
# *무관* 하게 신규 진입 차단.
_ALWAYS_BLOCK_REASONS: set[RiskVetoReason] = {
    RiskVetoReason.EMERGENCY_STOP,
    RiskVetoReason.PRE_MARKET_BLOCK,
    RiskVetoReason.RISK_OFFICER_REJECT,
}

# flag-derived reasons — risk_profile.max_flags 임계값으로 *완화* 가능한 set.
_FLAG_DERIVED_REASONS: set[RiskVetoReason] = {
    RiskVetoReason.STALE_DATA,
    RiskVetoReason.DUPLICATE_SIGNAL,
    RiskVetoReason.HIGH_CORRELATION,
    RiskVetoReason.OVERFIT_RISK,
    RiskVetoReason.LOW_LIQUIDITY,
}


# ─────────────────────────────────────────────────────────────────────────────
# Dataclasses
# ─────────────────────────────────────────────────────────────────────────────


@dataclass(frozen=True)
class RiskVetoDecision:
    """단일 전략/심볼에 대한 veto 결정 — *advisory*."""
    strategy:              str
    symbol:                str
    vetoed:                bool
    reasons:               list[RiskVetoReason]   = field(default_factory=list)
    severity:              RiskVetoSeverity       = RiskVetoSeverity.NONE
    allow_exit_if_holding: bool                   = True
    detail_lines:          list[str]              = field(default_factory=list)

    # 절대 invariant.
    is_order_signal:       bool = False
    auto_apply_allowed:    bool = False
    is_live_authorization: bool = False

    def __post_init__(self) -> None:
        for name, val in (
            ("is_order_signal",       self.is_order_signal),
            ("auto_apply_allowed",    self.auto_apply_allowed),
            ("is_live_authorization", self.is_live_authorization),
        ):
            if val is not False:
                raise ValueError(f"RiskVetoDecision.{name} must be False.")
        if not isinstance(self.severity, RiskVetoSeverity):
            raise ValueError("severity must be RiskVetoSeverity.")
        if self.vetoed and self.severity == RiskVetoSeverity.NONE:
            raise ValueError("vetoed=True requires severity != NONE.")
        if not self.vetoed and self.reasons:
            raise ValueError("vetoed=False must have empty reasons list.")
        for r in self.reasons:
            if not isinstance(r, RiskVetoReason):
                raise ValueError("reasons must contain RiskVetoReason enum.")

    def to_dict(self) -> dict[str, Any]:
        return {
            "strategy":              self.strategy,
            "symbol":                self.symbol,
            "vetoed":                bool(self.vetoed),
            "reasons":               [r.value for r in self.reasons],
            "reasons_label_ko":      [_REASON_LABEL_KO[r] for r in self.reasons],
            "severity":              self.severity.value,
            "allow_exit_if_holding": bool(self.allow_exit_if_holding),
            "detail_lines":          list(self.detail_lines),
            "is_order_signal":       False,
            "auto_apply_allowed":    False,
            "is_live_authorization": False,
        }


@dataclass(frozen=True)
class RiskVetoReport:
    """전체 veto 평가 결과 — bridge metadata 에 carry."""
    generated_at:           str
    schema_version:         str
    loop_state:             str
    explanation_verdict:    str

    # 모든 전략에 영향을 주는 *global* veto (EMERGENCY_STOP / Pre-market BLOCK).
    has_global_veto:        bool                       = False
    global_veto_reasons:    list[RiskVetoReason]       = field(default_factory=list)
    global_severity:        RiskVetoSeverity           = RiskVetoSeverity.NONE

    # 전략별 결정.
    decisions:              list[RiskVetoDecision]     = field(default_factory=list)

    # 사유별 카운트.
    summary:                dict[str, int]             = field(default_factory=dict)

    headline:               str = ""

    advisory_disclaimer:    str = (
        "본 veto 는 *advisory* — 실거래 주문이 아니며 자동 paper trader 흐름의 "
        "BUY/SELL/EXIT 후보 생성을 *코드 단*에서 차단합니다. "
        "is_order_signal=False / auto_apply_allowed=False / is_live_authorization=False."
    )

    # 절대 invariant.
    is_order_signal:       bool = False
    auto_apply_allowed:    bool = False
    is_live_authorization: bool = False

    def __post_init__(self) -> None:
        for name, val in (
            ("is_order_signal",       self.is_order_signal),
            ("auto_apply_allowed",    self.auto_apply_allowed),
            ("is_live_authorization", self.is_live_authorization),
        ):
            if val is not False:
                raise ValueError(f"RiskVetoReport.{name} must be False.")
        if not isinstance(self.global_severity, RiskVetoSeverity):
            raise ValueError("global_severity must be RiskVetoSeverity.")
        if self.has_global_veto and not self.global_veto_reasons:
            raise ValueError("has_global_veto=True requires non-empty reasons.")

    def to_dict(self) -> dict[str, Any]:
        return {
            "generated_at":         self.generated_at,
            "schema_version":       self.schema_version,
            "loop_state":           self.loop_state,
            "explanation_verdict":  self.explanation_verdict,
            "has_global_veto":      bool(self.has_global_veto),
            "global_veto_reasons":  [r.value for r in self.global_veto_reasons],
            "global_severity":      self.global_severity.value,
            "decisions":            [d.to_dict() for d in self.decisions],
            "decision_count":       len(self.decisions),
            "vetoed_count":         sum(1 for d in self.decisions if d.vetoed),
            "summary":              dict(self.summary),
            "headline":             self.headline,
            "advisory_disclaimer":  self.advisory_disclaimer,
            "is_order_signal":      False,
            "auto_apply_allowed":   False,
            "is_live_authorization": False,
        }


# ─────────────────────────────────────────────────────────────────────────────
# Helpers
# ─────────────────────────────────────────────────────────────────────────────


def _normalize_flag(raw: str) -> str:
    return (raw or "").strip().lower().replace("-", "_").replace(" ", "_")


def _flag_to_reason(flag: str) -> RiskVetoReason | None:
    return _RISK_FLAG_REASON_MAP.get(_normalize_flag(flag))


def _sort_reasons(reasons: list[RiskVetoReason]) -> list[RiskVetoReason]:
    """우선순위 순서로 정렬 — 중복 제거."""
    seen: set[RiskVetoReason] = set()
    ordered: list[RiskVetoReason] = []
    for r in _REASON_PRIORITY:
        if r in reasons and r not in seen:
            ordered.append(r)
            seen.add(r)
    return ordered


def _severity_for_reasons(reasons: list[RiskVetoReason]) -> RiskVetoSeverity:
    if any(r in _BLOCK_ALL_REASONS for r in reasons):
        return RiskVetoSeverity.BLOCK
    if reasons:
        return RiskVetoSeverity.BLOCK_NEW_ENTRY
    return RiskVetoSeverity.NONE


def _build_decision(
    exp:              StrategyExplanation,
    *,
    global_reasons:   list[RiskVetoReason],
    officer_reason:   str | None,
    extra_flags:      list[str],
    risk_veto_max_flags: int = 0,
) -> RiskVetoDecision:
    """단일 전략에 대한 veto 결정 — global + entry-level 통합.

    #4-RiskProfileApply: `risk_veto_max_flags` 가 N > 0 이면 *flag-derived*
    reasons (STALE_DATA / DUPLICATE_SIGNAL / HIGH_CORRELATION / OVERFIT_RISK /
    LOW_LIQUIDITY) 가 N개 이하인 경우 *완화* (BLOCK 안 함). RiskOfficer /
    EMERGENCY_STOP / PRE_MARKET_BLOCK 는 항상 BLOCK — 본 threshold 의 영향 X.
    `risk_veto_max_flags=0` (default) → 기존 동작 그대로 (1개라도 차단).
    """
    reasons: list[RiskVetoReason] = list(global_reasons)
    detail: list[str] = []

    # RiskOfficer reject — caller 가 명시 전달.
    if officer_reason:
        if RiskVetoReason.RISK_OFFICER_REJECT not in reasons:
            reasons.append(RiskVetoReason.RISK_OFFICER_REJECT)
        detail.append(f"RiskOfficer: {officer_reason}")

    # entry.risk_flags 매핑.
    for raw in list(exp.risk_flags) + list(extra_flags):
        rsn = _flag_to_reason(raw)
        if rsn is None:
            continue
        if rsn not in reasons:
            reasons.append(rsn)
        detail.append(f"risk_flag: {raw}")

    # overfit_verdict 명시도 OVERFIT_RISK 로 인식 — 4-03 carry.
    if exp.overfit_verdict and exp.overfit_verdict.upper() == "OVERFIT_RISK":
        if RiskVetoReason.OVERFIT_RISK not in reasons:
            reasons.append(RiskVetoReason.OVERFIT_RISK)
        detail.append("overfit_verdict=OVERFIT_RISK")

    # #4-RiskProfileApply: flag-derived reasons 가 threshold 이하면 *완화*.
    # always-block reasons (EMERGENCY_STOP / PRE_MARKET_BLOCK / RISK_OFFICER)
    # 는 본 완화의 영향을 받지 *않는다*.
    flag_derived_count = sum(1 for r in reasons if r in _FLAG_DERIVED_REASONS)
    has_always_block = any(r in _ALWAYS_BLOCK_REASONS for r in reasons)
    if (not has_always_block
            and flag_derived_count > 0
            and flag_derived_count <= int(risk_veto_max_flags)):
        # flag-derived reasons 만 있고 *허용 한도 이내* → veto 미발생.
        # 단 reasons / detail 은 carry — 운영자가 사유를 *볼 수* 있어야 함.
        detail.append(
            f"risk_profile relaxed: {flag_derived_count} flag(s) "
            f"<= max={int(risk_veto_max_flags)}"
        )
        return RiskVetoDecision(
            strategy=exp.strategy, symbol=exp.symbol,
            vetoed=False, reasons=[],
            severity=RiskVetoSeverity.NONE,
            allow_exit_if_holding=True,
            detail_lines=detail,
        )

    ordered = _sort_reasons(reasons)
    severity = _severity_for_reasons(ordered)
    vetoed = bool(ordered)
    allow_exit = (severity == RiskVetoSeverity.BLOCK_NEW_ENTRY)

    if not vetoed:
        return RiskVetoDecision(
            strategy=exp.strategy, symbol=exp.symbol,
            vetoed=False, reasons=[],
            severity=RiskVetoSeverity.NONE,
            allow_exit_if_holding=True,
            detail_lines=[],
        )

    return RiskVetoDecision(
        strategy=exp.strategy, symbol=exp.symbol,
        vetoed=True, reasons=ordered,
        severity=severity,
        allow_exit_if_holding=allow_exit,
        detail_lines=detail,
    )


# ─────────────────────────────────────────────────────────────────────────────
# Main entry — evaluate_risk_veto
# ─────────────────────────────────────────────────────────────────────────────


def evaluate_risk_veto(
    *,
    explanation:           PaperStartExplanation,
    loop_state:            str,
    risk_officer_rejects:  dict[tuple[str, str], str] | None = None,
    extra_risk_flags:      dict[tuple[str, str], list[str]] | None = None,
    risk_veto_max_flags:   int = 0,
    now:                   datetime | None = None,
) -> RiskVetoReport:
    """`PaperStartExplanation` + loop_state + RiskOfficer rejects → veto 평가.

    *broker 호출 0건* — 결정론적 평가.

    Args:
        explanation: 4-05 통합 설명 (verdict / risk_flags 사용).
        loop_state: AutoPaperLoop 상태 (`EMERGENCY_STOP` → global veto).
        risk_officer_rejects: `(strategy, symbol) → reason 문자열`.
        extra_risk_flags: 추가 flag carry — UI / caller 가 명시 (예: KIS
            stale-data 감지 결과). 4-05 의 entry.risk_flags 와 *합집합*.
        risk_veto_max_flags: AI 운용 성향 임계값 (#4-RiskProfileApply).
            flag-derived reasons (STALE_DATA / DUPLICATE_SIGNAL /
            HIGH_CORRELATION / OVERFIT_RISK / LOW_LIQUIDITY) 가 이 값 이하면
            *완화* (veto 미발생). always-block reasons (EMERGENCY_STOP /
            PRE_MARKET_BLOCK / RISK_OFFICER_REJECT) 는 영향 X. default=0
            (기존 동작 — 1개라도 차단).
    """
    if now is None:
        now = datetime.now(timezone.utc)

    officer_map: dict[tuple[str, str], str] = dict(risk_officer_rejects or {})
    extra_map:   dict[tuple[str, str], list[str]] = dict(extra_risk_flags or {})

    # 1. global veto 계산 — 모든 strategy 에 적용.
    global_reasons: list[RiskVetoReason] = []
    if loop_state == "EMERGENCY_STOP":
        global_reasons.append(RiskVetoReason.EMERGENCY_STOP)
    if explanation.verdict == ExplanationVerdict.DO_NOT_START:
        global_reasons.append(RiskVetoReason.PRE_MARKET_BLOCK)
    global_reasons = _sort_reasons(global_reasons)
    global_severity = _severity_for_reasons(global_reasons)

    # 2. 전략별 결정.
    all_entries: list[StrategyExplanation] = (
        list(explanation.recommended_explanations)
        + list(explanation.watchlist_explanations)
        + list(explanation.excluded_explanations)
    )

    decisions: list[RiskVetoDecision] = []
    for exp in all_entries:
        key = (exp.strategy, exp.symbol)
        officer_reason = officer_map.get(key)
        extra_flags = extra_map.get(key, [])
        decisions.append(
            _build_decision(
                exp,
                global_reasons=global_reasons,
                officer_reason=officer_reason,
                extra_flags=extra_flags,
                risk_veto_max_flags=int(risk_veto_max_flags),
            ),
        )

    # 3. 사유별 카운트.
    summary: dict[str, int] = {}
    for d in decisions:
        for r in d.reasons:
            summary[r.value] = summary.get(r.value, 0) + 1

    # 4. headline.
    if global_reasons:
        labels = ", ".join(_REASON_LABEL_KO[r] for r in global_reasons)
        headline = f"Risk veto 우선 — {labels} (Paper 주문 후보 생성 안 됨)"
    elif decisions and any(d.vetoed for d in decisions):
        n = sum(1 for d in decisions if d.vetoed)
        headline = f"Risk veto 우선 — {n}개 전략 차단 (HOLD/NO_OP 만 기록)"
    else:
        headline = "Risk veto 없음 — AI 추천 흐름 진행"

    return RiskVetoReport(
        generated_at=now.isoformat(),
        schema_version=RISK_VETO_SCHEMA_VERSION,
        loop_state=loop_state,
        explanation_verdict=explanation.verdict.value,
        has_global_veto=bool(global_reasons),
        global_veto_reasons=global_reasons,
        global_severity=global_severity,
        decisions=decisions,
        summary=summary,
        headline=headline,
    )


__all__ = [
    "RISK_VETO_SCHEMA_VERSION",
    "RiskVetoReason",
    "RiskVetoSeverity",
    "RiskVetoDecision",
    "RiskVetoReport",
    "evaluate_risk_veto",
]

"""#4-07: AI Agent 추천 → Paper Decision 연결 bridge.

4-05 `PaperStartExplanation` (4-01~4-04 통합) 결과 + 현재 가상 포지션 상태를
입력으로 받아, 2-10 `PaperDecision` (BUY/SELL/HOLD/EXIT/NO_OP) 으로 변환하고
2-09 Paper ledger 에 *advisory* event 로 기록한다.

## 핵심 정책 — 실제 주문 0건

본 bridge 는 **broker / OrderExecutor / route_order 를 호출하지 않는다**:
- 변환 단계 (recommended → BUY): `convert_to_paper_decision()` (2-10) 만 호출
- 기록 단계 (ledger): `record_paper_event()` (2-09) 만 호출
- 실 broker 호출은 *영구 불가* — `PaperDecision.is_order_signal=False` 양 끝 lock

## Gating 매트릭스 (사용자 spec)

| 조건 | 결과 |
|---|---|
| `loop_state != RUNNING` | 모든 trade action (BUY/SELL/EXIT) **차단**, HOLD/NO_OP audit 만 |
| `loop_state == EMERGENCY_STOP` | **모든 action 차단** (HOLD/NO_OP 포함) |
| `explanation.verdict == DO_NOT_START` | 모든 action 차단 (blocking_reasons 표시만) |
| 4-05 verdict 별 처리 | READY_TO_REVIEW / REVIEW_WITH_WARNING → trade 가능 |
| | HOLD / INSUFFICIENT_DATA → HOLD/NO_OP audit 만 |
| recommended_strategies | (pos=0) → BUY / (pos>0) → HOLD |
| watchlist_strategies (보유 중 + exit hint) | (pos>0) → EXIT / (pos=0) → HOLD |
| excluded_strategies (OVERFIT_RISK / STRESS / REJECT) | NO_OP audit (rationale carry, broker 호출 0건) |

4-04 LOW_LIQUIDITY / UNKNOWN / 4-03 OVERFIT_RISK 는 *4-05 시점에서 이미*
`verdict=DO_NOT_START` 또는 entry 가 excluded 로 분류 → 본 bridge 가 BUY 생성
*불가능*.

## 절대 invariant (CLAUDE.md 절대 원칙 1~5 상속)

1. `is_order_signal=False` / `auto_apply_allowed=False` /
   `is_live_authorization=False` carry 양 레벨 (decision + result)
2. broker / OrderExecutor / route_order import 0건 (정적 grep)
3. 외부 HTTP / AI SDK import 0건
4. EMERGENCY_STOP 차단 — `loop_state="EMERGENCY_STOP"` 시 ledger 도 손대지 않음
5. DB write 0건 — in-memory ledger 만
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

from app.agents.paper_start_explanation import (
    ExplanationVerdict,
    PaperStartExplanation,
    StrategyExplanation,
)
from app.auto_paper.decisions import (
    AIDirection,
    AIRecommendationInput,
    DEFAULT_VIRTUAL_TRADE_SIZE,
    PaperDecision,
    process_ai_recommendation,
)
from app.auto_paper.events import (
    DecisionAction,
    PaperFillStatus,
    PaperLoopEvent,
)
from app.auto_paper.ledger import LedgerStateError


BRIDGE_SCHEMA_VERSION = "1.0"


_TRADE_LOOP_STATE = "RUNNING"
_EMERGENCY_LOOP_STATE = "EMERGENCY_STOP"


@dataclass(frozen=True)
class PositionSnapshot:
    """현재 가상 포지션 — bridge 입력.

    *secret 필드 0건* — API key / 계좌번호 carry 0개 (테스트 lock).
    """
    strategy:        str
    symbol:          str
    quantity:        int                  = 0    # +/- 가상 보유
    exit_condition:  bool                 = False  # exit hint (watchlist + holding)


@dataclass(frozen=True)
class BridgeReport:
    """bridge 결과 — 변환된 PaperDecision list + 기록 통계.

    *주문 신호가 아니다* — `is_order_signal=False` 불변.
    """

    generated_at:        str
    schema_version:      str
    loop_state:          str
    explanation_verdict: str
    decisions:           list[PaperDecision]    = field(default_factory=list)
    events_recorded:     int                    = 0
    events_blocked:      int                    = 0
    block_reasons:       list[str]              = field(default_factory=list)
    summary:             str                    = ""
    advisory_disclaimer: str = (
        "본 결과는 *advisory* — Paper 가상 체결만, 실 broker 호출 0건. "
        "is_order_signal=False / auto_apply_allowed=False / is_live_authorization=False."
    )
    metadata:            dict[str, Any]         = field(default_factory=dict)

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
                raise ValueError(f"BridgeReport.{name} must be False.")
        if not isinstance(self.advisory_disclaimer, str) or not self.advisory_disclaimer:
            raise ValueError("advisory_disclaimer must be non-empty.")

    def to_dict(self) -> dict[str, Any]:
        return {
            "generated_at":        self.generated_at,
            "schema_version":      self.schema_version,
            "loop_state":          self.loop_state,
            "explanation_verdict": self.explanation_verdict,
            "decisions":           [d.to_dict() for d in self.decisions],
            "decision_count":      len(self.decisions),
            "events_recorded":     int(self.events_recorded),
            "events_blocked":      int(self.events_blocked),
            "block_reasons":       list(self.block_reasons),
            "summary":             self.summary,
            "advisory_disclaimer": self.advisory_disclaimer,
            "metadata":            dict(self.metadata),
            "is_order_signal":       False,
            "auto_apply_allowed":    False,
            "is_live_authorization": False,
        }


# ─────────────────────────────────────────────────────────────────────────────
# Helpers — verdict / position lookups
# ─────────────────────────────────────────────────────────────────────────────


def _verdict_allows_trade(verdict_str: str) -> bool:
    """4-05 verdict 가 trade action 을 허용하는지.

    READY_TO_REVIEW / REVIEW_WITH_WARNING 만 trade 가능. 그 외는 audit only.
    """
    return verdict_str in (
        ExplanationVerdict.READY_TO_REVIEW.value,
        ExplanationVerdict.REVIEW_WITH_WARNING.value,
    )


def _find_position(
    positions: list[PositionSnapshot],
    strategy:  str,
    symbol:    str,
) -> PositionSnapshot | None:
    for p in positions:
        if p.strategy == strategy and p.symbol == symbol:
            return p
    return None


def _explanation_to_direction(
    exp:        StrategyExplanation,
    position:   PositionSnapshot | None,
    *,
    allow_trade: bool,
) -> str:
    """단일 StrategyExplanation + 포지션 → AI direction.

    * 4-05 bucket 별 매핑:
      - recommended (pos=0): BUY (allow_trade=True 일 때만 실제 BUY, 아니면 HOLD)
      - recommended (pos>0): HOLD (이미 보유)
      - watchlist + exit_condition (pos>0): EXIT
      - watchlist (그 외): HOLD
      - excluded: NO_OP (audit only)
    """
    pos = int(position.quantity) if position is not None else 0
    exit_hint = bool(position.exit_condition) if position is not None else False

    if exp.bucket == "recommended":
        if pos > 0:
            return AIDirection.HOLD
        return AIDirection.BUY if allow_trade else AIDirection.HOLD

    if exp.bucket == "watchlist":
        if exit_hint and pos > 0:
            return AIDirection.EXIT if allow_trade else AIDirection.HOLD
        return AIDirection.HOLD

    # excluded — audit only (broker 호출 0건). PaperDecision NO_OP 로 변환.
    return AIDirection.NO_OP


def _bucket_reason(exp: StrategyExplanation) -> str:
    """rationale_lines 첫 줄 + bucket 라벨."""
    bucket_label = {
        "recommended": "추천",
        "watchlist":   "보류",
        "excluded":    "제외",
    }.get(exp.bucket, exp.bucket)
    rationale = (exp.rationale_lines[0] if exp.rationale_lines else "advisory")
    return f"[{bucket_label}] {rationale}"


# ─────────────────────────────────────────────────────────────────────────────
# Main bridge
# ─────────────────────────────────────────────────────────────────────────────


def bridge_explanation_to_paper_decisions(
    *,
    explanation:        PaperStartExplanation,
    loop_state:         str,
    positions:          list[PositionSnapshot] | None = None,
    virtual_trade_size: int                           = DEFAULT_VIRTUAL_TRADE_SIZE,
    auto_fill:          bool                          = True,
    record:             bool                          = True,
    now:                datetime | None               = None,
) -> BridgeReport:
    """4-05 explanation + 가상 포지션 + loop state → PaperDecision list.

    *broker 호출 0건* — 변환 + ledger 기록만.

    Gating:
    - `loop_state == EMERGENCY_STOP` → 모든 action 차단 (ledger 손대지 않음)
    - `loop_state != RUNNING` → trade action 차단, HOLD/NO_OP audit 만
    - `explanation.verdict == DO_NOT_START` → 모든 action 차단
    - 그 외 verdict (HOLD / INSUFFICIENT_DATA): HOLD/NO_OP audit 만
    - READY_TO_REVIEW / REVIEW_WITH_WARNING + RUNNING: trade 가능
    """
    if now is None:
        now = datetime.now(timezone.utc)
    pos_list: list[PositionSnapshot] = list(positions or [])

    decisions:        list[PaperDecision] = []
    events_recorded:  int = 0
    events_blocked:   int = 0
    block_reasons:    list[str] = []

    # 1. EMERGENCY_STOP — 어떤 변환 / 기록도 수행하지 않음.
    if loop_state == _EMERGENCY_LOOP_STATE:
        block_reasons.append(
            "loop_state=EMERGENCY_STOP — 모든 action 차단 (변환 0건, 기록 0건)"
        )
        return BridgeReport(
            generated_at=now.isoformat(),
            schema_version=BRIDGE_SCHEMA_VERSION,
            loop_state=loop_state,
            explanation_verdict=explanation.verdict.value,
            decisions=[],
            events_recorded=0,
            events_blocked=0,    # 시도 자체를 안 함 — 차단 카운트도 0
            block_reasons=block_reasons,
            summary="EMERGENCY_STOP — 모든 AI Paper 판단 변환 / 기록 영구 차단.",
            metadata={"input_entries": _count_explanation_entries(explanation)},
        )

    # 2. explanation.verdict 가 DO_NOT_START → 모든 trade 차단.
    verdict_allows_trade = _verdict_allows_trade(explanation.verdict.value)
    if explanation.verdict == ExplanationVerdict.DO_NOT_START:
        block_reasons.append(
            f"explanation.verdict=DO_NOT_START — trade action 차단 "
            f"(blocking_reasons={explanation.blocking_reasons})"
        )

    # 3. loop_state 가 RUNNING 이 아니면 trade action 차단 (HOLD/NO_OP 만 기록).
    allow_trade = (
        loop_state == _TRADE_LOOP_STATE and verdict_allows_trade
    )
    if loop_state != _TRADE_LOOP_STATE:
        block_reasons.append(
            f"loop_state={loop_state} ≠ RUNNING — trade action 차단 (audit only)"
        )

    # 4. 모든 explanation entry 순회 → bridge 변환.
    all_entries: list[StrategyExplanation] = (
        list(explanation.recommended_explanations)
        + list(explanation.watchlist_explanations)
        + list(explanation.excluded_explanations)
    )

    for exp in all_entries:
        position = _find_position(pos_list, exp.strategy, exp.symbol)
        direction = _explanation_to_direction(
            exp, position, allow_trade=allow_trade,
        )
        rec = AIRecommendationInput(
            strategy=exp.strategy,
            symbol=exp.symbol,
            direction=direction,
            reason=_bucket_reason(exp),
            confidence=None,
            risk_flags=list(exp.risk_flags),
            params={},
            current_position=int(position.quantity) if position else 0,
            metadata={
                "bridge_bucket":          exp.bucket,
                "paper_candidate_status": exp.paper_candidate_status,
                "overfit_verdict":        exp.overfit_verdict or "",
                "regime_policy_role":     exp.regime_policy_role or "",
            },
        )
        try:
            decision, _event = process_ai_recommendation(
                rec,
                loop_state=loop_state,
                virtual_trade_size=int(virtual_trade_size),
                auto_fill=bool(auto_fill),
                record=record,
            )
            decisions.append(decision)
            if _event is not None:
                events_recorded += 1
        except LedgerStateError as e:
            # ledger 가 차단 (trade event + non-RUNNING) — block 카운트 증가.
            events_blocked += 1
            block_reasons.append(
                f"{exp.strategy}/{exp.symbol}: {type(e).__name__}: {e}"
            )

    # 5. summary 라인.
    by_action: dict[str, int] = {}
    for d in decisions:
        by_action[d.action.value] = by_action.get(d.action.value, 0) + 1
    summary_parts = [
        f"verdict={explanation.verdict.value}",
        f"loop_state={loop_state}",
        f"decisions={len(decisions)} ({_dist_str(by_action)})",
        f"recorded={events_recorded}",
    ]
    if events_blocked > 0 or block_reasons:
        summary_parts.append(f"blocked={events_blocked}")
    summary = " | ".join(summary_parts) + " — advisory only"

    return BridgeReport(
        generated_at=now.isoformat(),
        schema_version=BRIDGE_SCHEMA_VERSION,
        loop_state=loop_state,
        explanation_verdict=explanation.verdict.value,
        decisions=decisions,
        events_recorded=events_recorded,
        events_blocked=events_blocked,
        block_reasons=block_reasons,
        summary=summary,
        metadata={
            "input_entries":      len(all_entries),
            "by_action":          by_action,
            "allow_trade":        allow_trade,
            "verdict_allow_trade": verdict_allows_trade,
        },
    )


def _count_explanation_entries(exp: PaperStartExplanation) -> int:
    return (
        len(exp.recommended_explanations)
        + len(exp.watchlist_explanations)
        + len(exp.excluded_explanations)
    )


def _dist_str(d: dict[str, int]) -> str:
    if not d:
        return "(empty)"
    return ", ".join(f"{k}={v}" for k, v in sorted(d.items()))


__all__ = [
    "BRIDGE_SCHEMA_VERSION",
    "BridgeReport",
    "PositionSnapshot",
    "bridge_explanation_to_paper_decisions",
    # Re-export for caller convenience.
    "AIDirection",
    "DecisionAction",
    "PaperDecision",
    "PaperFillStatus",
    "PaperLoopEvent",
]

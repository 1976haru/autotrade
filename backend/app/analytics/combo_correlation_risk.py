"""#3-14: 전략 조합 중복 / 상관 / 쏠림 위험 검증.

전략 조합이 같은 종목에 *중복 진입* 하거나, *같은 방향 LONG/SHORT 신호* 가
과도하게 몰리거나, *특정 전략 / 종목* 에 노출이 집중되는지 검증한다. AI
Agent 가 조합 추천 시 본 결과를 참고해 *위험한 조합* 을 자동 제외할 수 있다.

본 모듈은 *advisory* — 추천 제외는 caller / promotion gate 책임.
`recommended_for_paper=False` 영구 invariant (BLOCK 라벨에도, PASS 라벨에도
자동 적용 0건).

## 측정 지표

### Signal-level
- `overlap_count`        : 같은 (day, symbol) 에 2+ signal
- `overlap_ratio`        : overlap_count / max(signal_count, 1)
- `same_direction_count` : 같은 (day, symbol, direction) 에 2+ tactic group
- `same_direction_ratio` : same_direction_count / unique_day_symbol
- `conflict_count`       : 같은 (day, symbol) 에 BUY + SELL 동시
- `conflict_ratio`       : conflict_count / max(signal_count, 1)

### Aggregation-level
- `correlation_score`    : 0~1. (same_direction_ratio × confirmation_bonus)
  - tactic group 간 직접 PnL 상관계수가 *측정 불가* 한 경우 (현재 입력 구조
    상) **proxy** 로 same-direction 빈도 + tactic 다양성으로 추정.
- `concentration_score`  : 0~1. max(max_single_strategy_weight,
  max_single_symbol_weight)
- `max_single_strategy_weight` : 단일 strategy_id 가 차지하는 signal 비율
- `max_single_symbol_weight`   : 단일 symbol 이 차지하는 signal 비율

## Verdict

| Verdict | 조건 |
|---|---|
| `PASS` | overlap_ratio ≤ pass_overlap AND same_direction_ratio ≤ pass_same_dir AND conflict_ratio ≤ pass_conflict AND concentration_score ≤ pass_conc |
| `WATCH` | 일부 PASS 임계 boundary 초과, 아직 BLOCK 미만 |
| `HIGH_RISK` | 같은 방향 신호 또는 특정 종목 / 전략 집중도 높음 |
| `BLOCK` | concentration_score ≥ block_conc OR same_direction_ratio ≥ block_same_dir OR conflict_ratio ≥ block_conflict |
| `INSUFFICIENT_DATA` | signal_count < min_signals |

## 절대 invariant

1. broker / OrderExecutor / route_order import 0건.
2. `ComboRiskResult.is_order_signal/auto_apply_allowed/is_live_authorization
   /recommended_for_paper=False` 영구 — BLOCK 라벨도 자동 제외 X (caller 책임).
3. DB write 0건, secret 0건, settings mutation 0건.
4. Anthropic / OpenAI / httpx / requests import 0건.
"""

from __future__ import annotations

import json as _json
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import StrEnum
from pathlib import Path
from typing import Any

from app.analytics.strategy_combo_backtest import (
    StrategySignal,
    TacticGroup,
    combo_name,
    combo_strategies,
    enumerate_combinations,
)


COMBO_RISK_SCHEMA_VERSION = "1.0"


# ─────────────────────────────────────────────────────────────────────────────
# Verdict
# ─────────────────────────────────────────────────────────────────────────────


class ComboRiskVerdict(StrEnum):
    PASS              = "PASS"
    WATCH             = "WATCH"
    HIGH_RISK         = "HIGH_RISK"
    BLOCK             = "BLOCK"
    INSUFFICIENT_DATA = "INSUFFICIENT_DATA"


# ─────────────────────────────────────────────────────────────────────────────
# Criteria + Result dataclasses
# ─────────────────────────────────────────────────────────────────────────────


@dataclass(frozen=True)
class RiskCriteria:
    """위험 임계 — 운영자가 호출 시 override 가능."""
    min_signals:           int   = 5
    pass_overlap_ratio:    float = 0.20
    pass_same_dir_ratio:   float = 0.40
    pass_conflict_ratio:   float = 0.10
    pass_concentration:    float = 0.50
    watch_overlap_ratio:   float = 0.40
    watch_same_dir_ratio:  float = 0.60
    watch_concentration:   float = 0.65
    block_same_dir_ratio:  float = 0.85
    block_conflict_ratio:  float = 0.40
    block_concentration:   float = 0.85

    def __post_init__(self) -> None:
        if self.min_signals < 1:
            raise ValueError("min_signals must be >= 1")
        for name, val in (
            ("pass_overlap_ratio",    self.pass_overlap_ratio),
            ("pass_same_dir_ratio",   self.pass_same_dir_ratio),
            ("pass_conflict_ratio",   self.pass_conflict_ratio),
            ("pass_concentration",    self.pass_concentration),
            ("watch_overlap_ratio",   self.watch_overlap_ratio),
            ("watch_same_dir_ratio",  self.watch_same_dir_ratio),
            ("watch_concentration",   self.watch_concentration),
            ("block_same_dir_ratio",  self.block_same_dir_ratio),
            ("block_conflict_ratio",  self.block_conflict_ratio),
            ("block_concentration",   self.block_concentration),
        ):
            if not (0.0 <= val <= 1.0):
                raise ValueError(f"{name} must be in [0,1], got {val}")


@dataclass(frozen=True)
class ComboRiskResult:
    """단일 조합의 중복 / 상관 / 쏠림 결과 — *advisory*."""

    combo_name:                  str
    included_tactics:            tuple[str, ...]
    included_strategies:         tuple[str, ...]
    symbol:                      str | None

    signal_count:                int                  = 0
    unique_day_symbol:           int                  = 0

    overlap_count:               int                  = 0
    overlap_ratio:               float                = 0.0
    same_direction_count:        int                  = 0
    same_direction_ratio:        float                = 0.0
    conflict_count:              int                  = 0
    conflict_ratio:              float                = 0.0
    correlation_score:           float                = 0.0
    concentration_score:         float                = 0.0
    max_single_strategy_weight:  float                = 0.0
    max_single_symbol_weight:    float                = 0.0

    risk_verdict:                ComboRiskVerdict     = (
        ComboRiskVerdict.INSUFFICIENT_DATA
    )
    risk_flags:                  list[str]            = field(default_factory=list)
    exclusion_reasons:           list[str]            = field(default_factory=list)
    recommendation:              str                  = ""
    operator_note:               str                  = ""

    # AI Agent context — advisory only.
    agent_context_ready:         bool                 = True
    recommended_for_paper:       bool                 = False    # 영구 — 자동 적용 X

    # 절대 invariant.
    is_order_signal:             bool                 = False
    auto_apply_allowed:          bool                 = False
    is_live_authorization:       bool                 = False

    def __post_init__(self) -> None:
        for name, val in (
            ("is_order_signal",       self.is_order_signal),
            ("auto_apply_allowed",    self.auto_apply_allowed),
            ("is_live_authorization", self.is_live_authorization),
        ):
            if val is not False:
                raise ValueError(f"ComboRiskResult.{name} must be False.")
        if not isinstance(self.risk_verdict, ComboRiskVerdict):
            raise ValueError("risk_verdict must be ComboRiskVerdict.")
        # ratio 0~1 검증.
        for n, v in (
            ("overlap_ratio",        self.overlap_ratio),
            ("same_direction_ratio", self.same_direction_ratio),
            ("conflict_ratio",       self.conflict_ratio),
            ("correlation_score",    self.correlation_score),
            ("concentration_score",  self.concentration_score),
            ("max_single_strategy_weight", self.max_single_strategy_weight),
            ("max_single_symbol_weight",   self.max_single_symbol_weight),
        ):
            if not (0.0 <= v <= 1.0):
                raise ValueError(f"{n} must be in [0,1], got {v}")

    def to_dict(self) -> dict[str, Any]:
        return {
            "combo_name":                  self.combo_name,
            "included_tactics":            list(self.included_tactics),
            "included_strategies":         list(self.included_strategies),
            "symbol":                      self.symbol,
            "signal_count":                int(self.signal_count),
            "unique_day_symbol":           int(self.unique_day_symbol),
            "overlap_count":               int(self.overlap_count),
            "overlap_ratio":               float(self.overlap_ratio),
            "same_direction_count":        int(self.same_direction_count),
            "same_direction_ratio":        float(self.same_direction_ratio),
            "conflict_count":              int(self.conflict_count),
            "conflict_ratio":              float(self.conflict_ratio),
            "correlation_score":           float(self.correlation_score),
            "concentration_score":         float(self.concentration_score),
            "max_single_strategy_weight":  float(self.max_single_strategy_weight),
            "max_single_symbol_weight":    float(self.max_single_symbol_weight),
            "risk_verdict":                self.risk_verdict.value,
            "risk_flags":                  list(self.risk_flags),
            "exclusion_reasons":           list(self.exclusion_reasons),
            "recommendation":              self.recommendation,
            "operator_note":               self.operator_note,
            "agent_context_ready":         bool(self.agent_context_ready),
            "recommended_for_paper":       bool(self.recommended_for_paper),
            "is_order_signal":             False,
            "auto_apply_allowed":          False,
            "is_live_authorization":       False,
        }


@dataclass(frozen=True)
class ComboRiskReport:
    """전체 조합 위험 결과 묶음."""

    generated_at:           str
    schema_version:         str
    symbol:                 str | None
    results:                list[ComboRiskResult]
    criteria:               RiskCriteria
    notes:                  list[str]               = field(default_factory=list)

    advisory_disclaimer:    str = (
        "본 리포트는 *advisory* — 전략 조합 중복/상관/쏠림 위험 검증만, "
        "실거래 주문 0건. PASS 라벨도 Paper 후보 자동 적용 / 실거래 허가가 "
        "아니며, BLOCK 라벨도 자동 제외가 아닙니다 (caller 책임). "
        "is_order_signal=False / auto_apply_allowed=False / "
        "is_live_authorization=False."
    )

    is_order_signal:        bool = False
    auto_apply_allowed:     bool = False
    is_live_authorization:  bool = False

    def __post_init__(self) -> None:
        for name, val in (
            ("is_order_signal",       self.is_order_signal),
            ("auto_apply_allowed",    self.auto_apply_allowed),
            ("is_live_authorization", self.is_live_authorization),
        ):
            if val is not False:
                raise ValueError(f"ComboRiskReport.{name} must be False.")

    def to_dict(self) -> dict[str, Any]:
        return {
            "generated_at":          self.generated_at,
            "schema_version":        self.schema_version,
            "symbol":                self.symbol,
            "combo_count":           len(self.results),
            "results":               [r.to_dict() for r in self.results],
            "criteria":              {
                "min_signals":          self.criteria.min_signals,
                "pass_overlap_ratio":   self.criteria.pass_overlap_ratio,
                "pass_same_dir_ratio":  self.criteria.pass_same_dir_ratio,
                "pass_conflict_ratio":  self.criteria.pass_conflict_ratio,
                "pass_concentration":   self.criteria.pass_concentration,
                "watch_overlap_ratio":  self.criteria.watch_overlap_ratio,
                "watch_same_dir_ratio": self.criteria.watch_same_dir_ratio,
                "watch_concentration":  self.criteria.watch_concentration,
                "block_same_dir_ratio": self.criteria.block_same_dir_ratio,
                "block_conflict_ratio": self.criteria.block_conflict_ratio,
                "block_concentration":  self.criteria.block_concentration,
            },
            "notes":                 list(self.notes),
            "advisory_disclaimer":   self.advisory_disclaimer,
            "is_order_signal":       False,
            "auto_apply_allowed":    False,
            "is_live_authorization": False,
        }


# ─────────────────────────────────────────────────────────────────────────────
# Helpers
# ─────────────────────────────────────────────────────────────────────────────


_STRATEGY_TO_TACTIC: dict[str, TacticGroup] = {
    "sma_crossover":    TacticGroup.MOMENTUM,
    "volume_breakout":  TacticGroup.MOMENTUM,
    "rsi_reversion":    TacticGroup.REVERSION,
    "vwap_strategy":    TacticGroup.VWAP,
    "orb_vwap":         TacticGroup.ORB_PULLBACK,
    "pullback_rebreak": TacticGroup.ORB_PULLBACK,
}


def _normalize_direction(d: str) -> str:
    return (d or "").strip().upper()


def _filter_for_combo(
    signals:     list[StrategySignal],
    tactic_set:  set[TacticGroup],
) -> list[StrategySignal]:
    return [
        s for s in signals
        if _STRATEGY_TO_TACTIC[s.strategy_id] in tactic_set
    ]


# ─────────────────────────────────────────────────────────────────────────────
# Metric calculation
# ─────────────────────────────────────────────────────────────────────────────


def _signal_level_metrics(
    sigs: list[StrategySignal],
) -> tuple[int, float, int, float, int, float, int]:
    """(overlap, overlap_ratio, same_direction, same_dir_ratio, conflict,
        conflict_ratio, unique_day_symbol)."""
    if not sigs:
        return 0, 0.0, 0, 0.0, 0, 0.0, 0

    by_key: dict[tuple[str, str], list[StrategySignal]] = {}
    for s in sigs:
        by_key.setdefault((s.day_key, s.symbol), []).append(s)

    overlap, same_dir, conflict = 0, 0, 0
    unique_key = len(by_key)
    for _, group in by_key.items():
        if len(group) >= 2:
            overlap += 1
        dirs = {_normalize_direction(s.direction) for s in group}
        if "BUY" in dirs and "SELL" in dirs:
            conflict += 1
        for direction in ("BUY", "SELL"):
            tactics_in_dir = {
                _STRATEGY_TO_TACTIC[s.strategy_id]
                for s in group
                if _normalize_direction(s.direction) == direction
            }
            if len(tactics_in_dir) >= 2:
                same_dir += 1

    n = max(len(sigs), 1)
    overlap_ratio  = max(0.0, min(1.0, overlap / n))
    conflict_ratio = max(0.0, min(1.0, conflict / n))
    same_dir_ratio = (
        max(0.0, min(1.0, same_dir / unique_key)) if unique_key else 0.0
    )
    return overlap, overlap_ratio, same_dir, same_dir_ratio, \
        conflict, conflict_ratio, unique_key


def _concentration(sigs: list[StrategySignal]) -> tuple[float, float, float]:
    """(concentration_score, max_strategy_weight, max_symbol_weight) — 0~1."""
    if not sigs:
        return 0.0, 0.0, 0.0
    strat_counts: dict[str, int] = {}
    symbol_counts: dict[str, int] = {}
    for s in sigs:
        strat_counts[s.strategy_id] = strat_counts.get(s.strategy_id, 0) + 1
        symbol_counts[s.symbol] = symbol_counts.get(s.symbol, 0) + 1
    n = float(len(sigs))
    max_strat = max(strat_counts.values()) / n
    max_sym = max(symbol_counts.values()) / n
    conc = max(max_strat, max_sym)
    return float(conc), float(max_strat), float(max_sym)


def _correlation_score(
    *,
    same_direction_ratio: float,
    tactic_count:         int,
    overlap_ratio:        float,
) -> float:
    """Proxy correlation score — 0~1.

    실제 수익률 시계열이 없으므로 *same-direction frequency* 와 *tactic
    diversity* 로 추정. 같은 방향 신호가 많고 tactic 다양성이 낮으면 score 가 높다.
    """
    # tactic_count 가 4 (전체) 면 diversity 가 가장 높음 — score 가산 약화.
    diversity_penalty = 1.0 - min(0.5, (tactic_count - 1) * 0.15)
    # overlap_ratio 가 높으면 correlation 도 같이 가산.
    raw = same_direction_ratio * 0.7 + overlap_ratio * 0.3
    return max(0.0, min(1.0, raw * diversity_penalty))


# ─────────────────────────────────────────────────────────────────────────────
# Verdict
# ─────────────────────────────────────────────────────────────────────────────


def _verdict(
    *,
    signal_count:         int,
    overlap_ratio:        float,
    same_direction_ratio: float,
    conflict_ratio:       float,
    concentration_score:  float,
    criteria:             RiskCriteria,
) -> tuple[ComboRiskVerdict, list[str], list[str], str, str]:
    reasons: list[str] = []
    risk_flags: list[str] = []

    if signal_count < criteria.min_signals:
        return (
            ComboRiskVerdict.INSUFFICIENT_DATA,
            ["insufficient_signals"],
            [f"signal_count={signal_count} < min_signals={criteria.min_signals}"],
            "INSUFFICIENT_DATA — 분석 표본 부족",
            "데이터를 더 모은 뒤 다시 분석 필요.",
        )

    # BLOCK 우선.
    blocked = False
    if concentration_score >= criteria.block_concentration:
        risk_flags.append("extreme_concentration")
        reasons.append(
            f"concentration_score={concentration_score:.2f} >= block="
            f"{criteria.block_concentration:.2f}"
        )
        blocked = True
    if same_direction_ratio >= criteria.block_same_dir_ratio:
        risk_flags.append("extreme_same_direction")
        reasons.append(
            f"same_direction_ratio={same_direction_ratio:.2f} >= block="
            f"{criteria.block_same_dir_ratio:.2f}"
        )
        blocked = True
    if conflict_ratio >= criteria.block_conflict_ratio:
        risk_flags.append("extreme_conflict")
        reasons.append(
            f"conflict_ratio={conflict_ratio:.2f} >= block="
            f"{criteria.block_conflict_ratio:.2f}"
        )
        blocked = True
    if blocked:
        return (
            ComboRiskVerdict.BLOCK,
            risk_flags,
            reasons,
            "BLOCK — 중복/상관/쏠림 과도, Paper 후보 부적합",
            "운영자 검토 + 별도 PR 없이 Paper 후보에 절대 적용 금지.",
        )

    # HIGH_RISK / WATCH boundary.
    high_risk = False
    if same_direction_ratio > criteria.watch_same_dir_ratio:
        risk_flags.append("high_same_direction")
        reasons.append(
            f"same_direction_ratio={same_direction_ratio:.2f} > watch="
            f"{criteria.watch_same_dir_ratio:.2f}"
        )
        high_risk = True
    if concentration_score > criteria.watch_concentration:
        risk_flags.append("high_concentration")
        reasons.append(
            f"concentration_score={concentration_score:.2f} > watch="
            f"{criteria.watch_concentration:.2f}"
        )
        high_risk = True
    if high_risk:
        return (
            ComboRiskVerdict.HIGH_RISK,
            risk_flags,
            reasons,
            "HIGH_RISK — 같은 방향 / 종목 집중도 높음",
            "운영자 명시 검토 + size 축소 권고.",
        )

    watch = False
    if overlap_ratio > criteria.watch_overlap_ratio:
        risk_flags.append("watch_overlap")
        reasons.append(
            f"overlap_ratio={overlap_ratio:.2f} > watch="
            f"{criteria.watch_overlap_ratio:.2f}"
        )
        watch = True
    if same_direction_ratio > criteria.pass_same_dir_ratio:
        risk_flags.append("boundary_same_direction")
        reasons.append(
            f"same_direction_ratio={same_direction_ratio:.2f} > pass="
            f"{criteria.pass_same_dir_ratio:.2f}"
        )
        watch = True
    if concentration_score > criteria.pass_concentration:
        risk_flags.append("boundary_concentration")
        reasons.append(
            f"concentration_score={concentration_score:.2f} > pass="
            f"{criteria.pass_concentration:.2f}"
        )
        watch = True
    if conflict_ratio > criteria.pass_conflict_ratio:
        risk_flags.append("boundary_conflict")
        reasons.append(
            f"conflict_ratio={conflict_ratio:.2f} > pass="
            f"{criteria.pass_conflict_ratio:.2f}"
        )
        watch = True
    if watch:
        return (
            ComboRiskVerdict.WATCH,
            risk_flags,
            reasons,
            "WATCH — 일부 boundary 초과, Paper 관찰 가능",
            "운영자 관찰 + size 축소 검토 권고.",
        )

    return (
        ComboRiskVerdict.PASS,
        [],
        ["모든 PASS 기준 통과 — 중복/상관/쏠림 위험 낮음"],
        "PASS — 중복/상관/쏠림 위험 낮음 (advisory)",
        "그래도 Paper 후보 확정은 운영자 명시 승인 필요.",
    )


# ─────────────────────────────────────────────────────────────────────────────
# Main entry
# ─────────────────────────────────────────────────────────────────────────────


def compute_combo_risk(
    *,
    tactics:    tuple[TacticGroup, ...],
    signals:    list[StrategySignal],
    symbol:     str | None     = None,
    criteria:   RiskCriteria   = None,    # type: ignore[assignment]
) -> ComboRiskResult:
    if criteria is None:
        criteria = RiskCriteria()
    tactic_set = set(tactics)
    sigs = _filter_for_combo(signals, tactic_set)

    overlap, overlap_ratio, same_dir, same_dir_ratio, \
        conflict, conflict_ratio, unique_key = _signal_level_metrics(sigs)
    conc, max_strat, max_sym = _concentration(sigs)
    corr = _correlation_score(
        same_direction_ratio=same_dir_ratio,
        tactic_count=len(tactic_set),
        overlap_ratio=overlap_ratio,
    )

    verdict, risk_flags, reasons, recommendation, operator_note = _verdict(
        signal_count=len(sigs),
        overlap_ratio=overlap_ratio,
        same_direction_ratio=same_dir_ratio,
        conflict_ratio=conflict_ratio,
        concentration_score=conc,
        criteria=criteria,
    )

    return ComboRiskResult(
        combo_name=combo_name(tactics),
        included_tactics=tuple(t.value for t in tactics),
        included_strategies=combo_strategies(tactics),
        symbol=symbol,
        signal_count=len(sigs),
        unique_day_symbol=unique_key,
        overlap_count=overlap,
        overlap_ratio=overlap_ratio,
        same_direction_count=same_dir,
        same_direction_ratio=same_dir_ratio,
        conflict_count=conflict,
        conflict_ratio=conflict_ratio,
        correlation_score=corr,
        concentration_score=conc,
        max_single_strategy_weight=max_strat,
        max_single_symbol_weight=max_sym,
        risk_verdict=verdict,
        risk_flags=risk_flags,
        exclusion_reasons=reasons,
        recommendation=recommendation,
        operator_note=operator_note,
        # 영구 — BLOCK / PASS 모두 자동 적용 X.
        recommended_for_paper=False,
    )


def run_combo_risk_analysis(
    *,
    signals:        list[StrategySignal],
    symbol:         str | None       = None,
    criteria:       RiskCriteria     = None,    # type: ignore[assignment]
    only_sizes:     list[int] | None = None,
    now:            datetime | None  = None,
) -> ComboRiskReport:
    if criteria is None:
        criteria = RiskCriteria()
    if now is None:
        now = datetime.now(timezone.utc)

    catalog = enumerate_combinations()
    if only_sizes:
        wanted = set(int(x) for x in only_sizes)
        catalog = [c for c in catalog if len(c) in wanted]

    results: list[ComboRiskResult] = []
    for tactics in catalog:
        results.append(
            compute_combo_risk(
                tactics=tactics, signals=signals,
                symbol=symbol, criteria=criteria,
            ),
        )

    notes: list[str] = [
        "본 리포트는 advisory — 자동 Paper 후보 적용 0건.",
        "BLOCK 라벨도 자동 제외가 아니며, 운영자 승인 / promotion gate 책임.",
    ]
    if not signals:
        notes.append("INSUFFICIENT_DATA: 입력 signals == 0")

    return ComboRiskReport(
        generated_at=now.isoformat(),
        schema_version=COMBO_RISK_SCHEMA_VERSION,
        symbol=symbol,
        results=results,
        criteria=criteria,
        notes=notes,
    )


# ─────────────────────────────────────────────────────────────────────────────
# Renderers
# ─────────────────────────────────────────────────────────────────────────────


def _fmt(v: Any, places: int = 4) -> str:
    if v is None:
        return "—"
    if isinstance(v, float):
        return f"{v:.{places}f}"
    return str(v)


def render_markdown(report: ComboRiskReport) -> str:
    lines: list[str] = []
    lines.append("# 전략 조합 중복 / 상관 / 쏠림 위험 리포트")
    lines.append("")
    lines.append("> *advisory* — 중복/상관/쏠림 분석만, 실거래 주문 0건.")
    lines.append("> PASS 라벨도 Paper 후보 자동 적용 허가가 아니며,")
    lines.append("> BLOCK 라벨도 자동 제외가 아닙니다 (caller 책임).")
    lines.append("")
    lines.append(f"- 생성: `{report.generated_at}`")
    lines.append(f"- schema_version: `{report.schema_version}`")
    lines.append(f"- symbol: `{report.symbol or '—'}`")
    lines.append(f"- combo 수: **{len(report.results)}**")
    lines.append("")

    lines.append("## 조합별 위험 매트릭스")
    lines.append("")
    lines.append(
        "| combo | verdict | signals | overlap_ratio | same_dir_ratio | "
        "conflict_ratio | correlation | concentration | "
        "max_strategy_w | max_symbol_w |"
    )
    lines.append("|" + "|".join(["---"] * 10) + "|")
    for r in report.results:
        lines.append(
            f"| `{r.combo_name}` | **{r.risk_verdict.value}** | "
            f"{r.signal_count} | {_fmt(r.overlap_ratio)} | "
            f"{_fmt(r.same_direction_ratio)} | {_fmt(r.conflict_ratio)} | "
            f"{_fmt(r.correlation_score)} | {_fmt(r.concentration_score)} | "
            f"{_fmt(r.max_single_strategy_weight)} | "
            f"{_fmt(r.max_single_symbol_weight)} |"
        )
    lines.append("")

    if report.notes:
        lines.append("## 노트")
        for n in report.notes:
            lines.append(f"- {n}")
        lines.append("")

    lines.append("## 안전 invariant")
    lines.append("")
    lines.append(
        "- `is_order_signal=False` / `auto_apply_allowed=False` / "
        "`is_live_authorization=False`"
    )
    lines.append("- `recommended_for_paper=False` 영구 — PASS / BLOCK 모두 자동 적용 0건")
    lines.append("- broker / OrderExecutor / route_order 호출 0건")
    lines.append("")
    lines.append(report.advisory_disclaimer)
    lines.append("")
    return "\n".join(lines)


def render_ranking_csv(report: ComboRiskReport) -> str:
    headers = [
        "combo_name", "risk_verdict",
        "signal_count", "overlap_ratio", "same_direction_ratio",
        "conflict_ratio", "correlation_score", "concentration_score",
        "max_single_strategy_weight", "max_single_symbol_weight",
        "recommended_for_paper",
    ]
    # 위험도 정렬 — BLOCK > HIGH_RISK > WATCH > INSUFFICIENT_DATA > PASS.
    _order = {
        ComboRiskVerdict.BLOCK: 0,
        ComboRiskVerdict.HIGH_RISK: 1,
        ComboRiskVerdict.WATCH: 2,
        ComboRiskVerdict.INSUFFICIENT_DATA: 3,
        ComboRiskVerdict.PASS: 4,
    }
    rows = sorted(
        report.results,
        key=lambda r: (
            _order.get(r.risk_verdict, 9),
            -r.concentration_score,
        ),
    )
    out: list[str] = [",".join(headers)]
    for r in rows:
        out.append(",".join([
            r.combo_name,
            r.risk_verdict.value,
            str(int(r.signal_count)),
            _fmt(r.overlap_ratio),
            _fmt(r.same_direction_ratio),
            _fmt(r.conflict_ratio),
            _fmt(r.correlation_score),
            _fmt(r.concentration_score),
            _fmt(r.max_single_strategy_weight),
            _fmt(r.max_single_symbol_weight),
            "false",   # 영구
        ]))
    return "\n".join(out) + "\n"


def write_reports(
    report:  ComboRiskReport,
    out_dir: Path | str,
) -> dict[str, Path]:
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    json_path = out / "combo_correlation_risk_summary.json"
    md_path   = out / "combo_correlation_risk_report.md"
    csv_path  = out / "combo_correlation_risk_ranking.csv"
    json_path.write_text(
        _json.dumps(report.to_dict(), ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    md_path.write_text(render_markdown(report), encoding="utf-8")
    csv_path.write_text(render_ranking_csv(report), encoding="utf-8")
    return {"summary_json": json_path, "report_md": md_path, "ranking_csv": csv_path}


__all__ = [
    "COMBO_RISK_SCHEMA_VERSION",
    "ComboRiskVerdict",
    "RiskCriteria",
    "ComboRiskResult",
    "ComboRiskReport",
    "compute_combo_risk",
    "run_combo_risk_analysis",
    "render_markdown",
    "render_ranking_csv",
    "write_reports",
]

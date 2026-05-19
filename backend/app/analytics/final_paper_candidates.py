"""#3-15: 최종 Paper 조합 후보 선정.

3-02 ~ 3-14 의 *advisory* 분석 결과를 종합해 AI Paper Auto Loop 에 넣을 *최종*
운용 후보 **1~3개** 를 선정한다. 후보가 없으면 `status="NO_CANDIDATE"` + 사유
+ 빈 후보 리스트 반환 — *억지로 후보를 만들지 않는다*.

본 모듈은 *최종 게이트* 가 아니다 — 선정된 후보도 `requires_operator_approval=True`
영구 invariant. Paper Auto Loop 입력으로 *자동* 연결되지 않으며, 운영자 명시
승인 + 별도 흐름 (4-Paper-Connect 등) 에서만 실제 적용.

## 입력

`CandidateInput` (combo + symbol 별 단일 row):
- 백테스트 metric (3-02 / 3-06): expectancy / profit_factor / max_drawdown /
  win_rate / loss_streak / trade_count
- Walk-forward verdict (3-04): HEALTHY / WATCH / DECAY_WARNING /
  DISABLE_CANDIDATE / INSUFFICIENT_DATA
- Stress test verdict (3-05): PASS / WARN / FAIL / INSUFFICIENT_DATA
- Combo backtest verdict (3-12): PASS / WARN / FAIL / INSUFFICIENT_DATA
- Regime combo verdict (3-13): PASS / WATCH / FAIL / INSUFFICIENT_DATA /
  BLOCKED_REGIME — *primary regime* 의 verdict 기준
- Combo risk verdict (3-14): PASS / WATCH / HIGH_RISK / BLOCK /
  INSUFFICIENT_DATA
- Strategy paper_candidate_status (3-07): READY_FOR_PAPER / WATCHLIST_ONLY /
  REJECTED / OVERFIT_RISK / STRESS_FAILED / INSUFFICIENT_DATA

## 후보 선정 조건 (모두 통과해야 함)

1. `paper_candidate_status` ∈ {READY_FOR_PAPER, WATCHLIST_ONLY} (REJECTED /
   OVERFIT_RISK / STRESS_FAILED / INSUFFICIENT_DATA → 자동 제외).
2. `trade_count >= min_trades` AND `expectancy > 0` AND
   `profit_factor >= min_pf` AND `|max_drawdown| <= max_mdd`.
3. Walk-forward verdict ∈ {HEALTHY, WATCH}.
4. Stress test verdict ∈ {PASS, WARN}.
5. Combo backtest verdict ∈ {PASS, WARN}.
6. Regime combo verdict ∈ {PASS, WATCH} AND primary_regime 이
   LOW_LIQUIDITY / UNKNOWN 아님.
7. Combo risk verdict ∈ {PASS, WATCH} (HIGH_RISK / BLOCK → 자동 제외).

위 7 조건 중 *하나라도* 실패 시 후보에서 제외 + `exclusion_reasons` 에 사유
누적.

## 선정 정책

- 통과한 후보 중 `composite_score` 내림차순 정렬.
- 상위 *최대 3개* 만 선정 (`max_candidates=3` default).
- 통과 0개 → `status="NO_CANDIDATE"` + `reasons_no_candidate` 채워서 반환.
- 통과 1개 → `status="MIN_CANDIDATES"` + 1 개 carry.
- 통과 2~3개 → `status="OK"`.
- 통과 4+개 → 상위 3개만 carry, `excluded_above_top_n` 에 나머지 carry.

`composite_score` = 0.4 × profit_factor_norm + 0.3 × expectancy_norm +
0.2 × drawdown_score + 0.1 × confirmation_bonus (모두 0~1 정규화 후 합산).

## 절대 invariant (CLAUDE.md 절대 원칙 1~5 상속)

1. broker / OrderExecutor / route_order import 0건.
2. `PaperCandidate.is_order_signal/auto_apply_allowed/is_live_authorization=
   False` 영구. `recommended_for_paper=True` 가 *후보 리스트* 에 등재되어도
   `requires_operator_approval=True` 영구 — 자동 적용 X.
3. DB write 0건, secret 0건, settings mutation 0건.
4. Anthropic / OpenAI / httpx / requests import 0건.
"""

from __future__ import annotations

import json as _json
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import StrEnum
from pathlib import Path
from typing import Any, Iterable


FINAL_CANDIDATE_SCHEMA_VERSION = "1.0"


# ─────────────────────────────────────────────────────────────────────────────
# Enums + criteria
# ─────────────────────────────────────────────────────────────────────────────


class SelectionStatus(StrEnum):
    OK              = "OK"
    MIN_CANDIDATES  = "MIN_CANDIDATES"   # 1 개 후보만 통과
    NO_CANDIDATE    = "NO_CANDIDATE"


_ALLOWED_PAPER_STATUS = {"READY_FOR_PAPER", "WATCHLIST_ONLY"}
_ALLOWED_WALK_FORWARD = {"HEALTHY", "WATCH"}
_ALLOWED_STRESS       = {"PASS", "WARN"}
_ALLOWED_COMBO        = {"PASS", "WARN"}
_ALLOWED_REGIME       = {"PASS", "WATCH"}
_ALLOWED_COMBO_RISK   = {"PASS", "WATCH"}
_BLOCKED_REGIMES      = {"LOW_LIQUIDITY", "UNKNOWN"}


@dataclass(frozen=True)
class FinalCandidateCriteria:
    """후보 선정 임계값 — 운영자 호출 시 override 가능."""
    min_trades:         int   = 10
    min_expectancy:     float = 0.0    # > 0
    min_profit_factor:  float = 1.2
    max_drawdown_abs:   float = 0.20   # 절대값
    min_win_rate:       float = 0.0
    max_loss_streak:    int   = 10
    max_candidates:     int   = 3

    def __post_init__(self) -> None:
        if self.min_trades < 1:
            raise ValueError("min_trades must be >= 1")
        if self.min_profit_factor <= 0:
            raise ValueError("min_profit_factor must be > 0")
        if not (0.0 < self.max_drawdown_abs <= 1.0):
            raise ValueError("max_drawdown_abs must be in (0, 1]")
        if self.max_candidates < 1 or self.max_candidates > 10:
            raise ValueError("max_candidates must be in [1, 10]")


# ─────────────────────────────────────────────────────────────────────────────
# Input + result dataclasses
# ─────────────────────────────────────────────────────────────────────────────


@dataclass(frozen=True)
class CandidateInput:
    """단일 (combo / strategy, symbol) 후보의 모든 측정값.

    *secret 필드 0건* — strategy_id / symbol 외 식별자 없음. API key /
    계좌번호 carry 0개 (테스트 lock).
    """

    name:                       str           # 'sma_crossover' 또는 'MOMENTUM+VWAP' 같은 combo_name
    included_tactics:           tuple[str, ...]   = ()
    included_strategies:        tuple[str, ...]   = ()
    symbol:                     str            = "UNKNOWN"
    params:                     dict[str, Any]  = field(default_factory=dict)
    primary_regime:             str            = "UNKNOWN"

    # 3-02 / 3-06 백테스트 metric.
    trade_count:                int            = 0
    expectancy:                 float | None   = None
    profit_factor:              float | None   = None
    max_drawdown:               float | None   = None   # 절대값 0~1
    win_rate:                   float | None   = None
    loss_streak:                int            = 0
    total_return:               float          = 0.0

    # 3-04 / 3-05 / 3-07 verdict.
    paper_candidate_status:     str            = "INSUFFICIENT_DATA"
    walk_forward_verdict:       str            = "INSUFFICIENT_DATA"
    stress_verdict:             str            = "INSUFFICIENT_DATA"

    # 3-12 / 3-13 / 3-14 verdict.
    combo_verdict:              str            = "INSUFFICIENT_DATA"
    regime_combo_verdict:       str            = "INSUFFICIENT_DATA"
    combo_risk_verdict:         str            = "INSUFFICIENT_DATA"

    # 보조 metric.
    confirmation_score:         int            = 0
    correlation_score:          float          = 0.0    # 0~1
    concentration_score:        float          = 0.0    # 0~1

    def __post_init__(self) -> None:
        if not self.name or not self.symbol:
            raise ValueError("name / symbol must be non-empty")
        # 0~1 range guards.
        for n, v in (
            ("max_drawdown",         self.max_drawdown),
            ("win_rate",             self.win_rate),
            ("correlation_score",    self.correlation_score),
            ("concentration_score",  self.concentration_score),
        ):
            if v is not None and not (-1.0 <= v <= 1.0):
                raise ValueError(f"{n} out of range: {v}")


@dataclass(frozen=True)
class PaperCandidate:
    """선정된 후보 — *advisory*. 자동 적용 0건."""

    rank:                       int
    name:                       str
    included_tactics:           tuple[str, ...]
    included_strategies:        tuple[str, ...]
    symbol:                     str
    params:                     dict[str, Any]
    primary_regime:             str

    # 성과지표.
    trade_count:                int
    expectancy:                 float | None
    profit_factor:              float | None
    max_drawdown:               float | None
    win_rate:                   float | None
    loss_streak:                int
    total_return:               float

    # 위험지표.
    correlation_score:          float
    concentration_score:        float
    confirmation_score:         int

    # 종합 점수.
    composite_score:            float

    # verdict carry.
    paper_candidate_status:     str
    walk_forward_verdict:       str
    stress_verdict:             str
    combo_verdict:              str
    regime_combo_verdict:       str
    combo_risk_verdict:         str

    # 운영자 친화.
    recommended_reasons:        list[str]            = field(default_factory=list)
    risk_flags:                 list[str]            = field(default_factory=list)
    operator_note:              str                  = ""

    # AI Agent context — advisory only.
    agent_context_ready:        bool                 = True
    recommended_for_paper:      bool                 = True    # 후보 리스트에 포함되었음
    requires_operator_approval: bool                 = True    # 영구 — 자동 적용 금지

    # 절대 invariant.
    is_order_signal:            bool = False
    auto_apply_allowed:         bool = False
    is_live_authorization:      bool = False

    def __post_init__(self) -> None:
        for name, val in (
            ("is_order_signal",       self.is_order_signal),
            ("auto_apply_allowed",    self.auto_apply_allowed),
            ("is_live_authorization", self.is_live_authorization),
        ):
            if val is not False:
                raise ValueError(f"PaperCandidate.{name} must be False.")
        if self.requires_operator_approval is not True:
            raise ValueError("PaperCandidate.requires_operator_approval must be True.")
        if self.rank < 1:
            raise ValueError("rank must be >= 1")

    def to_dict(self) -> dict[str, Any]:
        return {
            "rank":                     int(self.rank),
            "name":                     self.name,
            "included_tactics":         list(self.included_tactics),
            "included_strategies":      list(self.included_strategies),
            "symbol":                   self.symbol,
            "params":                   dict(self.params),
            "primary_regime":           self.primary_regime,
            "trade_count":              int(self.trade_count),
            "expectancy":               self.expectancy,
            "profit_factor":            self.profit_factor,
            "max_drawdown":             self.max_drawdown,
            "win_rate":                 self.win_rate,
            "loss_streak":              int(self.loss_streak),
            "total_return":             float(self.total_return),
            "correlation_score":        float(self.correlation_score),
            "concentration_score":      float(self.concentration_score),
            "confirmation_score":       int(self.confirmation_score),
            "composite_score":          float(self.composite_score),
            "paper_candidate_status":   self.paper_candidate_status,
            "walk_forward_verdict":     self.walk_forward_verdict,
            "stress_verdict":           self.stress_verdict,
            "combo_verdict":            self.combo_verdict,
            "regime_combo_verdict":     self.regime_combo_verdict,
            "combo_risk_verdict":       self.combo_risk_verdict,
            "recommended_reasons":      list(self.recommended_reasons),
            "risk_flags":               list(self.risk_flags),
            "operator_note":            self.operator_note,
            "agent_context_ready":      bool(self.agent_context_ready),
            "recommended_for_paper":    bool(self.recommended_for_paper),
            "requires_operator_approval": bool(self.requires_operator_approval),
            "is_order_signal":          False,
            "auto_apply_allowed":       False,
            "is_live_authorization":    False,
        }


@dataclass(frozen=True)
class ExcludedCandidate:
    """선정에서 제외된 후보 — 운영자 검토용 사유 carry."""
    name:               str
    symbol:             str
    exclusion_reasons:  list[str]                = field(default_factory=list)
    risk_flags:         list[str]                = field(default_factory=list)
    measurements:       dict[str, Any]           = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "name":              self.name,
            "symbol":            self.symbol,
            "exclusion_reasons": list(self.exclusion_reasons),
            "risk_flags":        list(self.risk_flags),
            "measurements":      dict(self.measurements),
        }


@dataclass(frozen=True)
class FinalCandidateReport:
    """선정 결과 + 제외 사유 + 안전 invariants."""

    generated_at:           str
    schema_version:         str
    status:                 SelectionStatus
    period_label:           str

    candidates:             list[PaperCandidate]
    excluded:               list[ExcludedCandidate]
    reasons_no_candidate:   list[str]               = field(default_factory=list)

    criteria:               FinalCandidateCriteria  = field(
        default_factory=FinalCandidateCriteria
    )
    notes:                  list[str]                = field(default_factory=list)

    advisory_disclaimer:    str = (
        "본 리포트는 *advisory* — Paper Auto Loop 자동 연결 0건. "
        "선정된 후보도 'requires_operator_approval=True' 영구이며, "
        "Paper Auto Loop 에 *자동* 으로 들어가지 않습니다. 운영자 명시 승인 + "
        "별도 PR 후에만 실제 입력으로 사용. is_order_signal=False / "
        "auto_apply_allowed=False / is_live_authorization=False."
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
                raise ValueError(f"FinalCandidateReport.{name} must be False.")
        if not isinstance(self.status, SelectionStatus):
            raise ValueError("status must be SelectionStatus.")
        # status / candidates 일관성.
        if self.status == SelectionStatus.NO_CANDIDATE and self.candidates:
            raise ValueError(
                "NO_CANDIDATE status must have empty candidates list"
            )
        if self.status == SelectionStatus.OK and len(self.candidates) < 2:
            raise ValueError(
                f"OK status requires >= 2 candidates, got {len(self.candidates)}"
            )
        if self.status == SelectionStatus.MIN_CANDIDATES and len(self.candidates) != 1:
            raise ValueError(
                f"MIN_CANDIDATES status requires exactly 1 candidate, "
                f"got {len(self.candidates)}"
            )

    def to_dict(self) -> dict[str, Any]:
        return {
            "generated_at":          self.generated_at,
            "schema_version":        self.schema_version,
            "status":                self.status.value,
            "period_label":          self.period_label,
            "candidate_count":       len(self.candidates),
            "candidates":            [c.to_dict() for c in self.candidates],
            "excluded_count":        len(self.excluded),
            "excluded":              [e.to_dict() for e in self.excluded],
            "reasons_no_candidate":  list(self.reasons_no_candidate),
            "criteria":              {
                "min_trades":        self.criteria.min_trades,
                "min_expectancy":    self.criteria.min_expectancy,
                "min_profit_factor": self.criteria.min_profit_factor,
                "max_drawdown_abs":  self.criteria.max_drawdown_abs,
                "min_win_rate":      self.criteria.min_win_rate,
                "max_loss_streak":   self.criteria.max_loss_streak,
                "max_candidates":    self.criteria.max_candidates,
            },
            "notes":                 list(self.notes),
            "advisory_disclaimer":   self.advisory_disclaimer,
            "is_order_signal":       False,
            "auto_apply_allowed":    False,
            "is_live_authorization": False,
        }


# ─────────────────────────────────────────────────────────────────────────────
# Filtering + scoring
# ─────────────────────────────────────────────────────────────────────────────


def _qualify(
    inp:       CandidateInput,
    criteria:  FinalCandidateCriteria,
) -> tuple[bool, list[str], list[str]]:
    """후보 7 조건 검사 — (qualified?, exclusion_reasons, risk_flags)."""
    reasons: list[str] = []
    flags: list[str] = []

    # 1. paper_candidate_status.
    if inp.paper_candidate_status not in _ALLOWED_PAPER_STATUS:
        reasons.append(
            f"paper_candidate_status={inp.paper_candidate_status} "
            f"not in {sorted(_ALLOWED_PAPER_STATUS)}"
        )
        flags.append("paper_status_excluded")
        if inp.paper_candidate_status == "OVERFIT_RISK":
            flags.append("overfit_risk")
        elif inp.paper_candidate_status == "STRESS_FAILED":
            flags.append("stress_failed")
        elif inp.paper_candidate_status == "REJECTED":
            flags.append("paper_rejected")
        elif inp.paper_candidate_status == "INSUFFICIENT_DATA":
            flags.append("insufficient_data")

    # 2. backtest metrics.
    if inp.trade_count < criteria.min_trades:
        reasons.append(
            f"trade_count={inp.trade_count} < min={criteria.min_trades}"
        )
        flags.append("insufficient_trades")
    if inp.expectancy is None or inp.expectancy <= criteria.min_expectancy:
        reasons.append(
            f"expectancy={inp.expectancy} <= {criteria.min_expectancy}"
        )
        flags.append("non_positive_expectancy")
    if inp.profit_factor is None \
            or inp.profit_factor < criteria.min_profit_factor:
        reasons.append(
            f"profit_factor={inp.profit_factor} < min={criteria.min_profit_factor}"
        )
        flags.append("low_profit_factor")
    if inp.max_drawdown is None \
            or abs(inp.max_drawdown) > criteria.max_drawdown_abs:
        reasons.append(
            f"|max_drawdown|={inp.max_drawdown} > max={criteria.max_drawdown_abs}"
        )
        flags.append("high_drawdown")
    if inp.win_rate is not None and inp.win_rate < criteria.min_win_rate:
        reasons.append(
            f"win_rate={inp.win_rate} < min={criteria.min_win_rate}"
        )
        flags.append("low_win_rate")
    if inp.loss_streak > criteria.max_loss_streak:
        reasons.append(
            f"loss_streak={inp.loss_streak} > max={criteria.max_loss_streak}"
        )
        flags.append("high_loss_streak")

    # 3. walk-forward.
    if inp.walk_forward_verdict not in _ALLOWED_WALK_FORWARD:
        reasons.append(
            f"walk_forward_verdict={inp.walk_forward_verdict} "
            f"not in {sorted(_ALLOWED_WALK_FORWARD)}"
        )
        flags.append("walk_forward_excluded")

    # 4. stress test.
    if inp.stress_verdict not in _ALLOWED_STRESS:
        reasons.append(
            f"stress_verdict={inp.stress_verdict} "
            f"not in {sorted(_ALLOWED_STRESS)}"
        )
        flags.append("stress_excluded")

    # 5. combo backtest.
    if inp.combo_verdict not in _ALLOWED_COMBO:
        reasons.append(
            f"combo_verdict={inp.combo_verdict} "
            f"not in {sorted(_ALLOWED_COMBO)}"
        )
        flags.append("combo_backtest_excluded")

    # 6. regime combo.
    if inp.regime_combo_verdict not in _ALLOWED_REGIME:
        reasons.append(
            f"regime_combo_verdict={inp.regime_combo_verdict} "
            f"not in {sorted(_ALLOWED_REGIME)}"
        )
        flags.append("regime_combo_excluded")
    if inp.primary_regime in _BLOCKED_REGIMES:
        reasons.append(
            f"primary_regime={inp.primary_regime} is in blocked set "
            f"{sorted(_BLOCKED_REGIMES)}"
        )
        flags.append(f"regime_{inp.primary_regime.lower()}")

    # 7. combo risk.
    if inp.combo_risk_verdict not in _ALLOWED_COMBO_RISK:
        reasons.append(
            f"combo_risk_verdict={inp.combo_risk_verdict} "
            f"not in {sorted(_ALLOWED_COMBO_RISK)}"
        )
        flags.append("combo_risk_excluded")

    return (not reasons), reasons, flags


def _composite_score(
    inp:       CandidateInput,
    criteria:  FinalCandidateCriteria,
) -> float:
    """0~1 정규화된 종합 점수 — 후보 순위 결정."""
    # profit_factor: criteria.min_pf 부터 3.0 까지 0~1.
    pf = inp.profit_factor or 0.0
    pf_norm = max(0.0, min(1.0, (pf - criteria.min_profit_factor) / max(0.001, 3.0 - criteria.min_profit_factor)))
    # expectancy: positive → 0~1 with 1000 base.
    exp = inp.expectancy or 0.0
    exp_norm = max(0.0, min(1.0, exp / 1000.0))
    # max_drawdown: 0이 best (1.0 score), criteria.max_drawdown_abs 가 0.
    mdd = abs(inp.max_drawdown or criteria.max_drawdown_abs)
    dd_score = max(0.0, 1.0 - mdd / max(0.001, criteria.max_drawdown_abs))
    # confirmation_bonus: 0~10 confirmation_score → 0~1.
    conf_bonus = max(0.0, min(1.0, inp.confirmation_score / 10.0))
    return (
        0.4 * pf_norm
        + 0.3 * exp_norm
        + 0.2 * dd_score
        + 0.1 * conf_bonus
    )


def _build_candidate(
    *,
    rank:       int,
    inp:        CandidateInput,
    score:      float,
    flags:      list[str],
) -> PaperCandidate:
    reasons: list[str] = []
    reasons.append(
        f"profit_factor={inp.profit_factor:.2f} / expectancy={inp.expectancy:.2f}"
    )
    reasons.append(
        f"max_drawdown={inp.max_drawdown:.3f} (limit ok) / "
        f"walk_forward={inp.walk_forward_verdict} / stress={inp.stress_verdict}"
    )
    reasons.append(
        f"combo={inp.combo_verdict} / regime[{inp.primary_regime}]="
        f"{inp.regime_combo_verdict} / risk={inp.combo_risk_verdict}"
    )
    operator_note = (
        "본 후보는 *advisory* — Paper Auto Loop 에 자동 입력되지 않으며, "
        "운영자가 명시 승인 후 별도 PR 로 paper 흐름에 연결할 수 있습니다."
    )
    return PaperCandidate(
        rank=rank,
        name=inp.name,
        included_tactics=tuple(inp.included_tactics),
        included_strategies=tuple(inp.included_strategies),
        symbol=inp.symbol,
        params=dict(inp.params),
        primary_regime=inp.primary_regime,
        trade_count=int(inp.trade_count),
        expectancy=inp.expectancy,
        profit_factor=inp.profit_factor,
        max_drawdown=inp.max_drawdown,
        win_rate=inp.win_rate,
        loss_streak=int(inp.loss_streak),
        total_return=float(inp.total_return),
        correlation_score=float(inp.correlation_score),
        concentration_score=float(inp.concentration_score),
        confirmation_score=int(inp.confirmation_score),
        composite_score=float(score),
        paper_candidate_status=inp.paper_candidate_status,
        walk_forward_verdict=inp.walk_forward_verdict,
        stress_verdict=inp.stress_verdict,
        combo_verdict=inp.combo_verdict,
        regime_combo_verdict=inp.regime_combo_verdict,
        combo_risk_verdict=inp.combo_risk_verdict,
        recommended_reasons=reasons,
        risk_flags=list(flags),
        operator_note=operator_note,
        # 영구 — 자동 적용 절대 X.
        requires_operator_approval=True,
    )


# ─────────────────────────────────────────────────────────────────────────────
# Main entry
# ─────────────────────────────────────────────────────────────────────────────


def select_paper_candidates(
    *,
    inputs:        Iterable[CandidateInput],
    criteria:      FinalCandidateCriteria   = None,    # type: ignore[assignment]
    period_label:  str                       = "ad-hoc",
    now:           datetime | None           = None,
) -> FinalCandidateReport:
    """후보 입력 → 최종 Paper 후보 1~3 + 제외 사유.

    *broker 호출 0건* — 순수 분석.
    """
    if criteria is None:
        criteria = FinalCandidateCriteria()
    if now is None:
        now = datetime.now(timezone.utc)

    qualified: list[tuple[CandidateInput, float, list[str]]] = []
    excluded: list[ExcludedCandidate] = []

    for inp in inputs:
        ok, reasons, flags = _qualify(inp, criteria)
        if ok:
            score = _composite_score(inp, criteria)
            qualified.append((inp, score, flags))
        else:
            excluded.append(ExcludedCandidate(
                name=inp.name,
                symbol=inp.symbol,
                exclusion_reasons=reasons,
                risk_flags=flags,
                measurements={
                    "expectancy":           inp.expectancy,
                    "profit_factor":        inp.profit_factor,
                    "max_drawdown":         inp.max_drawdown,
                    "trade_count":          int(inp.trade_count),
                    "paper_candidate_status": inp.paper_candidate_status,
                    "walk_forward_verdict": inp.walk_forward_verdict,
                    "stress_verdict":       inp.stress_verdict,
                    "combo_verdict":        inp.combo_verdict,
                    "regime_combo_verdict": inp.regime_combo_verdict,
                    "combo_risk_verdict":   inp.combo_risk_verdict,
                    "primary_regime":       inp.primary_regime,
                },
            ))

    # 정렬 — composite_score desc.
    qualified.sort(key=lambda t: t[1], reverse=True)
    top_n = qualified[: criteria.max_candidates]

    candidates: list[PaperCandidate] = []
    for rank, (inp, score, flags) in enumerate(top_n, start=1):
        candidates.append(_build_candidate(
            rank=rank, inp=inp, score=score, flags=flags,
        ))

    # status 결정.
    reasons_no_candidate: list[str] = []
    if not candidates:
        status = SelectionStatus.NO_CANDIDATE
        if not list(inputs) and not excluded:
            reasons_no_candidate.append("입력 데이터 0건")
        else:
            reasons_no_candidate.append(
                f"모든 입력 {len(excluded)}건이 7 조건 중 하나 이상 실패"
            )
        # 가장 흔한 제외 사유 5개 carry.
        from collections import Counter
        flag_counts = Counter()
        for e in excluded:
            for f in e.risk_flags:
                flag_counts[f] += 1
        top_flags = flag_counts.most_common(5)
        for flag, c in top_flags:
            reasons_no_candidate.append(f"{flag}: {c}건")
    elif len(candidates) == 1:
        status = SelectionStatus.MIN_CANDIDATES
    else:
        status = SelectionStatus.OK

    notes: list[str] = [
        "선정된 후보도 requires_operator_approval=True 영구.",
        "Paper Auto Loop 자동 연결 0건 — 운영자 명시 승인 + 별도 PR 후에만 사용.",
        f"통과: {len(candidates)} / 제외: {len(excluded)}",
    ]

    return FinalCandidateReport(
        generated_at=now.isoformat(),
        schema_version=FINAL_CANDIDATE_SCHEMA_VERSION,
        status=status,
        period_label=period_label,
        candidates=candidates,
        excluded=excluded,
        reasons_no_candidate=reasons_no_candidate,
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


def render_markdown(report: FinalCandidateReport) -> str:
    lines: list[str] = []
    lines.append("# 최종 Paper 조합 후보 리포트")
    lines.append("")
    lines.append("> *advisory* — Paper Auto Loop 자동 연결 0건.")
    lines.append("> 선정된 후보도 `requires_operator_approval=True` 영구.")
    lines.append("")
    lines.append(f"- 생성: `{report.generated_at}`")
    lines.append(f"- schema_version: `{report.schema_version}`")
    lines.append(f"- 상태: **{report.status.value}**")
    lines.append(f"- 기간: `{report.period_label}`")
    lines.append(f"- 통과: {len(report.candidates)} / 제외: {len(report.excluded)}")
    lines.append("")

    if report.candidates:
        lines.append("## 선정 후보 (rank 순)")
        lines.append("")
        for c in report.candidates:
            lines.append(f"### #{c.rank} `{c.name}` — {c.symbol}")
            lines.append("")
            lines.append(f"- 매매기법군: `{', '.join(c.included_tactics)}`")
            lines.append(f"- 전략: `{', '.join(c.included_strategies)}`")
            lines.append(f"- 장세: `{c.primary_regime}`")
            lines.append(f"- composite_score: **{c.composite_score:.4f}**")
            lines.append("")
            lines.append("**성과지표**")
            lines.append("")
            lines.append(f"- trade_count: {c.trade_count}")
            lines.append(f"- expectancy: {_fmt(c.expectancy)}")
            lines.append(f"- profit_factor: {_fmt(c.profit_factor)}")
            lines.append(f"- max_drawdown: {_fmt(c.max_drawdown)}")
            lines.append(f"- win_rate: {_fmt(c.win_rate)}")
            lines.append(f"- loss_streak: {c.loss_streak}")
            lines.append("")
            lines.append("**위험지표**")
            lines.append("")
            lines.append(f"- correlation_score: {_fmt(c.correlation_score)}")
            lines.append(f"- concentration_score: {_fmt(c.concentration_score)}")
            lines.append(f"- confirmation_score: {c.confirmation_score}")
            lines.append("")
            lines.append("**verdict carry**")
            lines.append("")
            lines.append(f"- paper_candidate_status: `{c.paper_candidate_status}`")
            lines.append(f"- walk_forward: `{c.walk_forward_verdict}`")
            lines.append(f"- stress: `{c.stress_verdict}`")
            lines.append(f"- combo: `{c.combo_verdict}`")
            lines.append(f"- regime_combo: `{c.regime_combo_verdict}`")
            lines.append(f"- combo_risk: `{c.combo_risk_verdict}`")
            lines.append("")
            lines.append("**추천 사유**")
            lines.append("")
            for r in c.recommended_reasons:
                lines.append(f"- {r}")
            if c.risk_flags:
                lines.append("")
                lines.append("**위험 flag**")
                lines.append("")
                for f in c.risk_flags:
                    lines.append(f"- `{f}`")
            lines.append("")
            lines.append(f"> {c.operator_note}")
            lines.append("")
    else:
        lines.append("## 후보 없음")
        lines.append("")
        for r in report.reasons_no_candidate:
            lines.append(f"- {r}")
        lines.append("")

    if report.excluded:
        lines.append("## 제외 후보")
        lines.append("")
        for e in report.excluded[:20]:   # 상위 20개만.
            lines.append(f"### `{e.name}` — {e.symbol}")
            lines.append("")
            for r in e.exclusion_reasons[:5]:
                lines.append(f"- {r}")
            lines.append("")
        if len(report.excluded) > 20:
            lines.append(f"_( {len(report.excluded) - 20}개 추가 제외 — JSON / CSV 참조 )_")
            lines.append("")

    if report.notes:
        lines.append("## 노트")
        lines.append("")
        for n in report.notes:
            lines.append(f"- {n}")
        lines.append("")

    lines.append("## 안전 invariant")
    lines.append("")
    lines.append("- `is_order_signal=False` / `auto_apply_allowed=False` / `is_live_authorization=False`")
    lines.append("- `requires_operator_approval=True` 영구 — 자동 Paper Auto Loop 연결 0건")
    lines.append("- broker / OrderExecutor / route_order 호출 0건")
    lines.append("")
    lines.append(report.advisory_disclaimer)
    lines.append("")
    return "\n".join(lines)


def render_ranking_csv(report: FinalCandidateReport) -> str:
    headers = [
        "rank", "name", "symbol", "primary_regime",
        "composite_score", "expectancy", "profit_factor",
        "max_drawdown", "win_rate", "trade_count",
        "walk_forward_verdict", "stress_verdict",
        "combo_verdict", "regime_combo_verdict", "combo_risk_verdict",
        "requires_operator_approval",
    ]
    out: list[str] = [",".join(headers)]
    for c in report.candidates:
        out.append(",".join([
            str(c.rank), c.name, c.symbol, c.primary_regime,
            _fmt(c.composite_score),
            _fmt(c.expectancy), _fmt(c.profit_factor),
            _fmt(c.max_drawdown), _fmt(c.win_rate),
            str(int(c.trade_count)),
            c.walk_forward_verdict, c.stress_verdict,
            c.combo_verdict, c.regime_combo_verdict, c.combo_risk_verdict,
            "true",   # 영구
        ]))
    return "\n".join(out) + "\n"


def write_reports(
    report:  FinalCandidateReport,
    out_dir: Path | str,
) -> dict[str, Path]:
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    json_path = out / "final_paper_candidates_summary.json"
    md_path   = out / "final_paper_candidates_report.md"
    csv_path  = out / "final_paper_candidates_ranking.csv"
    json_path.write_text(
        _json.dumps(report.to_dict(), ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    md_path.write_text(render_markdown(report), encoding="utf-8")
    csv_path.write_text(render_ranking_csv(report), encoding="utf-8")
    return {"summary_json": json_path, "report_md": md_path, "ranking_csv": csv_path}


__all__ = [
    "FINAL_CANDIDATE_SCHEMA_VERSION",
    "SelectionStatus",
    "FinalCandidateCriteria",
    "CandidateInput",
    "PaperCandidate",
    "ExcludedCandidate",
    "FinalCandidateReport",
    "select_paper_candidates",
    "render_markdown",
    "render_ranking_csv",
    "write_reports",
]

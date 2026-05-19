"""#3-12: 전략 조합 백테스트 — 4 매매기법군 × 6 전략 후보의 조합 분석.

단독 전략뿐 아니라 *조합* 의 signal-level 중복 / 충돌 / 확인 점수를 산출해
AI Agent 가 참고할 수 있는 advisory 성과표를 만든다. 실거래 호출 0건.

## 4 매매기법군

| Tactic Group         | 후보 전략 |
|---|---|
| MOMENTUM             | sma_crossover, volume_breakout |
| REVERSION            | rsi_reversion |
| VWAP                 | vwap_strategy |
| ORB_PULLBACK         | orb_vwap, pullback_rebreak |

## 조합 enumeration

- 단일 매매기법군 (4 combos)
- 2개 매매기법군 (6 combos)
- 3개 매매기법군 (4 combos)
- 4개 매매기법군 전체 (1 combo)
= 총 15 조합 카탈로그.

각 combo 의 `included_strategies` 는 included_tactics 에 매핑된 *모든*
strategy_id 의 합집합.

## Signal-level scoring

caller 가 `StrategySignal` list (strategy_id / symbol / day_key / direction /
score) 를 입력. 같은 day_key + symbol 에서:
- **overlap_count**: 같은 day+symbol 에서 *같은 조합 안에 속한* signal 이
  2개 이상이면 카운트.
- **confirmation_score**: 같은 day+symbol + 같은 direction + *서로 다른 tactic
  group* signal 수의 합 (Agent 신뢰도 가산).
- **conflict_count**: 같은 day+symbol 에서 BUY 와 SELL 이 함께 나오면 +1.

## verdict 매트릭스

```
PASS               : trade_count >= min_trades AND expectancy > 0
                     AND profit_factor >= 1.2 AND |max_drawdown| <= 0.20
                     AND conflict_ratio <= 0.30
WARN               : 일부 지표 통과, 한 개 이상 boundary 근접
FAIL               : expectancy <= 0 OR profit_factor < 1.0
                     OR |max_drawdown| > 0.30
INSUFFICIENT_DATA  : trade_count < min_trades OR signal_count == 0
```

## 절대 invariant (CLAUDE.md 절대 원칙 1~5 상속)

1. broker / OrderExecutor / route_order import 0건 (정적 grep).
2. `ComboResult.is_order_signal=False` / `auto_apply_allowed=False` /
   `is_live_authorization=False` / `recommended_for_paper=False` (기본값)
   영구. AI Agent context 출력은 *advisory* — Paper 후보 자동 적용 0건.
3. 외부 HTTP / AI SDK / LLM import 0건.
4. DB write 0건 — 순수 분석 + 파일 출력.
5. `is_live_authorization=False` — PASS 라벨도 *실거래 허가 아님*.
"""

from __future__ import annotations

import json as _json
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import StrEnum
from itertools import combinations
from pathlib import Path
from typing import Any, Iterable


COMBO_SCHEMA_VERSION = "1.0"


# ─────────────────────────────────────────────────────────────────────────────
# Tactic groups + strategy catalog
# ─────────────────────────────────────────────────────────────────────────────


class TacticGroup(StrEnum):
    MOMENTUM       = "MOMENTUM"
    REVERSION      = "REVERSION"
    VWAP           = "VWAP"
    ORB_PULLBACK   = "ORB_PULLBACK"


_TACTIC_TO_STRATEGIES: dict[TacticGroup, tuple[str, ...]] = {
    TacticGroup.MOMENTUM:     ("sma_crossover", "volume_breakout"),
    TacticGroup.REVERSION:    ("rsi_reversion",),
    TacticGroup.VWAP:         ("vwap_strategy",),
    TacticGroup.ORB_PULLBACK: ("orb_vwap", "pullback_rebreak"),
}


_STRATEGY_TO_TACTIC: dict[str, TacticGroup] = {
    s: g for g, strats in _TACTIC_TO_STRATEGIES.items() for s in strats
}


_TACTIC_LABEL_KO: dict[TacticGroup, str] = {
    TacticGroup.MOMENTUM:     "추세추종 / Momentum",
    TacticGroup.REVERSION:    "평균회귀 / Reversion",
    TacticGroup.VWAP:         "VWAP / 장중 기준가",
    TacticGroup.ORB_PULLBACK: "장초반 돌파 / Pullback-Rebreak",
}


# ─────────────────────────────────────────────────────────────────────────────
# Verdict + DTOs
# ─────────────────────────────────────────────────────────────────────────────


class ComboVerdict(StrEnum):
    PASS              = "PASS"
    WARN              = "WARN"
    FAIL              = "FAIL"
    INSUFFICIENT_DATA = "INSUFFICIENT_DATA"


@dataclass(frozen=True)
class StrategySignal:
    """단일 signal — caller (백테스트 러너 / 데이터 로더) 가 채워 전달."""
    strategy_id:   str
    symbol:        str
    day_key:       str        # ISO date or bar timestamp string
    direction:     str        # BUY / SELL / HOLD / EXIT — 추가 라벨 허용
    score:         float     = 0.0
    realized_pnl:  float     = 0.0    # per-share or per-trade PnL — 단위는 caller 책임

    def __post_init__(self) -> None:
        if not self.strategy_id or not self.symbol:
            raise ValueError("strategy_id / symbol must be non-empty")
        if self.strategy_id not in _STRATEGY_TO_TACTIC:
            raise ValueError(
                f"unknown strategy_id={self.strategy_id!r}; "
                f"expected one of {list(_STRATEGY_TO_TACTIC.keys())}"
            )


@dataclass(frozen=True)
class ComboCriteria:
    """verdict 임계 — 운영자가 호출 시 override 가능."""
    min_trades:                int   = 10
    min_profit_factor:         float = 1.2
    pass_profit_factor:        float = 1.2
    fail_profit_factor:        float = 1.0
    pass_max_drawdown_abs:     float = 0.20
    fail_max_drawdown_abs:     float = 0.30
    pass_conflict_ratio:       float = 0.30
    fee_rate:                  float = 0.001   # 0.1% per side (advisory)
    slippage_rate:             float = 0.0005

    def __post_init__(self) -> None:
        if self.min_trades < 1:
            raise ValueError("min_trades must be >= 1")
        if not (0.0 < self.pass_profit_factor):
            raise ValueError("pass_profit_factor must be > 0")


@dataclass(frozen=True)
class ComboResult:
    """단일 조합의 결과 — *advisory*."""

    combo_name:               str
    included_tactics:         tuple[str, ...]
    included_strategies:      tuple[str, ...]
    symbol:                   str | None

    signal_count:             int                  = 0
    trade_count:              int                  = 0
    overlap_count:            int                  = 0
    conflict_count:           int                  = 0
    confirmation_score:       int                  = 0
    conflict_ratio:           float                = 0.0

    total_return:             float                = 0.0
    expectancy:               float | None         = None
    profit_factor:            float | None         = None
    max_drawdown:             float | None         = None
    win_rate:                 float | None         = None
    loss_streak:              int                  = 0
    risk_adjusted_score:      float | None         = None
    fee_adjusted_return:      float | None         = None
    slippage_adjusted_return: float | None         = None

    combo_verdict:            ComboVerdict         = ComboVerdict.INSUFFICIENT_DATA
    reasons:                  list[str]            = field(default_factory=list)
    risk_flags:               list[str]            = field(default_factory=list)

    # AI Agent context — *advisory only*.
    agent_context_ready:      bool                 = True
    recommended_for_paper:    bool                 = False    # 자동 적용 X

    # 절대 invariant.
    is_order_signal:          bool                 = False
    auto_apply_allowed:       bool                 = False
    is_live_authorization:    bool                 = False

    def __post_init__(self) -> None:
        for name, val in (
            ("is_order_signal",       self.is_order_signal),
            ("auto_apply_allowed",    self.auto_apply_allowed),
            ("is_live_authorization", self.is_live_authorization),
        ):
            if val is not False:
                raise ValueError(f"ComboResult.{name} must be False.")
        if not isinstance(self.combo_verdict, ComboVerdict):
            raise ValueError("combo_verdict must be ComboVerdict.")
        if self.signal_count < 0 or self.trade_count < 0:
            raise ValueError("counts must be >= 0")

    def to_dict(self) -> dict[str, Any]:
        return {
            "combo_name":               self.combo_name,
            "included_tactics":         list(self.included_tactics),
            "included_strategies":      list(self.included_strategies),
            "symbol":                   self.symbol,
            "signal_count":             int(self.signal_count),
            "trade_count":              int(self.trade_count),
            "overlap_count":            int(self.overlap_count),
            "conflict_count":           int(self.conflict_count),
            "confirmation_score":       int(self.confirmation_score),
            "conflict_ratio":           float(self.conflict_ratio),
            "total_return":             float(self.total_return),
            "expectancy":               self.expectancy,
            "profit_factor":            self.profit_factor,
            "max_drawdown":             self.max_drawdown,
            "win_rate":                 self.win_rate,
            "loss_streak":              int(self.loss_streak),
            "risk_adjusted_score":      self.risk_adjusted_score,
            "fee_adjusted_return":      self.fee_adjusted_return,
            "slippage_adjusted_return": self.slippage_adjusted_return,
            "combo_verdict":            self.combo_verdict.value,
            "reasons":                  list(self.reasons),
            "risk_flags":               list(self.risk_flags),
            "agent_context_ready":      bool(self.agent_context_ready),
            "recommended_for_paper":    bool(self.recommended_for_paper),
            "is_order_signal":          False,
            "auto_apply_allowed":       False,
            "is_live_authorization":    False,
        }


@dataclass(frozen=True)
class ComboBacktestReport:
    """전체 조합 결과 묶음."""

    generated_at:           str
    schema_version:         str
    symbol:                 str | None
    results:                list[ComboResult]
    criteria:               ComboCriteria
    notes:                  list[str]               = field(default_factory=list)

    advisory_disclaimer:    str = (
        "본 리포트는 *advisory* — 전략 조합 백테스트 분석만, 실거래 주문 0건. "
        "PASS 라벨도 실거래 활성화 / Paper 후보 자동 적용 허가가 아니며, "
        "운영자 명시 검토 + 별도 PR 후에만 Paper 후보 / Live 진입이 가능합니다. "
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
                raise ValueError(f"ComboBacktestReport.{name} must be False.")

    def to_dict(self) -> dict[str, Any]:
        return {
            "generated_at":         self.generated_at,
            "schema_version":       self.schema_version,
            "symbol":               self.symbol,
            "combo_count":          len(self.results),
            "results":              [r.to_dict() for r in self.results],
            "criteria":             {
                "min_trades":            self.criteria.min_trades,
                "pass_profit_factor":    self.criteria.pass_profit_factor,
                "fail_profit_factor":    self.criteria.fail_profit_factor,
                "pass_max_drawdown_abs": self.criteria.pass_max_drawdown_abs,
                "fail_max_drawdown_abs": self.criteria.fail_max_drawdown_abs,
                "pass_conflict_ratio":   self.criteria.pass_conflict_ratio,
                "fee_rate":              self.criteria.fee_rate,
                "slippage_rate":         self.criteria.slippage_rate,
            },
            "notes":                list(self.notes),
            "advisory_disclaimer":  self.advisory_disclaimer,
            "is_order_signal":      False,
            "auto_apply_allowed":   False,
            "is_live_authorization": False,
        }


# ─────────────────────────────────────────────────────────────────────────────
# Catalog: enumerate all combinations of tactic groups (size 1..4)
# ─────────────────────────────────────────────────────────────────────────────


def enumerate_combinations() -> list[tuple[TacticGroup, ...]]:
    """4 매매기법군의 size 1..4 모든 조합 — 총 15개 (4+6+4+1)."""
    groups = list(TacticGroup)
    out: list[tuple[TacticGroup, ...]] = []
    for r in range(1, len(groups) + 1):
        for combo in combinations(groups, r):
            out.append(tuple(combo))
    return out


def combo_strategies(tactics: Iterable[TacticGroup]) -> tuple[str, ...]:
    """tactics 의 합집합 strategy_id."""
    out: list[str] = []
    for t in tactics:
        for s in _TACTIC_TO_STRATEGIES[t]:
            if s not in out:
                out.append(s)
    return tuple(out)


def combo_name(tactics: Iterable[TacticGroup]) -> str:
    return "+".join(t.value for t in tactics)


# ─────────────────────────────────────────────────────────────────────────────
# Signal-level metrics
# ─────────────────────────────────────────────────────────────────────────────


def _signals_for_combo(
    signals:      list[StrategySignal],
    tactic_set:   set[TacticGroup],
) -> list[StrategySignal]:
    return [
        s for s in signals
        if _STRATEGY_TO_TACTIC[s.strategy_id] in tactic_set
    ]


def _normalize_direction(d: str) -> str:
    return (d or "").strip().upper()


def _signal_level_metrics(
    sigs: list[StrategySignal],
) -> tuple[int, int, int, float]:
    """(overlap_count, conflict_count, confirmation_score, conflict_ratio).

    overlap        : 같은 (day, symbol) 에 2개 이상 signal.
    conflict       : 같은 (day, symbol) 에 BUY + SELL 동시 등장.
    confirmation   : 같은 (day, symbol, direction) 에 서로 다른 *tactic group*
                     signal 수의 합 (BUY/SELL 양방향 모두).
    conflict_ratio : conflict_count / max(signal_count, 1) — 0~1 clamp.
    """
    if not sigs:
        return 0, 0, 0, 0.0

    by_key: dict[tuple[str, str], list[StrategySignal]] = {}
    for s in sigs:
        by_key.setdefault((s.day_key, s.symbol), []).append(s)

    overlap = 0
    conflict = 0
    confirmation = 0
    for _, group in by_key.items():
        if len(group) >= 2:
            overlap += 1
        dirs = {_normalize_direction(s.direction) for s in group}
        if "BUY" in dirs and "SELL" in dirs:
            conflict += 1
        # confirmation per direction.
        for direction in ("BUY", "SELL"):
            tactics_in_dir = {
                _STRATEGY_TO_TACTIC[s.strategy_id]
                for s in group
                if _normalize_direction(s.direction) == direction
            }
            if len(tactics_in_dir) >= 2:
                confirmation += len(tactics_in_dir)
    n = max(len(sigs), 1)
    conflict_ratio = max(0.0, min(1.0, float(conflict) / float(n)))
    return overlap, conflict, confirmation, conflict_ratio


def _trade_pnls(sigs: list[StrategySignal]) -> list[float]:
    """체결로 간주되는 BUY/SELL/EXIT 의 realized_pnl 리스트."""
    out: list[float] = []
    for s in sigs:
        if _normalize_direction(s.direction) in ("BUY", "SELL", "EXIT"):
            out.append(float(s.realized_pnl))
    return out


def _max_drawdown_abs(pnls: list[float]) -> float | None:
    """누적 수익 곡선의 최저 trough — 절대값 (0~total_return)."""
    if not pnls:
        return None
    cum = 0.0
    peak = 0.0
    mdd = 0.0
    for p in pnls:
        cum += p
        if cum > peak:
            peak = cum
        dd = peak - cum
        if dd > mdd:
            mdd = dd
    return float(mdd)


def _profit_factor(pnls: list[float]) -> float | None:
    if not pnls:
        return None
    pos = sum(p for p in pnls if p > 0)
    neg = -sum(p for p in pnls if p < 0)
    if neg <= 0:
        return None if pos == 0 else float(pos)
    return float(pos) / float(neg)


def _win_rate(pnls: list[float]) -> float | None:
    if not pnls:
        return None
    return float(sum(1 for p in pnls if p > 0)) / float(len(pnls))


def _loss_streak(pnls: list[float]) -> int:
    cur, best = 0, 0
    for p in pnls:
        if p < 0:
            cur += 1
            best = max(cur, best)
        else:
            cur = 0
    return int(best)


def _expectancy(pnls: list[float]) -> float | None:
    if not pnls:
        return None
    return float(sum(pnls)) / float(len(pnls))


def _risk_adjusted(pnls: list[float], mdd: float | None) -> float | None:
    """Sharpe-like advisory — sum(pnl) / |MDD|. MDD=0 → sum(pnl) carry."""
    if not pnls:
        return None
    total = float(sum(pnls))
    if mdd is None or mdd == 0:
        return total
    return total / float(mdd)


# ─────────────────────────────────────────────────────────────────────────────
# Verdict computation
# ─────────────────────────────────────────────────────────────────────────────


def _verdict_and_reasons(
    *,
    trade_count:        int,
    signal_count:       int,
    expectancy:         float | None,
    profit_factor:      float | None,
    max_drawdown:       float | None,
    conflict_ratio:     float,
    overlap_count:      int,
    criteria:           ComboCriteria,
) -> tuple[ComboVerdict, list[str], list[str]]:
    reasons: list[str] = []
    risk_flags: list[str] = []

    if signal_count == 0:
        return (
            ComboVerdict.INSUFFICIENT_DATA,
            ["signal_count == 0 — 입력 데이터 부족"],
            ["insufficient_data"],
        )
    if trade_count < criteria.min_trades:
        return (
            ComboVerdict.INSUFFICIENT_DATA,
            [
                f"trade_count={trade_count} < min_trades={criteria.min_trades} "
                f"— 거래 표본 부족"
            ],
            ["insufficient_trades"],
        )
    if expectancy is None or profit_factor is None or max_drawdown is None:
        return (
            ComboVerdict.INSUFFICIENT_DATA,
            ["metric 계산 불가 — pnl 데이터 부족"],
            ["insufficient_pnl"],
        )

    # FAIL 우선.
    if expectancy <= 0:
        reasons.append(f"expectancy={expectancy:.4f} <= 0")
        risk_flags.append("non_positive_expectancy")
    if profit_factor < criteria.fail_profit_factor:
        reasons.append(
            f"profit_factor={profit_factor:.3f} < fail_threshold="
            f"{criteria.fail_profit_factor:.2f}"
        )
        risk_flags.append("low_profit_factor")
    if abs(max_drawdown) > criteria.fail_max_drawdown_abs:
        reasons.append(
            f"max_drawdown={max_drawdown:.4f} 초과 fail_threshold="
            f"{criteria.fail_max_drawdown_abs:.2f}"
        )
        risk_flags.append("high_drawdown")
    if risk_flags:
        return ComboVerdict.FAIL, reasons, risk_flags

    # WARN — boundary 근접.
    warn = False
    if profit_factor < criteria.pass_profit_factor:
        reasons.append(
            f"profit_factor={profit_factor:.3f} < pass_threshold="
            f"{criteria.pass_profit_factor:.2f} — boundary"
        )
        risk_flags.append("profit_factor_boundary")
        warn = True
    if abs(max_drawdown) > criteria.pass_max_drawdown_abs:
        reasons.append(
            f"|max_drawdown|={abs(max_drawdown):.4f} > pass_threshold="
            f"{criteria.pass_max_drawdown_abs:.2f} — boundary"
        )
        risk_flags.append("drawdown_boundary")
        warn = True
    if conflict_ratio > criteria.pass_conflict_ratio:
        reasons.append(
            f"conflict_ratio={conflict_ratio:.2f} > pass_threshold="
            f"{criteria.pass_conflict_ratio:.2f} — 충돌 신호 과다"
        )
        risk_flags.append("high_conflict")
        warn = True
    if overlap_count >= max(trade_count, 1):
        # 거의 모든 거래가 중복 신호 — 집중 위험.
        reasons.append(
            f"overlap_count={overlap_count} >= trade_count={trade_count} — "
            f"중복 신호 과다"
        )
        risk_flags.append("high_overlap")
        warn = True

    if warn:
        return ComboVerdict.WARN, reasons, risk_flags

    reasons.append("모든 PASS 기준 통과 — advisory")
    return ComboVerdict.PASS, reasons, risk_flags


# ─────────────────────────────────────────────────────────────────────────────
# Main entry — compute_combo_metrics
# ─────────────────────────────────────────────────────────────────────────────


def compute_combo_metrics(
    *,
    tactics:    tuple[TacticGroup, ...],
    signals:    list[StrategySignal],
    symbol:     str | None     = None,
    criteria:   ComboCriteria  = None,    # type: ignore[assignment]
) -> ComboResult:
    if criteria is None:
        criteria = ComboCriteria()

    tactic_set = set(tactics)
    sigs = _signals_for_combo(signals, tactic_set)

    overlap, conflict, confirmation, conflict_ratio = _signal_level_metrics(sigs)
    pnls = _trade_pnls(sigs)
    total = float(sum(pnls)) if pnls else 0.0
    expectancy = _expectancy(pnls)
    profit_factor = _profit_factor(pnls)
    mdd = _max_drawdown_abs(pnls)
    win_rate = _win_rate(pnls)
    streak = _loss_streak(pnls)
    ras = _risk_adjusted(pnls, mdd)
    fee_adj = (
        float(sum(p - abs(p) * criteria.fee_rate for p in pnls))
        if pnls else None
    )
    slip_adj = (
        float(sum(p - abs(p) * criteria.slippage_rate for p in pnls))
        if pnls else None
    )

    verdict, reasons, risk_flags = _verdict_and_reasons(
        trade_count=len(pnls),
        signal_count=len(sigs),
        expectancy=expectancy,
        profit_factor=profit_factor,
        max_drawdown=mdd,
        conflict_ratio=conflict_ratio,
        overlap_count=overlap,
        criteria=criteria,
    )

    return ComboResult(
        combo_name=combo_name(tactics),
        included_tactics=tuple(t.value for t in tactics),
        included_strategies=combo_strategies(tactics),
        symbol=symbol,
        signal_count=len(sigs),
        trade_count=len(pnls),
        overlap_count=overlap,
        conflict_count=conflict,
        confirmation_score=confirmation,
        conflict_ratio=conflict_ratio,
        total_return=total,
        expectancy=expectancy,
        profit_factor=profit_factor,
        max_drawdown=mdd,
        win_rate=win_rate,
        loss_streak=streak,
        risk_adjusted_score=ras,
        fee_adjusted_return=fee_adj,
        slippage_adjusted_return=slip_adj,
        combo_verdict=verdict,
        reasons=reasons,
        risk_flags=risk_flags,
        # 자동 Paper 적용 절대 금지 — verdict 와 무관 영구 False.
        recommended_for_paper=False,
    )


def run_combo_backtest(
    *,
    signals:        list[StrategySignal],
    symbol:         str | None      = None,
    criteria:       ComboCriteria   = None,    # type: ignore[assignment]
    only_sizes:     Iterable[int] | None = None,   # 1..4 — None=all
    now:            datetime | None = None,
) -> ComboBacktestReport:
    if criteria is None:
        criteria = ComboCriteria()
    if now is None:
        now = datetime.now(timezone.utc)
    catalog = enumerate_combinations()
    if only_sizes:
        wanted = set(int(x) for x in only_sizes)
        catalog = [c for c in catalog if len(c) in wanted]

    notes: list[str] = []
    notes.append(
        "기본 추천은 *없음* — 본 리포트는 advisory. Paper 후보 자동 적용 0건."
    )
    if not signals:
        notes.append("INSUFFICIENT_DATA: 입력 signals == 0")

    results: list[ComboResult] = []
    for tactics in catalog:
        results.append(
            compute_combo_metrics(
                tactics=tactics,
                signals=signals,
                symbol=symbol,
                criteria=criteria,
            ),
        )

    return ComboBacktestReport(
        generated_at=now.isoformat(),
        schema_version=COMBO_SCHEMA_VERSION,
        symbol=symbol,
        results=results,
        criteria=criteria,
        notes=notes,
    )


# ─────────────────────────────────────────────────────────────────────────────
# Renderers — JSON / CSV / Markdown
# ─────────────────────────────────────────────────────────────────────────────


def _fmt(v: Any, places: int = 4) -> str:
    if v is None:
        return "—"
    if isinstance(v, float):
        return f"{v:.{places}f}"
    return str(v)


def render_markdown(report: ComboBacktestReport) -> str:
    lines: list[str] = []
    lines.append("# 전략 조합 백테스트 리포트")
    lines.append("")
    lines.append("> *advisory* — 전략 조합 분석만, 실거래 주문 0건.")
    lines.append("> PASS 라벨도 실거래 / Paper 후보 자동 적용 허가가 아닙니다.")
    lines.append("")
    lines.append(f"- 생성: `{report.generated_at}`")
    lines.append(f"- schema_version: `{report.schema_version}`")
    lines.append(f"- symbol: `{report.symbol or '—'}`")
    lines.append(f"- combo 수: **{len(report.results)}** (전체 15 중)")
    lines.append("")

    # 4 tactic 카탈로그.
    lines.append("## 매매기법군")
    lines.append("")
    lines.append("| 그룹 | 한글 | 전략 |")
    lines.append("|---|---|---|")
    for g in TacticGroup:
        strats = ", ".join(_TACTIC_TO_STRATEGIES[g])
        lines.append(f"| `{g.value}` | {_TACTIC_LABEL_KO[g]} | {strats} |")
    lines.append("")

    # 결과 매트릭스.
    lines.append("## 조합 결과")
    lines.append("")
    lines.append(
        "| combo | verdict | signal | trade | overlap | conflict | "
        "confirmation | expectancy | profit_factor | max_drawdown | win_rate | "
        "risk_adjusted |"
    )
    lines.append("|" + "|".join(["---"] * 12) + "|")
    for r in report.results:
        lines.append(
            f"| `{r.combo_name}` | **{r.combo_verdict.value}** | "
            f"{r.signal_count} | {r.trade_count} | "
            f"{r.overlap_count} | {r.conflict_count} | "
            f"{r.confirmation_score} | "
            f"{_fmt(r.expectancy)} | {_fmt(r.profit_factor)} | "
            f"{_fmt(r.max_drawdown)} | {_fmt(r.win_rate)} | "
            f"{_fmt(r.risk_adjusted_score)} |"
        )
    lines.append("")

    # 비교 노트.
    if report.notes:
        lines.append("## 노트")
        for n in report.notes:
            lines.append(f"- {n}")
        lines.append("")

    # invariants.
    lines.append("## 안전 invariant")
    lines.append("")
    lines.append(
        "- `is_order_signal=False` / `auto_apply_allowed=False` / "
        "`is_live_authorization=False`"
    )
    lines.append("- `recommended_for_paper=False` 영구 — 자동 Paper 후보 적용 0건")
    lines.append("- broker / OrderExecutor / route_order 호출 0건")
    lines.append("")
    lines.append(report.advisory_disclaimer)
    lines.append("")
    return "\n".join(lines)


def render_ranking_csv(report: ComboBacktestReport) -> str:
    headers = [
        "combo_name", "verdict",
        "trade_count", "overlap_count", "conflict_count", "confirmation_score",
        "expectancy", "profit_factor", "max_drawdown", "win_rate",
        "risk_adjusted_score", "fee_adjusted_return", "slippage_adjusted_return",
        "recommended_for_paper",
    ]
    rows = sorted(
        report.results,
        key=lambda r: (
            r.expectancy if r.expectancy is not None else -1e18,
        ),
        reverse=True,
    )
    out: list[str] = [",".join(headers)]
    for r in rows:
        row = [
            r.combo_name,
            r.combo_verdict.value,
            str(int(r.trade_count)),
            str(int(r.overlap_count)),
            str(int(r.conflict_count)),
            str(int(r.confirmation_score)),
            _fmt(r.expectancy),
            _fmt(r.profit_factor),
            _fmt(r.max_drawdown),
            _fmt(r.win_rate),
            _fmt(r.risk_adjusted_score),
            _fmt(r.fee_adjusted_return),
            _fmt(r.slippage_adjusted_return),
            "false",   # 영구 — recommended_for_paper 자동 적용 0건.
        ]
        out.append(",".join(row))
    return "\n".join(out) + "\n"


def write_reports(
    report:   ComboBacktestReport,
    out_dir:  Path | str,
) -> dict[str, Path]:
    """JSON / Markdown / CSV 3 파일 작성."""
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    json_path = out / "strategy_combo_summary.json"
    md_path   = out / "strategy_combo_report.md"
    csv_path  = out / "strategy_combo_ranking.csv"
    json_path.write_text(
        _json.dumps(report.to_dict(), ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    md_path.write_text(render_markdown(report), encoding="utf-8")
    csv_path.write_text(render_ranking_csv(report), encoding="utf-8")
    return {"summary_json": json_path, "report_md": md_path, "ranking_csv": csv_path}


__all__ = [
    "COMBO_SCHEMA_VERSION",
    "TacticGroup",
    "ComboVerdict",
    "StrategySignal",
    "ComboCriteria",
    "ComboResult",
    "ComboBacktestReport",
    "enumerate_combinations",
    "combo_strategies",
    "combo_name",
    "compute_combo_metrics",
    "run_combo_backtest",
    "render_markdown",
    "render_ranking_csv",
    "write_reports",
]

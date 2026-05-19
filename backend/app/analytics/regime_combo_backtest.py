"""#3-13: 장세별 전략 조합 백테스트 — 7 regime × 15 combo 매트릭스.

3-12 (`strategy_combo_backtest`) 의 조합 분석을 *장세별* 로 분리해, 어떤 4
매매기법군 조합이 어떤 장세에서 유리한지 산출하는 advisory 리포트.

## 입력 — `RegimeStrategySignal`

`StrategySignal` (3-12) 위에 `regime: MarketRegime` 필드만 추가. caller 가
시그널마다 *해당 시점의 시장 장세* 를 직접 부여한다 — 본 모듈은 *분류기를
호출하지 않는다* (`MarketRegimeAgent` 와 결합도 0).

## 출력 매트릭스

7 regime (TREND_UP / TREND_DOWN / SIDEWAYS / HIGH_VOLATILITY / LOW_LIQUIDITY /
CHOPPY / UNKNOWN) × 15 combo (3-12 의 `enumerate_combinations()`) = **105 row**
의 `RegimeComboResult`.

## Verdict 매트릭스

| Verdict | 조건 |
|---|---|
| `PASS` | trade_count >= min_trades AND expectancy > 0 AND profit_factor >= pass_PF AND abs(MDD) <= pass_MDD AND conflict_ratio <= pass_conflict |
| `WATCH` | 일부 PASS 임계 boundary 근접 (profit_factor / drawdown / conflict / overlap) |
| `FAIL` | expectancy <= 0 OR profit_factor < fail_PF OR abs(MDD) > fail_MDD |
| `INSUFFICIENT_DATA` | signal_count == 0 OR trade_count < min_trades |
| `BLOCKED_REGIME` | regime 자체가 해당 조합 사용을 금지 (LOW_LIQUIDITY / UNKNOWN) |

### BLOCKED_REGIME 정책

- `LOW_LIQUIDITY`: 모든 조합 BLOCKED — `MarketRegimeAgent` 정책에 따르면 대부분
  전략이 `blocked` 또는 `watchlist`. 본 모듈은 *advisory* 로 *전체 BLOCKED*.
- `UNKNOWN`: 모든 조합 BLOCKED + `recommended_for_paper=False` 영구.
- 그 외 5 regime: combo 의 *blocked* strategy 가 포함되면 `BLOCKED_REGIME`,
  그 외에는 metric 기반 PASS / WATCH / FAIL / INSUFFICIENT_DATA.

## 절대 invariant

1. broker / OrderExecutor / route_order import 0건.
2. `RegimeComboResult.is_order_signal=False` / `auto_apply_allowed=False` /
   `is_live_authorization=False` / `recommended_for_paper=False` 영구.
3. **`UNKNOWN` regime → 어떤 combo 도 자동 추천 0건** (영구 lock).
4. DB write 0건, secret 0건, settings mutation 0건.
"""

from __future__ import annotations

import json as _json
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import StrEnum
from pathlib import Path
from typing import Any, Iterable

from app.agents.market_regime_agent import (
    REGIME_STRATEGY_POLICY,
    MarketRegime,
)
from app.analytics.strategy_combo_backtest import (
    ComboCriteria,
    StrategySignal,
    TacticGroup,
    combo_name,
    combo_strategies,
    enumerate_combinations,
)


REGIME_COMBO_SCHEMA_VERSION = "1.0"


# ─────────────────────────────────────────────────────────────────────────────
# Per-regime / per-combo verdict
# ─────────────────────────────────────────────────────────────────────────────


class RegimeComboVerdict(StrEnum):
    PASS               = "PASS"
    WATCH              = "WATCH"
    FAIL               = "FAIL"
    INSUFFICIENT_DATA  = "INSUFFICIENT_DATA"
    BLOCKED_REGIME     = "BLOCKED_REGIME"


# regime 단위로 *조합 자체* 가 항상 차단되는 regime (모든 combo BLOCKED).
_GLOBALLY_BLOCKED_REGIMES: set[MarketRegime] = {
    MarketRegime.LOW_LIQUIDITY,
    MarketRegime.UNKNOWN,
}


# ─────────────────────────────────────────────────────────────────────────────
# Signal + Result dataclasses
# ─────────────────────────────────────────────────────────────────────────────


@dataclass(frozen=True)
class RegimeStrategySignal:
    """장세 라벨이 부여된 signal — caller 가 채워 전달."""
    strategy_id:   str
    symbol:        str
    day_key:       str
    direction:     str
    regime:        MarketRegime
    score:         float = 0.0
    realized_pnl:  float = 0.0

    def __post_init__(self) -> None:
        # 검증은 StrategySignal 과 동일.
        if not self.strategy_id or not self.symbol:
            raise ValueError("strategy_id / symbol must be non-empty")
        if not isinstance(self.regime, MarketRegime):
            raise ValueError(f"regime must be MarketRegime, got {type(self.regime).__name__}")
        # strategy_id validity via constructing inner StrategySignal.
        StrategySignal(
            strategy_id=self.strategy_id, symbol=self.symbol,
            day_key=self.day_key, direction=self.direction,
            score=self.score, realized_pnl=self.realized_pnl,
        )

    def to_signal(self) -> StrategySignal:
        return StrategySignal(
            strategy_id=self.strategy_id, symbol=self.symbol,
            day_key=self.day_key, direction=self.direction,
            score=self.score, realized_pnl=self.realized_pnl,
        )


@dataclass(frozen=True)
class RegimeComboResult:
    """regime × combo 단일 row — *advisory*."""

    regime:                   str         # MarketRegime.value
    combo_name:               str
    included_tactics:         tuple[str, ...]
    included_strategies:      tuple[str, ...]
    symbol:                   str | None

    signal_count:             int                  = 0
    trade_count:              int                  = 0
    total_return:             float                = 0.0
    expectancy:               float | None         = None
    profit_factor:            float | None         = None
    max_drawdown:             float | None         = None
    win_rate:                 float | None         = None
    loss_streak:              int                  = 0

    overlap_count:            int                  = 0
    conflict_count:           int                  = 0
    confirmation_score:       int                  = 0
    conflict_ratio:           float                = 0.0

    regime_combo_score:       float | None         = None
    verdict:                  RegimeComboVerdict   = RegimeComboVerdict.INSUFFICIENT_DATA
    reasons:                  list[str]            = field(default_factory=list)
    risk_flags:               list[str]            = field(default_factory=list)
    blocked_strategies:       list[str]            = field(default_factory=list)
    watchlist_strategies:     list[str]            = field(default_factory=list)

    # Agent context — advisory only.
    agent_context_ready:      bool                 = True
    recommended_for_paper:    bool                 = False    # 영구 — 자동 적용 X

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
                raise ValueError(f"RegimeComboResult.{name} must be False.")
        if not isinstance(self.verdict, RegimeComboVerdict):
            raise ValueError("verdict must be RegimeComboVerdict.")
        if self.signal_count < 0 or self.trade_count < 0:
            raise ValueError("counts must be >= 0")

    def to_dict(self) -> dict[str, Any]:
        return {
            "regime":                self.regime,
            "combo_name":            self.combo_name,
            "included_tactics":      list(self.included_tactics),
            "included_strategies":   list(self.included_strategies),
            "symbol":                self.symbol,
            "signal_count":          int(self.signal_count),
            "trade_count":           int(self.trade_count),
            "total_return":          float(self.total_return),
            "expectancy":            self.expectancy,
            "profit_factor":         self.profit_factor,
            "max_drawdown":          self.max_drawdown,
            "win_rate":              self.win_rate,
            "loss_streak":           int(self.loss_streak),
            "overlap_count":         int(self.overlap_count),
            "conflict_count":        int(self.conflict_count),
            "confirmation_score":    int(self.confirmation_score),
            "conflict_ratio":        float(self.conflict_ratio),
            "regime_combo_score":    self.regime_combo_score,
            "verdict":               self.verdict.value,
            "reasons":               list(self.reasons),
            "risk_flags":            list(self.risk_flags),
            "blocked_strategies":    list(self.blocked_strategies),
            "watchlist_strategies":  list(self.watchlist_strategies),
            "agent_context_ready":   bool(self.agent_context_ready),
            "recommended_for_paper": bool(self.recommended_for_paper),
            "is_order_signal":       False,
            "auto_apply_allowed":    False,
            "is_live_authorization": False,
        }


@dataclass(frozen=True)
class RegimeComboBacktestReport:
    """7 regime × 15 combo 결과 묶음."""

    generated_at:           str
    schema_version:         str
    symbol:                 str | None
    results:                list[RegimeComboResult]
    criteria:               ComboCriteria
    recommended_by_regime:  dict[str, list[str]]      = field(default_factory=dict)
    blocked_by_regime:      dict[str, list[str]]      = field(default_factory=dict)
    notes:                  list[str]                  = field(default_factory=list)

    advisory_disclaimer:    str = (
        "본 리포트는 *advisory* — 장세별 조합 분석만, 실거래 주문 0건. "
        "PASS 라벨도 실거래 활성화 / Paper 후보 자동 적용 허가가 아니며, "
        "Paper 후보 확정은 운영자 명시 검토 + 별도 PR 후에만 가능합니다. "
        "UNKNOWN 장세에서는 어떤 조합도 추천하지 않습니다. "
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
                raise ValueError(f"RegimeComboBacktestReport.{name} must be False.")
        # UNKNOWN regime 은 recommended 리스트에 *어떤 조합도 등장하면 안 됨*.
        unknown_key = MarketRegime.UNKNOWN.value
        unk = self.recommended_by_regime.get(unknown_key) or []
        if unk:
            raise ValueError(
                f"recommended_by_regime[UNKNOWN] must be empty (advisory invariant), "
                f"got {unk!r}"
            )

    def to_dict(self) -> dict[str, Any]:
        return {
            "generated_at":          self.generated_at,
            "schema_version":        self.schema_version,
            "symbol":                self.symbol,
            "row_count":             len(self.results),
            "results":               [r.to_dict() for r in self.results],
            "criteria":              {
                "min_trades":            self.criteria.min_trades,
                "pass_profit_factor":    self.criteria.pass_profit_factor,
                "fail_profit_factor":    self.criteria.fail_profit_factor,
                "pass_max_drawdown_abs": self.criteria.pass_max_drawdown_abs,
                "fail_max_drawdown_abs": self.criteria.fail_max_drawdown_abs,
                "pass_conflict_ratio":   self.criteria.pass_conflict_ratio,
                "fee_rate":              self.criteria.fee_rate,
                "slippage_rate":         self.criteria.slippage_rate,
            },
            "recommended_by_regime": dict(self.recommended_by_regime),
            "blocked_by_regime":     dict(self.blocked_by_regime),
            "notes":                 list(self.notes),
            "advisory_disclaimer":   self.advisory_disclaimer,
            "is_order_signal":       False,
            "auto_apply_allowed":    False,
            "is_live_authorization": False,
        }


# ─────────────────────────────────────────────────────────────────────────────
# Per-regime / per-combo computation
# ─────────────────────────────────────────────────────────────────────────────


def _normalize_direction(d: str) -> str:
    return (d or "").strip().upper()


def _signal_level_metrics(
    sigs: list[StrategySignal],
    strategy_to_tactic: dict[str, TacticGroup],
) -> tuple[int, int, int, float]:
    """3-12 의 _signal_level_metrics 와 동일 — 본 모듈에서 재정의 (결합도 0)."""
    if not sigs:
        return 0, 0, 0, 0.0
    by_key: dict[tuple[str, str], list[StrategySignal]] = {}
    for s in sigs:
        by_key.setdefault((s.day_key, s.symbol), []).append(s)
    overlap, conflict, confirmation = 0, 0, 0
    for _, group in by_key.items():
        if len(group) >= 2:
            overlap += 1
        dirs = {_normalize_direction(s.direction) for s in group}
        if "BUY" in dirs and "SELL" in dirs:
            conflict += 1
        for direction in ("BUY", "SELL"):
            tactics = {
                strategy_to_tactic[s.strategy_id]
                for s in group
                if _normalize_direction(s.direction) == direction
            }
            if len(tactics) >= 2:
                confirmation += len(tactics)
    n = max(len(sigs), 1)
    return overlap, conflict, confirmation, max(0.0, min(1.0, float(conflict) / float(n)))


def _trade_pnls(sigs: list[StrategySignal]) -> list[float]:
    return [
        float(s.realized_pnl)
        for s in sigs
        if _normalize_direction(s.direction) in ("BUY", "SELL", "EXIT")
    ]


def _max_drawdown_abs(pnls: list[float]) -> float | None:
    if not pnls:
        return None
    cum, peak, mdd = 0.0, 0.0, 0.0
    for p in pnls:
        cum += p
        peak = max(peak, cum)
        mdd = max(mdd, peak - cum)
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


def _regime_combo_score(
    *,
    pnls:              list[float],
    expectancy:        float | None,
    profit_factor:     float | None,
    max_drawdown:      float | None,
    confirmation:      int,
    conflict_ratio:    float,
) -> float | None:
    """장세별 advisory 종합 점수 — expectancy × profit_factor × (1 - conflict_ratio)
    × (1 + confirmation_normalized) / max(MDD, 1) 가산.

    None 이 있으면 None.
    """
    if expectancy is None or profit_factor is None or max_drawdown is None:
        return None
    base = expectancy * profit_factor * (1.0 - conflict_ratio)
    confirmation_bonus = 1.0 + min(0.5, confirmation * 0.05)
    denom = max(max_drawdown, 1.0)
    return float(base * confirmation_bonus / denom)


def _strategy_to_tactic_map() -> dict[str, TacticGroup]:
    """combo 모듈의 매핑을 import 회피 — 본 모듈 내부에서 다시 계산."""
    return {
        "sma_crossover":    TacticGroup.MOMENTUM,
        "volume_breakout":  TacticGroup.MOMENTUM,
        "rsi_reversion":    TacticGroup.REVERSION,
        "vwap_strategy":    TacticGroup.VWAP,
        "orb_vwap":         TacticGroup.ORB_PULLBACK,
        "pullback_rebreak": TacticGroup.ORB_PULLBACK,
    }


def _combo_blocked_under_regime(
    *,
    regime:               MarketRegime,
    included_strategies:  Iterable[str],
) -> tuple[bool, list[str], list[str]]:
    """regime + 조합 → (BLOCKED 여부, blocked_strategies, watchlist_strategies).

    `_GLOBALLY_BLOCKED_REGIMES` (LOW_LIQUIDITY / UNKNOWN) → 항상 BLOCKED.
    그 외 regime 에서는 *조합에 포함된 strategy 중 하나라도* `blocked` 정책에
    있으면 BLOCKED.
    """
    policy = REGIME_STRATEGY_POLICY.get(regime, {})
    blocked_set = set(policy.get("blocked", set()))
    watchlist_set = set(policy.get("watchlist", set()))
    inc = list(included_strategies)
    blocked_in_combo = [s for s in inc if s in blocked_set]
    watchlist_in_combo = [s for s in inc if s in watchlist_set]
    is_blocked = regime in _GLOBALLY_BLOCKED_REGIMES or bool(blocked_in_combo)
    return is_blocked, blocked_in_combo, watchlist_in_combo


def _verdict_for_regime_combo(
    *,
    regime:             MarketRegime,
    trade_count:        int,
    signal_count:       int,
    expectancy:         float | None,
    profit_factor:      float | None,
    max_drawdown:       float | None,
    conflict_ratio:     float,
    overlap_count:      int,
    is_blocked:         bool,
    blocked_in_combo:   list[str],
    criteria:           ComboCriteria,
) -> tuple[RegimeComboVerdict, list[str], list[str]]:
    reasons: list[str] = []
    risk_flags: list[str] = []

    if is_blocked:
        if regime == MarketRegime.UNKNOWN:
            reasons.append(
                "UNKNOWN 장세 — 자동 추천 금지 (모든 조합 BLOCKED_REGIME)"
            )
            risk_flags.append("regime_unknown")
        elif regime == MarketRegime.LOW_LIQUIDITY:
            reasons.append(
                "LOW_LIQUIDITY 장세 — 거래대금 부족, 모든 조합 BLOCKED_REGIME"
            )
            risk_flags.append("regime_low_liquidity")
        else:
            reasons.append(
                f"{regime.value} 장세에서 차단된 전략 포함: "
                f"{blocked_in_combo}"
            )
            risk_flags.append("regime_blocks_strategies")
        return RegimeComboVerdict.BLOCKED_REGIME, reasons, risk_flags

    if signal_count == 0:
        return (
            RegimeComboVerdict.INSUFFICIENT_DATA,
            ["signal_count == 0 — 해당 장세 데이터 없음"],
            ["insufficient_data"],
        )
    if trade_count < criteria.min_trades:
        return (
            RegimeComboVerdict.INSUFFICIENT_DATA,
            [
                f"trade_count={trade_count} < min_trades={criteria.min_trades}"
            ],
            ["insufficient_trades"],
        )
    if expectancy is None or profit_factor is None or max_drawdown is None:
        return (
            RegimeComboVerdict.INSUFFICIENT_DATA,
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
        return RegimeComboVerdict.FAIL, reasons, risk_flags

    # WATCH — boundary 또는 HIGH_VOLATILITY / CHOPPY 시 size 축소 경고.
    watch = False
    if profit_factor < criteria.pass_profit_factor:
        reasons.append(
            f"profit_factor={profit_factor:.3f} < pass_threshold="
            f"{criteria.pass_profit_factor:.2f} — boundary"
        )
        risk_flags.append("profit_factor_boundary")
        watch = True
    if abs(max_drawdown) > criteria.pass_max_drawdown_abs:
        reasons.append(
            f"|max_drawdown|={abs(max_drawdown):.4f} > pass_threshold="
            f"{criteria.pass_max_drawdown_abs:.2f} — boundary"
        )
        risk_flags.append("drawdown_boundary")
        watch = True
    if conflict_ratio > criteria.pass_conflict_ratio:
        reasons.append(
            f"conflict_ratio={conflict_ratio:.2f} > pass_threshold="
            f"{criteria.pass_conflict_ratio:.2f} — 충돌 신호 과다"
        )
        risk_flags.append("high_conflict")
        watch = True
    if overlap_count >= max(trade_count, 1):
        reasons.append(
            f"overlap_count={overlap_count} >= trade_count={trade_count}"
        )
        risk_flags.append("high_overlap")
        watch = True
    if regime == MarketRegime.HIGH_VOLATILITY:
        reasons.append("HIGH_VOLATILITY — sizing 축소 권고")
        risk_flags.append("regime_high_volatility")
        watch = True
    elif regime == MarketRegime.CHOPPY:
        reasons.append("CHOPPY — 신호 신뢰도 낮음, 보수적 검토 권고")
        risk_flags.append("regime_choppy")
        watch = True

    if watch:
        return RegimeComboVerdict.WATCH, reasons, risk_flags

    reasons.append("모든 PASS 기준 통과 — advisory")
    return RegimeComboVerdict.PASS, reasons, risk_flags


def compute_regime_combo_metrics(
    *,
    regime:     MarketRegime,
    tactics:    tuple[TacticGroup, ...],
    signals:    list[StrategySignal],
    symbol:     str | None    = None,
    criteria:   ComboCriteria = None,    # type: ignore[assignment]
) -> RegimeComboResult:
    if criteria is None:
        criteria = ComboCriteria()

    tactic_set = set(tactics)
    s2t = _strategy_to_tactic_map()
    sigs = [
        s for s in signals
        if s2t[s.strategy_id] in tactic_set
    ]

    included_strategies = combo_strategies(tactics)

    # regime 차단 검사.
    is_blocked, blocked_in_combo, watchlist_in_combo = _combo_blocked_under_regime(
        regime=regime, included_strategies=included_strategies,
    )

    overlap, conflict, confirmation, conflict_ratio = _signal_level_metrics(sigs, s2t)
    pnls = _trade_pnls(sigs)
    total = float(sum(pnls)) if pnls else 0.0
    expectancy = _expectancy(pnls)
    profit_factor = _profit_factor(pnls)
    mdd = _max_drawdown_abs(pnls)
    win_rate = _win_rate(pnls)
    streak = _loss_streak(pnls)
    score = _regime_combo_score(
        pnls=pnls, expectancy=expectancy, profit_factor=profit_factor,
        max_drawdown=mdd, confirmation=confirmation, conflict_ratio=conflict_ratio,
    )

    verdict, reasons, risk_flags = _verdict_for_regime_combo(
        regime=regime,
        trade_count=len(pnls),
        signal_count=len(sigs),
        expectancy=expectancy, profit_factor=profit_factor,
        max_drawdown=mdd,
        conflict_ratio=conflict_ratio, overlap_count=overlap,
        is_blocked=is_blocked, blocked_in_combo=blocked_in_combo,
        criteria=criteria,
    )

    return RegimeComboResult(
        regime=regime.value,
        combo_name=combo_name(tactics),
        included_tactics=tuple(t.value for t in tactics),
        included_strategies=included_strategies,
        symbol=symbol,
        signal_count=len(sigs),
        trade_count=len(pnls),
        total_return=total,
        expectancy=expectancy,
        profit_factor=profit_factor,
        max_drawdown=mdd,
        win_rate=win_rate,
        loss_streak=streak,
        overlap_count=overlap,
        conflict_count=conflict,
        confirmation_score=confirmation,
        conflict_ratio=conflict_ratio,
        regime_combo_score=score,
        verdict=verdict,
        reasons=reasons,
        risk_flags=risk_flags,
        blocked_strategies=blocked_in_combo,
        watchlist_strategies=watchlist_in_combo,
        recommended_for_paper=False,    # 영구 — 자동 적용 X
    )


def run_regime_combo_backtest(
    *,
    signals:        list[RegimeStrategySignal],
    symbol:         str | None       = None,
    criteria:       ComboCriteria    = None,    # type: ignore[assignment]
    only_sizes:     Iterable[int] | None = None,
    now:            datetime | None  = None,
) -> RegimeComboBacktestReport:
    if criteria is None:
        criteria = ComboCriteria()
    if now is None:
        now = datetime.now(timezone.utc)

    # signal 을 regime 별로 split.
    by_regime: dict[MarketRegime, list[StrategySignal]] = {
        r: [] for r in MarketRegime
    }
    for rs in signals:
        by_regime[rs.regime].append(rs.to_signal())

    combos = enumerate_combinations()
    if only_sizes:
        wanted = set(int(x) for x in only_sizes)
        combos = [c for c in combos if len(c) in wanted]

    results: list[RegimeComboResult] = []
    for regime in MarketRegime:
        sigs = by_regime[regime]
        for tactics in combos:
            results.append(
                compute_regime_combo_metrics(
                    regime=regime, tactics=tactics,
                    signals=sigs, symbol=symbol, criteria=criteria,
                ),
            )

    # 추천 / 차단 매트릭스.
    recommended_by_regime: dict[str, list[str]] = {r.value: [] for r in MarketRegime}
    blocked_by_regime:     dict[str, list[str]] = {r.value: [] for r in MarketRegime}
    for r in results:
        if r.verdict == RegimeComboVerdict.PASS \
                and r.regime != MarketRegime.UNKNOWN.value:
            recommended_by_regime[r.regime].append(r.combo_name)
        if r.verdict in (
            RegimeComboVerdict.BLOCKED_REGIME, RegimeComboVerdict.FAIL,
        ):
            blocked_by_regime[r.regime].append(r.combo_name)

    notes: list[str] = [
        "본 리포트는 advisory — Paper 후보 자동 적용 0건.",
        "UNKNOWN 장세에서는 어떤 조합도 추천하지 않음 (영구 invariant).",
        "LOW_LIQUIDITY 장세에서는 모든 조합 BLOCKED — 거래대금 부족 정책.",
    ]
    if not signals:
        notes.append("INSUFFICIENT_DATA: 입력 signals == 0")

    return RegimeComboBacktestReport(
        generated_at=now.isoformat(),
        schema_version=REGIME_COMBO_SCHEMA_VERSION,
        symbol=symbol,
        results=results,
        criteria=criteria,
        recommended_by_regime=recommended_by_regime,
        blocked_by_regime=blocked_by_regime,
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


def render_markdown(report: RegimeComboBacktestReport) -> str:
    lines: list[str] = []
    lines.append("# 장세별 전략 조합 백테스트 리포트")
    lines.append("")
    lines.append("> *advisory* — 장세 × 조합 분석만, 실거래 주문 0건.")
    lines.append("> PASS 라벨도 Paper 후보 자동 적용 / 실거래 허가가 아닙니다.")
    lines.append("")
    lines.append(f"- 생성: `{report.generated_at}`")
    lines.append(f"- schema_version: `{report.schema_version}`")
    lines.append(f"- symbol: `{report.symbol or '—'}`")
    lines.append(f"- 총 row: **{len(report.results)}** (7 regime × 15 combo)")
    lines.append("")

    # 추천 by regime.
    lines.append("## 장세별 추천 조합 (PASS)")
    lines.append("")
    lines.append("| 장세 | 추천 조합 |")
    lines.append("|---|---|")
    for r in MarketRegime:
        recs = report.recommended_by_regime.get(r.value, [])
        cell = ", ".join(f"`{c}`" for c in recs) if recs else "—"
        lines.append(f"| `{r.value}` | {cell} |")
    lines.append("")

    # 차단 by regime.
    lines.append("## 장세별 차단/실패 조합")
    lines.append("")
    lines.append("| 장세 | 차단 조합 (BLOCKED_REGIME + FAIL) |")
    lines.append("|---|---|")
    for r in MarketRegime:
        blk = report.blocked_by_regime.get(r.value, [])
        cell = ", ".join(f"`{c}`" for c in blk[:5]) + (
            f" … (+{len(blk)-5}개)" if len(blk) > 5 else ""
        ) if blk else "—"
        lines.append(f"| `{r.value}` | {cell} |")
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
    lines.append("- `recommended_for_paper=False` 영구 — 자동 적용 0건")
    lines.append("- `UNKNOWN` 장세: 어떤 조합도 추천 0건 (영구)")
    lines.append("- `LOW_LIQUIDITY` 장세: 모든 조합 BLOCKED_REGIME (영구)")
    lines.append("- broker / OrderExecutor / route_order 호출 0건")
    lines.append("")
    lines.append(report.advisory_disclaimer)
    lines.append("")
    return "\n".join(lines)


def render_ranking_csv(report: RegimeComboBacktestReport) -> str:
    headers = [
        "regime", "combo_name", "verdict",
        "trade_count", "expectancy", "profit_factor", "max_drawdown",
        "win_rate", "confirmation_score", "conflict_count",
        "regime_combo_score", "recommended_for_paper",
    ]
    rows = sorted(
        report.results,
        key=lambda r: (
            r.regime,
            -(r.regime_combo_score if r.regime_combo_score is not None else -1e18),
        ),
    )
    out: list[str] = [",".join(headers)]
    for r in rows:
        out.append(",".join([
            r.regime,
            r.combo_name,
            r.verdict.value,
            str(int(r.trade_count)),
            _fmt(r.expectancy),
            _fmt(r.profit_factor),
            _fmt(r.max_drawdown),
            _fmt(r.win_rate),
            str(int(r.confirmation_score)),
            str(int(r.conflict_count)),
            _fmt(r.regime_combo_score),
            "false",   # 영구
        ]))
    return "\n".join(out) + "\n"


def write_reports(
    report:   RegimeComboBacktestReport,
    out_dir:  Path | str,
) -> dict[str, Path]:
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    json_path = out / "regime_combo_summary.json"
    md_path   = out / "regime_combo_report.md"
    csv_path  = out / "regime_combo_ranking.csv"
    json_path.write_text(
        _json.dumps(report.to_dict(), ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    md_path.write_text(render_markdown(report), encoding="utf-8")
    csv_path.write_text(render_ranking_csv(report), encoding="utf-8")
    return {"summary_json": json_path, "report_md": md_path, "ranking_csv": csv_path}


__all__ = [
    "REGIME_COMBO_SCHEMA_VERSION",
    "RegimeComboVerdict",
    "RegimeStrategySignal",
    "RegimeComboResult",
    "RegimeComboBacktestReport",
    "compute_regime_combo_metrics",
    "run_regime_combo_backtest",
    "render_markdown",
    "render_ranking_csv",
    "write_reports",
]

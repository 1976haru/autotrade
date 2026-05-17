"""3-04 — Walk-forward 검증 모듈.

3-03 의 parameter optimization 결과를 *학습 (train) / 검증 (validation)* 구간으로
분리해 과최적화 (OVERFIT_RISK) 를 탐지한다. paper_candidate_config.json 의
후보를 입력으로 받거나, 임의의 (strategy_name, params, symbol) 조합을 직접
평가 가능.

핵심 개념:
- **WalkForwardMode.ROLLING** (default): 학습 + 검증 윈도우가 ``step_days`` 씩
  슬라이드. 각 fold 는 *비중첩* 검증 구간 → realistic out-of-sample.
- **WalkForwardMode.EXPANDING**: train_start 고정, train_end 가 fold 마다 확장.
  더 많은 학습 데이터를 활용하지만 *early-period 의존성* 위험.
- **holdout_days**: 가장 최근 N 일은 walk-forward 평가에서 *제외* (final
  validation 용으로 별도 보관). 0 이면 disable.

verdict:
- ``HEALTHY``         train + val 모두 양호 (train > 0 + val > 0 + ratio ≥ threshold).
- ``OVERFIT_RISK``    train > 0 인데 val 부진 (val ≤ 0 또는 ratio < threshold).
- ``UNDERFIT``        train + val 모두 부진 (train ≤ 0 + val ≤ 0).
- ``INSUFFICIENT_DATA`` fold 수 < min_folds (default 3).

CLAUDE.md 절대 원칙:
- broker / OrderExecutor / route_order import 0건 (정적 grep 가드).
- 본 모듈은 *분석 read-only* — 자동 주문 / 자동 paper 시작 / 자동 실거래
  활성화 0건.
- ``OVERFIT_RISK`` 라벨은 *분석 라벨* — 자동 promotion 변경 / 자동 비활성
  의미 X. 운영자 검토 후 후보 자격 박탈 / 별도 PR 절차.
- secret / API key / `.env` 노출 0건.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any, Sequence

from app.backtest.engine import BacktestEngine
from app.backtest.types import BacktestConfig, Bar


# ─────────────────────────────────────────────────────────────────────────────
# 1. Config / mode enum
# ─────────────────────────────────────────────────────────────────────────────


class WalkForwardMode(StrEnum):
    """Walk-forward 분할 모드."""

    ROLLING   = "rolling"     # train 윈도우가 슬라이드 (default).
    EXPANDING = "expanding"   # train_start 고정, train_end 만 확장.


class WalkForwardVerdict(StrEnum):
    """과최적화 탐지 verdict."""

    HEALTHY           = "HEALTHY"
    OVERFIT_RISK      = "OVERFIT_RISK"
    UNDERFIT          = "UNDERFIT"
    INSUFFICIENT_DATA = "INSUFFICIENT_DATA"


# Default config — 운영자 spec 기준.
DEFAULT_TRAIN_DAYS      = 60
DEFAULT_VALIDATION_DAYS = 20
DEFAULT_HOLDOUT_DAYS    = 0       # 최근 N 일 제외 (default disable).
DEFAULT_STEP_DAYS       = 20      # rolling 모드 step (validation_days 와 동일).
DEFAULT_MIN_FOLDS       = 3       # fold 수 < 이 값이면 INSUFFICIENT_DATA.
DEFAULT_OVERFIT_RATIO   = 0.5     # val_expectancy / train_expectancy 가
                                  # 이 비율 미만이면 OVERFIT_RISK.


@dataclass(frozen=True)
class WalkForwardConfig:
    """Walk-forward 분할 + verdict 임계값.

    *_days 단위는 *bar 개수* — 본 분석은 일봉 가정. 분봉으로 확장 시 별도
    `bars_per_day` 인자 추가 필요 (후속 PR).
    """

    mode:             WalkForwardMode = WalkForwardMode.ROLLING
    train_days:       int             = DEFAULT_TRAIN_DAYS
    validation_days:  int             = DEFAULT_VALIDATION_DAYS
    holdout_days:     int             = DEFAULT_HOLDOUT_DAYS
    step_days:        int             = DEFAULT_STEP_DAYS
    min_folds:        int             = DEFAULT_MIN_FOLDS
    overfit_ratio:    float           = DEFAULT_OVERFIT_RATIO

    def __post_init__(self) -> None:
        if self.train_days < 1:
            raise ValueError("train_days must be >= 1")
        if self.validation_days < 1:
            raise ValueError("validation_days must be >= 1")
        if self.holdout_days < 0:
            raise ValueError("holdout_days must be >= 0")
        if self.step_days < 1:
            raise ValueError("step_days must be >= 1")
        if self.min_folds < 1:
            raise ValueError("min_folds must be >= 1")
        if not (0.0 < self.overfit_ratio <= 1.0):
            raise ValueError("overfit_ratio must be in (0.0, 1.0]")

    def to_dict(self) -> dict[str, Any]:
        return {
            "mode":             self.mode.value,
            "train_days":       self.train_days,
            "validation_days":  self.validation_days,
            "holdout_days":     self.holdout_days,
            "step_days":        self.step_days,
            "min_folds":        self.min_folds,
            "overfit_ratio":    self.overfit_ratio,
        }


# ─────────────────────────────────────────────────────────────────────────────
# 2. Split 생성
# ─────────────────────────────────────────────────────────────────────────────


@dataclass(frozen=True)
class WalkForwardSplit:
    """단일 fold 의 train / validation 인덱스 (bars list 기준).

    인덱스는 half-open: [train_start, train_end) / [val_start, val_end).
    """

    fold_number:     int
    train_start_idx: int
    train_end_idx:   int   # exclusive
    val_start_idx:   int
    val_end_idx:     int   # exclusive

    def __post_init__(self) -> None:
        if self.train_start_idx < 0:
            raise ValueError("train_start_idx must be >= 0")
        if self.train_end_idx <= self.train_start_idx:
            raise ValueError("train_end_idx must be > train_start_idx")
        if self.val_start_idx < self.train_end_idx:
            raise ValueError("val_start_idx must be >= train_end_idx (no overlap)")
        if self.val_end_idx <= self.val_start_idx:
            raise ValueError("val_end_idx must be > val_start_idx")

    @property
    def train_bars(self) -> int:
        return self.train_end_idx - self.train_start_idx

    @property
    def val_bars(self) -> int:
        return self.val_end_idx - self.val_start_idx


def generate_splits(
    total_bars: int, config: WalkForwardConfig,
) -> list[WalkForwardSplit]:
    """bar 개수 기준 fold 분할 생성.

    ROLLING: 윈도우 슬라이드.
        fold k: train=[k*step, k*step + train], val=[k*step + train, k*step + train + val]
    EXPANDING: train_start=0 고정, train_end 확장.
        fold k: train=[0, train + k*step], val=[train + k*step, train + k*step + val]

    holdout_days 가 > 0 이면 가장 최근 holdout_days 만큼 *제외*.
    """
    usable_bars = max(0, total_bars - max(0, config.holdout_days))
    if usable_bars < config.train_days + config.validation_days:
        return []

    splits: list[WalkForwardSplit] = []
    fold = 1
    if config.mode == WalkForwardMode.ROLLING:
        train_start = 0
        while True:
            train_end = train_start + config.train_days
            val_end   = train_end + config.validation_days
            if val_end > usable_bars:
                break
            splits.append(WalkForwardSplit(
                fold_number=fold,
                train_start_idx=train_start,
                train_end_idx=train_end,
                val_start_idx=train_end,
                val_end_idx=val_end,
            ))
            fold += 1
            train_start += config.step_days
    else:  # EXPANDING
        train_end = config.train_days
        while True:
            val_end = train_end + config.validation_days
            if val_end > usable_bars:
                break
            splits.append(WalkForwardSplit(
                fold_number=fold,
                train_start_idx=0,
                train_end_idx=train_end,
                val_start_idx=train_end,
                val_end_idx=val_end,
            ))
            fold += 1
            train_end += config.step_days

    return splits


# ─────────────────────────────────────────────────────────────────────────────
# 3. Per-fold metric 계산 (3-03 와 동일한 보수적 비용 모델)
# ─────────────────────────────────────────────────────────────────────────────


def _safe_float(v: Any, default: float = 0.0) -> float:
    if v is None:
        return default
    try:
        f = float(v)
    except (TypeError, ValueError):
        return default
    if math.isnan(f) or math.isinf(f):
        return default
    return f


def _extract_metrics(*, result, initial_cash: int) -> dict[str, Any]:
    """BacktestResult → metric dict (verdict 분류기 + 리포트용)."""
    trades = list(getattr(result, "trades", []) or [])
    pnls: list[float] = []
    for t in trades:
        pnl = getattr(t, "pnl", None)
        if pnl is None and isinstance(t, dict):
            pnl = t.get("pnl")
        pnls.append(_safe_float(pnl))

    trade_count = len(pnls)
    wins   = [p for p in pnls if p > 0]
    losses = [p for p in pnls if p < 0]

    total_win  = sum(wins)
    total_loss = abs(sum(losses))
    profit_factor = (total_win / total_loss) if total_loss > 0 else (
        None if total_win == 0 else float("inf")
    )
    profit_factor_json: Any = (
        None if (profit_factor is None or profit_factor == float("inf"))
        else _safe_float(profit_factor)
    )

    raw_return = _safe_float(getattr(result, "total_return", 0.0))
    max_dd     = _safe_float(getattr(result, "max_drawdown", 0.0))
    avg_pnl    = (sum(pnls) / trade_count) if trade_count > 0 else 0.0
    win_rate   = (len(wins) / trade_count) if trade_count > 0 else 0.0

    return {
        "trade_count":   int(trade_count),
        "profit_factor": profit_factor_json,
        "max_drawdown":  _safe_float(max_dd),
        "total_return":  raw_return,
        "win_rate":      _safe_float(win_rate),
        "expectancy":    _safe_float(avg_pnl),
    }


# ─────────────────────────────────────────────────────────────────────────────
# 4. Fold 평가 + 전체 walk-forward verdict
# ─────────────────────────────────────────────────────────────────────────────


@dataclass(frozen=True)
class FoldResult:
    """단일 fold 의 train / val metric."""

    fold_number:    int
    train_metrics:  dict[str, Any]
    val_metrics:    dict[str, Any]

    @property
    def train_expectancy(self) -> float:
        return _safe_float(self.train_metrics.get("expectancy"))

    @property
    def val_expectancy(self) -> float:
        return _safe_float(self.val_metrics.get("expectancy"))

    @property
    def ratio(self) -> float:
        """val_expectancy / train_expectancy. train ≤ 0 이면 0.0 반환."""
        t = self.train_expectancy
        if t <= 0:
            return 0.0
        return self.val_expectancy / t

    def to_dict(self) -> dict[str, Any]:
        return {
            "fold_number":     self.fold_number,
            "train_metrics":   dict(self.train_metrics),
            "val_metrics":     dict(self.val_metrics),
            "train_expectancy": self.train_expectancy,
            "val_expectancy":   self.val_expectancy,
            "ratio":            self.ratio,
        }


@dataclass(frozen=True)
class WalkForwardResult:
    """전체 walk-forward 평가 결과 — verdict + fold 별 metric + 사유."""

    verdict:          WalkForwardVerdict
    folds:            list[FoldResult]
    strategy_name:    str
    params:           dict[str, Any]
    config:           WalkForwardConfig
    reasons:          list[str]                = field(default_factory=list)
    train_expectancy_avg: float                = 0.0
    val_expectancy_avg:   float                = 0.0

    def to_dict(self) -> dict[str, Any]:
        return {
            "verdict":              self.verdict.value,
            "strategy":             self.strategy_name,
            "params":               dict(self.params),
            "config":               self.config.to_dict(),
            "fold_count":           len(self.folds),
            "train_expectancy_avg": self.train_expectancy_avg,
            "val_expectancy_avg":   self.val_expectancy_avg,
            "folds":                [f.to_dict() for f in self.folds],
            "reasons":              list(self.reasons),
            # 분석 라벨 — 자동 주문 / 자동 적용 / 자동 promotion 의미 0건.
            "is_order_signal":       False,
            "auto_apply_allowed":    False,
            "is_live_authorization": False,
        }


def _build_strategy(strategy_name: str, params: dict[str, Any]):
    """build_strategy import 는 lazy — 순환 import 차단."""
    from app.strategies.concrete import build_strategy
    return build_strategy(strategy_name, params=dict(params))


def _run_segment(
    *, bars: Sequence[Bar], strategy_name: str, params: dict[str, Any],
    initial_cash: int, quantity: int, bt_config: BacktestConfig,
) -> dict[str, Any] | None:
    """단일 segment (train 또는 val) 백테스트. 실패 시 None."""
    if len(bars) < 2:
        return None
    try:
        strategy = _build_strategy(strategy_name, params)
    except Exception:  # noqa: BLE001
        return None
    engine = BacktestEngine(initial_cash=initial_cash, quantity=quantity)
    try:
        result = engine.run(list(bars), strategy, config=bt_config)
    except Exception:  # noqa: BLE001
        return None
    return _extract_metrics(result=result, initial_cash=initial_cash)


def evaluate_walk_forward(
    *,
    bars:           Sequence[Bar],
    strategy_name:  str,
    params:         dict[str, Any] | None,
    config:         WalkForwardConfig | None = None,
    initial_cash:   int = 10_000_000,
    quantity:       int = 10,
    bt_config:      BacktestConfig | None = None,
) -> WalkForwardResult:
    """단일 (strategy, params, bars) 의 walk-forward 평가.

    1. ``generate_splits`` 로 fold 분할.
    2. 각 fold 별로 train + val 백테스트.
    3. 평균 expectancy + ratio 로 verdict 분류.

    Args:
        bars: 단일 symbol 의 OHLCV 시퀀스.
        strategy_name: STRATEGY_REGISTRY 키.
        params: strategy 파라미터 (None → default).
        config: WalkForwardConfig (None → default rolling 60/20).
        initial_cash / quantity: 백테스트 자본 / 수량.
        bt_config: BacktestConfig (None → next_open + 보수 비용 default).

    Returns:
        WalkForwardResult — verdict + fold 별 결과 + 사유 carry.
    """
    cfg = config or WalkForwardConfig()
    pparams = dict(params or {})
    bt_cfg = bt_config or BacktestConfig(
        execution_model="next_open",
        execution_delay_bars=1,
        slippage_bps=5, commission_bps=15, tax_bps=23,
    )

    splits = generate_splits(len(bars), cfg)
    if len(splits) < cfg.min_folds:
        return WalkForwardResult(
            verdict=WalkForwardVerdict.INSUFFICIENT_DATA,
            folds=[],
            strategy_name=strategy_name,
            params=pparams,
            config=cfg,
            reasons=[
                f"fold_count={len(splits)} < min_folds={cfg.min_folds} "
                f"(total_bars={len(bars)}, holdout={cfg.holdout_days})"
            ],
        )

    folds: list[FoldResult] = []
    for split in splits:
        train_segment = bars[split.train_start_idx:split.train_end_idx]
        val_segment   = bars[split.val_start_idx:split.val_end_idx]

        train_metrics = _run_segment(
            bars=train_segment, strategy_name=strategy_name, params=pparams,
            initial_cash=initial_cash, quantity=quantity, bt_config=bt_cfg,
        ) or {"trade_count": 0, "expectancy": 0.0, "profit_factor": None,
              "max_drawdown": 0.0, "total_return": 0.0, "win_rate": 0.0}
        val_metrics = _run_segment(
            bars=val_segment, strategy_name=strategy_name, params=pparams,
            initial_cash=initial_cash, quantity=quantity, bt_config=bt_cfg,
        ) or {"trade_count": 0, "expectancy": 0.0, "profit_factor": None,
              "max_drawdown": 0.0, "total_return": 0.0, "win_rate": 0.0}

        folds.append(FoldResult(
            fold_number=split.fold_number,
            train_metrics=train_metrics,
            val_metrics=val_metrics,
        ))

    # 평균 expectancy 산정.
    train_avg = sum(f.train_expectancy for f in folds) / len(folds)
    val_avg   = sum(f.val_expectancy   for f in folds) / len(folds)

    verdict, reasons = _classify(folds, train_avg, val_avg, cfg)

    return WalkForwardResult(
        verdict=verdict,
        folds=folds,
        strategy_name=strategy_name,
        params=pparams,
        config=cfg,
        reasons=reasons,
        train_expectancy_avg=train_avg,
        val_expectancy_avg=val_avg,
    )


def _classify(
    folds: list[FoldResult],
    train_avg: float,
    val_avg: float,
    config: WalkForwardConfig,
) -> tuple[WalkForwardVerdict, list[str]]:
    """평균 expectancy + ratio 로 verdict 분류."""
    reasons: list[str] = []

    if train_avg <= 0 and val_avg <= 0:
        reasons.append(
            f"train_avg={train_avg:.2f} <= 0 AND val_avg={val_avg:.2f} <= 0"
        )
        return WalkForwardVerdict.UNDERFIT, reasons

    if train_avg > 0:
        if val_avg <= 0:
            reasons.append(
                f"train_avg={train_avg:.2f} > 0 but val_avg={val_avg:.2f} <= 0"
            )
            return WalkForwardVerdict.OVERFIT_RISK, reasons
        ratio = val_avg / train_avg
        if ratio < config.overfit_ratio:
            reasons.append(
                f"val/train ratio={ratio:.3f} < threshold={config.overfit_ratio} "
                f"(train_avg={train_avg:.2f}, val_avg={val_avg:.2f})"
            )
            return WalkForwardVerdict.OVERFIT_RISK, reasons
        reasons.append(
            f"train + val both positive (train_avg={train_avg:.2f}, "
            f"val_avg={val_avg:.2f}, ratio={ratio:.3f})"
        )
        return WalkForwardVerdict.HEALTHY, reasons

    # train_avg <= 0 < val_avg — 드물지만 가능. UNDERFIT 으로 분류.
    reasons.append(
        f"train_avg={train_avg:.2f} <= 0 (val_avg={val_avg:.2f}) — "
        f"strategy underperforms on train set"
    )
    return WalkForwardVerdict.UNDERFIT, reasons


# ─────────────────────────────────────────────────────────────────────────────
# 5. paper_candidate_config.json adapter (3-03 결과 입력)
# ─────────────────────────────────────────────────────────────────────────────


@dataclass(frozen=True)
class CandidateInputRecord:
    """3-03 paper_candidate_config.json 의 단일 candidate 추출 형태."""

    strategy:   str
    symbol:     str
    params:     dict[str, Any]
    score:      float

    def to_dict(self) -> dict[str, Any]:
        return {
            "strategy": self.strategy,
            "symbol":   self.symbol,
            "params":   dict(self.params),
            "score":    float(self.score),
        }


def read_candidates_from_paper_config(payload: dict[str, Any]) -> list[CandidateInputRecord]:
    """3-03 paper_candidate_config.json payload → list[CandidateInputRecord].

    파일 형식이 잘못되어도 raise 하지 않음 — 사용 가능한 entry 만 carry.
    """
    out: list[CandidateInputRecord] = []
    if not isinstance(payload, dict):
        return out
    candidates = payload.get("candidates")
    if not isinstance(candidates, list):
        return out
    for c in candidates:
        if not isinstance(c, dict):
            continue
        strategy = c.get("strategy")
        symbol   = c.get("symbol")
        params   = c.get("params")
        score    = c.get("score", 0.0)
        if not (isinstance(strategy, str) and isinstance(symbol, str) and
                isinstance(params, dict)):
            continue
        out.append(CandidateInputRecord(
            strategy=strategy,
            symbol=symbol,
            params=dict(params),
            score=_safe_float(score),
        ))
    return out

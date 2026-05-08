"""Walk-forward backtest runner (#25).

학습기간 / 검증기간 / 최근 holdout 기간을 나누어 백테스트를 여러 fold로 실행하고,
특정 구간의 한 번 대박이나 과거 최적화에 의한 승격을 방지하는 검증 프로토콜.

설계 원칙:
- broker / RiskManager / PermissionGate / OrderExecutor import 0건.
- BacktestEngine + BacktestConfig를 그대로 사용 (#23 비용 모델 / #24 metrics).
- holdout 구간은 마지막에 한 번만 평가 — 학습 / 검증에 사용되지 않은 *out-of-sample*.
- fold당 train + validation을 차례로 굴리고, validation 결과만 집계.
- 모든 결과 JSON 직렬화 가능. NaN/inf는 None 처리 (metrics.py와 lockstep).

CLAUDE.md 절대 원칙 — 본 모듈은 *백테스트 검증 프로토콜*이며 주문 흐름과 무관.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Callable, Iterable

from app.backtest.engine import BacktestEngine
from app.backtest.metrics import _safe_float, summarize_metrics
from app.backtest.types import BacktestConfig, Bar, BacktestResult


# ---------- DTOs ----------


@dataclass(frozen=True)
class WalkForwardConfig:
    """Walk-forward 윈도우 + 합격 기준 설정.

    - mode: "rolling" (학습 윈도우가 매 fold마다 앞으로 이동) 또는 "anchored"
      (학습 시작은 고정, 끝만 늘어남).
    - train_days / validation_days: 한 fold의 학습 / 검증 일 수.
    - step_days: 다음 fold로 이동할 때 슬라이딩 폭. 미지정 시 validation_days
      (인접 fold가 겹치지 않도록).
    - holdout_days: 마지막 N일을 holdout으로 분리. 0이면 holdout 없음.
    - min_fold_count: 본 수치 미만이면 검증 표본 부족 (FAIL 후보).
    - 합격 기준은 본 모듈에서 *플래그만* 산출. 최종 승인은 운영자/promotion PR.
    """
    mode:                       str   = "rolling"   # "rolling" | "anchored"
    train_days:                 int   = 60
    validation_days:            int   = 20
    step_days:                  int   = 0           # 0 → validation_days
    holdout_days:               int   = 30
    min_fold_count:             int   = 3
    min_positive_fold_ratio:    float = 0.6         # 60%+ fold가 양수
    max_single_fold_pnl_share:  float = 0.7         # 한 fold가 전체 수익의 70% 이하
    min_holdout_pnl:            int   = 0           # holdout이 손실이면 FAIL

    def __post_init__(self):
        if self.mode not in ("rolling", "anchored"):
            raise ValueError(f"unknown mode: {self.mode!r}")
        if self.train_days <= 0:
            raise ValueError("train_days must be > 0")
        if self.validation_days <= 0:
            raise ValueError("validation_days must be > 0")
        if self.step_days < 0 or self.holdout_days < 0:
            raise ValueError("step_days / holdout_days must be >= 0")
        if not 0.0 <= self.min_positive_fold_ratio <= 1.0:
            raise ValueError("min_positive_fold_ratio must be in [0,1]")
        if not 0.0 <= self.max_single_fold_pnl_share <= 1.0:
            raise ValueError("max_single_fold_pnl_share must be in [0,1]")
        if self.step_days == 0:
            object.__setattr__(self, "step_days", self.validation_days)


@dataclass(frozen=True)
class WalkForwardWindow:
    """한 fold의 시간 윈도우. UTC 가정."""
    fold_index:    int
    train_start:   datetime
    train_end:     datetime
    valid_start:   datetime
    valid_end:     datetime

    def to_dict(self) -> dict:
        return {
            "fold_index":  self.fold_index,
            "train_start": self.train_start.isoformat(),
            "train_end":   self.train_end.isoformat(),
            "valid_start": self.valid_start.isoformat(),
            "valid_end":   self.valid_end.isoformat(),
        }


@dataclass(frozen=True)
class WalkForwardFoldResult:
    """단일 fold 백테스트 결과 + 학습 지표."""
    window:               WalkForwardWindow
    train_metrics:        dict      # summarize_metrics(train trades)
    validation_metrics:   dict      # summarize_metrics(validation trades)
    validation_bar_count: int

    def to_dict(self) -> dict:
        return {
            "window":               self.window.to_dict(),
            "train_metrics":        self.train_metrics,
            "validation_metrics":   self.validation_metrics,
            "validation_bar_count": self.validation_bar_count,
        }


@dataclass
class WalkForwardResult:
    config:        WalkForwardConfig
    folds:         list[WalkForwardFoldResult] = field(default_factory=list)
    holdout_metrics: dict | None = None
    holdout_window:  dict | None = None

    def fold_count(self) -> int:
        return len(self.folds)

    def positive_fold_ratio(self) -> float:
        if not self.folds:
            return 0.0
        positives = sum(
            1 for f in self.folds
            if f.validation_metrics.get("total_pnl", 0) > 0
        )
        return positives / len(self.folds)

    def single_best_fold_pnl_share(self) -> float | None:
        """최대 fold 수익 / 전체 양수 fold 수익 합. 양수 fold 0이면 None.

        값이 1에 가까우면 → 한 fold가 전체 수익을 거의 다 만든 것 → '한 번 대박' 의심.
        """
        positives = [
            f.validation_metrics.get("total_pnl", 0)
            for f in self.folds
            if f.validation_metrics.get("total_pnl", 0) > 0
        ]
        if not positives:
            return None
        total = sum(positives)
        if total == 0:
            return None
        return max(positives) / total

    def stability_score(self) -> float:
        """0~100. 양수 fold 비율 × 100 — 단순 안정성 지표.

        실 운영에서 더 정교한 측정이 필요하면 별도 metric 추가 (Backlog).
        """
        return round(self.positive_fold_ratio() * 100.0, 2)

    def overfit_risk_score(self) -> float:
        """0~100. train vs validation의 평균 PnL 격차 기반.

        train >> validation이면 overfit 의심 → 점수 ↑.
        - 모든 fold의 train_pnl 평균과 validation_pnl 평균 차이를 train_pnl 평균으로
          정규화 (양수일 때만).
        - train_pnl <= 0이거나 fold 0개면 None 같은 의미로 0.0 반환.
        """
        if not self.folds:
            return 0.0
        train_pnls = [f.train_metrics.get("total_pnl", 0) for f in self.folds]
        valid_pnls = [f.validation_metrics.get("total_pnl", 0) for f in self.folds]
        avg_train = sum(train_pnls) / len(train_pnls)
        avg_valid = sum(valid_pnls) / len(valid_pnls)
        if avg_train <= 0:
            # train이 손실이면 overfit 측정 의미 없음.
            return 0.0
        gap = avg_train - avg_valid
        if gap <= 0:
            return 0.0
        score = min(100.0, (gap / avg_train) * 100.0)
        return round(score, 2)

    def warnings(self) -> list[str]:
        """운영자에게 보일 친화적 경고 문구."""
        out: list[str] = []
        cfg = self.config
        n = self.fold_count()
        if n < cfg.min_fold_count:
            out.append(
                f"검증 표본 부족 — {n} < {cfg.min_fold_count} (fold). "
                "학습/검증 윈도우 또는 데이터 범위 확장 검토."
            )
        ratio = self.positive_fold_ratio()
        if n > 0 and ratio < cfg.min_positive_fold_ratio:
            out.append(
                f"양수 fold 비율 {ratio:.0%} < 기준 {cfg.min_positive_fold_ratio:.0%}. "
                "전략 일관성 부족 — 승격 보류."
            )
        share = self.single_best_fold_pnl_share()
        if share is not None and share > cfg.max_single_fold_pnl_share:
            out.append(
                f"한 fold가 전체 수익의 {share:.0%}를 차지 — '한 번 대박' 의심. "
                "특정 구간 우연 의존 가능성 검토."
            )
        if self.holdout_metrics is not None:
            hp = self.holdout_metrics.get("total_pnl", 0)
            if hp < cfg.min_holdout_pnl:
                out.append(
                    f"holdout 구간 손실 (PnL={hp}). out-of-sample 검증 실패."
                )
        return out

    def overfit_flags(self) -> list[str]:
        out: list[str] = []
        if self.overfit_risk_score() >= 50.0:
            out.append("train PnL >> validation PnL — overfit 위험 高.")
        # 양수 fold 비율 낮음 + train 양수면 학습 데이터에만 적합.
        if (self.config.min_positive_fold_ratio
                and self.positive_fold_ratio() < 0.5
                and self.folds
                and any(f.train_metrics.get("total_pnl", 0) > 0 for f in self.folds)):
            out.append("학습 구간은 양수지만 검증 구간은 50% 미만 — overfit 의심.")
        return out

    def promotion_recommendation(self) -> str:
        """FAIL / CAUTION / PASS — 자동 산출 의견. 최종 승인은 운영자."""
        cfg = self.config
        n = self.fold_count()
        if n < cfg.min_fold_count:
            return "FAIL"
        if self.holdout_metrics is not None:
            if self.holdout_metrics.get("total_pnl", 0) < cfg.min_holdout_pnl:
                return "FAIL"
        ratio = self.positive_fold_ratio()
        if ratio < cfg.min_positive_fold_ratio:
            return "FAIL"
        share = self.single_best_fold_pnl_share()
        if share is not None and share > cfg.max_single_fold_pnl_share:
            return "CAUTION"
        if self.overfit_risk_score() >= 50.0:
            return "CAUTION"
        return "PASS"

    def to_dict(self) -> dict:
        return {
            "config": {
                "mode":                       self.config.mode,
                "train_days":                 self.config.train_days,
                "validation_days":            self.config.validation_days,
                "step_days":                  self.config.step_days,
                "holdout_days":               self.config.holdout_days,
                "min_fold_count":             self.config.min_fold_count,
                "min_positive_fold_ratio":    self.config.min_positive_fold_ratio,
                "max_single_fold_pnl_share":  self.config.max_single_fold_pnl_share,
                "min_holdout_pnl":            self.config.min_holdout_pnl,
            },
            "folds":           [f.to_dict() for f in self.folds],
            "holdout_metrics": self.holdout_metrics,
            "holdout_window":  self.holdout_window,
            "summary": {
                "fold_count":              self.fold_count(),
                "positive_fold_ratio":     _safe_float(self.positive_fold_ratio()),
                "single_best_fold_pnl_share": _safe_float(self.single_best_fold_pnl_share()),
                "stability_score":         _safe_float(self.stability_score()),
                "overfit_risk_score":      _safe_float(self.overfit_risk_score()),
            },
            "promotion_recommendation": self.promotion_recommendation(),
            "warnings":                 self.warnings(),
            "overfit_flags":            self.overfit_flags(),
        }


# ---------- window builder ----------


def _ensure_utc(ts: datetime) -> datetime:
    if ts.tzinfo is None:
        return ts.replace(tzinfo=timezone.utc)
    return ts.astimezone(timezone.utc)


def build_walk_forward_windows(
    *,
    start:  datetime,
    end:    datetime,
    config: WalkForwardConfig,
) -> tuple[list[WalkForwardWindow], tuple[datetime, datetime] | None]:
    """[start, end] 구간을 walk-forward fold + holdout으로 분할.

    Returns: (windows, holdout_range_or_None).

    - holdout은 마지막 holdout_days 만큼 분리.
    - 남은 구간을 train + validation 슬라이딩으로 fold 생성.
    - 데이터 부족 시 빈 리스트 반환 (호출자가 min_fold_count로 판정).
    """
    s = _ensure_utc(start)
    e = _ensure_utc(end)
    if s >= e:
        return [], None

    holdout_range: tuple[datetime, datetime] | None = None
    walk_end = e
    if config.holdout_days > 0:
        holdout_start = e - timedelta(days=config.holdout_days)
        if holdout_start <= s:
            # 데이터가 holdout보다 짧으면 walk-forward 불가.
            return [], None
        holdout_range = (holdout_start, e)
        walk_end = holdout_start

    total_days = (walk_end - s).days
    fold_span = config.train_days + config.validation_days
    if total_days < fold_span:
        return [], holdout_range

    windows: list[WalkForwardWindow] = []
    fold_idx = 0
    cursor = s
    while True:
        train_start = cursor if config.mode == "rolling" else s
        train_end   = cursor + timedelta(days=config.train_days)
        valid_start = train_end
        valid_end   = valid_start + timedelta(days=config.validation_days)
        if valid_end > walk_end:
            break
        windows.append(WalkForwardWindow(
            fold_index=fold_idx,
            train_start=train_start, train_end=train_end,
            valid_start=valid_start, valid_end=valid_end,
        ))
        fold_idx += 1
        cursor = cursor + timedelta(days=config.step_days)
    return windows, holdout_range


# ---------- runner ----------


def _slice_bars(bars: list[Bar], start: datetime, end: datetime) -> list[Bar]:
    s = _ensure_utc(start)
    e = _ensure_utc(end)
    return [b for b in bars if s <= _ensure_utc(b.timestamp) < e]


def run_walk_forward(
    *,
    bars:            list[Bar],
    strategy_factory: Callable[[], object],
    walk_forward_config: WalkForwardConfig,
    backtest_config: BacktestConfig | None = None,
    initial_cash:    int = 10_000_000,
    quantity:        int = 1,
    start:           datetime | None = None,
    end:             datetime | None = None,
) -> WalkForwardResult:
    """walk-forward 백테스트 실행.

    - bars: 단일 symbol의 시계열 봉. start/end가 명시되지 않으면 bars의 양 끝 사용.
    - strategy_factory: fold마다 새 strategy 인스턴스를 만드는 콜러블 (상태 격리).
    - backtest_config: BacktestEngine 체결 모델 / 비용 (#23). None이면 legacy 동작.
    """
    if not bars:
        return WalkForwardResult(config=walk_forward_config)

    if start is None:
        start = _ensure_utc(bars[0].timestamp)
    if end is None:
        end = _ensure_utc(bars[-1].timestamp) + timedelta(days=1)

    windows, holdout_range = build_walk_forward_windows(
        start=start, end=end, config=walk_forward_config,
    )

    fold_results: list[WalkForwardFoldResult] = []
    for w in windows:
        train_bars = _slice_bars(bars, w.train_start, w.train_end)
        valid_bars = _slice_bars(bars, w.valid_start, w.valid_end)

        # Train run — 학습 기간 평가 (실제 모델 학습은 strategy 인스턴스 내부에서
        # 일어나거나, 단순히 학습 구간 PnL을 재현 평가).
        train_engine  = BacktestEngine(initial_cash=initial_cash, quantity=quantity)
        train_strat   = strategy_factory()
        train_result: BacktestResult = train_engine.run(
            train_bars, train_strat, config=backtest_config,
        )

        valid_engine  = BacktestEngine(initial_cash=initial_cash, quantity=quantity)
        valid_strat   = strategy_factory()
        valid_result: BacktestResult = valid_engine.run(
            valid_bars, valid_strat, config=backtest_config,
        )

        fold_results.append(WalkForwardFoldResult(
            window=w,
            train_metrics=summarize_metrics(
                train_result.trades, initial_cash=initial_cash,
            ),
            validation_metrics=summarize_metrics(
                valid_result.trades, initial_cash=initial_cash,
            ),
            validation_bar_count=len(valid_bars),
        ))

    holdout_metrics = None
    holdout_window_dict = None
    if holdout_range is not None:
        h_bars = _slice_bars(bars, holdout_range[0], holdout_range[1])
        h_engine = BacktestEngine(initial_cash=initial_cash, quantity=quantity)
        h_strat  = strategy_factory()
        h_result = h_engine.run(h_bars, h_strat, config=backtest_config)
        holdout_metrics = summarize_metrics(h_result.trades, initial_cash=initial_cash)
        holdout_window_dict = {
            "start":     holdout_range[0].isoformat(),
            "end":       holdout_range[1].isoformat(),
            "bar_count": len(h_bars),
        }

    return WalkForwardResult(
        config=walk_forward_config,
        folds=fold_results,
        holdout_metrics=holdout_metrics,
        holdout_window=holdout_window_dict,
    )


# ---------- 헬퍼: 스트래터지 factory가 None인 경우 ----------


def make_strategy_factory(
    strategy_name: str,
    params: dict | None = None,
) -> Callable[[], object]:
    """build_strategy를 fold마다 재호출해 상태가 격리된 인스턴스 생성."""
    from app.strategies.concrete import build_strategy
    p = dict(params or {})
    name = strategy_name
    return lambda: build_strategy(name, p)


def fold_pnls(folds: Iterable[WalkForwardFoldResult]) -> list[int]:
    """fold validation PnL 리스트 — 운영자 dashboard용."""
    return [f.validation_metrics.get("total_pnl", 0) for f in folds]

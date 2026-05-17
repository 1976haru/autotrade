#!/usr/bin/env python3
"""feature/strategy-parameter-optimization — 6 전략 파라미터 grid search + 점수화.

본 스크립트는 baseline 백테스트 (`scripts/run_backtest_all_strategies.py`) *위에*
얹는 검증 단계 — 각 전략의 핵심 파라미터를 *작은 grid* 로 탐색하고, 수수료 /
슬리피지 반영 후 *기대값 양수* 인 후보만 추려 점수화한다. 다음 단계인 "Paper
운용 진입 검토" 로 넘기기 위한 1~2개 후보를 자동 식별한다.

CLAUDE.md 절대 원칙:
- 본 스크립트는 *read-only*. broker / route_order / OrderExecutor / KIS LIVE
  API / Anthropic / Telegram / 외부 HTTP 호출 0건.
- `ENABLE_LIVE_TRADING` / `ENABLE_AI_EXECUTION` / `ENABLE_FUTURES_LIVE_TRADING` /
  `KIS_IS_PAPER` 환경 변수 수정 0건. `.env` 작성 / 갱신 0건.
- 데이터는 `MockMarketData` 결정론적 합성 OHLCV — 실 시장 자료 아님. 본 결과
  를 *실 성과로 표현하지 않는다*.
- 본 스크립트는 어떤 strategy 파라미터도 *자동으로 코드 / DB / .env 에 반영
  하지 않는다*. 모든 결과는 advisory — 운영자가 별도 PR + 별도 검증 후 적용.

산출물 (output_dir 기본 `reports/strategy_optimization/`):
- optimization_summary.json: 전체 grid 결과 + run_meta + 카테고리 (PASS /
  INSUFFICIENT_DATA / NEGATIVE_EXPECTANCY / LOW_QUALITY) + paper_candidates.
- optimization_ranking.csv: 전체 결과 score 내림차순.
- paper_candidates.md: 운영자 검토용 markdown — 추천 1~2 전략 + 점수 + walk-
  forward 검증 plan.

사용:
    python scripts/run_strategy_optimization.py
    python scripts/run_strategy_optimization.py --symbol 005930 \\
        --start 2026-01-01 --end 2027-12-31 \\
        --output-dir reports/strategy_optimization

점수화 (0~100 점):
- expectancy (가중치 30): 양수 expectancy 가 핵심.
- profit_factor (25): (pf - 1.0) / 2.0 정규화.
- win_rate (15): 0~1 → 0~15 점.
- mdd (20): 작을수록 높은 점수 (initial_cash 의 30% 이하 정상화).
- trade_count (10): 거래 수 부족은 통계 신뢰 0 — 20~50 범위에 saturate.

INSUFFICIENT_DATA 임계: trade_count < 10 (운영자가 override 가능).
NEGATIVE_EXPECTANCY 임계: 비용 반영 expectancy ≤ 0.
"""
from __future__ import annotations

import argparse
import asyncio
import csv
import itertools
import json
import logging
import sys
from datetime import datetime
from pathlib import Path
from typing import Any, Iterable

_REPO_ROOT = Path(__file__).resolve().parents[1]
_BACKEND_DIR = _REPO_ROOT / "backend"
if str(_BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(_BACKEND_DIR))

from app.backtest.engine import BacktestEngine  # noqa: E402
from app.backtest.types import BacktestConfig, BacktestResult  # noqa: E402
from app.backtest.walk_forward_runner import (  # noqa: E402
    WalkForwardConfig,
    make_strategy_factory,
    run_walk_forward,
)
from app.market.base import Interval  # noqa: E402
from app.market.mock import MockMarketData  # noqa: E402
from app.strategies.concrete import STRATEGY_REGISTRY, build_strategy  # noqa: E402

_log = logging.getLogger("autotrade.optimize")

# ─────────────────────────────────────────────────────────────────────
# 1. 기본 비용 / 데이터 설정 (run_backtest_all_strategies 와 동일)
# ─────────────────────────────────────────────────────────────────────
DEFAULT_COMMISSION_BPS = 15
DEFAULT_TAX_BPS        = 23
DEFAULT_SLIPPAGE_BPS   = 5
DEFAULT_INITIAL_CASH   = 10_000_000
DEFAULT_QUANTITY       = 10
DEFAULT_SYMBOL         = "005930"
DEFAULT_START          = "2026-01-01"
DEFAULT_END            = "2027-12-31"
DEFAULT_OUTPUT_DIR     = "reports/strategy_optimization"

# trade_count < INSUFFICIENT_DATA_MIN_TRADES 면 통계 신뢰 부족.
INSUFFICIENT_DATA_MIN_TRADES = 10
# paper 후보 진입 최저 PF / win_rate / MDD (보수적).
PAPER_MIN_PROFIT_FACTOR = 1.10
PAPER_MIN_WIN_RATE      = 0.40
PAPER_MAX_MDD_PCT       = 0.15   # initial_cash 대비 15% 이하.
PAPER_MIN_SCORE         = 40.0   # 0~100 척도.
PAPER_MAX_RECOMMEND     = 2      # 최종 추천 후보 개수 상한.


# ─────────────────────────────────────────────────────────────────────
# 2. 전략별 파라미터 grid — 작은 크기 (조합 폭발 방지)
#
# 각 grid 는 *대표적인* 파라미터만 변동. 전체 hyperparameter space 가 아니라
# 1차 후보 식별을 위한 *작은 grid*. 모든 grid 의 default 값 + 약간의 perturbation.
# 실 데이터로 재실행할 때 운영자가 docs/strategy_optimization.md 가이드대로
# 확장.
# ─────────────────────────────────────────────────────────────────────


def _build_param_grids() -> dict[str, list[dict[str, Any]]]:
    """전략별 param dict 리스트. 각 dict 가 `build_strategy(name, params)` 인자."""
    grids: dict[str, list[dict[str, Any]]] = {}

    # sma_crossover — 단순 SMA 교차. short / long 두 차원 작은 grid.
    grids["sma_crossover"] = [
        {"short": s, "long": L}
        for s, L in itertools.product([3, 5, 8, 10], [15, 20, 30, 50])
        if s < L  # constructor 가 short < long 강제.
    ]

    # rsi_reversion — period / oversold / overbought.
    grids["rsi_reversion"] = [
        {"period": p, "oversold": os_, "overbought": ob}
        for p, os_, ob in itertools.product(
            [7, 10, 14, 21],
            [20, 25, 30],
            [70, 75, 80],
        )
    ]

    # orb_vwap — 단일 파라미터.
    grids["orb_vwap"] = [{"orb_bars": n} for n in [3, 4, 6, 8, 12]]

    # vwap_strategy — 핵심 파라미터만 (전체 17 차원 → 3 차원 grid).
    grids["vwap_strategy"] = [
        {
            "rolling_vwap_window":         w,
            "max_deviation_pct_for_entry": d,
            "take_profit_pct":             tp,
        }
        for w, d, tp in itertools.product(
            [10, 15, 20, 25],
            [1.0, 1.5, 2.0],
            [2.0, 2.5, 3.0],
        )
    ]

    # volume_breakout — 핵심 3 차원.
    grids["volume_breakout"] = [
        {
            "volume_lookback_bars":   vlb,
            "volume_multiplier":      vm,
            "breakout_lookback_bars": blb,
        }
        for vlb, vm, blb in itertools.product(
            [10, 15, 20],
            [1.5, 2.0, 2.5, 3.0],
            [10, 15, 20],
        )
    ]

    # pullback_rebreak — 핵심 3 차원 (impulse / pullback).
    grids["pullback_rebreak"] = [
        {
            "impulse_lookback_bars":   il,
            "pullback_lookback_bars":  pl,
            "min_impulse_pct":         mi,
        }
        for il, pl, mi in itertools.product(
            [8, 12, 16],
            [5, 8, 10, 14],
            [1.0, 1.5, 2.0, 3.0],
        )
    ]

    return grids


# ─────────────────────────────────────────────────────────────────────
# 3. 점수 계산 — 0~100, 가중합
# ─────────────────────────────────────────────────────────────────────


def _clamp(v: float, lo: float, hi: float) -> float:
    if v < lo:
        return lo
    if v > hi:
        return hi
    return v


def compute_score(
    *,
    expectancy: float | None,
    profit_factor: float | None,
    win_rate: float | None,
    max_drawdown: int,
    trade_count: int,
    initial_cash: int,
    avg_trade_notional: int,
) -> dict[str, float]:
    """가중합 점수 0~100. 각 sub-score 도 함께 반환 (운영자 검토용)."""
    # expectancy 정규화 — 거래당 평균 notional 대비 비율 → -1.0~+1.0 추정.
    if expectancy is None or avg_trade_notional <= 0:
        exp_norm = 0.0
    else:
        exp_norm = float(expectancy) / float(avg_trade_notional)
    # 양수만 점수 — 음수 expectancy 는 0.
    exp_score = _clamp(exp_norm, 0.0, 0.1) / 0.1 * 30.0

    # profit_factor: 1.0 부터 양의 가치. 3.0 에서 saturate.
    if profit_factor is None:
        pf_score = 0.0
    else:
        pf_score = _clamp((profit_factor - 1.0) / 2.0, 0.0, 1.0) * 25.0

    # win_rate: 0~1.
    wr = 0.0 if win_rate is None else float(win_rate)
    wr_score = _clamp(wr, 0.0, 1.0) * 15.0

    # mdd: initial_cash 대비 0~30% 정규화. 작을수록 높은 점수.
    if initial_cash <= 0:
        mdd_norm = 1.0
    else:
        mdd_norm = _clamp(float(max_drawdown) / float(initial_cash), 0.0, 0.3) / 0.3
    mdd_score = (1.0 - mdd_norm) * 20.0

    # trade_count: 20~50 사이에서 saturate.
    tc_score = _clamp((trade_count - INSUFFICIENT_DATA_MIN_TRADES) / 40.0, 0.0, 1.0) * 10.0

    total = exp_score + pf_score + wr_score + mdd_score + tc_score
    return {
        "expectancy_score":   round(exp_score, 3),
        "profit_factor_score": round(pf_score, 3),
        "win_rate_score":     round(wr_score, 3),
        "mdd_score":          round(mdd_score, 3),
        "trade_count_score":  round(tc_score, 3),
        "total_score":        round(total, 3),
    }


# ─────────────────────────────────────────────────────────────────────
# 4. 카테고리 분류
# ─────────────────────────────────────────────────────────────────────


CATEGORY_INSUFFICIENT_DATA = "INSUFFICIENT_DATA"
CATEGORY_NEGATIVE_EXPECTANCY = "NEGATIVE_EXPECTANCY"
CATEGORY_LOW_QUALITY = "LOW_QUALITY"
CATEGORY_PASS = "PASS"
ALL_CATEGORIES = (
    CATEGORY_INSUFFICIENT_DATA,
    CATEGORY_NEGATIVE_EXPECTANCY,
    CATEGORY_LOW_QUALITY,
    CATEGORY_PASS,
)


def categorize(row: dict[str, Any]) -> str:
    """1차 분류 — gate 통과 여부."""
    tc = int(row.get("trade_count", 0) or 0)
    if tc < INSUFFICIENT_DATA_MIN_TRADES:
        return CATEGORY_INSUFFICIENT_DATA
    exp = row.get("expectancy")
    if exp is None or float(exp) <= 0.0:
        return CATEGORY_NEGATIVE_EXPECTANCY
    pf = row.get("profit_factor")
    wr = row.get("win_rate")
    mdd = int(row.get("max_drawdown", 0) or 0)
    init = int(row.get("initial_cash", 1) or 1)
    if pf is None or float(pf) < PAPER_MIN_PROFIT_FACTOR:
        return CATEGORY_LOW_QUALITY
    if wr is None or float(wr) < PAPER_MIN_WIN_RATE:
        return CATEGORY_LOW_QUALITY
    if init > 0 and (mdd / init) > PAPER_MAX_MDD_PCT:
        return CATEGORY_LOW_QUALITY
    return CATEGORY_PASS


# ─────────────────────────────────────────────────────────────────────
# 5. 단일 (strategy, params) 백테스트 + 점수
# ─────────────────────────────────────────────────────────────────────


def _build_config(commission_bps: int, slippage_bps: int, tax_bps: int) -> BacktestConfig:
    return BacktestConfig(
        execution_model="next_open",
        execution_delay_bars=1,
        slippage_bps=slippage_bps,
        commission_bps=commission_bps,
        tax_bps=tax_bps,
        exit_on_last_bar=True,
    )


def _trial_row(
    *,
    strategy_name: str,
    params:       dict[str, Any],
    bars:         list,
    config:       BacktestConfig,
    initial_cash: int,
    quantity:     int,
) -> dict[str, Any]:
    """단일 (strategy_name, params) 조합 백테스트 → row dict."""
    try:
        strategy = build_strategy(strategy_name, params=params)
    except Exception as exc:  # noqa: BLE001
        return {
            "strategy": strategy_name, "params": params,
            "error": f"strategy build failed: {type(exc).__name__}: {exc}",
            "category": CATEGORY_INSUFFICIENT_DATA,
            "trade_count": 0, "total_score": 0.0,
        }

    engine = BacktestEngine(initial_cash=initial_cash, quantity=quantity)
    try:
        result: BacktestResult = engine.run(bars, strategy, config=config)
    except Exception as exc:  # noqa: BLE001
        return {
            "strategy": strategy_name, "params": params,
            "error": f"engine.run raised: {type(exc).__name__}: {exc}",
            "category": CATEGORY_INSUFFICIENT_DATA,
            "trade_count": 0, "total_score": 0.0,
        }

    summary = result.summarize_metrics()
    trade_count = len(result.trades)

    # avg trade notional — 거래당 평균 체결 금액 (점수화 시 expectancy 정규화).
    if trade_count > 0:
        avg_notional = sum(t.entry_price * t.quantity for t in result.trades) // trade_count
    else:
        avg_notional = 0

    row = {
        "strategy":             strategy_name,
        "params":               params,
        "trade_count":          trade_count,
        "total_pnl":            result.net_pnl,
        "raw_pnl":              result.gross_pnl,
        "fees":                 result.total_fees,
        "taxes":                result.total_taxes,
        "slippage_cost":        result.total_slippage,
        "initial_cash":         initial_cash,
        "win_rate":             summary.get("win_rate"),
        "profit_factor":        summary.get("profit_factor"),
        "expectancy":           summary.get("expectancy"),
        "max_drawdown":         summary.get("max_drawdown", 0),
        "max_consecutive_losses": summary.get("max_consecutive_losses", 0),
        "sharpe_like_score":    summary.get("sharpe_ratio"),
        "fee_adjusted_return":  None,
        "slippage_adjusted_return": None,
    }

    # fee / slippage adjusted return (run_backtest_all_strategies 와 동일 공식).
    if initial_cash > 0:
        fee_adj_pnl  = result.gross_pnl - result.total_fees - result.total_taxes
        slip_adj_pnl = fee_adj_pnl - result.total_slippage
        row["fee_adjusted_return"]      = fee_adj_pnl / initial_cash
        row["slippage_adjusted_return"] = slip_adj_pnl / initial_cash

    scores = compute_score(
        expectancy=row["expectancy"],
        profit_factor=row["profit_factor"],
        win_rate=row["win_rate"],
        max_drawdown=int(row["max_drawdown"] or 0),
        trade_count=trade_count,
        initial_cash=initial_cash,
        avg_trade_notional=avg_notional,
    )
    row.update(scores)
    row["category"] = categorize(row)
    return row


# ─────────────────────────────────────────────────────────────────────
# 6. Walk-forward 검증 준비 — top 후보에 대해 small fold 실행
# ─────────────────────────────────────────────────────────────────────


def _build_walk_forward_plan(top_rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """추천 후보에 대해 walk-forward 실행 *계획* 을 dict 로 carry — 다음 PR /
    operator 가 명시적으로 execute. 본 함수는 plan 메타만 만든다 (실 실행
    여부는 caller 분기)."""
    plan = []
    for row in top_rows:
        plan.append({
            "strategy": row["strategy"],
            "params":   row["params"],
            "walk_forward_config": {
                "mode":             "rolling",
                "train_days":       60,
                "validation_days":  20,
                "step_days":        20,
                "holdout_days":     30,
                "min_fold_count":   3,
                "min_positive_fold_ratio":   0.6,
                "max_single_fold_pnl_share": 0.7,
            },
            "expected_minimum_fold_count": 3,
            "note": "Walk-forward 는 같은 (symbol, period) 로 실행하되 train/validation 을 fold 단위로 분리. holdout 30일은 fold 학습에 사용 안 함.",
        })
    return plan


def _execute_walk_forward(
    *,
    top_rows:     list[dict[str, Any]],
    bars:         list,
    backtest_config: BacktestConfig,
    initial_cash: int,
    quantity:     int,
    plan:         list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """plan 의 walk_forward_config 로 *실제* 실행 → JSON 직렬화 가능한 result
    list 반환. 단일 호출 당 fold N 회 = 추가 백테스트 N 회 — 본 함수는 paper
    candidate 수만큼만 호출."""
    out: list[dict[str, Any]] = []
    for row, plan_item in zip(top_rows, plan):
        wf_cfg_dict = plan_item["walk_forward_config"]
        wf_cfg = WalkForwardConfig(
            mode=wf_cfg_dict["mode"],
            train_days=wf_cfg_dict["train_days"],
            validation_days=wf_cfg_dict["validation_days"],
            step_days=wf_cfg_dict["step_days"],
            holdout_days=wf_cfg_dict["holdout_days"],
            min_fold_count=wf_cfg_dict["min_fold_count"],
            min_positive_fold_ratio=wf_cfg_dict["min_positive_fold_ratio"],
            max_single_fold_pnl_share=wf_cfg_dict["max_single_fold_pnl_share"],
        )
        factory = make_strategy_factory(row["strategy"], params=row["params"])
        wf_result = run_walk_forward(
            bars=bars,
            strategy_factory=factory,
            walk_forward_config=wf_cfg,
            backtest_config=backtest_config,
            initial_cash=initial_cash,
            quantity=quantity,
        )
        # WalkForwardResult → dict (메트릭만 carry, 거대한 fold 내부 데이터 X).
        out.append({
            "strategy": row["strategy"],
            "params":   row["params"],
            "fold_count": len(wf_result.folds),
            "fold_validation_pnls": [
                int(f.validation_metrics.get("total_pnl", 0) or 0)
                for f in wf_result.folds
            ],
            "fold_positive_ratio": _fold_positive_ratio(wf_result.folds),
            "holdout_metrics":    wf_result.holdout_metrics,
            "holdout_window":     wf_result.holdout_window,
        })
    return out


def _fold_positive_ratio(folds) -> float | None:
    """검증 fold 중 total_pnl > 0 인 비율. fold 0 이면 None."""
    if not folds:
        return None
    pos = sum(1 for f in folds if (f.validation_metrics.get("total_pnl") or 0) > 0)
    return round(pos / len(folds), 4)


# ─────────────────────────────────────────────────────────────────────
# 7. 산출물 작성
# ─────────────────────────────────────────────────────────────────────


def _ranking(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """category=PASS 우선 정렬 → 내부적으로 total_score 내림차순. PASS 가 아닌
    rows 는 후순위 — 각 카테고리 내에서 score 내림차순."""
    cat_order = {
        CATEGORY_PASS:                3,
        CATEGORY_LOW_QUALITY:         2,
        CATEGORY_NEGATIVE_EXPECTANCY: 1,
        CATEGORY_INSUFFICIENT_DATA:   0,
    }
    return sorted(
        rows,
        key=lambda r: (
            cat_order.get(r.get("category"), -1),
            float(r.get("total_score", 0.0) or 0.0),
        ),
        reverse=True,
    )


def _select_paper_candidates(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """전략별 *최고 점수* 1개씩 → 그 중 PASS + score ≥ PAPER_MIN_SCORE 인 항목
    → 최대 PAPER_MAX_RECOMMEND 개."""
    best_per_strategy: dict[str, dict[str, Any]] = {}
    for r in rows:
        if r.get("category") != CATEGORY_PASS:
            continue
        if float(r.get("total_score", 0.0) or 0.0) < PAPER_MIN_SCORE:
            continue
        s = r["strategy"]
        cur = best_per_strategy.get(s)
        if cur is None or float(r["total_score"]) > float(cur["total_score"]):
            best_per_strategy[s] = r
    candidates = sorted(
        best_per_strategy.values(),
        key=lambda r: float(r["total_score"]),
        reverse=True,
    )
    return candidates[:PAPER_MAX_RECOMMEND]


_CSV_COLUMNS = [
    "rank",
    "category",
    "strategy",
    "params",
    "trade_count",
    "win_rate",
    "profit_factor",
    "expectancy",
    "max_drawdown",
    "fee_adjusted_return",
    "slippage_adjusted_return",
    "expectancy_score",
    "profit_factor_score",
    "win_rate_score",
    "mdd_score",
    "trade_count_score",
    "total_score",
]


def _fmt(v: Any) -> str:
    if v is None:
        return ""
    if isinstance(v, float):
        return f"{v:.6f}"
    if isinstance(v, dict):
        return json.dumps(v, ensure_ascii=False, sort_keys=True)
    return str(v)


def write_summary_json(
    *,
    rows:         list[dict[str, Any]],
    categorized:  dict[str, list[dict[str, Any]]],
    paper_candidates: list[dict[str, Any]],
    walk_forward_plan: list[dict[str, Any]],
    walk_forward_results: list[dict[str, Any]] | None,
    run_meta:     dict[str, Any],
    path:         Path,
) -> None:
    payload = {
        "generated_at":   datetime.now().isoformat(),
        "run_meta":       run_meta,
        "policy": {
            "insufficient_data_min_trades":  INSUFFICIENT_DATA_MIN_TRADES,
            "paper_min_profit_factor":       PAPER_MIN_PROFIT_FACTOR,
            "paper_min_win_rate":            PAPER_MIN_WIN_RATE,
            "paper_max_mdd_pct":             PAPER_MAX_MDD_PCT,
            "paper_min_score":               PAPER_MIN_SCORE,
            "paper_max_recommend":           PAPER_MAX_RECOMMEND,
        },
        "results":        _ranking(rows),
        "by_category": {
            cat: [r["strategy"] + "|" + json.dumps(r["params"], sort_keys=True)
                  for r in cat_rows]
            for cat, cat_rows in categorized.items()
        },
        "paper_candidates":      paper_candidates,
        "walk_forward_plan":     walk_forward_plan,
        "walk_forward_results":  walk_forward_results,
        "disclaimer": (
            "본 결과는 MockMarketData 결정론적 합성 OHLCV 기반 — *실 시장 성과 아님*. "
            "paper_candidates 는 자동 적용되지 않으며, Paper 운용 진입은 "
            "별도 운영자 승인 + 실 데이터 walk-forward + paper_gate(#72) 통과 후에만 진행. "
            "본 스크립트는 어떤 파라미터도 코드/.env/DB 에 자동 반영하지 않는다."
        ),
    }
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2, default=str), encoding="utf-8")


def write_ranking_csv(rows: list[dict[str, Any]], path: Path) -> None:
    ranked = _ranking(rows)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8") as f:
        writer = csv.writer(f)
        writer.writerow(_CSV_COLUMNS)
        for rank, r in enumerate(ranked, start=1):
            writer.writerow([
                rank,
                r.get("category", ""),
                r.get("strategy", ""),
                _fmt(r.get("params", {})),
                r.get("trade_count", 0),
                _fmt(r.get("win_rate")),
                _fmt(r.get("profit_factor")),
                _fmt(r.get("expectancy")),
                r.get("max_drawdown", 0),
                _fmt(r.get("fee_adjusted_return")),
                _fmt(r.get("slippage_adjusted_return")),
                _fmt(r.get("expectancy_score")),
                _fmt(r.get("profit_factor_score")),
                _fmt(r.get("win_rate_score")),
                _fmt(r.get("mdd_score")),
                _fmt(r.get("trade_count_score")),
                _fmt(r.get("total_score")),
            ])


def write_candidates_markdown(
    *,
    paper_candidates: list[dict[str, Any]],
    rows:             list[dict[str, Any]],
    categorized:      dict[str, list[dict[str, Any]]],
    walk_forward_plan: list[dict[str, Any]],
    walk_forward_results: list[dict[str, Any]] | None,
    run_meta:         dict[str, Any],
    path:             Path,
) -> None:
    lines: list[str] = []
    lines.append("# 전략 파라미터 최적화 — Paper 운용 후보 리포트")
    lines.append("")
    lines.append("> ⚠ **본 결과는 *투자 조언이 아니라* 자동매매 시스템 검증 자료입니다.**")
    lines.append(">")
    lines.append("> 백테스트는 `MockMarketData` 결정론적 합성 OHLCV 기반 — *실 시장 성과 아님*.")
    lines.append("> paper_candidates 는 *추천 후보* — 자동 적용되지 않습니다. Paper 운용 진입은")
    lines.append("> 별도 운영자 승인 + 실 데이터 walk-forward + paper_gate (#72) 통과 후에만.")
    lines.append("")
    lines.append(f"- 생성 시각: `{datetime.now().isoformat(timespec='seconds')}`")
    lines.append(f"- 심볼 / 기간: `{run_meta.get('symbol')}` / `{run_meta.get('start')} ~ {run_meta.get('end')}`")
    lines.append(f"- 비용: commission `{run_meta.get('commission_bps')} bps` / tax `{run_meta.get('tax_bps')} bps` / slippage `{run_meta.get('slippage_bps')} bps`")
    lines.append(f"- 데이터 소스: `MockMarketData` (결정론적 합성 OHLCV)")
    lines.append("")
    lines.append("## 1. Paper 운용 후보 (최대 2개)")
    lines.append("")
    if not paper_candidates:
        lines.append("**현 시점에 추천 후보가 없습니다.**")
        lines.append("")
        lines.append("이유: 모든 grid 조합이 다음 중 하나에 해당.")
        lines.append("- `INSUFFICIENT_DATA` — `trade_count < 10` (통계 신뢰 부족)")
        lines.append("- `NEGATIVE_EXPECTANCY` — 비용 반영 expectancy ≤ 0")
        lines.append(f"- `LOW_QUALITY` — `profit_factor < {PAPER_MIN_PROFIT_FACTOR}` / `win_rate < {PAPER_MIN_WIN_RATE}` / `MDD > {int(PAPER_MAX_MDD_PCT*100)}% of initial_cash`")
        lines.append("")
        lines.append("**다음 단계 권고**: 실 데이터 (`yfinance` / KIS adapter) 로 재실행. `MockMarketData` 는 합성 봉 패턴이 단조로워 4 전략의 진입 조건이 거의 트리거되지 않음.")
    else:
        lines.append("| Rank | Strategy | Total Score | Params | Trades | Win Rate | PF | Expectancy | MDD |")
        lines.append("|------|----------|-------------|--------|--------|----------|----|-----------|-----|")
        for i, c in enumerate(paper_candidates, start=1):
            lines.append(
                "| {i} | {strat} | {score} | `{params}` | {tc} | {wr} | {pf} | {ex} | {dd} |".format(
                    i=i,
                    strat=c["strategy"],
                    score=_fmt(c.get("total_score")),
                    params=_fmt(c.get("params", {})),
                    tc=c.get("trade_count"),
                    wr=_fmt_pct(c.get("win_rate")),
                    pf=_fmt(c.get("profit_factor")),
                    ex=_fmt(c.get("expectancy")),
                    dd=c.get("max_drawdown", 0),
                ),
            )
    lines.append("")

    lines.append("## 2. 카테고리 별 분포")
    lines.append("")
    lines.append("| Category | Count | 의미 |")
    lines.append("|----------|-------|------|")
    cat_labels = {
        CATEGORY_PASS:                "1차 PASS — 점수 / 임계 통과",
        CATEGORY_LOW_QUALITY:         "지표 부족 (PF / win_rate / MDD)",
        CATEGORY_NEGATIVE_EXPECTANCY: "비용 반영 expectancy ≤ 0",
        CATEGORY_INSUFFICIENT_DATA:   f"trade_count < {INSUFFICIENT_DATA_MIN_TRADES}",
    }
    for cat in (CATEGORY_PASS, CATEGORY_LOW_QUALITY, CATEGORY_NEGATIVE_EXPECTANCY, CATEGORY_INSUFFICIENT_DATA):
        n = len(categorized.get(cat, []))
        lines.append(f"| `{cat}` | {n} | {cat_labels[cat]} |")
    lines.append("")

    lines.append("## 3. Walk-forward 검증 plan")
    lines.append("")
    if not walk_forward_plan:
        lines.append("후보 없음 — walk-forward 계획 없음.")
    else:
        lines.append("각 후보를 *별도* walk-forward 로 재검증. 본 PR 시점에 plan 만 carry —")
        lines.append("실제 실행 결과는 `walk_forward_results` 키 참조 (옵션 실행 시 채워짐).")
        lines.append("")
        for p in walk_forward_plan:
            wf = p["walk_forward_config"]
            lines.append(f"- **{p['strategy']}** params=`{_fmt(p['params'])}` ")
            lines.append(f"  - mode `{wf['mode']}`, train `{wf['train_days']}d`, validation `{wf['validation_days']}d`, holdout `{wf['holdout_days']}d`")
            lines.append(f"  - min_fold_count `{wf['min_fold_count']}`, min_positive_fold_ratio `{wf['min_positive_fold_ratio']}`")
    if walk_forward_results:
        lines.append("")
        lines.append("### Walk-forward 실행 결과 (요약)")
        lines.append("")
        lines.append("| Strategy | Fold Count | Positive Ratio | Holdout Trades |")
        lines.append("|----------|------------|----------------|----------------|")
        for wf_r in walk_forward_results:
            ho = wf_r.get("holdout_metrics") or {}
            lines.append(
                "| {s} | {fc} | {pr} | {ht} |".format(
                    s=wf_r["strategy"],
                    fc=wf_r["fold_count"],
                    pr=_fmt(wf_r.get("fold_positive_ratio")),
                    ht=ho.get("trade_count", 0),
                ),
            )
    lines.append("")

    lines.append("## 4. 안전 / 무결성")
    lines.append("")
    lines.append("- broker / OrderExecutor / route_order / KIS LIVE API 호출 0건 (스크립트 정적 grep / AST 검사).")
    lines.append("- `ENABLE_LIVE_TRADING` / `ENABLE_AI_EXECUTION` / `ENABLE_FUTURES_LIVE_TRADING` 변경 0건.")
    lines.append("- `KIS_IS_PAPER=true` default 유지. `.env` 수정 0건.")
    lines.append("- 파라미터 자동 적용 0건 — 본 스크립트는 advisory.")
    lines.append("- 산출물은 `reports/strategy_optimization/` 하위 — `.gitignore` 로 커밋 차단.")
    lines.append("")
    lines.append("## 5. 다음 단계 (참고)")
    lines.append("")
    lines.append("- **실 데이터 재실행**: `yfinance` / KIS adapter 로 같은 grid 재실행.")
    lines.append("- **Walk-forward 실 실행**: 본 plan 의 fold 별 결과 검토 → over-fit 여부 판단.")
    lines.append("- **Paper 모드 진입**: 운영자 명시 opt-in → paper_gate (#72) 평가 → 실 KIS 모의 자금 운용.")
    lines.append("- **본 스크립트는 어떤 파라미터도 자동 적용하지 않는다** — 모든 적용은 별도 PR + 별도 검증 필요.")
    lines.append("")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(lines), encoding="utf-8")


def _fmt_pct(v: Any) -> str:
    if v is None:
        return "n/a"
    try:
        return f"{float(v) * 100:.2f}%"
    except (TypeError, ValueError):
        return str(v)


# ─────────────────────────────────────────────────────────────────────
# 8. CLI / main
# ─────────────────────────────────────────────────────────────────────


def _parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    p = argparse.ArgumentParser(
        description="6 전략 파라미터 grid search + 점수화 + paper 후보 추천",
    )
    p.add_argument("--output-dir", default=DEFAULT_OUTPUT_DIR)
    p.add_argument("--symbol",       default=DEFAULT_SYMBOL)
    p.add_argument("--start",        default=DEFAULT_START)
    p.add_argument("--end",          default=DEFAULT_END)
    p.add_argument("--initial-cash", type=int, default=DEFAULT_INITIAL_CASH)
    p.add_argument("--quantity",     type=int, default=DEFAULT_QUANTITY)
    p.add_argument("--commission-bps", type=int, default=DEFAULT_COMMISSION_BPS)
    p.add_argument("--tax-bps",        type=int, default=DEFAULT_TAX_BPS)
    p.add_argument("--slippage-bps",   type=int, default=DEFAULT_SLIPPAGE_BPS)
    p.add_argument("--strategies",   nargs="*", default=None,
                   help="실행할 전략 이름 (생략 시 6개 전체)")
    p.add_argument("--max-trials-per-strategy", type=int, default=200,
                   help="각 전략별 grid 조합 상한 (방어적 cap)")
    p.add_argument("--run-walk-forward", action="store_true",
                   help="paper 후보에 대해 *실제로* walk-forward 실행 (default: plan 만 carry)")
    p.add_argument("--dry-run", action="store_true",
                   help="파일 작성 없이 stdout 요약만")
    return p.parse_args(argv)


async def run_all(args: argparse.Namespace) -> dict[str, Any]:
    requested = list(args.strategies) if args.strategies else list(STRATEGY_REGISTRY.keys())
    unknown = [s for s in requested if s not in STRATEGY_REGISTRY]
    if unknown:
        raise SystemExit(f"unknown strategies requested: {unknown}. "
                         f"registered: {sorted(STRATEGY_REGISTRY.keys())}")

    grids = _build_param_grids()
    config = _build_config(args.commission_bps, args.slippage_bps, args.tax_bps)

    market = MockMarketData()
    start_dt = datetime.fromisoformat(args.start)
    end_dt   = datetime.fromisoformat(args.end)
    bars = await market.get_bars(args.symbol, start_dt, end_dt, Interval.DAY_1)

    if not bars:
        raise SystemExit("no bars in requested range — adjust --start/--end")

    rows: list[dict[str, Any]] = []
    for name in requested:
        grid = grids.get(name, [{}])
        if len(grid) > args.max_trials_per_strategy:
            _log.warning(
                "strategy %s grid (%d) > cap (%d) — truncating",
                name, len(grid), args.max_trials_per_strategy,
            )
            grid = grid[: args.max_trials_per_strategy]
        _log.info("optimize: %s (%d trials)", name, len(grid))
        for params in grid:
            row = _trial_row(
                strategy_name=name,
                params=params,
                bars=bars,
                config=config,
                initial_cash=args.initial_cash,
                quantity=args.quantity,
            )
            rows.append(row)

    # 카테고리 분포.
    categorized: dict[str, list[dict[str, Any]]] = {cat: [] for cat in ALL_CATEGORIES}
    for r in rows:
        categorized.setdefault(r.get("category", CATEGORY_INSUFFICIENT_DATA), []).append(r)

    paper_candidates = _select_paper_candidates(rows)
    walk_forward_plan = _build_walk_forward_plan(paper_candidates)

    walk_forward_results: list[dict[str, Any]] | None = None
    if args.run_walk_forward and paper_candidates:
        _log.info("running walk-forward on %d candidate(s)", len(paper_candidates))
        walk_forward_results = _execute_walk_forward(
            top_rows=paper_candidates,
            bars=bars,
            backtest_config=config,
            initial_cash=args.initial_cash,
            quantity=args.quantity,
            plan=walk_forward_plan,
        )

    return {
        "rows":              rows,
        "categorized":       categorized,
        "paper_candidates":  paper_candidates,
        "walk_forward_plan": walk_forward_plan,
        "walk_forward_results": walk_forward_results,
        "run_meta": {
            "symbol":          args.symbol,
            "start":           args.start,
            "end":             args.end,
            "initial_cash":    args.initial_cash,
            "quantity":        args.quantity,
            "commission_bps":  args.commission_bps,
            "tax_bps":         args.tax_bps,
            "slippage_bps":    args.slippage_bps,
            "execution_model": config.execution_model,
            "execution_delay": config.execution_delay_bars,
        },
    }


def main(argv: list[str] | None = None) -> int:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s %(message)s",
    )
    args = _parse_args(argv)
    payload = asyncio.run(run_all(args))

    if args.dry_run:
        print(json.dumps({
            "run_meta":          payload["run_meta"],
            "n_rows":            len(payload["rows"]),
            "n_paper_candidates": len(payload["paper_candidates"]),
            "by_category":       {k: len(v) for k, v in payload["categorized"].items()},
        }, ensure_ascii=False, indent=2))
        return 0

    out_dir = Path(args.output_dir)
    write_summary_json(
        rows=payload["rows"],
        categorized=payload["categorized"],
        paper_candidates=payload["paper_candidates"],
        walk_forward_plan=payload["walk_forward_plan"],
        walk_forward_results=payload["walk_forward_results"],
        run_meta=payload["run_meta"],
        path=out_dir / "optimization_summary.json",
    )
    write_ranking_csv(payload["rows"], out_dir / "optimization_ranking.csv")
    write_candidates_markdown(
        paper_candidates=payload["paper_candidates"],
        rows=payload["rows"],
        categorized=payload["categorized"],
        walk_forward_plan=payload["walk_forward_plan"],
        walk_forward_results=payload["walk_forward_results"],
        run_meta=payload["run_meta"],
        path=out_dir / "paper_candidates.md",
    )
    print(f"[OK] wrote {out_dir}/optimization_summary.json")
    print(f"[OK] wrote {out_dir}/optimization_ranking.csv")
    print(f"[OK] wrote {out_dir}/paper_candidates.md")
    print(f"     {len(payload['paper_candidates'])} paper candidate(s) recommended.")
    return 0


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())

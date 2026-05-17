#!/usr/bin/env python3
"""feature/backtest-six-strategies — baseline backtest 6 strategies.

본 스크립트는 `STRATEGY_REGISTRY` 의 6개 등록 전략을 모두 백테스트하고, 동일한
지표 매트릭스로 비교한 결과를 `reports/backtest/` 에 JSON / CSV / Markdown 으로
저장한다.

CLAUDE.md 절대 원칙:
- 본 스크립트는 *read-only*. broker / route_order / OrderExecutor / KIS API /
  AI SDK / 외부 HTTP 어떤 것도 호출하지 *않는다*.
- `ENABLE_LIVE_TRADING` / `ENABLE_AI_EXECUTION` / `ENABLE_FUTURES_LIVE_TRADING` /
  `KIS_IS_PAPER` 환경 변수 *수정 0건*. `.env` 작성 / 갱신 0건.
- `broker.place_order(` / `route_order(` / `OrderExecutor` import / 호출 0건.
- Anthropic / OpenAI / Telegram API 호출 0건.
- 백테스트 데이터는 `MockMarketData` 의 결정론적 합성 OHLCV — 실 시장 자료
  아님. 본 결과를 *실 성과로 표현하지 않는다*.

산출물 (output_dir 기본 `reports/backtest/`):
- strategy_backtest_summary.json — 전략별 raw + 비용 반영 지표 풀세트.
- strategy_backtest_ranking.csv — 비용 반영 risk_adjusted_score 내림차순.
- strategy_backtest_report.md — 운영자 / 다음 단계 (파라미터 최적화) 검토용
  요약 markdown.

사용:
    python scripts/run_backtest_all_strategies.py
    python scripts/run_backtest_all_strategies.py \\
        --output-dir reports/backtest \\
        --symbol 005930 --start 2026-01-01 --end 2026-06-30 \\
        --initial-cash 10000000 --quantity 10

지표:
- total_return / annualized_return / win_rate / trade_count / profit_factor /
  expectancy / max_drawdown / avg_trade_pnl / loss_streak / sharpe_like_score
  (sharpe_ratio with NaN→None) / risk_adjusted_score (= expectancy / max_dd
  proxy) / fee_adjusted_return / slippage_adjusted_return.
- "fee_adjusted_return" : raw_return - fees - taxes (slippage 제외).
- "slippage_adjusted_return" : raw - fees - taxes - slippage (전체 비용 반영).

수수료 / 슬리피지 기본값 (보수적 한국 주식 가정 — 운영자 override 가능):
- commission_bps = 15  (0.15% = 일반 증권사 + KIS 수수료 보수 추정)
- tax_bps        = 23  (0.23% = 한국 거래세, SELL 측만 적용)
- slippage_bps   = 5   (0.05% 보수, 호가 갭 / 체결 지연 가정)

본 값은 *기본값* 이며 백테스트 *결과 자체가 운영자의 실 비용을 보장하지 않는다*.
"""
from __future__ import annotations

import argparse
import asyncio
import csv
import json
import logging
import sys
from datetime import datetime
from pathlib import Path
from typing import Any

# backend/ 를 sys.path 에 추가 — 본 스크립트는 repo root 에서 실행되는 것을
# 가정. 별도 venv 진입은 backend/.venv-310 을 운영자가 명시적으로 활성화.
_REPO_ROOT = Path(__file__).resolve().parents[1]
_BACKEND_DIR = _REPO_ROOT / "backend"
if str(_BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(_BACKEND_DIR))

from app.backtest.engine import BacktestEngine  # noqa: E402
from app.backtest.types import BacktestConfig, BacktestResult, Trade  # noqa: E402
from app.market.base import Interval  # noqa: E402
from app.market.mock import MockMarketData  # noqa: E402
from app.strategies.concrete import STRATEGY_REGISTRY, build_strategy  # noqa: E402

_log = logging.getLogger("autotrade.backtest_all")


# ─────────────────────────────────────────────────────────────────────
# 1. 기본값 (운영자 override 가능)
# ─────────────────────────────────────────────────────────────────────
DEFAULT_COMMISSION_BPS = 15   # 0.15%
DEFAULT_TAX_BPS        = 23   # 0.23% (SELL 측)
DEFAULT_SLIPPAGE_BPS   = 5    # 0.05%
DEFAULT_INITIAL_CASH   = 10_000_000
DEFAULT_QUANTITY       = 10
DEFAULT_SYMBOL         = "005930"  # MockMarketData base price = 75,000
DEFAULT_START          = "2026-01-01"
DEFAULT_END            = "2026-06-30"


# ─────────────────────────────────────────────────────────────────────
# 2. 지표 계산 helpers
# ─────────────────────────────────────────────────────────────────────


def _years_between(first_ts: datetime, last_ts: datetime) -> float:
    """기간을 연 단위 float 로. 1일 이하 / 음수면 0.0 으로 클램프."""
    if last_ts <= first_ts:
        return 0.0
    days = (last_ts - first_ts).total_seconds() / 86400.0
    return max(0.0, days / 365.25)


def _annualized_return(total_return_pct: float, years: float) -> float | None:
    """`(1+r)^(1/years) - 1`. 1년 미만 / 음수 cumulative 면 단순 비례.
    NaN/inf 가능성 차단 — finite 가 아니면 None."""
    import math
    if years <= 0:
        return None
    base = 1.0 + total_return_pct
    if base <= 0:
        # 자본 손실 100% 이상 — 산식상 음수 base 의 fractional power 는 정의 불가.
        # 운영자가 결과를 즉시 인지하도록 None.
        return None
    try:
        val = base ** (1.0 / years) - 1.0
    except (OverflowError, ValueError):
        return None
    return val if math.isfinite(val) else None


def _safe_pct(numer: float, denom: float) -> float | None:
    """numer / denom. denom == 0 → None."""
    if denom == 0:
        return None
    return float(numer) / float(denom)


def _avg_trade_pnl(trades: list[Trade]) -> float | None:
    if not trades:
        return None
    return float(sum(t.pnl for t in trades)) / len(trades)


def _risk_adjusted_score(
    expectancy: float | None,
    max_dd: int,
    trade_count: int,
) -> float | None:
    """단순한 risk-adjusted 점수 — `expectancy / |max_dd|` 의 정수 normalization.

    sharpe_ratio 가 None (분산 0, 거래수 부족 등) 일 때의 *fallback*. trade_count
    가 너무 적으면 (≤ 1) None 으로 무의미한 점수 차단.
    """
    if expectancy is None or trade_count <= 1:
        return None
    if max_dd <= 0:
        return None
    return float(expectancy) / float(max_dd)


# ─────────────────────────────────────────────────────────────────────
# 3. 단일 전략 백테스트
# ─────────────────────────────────────────────────────────────────────


def _build_config(commission_bps: int, slippage_bps: int, tax_bps: int) -> BacktestConfig:
    """체결 모델은 *next_open* 권장 — same_close 는 promotion 평가 금지 (BacktestConfig docstring)."""
    return BacktestConfig(
        execution_model="next_open",
        execution_delay_bars=1,
        slippage_bps=slippage_bps,
        commission_bps=commission_bps,
        tax_bps=tax_bps,
        exit_on_last_bar=True,
    )


def _result_to_metrics(
    *,
    strategy_name: str,
    result: BacktestResult,
    initial_cash: int,
    first_ts: datetime | None,
    last_ts: datetime | None,
    config: BacktestConfig,
) -> dict[str, Any]:
    """`BacktestResult` 를 운영자 / 다음 단계가 비교 가능한 지표 dict 로 변환."""
    trades = result.trades
    raw_pnl   = result.gross_pnl
    net_pnl   = result.net_pnl
    fees      = result.total_fees
    taxes     = result.total_taxes
    slip      = result.total_slippage

    # 수익률 (initial_cash 대비). 백분율 float.
    total_return_pct           = _safe_pct(net_pnl, initial_cash) or 0.0
    raw_return_pct             = _safe_pct(raw_pnl, initial_cash) or 0.0
    # fee_adjusted = raw - fees - taxes (slippage 제외).
    fee_adj_pnl                = raw_pnl - fees - taxes
    fee_adjusted_return_pct    = _safe_pct(fee_adj_pnl, initial_cash) or 0.0
    # slippage_adjusted = raw - fees - taxes - slippage (전체 비용).
    slip_adj_pnl               = raw_pnl - fees - taxes - slip
    slippage_adjusted_return_pct = _safe_pct(slip_adj_pnl, initial_cash) or 0.0

    years = _years_between(first_ts, last_ts) if first_ts and last_ts else 0.0
    annualized = _annualized_return(total_return_pct, years)

    summary = result.summarize_metrics()
    expectancy_val   = summary.get("expectancy")
    profit_factor_v  = summary.get("profit_factor")
    sharpe_v         = summary.get("sharpe_ratio")
    max_dd           = int(summary.get("max_drawdown", 0))
    loss_streak      = int(summary.get("max_consecutive_losses", 0))

    return {
        "strategy":                strategy_name,
        "trade_count":             len(trades),
        "bars_processed":          result.bars_processed,
        "initial_cash":            initial_cash,
        "final_cash":              result.final_cash,
        "raw_pnl":                 raw_pnl,
        "net_pnl":                 net_pnl,
        "fees":                    fees,
        "taxes":                   taxes,
        "slippage_cost":           slip,

        # 필수 지표 (사용자 요청서 12종)
        "total_return":               total_return_pct,
        "annualized_return":          annualized,
        "win_rate":                   summary.get("win_rate"),
        "profit_factor":              profit_factor_v,
        "expectancy":                 expectancy_val,
        "max_drawdown":               max_dd,
        "avg_trade_pnl":              _avg_trade_pnl(trades),
        "loss_streak":                loss_streak,
        "sharpe_like_score":          sharpe_v,
        "risk_adjusted_score":        _risk_adjusted_score(expectancy_val, max_dd, len(trades)),
        "fee_adjusted_return":        fee_adjusted_return_pct,
        "slippage_adjusted_return":   slippage_adjusted_return_pct,

        # 추가 진단 정보
        "max_consecutive_wins":       summary.get("max_consecutive_wins"),
        "first_bar_ts":               first_ts.isoformat() if first_ts else None,
        "last_bar_ts":                last_ts.isoformat() if last_ts else None,
        "years_observed":             years,
        "config": {
            "execution_model":  config.execution_model,
            "execution_delay":  config.execution_delay_bars,
            "commission_bps":   config.commission_bps,
            "tax_bps":          config.tax_bps,
            "slippage_bps":     config.slippage_bps,
        },
    }


async def _backtest_single(
    *,
    strategy_name: str,
    market: MockMarketData,
    symbol: str,
    start: datetime,
    end: datetime,
    initial_cash: int,
    quantity: int,
    config: BacktestConfig,
) -> dict[str, Any]:
    """단일 전략 실행 + 지표 계산. 실패는 reason 포함 dict 로 carry — process 중단 X."""
    try:
        bars = await market.get_bars(symbol, start, end, Interval.DAY_1)
    except Exception as exc:  # noqa: BLE001
        return {
            "strategy":    strategy_name,
            "error":       f"market data load failed: {type(exc).__name__}",
            "trade_count": 0,
        }

    if not bars:
        return {
            "strategy":    strategy_name,
            "error":       "no bars in requested range",
            "trade_count": 0,
        }

    try:
        strategy = build_strategy(strategy_name, params=None)
    except Exception as exc:  # noqa: BLE001
        return {
            "strategy":    strategy_name,
            "error":       f"strategy build failed: {type(exc).__name__}: {exc}",
            "trade_count": 0,
        }

    engine = BacktestEngine(initial_cash=initial_cash, quantity=quantity)
    try:
        result = engine.run(bars, strategy, config=config)
    except Exception as exc:  # noqa: BLE001
        return {
            "strategy":    strategy_name,
            "error":       f"engine.run raised: {type(exc).__name__}: {exc}",
            "trade_count": 0,
        }

    metrics = _result_to_metrics(
        strategy_name=strategy_name,
        result=result,
        initial_cash=initial_cash,
        first_ts=bars[0].timestamp,
        last_ts=bars[-1].timestamp,
        config=config,
    )
    return metrics


# ─────────────────────────────────────────────────────────────────────
# 4. 산출물 작성
# ─────────────────────────────────────────────────────────────────────


def _rank_by_risk_adjusted(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """risk_adjusted_score 내림차순 정렬 — None 은 끝으로. tie-break: total_return."""
    def _key(r: dict[str, Any]) -> tuple[int, float, float]:
        score = r.get("risk_adjusted_score")
        # None 은 끝으로 (key 0 = None 그룹 / 1 = score 그룹).
        if score is None:
            return (0, 0.0, float(r.get("total_return") or 0.0))
        return (1, float(score), float(r.get("total_return") or 0.0))
    return sorted(rows, key=_key, reverse=True)


def write_summary_json(rows: list[dict[str, Any]], path: Path, *, run_meta: dict) -> None:
    payload = {
        "generated_at":     datetime.now().isoformat(),
        "run_meta":         run_meta,
        "strategies":       rows,
        "ranking":          [r["strategy"] for r in _rank_by_risk_adjusted(rows)],
        "disclaimer":       (
            "본 결과는 MockMarketData 의 결정론적 합성 OHLCV 기반이며 *실 시장 성과* 가 아닙니다. "
            "실거래 적용 전 실 데이터 (KIS / yfinance) 백테스트 + walk-forward + paper / shadow 검증 필요."
        ),
    }
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


_CSV_COLUMNS = [
    "rank",
    "strategy",
    "trade_count",
    "total_return",
    "annualized_return",
    "win_rate",
    "profit_factor",
    "expectancy",
    "max_drawdown",
    "avg_trade_pnl",
    "loss_streak",
    "sharpe_like_score",
    "risk_adjusted_score",
    "fee_adjusted_return",
    "slippage_adjusted_return",
]


def write_ranking_csv(rows: list[dict[str, Any]], path: Path) -> None:
    """비용 반영 risk_adjusted_score 내림차순 CSV — 다음 단계 (parameter
    optimization) 가 1차 후보 정렬에 사용."""
    ranked = _rank_by_risk_adjusted(rows)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8") as f:
        writer = csv.writer(f)
        writer.writerow(_CSV_COLUMNS)
        for rank, r in enumerate(ranked, start=1):
            writer.writerow([
                rank,
                r.get("strategy", ""),
                r.get("trade_count", 0),
                _fmt(r.get("total_return")),
                _fmt(r.get("annualized_return")),
                _fmt(r.get("win_rate")),
                _fmt(r.get("profit_factor")),
                _fmt(r.get("expectancy")),
                r.get("max_drawdown", 0),
                _fmt(r.get("avg_trade_pnl")),
                r.get("loss_streak", 0),
                _fmt(r.get("sharpe_like_score")),
                _fmt(r.get("risk_adjusted_score")),
                _fmt(r.get("fee_adjusted_return")),
                _fmt(r.get("slippage_adjusted_return")),
            ])


def _fmt(v: Any) -> str:
    if v is None:
        return ""
    if isinstance(v, float):
        return f"{v:.6f}"
    return str(v)


def write_markdown_report(rows: list[dict[str, Any]], path: Path, *, run_meta: dict) -> None:
    """운영자가 *읽기 좋은* markdown 요약 — 다음 단계 진입 전 검토용."""
    ranked = _rank_by_risk_adjusted(rows)
    lines: list[str] = []
    lines.append("# 6 전략 baseline 백테스트 리포트")
    lines.append("")
    lines.append("> ⚠ **본 결과는 *투자 조언이 아니라* 자동매매 시스템 운영·검증·개선 자료입니다.**")
    lines.append("> ")
    lines.append("> 백테스트는 `MockMarketData` 의 결정론적 합성 OHLCV 기반 — *실 시장 성과 아님*.")
    lines.append("> 실거래 적용 전 실 데이터 + walk-forward + paper / shadow 검증이 별도로 필요합니다.")
    lines.append("")
    lines.append(f"- 생성 시각: `{datetime.now().isoformat(timespec='seconds')}`")
    lines.append(f"- 데이터 소스: `MockMarketData` (결정론적 합성 OHLCV)")
    lines.append(f"- 심볼: `{run_meta.get('symbol')}` / 기간: `{run_meta.get('start')} ~ {run_meta.get('end')}`")
    lines.append(f"- 초기 자본: `{run_meta.get('initial_cash'):,} KRW` / 1주문 수량: `{run_meta.get('quantity')}`")
    lines.append(f"- 체결 모델: `{run_meta.get('execution_model')}` / delay: `{run_meta.get('execution_delay')}` bars")
    lines.append(f"- 비용: commission `{run_meta.get('commission_bps')} bps` / tax `{run_meta.get('tax_bps')} bps` / slippage `{run_meta.get('slippage_bps')} bps`")
    lines.append("")
    lines.append("## 순위 (risk_adjusted_score 내림차순)")
    lines.append("")
    lines.append("| Rank | Strategy | Trades | Total Return | Annualized | Win Rate | Profit Factor | Expectancy | Max DD | Loss Streak | Sharpe-like | Risk-Adj |")
    lines.append("|------|----------|--------|--------------|------------|----------|---------------|------------|--------|-------------|-------------|----------|")
    for rank, r in enumerate(ranked, start=1):
        lines.append(
            "| {rank} | {strat} | {tc} | {tr} | {an} | {wr} | {pf} | {ex} | {dd} | {ls} | {sh} | {ra} |".format(
                rank=rank,
                strat=r.get("strategy", "?"),
                tc=r.get("trade_count", 0),
                tr=_fmt_pct(r.get("total_return")),
                an=_fmt_pct(r.get("annualized_return")),
                wr=_fmt_pct(r.get("win_rate")),
                pf=_fmt(r.get("profit_factor")),
                ex=_fmt(r.get("expectancy")),
                dd=r.get("max_drawdown", 0),
                ls=r.get("loss_streak", 0),
                sh=_fmt(r.get("sharpe_like_score")),
                ra=_fmt(r.get("risk_adjusted_score")),
            ),
        )
    lines.append("")
    lines.append("## 비용 영향 (fee_adjusted vs slippage_adjusted)")
    lines.append("")
    lines.append("| Strategy | Raw Return | Fee-Adj Return | Slippage-Adj Return | Fees | Taxes | Slippage |")
    lines.append("|----------|------------|----------------|---------------------|------|-------|----------|")
    for r in ranked:
        raw_pnl = r.get("raw_pnl", 0)
        init    = r.get("initial_cash", 1) or 1
        raw_ret = (raw_pnl / init) if init else 0.0
        lines.append(
            "| {strat} | {rr} | {fr} | {sr} | {f} | {t} | {s} |".format(
                strat=r.get("strategy", "?"),
                rr=_fmt_pct(raw_ret),
                fr=_fmt_pct(r.get("fee_adjusted_return")),
                sr=_fmt_pct(r.get("slippage_adjusted_return")),
                f=r.get("fees", 0),
                t=r.get("taxes", 0),
                s=r.get("slippage_cost", 0),
            ),
        )
    lines.append("")
    lines.append("## 안전 / 무결성")
    lines.append("")
    lines.append("- broker / OrderExecutor / route_order / KIS API 호출 0건 (스크립트 정적 grep 가드).")
    lines.append("- `ENABLE_LIVE_TRADING` / `ENABLE_AI_EXECUTION` / `ENABLE_FUTURES_LIVE_TRADING` 변경 0건.")
    lines.append("- `KIS_IS_PAPER=true` default 유지. `.env` 수정 0건.")
    lines.append("- 본 산출물은 `reports/backtest/` 하위 — `.gitignore` 로 커밋 차단됨.")
    lines.append("")
    lines.append("## 다음 단계 (참고)")
    lines.append("")
    lines.append("- 나. 전략별 파라미터 최적화: 본 ranking 의 상위 후보부터 grid / random search 진행.")
    lines.append("- 실 데이터 백테스트: `MarketDataAdapter` 의 yfinance / KIS adapter 로 재실행.")
    lines.append("- walk-forward + Monte Carlo: 단일 기간 결과의 over-fit 여부 검증.")
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
# 5. CLI
# ─────────────────────────────────────────────────────────────────────


def _parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    p = argparse.ArgumentParser(
        description="6 전략 baseline backtest — 결과를 JSON / CSV / Markdown 으로 저장",
    )
    p.add_argument("--output-dir", default="reports/backtest",
                   help="결과 저장 디렉토리 (default: reports/backtest)")
    p.add_argument("--symbol",       default=DEFAULT_SYMBOL)
    p.add_argument("--start",        default=DEFAULT_START, help="ISO date (YYYY-MM-DD)")
    p.add_argument("--end",          default=DEFAULT_END,   help="ISO date (YYYY-MM-DD)")
    p.add_argument("--initial-cash", type=int, default=DEFAULT_INITIAL_CASH)
    p.add_argument("--quantity",     type=int, default=DEFAULT_QUANTITY)
    p.add_argument("--commission-bps", type=int, default=DEFAULT_COMMISSION_BPS)
    p.add_argument("--tax-bps",        type=int, default=DEFAULT_TAX_BPS)
    p.add_argument("--slippage-bps",   type=int, default=DEFAULT_SLIPPAGE_BPS)
    p.add_argument("--strategies",   nargs="*", default=None,
                   help="실행할 전략 이름 (생략 시 6개 전체)")
    p.add_argument("--dry-run", action="store_true",
                   help="파일 작성 X — stdout 에 요약만 출력")
    return p.parse_args(argv)


async def run_all(args: argparse.Namespace) -> dict[str, Any]:
    """모든 전략 백테스트 실행 + 결과 dict 반환. 호출자가 파일 작성 분기 결정."""
    requested = list(args.strategies) if args.strategies else list(STRATEGY_REGISTRY.keys())
    # 유효성 검사 — 등록되지 않은 이름은 즉시 에러 (silent skip 금지).
    unknown = [s for s in requested if s not in STRATEGY_REGISTRY]
    if unknown:
        raise SystemExit(f"unknown strategies requested: {unknown}. "
                         f"registered: {sorted(STRATEGY_REGISTRY.keys())}")

    config = _build_config(args.commission_bps, args.slippage_bps, args.tax_bps)
    market = MockMarketData()
    start_dt = datetime.fromisoformat(args.start)
    end_dt   = datetime.fromisoformat(args.end)

    rows: list[dict[str, Any]] = []
    for name in requested:
        _log.info("backtest: %s", name)
        row = await _backtest_single(
            strategy_name=name,
            market=market,
            symbol=args.symbol,
            start=start_dt,
            end=end_dt,
            initial_cash=args.initial_cash,
            quantity=args.quantity,
            config=config,
        )
        rows.append(row)

    return {
        "rows": rows,
        "run_meta": {
            "symbol":           args.symbol,
            "start":            args.start,
            "end":              args.end,
            "initial_cash":     args.initial_cash,
            "quantity":         args.quantity,
            "commission_bps":   args.commission_bps,
            "tax_bps":          args.tax_bps,
            "slippage_bps":     args.slippage_bps,
            "execution_model":  config.execution_model,
            "execution_delay":  config.execution_delay_bars,
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
        print(json.dumps({"run_meta": payload["run_meta"], "n_strategies": len(payload["rows"])}, ensure_ascii=False, indent=2))
        return 0

    out_dir = Path(args.output_dir)
    write_summary_json(
        payload["rows"], out_dir / "strategy_backtest_summary.json",
        run_meta=payload["run_meta"],
    )
    write_ranking_csv(payload["rows"], out_dir / "strategy_backtest_ranking.csv")
    write_markdown_report(
        payload["rows"], out_dir / "strategy_backtest_report.md",
        run_meta=payload["run_meta"],
    )
    print(f"[OK] wrote {out_dir}/strategy_backtest_summary.json")
    print(f"[OK] wrote {out_dir}/strategy_backtest_ranking.csv")
    print(f"[OK] wrote {out_dir}/strategy_backtest_report.md")
    return 0


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())

"""ACUS 의존성 주입 — 무거운/외부 호출을 테스트에서 fake 로 대체 가능하게 분리.

기본 구현은 *기존 sanctioned 모듈만* 재사용한다:
  - bars 적재: ``app.market_data.intraday_ohlcv.load_intraday_csv`` (read-only)
  - 백테스트: ``app.backtest.strategy_council_backtest.run_strategy_council_backtest``
              (Agent Council = 기존 ``agent_council.run_agent_council`` 재사용)

broker / OrderExecutor / route_order / KIS 주문 API import·호출 0건.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable, Optional

# bars 적재: path -> (bars, meta)
LoadBarsFn = Callable[[str], "tuple[list[Any], dict[str, Any]]"]
# 백테스트: bars -> 요약 dict {profit_factor, expectancy, average_return, win_rate, max_drawdown, trades}
CouncilBacktestFn = Callable[["list[Any]"], "dict[str, Any]"]
# 뉴스 분석: (symbol, name) -> {score:int, rationale:str, category:str} | None(=UNKNOWN)
#   usage_out 는 누적 토큰/비용을 기록할 mutable dict (cost cap 추적).
NewsAnalyzeFn = Callable[[str, Optional[str], "dict[str, Any]"], "Optional[dict[str, Any]]"]


def _default_load_bars(path: str) -> "tuple[list[Any], dict[str, Any]]":
    from app.market_data.intraday_ohlcv import load_intraday_csv

    return load_intraday_csv(path)


def _default_council_backtest(bars: "list[Any]") -> "dict[str, Any]":
    """council 백테스트 → 핵심 메트릭 dict. (분석 전용, 주문 0건)"""
    from app.backtest.strategy_council_backtest import (
        BacktestInput,
        run_strategy_council_backtest,
    )

    rep = run_strategy_council_backtest(BacktestInput(bars=tuple(bars)))
    council = rep.council
    perf = council.performance if council else None
    buy = 0
    if council:
        buy = int(council.final_action_counts.get("BUY", 0) or 0)
    return {
        "profit_factor": perf.profit_factor if perf else None,
        "expectancy": perf.expectancy if perf else None,
        "average_return": perf.average_return if perf else None,
        "win_rate": perf.win_rate if perf else None,
        "max_drawdown": perf.max_drawdown if perf else None,
        "trades": buy,
        "insufficient_data": bool(rep.insufficient_data),
    }


@dataclass
class PipelineDeps:
    """파이프라인 단계가 사용하는 주입 의존성. 기본값은 실제 모듈."""

    load_bars: LoadBarsFn = _default_load_bars
    council_backtest: CouncilBacktestFn = _default_council_backtest
    analyze_news: Optional[NewsAnalyzeFn] = None  # None = 실 API 미구성 → 모두 NEWS_UNKNOWN


def default_deps() -> PipelineDeps:
    return PipelineDeps()

"""3-02 / 3-03 — 실제 OHLCV 데이터 기반 백테스트 + 파라미터 최적화 패키지.

본 패키지는 ``MockMarketData`` 가 아니라 *실제 / 준실제* OHLCV 데이터로 6개
등록 전략의 성과 / 위험 지표를 측정 + grid search 로 파라미터를 탐색한다.

서브모듈:
- ``symbols``                  대표 종목 10종 카탈로그 + yfinance ticker 변환.
- ``loader``                   CSV → yfinance fallback → 데이터 없음 graceful.
- ``verdicts`` (3-02)          4단계 — INSUFFICIENT_DATA / LOW_QUALITY /
                               HIGH_DRAWDOWN / BACKTEST_PASS.
- ``grid_search`` (3-03)       6 전략 제한된 parameter grid catalog.
- ``optimization_verdicts`` (3-03) 5단계 — INSUFFICIENT_DATA / NEGATIVE_EXPECTANCY /
                               HIGH_DRAWDOWN / LOW_QUALITY / PAPER_CANDIDATE.
- ``paper_candidate`` (3-03)   ``paper_candidate_config.json`` 생성기.

3-04 (Walk-forward) / 3-05 (Stress test) 는 *별도 PR* — 본 모듈에서 import 0건.

절대 원칙 (CLAUDE.md):
- broker / OrderExecutor / route_order import 0건 (정적 grep 가드).
- KIS 주문 API 호출 0건. yfinance 는 read-only 시세 fetch 만.
- 실거래 / Place Order 0건. 본 패키지는 *분석 read-only*.
- secret / API key / `.env` 노출 0건.
- ``ENABLE_LIVE_TRADING`` / ``ENABLE_AI_EXECUTION`` /
  ``ENABLE_FUTURES_LIVE_TRADING`` / ``KIS_IS_PAPER`` mutate 0건.
"""

from app.backtest.real_data.symbols import (
    REPRESENTATIVE_SYMBOLS,
    RepresentativeSymbol,
    representative_symbol_codes,
    yahoo_ticker,
)
from app.backtest.real_data.verdicts import (
    BacktestVerdict,
    FilterThresholds,
    classify_backtest_metrics,
)
from app.backtest.real_data.optimization_verdicts import (
    OptimizationVerdict,
    OptimizationThresholds,
    classify_optimization_run,
)
from app.backtest.real_data.grid_search import (
    PARAMETER_GRIDS,
    iter_param_grid,
    total_combinations,
)

__all__ = [
    "BacktestVerdict",
    "FilterThresholds",
    "OptimizationThresholds",
    "OptimizationVerdict",
    "PARAMETER_GRIDS",
    "REPRESENTATIVE_SYMBOLS",
    "RepresentativeSymbol",
    "classify_backtest_metrics",
    "classify_optimization_run",
    "iter_param_grid",
    "representative_symbol_codes",
    "total_combinations",
    "yahoo_ticker",
]

"""3-02 — 실제 / 준실제 OHLCV 데이터 기반 백테스트 실행 패키지.

본 패키지는 ``MockMarketData`` 가 아니라 *실제 / 준실제* OHLCV 데이터로 6개
등록 전략의 baseline 성과 / 위험 지표를 측정하는 *실행 전용* 모듈을 정의한다.

본 PR (3-02) 의 책임 범위:
- CSV (repo) → yfinance fallback → 데이터 없음 graceful 로더.
- 4단계 verdict (INSUFFICIENT_DATA / LOW_QUALITY / HIGH_DRAWDOWN /
  BACKTEST_PASS) 분류기.
- 6 전략 × N 종목 매트릭스 1회 실행 CLI.

3-03 (파라미터 최적화) / 3-04 (Walk-forward) / 3-05 (Stress test) /
3-07 (paper_candidate_config) 는 *별도 PR* — 본 모듈에서 import 0건.

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

__all__ = [
    "BacktestVerdict",
    "FilterThresholds",
    "REPRESENTATIVE_SYMBOLS",
    "RepresentativeSymbol",
    "classify_backtest_metrics",
    "representative_symbol_codes",
    "yahoo_ticker",
]

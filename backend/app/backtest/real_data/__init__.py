"""3단계 — 실제 데이터 기반 백테스트 파이프라인.

본 패키지는 *준실제 / 실제* OHLCV 데이터를 사용한 백테스트 + 검증 + 후보 추출
파이프라인을 제공한다. MockMarketData (`app.market.mock`) 는 *시스템 동작 확인*
용도로만 유지되며, 전략 수익성 판단에는 사용하지 않는다.

모듈 구성:
- ``symbols``                  대표 종목 10개 카탈로그 + 확장 후보 정책.
- ``data_source``              CSV → yfinance fallback → 데이터 없음 처리.
- ``metrics``                  13개 표준 지표 계산기 (기존 metrics.py 확장).
- ``filters``                  5단계 verdict (INSUFFICIENT_DATA, NEGATIVE_EXPECTANCY,
                                LOW_QUALITY, HIGH_DRAWDOWN, PAPER_CANDIDATE).
- ``paper_candidate``          paper_candidate_config.json 생성기.
- ``walk_forward_connector``   Walk-forward 분리 / OVERFIT_RISK 판정 stub.
- ``stress_test_connector``    6개 stress 시나리오 stub.

절대 원칙 (CLAUDE.md):
- broker / OrderExecutor / route_order / RiskManager runtime 호출 0건.
- 본 모듈은 *분석 전용* — 주문 신호 / 자동 적용 / 실거래 활성화 0건.
- 데이터 fetch 실패 시 graceful — 후보 비우고 사유 기록 (예외 0건).
- secret / API key / `.env` 노출 0건.
"""

from app.backtest.real_data.symbols import (
    REPRESENTATIVE_SYMBOLS,
    RepresentativeSymbol,
    representative_symbol_codes,
)
from app.backtest.real_data.filters import (
    BacktestVerdict,
    classify_backtest_result,
)
from app.backtest.real_data.metrics import (
    REQUIRED_METRIC_KEYS,
    compute_extended_metrics,
)

__all__ = [
    "BacktestVerdict",
    "REPRESENTATIVE_SYMBOLS",
    "REQUIRED_METRIC_KEYS",
    "RepresentativeSymbol",
    "classify_backtest_result",
    "compute_extended_metrics",
    "representative_symbol_codes",
]

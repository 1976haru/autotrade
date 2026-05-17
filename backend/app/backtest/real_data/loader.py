"""실제 OHLCV 데이터 로더 — CSV 우선 + yfinance fallback + graceful 실패.

데이터 소스 우선순위:
1. **로컬 CSV** — repo 표준 위치 (``data/ohlcv/{symbol}.csv`` 또는
   ``backend/tests/fixtures/real_data/{symbol}.csv``).
2. **yfinance fallback** — ``enable_yfinance=True`` 일 때만 시도. 네트워크 /
   rate-limit / 파싱 실패 모두 *graceful* (예외 raise 0건).
3. **데이터 없음** — `LoadResult(bars=None, status="DISABLED" | "NO_DATA")`.
   *mock 으로 silent swap 0건* — caller 가 status / reason 으로 분기.

KIS read-only 시세 API 는 *후속 옵션* — 본 PR 시점 미구현 (`docs/real_data_backtest.md`
§9 에 문서화만).

CLAUDE.md 절대 원칙:
- broker / OrderExecutor / route_order import 0건.
- KIS 주문 API import / 호출 0건.
- yfinance 는 read-only 과거 데이터 fetch 만. 주문 / 계좌 조회 0건.
- 실패 시 mock 으로 silent swap 0건 (반드시 status 로 surface).
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum
from pathlib import Path

from app.backtest.loaders import load_bars_from_csv
from app.backtest.real_data.symbols import yahoo_ticker
from app.backtest.types import Bar


_log = logging.getLogger("autotrade.real_data_loader")


class LoadStatus(StrEnum):
    """데이터 로드 결과 상태."""

    CSV_LOCAL    = "CSV_LOCAL"      # repo 내 CSV 에서 로드 성공.
    YFINANCE_OK  = "YFINANCE_OK"    # yfinance 응답 성공.
    DISABLED     = "DISABLED"       # CSV 없음 + enable_yfinance=False.
    NO_DATA      = "NO_DATA"        # CSV 없음 + yfinance 응답 빈 / 미설치.
    FETCH_FAILED = "FETCH_FAILED"   # yfinance 호출 후 네트워크 / 파싱 실패.


@dataclass(frozen=True)
class LoadResult:
    """단일 symbol 의 OHLCV 로드 결과.

    ``bars`` 가 None 이면 백테스트 skip. caller 는 ``status`` + ``reason`` 을
    리포트 / verdict 사유에 carry.
    """

    symbol:    str
    status:    LoadStatus
    bars:      list[Bar] | None = None
    reason:    str              = ""   # 사람이 읽는 사유 라벨 (secret 금지)
    source:    str              = ""   # CSV path 또는 "yfinance"
    bar_count: int              = 0

    def __post_init__(self) -> None:
        if self.bars is not None and len(self.bars) != self.bar_count:
            object.__setattr__(self, "bar_count", len(self.bars))


def _default_csv_paths(symbol: str) -> list[Path]:
    """CSV 후보 경로 — repo 표준 위치 우선."""
    # backend/app/backtest/real_data/loader.py 의 4단계 위 = repo root.
    repo_root = Path(__file__).resolve().parents[4]
    return [
        repo_root / "data" / "ohlcv" / f"{symbol}.csv",
        repo_root / "backend" / "tests" / "fixtures" / "real_data" / f"{symbol}.csv",
    ]


def _try_load_csv(
    symbol: str, *, extra_paths: list[Path] | None = None,
) -> LoadResult | None:
    """CSV 우선 시도. 성공하면 LoadResult, 실패하면 None — caller 가 fallback."""
    candidates = _default_csv_paths(symbol)
    if extra_paths:
        candidates = list(extra_paths) + candidates
    for path in candidates:
        if not path.exists():
            continue
        try:
            bars = load_bars_from_csv(path, symbol)
        except Exception as exc:  # noqa: BLE001 — CSV 파싱 실패도 surface.
            _log.warning("[real-data] CSV parse failed for %s @ %s: %s",
                         symbol, path, exc)
            return LoadResult(
                symbol=symbol,
                status=LoadStatus.FETCH_FAILED,
                reason=f"csv_parse_error: {type(exc).__name__}",
                source=str(path),
            )
        if not bars:
            continue
        return LoadResult(
            symbol=symbol,
            status=LoadStatus.CSV_LOCAL,
            bars=bars,
            source=str(path),
            bar_count=len(bars),
        )
    return None


def _try_load_yfinance(
    symbol: str, start: datetime, end: datetime,
) -> LoadResult:
    """yfinance fallback — *read-only 시세 fetch 만*. 어떤 에러도 raise 0건.

    KIS 주문 API / route_order / OrderExecutor / order-placing import 0건
    (정적 grep 가드).
    """
    try:
        # lazy import — 패키지 미설치 환경에서도 모듈 import 가능.
        from app.market.yfinance_adapter import YfinanceMarketData
        from app.market.base import Interval
    except Exception as exc:  # noqa: BLE001
        return LoadResult(
            symbol=symbol,
            status=LoadStatus.NO_DATA,
            reason=f"yfinance_adapter_unavailable: {type(exc).__name__}",
        )

    try:
        adapter = YfinanceMarketData()
        import asyncio
        try:
            loop = asyncio.get_event_loop()
            if loop.is_running():
                # 이미 running async loop 안 — caller 가 async path 사용해야 함.
                return LoadResult(
                    symbol=symbol,
                    status=LoadStatus.NO_DATA,
                    reason="yfinance_sync_call_inside_async_loop",
                )
        except RuntimeError:
            pass
        bars: list[Bar] = asyncio.run(
            adapter.get_bars(symbol, start, end, interval=Interval.DAY_1)
        )
    except Exception as exc:  # noqa: BLE001 — 네트워크 / 파싱 / rate-limit 등.
        _log.warning("[real-data] yfinance fetch failed for %s: %s", symbol, exc)
        return LoadResult(
            symbol=symbol,
            status=LoadStatus.FETCH_FAILED,
            reason=f"yfinance_error: {type(exc).__name__}",
            source="yfinance",
        )

    if not bars:
        return LoadResult(
            symbol=symbol,
            status=LoadStatus.NO_DATA,
            reason="yfinance_returned_empty",
            source="yfinance",
        )

    return LoadResult(
        symbol=symbol,
        status=LoadStatus.YFINANCE_OK,
        bars=bars,
        source=f"yfinance:{yahoo_ticker(symbol)}",
        bar_count=len(bars),
    )


def load_real_ohlcv(
    symbol: str,
    *,
    start: datetime,
    end: datetime,
    enable_yfinance: bool = False,
    extra_csv_paths: list[Path] | None = None,
) -> LoadResult:
    """단일 symbol OHLCV 로드 — CSV → yfinance → 데이터 없음.

    Args:
        symbol: 6자리 종목코드 (`yahoo_ticker` 가 변환).
        start / end: 백테스트 기간 (datetime).
        enable_yfinance: True 일 때만 외부 fetch 시도. default False
            (CI / 자동 테스트는 절대 외부 호출 X — repo CSV 만 사용).
        extra_csv_paths: 우선 검색할 CSV 경로 (테스트 fixture 주입용).

    Returns:
        LoadResult — status 로 분기. 어떤 입력으로도 예외 raise 0건.
    """
    csv_result = _try_load_csv(symbol, extra_paths=extra_csv_paths)
    if csv_result is not None:
        return csv_result

    if not enable_yfinance:
        return LoadResult(
            symbol=symbol,
            status=LoadStatus.DISABLED,
            reason="csv_missing_and_yfinance_disabled",
        )

    return _try_load_yfinance(symbol, start, end)


def summarize_load_results(results: list[LoadResult]) -> dict[str, list[str]]:
    """status 별 symbol 리스트로 집계 — 운영자 리포트용."""
    summary: dict[str, list[str]] = {}
    for r in results:
        summary.setdefault(r.status.value, []).append(r.symbol)
    return summary

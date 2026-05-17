"""실제 OHLCV 데이터 로더 — CSV 우선 + yfinance fallback + graceful 실패.

데이터 소스 우선순위:
1. **로컬 CSV** (``data/ohlcv/{symbol}.csv`` 또는 ``backend/tests/fixtures/
   real_data/{symbol}.csv``) — repo 에 commit 된 시드 데이터.
2. **yfinance fallback** — 운영자가 명시 enable_yfinance=True 일 때만 호출.
   네트워크 / API rate-limit / 파싱 실패는 *예외 raise 안 함* — None 반환 +
   사유 carry.
3. **데이터 없음** — `DataLoadResult(bars=None, status="NO_DATA", reason=...)`.

caller (백테스트 CLI) 는 status 를 보고 *해당 symbol skip + paper_candidate
config 에 "데이터 없음" 사유 기록* — *억지로 fallback / MockData 사용 0건*.

CLAUDE.md 절대 원칙:
- broker / OrderExecutor / route_order import 0건.
- yfinance import 는 함수 내부에서 lazy — 패키지 미설치 환경도 동작.
- 실패 시 mock 으로 silent swap 0건 (반드시 status 로 surface).
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import StrEnum
from pathlib import Path

from app.backtest.loaders import load_bars_from_csv
from app.backtest.types import Bar


_log = logging.getLogger("autotrade.real_data")


class DataLoadStatus(StrEnum):
    """데이터 로드 결과 상태 — verdict 와 별개 (verdict 는 백테스트 결과 분류)."""
    CSV_LOCAL    = "CSV_LOCAL"      # repo 내 CSV 에서 로드 성공.
    YFINANCE_OK  = "YFINANCE_OK"    # yfinance 응답 성공.
    NO_DATA      = "NO_DATA"        # CSV 없음 + yfinance 미사용 (또는 미설치).
    FETCH_FAILED = "FETCH_FAILED"   # yfinance 호출했지만 네트워크 / 파싱 실패.
    DISABLED     = "DISABLED"       # CSV 없음 + enable_yfinance=False.


@dataclass(frozen=True)
class DataLoadResult:
    """단일 symbol 의 OHLCV 로드 결과.

    ``bars`` 가 None 이면 백테스트 skip. caller 는 ``status`` + ``reason`` 을
    paper_candidate_config 의 사유 필드에 carry.
    """
    symbol:   str
    status:   DataLoadStatus
    bars:     list[Bar] | None  = None
    reason:   str               = ""    # 사람이 읽는 사유 라벨 (secret 금지)
    source:   str               = ""    # 로드 출처 경로 / "yfinance" / ""
    bar_count: int              = 0

    def __post_init__(self) -> None:
        # bars 가 있으면 bar_count 검증.
        if self.bars is not None and len(self.bars) != self.bar_count:
            object.__setattr__(self, "bar_count", len(self.bars))


def _default_csv_candidates(symbol: str) -> list[Path]:
    """CSV 후보 경로 — repo 표준 위치 우선."""
    # repo root 는 backend/app/backtest/real_data/data_source.py 의 4단계 위.
    repo_root = Path(__file__).resolve().parents[4]
    return [
        repo_root / "data" / "ohlcv" / f"{symbol}.csv",
        repo_root / "backend" / "tests" / "fixtures" / "real_data" / f"{symbol}.csv",
    ]


def _try_load_csv(symbol: str, *, extra_paths: list[Path] | None = None) -> DataLoadResult | None:
    """CSV 우선 시도. 성공하면 DataLoadResult, 실패하면 None."""
    candidates = _default_csv_candidates(symbol)
    if extra_paths:
        candidates = list(extra_paths) + candidates
    for path in candidates:
        if not path.exists():
            continue
        try:
            bars = load_bars_from_csv(path, symbol)
        except Exception as exc:  # noqa: BLE001 — CSV 파싱 실패는 surface.
            _log.warning(
                "[real-data] CSV parse failed for %s @ %s: %s",
                symbol, path, exc,
            )
            return DataLoadResult(
                symbol=symbol,
                status=DataLoadStatus.FETCH_FAILED,
                reason=f"csv_parse_error: {type(exc).__name__}",
                source=str(path),
            )
        if not bars:
            continue
        return DataLoadResult(
            symbol=symbol,
            status=DataLoadStatus.CSV_LOCAL,
            bars=bars,
            source=str(path),
            bar_count=len(bars),
            reason="",
        )
    return None


def _try_load_yfinance(
    symbol: str,
    start: datetime,
    end: datetime,
) -> DataLoadResult:
    """yfinance fallback — 패키지 미설치 / 네트워크 실패 모두 graceful.

    절대 raise 하지 않는다 — caller 가 status / reason 으로 분기.
    """
    try:
        # lazy import — 패키지 미설치 환경에서도 모듈 import 가능.
        from app.market.yfinance_adapter import YfinanceMarketData
        from app.market.base import Interval
    except Exception as exc:  # noqa: BLE001
        return DataLoadResult(
            symbol=symbol,
            status=DataLoadStatus.NO_DATA,
            reason=f"yfinance_adapter_unavailable: {type(exc).__name__}",
        )

    try:
        adapter = YfinanceMarketData()
        # 동기 wrapper — asyncio.run 없이 직접 호출하려면 별도 helper.
        # 현재 adapter 는 async — caller 가 async 컨텍스트에서 처리해야 함.
        # 본 함수는 *synchronous wrapper* 로 동작하기 위해 별도 task 실행.
        import asyncio

        try:
            loop = asyncio.get_event_loop()
            if loop.is_running():
                # 이미 async loop 안 — caller 가 async fetch 사용해야 함.
                return DataLoadResult(
                    symbol=symbol,
                    status=DataLoadStatus.NO_DATA,
                    reason="yfinance_sync_call_inside_async_loop",
                )
        except RuntimeError:
            # 새 loop 가 필요.
            pass

        bars: list[Bar] = asyncio.run(
            adapter.get_bars(symbol, start, end, interval=Interval.DAY_1)
        )
    except Exception as exc:  # noqa: BLE001 — 네트워크 / 파싱 등 모두 graceful.
        _log.warning(
            "[real-data] yfinance fetch failed for %s: %s",
            symbol, exc,
        )
        return DataLoadResult(
            symbol=symbol,
            status=DataLoadStatus.FETCH_FAILED,
            reason=f"yfinance_error: {type(exc).__name__}",
            source="yfinance",
        )

    if not bars:
        return DataLoadResult(
            symbol=symbol,
            status=DataLoadStatus.NO_DATA,
            reason="yfinance_returned_empty",
            source="yfinance",
        )

    return DataLoadResult(
        symbol=symbol,
        status=DataLoadStatus.YFINANCE_OK,
        bars=bars,
        source="yfinance",
        bar_count=len(bars),
    )


def load_real_ohlcv(
    symbol: str,
    *,
    start: datetime,
    end: datetime,
    enable_yfinance: bool = False,
    extra_csv_paths: list[Path] | None = None,
) -> DataLoadResult:
    """단일 symbol 의 OHLCV 로드 — CSV → yfinance → 없음.

    Args:
        symbol: 6자리 종목코드 (KOSPI/KOSDAQ).
        start / end: 백테스트 기간.
        enable_yfinance: True 일 때만 외부 fetch 시도. default False.
            (CI / 자동 테스트에서는 절대 외부 호출하지 않음 — repo CSV 만.)
        extra_csv_paths: 우선 검색할 CSV 경로 (테스트 fixture 주입용).

    Returns:
        DataLoadResult — status 로 분기. caller 가 bars 사용 여부 결정.

    절대 보장:
        - 어떤 입력으로도 예외 raise 0건 (graceful).
        - 데이터 없을 시 mock 으로 silent swap 0건.
    """
    csv_result = _try_load_csv(symbol, extra_paths=extra_csv_paths)
    if csv_result is not None:
        return csv_result

    if not enable_yfinance:
        return DataLoadResult(
            symbol=symbol,
            status=DataLoadStatus.DISABLED,
            reason="csv_missing_and_yfinance_disabled",
        )

    return _try_load_yfinance(symbol, start, end)


def summarize_load_results(results: list[DataLoadResult]) -> dict[str, list[str]]:
    """로드 결과를 status 별 symbol 리스트로 집계 — 운영자 리포트용."""
    summary: dict[str, list[str]] = {}
    for r in results:
        summary.setdefault(r.status.value, []).append(r.symbol)
    return summary

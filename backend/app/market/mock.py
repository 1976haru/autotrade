from datetime import datetime, timedelta

from app.backtest.types import Bar
from app.market.base import Interval, MarketDataAdapter


_BASE_PRICES: dict[str, int] = {
    "005930":  75_000,
    "000660": 185_000,
    "035420": 205_000,
    "035720":  61_000,
    "005380": 245_000,
}

_DEFAULT_BASE = 50_000
_ANCHOR_ORDINAL = datetime(2026, 1, 1).toordinal()


def _symbol_seed(symbol: str) -> int:
    return sum(ord(c) for c in symbol)


class MockMarketData(MarketDataAdapter):
    """결정론적 합성 OHLCV 생성기.

    같은 (symbol, timestamp)에 대해서는 항상 같은 봉을 반환한다.
    랜덤이 아니라 단순 모듈러 산식이며, 결과를 실제 시장 성과로 표현하지 않는다.
    """

    async def get_bars(
        self,
        symbol:   str,
        start:    datetime,
        end:      datetime,
        interval: Interval = Interval.DAY_1,
    ) -> list[Bar]:
        if interval != Interval.DAY_1:
            raise ValueError(f"MockMarketData only supports daily interval, got {interval}")
        if start > end:
            return []

        base = _BASE_PRICES.get(symbol, _DEFAULT_BASE)
        seed = _symbol_seed(symbol)

        cur = start.replace(hour=0, minute=0, second=0, microsecond=0)
        end_day = end.replace(hour=0, minute=0, second=0, microsecond=0)

        bars: list[Bar] = []
        while cur <= end_day:
            d = cur.toordinal() - _ANCHOR_ORDINAL
            close = max(1_000, base + ((seed + d * 7) % 1001 - 500) * 10)
            bars.append(Bar(
                symbol=symbol,
                timestamp=cur,
                open=close - 50,
                high=close + 100,
                low=close - 100,
                close=close,
                volume=10_000 + ((seed + d * 13) % 500) * 10,
            ))
            cur += timedelta(days=1)

        return bars

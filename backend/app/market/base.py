from abc import ABC, abstractmethod
from datetime import datetime
from enum import StrEnum

from app.backtest.types import Bar


class Interval(StrEnum):
    DAY_1    = "1d"
    HOUR_1   = "1h"
    MINUTE_5 = "5m"
    MINUTE_1 = "1m"


class MarketDataAdapter(ABC):
    @abstractmethod
    async def get_bars(
        self,
        symbol:   str,
        start:    datetime,
        end:      datetime,
        interval: Interval = Interval.DAY_1,
    ) -> list[Bar]:
        """[start, end] 구간의 OHLCV 봉을 timestamp 오름차순으로 반환."""

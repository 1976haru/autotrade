import asyncio
from datetime import datetime, timedelta, timezone
from typing import TYPE_CHECKING

from app.backtest.types import Bar
from app.market.base import Interval, MarketDataAdapter

if TYPE_CHECKING:
    import pandas as pd


_INTERVAL_TO_YF: dict[Interval, str] = {
    Interval.DAY_1:    "1d",
    Interval.HOUR_1:   "60m",
    Interval.MINUTE_5: "5m",
    Interval.MINUTE_1: "1m",
}

_INTERVAL_OFFSET: dict[Interval, timedelta] = {
    Interval.DAY_1:    timedelta(days=1),
    Interval.HOUR_1:   timedelta(hours=1),
    Interval.MINUTE_5: timedelta(minutes=5),
    Interval.MINUTE_1: timedelta(minutes=1),
}


class YfinanceMarketData(MarketDataAdapter):
    """Yahoo Finance를 통해 OHLCV를 가져오는 어댑터.

    한국 주식은 .KS(KOSPI) 또는 .KQ(KOSDAQ) suffix가 필요하다.
    심볼이 6자리 숫자이고 점이 없으면 default_suffix를 자동 부여한다.
    yfinance는 end를 exclusive로 처리하므로 어댑터 내부에서 +1 interval 보정한다.
    네트워크 호출은 별도 스레드에서 수행한다.
    """

    def __init__(self, default_suffix: str = ".KS"):
        self.default_suffix = default_suffix

    def _yahoo_ticker(self, symbol: str) -> str:
        if "." in symbol:
            return symbol
        if len(symbol) == 6 and symbol.isdigit():
            return f"{symbol}{self.default_suffix}"
        return symbol

    async def get_bars(
        self,
        symbol: str,
        start: datetime,
        end: datetime,
        interval: Interval = Interval.DAY_1,
    ) -> list[Bar]:
        if interval not in _INTERVAL_TO_YF:
            raise ValueError(f"unsupported interval: {interval}")
        if start > end:
            return []

        yahoo_symbol = self._yahoo_ticker(symbol)
        end_exclusive = end + _INTERVAL_OFFSET[interval]
        df = await asyncio.to_thread(
            self._fetch,
            yahoo_symbol,
            start,
            end_exclusive,
            _INTERVAL_TO_YF[interval],
        )
        return _df_to_bars(df, symbol)

    def _fetch(self, ticker: str, start: datetime, end: datetime, interval: str):
        import yfinance as yf
        return yf.Ticker(ticker).history(
            start=start,
            end=end,
            interval=interval,
            auto_adjust=False,
        )


def _df_to_bars(df: "pd.DataFrame | None", symbol: str) -> list[Bar]:
    if df is None or len(df) == 0:
        return []
    bars: list[Bar] = []
    for idx, row in df.iterrows():
        ts = idx.to_pydatetime() if hasattr(idx, "to_pydatetime") else idx
        if ts.tzinfo is None:
            ts = ts.replace(tzinfo=timezone.utc)
        else:
            ts = ts.astimezone(timezone.utc)
        bars.append(Bar(
            symbol=symbol,
            timestamp=ts,
            open=int(round(float(row["Open"]))),
            high=int(round(float(row["High"]))),
            low=int(round(float(row["Low"]))),
            close=int(round(float(row["Close"]))),
            volume=int(row["Volume"]),
        ))
    return bars

from app.backtest.strategy import Strategy
from app.backtest.types import Bar, Signal


class SmaCrossoverStrategy(Strategy):
    """단기/장기 이동평균 교차 전략.

    단기 SMA가 장기 SMA를 상향 돌파한 봉에서 BUY, 하향 돌파한 봉에서 SELL.
    워밍업 직후 첫 비교는 기준점 설정만 하므로 HOLD를 반환한다.
    """

    def __init__(self, short: int = 5, long: int = 20):
        if short < 1 or long < 1:
            raise ValueError("SMA periods must be positive")
        if short >= long:
            raise ValueError("short period must be smaller than long period")
        self.short = short
        self.long  = long
        self._prev_short_above: bool | None = None

    def on_bar(self, bars: list[Bar]) -> Signal:
        if len(bars) < self.long:
            return Signal.HOLD
        short_sma = sum(b.close for b in bars[-self.short:]) / self.short
        long_sma  = sum(b.close for b in bars[-self.long:])  / self.long
        short_above = short_sma > long_sma

        signal: Signal = Signal.HOLD
        if self._prev_short_above is not None:
            if short_above and not self._prev_short_above:
                signal = Signal.BUY
            elif not short_above and self._prev_short_above:
                signal = Signal.SELL
        self._prev_short_above = short_above
        return signal

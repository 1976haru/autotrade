from app.backtest.types import Bar, Signal
from app.strategies.base import Strategy


class SmaCrossoverStrategy(Strategy):
    """단기/장기 이동평균 교차 전략.

    단기 SMA가 장기 SMA를 상향 돌파한 봉에서 BUY, 하향 돌파한 봉에서 SELL.
    워밍업 직후 첫 비교는 기준점 설정만 하므로 HOLD를 반환한다.
    """

    # 131 contract metadata — 운영자/감사 가독.
    entry        = "단기 SMA가 장기 SMA를 상향 돌파한 봉의 마감에서 BUY 신호"
    exit         = "단기 SMA가 장기 SMA를 하향 돌파한 봉의 마감에서 SELL 신호"
    invalidation = "추세 전환(반대 cross) 또는 운영자 수동 해제"
    required_regime = "trending"  # 횡보장에서는 휘둘림(whipsaw) 위험
    risk_profile = {
        "position_size_pct": 5,   # 한 종목에 자본의 5% 노출 권장
        "stop_loss_pct":     2,   # 진입가 대비 -2% 손절 권장 (지표 자체엔 미적용)
        "max_concurrent":    1,   # 동시에 한 종목만 운용 권장
    }

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

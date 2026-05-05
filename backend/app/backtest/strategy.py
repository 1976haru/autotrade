from abc import ABC, abstractmethod

from app.backtest.types import Bar, Signal


class Strategy(ABC):
    @abstractmethod
    def on_bar(self, bars: list[Bar]) -> Signal:
        """현재 봉까지의 히스토리를 받아 다음 행동 신호를 반환."""

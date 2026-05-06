from app.backtest.types import Bar, Signal
from app.strategies.base import Strategy


class RsiReversionStrategy(Strategy):
    """RSI 평균회귀(mean-reversion) 전략.

    RSI가 과매도 임계 아래로 떨어진 직후의 첫 회복 봉에서 BUY, 과매수 임계
    위로 올라간 직후의 첫 하락 봉에서 SELL. 횡보장의 단기 반등 포착이 목적.

    RSI는 표준 정의 — `period`개의 직전 봉에서의 평균 상승폭 / 평균 하락폭으로
    계산한다 (Wilder의 지수 평활화 대신 단순 평균을 사용 — 결정적이고
    테스트 가능한 동작이 안전성에 더 유리). avg_loss=0이면 RSI=100.

    신호 트리거 (cross-back, 봉 마감 기준):
    - 직전 봉 RSI ≤ oversold AND 현재 봉 RSI > oversold → BUY
    - 직전 봉 RSI ≥ overbought AND 현재 봉 RSI < overbought → SELL
    그 외에는 HOLD. 첫 RSI 산출에 `period + 1`개의 봉이 필요하다 — 그 전까지는
    HOLD만 반환한다.
    """

    entry = (
        "RSI(period=14)가 oversold(<=30) 영역에서 임계 위로 회복되는 첫 봉에서 BUY"
    )
    exit = (
        "RSI가 overbought(>=70)에서 임계 아래로 하락하는 첫 봉에서 SELL"
    )
    invalidation = (
        "강한 추세 형성으로 RSI가 임계 영역을 5봉 이상 유지 (mean-reversion 가설 깨짐)"
    )
    required_regime = "ranging"  # 횡보 / 박스권에서 가장 안정적
    risk_profile = {
        "position_size_pct": 3,   # 추세장에서 휘말리면 손실이 큼 — 보수적
        "stop_loss_pct":     2,
        "max_concurrent":    2,
    }

    def __init__(self, period: int = 14, oversold: int = 30, overbought: int = 70):
        if period < 2:
            raise ValueError("RSI period must be >= 2")
        if not (0 < oversold < overbought < 100):
            raise ValueError("must satisfy 0 < oversold < overbought < 100")
        self.period = period
        self.oversold = oversold
        self.overbought = overbought
        self._prev_rsi: float | None = None

    def _compute_rsi(self, bars: list[Bar]) -> float:
        """가장 최근 `period`개의 close 변화로 RSI 산출. 호출 측에서 길이 보장."""
        closes = [b.close for b in bars[-(self.period + 1):]]
        gains = 0.0
        losses = 0.0
        for prev, curr in zip(closes[:-1], closes[1:]):
            diff = curr - prev
            if diff >= 0:
                gains += diff
            else:
                losses += -diff
        avg_gain = gains / self.period
        avg_loss = losses / self.period
        if avg_loss == 0:
            return 100.0
        rs = avg_gain / avg_loss
        return 100.0 - (100.0 / (1.0 + rs))

    def on_bar(self, bars: list[Bar]) -> Signal:
        if len(bars) < self.period + 1:
            return Signal.HOLD
        rsi = self._compute_rsi(bars)

        signal: Signal = Signal.HOLD
        if self._prev_rsi is not None:
            if self._prev_rsi <= self.oversold and rsi > self.oversold:
                signal = Signal.BUY
            elif self._prev_rsi >= self.overbought and rsi < self.overbought:
                signal = Signal.SELL
        self._prev_rsi = rsi
        return signal

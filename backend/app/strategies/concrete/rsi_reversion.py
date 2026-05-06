from app.backtest.types import Bar, Signal
from app.strategies.base import Strategy


class RsiReversionStrategy(Strategy):
    """RSI 평균회귀(mean-reversion) 전략.

    RSI가 과매도 임계 아래로 떨어진 직후의 첫 회복 봉에서 BUY, 과매수 임계
    위로 올라간 직후의 첫 하락 봉에서 SELL. 횡보장의 단기 반등 포착이 목적.

    NOTE (131): 현재는 stub. on_bar는 항상 HOLD를 반환하며 어떤 시그널도
    생산하지 않는다. metadata만 명시해 미래 구현 시 운영자가 검토할 contract
    를 고정.
    """

    entry = (
        "RSI(period=14)가 oversold(<=30) 영역에서 임계 위로 회복되는 첫 봉에서 BUY"
    )
    exit = (
        "RSI가 중립선(50) 회복 또는 overbought(>=70)에서 임계 아래로 하락"
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
        # TODO(131-followup): 실제 RSI 계산 + 회복 봉 감지 + 임계 cross 구현.
        if period < 2:
            raise ValueError("RSI period must be >= 2")
        if not (0 < oversold < overbought < 100):
            raise ValueError("must satisfy 0 < oversold < overbought < 100")
        self.period = period
        self.oversold = oversold
        self.overbought = overbought

    def on_bar(self, bars: list[Bar]) -> Signal:
        # TODO(131-followup): RSI 계산, oversold/overbought 추적, 회복 시 BUY/SELL.
        # 현재는 미작동 — 자동매매 안전성에 영향 X.
        return Signal.HOLD

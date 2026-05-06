from app.backtest.types import Bar, Signal
from app.strategies.base import Strategy


class OrbVwapStrategy(Strategy):
    """Opening Range Breakout + 세션 VWAP 결합 전략.

    각 거래일의 첫 `orb_bars`개 봉이 형성하는 가격 범위(ORB)를 기준선으로
    삼고, 그 이후의 봉이 ORB 상단을 *동시에* 세션 VWAP 위에서 마감하면 BUY,
    ORB 하단을 VWAP 아래에서 마감하면 SELL. 단방향 추세장에서 쓰인다.

    설계 결정:
    - ORB 윈도우는 분(min) 단위가 아닌 *bar count*로 받는다 — Strategy ABC의
      `on_bar(bars)`가 봉 단위라 분으로 표현하면 봉 간격을 가정해야 하기 때문.
      운영자는 데이터 봉 간격을 알고 있으니 직접 환산해서 넘긴다 (5분봉 6개 ≈
      30분 ORB).
    - VWAP은 *세션 누적* — 거래일이 바뀌면 reset. 일중 rolling window가 아닌
      이유는 표준 VWAP 정의가 누적이기 때문.
    - 거래일은 봉의 timestamp의 date()로 구분 (timezone-aware/naive 모두 동작).
    - 한 거래일에 진입 신호는 한 번만 (ORB 돌파는 일회성 이벤트). 같은 날의
      반대 방향 cross도 무시 — 운영자가 실거래에서 일중 재진입을 원하면 별도
      파라미터로 확장한다.
    - cross-up edge 검출 — 직전 봉이 breakout 조건을 만족했다면 신호 발생 X.
    """

    entry = (
        "당일 첫 N봉으로 형성된 ORB의 상단을 돌파하면서 동시에 세션 VWAP 위에서 "
        "마감하는 첫 봉에서 BUY"
    )
    exit = (
        "VWAP 하향 이탈 또는 ORB 하단 재진입에서 SELL (세션 종료 시 운영자 청산 권장)"
    )
    invalidation = (
        "VWAP 하향 이탈이 5봉 이상 회복 실패하거나 ORB 하단 재진입 후 추세 무효화"
    )
    required_regime = "trending_up"  # 강한 단방향 추세에서 가장 잘 작동
    risk_profile = {
        "position_size_pct": 5,
        "stop_loss_pct":     1.5,  # 단기 변동 흡수
        "max_concurrent":    2,
    }

    def __init__(self, orb_bars: int = 6):
        if orb_bars < 1:
            raise ValueError("orb_bars must be positive")
        self.orb_bars = orb_bars
        self._current_date = None
        self._orb_high: int | None = None
        self._orb_low:  int | None = None
        self._orb_count = 0
        # VWAP 분자/분모 누적. typical_price = (h+l+c)/3.
        self._vwap_pv = 0.0
        self._vwap_v  = 0
        # cross edge detection — 직전 봉의 breakout 상태 기억.
        self._prev_above = False
        self._prev_below = False
        # 한 거래일에 한 번만 진입 신호.
        self._fired_today = False

    def _reset_for_new_day(self, bar: Bar) -> None:
        self._current_date = bar.timestamp.date()
        self._orb_high = bar.high
        self._orb_low  = bar.low
        self._orb_count = 1
        self._vwap_pv = ((bar.high + bar.low + bar.close) / 3.0) * bar.volume
        self._vwap_v  = bar.volume
        self._prev_above = False
        self._prev_below = False
        self._fired_today = False

    def on_bar(self, bars: list[Bar]) -> Signal:
        if not bars:
            return Signal.HOLD
        bar = bars[-1]

        if bar.timestamp.date() != self._current_date:
            self._reset_for_new_day(bar)
            return Signal.HOLD

        # ORB phase: 같은 날의 첫 N봉이 ORB을 형성. 이 구간 동안은 신호 X,
        # high/low 누적과 VWAP 누적만 갱신.
        if self._orb_count < self.orb_bars:
            assert self._orb_high is not None and self._orb_low is not None
            self._orb_high = max(self._orb_high, bar.high)
            self._orb_low  = min(self._orb_low,  bar.low)
            self._orb_count += 1
            self._vwap_pv += ((bar.high + bar.low + bar.close) / 3.0) * bar.volume
            self._vwap_v  += bar.volume
            return Signal.HOLD

        # ORB 형성 완료 — VWAP 누적 + 돌파 판정.
        self._vwap_pv += ((bar.high + bar.low + bar.close) / 3.0) * bar.volume
        self._vwap_v  += bar.volume

        if self._vwap_v == 0:
            # 거래량 0인 세션 — 결정 불가, 안전 측 HOLD.
            return Signal.HOLD
        vwap = self._vwap_pv / self._vwap_v
        assert self._orb_high is not None and self._orb_low is not None

        above = bar.close > self._orb_high and bar.close > vwap
        below = bar.close < self._orb_low  and bar.close < vwap

        signal: Signal = Signal.HOLD
        if not self._fired_today:
            if above and not self._prev_above:
                signal = Signal.BUY
                self._fired_today = True
            elif below and not self._prev_below:
                signal = Signal.SELL
                self._fired_today = True

        self._prev_above = above
        self._prev_below = below
        return signal

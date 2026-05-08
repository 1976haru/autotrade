"""Pullback Rebreak Strategy (#30) — 2차 전략.

1차 강한 상승(impulse) 직후의 재돌파를 추격하지 않고, 거래량이 줄어드는
*눌림(pullback)* 구간을 기다렸다가 그 이후 재돌파(rebreak) 시점에만 BUY
후보를 만든다. 1차 전략 `VolumeBreakoutStrategy`(#29)가 첫 돌파를 잡는다면
본 전략은 *그 다음 안전한 진입 후보*를 노린다.

설계 의도:
- 1차 급등을 그대로 따라잡는 추격매수 위험을 줄인다.
- 거래량 fade가 동반되는 눌림은 매도 압력 소진을 의미 → 재돌파 시 추세 지속
  가능성이 상대적으로 높다.
- 패턴 인식 *과최적화 방지* — 모든 임계는 명시 파라미터로 노출, 단일 magic
  number 없음. impulse / pullback / rebreak 각각 hard-cap을 둬 한 축이 너무
  강해도 reject되도록 한다.

본 모듈은 *주문을 실행하지 않는다* (CLAUDE.md 절대 원칙 2). broker / risk /
permission / execution / governance 어떤 모듈도 import하지 않으며, 모든
`StrategySignal.is_order_intent`는 항상 `False`. 실제 주문은 route_order
단일 진입점이 RiskManager → PermissionGate → OrderExecutor 흐름으로 처리.

분기:
- BUY: impulse + pullback(volume fade) + rebreak 모두 충족, 일중 1회.
- WATCH: 구조는 형성됐으나 재돌파 전, 또는 rebreak 거래량 부족 등 부분 충족.
- NO_SIGNAL: 패턴 미형성 (bars 부족, peak/trough 식별 실패, 등).
- REJECT: 안전 가드 차단 — stale data, blocked regime, open cooldown,
  impulse 과도, pullback 과도(깊은 눌림), VWAP 격차 과도, intraday runup 과도.
  SignalAction에 REJECT enum이 없어 `action=NO_SIGNAL` + `decision_kind="REJECT"`
  로 표시 (volume_breakout과 동일 컨벤션).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

from app.backtest.types import Bar, Signal
from app.strategies.base import (
    ExitPlan,
    SignalAction,
    SignalExplanation,
    SizingHint,
    Strategy,
    StrategyContext,
    StrategySignal,
)


_KIND_BUY        = "BUY"
_KIND_WATCH      = "WATCH"
_KIND_NO_SIGNAL  = "NO_SIGNAL"
_KIND_REJECT     = "REJECT"


@dataclass(frozen=True)
class _Decision:
    """내부 평가 결과 — generate_signal/on_bar 모두가 동일 평가 함수를 거친다."""
    action:        SignalAction
    kind:          str
    reasons:       list[str] = field(default_factory=list)
    risk_notes:    list[str] = field(default_factory=list)
    indicators:    dict      = field(default_factory=dict)
    confidence:    int       = 0
    quality_score: int       = 0


@dataclass(frozen=True)
class _Pivots:
    """impulse → pullback → 현재 구조의 핵심 인덱스 + 가격."""
    impulse_low_idx:    int
    peak_idx:           int
    pullback_low_idx:   int
    impulse_low_close:  int
    peak_close:         int
    pullback_low_close: int


class PullbackRebreakStrategy(Strategy):
    """눌림목 재돌파 전략 (2차).

    구조:
        impulse_low (저점) → peak (고점, impulse 끝) → pullback_low (눌림 저점) → 현재

    1. impulse 검증: peak_close가 impulse_low_close 대비 [min_impulse_pct,
       max_impulse_pct] 범위. impulse 구간 거래량은 baseline 대비 증가.
    2. pullback 검증: pullback_low_close가 peak_close 대비 [pullback_min_pct,
       pullback_max_pct] 범위로 하락. pullback 구간 평균 volume은 impulse 구간
       평균 volume의 `pullback_volume_fade_ratio` 이하 (거래량 fade).
    3. rebreak 검증: 현재 종가가 peak_close 위 (재돌파). 현재 봉 volume이
       pullback 평균 volume의 `rebreak_volume_min_ratio` 이상 (참여 확인).
    4. 가격 위치 검증: 현재 종가 > 세션 VWAP, VWAP 격차 ≤ 임계.
    5. 운영 가드: stale data / blocked regime / open cooldown / intraday runup.

    설계 결정:
    - 모든 임계는 명시 파라미터 — 패턴 인식 over-fit 방지.
    - peak / pullback_low / impulse_low는 *현재 봉 제외* lookback 윈도우 내에서
      argmax/argmin으로 결정 (현재 봉이 peak에 포함되면 rebreak 정의 불가).
    - 거래량 비교는 `volume`이 아니라 `close * volume`(거래대금)으로 계산 —
      종목/시점 간 비교 안정화 (volume_breakout과 동일 정책).
    - VWAP은 *세션 누적*. 거래일이 바뀌면 reset.
    - 일중 1회 진입 (`_fired_today`) — 재돌파 신호가 같은 날 두 번 나오는
      케이스는 운영자가 별도 결정.
    """

    entry = (
        "1차 상승 impulse 후 거래량 감소 눌림이 형성되고 현재 봉이 impulse 고점을 "
        "재돌파하는 첫 봉에서 BUY 후보 (VWAP 위 + 추격 가드 통과)"
    )
    exit = (
        "pullback_low 이탈 또는 VWAP 하향 이탈 또는 trailing/SL/TP/시간 청산"
    )
    invalidation = (
        "깊은 눌림(pullback_max_pct 초과), 거래량 급증 하락, VWAP 이탈, "
        "stale data, blocked regime"
    )
    required_regime = "trending_up"
    risk_profile = {
        "position_size_pct": 5,    # impulse 검증 후 진입이라 VB(#29)보다 약간 공격적
        "stop_loss_pct":     2.0,  # 운영 baseline — 실제 stop은 pullback_low 기반 동적
        "take_profit_pct":   4.0,
        "trailing_pct":      1.5,
        "time_exit_bars":    30,
        "max_concurrent":    1,
    }

    DEFAULT_BLOCKED_REGIMES: tuple[str, ...] = ("trending_down", "high_vol", "blocked")
    DEFAULT_ALLOWED_REGIMES: tuple[str, ...] = ("trending_up", "news_driven", "gap_day", "any")

    def __init__(
        self,
        min_bars_required:           int   = 30,
        impulse_lookback_bars:       int   = 12,
        pullback_lookback_bars:      int   = 10,
        min_impulse_pct:             float = 1.5,
        max_impulse_pct:             float = 12.0,
        pullback_min_pct:            float = 0.3,
        pullback_max_pct:            float = 4.0,
        pullback_volume_fade_ratio:  float = 0.85,
        rebreak_volume_min_ratio:    float = 1.2,
        require_vwap_above:          bool  = True,
        max_vwap_distance_pct:       float = 4.0,
        max_intraday_runup_pct:      float = 12.0,
        open_cooldown_bars:          int   = 5,
        stop_loss_below_pullback_low_pct: float = 1.0,
        take_profit_pct:             float = 4.0,
        trailing_pct:                float = 1.5,
        time_exit_bars:              int   = 30,
        stale_max_age_seconds:       int   = 60,
        blocked_regimes:             tuple[str, ...] | None = None,
        allowed_regimes:             tuple[str, ...] | None = None,
    ):
        if min_bars_required < impulse_lookback_bars + pullback_lookback_bars + 2:
            raise ValueError(
                "min_bars_required must be ≥ impulse_lookback + pullback_lookback + 2"
            )
        if impulse_lookback_bars < 2 or pullback_lookback_bars < 2:
            raise ValueError("lookback windows must be ≥ 2")
        if not (0 < min_impulse_pct < max_impulse_pct):
            raise ValueError("0 < min_impulse_pct < max_impulse_pct required")
        if not (0 < pullback_min_pct < pullback_max_pct):
            raise ValueError("0 < pullback_min_pct < pullback_max_pct required")
        if not (0 < pullback_volume_fade_ratio < 1):
            raise ValueError("pullback_volume_fade_ratio must be in (0, 1)")
        if rebreak_volume_min_ratio <= 0:
            raise ValueError("rebreak_volume_min_ratio must be positive")
        if max_vwap_distance_pct <= 0 or max_intraday_runup_pct <= 0:
            raise ValueError("max_*_pct must be positive")
        if open_cooldown_bars < 0:
            raise ValueError("open_cooldown_bars must be ≥ 0")
        if stop_loss_below_pullback_low_pct <= 0 or take_profit_pct <= 0:
            raise ValueError("stop_loss_below_pullback_low_pct / take_profit_pct must be positive")
        if stale_max_age_seconds <= 0:
            raise ValueError("stale_max_age_seconds must be positive")

        self.min_bars_required          = min_bars_required
        self.impulse_lookback_bars      = impulse_lookback_bars
        self.pullback_lookback_bars     = pullback_lookback_bars
        self.min_impulse_pct            = float(min_impulse_pct)
        self.max_impulse_pct            = float(max_impulse_pct)
        self.pullback_min_pct           = float(pullback_min_pct)
        self.pullback_max_pct           = float(pullback_max_pct)
        self.pullback_volume_fade_ratio = float(pullback_volume_fade_ratio)
        self.rebreak_volume_min_ratio   = float(rebreak_volume_min_ratio)
        self.require_vwap_above         = require_vwap_above
        self.max_vwap_distance_pct      = float(max_vwap_distance_pct)
        self.max_intraday_runup_pct     = float(max_intraday_runup_pct)
        self.open_cooldown_bars         = open_cooldown_bars
        self.stop_loss_below_pullback_low_pct = float(stop_loss_below_pullback_low_pct)
        self.take_profit_pct            = float(take_profit_pct)
        self.trailing_pct               = float(trailing_pct)
        self.time_exit_bars             = time_exit_bars
        self.stale_max_age_seconds      = stale_max_age_seconds
        self.blocked_regimes            = tuple(blocked_regimes or self.DEFAULT_BLOCKED_REGIMES)
        self.allowed_regimes            = tuple(allowed_regimes or self.DEFAULT_ALLOWED_REGIMES)

        # session state
        self._current_date: object = None
        self._fired_today:  bool   = False

    # ---------- session helpers ----------

    def _maybe_reset_session(self, bar: Bar) -> None:
        d = bar.timestamp.date()
        if d != self._current_date:
            self._current_date = d
            self._fired_today  = False

    @staticmethod
    def _session_bars(bars: list[Bar]) -> list[Bar]:
        if not bars:
            return []
        last_date = bars[-1].timestamp.date()
        for i in range(len(bars) - 1, -1, -1):
            if bars[i].timestamp.date() != last_date:
                return bars[i + 1:]
        return list(bars)

    @staticmethod
    def _stale_seconds(bar: Bar, now: datetime | None) -> float | None:
        if now is None:
            return None
        bar_ts = bar.timestamp
        if bar_ts.tzinfo is None:
            bar_ts = bar_ts.replace(tzinfo=timezone.utc)
        if now.tzinfo is None:
            now = now.replace(tzinfo=timezone.utc)
        return (now - bar_ts).total_seconds()

    # ---------- pivot detection ----------

    def _find_pivots(self, bars: list[Bar]) -> _Pivots | None:
        """impulse_low → peak → pullback_low 3 인덱스 식별. None이면 패턴 무.

        검색 정책:
        - 마지막 bar(현재 봉)는 rebreak 후보 — peak/pullback에 포함시키지 않는다.
        - peak 검색 윈도우: 최근 (impulse_lookback + pullback_lookback) 봉 중
          현재 봉 직전까지에서 close 최대인 지점. 단 peak가 윈도우의 가장 오른쪽
          이면 pullback이 형성되지 않은 셈 → reject.
        - peak 좌측에서 impulse_lookback 만큼의 윈도우 내 close 최저가 impulse_low.
        - peak 우측의 close 최저가 pullback_low (현재 봉 제외).
        """
        n = len(bars)
        # peak는 최소 좌측 1봉(impulse_low) + 우측 1봉(pullback_low) 필요 + 현재 봉.
        if n < 4:
            return None

        # 현재 봉(n-1) 직전까지 (n-2)에서 peak 검색.
        peak_window_start = max(1, n - 1 - self.impulse_lookback_bars - self.pullback_lookback_bars)
        peak_window_end   = n - 2  # inclusive
        if peak_window_end < peak_window_start:
            return None

        peak_idx = max(range(peak_window_start, peak_window_end + 1),
                       key=lambda i: bars[i].close)
        # peak가 윈도우 가장 오른쪽이면 pullback 형성 X — 우측에 적어도 1봉 필요.
        if peak_idx >= n - 2:
            return None

        # impulse_low는 peak 좌측 impulse_lookback 윈도우의 close 최저.
        impulse_low_start = max(0, peak_idx - self.impulse_lookback_bars)
        if impulse_low_start >= peak_idx:
            return None
        impulse_low_idx = min(range(impulse_low_start, peak_idx),
                              key=lambda i: bars[i].close)

        # pullback_low는 peak 우측 ~ 현재 봉 직전의 close 최저.
        pullback_low_idx = min(range(peak_idx + 1, n - 1),
                               key=lambda i: bars[i].close)

        return _Pivots(
            impulse_low_idx    = impulse_low_idx,
            peak_idx           = peak_idx,
            pullback_low_idx   = pullback_low_idx,
            impulse_low_close  = bars[impulse_low_idx].close,
            peak_close         = bars[peak_idx].close,
            pullback_low_close = bars[pullback_low_idx].close,
        )

    # ---------- evaluation ----------

    def _evaluate(
        self,
        bars: list[Bar],
        *,
        regime:        str | None  = None,
        stale_seconds: float | None = None,
    ) -> _Decision:
        if not bars:
            return _Decision(action=SignalAction.NO_SIGNAL, kind=_KIND_NO_SIGNAL,
                             reasons=["bars 비어있음"])

        if len(bars) < self.min_bars_required:
            return _Decision(
                action=SignalAction.NO_SIGNAL, kind=_KIND_NO_SIGNAL,
                reasons=[f"bars {len(bars)} < min {self.min_bars_required}"],
                indicators={"bars": len(bars)},
            )

        cur     = bars[-1]
        session = self._session_bars(bars)
        if not session:
            return _Decision(action=SignalAction.NO_SIGNAL, kind=_KIND_NO_SIGNAL,
                             reasons=["session 없음"])
        session_open = session[0].open
        bars_in_session = len(session)

        indicators: dict[str, Any] = {
            "bars":             len(bars),
            "session_open":     session_open,
            "bars_in_session":  bars_in_session,
            "current_close":    cur.close,
            "current_volume":   cur.volume,
        }

        # 1. liquidity 가드
        if cur.volume <= 0:
            indicators["decision_kind"] = _KIND_REJECT
            return _Decision(
                action=SignalAction.NO_SIGNAL, kind=_KIND_REJECT,
                reasons=["volume == 0 — liquidity insufficient"],
                indicators=indicators,
            )

        # 2. stale
        if stale_seconds is not None and stale_seconds > self.stale_max_age_seconds:
            indicators["decision_kind"] = _KIND_REJECT
            indicators["stale_seconds"] = stale_seconds
            return _Decision(
                action=SignalAction.NO_SIGNAL, kind=_KIND_REJECT,
                reasons=[
                    f"stale data: {stale_seconds:.1f}s > "
                    f"stale_max_age_seconds={self.stale_max_age_seconds}s"
                ],
                indicators=indicators,
            )

        # 3. blocked regime
        if regime is not None and regime.lower() in {r.lower() for r in self.blocked_regimes}:
            indicators["decision_kind"] = _KIND_REJECT
            indicators["regime"] = regime
            return _Decision(
                action=SignalAction.NO_SIGNAL, kind=_KIND_REJECT,
                reasons=[f"blocked regime: {regime}"],
                indicators=indicators,
            )

        # 4. open cooldown
        if bars_in_session <= self.open_cooldown_bars:
            indicators["decision_kind"] = _KIND_REJECT
            return _Decision(
                action=SignalAction.NO_SIGNAL, kind=_KIND_REJECT,
                reasons=[
                    f"open cooldown: bars_in_session={bars_in_session} ≤ "
                    f"open_cooldown_bars={self.open_cooldown_bars}"
                ],
                indicators=indicators,
            )

        # 5. intraday runup (peak 형성 후 누적)
        runup_pct = (cur.close - session_open) / session_open * 100.0 if session_open else 0.0
        indicators["intraday_runup_pct"] = round(runup_pct, 3)
        if runup_pct > self.max_intraday_runup_pct:
            indicators["decision_kind"] = _KIND_REJECT
            return _Decision(
                action=SignalAction.NO_SIGNAL, kind=_KIND_REJECT,
                reasons=[
                    f"intraday runup {runup_pct:.2f}% > "
                    f"max_intraday_runup_pct={self.max_intraday_runup_pct}%"
                ],
                indicators=indicators,
            )

        # 5.5 일중 1회 진입 invariant — 운영/세션 가드와 함께 가장 일찍 차단
        # (이미 BUY를 발화했으면 신규 패턴 식별/계산 자체가 무의미).
        if self._fired_today:
            return _Decision(
                action=SignalAction.NO_SIGNAL, kind=_KIND_NO_SIGNAL,
                reasons=["already fired today — 일중 재진입 차단"],
                indicators=indicators,
            )

        # 6. pivots 식별
        pivots = self._find_pivots(bars)
        if pivots is None:
            return _Decision(
                action=SignalAction.NO_SIGNAL, kind=_KIND_NO_SIGNAL,
                reasons=["impulse/pullback 구조 식별 실패"],
                indicators=indicators,
            )

        indicators.update({
            "impulse_low_idx":    pivots.impulse_low_idx,
            "peak_idx":           pivots.peak_idx,
            "pullback_low_idx":   pivots.pullback_low_idx,
            "impulse_low_close":  pivots.impulse_low_close,
            "peak_close":         pivots.peak_close,
            "pullback_low_close": pivots.pullback_low_close,
        })

        # 7. impulse_pct
        if pivots.impulse_low_close <= 0:
            indicators["decision_kind"] = _KIND_REJECT
            return _Decision(
                action=SignalAction.NO_SIGNAL, kind=_KIND_REJECT,
                reasons=["impulse_low_close ≤ 0 — 가격 데이터 이상"],
                indicators=indicators,
            )
        impulse_pct = (pivots.peak_close - pivots.impulse_low_close) / pivots.impulse_low_close * 100.0
        indicators["impulse_pct"] = round(impulse_pct, 3)

        if impulse_pct < self.min_impulse_pct:
            return _Decision(
                action=SignalAction.NO_SIGNAL, kind=_KIND_NO_SIGNAL,
                reasons=[
                    f"impulse {impulse_pct:.2f}% < min_impulse_pct={self.min_impulse_pct}%"
                ],
                indicators=indicators,
            )
        if impulse_pct > self.max_impulse_pct:
            indicators["decision_kind"] = _KIND_REJECT
            return _Decision(
                action=SignalAction.NO_SIGNAL, kind=_KIND_REJECT,
                reasons=[
                    f"impulse {impulse_pct:.2f}% > max_impulse_pct={self.max_impulse_pct}% — "
                    "추격 위험"
                ],
                indicators=indicators,
            )

        # 8. pullback_pct
        if pivots.peak_close <= 0:
            indicators["decision_kind"] = _KIND_REJECT
            return _Decision(
                action=SignalAction.NO_SIGNAL, kind=_KIND_REJECT,
                reasons=["peak_close ≤ 0"],
                indicators=indicators,
            )
        pullback_pct = (pivots.peak_close - pivots.pullback_low_close) / pivots.peak_close * 100.0
        indicators["pullback_pct"] = round(pullback_pct, 3)

        if pullback_pct < self.pullback_min_pct:
            # 너무 얕은 눌림 — 패턴이 약함. WATCH로 보고 운영자에게 surface.
            return _Decision(
                action=SignalAction.WATCH, kind=_KIND_WATCH,
                reasons=[
                    f"pullback {pullback_pct:.2f}% < pullback_min_pct={self.pullback_min_pct}% — "
                    "눌림 미형성"
                ],
                indicators=indicators,
            )
        if pullback_pct > self.pullback_max_pct:
            indicators["decision_kind"] = _KIND_REJECT
            return _Decision(
                action=SignalAction.NO_SIGNAL, kind=_KIND_REJECT,
                reasons=[
                    f"pullback {pullback_pct:.2f}% > pullback_max_pct={self.pullback_max_pct}% — "
                    "깊은 눌림"
                ],
                indicators=indicators,
            )

        # 9. impulse / pullback 거래대금 비교
        impulse_segment = bars[pivots.impulse_low_idx:pivots.peak_idx + 1]
        pullback_segment = bars[pivots.peak_idx + 1:len(bars) - 1]  # 현재 봉 제외
        impulse_avg_turnover = (
            sum(b.close * b.volume for b in impulse_segment) / len(impulse_segment)
            if impulse_segment else 0.0
        )
        pullback_avg_turnover = (
            sum(b.close * b.volume for b in pullback_segment) / len(pullback_segment)
            if pullback_segment else 0.0
        )
        indicators["impulse_avg_turnover"]  = impulse_avg_turnover
        indicators["pullback_avg_turnover"] = pullback_avg_turnover

        if impulse_avg_turnover <= 0:
            indicators["decision_kind"] = _KIND_REJECT
            return _Decision(
                action=SignalAction.NO_SIGNAL, kind=_KIND_REJECT,
                reasons=["impulse 구간 turnover ≤ 0"],
                indicators=indicators,
            )

        volume_fade_ratio = pullback_avg_turnover / impulse_avg_turnover
        indicators["volume_fade_ratio"] = round(volume_fade_ratio, 4)

        if volume_fade_ratio > self.pullback_volume_fade_ratio:
            return _Decision(
                action=SignalAction.NO_SIGNAL, kind=_KIND_NO_SIGNAL,
                reasons=[
                    f"pullback turnover {volume_fade_ratio:.2f} of impulse > "
                    f"pullback_volume_fade_ratio={self.pullback_volume_fade_ratio} — "
                    "거래량 fade 미발생"
                ],
                indicators=indicators,
            )

        # 10. VWAP (세션 누적)
        vwap_pv = sum(((b.high + b.low + b.close) / 3.0) * b.volume for b in session)
        vwap_v  = sum(b.volume for b in session)
        if vwap_v <= 0:
            indicators["decision_kind"] = _KIND_REJECT
            return _Decision(
                action=SignalAction.NO_SIGNAL, kind=_KIND_REJECT,
                reasons=["session volume == 0 — VWAP 정의 불가"],
                indicators=indicators,
            )
        vwap = vwap_pv / vwap_v
        indicators["vwap"] = round(vwap, 4)
        vwap_distance_pct = (cur.close - vwap) / vwap * 100.0 if vwap else 0.0
        indicators["vwap_distance_pct"] = round(vwap_distance_pct, 3)
        above_vwap = cur.close > vwap

        # 11. VWAP 격차 가드 (rebreak 시점에 너무 멀면 추격)
        if vwap_distance_pct > self.max_vwap_distance_pct:
            indicators["decision_kind"] = _KIND_REJECT
            return _Decision(
                action=SignalAction.NO_SIGNAL, kind=_KIND_REJECT,
                reasons=[
                    f"VWAP 격차 {vwap_distance_pct:.2f}% > "
                    f"max_vwap_distance_pct={self.max_vwap_distance_pct}%"
                ],
                indicators=indicators,
            )

        # 12. pullback이 VWAP 위에서 버텼는지 (운영자/감사 surface)
        pullback_held_above_vwap = pivots.pullback_low_close > vwap
        indicators["pullback_held_above_vwap"] = pullback_held_above_vwap

        # 13. rebreak 검증 — 현재 종가가 peak_close 위
        is_rebreak = cur.close > pivots.peak_close

        # 14. rebreak 거래량 (현재 봉 turnover ≥ pullback 평균 × min_ratio)
        cur_turnover = cur.close * cur.volume
        indicators["current_turnover"] = cur_turnover
        if pullback_avg_turnover > 0:
            rebreak_volume_ratio = cur_turnover / pullback_avg_turnover
        else:
            # pullback 구간이 거래량 0인 극단 — 현재 봉 turnover로 자체 판단.
            rebreak_volume_ratio = float("inf") if cur_turnover > 0 else 0.0
        indicators["rebreak_volume_ratio"] = (
            round(rebreak_volume_ratio, 4) if rebreak_volume_ratio != float("inf") else "inf"
        )
        rebreak_volume_strong = rebreak_volume_ratio >= self.rebreak_volume_min_ratio

        # 합성 분기
        risk_notes: list[str] = []
        if vwap_distance_pct > self.max_vwap_distance_pct * 0.7:
            risk_notes.append(
                f"VWAP 격차 {vwap_distance_pct:.2f}% — 추격 위험으로 사이즈 축소 권장"
            )
        if runup_pct > self.max_intraday_runup_pct * 0.7:
            risk_notes.append(
                f"당일 누적 상승 {runup_pct:.2f}% — 사이즈 축소 권장"
            )
        if impulse_pct > self.max_impulse_pct * 0.8:
            risk_notes.append(
                f"impulse {impulse_pct:.2f}% — 강한 1차 상승 후 진입, 사이즈 축소 권장"
            )
        if not pullback_held_above_vwap:
            risk_notes.append(
                "pullback이 VWAP 아래에서 형성됨 — 지지 미확인"
            )

        if not is_rebreak:
            return _Decision(
                action=SignalAction.WATCH, kind=_KIND_WATCH,
                reasons=[
                    f"impulse {impulse_pct:.2f}% + pullback {pullback_pct:.2f}% (volume fade "
                    f"{volume_fade_ratio:.2f}) 형성, peak_close={pivots.peak_close} 재돌파 대기"
                ],
                indicators=indicators,
                risk_notes=risk_notes,
            )

        if self.require_vwap_above and not above_vwap:
            return _Decision(
                action=SignalAction.WATCH, kind=_KIND_WATCH,
                reasons=[
                    f"rebreak 발생했으나 close {cur.close} < VWAP {vwap:.2f} — VWAP 회복 대기"
                ],
                indicators=indicators,
                risk_notes=risk_notes,
            )

        if not rebreak_volume_strong:
            return _Decision(
                action=SignalAction.WATCH, kind=_KIND_WATCH,
                reasons=[
                    f"rebreak 발생했으나 거래량 {rebreak_volume_ratio:.2f}x of pullback < "
                    f"rebreak_volume_min_ratio={self.rebreak_volume_min_ratio}"
                ],
                indicators=indicators,
                risk_notes=risk_notes,
            )

        # BUY 후보
        confidence = self._confidence(impulse_pct, pullback_pct, volume_fade_ratio,
                                       rebreak_volume_ratio, vwap_distance_pct,
                                       pullback_held_above_vwap, regime)
        quality    = self._quality_score(impulse_pct, pullback_pct, volume_fade_ratio,
                                          rebreak_volume_ratio, vwap_distance_pct,
                                          pullback_held_above_vwap, regime)
        indicators["decision_kind"] = _KIND_BUY
        reasons = [
            f"1차 상승 impulse {impulse_pct:.2f}% (impulse_low={pivots.impulse_low_close} → "
            f"peak={pivots.peak_close})",
            f"눌림 {pullback_pct:.2f}% (pullback_low={pivots.pullback_low_close}), "
            f"거래량 fade {volume_fade_ratio:.2f}",
            f"현재 봉이 peak {pivots.peak_close} 재돌파 (close={cur.close}), "
            f"rebreak volume {rebreak_volume_ratio if isinstance(rebreak_volume_ratio, float) else '∞'}x of pullback",
            f"close > VWAP({vwap:.2f}), 격차 {vwap_distance_pct:.2f}%",
        ]
        return _Decision(
            action=SignalAction.BUY, kind=_KIND_BUY,
            reasons=reasons, risk_notes=risk_notes,
            indicators=indicators,
            confidence=confidence, quality_score=quality,
        )

    # ---------- 점수 ----------

    def _confidence(
        self,
        impulse_pct:        float,
        pullback_pct:       float,
        volume_fade_ratio:  float,
        rebreak_volume_ratio: float,
        vwap_distance_pct:  float,
        pullback_held_above_vwap: bool,
        regime:             str | None,
    ) -> int:
        # impulse strength: min_impulse일 때 30, 임계 중간 70, 임계의 80%에서 90, 초과는 페널티.
        mid = (self.min_impulse_pct + self.max_impulse_pct) / 2.0
        imp_term = max(0.0, 90.0 - abs(impulse_pct - mid) / max(mid - self.min_impulse_pct, 1e-9) * 60.0)
        # pullback sweet spot: 임계 범위 중간 정도가 가장 우수.
        p_mid = (self.pullback_min_pct + self.pullback_max_pct) / 2.0
        pb_term = max(0.0, 90.0 - abs(pullback_pct - p_mid) / max(p_mid - self.pullback_min_pct, 1e-9) * 60.0)
        # volume fade — 비율이 작을수록 좋음 (0.5면 강한 fade).
        fade_term = max(0.0, 100.0 * (1.0 - volume_fade_ratio / max(self.pullback_volume_fade_ratio, 1e-9)))
        # rebreak volume
        if rebreak_volume_ratio == float("inf"):
            rb_term = 100.0
        else:
            rb_term = min(100.0, rebreak_volume_ratio / max(self.rebreak_volume_min_ratio, 1e-9) * 50.0)
        # VWAP 페널티 — 멀수록 추격.
        vwap_term = max(0.0, 100.0 * (1.0 - vwap_distance_pct / max(self.max_vwap_distance_pct, 1e-9)))

        score = (
            0.25 * imp_term + 0.20 * pb_term + 0.20 * fade_term +
            0.20 * rb_term + 0.15 * vwap_term
        )
        if pullback_held_above_vwap:
            score += 5.0
        if regime and regime.lower() in {r.lower() for r in self.allowed_regimes}:
            score += 5.0
        return int(max(0.0, min(100.0, score)))

    def _quality_score(
        self,
        impulse_pct:        float,
        pullback_pct:       float,
        volume_fade_ratio:  float,
        rebreak_volume_ratio: float,
        vwap_distance_pct:  float,
        pullback_held_above_vwap: bool,
        regime:             str | None,
    ) -> int:
        # quality_score는 신호 자체의 강도 — confidence와 weight를 다르게.
        imp_term = min(100.0, impulse_pct / max(self.max_impulse_pct, 1e-9) * 100.0)
        # pullback sweet spot 동일 — 적정 깊이.
        p_mid = (self.pullback_min_pct + self.pullback_max_pct) / 2.0
        pb_term = max(0.0, 100.0 - abs(pullback_pct - p_mid) / max(p_mid - self.pullback_min_pct, 1e-9) * 80.0)
        fade_term = max(0.0, 100.0 * (1.0 - volume_fade_ratio / max(self.pullback_volume_fade_ratio, 1e-9)))
        if rebreak_volume_ratio == float("inf"):
            rb_term = 100.0
        else:
            rb_term = min(100.0, rebreak_volume_ratio / max(self.rebreak_volume_min_ratio, 1e-9) * 50.0)

        score = 0.30 * imp_term + 0.25 * pb_term + 0.25 * fade_term + 0.20 * rb_term
        if pullback_held_above_vwap:
            score = min(100.0, score + 5.0)
        if regime and regime.lower() in {r.lower() for r in self.allowed_regimes}:
            score = min(100.0, score + 5.0)
        return int(max(0.0, min(100.0, score)))

    # ---------- legacy on_bar ----------

    def on_bar(self, bars: list[Bar]) -> Signal:
        if not bars:
            return Signal.HOLD
        self._maybe_reset_session(bars[-1])
        decision = self._evaluate(bars, regime=None, stale_seconds=None)
        if decision.action == SignalAction.BUY:
            self._fired_today = True
            return Signal.BUY
        return Signal.HOLD

    # ---------- 새 인터페이스 ----------

    def generate_signal(self, context: StrategyContext) -> StrategySignal:
        bars = list(context.bars or [])
        if not bars:
            return StrategySignal(
                action=SignalAction.NO_SIGNAL, symbol=context.symbol,
                explanation=SignalExplanation(summary="bars 비어있음"),
                is_order_intent=False,
            )
        self._maybe_reset_session(bars[-1])

        extra = context.extra or {}
        stale_seconds: float | None = extra.get("data_age_seconds")
        if stale_seconds is None:
            now = extra.get("now")
            if isinstance(now, datetime):
                stale_seconds = self._stale_seconds(bars[-1], now)
        regime = context.regime or extra.get("regime")

        decision = self._evaluate(bars, regime=regime, stale_seconds=stale_seconds)

        if decision.action == SignalAction.BUY:
            self._fired_today = True

        explanation = SignalExplanation(
            summary=self._summary(decision),
            reasons=list(decision.reasons),
            confidence=decision.confidence if decision.action == SignalAction.BUY else None,
            indicators={
                **decision.indicators,
                "decision_kind":  decision.kind,
                "quality_score":  decision.quality_score,
                "confidence":     decision.confidence,
                "risk_notes":     list(decision.risk_notes),
                "strategy_name":  type(self).__name__,
            },
            required_regime=self.required_regime,
        )

        sizing_hint = (
            self.calculate_size(
                StrategySignal(action=decision.action, symbol=context.symbol),
                account_context=None,
                risk_context={
                    "confidence":         decision.confidence,
                    "vwap_distance_pct":  decision.indicators.get("vwap_distance_pct"),
                    "intraday_runup_pct": decision.indicators.get("intraday_runup_pct"),
                    "impulse_pct":        decision.indicators.get("impulse_pct"),
                    "pullback_pct":       decision.indicators.get("pullback_pct"),
                    "pullback_low_close": decision.indicators.get("pullback_low_close"),
                    "current_close":      decision.indicators.get("current_close"),
                    "risk_notes":         list(decision.risk_notes),
                },
            )
            if decision.action == SignalAction.BUY
            else None
        )

        exit_plan = (
            self.exit_rule(
                StrategySignal(action=decision.action, symbol=context.symbol),
                position_context={
                    "pullback_low_close": decision.indicators.get("pullback_low_close"),
                    "peak_close":         decision.indicators.get("peak_close"),
                    "current_close":      decision.indicators.get("current_close"),
                },
            )
            if decision.action == SignalAction.BUY
            else None
        )

        return StrategySignal(
            action=decision.action,
            symbol=context.symbol,
            sizing_hint=sizing_hint,
            exit_plan=exit_plan,
            explanation=explanation,
            is_order_intent=False,
        )

    def _summary(self, decision: _Decision) -> str:
        if decision.action == SignalAction.BUY:
            return f"PullbackRebreakStrategy → BUY (kind={decision.kind})"
        if decision.action == SignalAction.WATCH:
            return f"PullbackRebreakStrategy → WATCH (kind={decision.kind})"
        if decision.kind == _KIND_REJECT:
            return f"PullbackRebreakStrategy → REJECTED ({decision.reasons[0] if decision.reasons else ''})"
        return "PullbackRebreakStrategy → NO_SIGNAL"

    # ---------- exit_rule / calculate_size override ----------

    def exit_rule(
        self,
        signal: StrategySignal,
        *,
        position_context: dict[str, Any] | None = None,
    ) -> ExitPlan:
        """청산 계획 — pullback_low 기반 stop을 동적으로 산출.

        position_context.pullback_low_close가 주어지면 stop_loss_pct는 entry 대비
        실제 백분율로 계산. 없으면 risk_profile의 baseline 사용.
        """
        ctx = position_context or {}
        pullback_low = ctx.get("pullback_low_close")
        entry_close  = ctx.get("current_close")
        sl_pct: float = self.risk_profile.get("stop_loss_pct", 2.0)  # baseline

        if isinstance(pullback_low, (int, float)) and isinstance(entry_close, (int, float)) and entry_close > 0:
            # stop = pullback_low * (1 - stop_loss_below_pullback_low_pct/100)
            stop_price = float(pullback_low) * (1.0 - self.stop_loss_below_pullback_low_pct / 100.0)
            sl_pct = max(0.1, (float(entry_close) - stop_price) / float(entry_close) * 100.0)

        return ExitPlan(
            take_profit_pct=self.take_profit_pct,
            stop_loss_pct=round(sl_pct, 3),
            time_exit_bars=self.time_exit_bars,
            invalidation=(
                f"pullback_low 이탈 또는 VWAP 하향 이탈 또는 trailing {self.trailing_pct}% 손절 또는 "
                f"{self.time_exit_bars}봉 시간 청산"
            ),
            rule_summary=(
                f"TP {self.take_profit_pct}% / SL {sl_pct:.2f}% (pullback_low base) / "
                f"trailing {self.trailing_pct}% / {self.time_exit_bars}봉 청산"
            ),
        )

    def calculate_size(
        self,
        signal: StrategySignal,
        *,
        account_context: dict[str, Any] | None = None,
        risk_context:    dict[str, Any] | None = None,
    ) -> SizingHint:
        """confidence + 추격 위험 + 손절폭 기반 권장 사이즈."""
        base_pct = float(self.risk_profile.get("position_size_pct") or 5)
        risk_pct_default = float(self.risk_profile.get("stop_loss_pct") or 2.0)

        rc = risk_context or {}
        confidence = rc.get("confidence")
        vwap_dist  = rc.get("vwap_distance_pct")
        runup_pct  = rc.get("intraday_runup_pct")
        impulse_pct = rc.get("impulse_pct")
        pullback_low = rc.get("pullback_low_close")
        cur_close    = rc.get("current_close")
        notes = list(rc.get("risk_notes") or [])

        scale = 1.0

        # confidence 단계 축소
        if isinstance(confidence, (int, float)):
            if confidence < 40:
                scale *= 0.5
                notes.append("confidence < 40 — 사이즈 50%")
            elif confidence < 60:
                scale *= 0.7
                notes.append("confidence < 60 — 사이즈 70%")

        # 추격 위험
        if isinstance(vwap_dist, (int, float)) and vwap_dist > self.max_vwap_distance_pct * 0.7:
            scale *= 0.7
            notes.append("VWAP 격차 큼 — 추격 위험 사이즈 70%")
        if isinstance(runup_pct, (int, float)) and runup_pct > self.max_intraday_runup_pct * 0.7:
            scale *= 0.7
            notes.append("당일 누적 상승 큼 — 사이즈 70%")
        if isinstance(impulse_pct, (int, float)) and impulse_pct > self.max_impulse_pct * 0.8:
            scale *= 0.8
            notes.append("impulse 과강 — 사이즈 80%")

        # 손절폭이 넓으면(=pullback_low가 entry에서 멀면) size 축소
        risk_pct = risk_pct_default
        if (isinstance(pullback_low, (int, float)) and isinstance(cur_close, (int, float))
                and cur_close > 0 and pullback_low > 0):
            stop_price = float(pullback_low) * (1.0 - self.stop_loss_below_pullback_low_pct / 100.0)
            risk_pct = max(0.1, (float(cur_close) - stop_price) / float(cur_close) * 100.0)
            if risk_pct > risk_pct_default * 1.5:
                scale *= 0.7
                notes.append(f"손절폭 {risk_pct:.2f}% (baseline 1.5x 초과) — 사이즈 70%")

        return SizingHint(
            position_size_pct=round(base_pct * scale, 3),
            risk_pct=round(risk_pct, 3),
            reduce_only=False,
            note=" / ".join(notes) if notes else None,
        )

    def explain_signal(
        self,
        signal: StrategySignal,
        *,
        context: StrategyContext | None = None,
    ) -> SignalExplanation:
        if signal.explanation is not None:
            return signal.explanation
        return super().explain_signal(signal, context=context)

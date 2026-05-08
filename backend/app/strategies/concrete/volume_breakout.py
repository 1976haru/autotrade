"""Volume Breakout Strategy (#29).

거래대금 급증 + 최근 고점 돌파 + VWAP 상단을 동시에 충족하는 봉에서 BUY
후보를 생성하는 1차 전략. 단순/디버깅 가능한 구조이며, 급등주 추격과 데이터
오류로 인한 잘못된 진입을 막기 위한 기본 안전조건을 코드 단에서 강제한다.

본 모듈은 *주문을 실행하지 않는다* — `Strategy` ABC 계약(CLAUDE.md 절대
원칙 2)에 따라 신호와 설명만 만들고, 실제 주문은 `route_order` 단일 진입점
(RiskManager → PermissionGate → OrderExecutor)이 처리한다. broker / risk /
permission / execution 어떤 모듈도 import하지 않는다.

진입 조건 (BUY 후보):
- bars >= min_bars_required (충분한 lookback 확보)
- 현재 봉 거래대금 ≥ lookback 평균 × volume_multiplier
- 현재 종가 > 최근 breakout_lookback_bars의 종가 최고
- 현재 종가 > 세션 누적 VWAP (require_vwap_above=True 시)
- VWAP 대비 가격 격차 ≤ max_vwap_distance_pct (추격 매수 차단)
- 세션 시가 대비 등락률 ≤ max_intraday_runup_pct (당일 급등주 추격 차단)
- 세션 시작 후 open_cooldown_bars 이상 경과 (장 초반 과열 회피)
- regime이 blocked_regimes에 포함되지 않음
- 시세 timestamp의 stale 정도 ≤ stale_max_age_seconds
- 현재 봉의 volume > 0 + lookback 평균 turnover > 0 (liquidity)

분기:
- BUY: 모든 조건 충족, 일중 1회 진입
- WATCH: 부분 충족 — volume 강하지만 breakout 미발생, breakout 있지만 VWAP
  미충족 등
- NO_SIGNAL: 조건 미충족 (bars 부족, 신호 없음)
- REJECT: 안전 가드 차단 — stale / blocked regime / open cooldown / 과열 /
  과도한 VWAP 격차 / liquidity 부족. SignalAction에 REJECT enum이 없으므로
  `action=NO_SIGNAL`로 두고 `indicators["decision_kind"]="REJECT"`로 표시.
  REJECT는 운영자/감사에게 "신호 없음"이 아닌 "안전 차단"임을 명시한다.
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


# decision_kind: signal evaluation의 4분기. SignalAction과 별개로 운영자/감사가
# 안전 차단(REJECT)과 단순 무신호(NO_SIGNAL)를 구분할 수 있도록 indicators에 carry.
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


class VolumeBreakoutStrategy(Strategy):
    """거래대금 돌파 전략 (단순 / 디버깅 가능한 1차 전략).

    설계 결정:
    - VWAP은 *세션 누적* (typical price (h+l+c)/3, 거래량 가중) — 거래일이
      바뀌면 reset. ORB와 동일한 정의 (운영자/감사 가독성).
    - 거래일은 `bar.timestamp.date()`로 구분 (timezone-aware/naive 동작).
    - 거래대금(turnover) = close × volume — 호가 단위 이상의 noise 흡수.
    - lookback window는 *현재 봉 제외* (current 봉을 baseline에 포함하면
      multiplier가 자기 자신을 기준 삼아 항상 1.0 근처로 수렴).
    - 일중 진입 신호는 한 번만 (`_fired_today`) — 단타 자동매매에서 일중
      재진입은 운영자 결정.
    - 추격 가드 2종 — VWAP 격차(max_vwap_distance_pct)와 세션 시가 대비
      runup(max_intraday_runup_pct)을 각각 검사. 하나는 spike 이후 제자리
      회귀를 잡고, 다른 하나는 당일 누적 급등을 잡는다.
    - 안전 차단(REJECT)은 `action=NO_SIGNAL` + `indicators.decision_kind="REJECT"`로
      표시 — SignalAction enum 확장 없이 운영자/감사에 신호 없음 vs 차단을
      구분 가능. 본 전략의 모든 호출자는 SignalAction 기반으로 주문 의도를
      절대 만들지 않는다 (`is_order_intent=False`).
    """

    entry = (
        "거래대금이 lookback 평균의 N배 이상이면서 최근 N봉 종가 고점을 돌파하고 "
        "동시에 세션 VWAP 위에서 마감하는 첫 봉에서 BUY 후보"
    )
    exit = (
        "stop_loss_pct/take_profit_pct/trailing_pct/time_exit_bars 또는 VWAP 하향 이탈"
    )
    invalidation = (
        "stale data, blocked regime, 세션 시가 대비 과도한 runup, VWAP 대비 과도한 격차"
    )
    required_regime = "trending_up"  # TREND_UP / NEWS_DRIVEN / GAP_DAY 권장
    risk_profile = {
        "position_size_pct": 4,    # 추격 가드가 강하지만 momentum이라 보수적
        "stop_loss_pct":     2.0,
        "take_profit_pct":   4.0,
        "trailing_pct":      1.5,
        "time_exit_bars":    30,
        "max_concurrent":    1,
    }

    # 본 전략이 허용하지 않는 regime — high_vol / trending_down / blocked.
    DEFAULT_BLOCKED_REGIMES: tuple[str, ...] = ("trending_down", "high_vol", "blocked")
    # 권장 regime — context.regime이 이 안에 있으면 confidence 가산.
    DEFAULT_ALLOWED_REGIMES: tuple[str, ...] = ("trending_up", "news_driven", "gap_day", "any")

    def __init__(
        self,
        min_bars_required:       int   = 25,
        volume_lookback_bars:    int   = 20,
        volume_multiplier:       float = 2.0,
        breakout_lookback_bars:  int   = 20,
        require_vwap_above:      bool  = True,
        max_vwap_distance_pct:   float = 3.0,
        max_intraday_runup_pct:  float = 8.0,
        open_cooldown_bars:      int   = 5,
        stop_loss_pct:           float = 2.0,
        take_profit_pct:         float = 4.0,
        trailing_pct:            float = 1.5,
        time_exit_bars:          int   = 30,
        stale_max_age_seconds:   int   = 60,
        blocked_regimes:         tuple[str, ...] | None = None,
        allowed_regimes:         tuple[str, ...] | None = None,
    ):
        if min_bars_required < 2:
            raise ValueError("min_bars_required must be >= 2")
        if volume_lookback_bars < 1 or breakout_lookback_bars < 1:
            raise ValueError("lookback windows must be >= 1")
        if volume_multiplier <= 1.0:
            raise ValueError("volume_multiplier must be > 1.0")
        if max_vwap_distance_pct <= 0 or max_intraday_runup_pct <= 0:
            raise ValueError("max_*_pct must be positive")
        if open_cooldown_bars < 0:
            raise ValueError("open_cooldown_bars must be >= 0")
        if stop_loss_pct <= 0 or take_profit_pct <= 0:
            raise ValueError("stop_loss_pct / take_profit_pct must be positive")
        if stale_max_age_seconds <= 0:
            raise ValueError("stale_max_age_seconds must be positive")

        self.min_bars_required      = min_bars_required
        self.volume_lookback_bars   = volume_lookback_bars
        self.volume_multiplier      = float(volume_multiplier)
        self.breakout_lookback_bars = breakout_lookback_bars
        self.require_vwap_above     = require_vwap_above
        self.max_vwap_distance_pct  = float(max_vwap_distance_pct)
        self.max_intraday_runup_pct = float(max_intraday_runup_pct)
        self.open_cooldown_bars     = open_cooldown_bars
        self.stop_loss_pct          = float(stop_loss_pct)
        self.take_profit_pct        = float(take_profit_pct)
        self.trailing_pct           = float(trailing_pct)
        self.time_exit_bars         = time_exit_bars
        self.stale_max_age_seconds  = stale_max_age_seconds
        self.blocked_regimes        = tuple(blocked_regimes or self.DEFAULT_BLOCKED_REGIMES)
        self.allowed_regimes        = tuple(allowed_regimes or self.DEFAULT_ALLOWED_REGIMES)

        # session state — 거래일 reset.
        self._current_date: object = None
        self._fired_today:  bool   = False

    # ---------- session helpers ----------

    def _maybe_reset_session(self, bar: Bar) -> None:
        """거래일 경계에서 일별 state reset. on_bar/generate_signal 모두 호출."""
        d = bar.timestamp.date()
        if d != self._current_date:
            self._current_date = d
            self._fired_today = False

    @staticmethod
    def _session_bars(bars: list[Bar]) -> list[Bar]:
        """가장 최근 봉의 거래일과 같은 날짜의 봉만 추출 (session window)."""
        if not bars:
            return []
        last_date = bars[-1].timestamp.date()
        # 뒤에서부터 day가 바뀌는 지점을 찾는다 — 일반적으로 데이터는 시간순.
        for i in range(len(bars) - 1, -1, -1):
            if bars[i].timestamp.date() != last_date:
                return bars[i + 1:]
        return list(bars)

    @staticmethod
    def _stale_seconds(bar: Bar, now: datetime | None) -> float | None:
        """bar.timestamp와 now의 차이를 초로. now=None이면 None (legacy 백테스트)."""
        if now is None:
            return None
        bar_ts = bar.timestamp
        # naive ↔ aware 비교 보호 — 양쪽을 UTC aware로 일치.
        if bar_ts.tzinfo is None:
            bar_ts = bar_ts.replace(tzinfo=timezone.utc)
        if now.tzinfo is None:
            now = now.replace(tzinfo=timezone.utc)
        return (now - bar_ts).total_seconds()

    # ---------- 평가 ----------

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

        # 1. 충분한 데이터
        if len(bars) < self.min_bars_required:
            return _Decision(
                action=SignalAction.NO_SIGNAL, kind=_KIND_NO_SIGNAL,
                reasons=[f"bars {len(bars)} < min {self.min_bars_required}"],
                indicators={"bars": len(bars)},
            )

        cur = bars[-1]
        session = self._session_bars(bars)
        if not session:
            return _Decision(action=SignalAction.NO_SIGNAL, kind=_KIND_NO_SIGNAL,
                             reasons=["session 없음"])
        session_open  = session[0].open
        bars_in_sess  = len(session)
        indicators: dict[str, Any] = {
            "bars":               len(bars),
            "session_open":       session_open,
            "bars_in_session":    bars_in_sess,
            "current_close":      cur.close,
            "current_volume":     cur.volume,
        }

        # 2. liquidity 가드 — 현재 봉 거래량 0 (호가/체결 정보 부재)
        if cur.volume <= 0:
            indicators["decision_kind"] = _KIND_REJECT
            return _Decision(
                action=SignalAction.NO_SIGNAL, kind=_KIND_REJECT,
                reasons=["volume == 0 — liquidity insufficient"],
                indicators=indicators,
            )

        # 3. stale data
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

        # 4. blocked regime (대문자/소문자 모두 허용)
        if regime is not None and regime.lower() in {r.lower() for r in self.blocked_regimes}:
            indicators["decision_kind"] = _KIND_REJECT
            indicators["regime"] = regime
            return _Decision(
                action=SignalAction.NO_SIGNAL, kind=_KIND_REJECT,
                reasons=[f"blocked regime: {regime}"],
                indicators=indicators,
            )

        # 5. 장 초반 cooldown — 세션 시작 후 N봉 이내는 차단
        if bars_in_sess <= self.open_cooldown_bars:
            indicators["decision_kind"] = _KIND_REJECT
            return _Decision(
                action=SignalAction.NO_SIGNAL, kind=_KIND_REJECT,
                reasons=[
                    f"open cooldown: bars_in_session={bars_in_sess} ≤ "
                    f"open_cooldown_bars={self.open_cooldown_bars}"
                ],
                indicators=indicators,
            )

        # 6. session 시가 대비 intraday runup
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

        # 7. 거래대금 평균 (현재 봉 제외)
        vol_window = bars[-(self.volume_lookback_bars + 1):-1]
        if not vol_window:
            return _Decision(action=SignalAction.NO_SIGNAL, kind=_KIND_NO_SIGNAL,
                             reasons=["volume_lookback window empty"], indicators=indicators)
        avg_turnover = sum(b.close * b.volume for b in vol_window) / len(vol_window)
        cur_turnover = cur.close * cur.volume
        indicators["avg_turnover"] = avg_turnover
        indicators["current_turnover"] = cur_turnover

        if avg_turnover <= 0:
            indicators["decision_kind"] = _KIND_REJECT
            return _Decision(
                action=SignalAction.NO_SIGNAL, kind=_KIND_REJECT,
                reasons=["lookback avg_turnover ≤ 0 — liquidity baseline 부재"],
                indicators=indicators,
            )

        volume_mult = cur_turnover / avg_turnover
        indicators["volume_multiplier"] = round(volume_mult, 4)

        # 8. 고점 (현재 봉 제외)
        bo_window = bars[-(self.breakout_lookback_bars + 1):-1]
        breakout_high = max(b.close for b in bo_window) if bo_window else cur.close
        indicators["breakout_high"] = breakout_high
        is_breakout = cur.close > breakout_high

        # 9. session VWAP
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

        # 10. VWAP 추격 가드 — 너무 멀면 차단
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

        # 11. 일중 1회 진입 invariant
        if self._fired_today:
            return _Decision(
                action=SignalAction.NO_SIGNAL, kind=_KIND_NO_SIGNAL,
                reasons=["already fired today — 일중 재진입 차단"],
                indicators=indicators,
            )

        # 12. 신호 합성
        volume_strong = volume_mult >= self.volume_multiplier
        risk_notes: list[str] = []

        # warning thresholds — BUY로 가더라도 sizing 축소 권고.
        if vwap_distance_pct > self.max_vwap_distance_pct * 0.7:
            risk_notes.append(
                f"VWAP 격차 {vwap_distance_pct:.2f}% — 추격 위험으로 사이즈 축소 권장"
            )
        if runup_pct > self.max_intraday_runup_pct * 0.7:
            risk_notes.append(
                f"당일 누적 상승 {runup_pct:.2f}% — 사이즈 축소 권장"
            )

        # 합성 분기:
        if not volume_strong and not is_breakout:
            return _Decision(
                action=SignalAction.NO_SIGNAL, kind=_KIND_NO_SIGNAL,
                reasons=[
                    f"volume {volume_mult:.2f}x < {self.volume_multiplier:.2f}x + 고점 미돌파"
                ],
                indicators=indicators,
            )
        if volume_strong and not is_breakout:
            return _Decision(
                action=SignalAction.WATCH, kind=_KIND_WATCH,
                reasons=[
                    f"거래대금 {volume_mult:.2f}x 증가했으나 고점 {breakout_high} 미돌파"
                ],
                indicators=indicators,
            )
        if is_breakout and not volume_strong:
            return _Decision(
                action=SignalAction.WATCH, kind=_KIND_WATCH,
                reasons=[
                    f"고점 돌파했으나 거래대금 {volume_mult:.2f}x < "
                    f"{self.volume_multiplier:.2f}x"
                ],
                indicators=indicators,
            )
        if self.require_vwap_above and not above_vwap:
            return _Decision(
                action=SignalAction.WATCH, kind=_KIND_WATCH,
                reasons=[
                    f"거래대금 + 고점 돌파 충족했으나 close < VWAP({vwap:.2f}) — "
                    "VWAP 회복 대기"
                ],
                indicators=indicators,
            )

        # BUY 후보 — quality_score / confidence 산출
        confidence = self._confidence(volume_mult, vwap_distance_pct, runup_pct, regime)
        quality    = self._quality_score(volume_mult, breakout_high, cur.close,
                                         vwap_distance_pct, regime)
        indicators["decision_kind"] = _KIND_BUY
        reasons = [
            f"거래대금 lookback 평균 대비 {volume_mult:.2f}x 증가",
            f"최근 {self.breakout_lookback_bars}봉 종가 고점 {breakout_high} 돌파 (close={cur.close})",
            f"close > VWAP({vwap:.2f}), 격차 {vwap_distance_pct:.2f}%",
        ]
        return _Decision(
            action=SignalAction.BUY, kind=_KIND_BUY,
            reasons=reasons, risk_notes=risk_notes,
            indicators=indicators,
            confidence=confidence, quality_score=quality,
        )

    # ---------- 점수 산출 ----------

    def _confidence(
        self,
        volume_mult:       float,
        vwap_distance_pct: float,
        runup_pct:         float,
        regime:            str | None,
    ) -> int:
        """0~100. volume strength + VWAP 가격 정렬 + regime 일치를 종합."""
        # volume term: multiplier가 threshold일 때 50점, 2*threshold면 80점, 더 크면 capped.
        excess = max(0.0, volume_mult - self.volume_multiplier)
        vol_term = 50.0 + min(40.0, excess / max(self.volume_multiplier, 1e-9) * 40.0)
        # VWAP 격차 페널티 — 멀수록 추격 위험.
        vwap_term = max(0.0, 30.0 * (1.0 - vwap_distance_pct / max(self.max_vwap_distance_pct, 1e-9)))
        # runup 페널티 — 누적 상승이 클수록 진입 위험.
        runup_term = max(0.0, 20.0 * (1.0 - runup_pct / max(self.max_intraday_runup_pct, 1e-9)))
        score = 0.45 * vol_term + 0.30 * vwap_term + 0.15 * runup_term
        if regime and regime.lower() in {r.lower() for r in self.allowed_regimes}:
            score += 10.0
        return int(max(0.0, min(100.0, score)))

    def _quality_score(
        self,
        volume_mult:       float,
        breakout_high:     float,
        close:             float,
        vwap_distance_pct: float,
        regime:            str | None,
    ) -> int:
        """0~100. 신호 자체의 강도 — breakout depth + volume strength + VWAP 정렬."""
        bo_pct = (close - breakout_high) / breakout_high * 100.0 if breakout_high else 0.0
        # breakout depth: 0.5% → 25, 2% → 100 (cap).
        bo_term = max(0.0, min(100.0, bo_pct * 50.0))
        vol_term = max(0.0, min(100.0, (volume_mult / max(self.volume_multiplier, 1e-9)) * 50.0))
        # vwap 정렬 — close > VWAP이지만 너무 멀지는 않은 sweet spot.
        ratio = vwap_distance_pct / max(self.max_vwap_distance_pct, 1e-9)
        vwap_term = max(0.0, 100.0 - abs(ratio - 0.3) * 100.0)
        score = 0.4 * bo_term + 0.4 * vol_term + 0.2 * vwap_term
        if regime and regime.lower() in {r.lower() for r in self.allowed_regimes}:
            score = min(100.0, score + 5.0)
        return int(max(0.0, min(100.0, score)))

    # ---------- legacy on_bar ----------

    def on_bar(self, bars: list[Bar]) -> Signal:
        """legacy 인터페이스 — 새 generate_signal과 동일 평가 로직을 거쳐 BUY/HOLD."""
        if not bars:
            return Signal.HOLD
        self._maybe_reset_session(bars[-1])
        decision = self._evaluate(bars, regime=None, stale_seconds=None)
        if decision.action == SignalAction.BUY:
            self._fired_today = True
            return Signal.BUY
        # 전략은 BUY only — SELL은 exit_rule(stop/tp/trailing)이 담당.
        # WATCH / NO_SIGNAL → HOLD.
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

        # context.extra에서 stale 정도 / now timestamp / regime override 추출.
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
                    "confidence":        decision.confidence,
                    "vwap_distance_pct": decision.indicators.get("vwap_distance_pct"),
                    "intraday_runup_pct": decision.indicators.get("intraday_runup_pct"),
                    "risk_notes":        list(decision.risk_notes),
                },
            )
            if decision.action == SignalAction.BUY
            else None
        )

        exit_plan = (
            self.exit_rule(
                StrategySignal(action=decision.action, symbol=context.symbol),
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
            return f"VolumeBreakoutStrategy → BUY (kind={decision.kind})"
        if decision.action == SignalAction.WATCH:
            return f"VolumeBreakoutStrategy → WATCH (kind={decision.kind})"
        if decision.kind == _KIND_REJECT:
            return f"VolumeBreakoutStrategy → REJECTED ({decision.reasons[0] if decision.reasons else ''})"
        return "VolumeBreakoutStrategy → NO_SIGNAL"

    # ---------- exit_rule / calculate_size override ----------

    def exit_rule(
        self,
        signal: StrategySignal,
        *,
        position_context: dict[str, Any] | None = None,
    ) -> ExitPlan:
        """trailing / time exit / VWAP 이탈 invalidation을 모두 담는다."""
        return ExitPlan(
            take_profit_pct=self.take_profit_pct,
            stop_loss_pct=self.stop_loss_pct,
            time_exit_bars=self.time_exit_bars,
            invalidation=(
                f"VWAP 하향 이탈 또는 trailing {self.trailing_pct}% 손절 또는 "
                f"{self.time_exit_bars}봉 시간 청산"
            ),
            rule_summary=(
                f"TP {self.take_profit_pct}% / SL {self.stop_loss_pct}% / "
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
        """confidence + 추격 위험 기반 권장 사이즈. *수량 확정이 아니라 hint*.

        최종 수량은 RiskManager / PositionSizingAgent가 결정한다 — 본 메서드는
        position_size_pct (권장 자본 비율)와 risk_pct (stop loss 기반 위험 한도)
        만 채워 반환한다.
        """
        base_pct = float(self.risk_profile.get("position_size_pct") or 4)
        risk_pct_default = float(self.risk_profile.get("stop_loss_pct") or 2.0)

        rc = risk_context or {}
        confidence = rc.get("confidence")
        vwap_dist = rc.get("vwap_distance_pct")
        runup_pct = rc.get("intraday_runup_pct")
        notes = list(rc.get("risk_notes") or [])

        # confidence가 낮으면 size 축소 (60 미만 → 70%, 40 미만 → 50%).
        scale = 1.0
        if isinstance(confidence, int) or isinstance(confidence, float):
            if confidence < 40:
                scale = 0.5
                notes.append("confidence < 40 — 사이즈 50%")
            elif confidence < 60:
                scale = 0.7
                notes.append("confidence < 60 — 사이즈 70%")

        # 추격 위험: VWAP 격차 또는 runup이 임계의 70% 초과면 추가 축소.
        if isinstance(vwap_dist, (int, float)) and vwap_dist > self.max_vwap_distance_pct * 0.7:
            scale *= 0.7
            notes.append("VWAP 격차 큼 — 추격 위험 사이즈 70%")
        if isinstance(runup_pct, (int, float)) and runup_pct > self.max_intraday_runup_pct * 0.7:
            scale *= 0.7
            notes.append("당일 누적 상승 큼 — 사이즈 70%")

        return SizingHint(
            position_size_pct=round(base_pct * scale, 3),
            risk_pct=risk_pct_default,
            reduce_only=False,
            note=" / ".join(notes) if notes else None,
        )

    def explain_signal(
        self,
        signal: StrategySignal,
        *,
        context: StrategyContext | None = None,
    ) -> SignalExplanation:
        """signal에 explanation이 이미 있으면 그걸 그대로, 없으면 metadata 기반."""
        if signal.explanation is not None:
            return signal.explanation
        return super().explain_signal(signal, context=context)

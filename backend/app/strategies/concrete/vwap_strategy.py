"""VWAP Strategy (#31) — 보조 전략 (VWAP 회귀/이탈).

세션 VWAP을 *기준선*으로 삼고, VWAP 아래에서 위로 reclaim하는 봉이나 VWAP
근처 pullback support 봉을 BUY 후보로, VWAP 아래로 이탈하는 봉을 EXIT 후보
(보유 시)로 surface한다.

본 전략은 *주문을 실행하지 않는다* (CLAUDE.md 절대 원칙 2). broker / risk /
permission / execution / governance 모듈 어떤 것도 import하지 않으며 모든
StrategySignal은 `is_order_intent=False`. 실제 주문은 `route_order`가
RiskManager → PermissionGate → OrderExecutor 흐름으로 처리.

기존 `OrbVwapStrategy`(orb_vwap.py)는 그대로 유지 — ORB + VWAP 결합 전략과는
별도의 *독립 보조 전략*. 본 전략은 ORB 윈도우를 형성하지 않고, 세션 시작
직후의 cooldown만 둔다.

VWAP 계산은 `app.strategies.vwap` 유틸을 사용 — `session_vwap` /
`rolling_vwap` / `vwap_deviation_pct` / `check_liquidity`. 거래량 적은
종목에서 소수 체결로 VWAP이 왜곡되는 케이스는 LOW_LIQUIDITY REJECT.

분기:
- BUY: VWAP reclaim (이전 봉 ≤ VWAP, 현재 봉 > VWAP) + 거래량 증가 + 거래량/
  거래대금 충분 + 괴리율 entry cap 이내 + 운영 가드 통과 + 일중 1회.
- EXIT: 보유 중(외부 컨텍스트로 추정) + 현재 봉이 VWAP 아래로 이탈 — 운영자/
  Agent가 청산 결정 시 활용.
- WATCH: VWAP 근처에 있지만 reclaim 전, 거래량 충분하나 가격 확인 부족, VWAP
  위지만 거래량 부족 등 부분 충족.
- NO_SIGNAL: 패턴 미형성.
- REJECT: 안전 가드 차단 — LOW_LIQUIDITY / 과도한 VWAP 이격(추격) /
  open_cooldown / stale data / blocked regime. SignalAction에 REJECT enum이
  없어 `action=NO_SIGNAL` + `decision_kind="REJECT"`로 표시.
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
from app.strategies.vwap import (
    check_liquidity,
    extract_session_bars,
    rolling_vwap,
    session_vwap,
    vwap_deviation_pct,
)


_KIND_BUY        = "BUY"
_KIND_EXIT       = "EXIT"
_KIND_WATCH      = "WATCH"
_KIND_NO_SIGNAL  = "NO_SIGNAL"
_KIND_REJECT     = "REJECT"


@dataclass(frozen=True)
class _Decision:
    action:        SignalAction
    kind:          str
    reasons:       list[str] = field(default_factory=list)
    risk_notes:    list[str] = field(default_factory=list)
    indicators:    dict      = field(default_factory=dict)
    confidence:    int       = 0
    quality_score: int       = 0


class VWAPStrategy(Strategy):
    """VWAP 회귀/이탈 보조 전략.

    설계 결정:
    - 세션 VWAP을 1차 기준선으로 사용 (`session_vwap`).
    - rolling VWAP은 보조 — 단기 deviation 비교용 (`rolling_vwap_window`,
      기본 20봉). 둘 다 indicators에 carry하여 운영자/감사 surface.
    - reclaim 검출은 *cross-up edge* — 이전 봉의 VWAP 대비 위치를 기억해 매
      봉마다 reset. cross-up이 발생한 첫 봉에서만 BUY 후보.
    - 거래량/거래대금 평균 임계는 모두 명시 파라미터 — 거래량 적은 종목에서의
      VWAP 왜곡 가드. 임계 미만이면 LOW_LIQUIDITY REJECT.
    - 일중 1회 진입(`_fired_today`) — 같은 세션의 두 번째 reclaim은 운영자
      결정.
    - VWAP 이탈(close < VWAP, 직전 봉 ≥ VWAP)은 EXIT 후보로 surface — 보유
      여부는 외부(position_context)로 추정. 보유 중이 아니면 NO_SIGNAL로
      처리한다(EXIT 없이).
    """

    entry = (
        "세션 VWAP 위로 reclaim하는 첫 봉(이전 봉 ≤ VWAP, 현재 봉 > VWAP) + "
        "거래량 증가 + 거래량/거래대금 임계 충족 + 괴리율 entry cap 이내"
    )
    exit = (
        "현재 봉이 VWAP 아래로 이탈 (close < VWAP, 직전 봉 ≥ VWAP) 또는 "
        "stop_loss_pct/take_profit_pct/trailing/time_exit 도달"
    )
    invalidation = (
        "VWAP 하향 이탈 후 5봉 이상 회복 실패, LOW_LIQUIDITY, blocked regime, "
        "stale data, 과도한 VWAP 이격"
    )
    required_regime = "trending_up"  # ranging/trending_up 모두 사용 가능 (보조)
    risk_profile = {
        "position_size_pct": 3,    # 보조 전략 — 보수적
        "stop_loss_pct":     1.5,
        "take_profit_pct":   2.5,
        "trailing_pct":      1.0,
        "time_exit_bars":    20,
        "max_concurrent":    1,
    }

    DEFAULT_BLOCKED_REGIMES: tuple[str, ...] = ("trending_down", "high_vol", "blocked")
    DEFAULT_ALLOWED_REGIMES: tuple[str, ...] = ("trending_up", "ranging", "any")

    def __init__(
        self,
        min_bars_required:           int   = 25,
        rolling_vwap_window:         int   = 20,
        liquidity_window:            int   = 20,
        min_avg_volume:              float = 100.0,
        min_avg_turnover:            float = 0.0,
        max_deviation_pct_for_entry: float = 1.5,
        overextension_deviation_pct: float = 3.0,
        reclaim_volume_min_ratio:    float = 1.2,
        require_volume_increase_on_reclaim: bool = True,
        open_cooldown_bars:          int   = 5,
        stop_loss_pct:               float = 1.5,
        take_profit_pct:             float = 2.5,
        trailing_pct:                float = 1.0,
        time_exit_bars:              int   = 20,
        stale_max_age_seconds:       int   = 60,
        blocked_regimes:             tuple[str, ...] | None = None,
        allowed_regimes:             tuple[str, ...] | None = None,
    ):
        if min_bars_required < 3:
            raise ValueError("min_bars_required must be ≥ 3")
        if rolling_vwap_window < 2 or liquidity_window < 2:
            raise ValueError("windows must be ≥ 2")
        if max_deviation_pct_for_entry <= 0:
            raise ValueError("max_deviation_pct_for_entry must be positive")
        if overextension_deviation_pct <= max_deviation_pct_for_entry:
            raise ValueError(
                "overextension_deviation_pct must be > max_deviation_pct_for_entry"
            )
        if reclaim_volume_min_ratio <= 0:
            raise ValueError("reclaim_volume_min_ratio must be positive")
        if open_cooldown_bars < 0:
            raise ValueError("open_cooldown_bars must be ≥ 0")
        if stop_loss_pct <= 0 or take_profit_pct <= 0:
            raise ValueError("stop_loss_pct / take_profit_pct must be positive")
        if stale_max_age_seconds <= 0:
            raise ValueError("stale_max_age_seconds must be positive")
        if min_avg_volume < 0 or min_avg_turnover < 0:
            raise ValueError("min_avg_volume / min_avg_turnover must be ≥ 0")

        self.min_bars_required           = min_bars_required
        self.rolling_vwap_window         = rolling_vwap_window
        self.liquidity_window            = liquidity_window
        self.min_avg_volume              = float(min_avg_volume)
        self.min_avg_turnover            = float(min_avg_turnover)
        self.max_deviation_pct_for_entry = float(max_deviation_pct_for_entry)
        self.overextension_deviation_pct = float(overextension_deviation_pct)
        self.reclaim_volume_min_ratio    = float(reclaim_volume_min_ratio)
        self.require_volume_increase_on_reclaim = require_volume_increase_on_reclaim
        self.open_cooldown_bars          = open_cooldown_bars
        self.stop_loss_pct               = float(stop_loss_pct)
        self.take_profit_pct             = float(take_profit_pct)
        self.trailing_pct                = float(trailing_pct)
        self.time_exit_bars              = time_exit_bars
        self.stale_max_age_seconds       = stale_max_age_seconds
        self.blocked_regimes = tuple(blocked_regimes or self.DEFAULT_BLOCKED_REGIMES)
        self.allowed_regimes = tuple(allowed_regimes or self.DEFAULT_ALLOWED_REGIMES)

        # session state — cross-up edge detection을 위한 직전 봉의 VWAP 대비 위치.
        self._current_date:    object       = None
        self._prev_above_vwap: bool | None  = None
        self._fired_today:     bool         = False

    # ---------- session helpers ----------

    def _maybe_reset_session(self, bar: Bar) -> None:
        d = bar.timestamp.date()
        if d != self._current_date:
            self._current_date    = d
            self._prev_above_vwap = None
            self._fired_today     = False

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

    # ---------- evaluation ----------

    def _evaluate(
        self,
        bars: list[Bar],
        *,
        regime:           str | None  = None,
        stale_seconds:    float | None = None,
        position_context: dict[str, Any] | None = None,
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
        session = extract_session_bars(bars)
        if not session:
            return _Decision(action=SignalAction.NO_SIGNAL, kind=_KIND_NO_SIGNAL,
                             reasons=["session 없음"])
        bars_in_session = len(session)
        session_open    = session[0].open
        indicators: dict[str, Any] = {
            "bars":             len(bars),
            "session_open":     session_open,
            "bars_in_session":  bars_in_session,
            "current_close":    cur.close,
            "current_volume":   cur.volume,
        }

        # 1. liquidity 가드 (현재 봉 거래량 0)
        if cur.volume <= 0:
            indicators["decision_kind"] = _KIND_REJECT
            return _Decision(
                action=SignalAction.NO_SIGNAL, kind=_KIND_REJECT,
                reasons=["volume == 0 — liquidity insufficient"],
                indicators=indicators,
            )

        # 2. stale data
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

        # 5. liquidity (rolling) — 거래량 적은 종목 VWAP 왜곡 가드
        liq = check_liquidity(
            bars,
            window=self.liquidity_window,
            min_avg_volume=self.min_avg_volume,
            min_avg_turnover=self.min_avg_turnover,
        )
        indicators["avg_volume"]   = liq.avg_volume
        indicators["avg_turnover"] = liq.avg_turnover
        if not liq.ok:
            indicators["decision_kind"] = _KIND_REJECT
            return _Decision(
                action=SignalAction.NO_SIGNAL, kind=_KIND_REJECT,
                reasons=[liq.reason or "LOW_LIQUIDITY"],
                indicators=indicators,
            )

        # 6. VWAP 계산 (세션 누적 + rolling)
        s_vwap = session_vwap(bars)
        r_vwap = rolling_vwap(bars, self.rolling_vwap_window)
        if s_vwap is None:
            indicators["decision_kind"] = _KIND_REJECT
            return _Decision(
                action=SignalAction.NO_SIGNAL, kind=_KIND_REJECT,
                reasons=["session VWAP 정의 불가 (volume == 0)"],
                indicators=indicators,
            )
        indicators["session_vwap"] = round(s_vwap, 4)
        if r_vwap is not None:
            indicators["rolling_vwap"] = round(r_vwap, 4)

        # 7. 괴리율
        deviation = vwap_deviation_pct(cur.close, s_vwap)
        if deviation is None:
            indicators["decision_kind"] = _KIND_REJECT
            return _Decision(
                action=SignalAction.NO_SIGNAL, kind=_KIND_REJECT,
                reasons=["vwap_deviation 계산 불가"],
                indicators=indicators,
            )
        indicators["vwap_deviation_pct"] = round(deviation, 4)

        # 8. 과도한 이격 (추격 차단)
        if deviation > self.overextension_deviation_pct:
            indicators["decision_kind"] = _KIND_REJECT
            return _Decision(
                action=SignalAction.NO_SIGNAL, kind=_KIND_REJECT,
                reasons=[
                    f"VWAP 괴리율 {deviation:.2f}% > "
                    f"overextension_deviation_pct={self.overextension_deviation_pct}% — "
                    "추격 위험"
                ],
                indicators=indicators,
            )

        # 9. EXIT 후보 — 직전 봉 ≥ VWAP, 현재 봉 < VWAP. 보유 중일 때만 EXIT 발화.
        prev_above = self._prev_above_vwap
        above_vwap = cur.close > s_vwap
        below_vwap = cur.close < s_vwap

        position_open = bool((position_context or {}).get("has_open_position"))

        # cross-down (loss) detection
        if prev_above is True and below_vwap:
            if position_open:
                indicators["decision_kind"] = _KIND_EXIT
                return _Decision(
                    action=SignalAction.EXIT, kind=_KIND_EXIT,
                    reasons=[
                        f"VWAP 하향 이탈 — 직전 봉 ≥ VWAP, 현재 close {cur.close} < VWAP {s_vwap:.2f}"
                    ],
                    indicators=indicators,
                )
            # 보유 중이 아니면 EXIT 의미 없음 — 관찰만.

        # 10. fired_today invariant
        if self._fired_today:
            return _Decision(
                action=SignalAction.NO_SIGNAL, kind=_KIND_NO_SIGNAL,
                reasons=["already fired today — 일중 재진입 차단"],
                indicators=indicators,
            )

        # 11. reclaim 검출 — 직전 봉 ≤ VWAP, 현재 봉 > VWAP
        is_reclaim = (prev_above is False or prev_above is None) and above_vwap
        # 직전 봉이 VWAP 위였으면 cross-up edge 아님.
        if prev_above is True and above_vwap:
            is_reclaim = False

        # rolling vs session 차이가 큰지 (단기 spike 식별)
        if r_vwap is not None and r_vwap > 0:
            rolling_dev = (cur.close - r_vwap) / r_vwap * 100.0
            indicators["rolling_vwap_deviation_pct"] = round(rolling_dev, 4)

        # 거래량 증가 (reclaim 기준)
        liq_window_bars = bars[-(self.liquidity_window + 1):-1]  # 현재 봉 제외 직전 N봉
        prior_avg_volume = (
            sum(b.volume for b in liq_window_bars) / len(liq_window_bars)
            if liq_window_bars else 0.0
        )
        if prior_avg_volume > 0:
            volume_ratio = cur.volume / prior_avg_volume
        else:
            volume_ratio = float("inf") if cur.volume > 0 else 0.0
        indicators["volume_ratio"] = (
            round(volume_ratio, 4) if volume_ratio != float("inf") else "inf"
        )

        # 12. 합성 분기
        risk_notes: list[str] = []
        if deviation > self.max_deviation_pct_for_entry:
            risk_notes.append(
                f"VWAP 괴리율 {deviation:.2f}% > entry cap "
                f"{self.max_deviation_pct_for_entry}% — 추격 위험"
            )
        if deviation > self.overextension_deviation_pct * 0.7:
            risk_notes.append(
                f"VWAP 괴리율 {deviation:.2f}% — 과열 임계 근접, 사이즈 축소 권장"
            )

        # entry cap 초과면 reclaim이라도 BUY 안 함 (NO_SIGNAL with risk_notes)
        if is_reclaim and deviation > self.max_deviation_pct_for_entry:
            return _Decision(
                action=SignalAction.NO_SIGNAL, kind=_KIND_NO_SIGNAL,
                reasons=[
                    f"reclaim 발생했으나 VWAP 괴리율 {deviation:.2f}% > "
                    f"max_deviation_pct_for_entry={self.max_deviation_pct_for_entry}% — 추격 보류"
                ],
                indicators=indicators, risk_notes=risk_notes,
            )

        if is_reclaim:
            volume_strong = volume_ratio >= self.reclaim_volume_min_ratio
            if self.require_volume_increase_on_reclaim and not volume_strong:
                return _Decision(
                    action=SignalAction.WATCH, kind=_KIND_WATCH,
                    reasons=[
                        f"reclaim 발생했으나 거래량 {volume_ratio if isinstance(volume_ratio, float) else '∞'}x "
                        f"< reclaim_volume_min_ratio={self.reclaim_volume_min_ratio}"
                    ],
                    indicators=indicators, risk_notes=risk_notes,
                )

            confidence = self._confidence(deviation, volume_ratio, regime,
                                          rolling_alignment=indicators.get("rolling_vwap_deviation_pct"))
            quality    = self._quality_score(deviation, volume_ratio, regime,
                                              rolling_alignment=indicators.get("rolling_vwap_deviation_pct"))
            indicators["decision_kind"] = _KIND_BUY
            reasons = [
                f"세션 VWAP {s_vwap:.2f} 회복 — 직전 봉 ≤ VWAP, 현재 close {cur.close} > VWAP",
                f"거래량 {volume_ratio if isinstance(volume_ratio, float) else '∞'}x of prior {self.liquidity_window}봉 평균",
                f"VWAP 괴리율 {deviation:.2f}% (entry cap {self.max_deviation_pct_for_entry}%)",
            ]
            return _Decision(
                action=SignalAction.BUY, kind=_KIND_BUY,
                reasons=reasons, risk_notes=risk_notes,
                indicators=indicators,
                confidence=confidence, quality_score=quality,
            )

        # 13. WATCH 분기 — VWAP 근처 / 거래량 부족 / 가격 미확인
        if above_vwap and volume_ratio < self.reclaim_volume_min_ratio:
            return _Decision(
                action=SignalAction.WATCH, kind=_KIND_WATCH,
                reasons=[
                    f"close > VWAP({s_vwap:.2f})이나 거래량 {volume_ratio if isinstance(volume_ratio, float) else '∞'}x < "
                    f"reclaim_volume_min_ratio={self.reclaim_volume_min_ratio}"
                ],
                indicators=indicators, risk_notes=risk_notes,
            )
        if not above_vwap and abs(deviation) <= self.max_deviation_pct_for_entry:
            return _Decision(
                action=SignalAction.WATCH, kind=_KIND_WATCH,
                reasons=[
                    f"VWAP 근처 (괴리율 {deviation:.2f}%) — reclaim 대기"
                ],
                indicators=indicators, risk_notes=risk_notes,
            )

        return _Decision(
            action=SignalAction.NO_SIGNAL, kind=_KIND_NO_SIGNAL,
            reasons=[f"VWAP 합성 미충족 (deviation={deviation:.2f}%, above_vwap={above_vwap})"],
            indicators=indicators, risk_notes=risk_notes,
        )

    # ---------- 점수 ----------

    def _confidence(
        self,
        deviation:         float,
        volume_ratio:      float,
        regime:            str | None,
        *,
        rolling_alignment: float | None = None,
    ) -> int:
        # deviation: 0 근처일수록 좋음 (entry cap에서 멀어질수록 페널티).
        dev_term = max(0.0, 100.0 * (1.0 - abs(deviation) / max(self.max_deviation_pct_for_entry, 1e-9)))
        # volume strength
        if volume_ratio == float("inf"):
            vol_term = 100.0
        else:
            vol_term = min(100.0, volume_ratio / max(self.reclaim_volume_min_ratio, 1e-9) * 50.0)
        score = 0.45 * dev_term + 0.40 * vol_term
        # rolling alignment — rolling VWAP과 session VWAP 모두 위면 가산.
        if rolling_alignment is not None and rolling_alignment > 0:
            score += 10.0
        if regime and regime.lower() in {r.lower() for r in self.allowed_regimes}:
            score += 5.0
        return int(max(0.0, min(100.0, score)))

    def _quality_score(
        self,
        deviation:         float,
        volume_ratio:      float,
        regime:            str | None,
        *,
        rolling_alignment: float | None = None,
    ) -> int:
        # quality는 신호 자체의 강도 — 거래량 + rolling 정렬 비중 더 큼.
        if volume_ratio == float("inf"):
            vol_term = 100.0
        else:
            vol_term = min(100.0, volume_ratio / max(self.reclaim_volume_min_ratio, 1e-9) * 50.0)
        # deviation의 sweet spot — entry cap의 30% 부근(VWAP 살짝 위)이 가장 깔끔.
        target = self.max_deviation_pct_for_entry * 0.3
        dev_term = max(0.0, 100.0 - abs(deviation - target) / max(self.max_deviation_pct_for_entry, 1e-9) * 100.0)
        score = 0.5 * vol_term + 0.4 * dev_term
        if rolling_alignment is not None and rolling_alignment > 0:
            score = min(100.0, score + 5.0)
        if regime and regime.lower() in {r.lower() for r in self.allowed_regimes}:
            score = min(100.0, score + 5.0)
        return int(max(0.0, min(100.0, score)))

    # ---------- legacy on_bar ----------

    def on_bar(self, bars: list[Bar]) -> Signal:
        """legacy 인터페이스 — VWAP reclaim → BUY, VWAP 이탈 → SELL.

        legacy on_bar는 position_context가 없으므로 EXIT(=SELL)는 cross-down
        edge에서 발화. 신규 호출자는 generate_signal + position_context를 사용.
        """
        if not bars:
            return Signal.HOLD
        cur = bars[-1]
        self._maybe_reset_session(cur)

        # cross-down detection — legacy는 position 모름이라 직접 SELL.
        decision = self._evaluate(
            bars, regime=None, stale_seconds=None,
            position_context={"has_open_position": True},  # legacy: assume open
        )

        # Update prev_above for next bar AFTER decision is made.
        s_vwap = decision.indicators.get("session_vwap")
        if isinstance(s_vwap, (int, float)) and s_vwap:
            self._prev_above_vwap = cur.close > s_vwap

        if decision.action == SignalAction.BUY:
            self._fired_today = True
            return Signal.BUY
        if decision.action in (SignalAction.SELL, SignalAction.EXIT):
            return Signal.SELL
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
        cur = bars[-1]
        self._maybe_reset_session(cur)

        extra = context.extra or {}
        stale_seconds: float | None = extra.get("data_age_seconds")
        if stale_seconds is None:
            now = extra.get("now")
            if isinstance(now, datetime):
                stale_seconds = self._stale_seconds(cur, now)
        regime = context.regime or extra.get("regime")
        position_context = extra.get("position_context") or {}

        decision = self._evaluate(
            bars, regime=regime, stale_seconds=stale_seconds,
            position_context=position_context,
        )

        # 직전 봉 위치 갱신 — 이번 봉에 대한 결정 후, 다음 호출의 prev_above로 사용.
        s_vwap = decision.indicators.get("session_vwap")
        if isinstance(s_vwap, (int, float)) and s_vwap:
            self._prev_above_vwap = cur.close > s_vwap

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
                    "vwap_deviation_pct": decision.indicators.get("vwap_deviation_pct"),
                    "avg_volume":        decision.indicators.get("avg_volume"),
                    "avg_turnover":      decision.indicators.get("avg_turnover"),
                    "volume_ratio":      decision.indicators.get("volume_ratio"),
                    "risk_notes":        list(decision.risk_notes),
                },
            )
            if decision.action == SignalAction.BUY
            else None
        )

        exit_plan = (
            self.exit_rule(StrategySignal(action=decision.action, symbol=context.symbol))
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
            return f"VWAPStrategy → BUY (kind={decision.kind})"
        if decision.action == SignalAction.EXIT:
            return f"VWAPStrategy → EXIT (kind={decision.kind})"
        if decision.action == SignalAction.WATCH:
            return f"VWAPStrategy → WATCH (kind={decision.kind})"
        if decision.kind == _KIND_REJECT:
            return f"VWAPStrategy → REJECTED ({decision.reasons[0] if decision.reasons else ''})"
        return "VWAPStrategy → NO_SIGNAL"

    # ---------- exit_rule / calculate_size override ----------

    def exit_rule(
        self,
        signal: StrategySignal,
        *,
        position_context: dict[str, Any] | None = None,
    ) -> ExitPlan:
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
                f"trailing {self.trailing_pct}% / {self.time_exit_bars}봉 청산 / VWAP 이탈 EXIT"
            ),
        )

    def calculate_size(
        self,
        signal: StrategySignal,
        *,
        account_context: dict[str, Any] | None = None,
        risk_context:    dict[str, Any] | None = None,
    ) -> SizingHint:
        base_pct = float(self.risk_profile.get("position_size_pct") or 3)
        risk_pct_default = float(self.risk_profile.get("stop_loss_pct") or 1.5)
        rc = risk_context or {}
        confidence = rc.get("confidence")
        deviation  = rc.get("vwap_deviation_pct")
        avg_vol    = rc.get("avg_volume")
        avg_to     = rc.get("avg_turnover")
        notes = list(rc.get("risk_notes") or [])

        scale = 1.0
        if isinstance(confidence, (int, float)):
            if confidence < 40:
                scale *= 0.5
                notes.append("confidence < 40 — 사이즈 50%")
            elif confidence < 60:
                scale *= 0.7
                notes.append("confidence < 60 — 사이즈 70%")

        # VWAP 괴리율이 entry cap의 70% 초과면 추가 축소.
        if isinstance(deviation, (int, float)) and abs(deviation) > self.max_deviation_pct_for_entry * 0.7:
            scale *= 0.7
            notes.append(f"VWAP 괴리율 {deviation:.2f}% — 추격 위험 사이즈 70%")

        # 거래량 / 거래대금이 임계의 2배 미만이면(=마진이 좁으면) 축소.
        if (isinstance(avg_vol, (int, float)) and self.min_avg_volume > 0
                and avg_vol < self.min_avg_volume * 2.0):
            scale *= 0.8
            notes.append("avg_volume 마진 좁음 — 사이즈 80%")
        if (isinstance(avg_to, (int, float)) and self.min_avg_turnover > 0
                and avg_to < self.min_avg_turnover * 2.0):
            scale *= 0.8
            notes.append("avg_turnover 마진 좁음 — 사이즈 80%")

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
        if signal.explanation is not None:
            return signal.explanation
        return super().explain_signal(signal, context=context)


__all__ = ["VWAPStrategy"]

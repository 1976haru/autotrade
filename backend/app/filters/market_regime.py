"""Market Regime Filter (#32).

지수 급락 / 변동성 확대 / 거래대금 위축 / 장 초반 혼란 구간에서 신규 BUY를
제한하거나 차단하는 advisory filter. *주문을 직접 만들지 않는다* — Strategy
신호를 받아 변환만 한다 (CLAUDE.md 절대 원칙 2).

기존 `app.market.regime`(135)는 단순 trending/ranging/high_vol 분류만 반환한다.
본 모듈은 그 위에 더 풍부한 시장 국면(`MarketRegime`) + 결정 정책
(`RegimeDecisionKind`) + buy/sell 분리 + size_multiplier를 얹는다. 기존
regime.py는 그대로 유지 (advisory 호출자 호환).

설계:
- SELL/EXIT은 *기본 차단하지 않는다* — 리스크 축소 주문을 막으면 안 된다.
- BUY는 regime 결정에 따라 ALLOW / REDUCE_SIZE / WATCH_ONLY / BLOCK_NEW_BUY로
  분기. WATCH_ONLY/BLOCK_NEW_BUY면 Strategy의 BUY 신호는 NO_SIGNAL로 강등.
- 본 filter는 helper로만 제공 — `LiveStrategyEngine` / `StrategyEngine` /
  `route_order`에 자동 적용하지 않는다 (운영자 / Agent가 명시적으로 호출).
- broker / risk / permission / execution / governance 어떤 모듈도 import하지
  않는다.
"""

from __future__ import annotations

import dataclasses
from dataclasses import dataclass, field
from enum import StrEnum
from statistics import mean, stdev
from typing import Any

from app.backtest.types import Bar
from app.market.regime import classify_regime
from app.strategies.base import (
    SignalAction,
    SignalExplanation,
    SizingHint,
    StrategySignal,
)


# ---------- 시장 국면 ----------


class MarketRegime(StrEnum):
    """체크리스트 #32 요구의 8개 분류. 기존 regime.py의 어휘와는 별도 — 본
    filter는 더 풍부한 분류를 사용. 기존 'trending_up'/'ranging' 등은
    `classify_regime` 호출 결과로부터 매핑된다.
    """
    TREND_UP         = "TREND_UP"
    TREND_DOWN       = "TREND_DOWN"
    CHOPPY           = "CHOPPY"
    HIGH_VOLATILITY  = "HIGH_VOLATILITY"
    LOW_LIQUIDITY    = "LOW_LIQUIDITY"
    RISK_OFF         = "RISK_OFF"
    OPENING_CHAOS    = "OPENING_CHAOS"
    UNKNOWN          = "UNKNOWN"


class RegimeDecisionKind(StrEnum):
    """결정 정책 — buy/sell 허용과 size_multiplier를 결정한다."""
    ALLOW           = "ALLOW"            # buy_allowed=True,  size=1.0
    REDUCE_SIZE     = "REDUCE_SIZE"      # buy_allowed=True,  size<1.0
    WATCH_ONLY      = "WATCH_ONLY"       # buy_allowed=False, sell 허용
    BLOCK_NEW_BUY   = "BLOCK_NEW_BUY"    # buy_allowed=False, hard reject (기록 강조)


@dataclass(frozen=True)
class RegimeDecision:
    """필터 평가 결과.

    호출자(운영자/Agent/Strategy 후처리)는 본 객체를 받아 신호 처리 정책을
    결정한다. 실제 주문 생성은 RiskManager → PermissionGate → OrderExecutor
    흐름으로 나뉘어 있어 본 객체로는 주문을 만들 수 없다.
    """
    regime:           MarketRegime
    decision:         RegimeDecisionKind
    buy_allowed:      bool
    sell_allowed:     bool
    size_multiplier:  float                       # 0.0 ~ 1.0
    reasons:          list[str] = field(default_factory=list)
    risk_notes:       list[str] = field(default_factory=list)
    indicators:       dict      = field(default_factory=dict)

    def to_dict(self) -> dict:
        return {
            "regime":          self.regime.value,
            "decision":        self.decision.value,
            "buy_allowed":     self.buy_allowed,
            "sell_allowed":    self.sell_allowed,
            "size_multiplier": self.size_multiplier,
            "reasons":         list(self.reasons),
            "risk_notes":      list(self.risk_notes),
            "indicators":      dict(self.indicators),
        }


# ---------- 필터 ----------


class MarketRegimeFilter:
    """시장 국면을 평가해 `RegimeDecision`을 반환하는 advisory filter.

    초기 휴리스틱은 단순 — 종목 봉(또는 지수 봉)으로부터:
    1. OPENING_CHAOS — 세션 시작 후 N봉 이내
    2. LOW_LIQUIDITY — 평균 거래대금 / 거래량이 임계 미만
    3. RISK_OFF — 최근 N봉 누적 등락률이 -X% 이하 (지수 급락)
    4. HIGH_VOLATILITY — 종가 CV (표준편차/평균) 임계 이상
    5. TREND_UP/DOWN — `classify_regime`이 trending_up/down 반환
    6. CHOPPY — `classify_regime`이 ranging 반환
    7. UNKNOWN — 데이터 부족 (`classify_regime` == 'any')

    각 분류는 `_default_decision_for(regime)`로 정책에 매핑.

    KOSPI/KOSDAQ 실시간 지수 연동은 Phase 2 — 현재는 종목 봉 자체를 proxy로
    사용한다. 운영자는 실제 지수 데이터(예: ^KS11)를 별도 watchlist 슬롯에
    수집해 본 filter에 입력한다.
    """

    def __init__(
        self,
        opening_chaos_bars:           int   = 5,
        min_avg_volume:               float = 100.0,
        min_avg_turnover:             float = 0.0,
        liquidity_window:             int   = 20,
        risk_off_drop_pct:            float = -2.0,
        risk_off_lookback_bars:       int   = 30,
        high_vol_cv_pct:              float = 2.5,
        high_vol_window:              int   = 20,
        min_bars_required:            int   = 20,
        # 결정 정책 (각 regime → RegimeDecisionKind). 운영자가 universe 별로
        # tighter/looser 정책을 원하면 override.
        regime_policy:                dict[MarketRegime, RegimeDecisionKind] | None = None,
        # REDUCE_SIZE 시 적용할 multiplier (0~1).
        reduce_size_multiplier:       float = 0.5,
    ):
        if opening_chaos_bars < 0:
            raise ValueError("opening_chaos_bars must be >= 0")
        if liquidity_window < 2 or high_vol_window < 2:
            raise ValueError("windows must be >= 2")
        if risk_off_drop_pct >= 0:
            raise ValueError("risk_off_drop_pct must be negative")
        if risk_off_lookback_bars < 2:
            raise ValueError("risk_off_lookback_bars must be >= 2")
        if high_vol_cv_pct <= 0:
            raise ValueError("high_vol_cv_pct must be positive")
        if min_bars_required < 2:
            raise ValueError("min_bars_required must be >= 2")
        if not (0.0 <= reduce_size_multiplier <= 1.0):
            raise ValueError("reduce_size_multiplier must be in [0, 1]")
        if min_avg_volume < 0 or min_avg_turnover < 0:
            raise ValueError("min_avg_* must be >= 0")

        self.opening_chaos_bars     = opening_chaos_bars
        self.min_avg_volume         = float(min_avg_volume)
        self.min_avg_turnover       = float(min_avg_turnover)
        self.liquidity_window       = liquidity_window
        self.risk_off_drop_pct      = float(risk_off_drop_pct)
        self.risk_off_lookback_bars = risk_off_lookback_bars
        self.high_vol_cv_pct        = float(high_vol_cv_pct)
        self.high_vol_window        = high_vol_window
        self.min_bars_required      = min_bars_required
        self.reduce_size_multiplier = float(reduce_size_multiplier)
        self.regime_policy          = dict(regime_policy or self._default_policy())

    # ---------- 기본 정책 매핑 ----------

    @staticmethod
    def _default_policy() -> dict[MarketRegime, RegimeDecisionKind]:
        return {
            MarketRegime.TREND_UP:        RegimeDecisionKind.ALLOW,
            MarketRegime.TREND_DOWN:      RegimeDecisionKind.WATCH_ONLY,
            MarketRegime.CHOPPY:          RegimeDecisionKind.REDUCE_SIZE,
            MarketRegime.HIGH_VOLATILITY: RegimeDecisionKind.REDUCE_SIZE,
            MarketRegime.LOW_LIQUIDITY:   RegimeDecisionKind.BLOCK_NEW_BUY,
            MarketRegime.RISK_OFF:        RegimeDecisionKind.BLOCK_NEW_BUY,
            MarketRegime.OPENING_CHAOS:   RegimeDecisionKind.BLOCK_NEW_BUY,
            MarketRegime.UNKNOWN:         RegimeDecisionKind.WATCH_ONLY,
        }

    # ---------- 평가 ----------

    def evaluate(
        self,
        bars: list[Bar],
        *,
        regime_override: MarketRegime | None = None,
    ) -> RegimeDecision:
        """봉 시퀀스를 받아 `RegimeDecision`을 반환.

        `regime_override`가 주어지면 휴리스틱 분류를 건너뛰고 정책 매핑만 적용 —
        실시간 지수 데이터 / 외부 risk-off 신호를 운영자가 직접 주입할 때 사용.
        """
        indicators: dict[str, Any] = {
            "bars": len(bars),
        }

        if regime_override is not None:
            return self._make_decision(regime_override, indicators=indicators,
                                        reasons=[f"regime_override={regime_override.value}"])

        if not bars or len(bars) < self.min_bars_required:
            return self._make_decision(
                MarketRegime.UNKNOWN, indicators=indicators,
                reasons=[f"bars {len(bars)} < min_bars_required={self.min_bars_required}"],
            )

        # 1. OPENING_CHAOS — 마지막 봉의 거래일과 같은 날짜의 봉 수가 cooldown 이내
        session = self._session_bars(bars)
        bars_in_session = len(session)
        indicators["bars_in_session"] = bars_in_session
        if bars_in_session > 0 and bars_in_session <= self.opening_chaos_bars:
            return self._make_decision(
                MarketRegime.OPENING_CHAOS, indicators=indicators,
                reasons=[
                    f"OPENING_CHAOS: bars_in_session={bars_in_session} ≤ "
                    f"opening_chaos_bars={self.opening_chaos_bars}"
                ],
            )

        # 2. LOW_LIQUIDITY — 평균 거래량/거래대금이 임계 미만
        liq_window = bars[-self.liquidity_window:]
        avg_volume   = sum(b.volume for b in liq_window) / len(liq_window)
        avg_turnover = sum(b.close * b.volume for b in liq_window) / len(liq_window)
        indicators["avg_volume"]   = avg_volume
        indicators["avg_turnover"] = avg_turnover

        if self.min_avg_volume > 0 and avg_volume < self.min_avg_volume:
            return self._make_decision(
                MarketRegime.LOW_LIQUIDITY, indicators=indicators,
                reasons=[
                    f"LOW_LIQUIDITY: avg_volume {avg_volume:.0f} < "
                    f"min_avg_volume={self.min_avg_volume:.0f}"
                ],
            )
        if self.min_avg_turnover > 0 and avg_turnover < self.min_avg_turnover:
            return self._make_decision(
                MarketRegime.LOW_LIQUIDITY, indicators=indicators,
                reasons=[
                    f"LOW_LIQUIDITY: avg_turnover {avg_turnover:.0f} < "
                    f"min_avg_turnover={self.min_avg_turnover:.0f}"
                ],
            )

        # 3. RISK_OFF — 최근 N봉 누적 등락률이 임계 이하
        risk_window = bars[-self.risk_off_lookback_bars:]
        if len(risk_window) >= 2 and risk_window[0].close > 0:
            cumulative_pct = (risk_window[-1].close - risk_window[0].close) / risk_window[0].close * 100.0
            indicators["cumulative_pct"] = round(cumulative_pct, 3)
            if cumulative_pct <= self.risk_off_drop_pct:
                return self._make_decision(
                    MarketRegime.RISK_OFF, indicators=indicators,
                    reasons=[
                        f"RISK_OFF: 최근 {self.risk_off_lookback_bars}봉 누적 "
                        f"{cumulative_pct:.2f}% ≤ risk_off_drop_pct={self.risk_off_drop_pct}%"
                    ],
                )

        # 4. HIGH_VOLATILITY — 종가 CV (표준편차/평균)
        vol_window = bars[-self.high_vol_window:]
        closes = [b.close for b in vol_window]
        avg = mean(closes) if closes else 0.0
        if len(closes) >= 2 and avg > 0:
            sd = stdev(closes)
            cv_pct = (sd / avg) * 100.0
            indicators["cv_pct"] = round(cv_pct, 3)
            if cv_pct >= self.high_vol_cv_pct:
                return self._make_decision(
                    MarketRegime.HIGH_VOLATILITY, indicators=indicators,
                    reasons=[
                        f"HIGH_VOLATILITY: CV {cv_pct:.2f}% ≥ "
                        f"high_vol_cv_pct={self.high_vol_cv_pct}%"
                    ],
                )

        # 5/6/7. classify_regime → TREND_UP/DOWN/CHOPPY/UNKNOWN
        legacy = classify_regime(bars)
        indicators["legacy_regime"] = legacy
        regime = self._map_legacy_regime(legacy)
        return self._make_decision(
            regime, indicators=indicators,
            reasons=[f"classify_regime → {legacy} → {regime.value}"],
        )

    # ---------- helpers ----------

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
    def _map_legacy_regime(legacy: str) -> MarketRegime:
        """기존 `classify_regime` 어휘 → 본 모듈의 MarketRegime 매핑."""
        mapping = {
            "trending_up":   MarketRegime.TREND_UP,
            "trending_down": MarketRegime.TREND_DOWN,
            "trending":      MarketRegime.TREND_UP,
            "ranging":       MarketRegime.CHOPPY,
            "high_vol":      MarketRegime.HIGH_VOLATILITY,
            "any":           MarketRegime.UNKNOWN,
        }
        return mapping.get(legacy, MarketRegime.UNKNOWN)

    def _make_decision(
        self,
        regime: MarketRegime,
        *,
        indicators: dict,
        reasons:    list[str] | None = None,
    ) -> RegimeDecision:
        kind = self.regime_policy.get(regime, RegimeDecisionKind.WATCH_ONLY)
        buy_allowed = kind in (RegimeDecisionKind.ALLOW, RegimeDecisionKind.REDUCE_SIZE)
        size_mult   = (
            self.reduce_size_multiplier if kind == RegimeDecisionKind.REDUCE_SIZE
            else (1.0 if kind == RegimeDecisionKind.ALLOW else 0.0)
        )
        risk_notes: list[str] = []
        if kind == RegimeDecisionKind.REDUCE_SIZE:
            risk_notes.append(
                f"regime={regime.value} → REDUCE_SIZE (size × {size_mult:.2f}) — "
                "운영자/Agent가 sizing_hint에 multiplier 적용 권장"
            )
        if kind == RegimeDecisionKind.WATCH_ONLY:
            risk_notes.append(
                f"regime={regime.value} → WATCH_ONLY — 신규 BUY 차단, SELL/EXIT 허용"
            )
        if kind == RegimeDecisionKind.BLOCK_NEW_BUY:
            risk_notes.append(
                f"regime={regime.value} → BLOCK_NEW_BUY — 신규 BUY 강제 차단, SELL/EXIT 허용"
            )

        indicators_out = {**indicators, "regime": regime.value, "decision": kind.value}
        return RegimeDecision(
            regime=regime,
            decision=kind,
            buy_allowed=buy_allowed,
            sell_allowed=True,  # SELL/EXIT은 항상 허용 (리스크 축소 보호)
            size_multiplier=size_mult,
            reasons=list(reasons or []),
            risk_notes=risk_notes,
            indicators=indicators_out,
        )


# ---------- Strategy 신호 차단 helper ----------


def apply_regime_filter_to_signal(
    signal:           StrategySignal | None,
    regime_decision:  RegimeDecision,
) -> StrategySignal | None:
    """전략 신호에 regime decision을 적용해 변환된 신호를 반환.

    정책:
    - signal이 None이면 그대로 None.
    - signal.action ∈ {SELL, EXIT}이면 regime과 무관하게 통과 (리스크 축소).
    - signal.action == BUY:
      * decision=ALLOW → 그대로.
      * decision=REDUCE_SIZE → action 유지, sizing_hint × size_multiplier 축소,
        risk_notes / reasons에 사유 추가.
      * decision=WATCH_ONLY → action을 WATCH로 강등, reasons에 차단 사유 추가.
      * decision=BLOCK_NEW_BUY → action을 NO_SIGNAL로 강등, indicators에
        decision_kind="REJECT" 표시, reasons에 차단 사유 추가.
    - 그 외 action(WATCH/NO_SIGNAL)은 그대로.

    절대 원칙:
    - 본 함수는 *주문을 생성하지 않는다*. signal의 `is_order_intent`는 항상
      False를 유지.
    - broker / RiskManager / PermissionGate / OrderExecutor를 호출하지 않는다.
    """
    if signal is None:
        return None

    # SELL/EXIT은 차단 안 함 — 리스크 축소 보호.
    if signal.action in (SignalAction.SELL, SignalAction.EXIT):
        return signal

    if signal.action != SignalAction.BUY:
        return signal

    kind = regime_decision.decision
    if kind == RegimeDecisionKind.ALLOW:
        return signal

    block_reason = (
        f"market regime filter → {kind.value} (regime={regime_decision.regime.value})"
    )
    cause_lines = list(regime_decision.reasons)

    # 기존 explanation 보존 + 사유 추가.
    expl = signal.explanation or SignalExplanation(summary="(no explanation)")
    new_reasons = list(expl.reasons) + [block_reason] + cause_lines
    new_indicators = dict(expl.indicators) if expl.indicators else {}
    new_indicators["regime_filter"] = regime_decision.to_dict()

    if kind == RegimeDecisionKind.REDUCE_SIZE:
        # action은 BUY 유지, sizing_hint를 축소.
        scale = max(0.0, regime_decision.size_multiplier)
        new_sizing = signal.sizing_hint
        if new_sizing is not None:
            new_pct = (
                round(new_sizing.position_size_pct * scale, 4)
                if new_sizing.position_size_pct is not None else None
            )
            note = (
                f"regime={regime_decision.regime.value} REDUCE_SIZE × {scale:.2f}"
                + (f" / {new_sizing.note}" if new_sizing.note else "")
            )
            new_sizing = SizingHint(
                quantity=new_sizing.quantity,
                position_size_pct=new_pct,
                risk_pct=new_sizing.risk_pct,
                reduce_only=new_sizing.reduce_only,
                note=note,
            )
        new_explanation = SignalExplanation(
            summary=expl.summary,
            reasons=new_reasons,
            confidence=expl.confidence,
            indicators=new_indicators,
            required_regime=expl.required_regime,
        )
        return dataclasses.replace(
            signal,
            sizing_hint=new_sizing,
            explanation=new_explanation,
        )

    # WATCH_ONLY / BLOCK_NEW_BUY — BUY 강등.
    if kind == RegimeDecisionKind.WATCH_ONLY:
        new_action = SignalAction.WATCH
        new_indicators["decision_kind"] = "WATCH"
    else:  # BLOCK_NEW_BUY
        new_action = SignalAction.NO_SIGNAL
        new_indicators["decision_kind"] = "REJECT"

    new_explanation = SignalExplanation(
        summary=f"{expl.summary} | regime_filter={kind.value}",
        reasons=new_reasons,
        confidence=expl.confidence,
        indicators=new_indicators,
        required_regime=expl.required_regime,
    )
    return dataclasses.replace(
        signal,
        action=new_action,
        # BUY 강등 시 sizing_hint/exit_plan은 사용 불필요 — 원본 보존하되 의미 X.
        explanation=new_explanation,
    )

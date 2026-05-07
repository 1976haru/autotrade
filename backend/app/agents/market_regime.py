"""Market Regime Filter (225, MUST).

10가지 regime 분류 + 전략 허용/차단 매핑 + 리스크 곱셈 계수. 입력은 전부
숫자 (변동성·거래량·갭·시간대) — 외부 LLM 의존 없음. AI Key 미설정에서도
deterministic 동작.

Regime:
  TREND_UP / TREND_DOWN — 강한 추세
  CHOPPY                — 횡보 / 무방향
  HIGH_VOLATILITY       — 변동성 급증
  LOW_LIQUIDITY         — 거래대금 빈약
  GAP_DAY               — 큰 갭 발생일
  NEWS_DRIVEN           — 뉴스 sentiment 강함
  RISK_OFF              — 시장 전반 위험 회피
  OPENING_CHAOS         — 장초반 혼란
  LATE_DAY_FADE         — 마감 직전 흐름 약화

출력:
  regime, confidence, reasons, allowed_strategies, blocked_strategies,
  risk_multiplier, max_position_size_multiplier, trade_permission,
  operator_summary
"""

from __future__ import annotations

from dataclasses import dataclass, field


# 운영자가 운용하는 전략 목록 — config/strategies와 동기화. regime별로 어떤
# 전략이 적합한지 매핑. 본 모듈에서는 일단 하드코딩 — 운영자 설정 UI는 후속.
_STRATEGIES = ["sma_crossover", "orb_vwap", "rsi_reversion"]


@dataclass
class RegimeOutput:
    regime:                       str
    confidence:                   int
    reasons:                      list[str]      = field(default_factory=list)
    allowed_strategies:           list[str]      = field(default_factory=list)
    blocked_strategies:           list[str]      = field(default_factory=list)
    risk_multiplier:              float          = 1.0
    max_position_size_multiplier: float          = 1.0
    trade_permission:             str            = "ALLOW"   # ALLOW / WATCH / PAUSE / BLOCK
    operator_summary:             list[str]      = field(default_factory=list)


def classify_market_regime(
    *,
    trend_strength_pct:    float = 0.0,   # 양수=상승 추세, 음수=하락
    volatility_pct:        float = 0.0,
    volume_ratio:          float = 1.0,   # vs 평균
    gap_pct:               float = 0.0,
    news_sentiment:        int   = 50,    # 0-100
    is_opening_30min:      bool  = False,
    is_late_day_30min:     bool  = False,
    risk_off_signal:       bool  = False,
) -> RegimeOutput:
    """입력으로 들어오는 정량 지표만으로 regime 판정. 위험-우선 분기.

    우선순위:
      1) RISK_OFF / OPENING_CHAOS / GAP_DAY — 가장 보수적 처리
      2) HIGH_VOLATILITY / LOW_LIQUIDITY    — sizing 축소 + WATCH
      3) NEWS_DRIVEN / LATE_DAY_FADE        — 정보용 + sizing 축소
      4) TREND_UP / TREND_DOWN              — 통상 운용
      5) CHOPPY                             — fallback
    """
    reasons: list[str] = []

    # 1. risk-off — 시장 전반 매도 압력
    if risk_off_signal:
        reasons.append("risk_off:explicit_signal")
        return _build("RISK_OFF", 90, reasons,
                      allowed=[], blocked=_STRATEGIES,
                      risk_mult=0.0, size_mult=0.0, perm="BLOCK")

    # 1. opening chaos
    if is_opening_30min and volatility_pct >= 3.0:
        reasons.append(f"opening_30min:vol={volatility_pct:.1f}%")
        return _build("OPENING_CHAOS", 80, reasons,
                      allowed=[], blocked=_STRATEGIES,
                      risk_mult=0.5, size_mult=0.5, perm="PAUSE")

    # 1. gap day — 갭 ±2% 이상
    if abs(gap_pct) >= 2.0:
        reasons.append(f"gap:{gap_pct:+.2f}%")
        return _build("GAP_DAY", 75, reasons,
                      allowed=["orb_vwap"], blocked=["rsi_reversion"],
                      risk_mult=0.7, size_mult=0.7, perm="WATCH")

    # 2. high volatility (gap이 없어도 변동성 자체가 큰 케이스)
    if volatility_pct >= 5.0:
        reasons.append(f"high_volatility:{volatility_pct:.1f}%")
        return _build("HIGH_VOLATILITY", 80, reasons,
                      allowed=["sma_crossover"], blocked=["rsi_reversion"],
                      risk_mult=0.6, size_mult=0.7, perm="WATCH")

    # 2. low liquidity — 거래량 평균의 50% 미만
    if volume_ratio < 0.5:
        reasons.append(f"volume_ratio:{volume_ratio:.2f}")
        return _build("LOW_LIQUIDITY", 70, reasons,
                      allowed=[], blocked=_STRATEGIES,
                      risk_mult=0.5, size_mult=0.5, perm="WATCH")

    # 3. late-day fade — 마감 30분, 추세 약화
    if is_late_day_30min:
        reasons.append("late_day_30min")
        return _build("LATE_DAY_FADE", 65, reasons,
                      allowed=[], blocked=_STRATEGIES,
                      risk_mult=0.5, size_mult=0.3, perm="WATCH")

    # 3. news-driven (sentiment 극단)
    if news_sentiment >= 80 or news_sentiment <= 20:
        reasons.append(f"news_sentiment:{news_sentiment}")
        return _build("NEWS_DRIVEN", 65, reasons,
                      allowed=["orb_vwap"], blocked=["rsi_reversion"],
                      risk_mult=0.8, size_mult=0.8, perm="WATCH")

    # 4. trend
    if trend_strength_pct >= 1.5:
        reasons.append(f"trend_up:{trend_strength_pct:+.2f}%")
        return _build("TREND_UP", 75, reasons,
                      allowed=["sma_crossover", "orb_vwap"],
                      blocked=["rsi_reversion"],
                      risk_mult=1.0, size_mult=1.0, perm="ALLOW")
    if trend_strength_pct <= -1.5:
        reasons.append(f"trend_down:{trend_strength_pct:+.2f}%")
        return _build("TREND_DOWN", 75, reasons,
                      allowed=["sma_crossover"], blocked=["orb_vwap", "rsi_reversion"],
                      risk_mult=0.8, size_mult=0.7, perm="WATCH")

    # 5. choppy fallback
    reasons.append(f"choppy:trend={trend_strength_pct:+.2f}%_vol={volatility_pct:.1f}%")
    return _build("CHOPPY", 60, reasons,
                  allowed=["rsi_reversion"], blocked=["orb_vwap"],
                  risk_mult=0.8, size_mult=0.8, perm="ALLOW")


def _build(
    regime: str, confidence: int, reasons: list[str],
    *,
    allowed: list[str], blocked: list[str],
    risk_mult: float, size_mult: float, perm: str,
) -> RegimeOutput:
    summary = _operator_summary(regime, perm, risk_mult, size_mult)
    return RegimeOutput(
        regime=regime,
        confidence=confidence,
        reasons=reasons,
        allowed_strategies=list(allowed),
        blocked_strategies=list(blocked),
        risk_multiplier=risk_mult,
        max_position_size_multiplier=size_mult,
        trade_permission=perm,
        operator_summary=summary,
    )


_REGIME_LABEL = {
    "TREND_UP":        "추세 상승",
    "TREND_DOWN":      "추세 하락",
    "CHOPPY":          "횡보",
    "HIGH_VOLATILITY": "고변동성",
    "LOW_LIQUIDITY":   "유동성 부족",
    "GAP_DAY":         "갭 데이",
    "NEWS_DRIVEN":     "뉴스 주도",
    "RISK_OFF":        "리스크 오프",
    "OPENING_CHAOS":   "장초반 혼란",
    "LATE_DAY_FADE":   "마감 약화",
}


def _operator_summary(
    regime: str, perm: str, risk_mult: float, size_mult: float,
) -> list[str]:
    label = _REGIME_LABEL.get(regime, regime)
    perm_kr = {"ALLOW": "거래 가능", "WATCH": "주의 거래", "PAUSE": "일시 정지", "BLOCK": "거래 금지"}[perm]
    sizing = f"리스크 ×{risk_mult:.1f} / 포지션 ×{size_mult:.1f}"
    return [f"장세: {label}", perm_kr, sizing]

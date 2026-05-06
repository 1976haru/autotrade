"""Signal quality scoring (136, MUST).

전략이 BUY/SELL을 emit할 때 그 신호가 *얼마나 강하고 얼마나 신뢰할 만한지*
운영자/감사가 즉시 볼 수 있게 0-100 점수로 노출한다. AI confluence score
(004 패턴)와 별도 — 이건 시스템적 신호의 자체 평가지 AI 판단이 아니다.

CLAUDE.md '수익률보다 손실 방어' — 'BUY 신호'만으로는 운영자가 진입 결정을
내리기 어렵다. 같은 BUY라도 강한 추세 + 충분한 데이터 vs 약한 cross + 데이터
부족은 차이가 크다.

본 모듈은 advisory만 — 신호를 차단하지 않는다. 점수가 낮아도 신호 자체는
그대로 흐르며, 운영자/PermissionGate가 final decision-maker.

반환 shape: {"strength": 0-100, "confidence": 0-100}.
- strength: 신호의 강도 (예: cross의 폭, 추세의 가파름)
- confidence: 신호를 신뢰할 만한 컨텍스트 정도 (충분한 데이터/regime 매칭/낮은 변동성)
"""

from statistics import mean, stdev

from app.backtest.types import Bar, Signal


def _clamp(value: float, lo: float = 0.0, hi: float = 100.0) -> int:
    return int(max(lo, min(hi, value)))


def _strength(bars: list[Bar], signal: Signal) -> int:
    """SMA gap percent와 단기 momentum의 결합. HOLD는 0."""
    if signal == Signal.HOLD or len(bars) < 20:
        return 0
    closes = [b.close for b in bars]
    short_window = closes[-20:]
    long_window  = closes[-60:] if len(closes) >= 60 else closes
    short_sma = mean(short_window)
    long_sma  = mean(long_window) or 1
    gap_pct = abs((short_sma - long_sma) / long_sma) * 100.0
    # gap 1% → 50점, gap 3%+ → 100점.
    return _clamp(gap_pct * 50)


def _confidence(bars: list[Bar], regime_matches: bool) -> int:
    """다음 요인의 결합:
    - 봉 수 (>= 60: full credit, < 20: 0)
    - regime이 strategy와 매칭 (50% 가중)
    - 변동성이 안정 (CV < 1.5%면 가산)
    """
    if not bars:
        return 0
    n = len(bars)
    # 봉 수 구간 — 60 이상이면 60점 만점
    bar_score = _clamp(n / 60 * 60, hi=60)

    # regime 매칭 — 매칭이면 +25, 아니면 0
    regime_score = 25 if regime_matches else 0

    # 변동성 안정 — CV < 1.5%면 +15
    closes = [b.close for b in bars[-20:]]
    if len(closes) >= 20:
        avg = mean(closes) or 1
        sd = stdev(closes)
        cv_pct = (sd / avg) * 100.0
        vol_score = 15 if cv_pct < 1.5 else 0
    else:
        vol_score = 0

    return _clamp(bar_score + regime_score + vol_score)


def signal_quality(
    bars: list[Bar], signal: Signal, regime_matches: bool = True,
) -> dict:
    """현재 봉/신호/regime 매칭으로부터 quality 산출.

    HOLD 신호는 quality가 의미 없으므로 둘 다 0. BUY/SELL일 때만 의미 있는
    점수를 돌려준다.
    """
    return {
        "strength":   _strength(bars, signal),
        "confidence": _confidence(bars, regime_matches),
    }

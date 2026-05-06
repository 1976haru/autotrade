"""Market regime classifier (135, MUST).

131이 Strategy.required_regime을 도입하면서 'sma_crossover은 trending에서만
유효하다' 같은 contract를 코드 단에 명시했다. 135는 그 반대편 — *현재 시장이
어떤 체제인가*를 분류해 운영자/전략에게 advisory로 surface한다.

Advisory만 하고 신호를 자동 차단하지는 않는다 — 단타 자동매매에서 분류기가
오판해 신호를 막으면 수익 기회를 잃는 위험이 신호 그대로 가는 위험보다 클
수 있고, 운영자가 final decision-maker라는 CLAUDE.md 원칙과 일치.

분류 로직은 단순한 휴리스틱:
1. 충분한 봉이 없으면(<20) 'any'
2. SMA(20)가 SMA(60)보다 훨씬 위 + 단기 momentum 양수 → 'trending_up'
3. 반대 → 'trending_down'
4. 표준편차/평균 비율(coefficient of variation)이 임계 이상 → 'high_vol'
5. 그 외 → 'ranging'
"""

from statistics import mean, stdev

from app.backtest.types import Bar


# 임계값 — 운영 환경에서 백테스트 데이터로 튜닝 권장. 본 파일의 default는
# 한국 단타 KOSPI 종목의 분봉 가정.
_TRENDING_GAP_PCT = 0.5   # SMA(20) vs SMA(60) gap이 0.5% 이상이면 추세
_HIGH_VOL_CV_PCT  = 1.5   # 종가 변동계수가 1.5% 이상이면 고변동


def classify_regime(bars: list[Bar]) -> str:
    """현재 시점에서 추정되는 시장 체제를 반환.

    돌려주는 값은 Strategy.required_regime과 매칭되도록 같은 어휘를 사용 —
    'trending_up' / 'trending_down' / 'trending' / 'ranging' / 'high_vol' / 'any'.
    'trending'은 방향 무관 추세 (sma_crossover처럼 양방향 작동하는 전략용)
    이지만 분류기는 항상 trending_up/trending_down으로 세분 — Strategy 측에서
    "trending"으로 요구하면 trending_up/down 모두 OK로 매칭한다.
    """
    if not bars or len(bars) < 20:
        return "any"

    closes = [b.close for b in bars]
    avg = mean(closes)
    if avg <= 0:
        return "any"

    # 1) 변동성 — coefficient of variation(표준편차/평균).
    if len(closes) >= 20:
        sd = stdev(closes[-20:])
        cv_pct = (sd / avg) * 100.0
        if cv_pct >= _HIGH_VOL_CV_PCT:
            return "high_vol"

    # 2) 추세 — SMA(20) vs SMA(60). 60봉 부족하면 직전 N봉 평균과 단기 평균.
    short_window = closes[-20:]
    long_window  = closes[-60:] if len(closes) >= 60 else closes
    short_sma = mean(short_window)
    long_sma  = mean(long_window)
    gap_pct = ((short_sma - long_sma) / long_sma) * 100.0 if long_sma else 0.0
    if abs(gap_pct) >= _TRENDING_GAP_PCT:
        return "trending_up" if gap_pct > 0 else "trending_down"

    return "ranging"


def matches_required_regime(current: str, required: str) -> bool:
    """전략이 요구하는 regime과 현재 regime이 호환되는지.

    required="any"이면 항상 OK.
    required="trending"이면 trending_up / trending_down 모두 OK.
    그 외는 정확히 일치해야 OK.
    """
    if required == "any" or not required:
        return True
    if required == "trending":
        return current in ("trending_up", "trending_down", "trending")
    return current == required

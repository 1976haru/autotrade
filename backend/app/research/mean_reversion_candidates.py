"""장중 평균회귀(mean-reversion) 후보 진입 조건 (research-only, 백테스트 전용).

기존 돌파/모멘텀 추종 전략군(ORB/Momentum/Gap/VWAP)은 실데이터에서 비용 전에도 엣지가
없었다 — 진입 후 MAE>MFE, stop_first≫target_hit (추격 진입이 평균회귀에 당하는 구조).
본 모듈은 *반대 가설* — 과도 이탈 후 되돌림(mean reversion) long 후보를 정의한다.

엄격히 research-only:
- 본 모듈의 어떤 함수도 런타임 strategy registry / LiveStrategyEngine / broker /
  OrderExecutor / route_order 와 연결되지 않는다. 등록/자동 적용 0건.
- 모든 후보는 *순수 판정 함수* (mi + 당일 누적 정보만) — look-ahead 금지.
- 본 프로젝트는 long-only — overheated-up→short 류 후보는 long-only 제약으로 *연구 제외*
  표시하고 oversold-washout→long 형태로만 구현.
- 파라미터는 1~2개 보수적 기본값만 (넓은 grid search / 과최적화 금지).
- IS_RESEARCH_ONLY = True (불변).
"""

from __future__ import annotations

from typing import Any, Callable

IS_RESEARCH_ONLY = True

# 후보 진입 임계 (보수적 단일 기본값 — grid search 아님).
_VWAP_DEV_BPS = 0.004        # VWAP 대비 40bps 이탈을 과도 이탈로 간주
_VOL_SPIKE_DEV_BPS = 0.005   # 거래량 급증 시 50bps 이탈
_VOL_SPIKE_MULT = 2.0        # 거래량 급증 배수
_OVERSOLD_BPS = 0.005        # 당일 고점 대비 50bps 이상 눌림 = 과매도
_RANGE_MIN, _RANGE_MAX = 0.005, 0.03  # range-mid 회귀가 의미있는 폭
_RANGE_LOWER_POS = 0.35      # range 하단 35% 이하
_TREND_MOVE = 0.02           # 시가 대비 2% 이동 = 추세일 후보
_GAP_MIN, _GAP_MAX = -0.06, -0.01  # 과도하지 않은 gap-down


def _running_hilo(day_bars, i) -> tuple[float, float]:
    """진입 시점까지(point-in-time) 당일 누적 high/low — look-ahead 없음."""
    win = day_bars[: i + 1]
    if not win:
        return 0.0, 0.0
    return max(b.high for b in win), min(b.low for b in win)


# ─────────────────────────────────────────────────────────────────────────────
# 후보 A~F (long mean-reversion) — 모두 (mi, *, ve, gap, day_bars, i) -> bool
# ─────────────────────────────────────────────────────────────────────────────


def cand_vwap_deviation_revert(mi, *, ve, gap, day_bars, i) -> bool:
    """A. VWAP 에서 과도(≥40bps) 아래 이탈 후 반전 캔들 → VWAP 회귀 long."""
    vwap, cp, rc = mi.vwap, mi.current_price, mi.recent_closes
    if not vwap or vwap <= 0 or len(rc) < 2:
        return False
    dev = (vwap - cp) / vwap                 # 양수 = VWAP 아래(과매도)
    return dev >= _VWAP_DEV_BPS and cp > rc[-2]   # 반전(직전봉 대비 상승)


def cand_orb_failed_breakout_fade(mi, *, ve, gap, day_bars, i) -> bool:
    """B. opening range 하단 이탈(false breakdown) 후 range 내부 복귀 → 회귀 long."""
    orh, orl, cp, rc = (mi.opening_range_high, mi.opening_range_low,
                        mi.current_price, mi.recent_closes)
    if orh is None or orl is None or len(rc) < 2:
        return False
    broke_down = min(rc[:-1]) < orl          # 직전 구간 ORL 아래로 이탈
    back_inside = orl <= cp < orh            # 다시 range 내부로 복귀
    return broke_down and back_inside and cp > rc[-2]


def cand_gap_fade_mean_reversion(mi, *, ve, gap, day_bars, i) -> bool:
    """C. 과도하지 않은 gap-down 후 장초반 지속 실패 → 전일종가/시가 방향 회귀 long."""
    if gap is None or not (_GAP_MIN <= gap <= _GAP_MAX):
        return False
    reverting = mi.current_price > mi.open_price          # 시가 위로 회복
    room = (mi.prev_close is None) or (mi.current_price < mi.prev_close)  # 회귀 여지
    return reverting and room


def cand_volume_spike_reversion(mi, *, ve, gap, day_bars, i) -> bool:
    """D. 거래량 급증 + 과매도 washout 후 반전 → 회귀 long.

    (overheated-up→short 형태는 long-only 제약으로 연구 제외 — oversold washout 만 구현.)
    """
    vwap, cp, rc = mi.vwap, mi.current_price, mi.recent_closes
    if not vwap or vwap <= 0 or len(rc) < 2 or ve < _VOL_SPIKE_MULT:
        return False
    dev = (vwap - cp) / vwap
    return dev >= _VOL_SPIKE_DEV_BPS and cp > rc[-2]


def cand_lower_band_reclaim(mi, *, ve, gap, day_bars, i) -> bool:
    """E. 장중 저점 형성(밴드 하단 이탈) 후 재진입 → 과매도 회복 long."""
    rc, cp = mi.recent_closes, mi.current_price
    if len(rc) < 3 or max(rc) <= 0:
        return False
    made_low = rc[-2] <= min(rc) * 1.001     # 직전봉이 최근 저점
    oversold = (max(rc) - cp) / max(rc) >= _OVERSOLD_BPS
    return made_low and oversold and cp > rc[-2]


def cand_range_mid_reversion(mi, *, ve, gap, day_bars, i) -> bool:
    """F. 당일 range 하단 근처에서 중심선 회귀 — 횡보장 한정(추세일은 별도 filter 로 차단)."""
    cp, rc = mi.current_price, mi.recent_closes
    hi, lo = _running_hilo(day_bars, i)
    if hi <= lo or cp <= 0 or len(rc) < 2:
        return False
    width = (hi - lo) / cp
    if not (_RANGE_MIN <= width <= _RANGE_MAX):
        return False
    pos = (cp - lo) / (hi - lo)
    return pos <= _RANGE_LOWER_POS and cp > rc[-2]


# ─────────────────────────────────────────────────────────────────────────────
# 후보 G — NO_TRADE_TREND_DAY_FILTER (평균회귀가 죽는 강추세일 차단, look-ahead 금지)
# ─────────────────────────────────────────────────────────────────────────────


def is_trend_day(mi, *, day_bars, i) -> bool:
    """진입 시점까지의 정보만으로 강한 추세일 판정 — True 면 평균회귀 진입 차단 권고."""
    op, cp = mi.open_price, mi.current_price
    if op <= 0:
        return False
    move = abs(cp - op) / op
    hi, lo = _running_hilo(day_bars, i)
    if move >= _TREND_MOVE and hi > lo:
        pos = (cp - lo) / (hi - lo)
        return pos >= 0.8 or pos <= 0.2     # 극단(한 방향 추세) 위치
    return False


CANDIDATES: dict[str, Callable[..., bool]] = {
    "VWAP_DEVIATION_REVERT": cand_vwap_deviation_revert,
    "ORB_FAILED_BREAKOUT_FADE": cand_orb_failed_breakout_fade,
    "GAP_FADE_MEAN_REVERSION": cand_gap_fade_mean_reversion,
    "VOLUME_SPIKE_REVERSION": cand_volume_spike_reversion,
    "LOWER_BAND_RECLAIM": cand_lower_band_reclaim,
    "RANGE_MID_REVERSION": cand_range_mid_reversion,
}


def candidate_catalog() -> dict[str, Any]:
    """후보 메타(read-only) — 런타임 등록용 아님."""
    return {
        "is_research_only": IS_RESEARCH_ONLY,
        "candidates": list(CANDIDATES),
        "no_trade_filter": "is_trend_day",
        "long_only": True,
        "note": "research-only — 런타임 전략 미등록, 자동 적용 0.",
    }

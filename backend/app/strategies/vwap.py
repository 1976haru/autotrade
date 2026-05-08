"""VWAP 계산 유틸 (#31).

세션 누적 VWAP / rolling VWAP / 괴리율 / 거래량-유동성 가드를 제공.
`orb_vwap.py`(#142)는 자체 VWAP 누적을 인라인으로 가지고 있고 본 모듈을
import하지 않는다 — 기존 동작 보존이 우선이라 추후 통합 PR에서 정리.

본 모듈은 어떤 broker/risk/permission/execution 모듈도 import하지 않는다.
순수 계산 + Bar 입력 + 결과 반환만 한다.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable

from app.backtest.types import Bar


# ---------- 기본 계산 ----------


def typical_price(bar: Bar) -> float:
    """(h+l+c)/3. 표준 typical price 정의 — VWAP 분자에 쓰인다."""
    return (bar.high + bar.low + bar.close) / 3.0


def vwap_of(bars: Iterable[Bar]) -> float | None:
    """주어진 봉들의 거래량 가중 평균가. 거래량 합 0이면 None.

    None을 반환하는 이유 — VWAP은 정의상 거래량이 분모이므로 0 거래량 세션은
    값이 없는 게 맞다. 호출자는 None을 받아 안전 기본 동작(예: HOLD)으로 분기.
    """
    pv  = 0.0
    vol = 0
    for b in bars:
        pv  += typical_price(b) * b.volume
        vol += b.volume
    if vol <= 0:
        return None
    return pv / vol


def extract_session_bars(bars: list[Bar]) -> list[Bar]:
    """가장 최근 봉의 거래일과 같은 날짜의 봉만 추출.

    VWAP은 일반적으로 *세션 누적* — 거래일이 바뀌면 reset. 본 함수가 호출자
    각각이 동일한 날짜 비교 로직을 재구현하지 않게 한다.
    """
    if not bars:
        return []
    last_date = bars[-1].timestamp.date()
    for i in range(len(bars) - 1, -1, -1):
        if bars[i].timestamp.date() != last_date:
            return bars[i + 1:]
    return list(bars)


def session_vwap(bars: list[Bar]) -> float | None:
    """현재 세션(=마지막 봉의 거래일)의 누적 VWAP."""
    return vwap_of(extract_session_bars(bars))


def rolling_vwap(bars: list[Bar], window: int) -> float | None:
    """최근 `window`개 봉의 거래량 가중 평균가. window<=0이면 ValueError.

    rolling은 거래일 경계를 가로지를 수 있다 — 거래일 reset 의미가 필요하면
    `session_vwap`을 쓴다. 본 함수는 단기 deviation 계산용 (예: 5분봉 VWAP).
    """
    if window <= 0:
        raise ValueError("window must be > 0")
    if not bars:
        return None
    return vwap_of(bars[-window:])


def vwap_deviation_pct(price: float, vwap: float | None) -> float | None:
    """VWAP 대비 가격 괴리율 (%, signed).

    vwap이 None 또는 0이면 None — 호출자는 None을 받아 NEEDS_DATA 등 안전
    분기를 한다. 가격이 VWAP보다 높으면 양수, 낮으면 음수.
    """
    if vwap is None or vwap == 0:
        return None
    return (price - vwap) / vwap * 100.0


# ---------- 거래량 / 유동성 가드 ----------


@dataclass(frozen=True)
class LiquidityCheck:
    """거래량 / 거래대금 가드 결과.

    `ok=True`면 통과, 아니면 `reason`이 사유. 호출자는 reason을 audit/UI surface.
    """
    ok:           bool
    avg_volume:   float = 0.0
    avg_turnover: float = 0.0
    reason:       str | None = None


def average_volume(bars: list[Bar], *, window: int | None = None) -> float:
    """평균 거래량. window=None이면 전체. 빈 리스트면 0."""
    target = bars if window is None else bars[-window:]
    if not target:
        return 0.0
    return sum(b.volume for b in target) / len(target)


def average_turnover(bars: list[Bar], *, window: int | None = None) -> float:
    """평균 거래대금 (close × volume). window=None이면 전체. 빈 리스트면 0."""
    target = bars if window is None else bars[-window:]
    if not target:
        return 0.0
    return sum(b.close * b.volume for b in target) / len(target)


def check_liquidity(
    bars:             list[Bar],
    *,
    window:           int   = 20,
    min_avg_volume:   float = 0.0,
    min_avg_turnover: float = 0.0,
) -> LiquidityCheck:
    """거래량 적은 종목에서의 VWAP 왜곡 방지 가드.

    avg_volume / avg_turnover가 임계 미만이면 LOW_LIQUIDITY로 분류. VWAP이
    소수의 큰 체결로 왜곡되는 케이스를 호출자가 reject할 수 있게 한다.

    - `window`: 평균 산출 윈도우 (최근 N봉, 기본 20).
    - `min_avg_volume` / `min_avg_turnover`: 둘 중 하나라도 미만이면 fail.
      0이면 해당 축은 검사하지 않음.
    """
    if not bars:
        return LiquidityCheck(ok=False, reason="bars empty")
    avg_vol  = average_volume(bars, window=window)
    avg_to   = average_turnover(bars, window=window)
    if min_avg_volume > 0 and avg_vol < min_avg_volume:
        return LiquidityCheck(
            ok=False, avg_volume=avg_vol, avg_turnover=avg_to,
            reason=f"LOW_LIQUIDITY: avg_volume {avg_vol:.0f} < {min_avg_volume:.0f}",
        )
    if min_avg_turnover > 0 and avg_to < min_avg_turnover:
        return LiquidityCheck(
            ok=False, avg_volume=avg_vol, avg_turnover=avg_to,
            reason=f"LOW_LIQUIDITY: avg_turnover {avg_to:.0f} < {min_avg_turnover:.0f}",
        )
    return LiquidityCheck(ok=True, avg_volume=avg_vol, avg_turnover=avg_to)

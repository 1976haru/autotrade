"""Stress test 시나리오 — 백테스트 입력에 대한 deterministic 변형 함수.

5종 시나리오 (주식 단타 기준):
- 시세 지연 (LATENCY)
- 체결 거부 (FILL_REJECTION)
- 데이터 누락 (DATA_MISSING)
- 동일 종목 신호 과다 (SIGNAL_OVERLOAD)
- 상관관계 높은 종목 동시 진입 (HIGH_CORRELATION)

각 시나리오는 *bar 시퀀스* 를 입력으로 받아 변형된 bar 시퀀스 또는 메타데이터
를 반환. 본 모듈은 *순수* — broker / network / DB 의존성 0건.

CLAUDE.md 정적 grep 가드: broker / OrderExecutor / route_order / 외부 API
client import 0건. 실 주문 / 한투 API / 거래소 API 호출 0건.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum

from app.backtest.types import Bar


class StressScenario(StrEnum):
    """5종 시나리오 enum.

    BUY/SELL/HOLD 같은 주문 라벨 0건 (CLAUDE.md invariant).
    """
    LATENCY            = "latency"
    FILL_REJECTION     = "fill_rejection"
    DATA_MISSING       = "data_missing"
    SIGNAL_OVERLOAD    = "signal_overload"
    HIGH_CORRELATION   = "high_correlation"


@dataclass(frozen=True)
class ScenarioParams:
    """시나리오 파라미터 — 기본값은 *온화한 스트레스*."""
    # 시세 지연: 마지막 N개 bar 의 timestamp 가 동일하게 stale.
    latency_stale_bars:        int = 3
    # 체결 거부: BUY 신호 N개 중 1개 거부 → bar 일부를 drop 으로 simulate.
    fill_rejection_drop_rate:  float = 0.20
    # 데이터 누락: 전체 bar 의 N% drop.
    data_missing_drop_rate:    float = 0.15
    # 동일 종목 신호 과다: 연속 N개 bar 가 동일 close (signal storm 유도).
    signal_overload_flat_bars: int = 10
    # 상관관계: 2번째 symbol 의 close 가 1번째 symbol close × k.
    high_correlation_factor:   float = 1.0


# ----------------------------------------------------------------------
# 변형 함수 — 각 시나리오를 deterministic 하게 적용
# ----------------------------------------------------------------------


def apply_latency(bars: list[Bar], stale_bars: int = 3) -> list[Bar]:
    """마지막 stale_bars 개의 timestamp 를 동일하게 만들어 *시세 정체* 시뮬.

    stale_bars <= 0 또는 bars 길이 < 2 이면 입력 그대로 반환.
    """
    if stale_bars <= 0 or len(bars) < 2:
        return list(bars)
    n = min(stale_bars, len(bars) - 1)
    cutoff = len(bars) - n
    out: list[Bar] = list(bars[:cutoff])
    stale_ts = bars[cutoff - 1].timestamp
    for b in bars[cutoff:]:
        out.append(
            Bar(
                symbol=b.symbol,
                timestamp=stale_ts,
                open=b.open,
                high=b.high,
                low=b.low,
                close=b.close,
                volume=b.volume,
            )
        )
    return out


def apply_fill_rejection(bars: list[Bar], drop_rate: float = 0.20) -> list[Bar]:
    """drop_rate 비율로 일부 bar 를 제거 (체결 누락 simulate).

    deterministic — 매 N번째 bar 를 drop. drop_rate <= 0 이면 입력 그대로.
    drop_rate >= 1.0 이면 빈 리스트.
    """
    if drop_rate <= 0:
        return list(bars)
    if drop_rate >= 1.0:
        return []
    step = max(2, int(round(1.0 / drop_rate)))
    return [b for i, b in enumerate(bars) if (i + 1) % step != 0]


def apply_data_missing(bars: list[Bar], drop_rate: float = 0.15) -> list[Bar]:
    """drop_rate 비율로 *연속 구간* 제거 (데이터 피드 끊김 simulate).

    deterministic — 시퀀스 중간의 한 구간을 drop. drop_rate >= 1.0 이면 빈 리스트.
    """
    if drop_rate <= 0:
        return list(bars)
    if drop_rate >= 1.0:
        return []
    if len(bars) < 4:
        return list(bars)
    drop_count = max(1, int(len(bars) * drop_rate))
    start = max(1, len(bars) // 3)
    end = min(len(bars) - 1, start + drop_count)
    return list(bars[:start]) + list(bars[end:])


def apply_signal_overload(
    bars: list[Bar], flat_bars: int = 10
) -> list[Bar]:
    """전반부 N봉을 close 동일 + 후반부 강한 진동 → 신호 storm 유도.

    flat_bars <= 0 또는 bars 부족 시 입력 그대로.
    """
    if flat_bars <= 0 or len(bars) < flat_bars + 4:
        return list(bars)
    out: list[Bar] = []
    flat_close = bars[0].close
    for i, b in enumerate(bars):
        if i < flat_bars:
            out.append(
                Bar(
                    symbol=b.symbol,
                    timestamp=b.timestamp,
                    open=flat_close,
                    high=flat_close,
                    low=flat_close,
                    close=flat_close,
                    volume=b.volume,
                )
            )
        else:
            # 후반부 — alternating high/low → 강한 oscillation
            shock = flat_close * 2 // 100  # 2% 폭
            close = flat_close + shock if i % 2 == 0 else flat_close - shock
            out.append(
                Bar(
                    symbol=b.symbol,
                    timestamp=b.timestamp,
                    open=b.open,
                    high=max(close, b.open) + 1,
                    low=min(close, b.open) - 1,
                    close=close,
                    volume=b.volume,
                )
            )
    return out


def apply_high_correlation(
    bars_a: list[Bar],
    symbol_b: str,
    factor: float = 1.0,
) -> list[Bar]:
    """bars_a 와 거의 동일 거동의 symbol_b bars 생성 (상관계수 ≈ 1.0).

    factor 만큼 close 를 곱해서 새로운 symbol_b bar 시퀀스 반환. 두 시퀀스가
    동시 진입하는지 백테스트로 확인하기 위한 입력.
    """
    if factor <= 0:
        factor = 1.0
    out: list[Bar] = []
    for b in bars_a:
        new_close = max(1, int(b.close * factor))
        new_open = max(1, int(b.open * factor))
        new_high = max(new_close, new_open) + 1
        new_low = min(new_close, new_open) - 1
        out.append(
            Bar(
                symbol=symbol_b,
                timestamp=b.timestamp,
                open=new_open,
                high=new_high,
                low=max(1, new_low),
                close=new_close,
                volume=b.volume,
            )
        )
    return out


def transform(
    bars: list[Bar],
    scenario: StressScenario,
    params: ScenarioParams | None = None,
) -> list[Bar]:
    """단일 bar 시퀀스 + 시나리오 enum → 변형된 bar 시퀀스.

    HIGH_CORRELATION 은 *2개 시퀀스* 가 필요하므로 본 단일-입력 API 에서는
    bars 를 그대로 반환 — apply_high_correlation() 직접 호출 필요.
    """
    p = params or ScenarioParams()
    if scenario == StressScenario.LATENCY:
        return apply_latency(bars, p.latency_stale_bars)
    if scenario == StressScenario.FILL_REJECTION:
        return apply_fill_rejection(bars, p.fill_rejection_drop_rate)
    if scenario == StressScenario.DATA_MISSING:
        return apply_data_missing(bars, p.data_missing_drop_rate)
    if scenario == StressScenario.SIGNAL_OVERLOAD:
        return apply_signal_overload(bars, p.signal_overload_flat_bars)
    if scenario == StressScenario.HIGH_CORRELATION:
        # 단일 입력에서는 변형 없음 — 호출자가 apply_high_correlation 직접 호출.
        return list(bars)
    raise ValueError(f"unknown scenario: {scenario!r}")

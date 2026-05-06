"""Futures simulation engine (151, MUST).

가상 선물 환경의 산식 모음. 실거래 broker가 비활성화된 상태에서
`MockFuturesBroker`가 본 모듈로 leverage / margin / 강제청산 / 수수료 / 슬리피지를
시뮬레이션한다. 실거래 broker는 별도 PR에서만 활성화되며, 본 모듈은
시뮬레이션 전용이라는 invariant를 유지한다.

설계 결정:
- 가격은 정수(원). margin도 정수(원). 비율(margin %, leverage)은 float.
- mark price 변동 시 unrealized_pnl 산출은 caller 책임 (read-only). 본 모듈은
  *결정* (강제청산 여부) + *계산* (margin / liquidation price)만 제공.
- LONG 진입가 vs SHORT 진입가의 strict한 부호 처리는 caller가 FuturesSide /
  FuturesPositionSide enum으로 보장.
"""

from dataclasses import dataclass

from app.futures.types import FuturesPosition, FuturesPositionSide


@dataclass(frozen=True)
class FuturesSimulationParams:
    """가상 선물 거래의 산식 파라미터. 운영자가 시나리오 분석용으로 조정 가능.

    실거래 KIS 선물의 실제 증거금 / 수수료 / 강제청산 임계와는 다를 수 있으며,
    `docs/futures_simulation_report.md`에 명시된 값과 매핑.
    """
    # 5x — KOSPI200 선물 평균. max는 사용자가 의도적으로 올릴 수 없도록 가드.
    default_leverage:    float = 5.0
    max_leverage:        float = 10.0
    # initial margin = notional / leverage. 위 leverage가 5x면 20%.
    # maintenance margin = initial * 50% (5x 가정). 강제청산 임계 산출에 사용.
    maintenance_margin_pct: float = 10.0   # notional 대비 %
    fee_bps:             int   = 2          # 0.02% (왕복 4bps)
    slippage_bps:        int   = 5          # 0.05%


def compute_initial_margin(*, notional: int, leverage: float) -> int:
    """초기 증거금 = 명목금액 / 레버리지. 정수 원으로 round-up."""
    if leverage <= 0:
        raise ValueError("leverage must be positive")
    if notional <= 0:
        raise ValueError("notional must be positive")
    margin = notional / leverage
    # 증거금 부족 사고를 줄이기 위해 ceil 측 — 0.5원이라도 부족하면 거부 권장.
    return int(margin) if margin == int(margin) else int(margin) + 1


def compute_liquidation_price(
    *,
    side:                  FuturesPositionSide,
    entry_price:           int,
    leverage:              float,
    maintenance_margin_pct: float,
) -> int:
    """강제청산 가격.

    LONG: 진입가에서 -((1/leverage) - mm_pct/100)만큼 하락하면 청산.
    SHORT: 반대로 +(...)만큼 상승하면 청산.

    예: leverage=5, mm=10 → loss buffer = 1/5 - 10/100 = 0.20 - 0.10 = 0.10.
    LONG 진입가 1000 → liquidation ≈ 900. SHORT 1000 → 1100.
    """
    if entry_price <= 0:
        raise ValueError("entry_price must be positive")
    if leverage <= 0:
        raise ValueError("leverage must be positive")

    initial_margin_ratio  = 1.0 / leverage
    maintenance_ratio     = maintenance_margin_pct / 100.0
    loss_buffer_ratio     = max(0.0, initial_margin_ratio - maintenance_ratio)

    if side == FuturesPositionSide.LONG:
        liquidation = entry_price * (1.0 - loss_buffer_ratio)
    else:
        liquidation = entry_price * (1.0 + loss_buffer_ratio)
    # int rounding — 호가 step 단위는 caller가 별도로 처리.
    return int(round(liquidation))


def should_force_liquidate(
    position: FuturesPosition,
    mark_price: int,
) -> bool:
    """현재 mark price가 강제청산 임계를 넘었는지.

    LONG은 mark ≤ liquidation, SHORT은 mark ≥ liquidation. liquidation_price
    가 None이면 (라이브 stub 등) 본 함수는 False — 안전 측 기본값.
    """
    if position.liquidation_price is None:
        return False
    if position.side == FuturesPositionSide.LONG:
        return mark_price <= position.liquidation_price
    return mark_price >= position.liquidation_price


def apply_slippage(*, price: int, side: str, slippage_bps: int) -> int:
    """주식 fill_engine과 동일한 단순 슬리피지 — BUY/LONG 위로, SELL/SHORT 아래로."""
    if slippage_bps <= 0:
        return price
    delta = max(1, int(price * slippage_bps / 10_000))
    if side in ("BUY", "LONG"):
        return price + delta
    return max(1, price - delta)


def compute_fee(*, notional: int, fee_bps: int) -> int:
    """단순 비례 수수료. 한 방향의 명목에 대해 fee_bps."""
    if fee_bps <= 0:
        return 0
    return max(1, int(notional * fee_bps / 10_000))


def realized_pnl_on_close(
    *,
    side:        FuturesPositionSide,
    quantity:    int,
    entry_price: int,
    exit_price:  int,
    multiplier:  int = 1,
) -> int:
    """LONG: (exit - entry) * qty * multiplier. SHORT: 부호 반대.

    수수료/슬리피지는 caller가 별도로 빼야 한다 (본 함수는 raw price PnL).
    """
    if quantity <= 0:
        return 0
    if side == FuturesPositionSide.LONG:
        return (exit_price - entry_price) * quantity * multiplier
    return (entry_price - exit_price) * quantity * multiplier

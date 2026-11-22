"""빠른 청산(fast clearing) 결정 helper — 보유 *롱* 포지션을 *언제 강제 청산*할지
판단하는 PURE 함수.

배경 (2026-06-03 운영자 요청 STEP 2-3):
  Agent Council 은 보유 포지션에 SELL 신호를 *직접 낼 때만* 청산한다. council 이
  HOLD 를 유지하면 포지션이 무기한 보유될 수 있어, 단타 회전(빠른 청산)이 안 된다.
  본 helper 는 익절/손절 도달, 보유시간 상한(time-stop), 장 마감 전 정리(EOD)를
  *권고* 한다.

설계 원칙 (CLAUDE.md 절대 원칙 준수):
  - 본 함수는 *청산 권고(advisory)* 만 반환한다. broker / OrderExecutor /
    route_order / 주문 객체를 만들지 *않는다* — 실제 SELL 은 기존 sanctioned
    경로(`execute_kis_paper_auto_order` → route_order → RiskManager →
    PermissionGate → OrderExecutor)가 수행한다.
  - `FastClearDecision.is_order_signal` 은 *항상* False (dataclass 가드).
  - **backward-compatible**: 모든 임계가 0/None 이면 `NO_ACTION` — 현재 동작
    완전 보존. 운영자가 임계를 켤 때만 활성화된다.
  - **손실 방어 우선**: 손절을 익절보다 먼저 평가한다 (CLAUDE.md — 수익률보다
    손실 방어 우선).

★ 비용 주의 (운영자): 보유시간 상한·손절을 *너무 타이트하게* 잡으면 잦은 청산
  → 왕복 거래비용(거래세 0.20% + 수수료 + 슬리피지 ≈ 33bps)이 수익을 잠식한다.
  본 helper 는 *주어진* 임계로 판단만 하며, 임계 자체는 운영자가 균형 있게 설정.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from enum import StrEnum

from app.scheduler.market_clock import KOREAN_MARKET_CLOSE, to_kst


class FastClearReason(StrEnum):
    NONE        = "NONE"
    STOP_LOSS   = "STOP_LOSS"
    TAKE_PROFIT = "TAKE_PROFIT"
    EOD_FLATTEN = "EOD_FLATTEN"
    TIME_STOP   = "TIME_STOP"


@dataclass(frozen=True)
class FastClearDecision:
    """보유 포지션 강제 청산 권고. *주문 신호가 아니다*."""

    should_clear: bool
    reason:       FastClearReason
    detail:       str = ""
    is_order_signal: bool = field(default=False)

    def __post_init__(self) -> None:
        if self.is_order_signal:
            raise ValueError("FastClearDecision.is_order_signal must be False (advisory only)")

    def to_dict(self) -> dict:
        return {
            "should_clear":    bool(self.should_clear),
            "reason":          self.reason.value,
            "detail":          self.detail,
            "is_order_signal": False,
        }


def _to_num(v: object) -> float | None:
    if v is None:
        return None
    try:
        n = float(v)
    except (TypeError, ValueError):
        return None
    return n


def minutes_to_market_close(now: datetime) -> float:
    """KST 정규장 마감(15:30)까지 남은 분. 이미 지났으면 음수, 장 시작 전이면 양수."""
    kst = to_kst(now)
    close_dt = kst.replace(
        hour=KOREAN_MARKET_CLOSE.hour, minute=KOREAN_MARKET_CLOSE.minute,
        second=0, microsecond=0,
    )
    return (close_dt - kst).total_seconds() / 60.0


def evaluate_fast_clear(
    *,
    last_price:                       float | int | None,
    take_profit_price:                float | int | None = None,
    stop_loss_price:                  float | int | None = None,
    holding_minutes:                  float | None       = None,
    max_holding_minutes:              int                = 0,
    now:                              datetime | None    = None,
    eod_flatten_minutes_before_close: int                = 0,
) -> FastClearDecision:
    """보유 롱 포지션을 강제 청산할지 판단 (PURE, side-effect 0).

    우선순위: **손절 > 익절 > EOD > 시간상한**. 모든 임계가 0/None 이면
    `NO_ACTION` (현재 동작 보존). 반환은 *권고* 일 뿐 — 호출자가 기존 sanctioned
    SELL 경로로 보낸다.
    """
    lp = _to_num(last_price)

    # 1) 손절 — 손실 방어 우선.
    sl = _to_num(stop_loss_price)
    if lp is not None and sl is not None and sl > 0 and lp <= sl:
        return FastClearDecision(True, FastClearReason.STOP_LOSS,
                                 f"현재가 {lp:.0f} ≤ 손절가 {sl:.0f}")

    # 2) 익절.
    tp = _to_num(take_profit_price)
    if lp is not None and tp is not None and tp > 0 and lp >= tp:
        return FastClearDecision(True, FastClearReason.TAKE_PROFIT,
                                 f"현재가 {lp:.0f} ≥ 익절가 {tp:.0f}")

    # 3) 장 마감 전 정리(EOD) — 오버나잇 미보유.
    if eod_flatten_minutes_before_close and eod_flatten_minutes_before_close > 0 and now is not None:
        mtc = minutes_to_market_close(now)
        if 0 <= mtc <= float(eod_flatten_minutes_before_close):
            return FastClearDecision(True, FastClearReason.EOD_FLATTEN,
                                     f"마감 {mtc:.0f}분 전 ≤ 설정 {eod_flatten_minutes_before_close}분")

    # 4) 보유시간 상한(time-stop).
    if max_holding_minutes and max_holding_minutes > 0 and holding_minutes is not None:
        if float(holding_minutes) >= float(max_holding_minutes):
            return FastClearDecision(True, FastClearReason.TIME_STOP,
                                     f"보유 {float(holding_minutes):.0f}분 ≥ 상한 {max_holding_minutes}분")

    return FastClearDecision(False, FastClearReason.NONE, "")


__all__ = [
    "FastClearReason",
    "FastClearDecision",
    "evaluate_fast_clear",
    "minutes_to_market_close",
]

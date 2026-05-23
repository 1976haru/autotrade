"""3-02: 보유 포지션 context — Agent Council 의 SELL(청산) 판단 기준.

자동매매는 매수보다 *청산/매도* 가 핵심이다. SELL 은 **보유 종목(held_position)을
기준으로 한 청산** 이며 신규 숏 진입이 아니다. 본 모듈은 보유 포지션 정보(수량/
평단/현재가/손절·익절)와 청산 트리거(stop_loss/take_profit/장마감)를 정의한다.

## 절대 invariant (CLAUDE.md)
- SELL 은 *보유 청산만* — `is_short_entry=False` / `short_position=False` 영구.
- 보유 포지션이 없으면(held_position=False 또는 sellable<=0) SELL 금지.
- 실제 계좌 잔고가 아니라 Paper / virtual portfolio 기준 — 실 계좌 조회 0건.
- broker / OrderExecutor / route_order import 0건.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

# 보유 포지션 기반 SELL 트리거 reason_code (sell_reason.py 와 일관).
SELL_STOP_LOSS         = "STOP_LOSS"
SELL_TAKE_PROFIT       = "TAKE_PROFIT"
SELL_TRAILING_STOP     = "TRAILING_STOP"
SELL_MARKET_CLOSE_EXIT = "MARKET_CLOSE_EXIT"

# 보유 없음 / 손절·익절 미설정 차단 reason.
NO_HELD_POSITION_FOR_SELL = "NO_HELD_POSITION_FOR_SELL"
SELL_QUANTITY_EXCEEDS_POSITION = "SELL_QUANTITY_EXCEEDS_POSITION"
STOP_LOSS_NOT_CONFIGURED = "STOP_LOSS_NOT_CONFIGURED"
TAKE_PROFIT_NOT_CONFIGURED = "TAKE_PROFIT_NOT_CONFIGURED"
TRAILING_STOP_NOT_CONFIGURED = "TRAILING_STOP_NOT_CONFIGURED"
TRAILING_STOP_NOT_IN_PROFIT = "TRAILING_STOP_NOT_IN_PROFIT"   # 최고가 ≤ 평단


def _f(v: Any) -> float | None:
    try:
        return float(v) if v is not None else None
    except (TypeError, ValueError):
        return None


def _parse_hhmm(v: Any) -> int | None:
    """\"HH:MM\" (KST) → 자정 기준 분(minutes). 파싱 실패는 None."""
    if v is None:
        return None
    s = str(v).strip()
    if ":" not in s:
        return None
    try:
        hh, mm = s.split(":")[:2]
        h, m = int(hh), int(mm)
    except (TypeError, ValueError):
        return None
    if not (0 <= h <= 23 and 0 <= m <= 59):
        return None
    return h * 60 + m


def _norm_pct(v: Any) -> float | None:
    """손절/익절 % 정규화 → percent (2.0 = 2%). 0<v<=1 은 ratio 로 보고 *100.

    음수/0/None/비정상은 None(미설정). 예: 2.0→2.0, 0.02→2.0, 1.5→1.5, 0.5→50.0.
    """
    f = _f(v)
    if f is None or f <= 0:
        return None
    return round(f * 100.0, 6) if f <= 1.0 else f


@dataclass(frozen=True)
class PositionContext:
    """보유 포지션 스냅샷 — SELL 판단 입력 (Paper/virtual portfolio 기준)."""

    held_position:       bool = False
    symbol:              str | None = None
    quantity:            int = 0
    available_quantity:  int = 0
    average_entry_price: float | None = None
    current_price:       float | None = None
    stop_loss:           float | None = None       # 절대 손절가 (최우선)
    take_profit:         float | None = None       # 절대 익절가 (최우선)
    # 3-03: 손절/익절 % — 절대가 없을 때 average_entry_price 기준으로 계산.
    #       **내부 표준은 percent** (2.0 = 2%). 0<v<=1 입력(0.02)은 ratio 로 보고
    #       *100 정규화(0.02 → 2.0%). 음수/0/None 은 미설정.
    stop_loss_pct:       float | None = None
    take_profit_pct:     float | None = None
    # 3-05: 트레일링 스탑 — high_watermark(장중 최고가) 대비 trailing_stop_pct 하락.
    high_watermark:      float | None = None
    trailing_stop_pct:   float | None = None
    market_time_phase:   str | None = None       # PRE_MARKET/.../CLOSING/... (legacy)
    market_close_exit_enabled: bool = False
    # 3-06: 장마감 강제청산 — KST 시각이 force_exit_time_kst 이상이면 청산 후보.
    #       오버나이트 허용 전략(allow_overnight=True)은 강제청산 미발동.
    current_time_kst:    str | None = None        # "HH:MM" (KST). caller 가 채움.
    force_exit_time_kst: str = "15:20"            # 기본 강제청산 시각.
    market_close_time_kst: str = "15:30"
    allow_overnight:     bool = False

    # SELL 은 보유 청산만 — 숏 진입 아님 (영구 False).
    is_short_entry:      bool = False
    short_position:      bool = False

    def __post_init__(self) -> None:
        if self.is_short_entry is not False:
            raise ValueError("PositionContext.is_short_entry must be False (no short entry)")
        if self.short_position is not False:
            raise ValueError("PositionContext.short_position must be False")

    @property
    def sellable_quantity(self) -> int:
        """청산 가능 수량 — available_quantity 우선, 없으면 quantity. 0 미만 0."""
        avail = int(self.available_quantity or 0)
        qty = int(self.quantity or 0)
        base = avail if avail > 0 else qty
        return max(0, min(base, qty if qty > 0 else base))

    @property
    def is_sellable(self) -> bool:
        return bool(self.held_position) and self.sellable_quantity > 0

    @property
    def unrealized_return_pct(self) -> float | None:
        entry = _f(self.average_entry_price)
        cur = _f(self.current_price)
        if entry is None or entry <= 0 or cur is None:
            return None
        return round((cur - entry) / entry * 100.0, 4)

    def resolve_stop_loss_price(self, *, default_stop_loss_pct: float | None = None
                                ) -> tuple[float | None, str]:
        """손절가(절대) 해소 — 우선순위 + source 반환.

        1) `stop_loss`(절대) → ("absolute") 2) `stop_loss_pct`(평단 기준) →
        ("pct") 3) `default_stop_loss_pct`(risk_profile) → ("default_pct")
        4) 없음 → (None, "STOP_LOSS_NOT_CONFIGURED").
        """
        sl = _f(self.stop_loss)
        if sl is not None and sl > 0:
            return sl, "absolute"
        entry = _f(self.average_entry_price)
        for pct, src in ((self.stop_loss_pct, "pct"),
                         (default_stop_loss_pct, "default_pct")):
            p = _norm_pct(pct)
            if p is not None and entry is not None and entry > 0:
                return round(entry * (1.0 - p / 100.0), 4), src
        return None, "STOP_LOSS_NOT_CONFIGURED"

    def resolve_take_profit_price(self, *, default_take_profit_pct: float | None = None
                                  ) -> float | None:
        tp = _f(self.take_profit)
        if tp is not None and tp > 0:
            return tp
        entry = _f(self.average_entry_price)
        for pct in (self.take_profit_pct, default_take_profit_pct):
            p = _norm_pct(pct)
            if p is not None and entry is not None and entry > 0:
                return round(entry * (1.0 + p / 100.0), 4)
        return None

    def resolve_trailing_stop_price(self, *, default_trailing_stop_pct: float | None = None
                                    ) -> tuple[float | None, str]:
        """트레일링 스탑가(절대) 해소 + source/사유 반환.

        high_watermark(장중 최고가) × (1 − pct/100). 단, **수익 보호 구간** —
        high_watermark > average_entry_price 일 때만 유효. 그 외:
        - high_watermark 없음/≤ 평단 → (None, "TRAILING_STOP_NOT_IN_PROFIT")
        - pct(자체 > default) 없음 → (None, "TRAILING_STOP_NOT_CONFIGURED").
        high_watermark 는 *임의 추정하지 않는다* (없으면 미발동).
        """
        hwm = _f(self.high_watermark)
        entry = _f(self.average_entry_price)
        p = _norm_pct(self.trailing_stop_pct) or _norm_pct(default_trailing_stop_pct)
        if p is None:
            return None, TRAILING_STOP_NOT_CONFIGURED
        if hwm is None or hwm <= 0 or (entry is not None and hwm <= entry):
            return None, TRAILING_STOP_NOT_IN_PROFIT
        return round(hwm * (1.0 - p / 100.0), 4), "pct"

    def is_market_close_exit_triggered(self) -> bool:
        """장마감 강제청산 트리거 — 보유 + 강제청산 enabled + (시각 ≥ 강제청산시각).

        오버나이트 허용(allow_overnight=True)이면 항상 False. KST 시각 비교
        (`current_time_kst` ≥ `force_exit_time_kst`). legacy `market_time_phase
        =="CLOSING"` 도 트리거로 인정 (하위호환).
        """
        if not self.is_sellable or self.allow_overnight:
            return False
        if not self.market_close_exit_enabled:
            return False
        cur = _parse_hhmm(self.current_time_kst)
        fe = _parse_hhmm(self.force_exit_time_kst)
        if cur is not None and fe is not None and cur >= fe:
            return True
        return str(self.market_time_phase or "").upper() == "CLOSING"

    def to_dict(self) -> dict[str, Any]:
        return {
            "held_position":       bool(self.held_position),
            "symbol":              self.symbol,
            "quantity":            int(self.quantity or 0),
            "available_quantity":  int(self.available_quantity or 0),
            "sellable_quantity":   self.sellable_quantity,
            "average_entry_price": _f(self.average_entry_price),
            "current_price":       _f(self.current_price),
            "stop_loss":           _f(self.stop_loss),
            "take_profit":         _f(self.take_profit),
            "stop_loss_pct":       _norm_pct(self.stop_loss_pct),
            "take_profit_pct":     _norm_pct(self.take_profit_pct),
            "high_watermark":      _f(self.high_watermark),
            "trailing_stop_pct":   _norm_pct(self.trailing_stop_pct),
            "resolved_stop_loss_price": self.resolve_stop_loss_price()[0],
            "resolved_trailing_stop_price": self.resolve_trailing_stop_price()[0],
            "unrealized_return_pct": self.unrealized_return_pct,
            "market_time_phase":   self.market_time_phase,
            "current_time_kst":    self.current_time_kst,
            "force_exit_time_kst": self.force_exit_time_kst,
            "allow_overnight":     bool(self.allow_overnight),
            "market_close_exit_triggered": self.is_market_close_exit_triggered(),
            "is_short_entry":      False,
            "short_position":      False,
        }


def infer_position_sell_reason(
    position: PositionContext | None,
    *,
    default_stop_loss_pct: float | None = None,
    default_take_profit_pct: float | None = None,
    default_trailing_stop_pct: float | None = None,
) -> str | None:
    """보유 포지션 기반 *청산 트리거* reason_code (우선순위 적용). 없으면 None.

    우선순위: **STOP_LOSS > TAKE_PROFIT > TRAILING_STOP > MARKET_CLOSE_EXIT**.
    동시 성립(비정상)은 위 순서로 고정. 손절/익절/트레일링가는 각 resolver
    (절대 > pct > risk_profile default) 로 해소. 보유 없음/현재가 없음이면 None.
    트레일링은 high_watermark > 평단(수익 보호 구간)에서만 발동. 숏 진입 0건.
    """
    if position is None or not position.is_sellable:
        return None
    cur = _f(position.current_price)
    if cur is not None:
        sl, _src = position.resolve_stop_loss_price(default_stop_loss_pct=default_stop_loss_pct)
        if sl is not None and sl > 0 and cur <= sl:
            return SELL_STOP_LOSS
        tp = position.resolve_take_profit_price(default_take_profit_pct=default_take_profit_pct)
        if tp is not None and tp > 0 and cur >= tp:
            return SELL_TAKE_PROFIT
        ts, _tsrc = position.resolve_trailing_stop_price(
            default_trailing_stop_pct=default_trailing_stop_pct)
        if ts is not None and ts > 0 and cur <= ts:
            return SELL_TRAILING_STOP
    if position.is_market_close_exit_triggered():
        return SELL_MARKET_CLOSE_EXIT
    return None


def calculate_take_profit_price(
    position: PositionContext | None, *, default_take_profit_pct: float | None = None,
) -> tuple[float | None, str]:
    """익절가(절대) 해소 + source 반환.

    1) `take_profit`(절대) → ("absolute") 2) `take_profit_pct`(평단 기준) →
    ("pct") 3) `default_take_profit_pct`(risk_profile) → ("default_pct")
    4) 없음 → (None, "TAKE_PROFIT_NOT_CONFIGURED").
    """
    if position is None:
        return None, TAKE_PROFIT_NOT_CONFIGURED
    tp = _f(position.take_profit)
    if tp is not None and tp > 0:
        return tp, "absolute"
    entry = _f(position.average_entry_price)
    for pct, src in ((position.take_profit_pct, "pct"),
                     (default_take_profit_pct, "default_pct")):
        p = _norm_pct(pct)
        if p is not None and entry is not None and entry > 0:
            return round(entry * (1.0 + p / 100.0), 4), src
    return None, TAKE_PROFIT_NOT_CONFIGURED


def is_take_profit_triggered(
    position: PositionContext | None, *, default_take_profit_pct: float | None = None,
) -> dict[str, Any]:
    """익절 트리거 평가 결과 dict (triggered / reason_code / 가격들). 보유 청산 기준.

    **주의**: stop_loss 와 take_profit 이 동시에 걸리는 비정상 데이터에서는
    STOP_LOSS 우선 — 종합 SELL 판단은 `infer_position_sell_reason`(stop_loss 먼저)
    을 사용한다. 본 함수는 익절 단독 평가용.
    """
    out: dict[str, Any] = {
        "triggered": False, "reason_code": None,
        "entry_price": (_f(position.average_entry_price) if position else None),
        "current_price": (_f(position.current_price) if position else None),
        "take_profit_price": None, "take_profit_pct": None,
    }
    if position is None or not position.is_sellable:
        out["reason_code"] = NO_HELD_POSITION_FOR_SELL if position is not None else None
        return out
    tp_price, src = calculate_take_profit_price(
        position, default_take_profit_pct=default_take_profit_pct)
    out["take_profit_price"] = tp_price
    out["take_profit_pct"] = _norm_pct(position.take_profit_pct) \
        or _norm_pct(default_take_profit_pct)
    cur = _f(position.current_price)
    if tp_price is None:
        out["reason_code"] = TAKE_PROFIT_NOT_CONFIGURED
        return out
    if cur is not None and cur >= tp_price:
        out["triggered"] = True
        out["reason_code"] = SELL_TAKE_PROFIT
    return out


def is_trailing_stop_triggered(
    position: PositionContext | None, *, default_trailing_stop_pct: float | None = None,
) -> dict[str, Any]:
    """트레일링 스탑 트리거 평가 dict (triggered / reason_code / 최고가/트레일가).

    high_watermark > 평단(수익 보호 구간)에서만 발동. 보유 없음/미설정/수익 구간
    아님이면 triggered=False + 사유 reason_code. STOP_LOSS/TAKE_PROFIT 동시 성립 시
    종합 판단은 `infer_position_sell_reason`(우선순위)을 사용.
    """
    out: dict[str, Any] = {
        "triggered": False, "reason_code": None,
        "entry_price": (_f(position.average_entry_price) if position else None),
        "current_price": (_f(position.current_price) if position else None),
        "high_watermark": (_f(position.high_watermark) if position else None),
        "trailing_stop_price": None,
        "trailing_stop_pct": (_norm_pct(position.trailing_stop_pct)
                              or _norm_pct(default_trailing_stop_pct)) if position else None,
    }
    if position is None or not position.is_sellable:
        out["reason_code"] = NO_HELD_POSITION_FOR_SELL if position is not None else None
        return out
    ts_price, src = position.resolve_trailing_stop_price(
        default_trailing_stop_pct=default_trailing_stop_pct)
    out["trailing_stop_price"] = ts_price
    if ts_price is None:
        out["reason_code"] = src   # TRAILING_STOP_NOT_CONFIGURED / NOT_IN_PROFIT
        return out
    cur = _f(position.current_price)
    if cur is not None and cur <= ts_price:
        out["triggered"] = True
        out["reason_code"] = SELL_TRAILING_STOP
    return out


def cap_sell_quantity(requested: int, position: PositionContext | None) -> int:
    """SELL 수량을 보유(청산 가능) 수량 이하로 제한. position 없으면 requested 그대로."""
    req = int(requested or 0)
    if position is None:
        return max(0, req)
    return max(0, min(req, position.sellable_quantity))


__all__ = [
    "PositionContext", "infer_position_sell_reason", "cap_sell_quantity",
    "calculate_take_profit_price", "is_take_profit_triggered",
    "is_trailing_stop_triggered",
    "SELL_STOP_LOSS", "SELL_TAKE_PROFIT", "SELL_TRAILING_STOP", "SELL_MARKET_CLOSE_EXIT",
    "NO_HELD_POSITION_FOR_SELL", "SELL_QUANTITY_EXCEEDS_POSITION",
    "STOP_LOSS_NOT_CONFIGURED", "TAKE_PROFIT_NOT_CONFIGURED",
    "TRAILING_STOP_NOT_CONFIGURED", "TRAILING_STOP_NOT_IN_PROFIT",
]

"""1분봉 intrabar execution realism — 5분봉 체결 착시 감소 (백테스트 전용).

5분봉 한 캔들 안에서 stop/target 이 모두 닿으면 선후가 불명확하다. 1분봉이 있으면
1분봉으로 replay 해 실제에 가까운 체결 순서를 판정하고, 없으면 5분봉 fallback +
보수적 stop-first 를 적용한다.

본 모듈은 *순수 함수* — broker 주문 메서드 / 단일 주문 라우터 / 주문 실행기 / KIS API
import 0건, 실주문 0건. 백테스트 체결 시뮬레이션 전용.
IntrabarExecutionResult.is_live_authorization 항상 False.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from enum import Enum
from typing import Any, Sequence


class ExecutionSource(str, Enum):
    ONE_MINUTE_REPLAY = "ONE_MINUTE_REPLAY"
    FIVE_MINUTE_FALLBACK = "FIVE_MINUTE_FALLBACK"
    AMBIGUOUS_CONSERVATIVE = "AMBIGUOUS_CONSERVATIVE"


class ExecutionConfidence(str, Enum):
    HIGH = "HIGH"
    LOW = "LOW"


class ExitReason(str, Enum):
    TARGET_HIT = "TARGET_HIT"
    STOP_HIT = "STOP_HIT"
    EOD_EXIT = "EOD_EXIT"
    MAX_HOLD_EXIT = "MAX_HOLD_EXIT"
    TIME_STOP = "TIME_STOP"
    AMBIGUOUS_STOP_FIRST = "AMBIGUOUS_STOP_FIRST"


@dataclass(frozen=True)
class CostModel:
    """체결 비용 (bps). slippage 는 stress 로 7/10 도 가능."""
    commission_bps: float = 1.5      # per side
    tax_bps: float = 18.0            # sell leg only (KR)
    slippage_bps: float = 5.0        # per side


@dataclass(frozen=True)
class IntrabarExecutionResult:
    exit_time: str | None
    exit_price: float
    exit_reason: str
    gross_pnl: float
    net_pnl: float
    cost_paid: float
    slippage_paid: float
    tax_paid: float
    hold_minutes: float
    execution_source: str
    execution_confidence: str
    intrabar_notes: str = ""
    is_live_authorization: bool = False

    def __post_init__(self) -> None:
        if self.is_live_authorization is not False:
            raise ValueError("IntrabarExecutionResult.is_live_authorization must be False")

    def to_dict(self) -> dict[str, Any]:
        return {
            "exit_time": self.exit_time,
            "exit_price": round(self.exit_price, 4),
            "exit_reason": self.exit_reason,
            "gross_pnl": round(self.gross_pnl, 6),
            "net_pnl": round(self.net_pnl, 6),
            "cost_paid": round(self.cost_paid, 6),
            "slippage_paid": round(self.slippage_paid, 6),
            "tax_paid": round(self.tax_paid, 6),
            "hold_minutes": round(self.hold_minutes, 2),
            "execution_source": self.execution_source,
            "execution_confidence": self.execution_confidence,
            "intrabar_notes": self.intrabar_notes,
            "is_live_authorization": False,
        }


def _parse_ts(v: Any) -> datetime | None:
    if v is None:
        return None
    if isinstance(v, datetime):
        return v if v.tzinfo else v.replace(tzinfo=timezone.utc)
    try:
        dt = datetime.fromisoformat(str(v))
        return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)
    except Exception:  # noqa: BLE001
        return None


def _bar(b: Any) -> dict[str, Any]:
    """dict 또는 object → {ts, high, low, close, open}."""
    if isinstance(b, dict):
        g = b.get
    else:
        def g(k, d=None):  # type: ignore
            return getattr(b, k, d)
    return {
        "ts": _parse_ts(g("timestamp", g("ts"))),
        "high": float(g("high", 0) or 0),
        "low": float(g("low", 0) or 0),
        "close": float(g("close", 0) or 0),
        "open": float(g("open", 0) or 0),
    }


def _touches(side: str, bar: dict, stop: float, target: float) -> tuple[bool, bool]:
    """(stop_touched, target_touched) — BUY/SELL 방향별."""
    if side.upper() == "BUY":
        return (bar["low"] <= stop, bar["high"] >= target)
    # SELL (short): stop 위로, target 아래로.
    return (bar["high"] >= stop, bar["low"] <= target)


def _compute_costs(side: str, entry: float, exit_px: float, cost: CostModel
                   ) -> tuple[float, float, float, float, float]:
    """(gross, commission, slippage, tax, net) per 1 share."""
    s = side.upper()
    gross = (exit_px - entry) if s == "BUY" else (entry - exit_px)
    slippage = (entry + exit_px) * (cost.slippage_bps / 1e4)
    commission = (entry + exit_px) * (cost.commission_bps / 1e4)
    # tax: sell leg. BUY → exit 이 매도, SELL(short) → entry 가 매도.
    sell_leg = exit_px if s == "BUY" else entry
    tax = sell_leg * (cost.tax_bps / 1e4)
    net = gross - commission - slippage - tax
    return gross, commission, slippage, tax, net


def _bars_in_window(bars: Sequence[Any], entry_time: datetime | None,
                    max_hold_minutes: int) -> list[dict]:
    norm = [_bar(b) for b in (bars or [])]
    if entry_time is None:
        return norm
    end = entry_time + timedelta(minutes=max_hold_minutes)
    out = [b for b in norm if b["ts"] is not None
           and entry_time <= b["ts"] <= end]
    return out or norm  # 윈도우 매칭 실패 시 전체 사용(보수적)


def simulate_intrabar_execution(
    *,
    side: str,
    entry_time: Any,
    entry_price: float,
    stop_price: float,
    target_price: float,
    max_hold_minutes: int,
    bars_5m: Sequence[Any],
    bars_1m: Sequence[Any] | None = None,
    cost: CostModel | None = None,
    eod_time: Any | None = None,
) -> IntrabarExecutionResult:
    """5분봉 진입 신호를 1분봉(있으면)으로 replay 해 체결 판정.

    1분봉 없으면 5분봉 fallback + execution_confidence=LOW. 같은 캔들에서 stop/target
    동시 터치는 보수적 stop-first (AMBIGUOUS_STOP_FIRST).
    """
    cost = cost or CostModel()
    s = side.upper()
    et = _parse_ts(entry_time)
    eod = _parse_ts(eod_time)

    use_1m = bool(bars_1m)
    bars = _bars_in_window(bars_1m if use_1m else bars_5m, et, max_hold_minutes)
    source = (ExecutionSource.ONE_MINUTE_REPLAY if use_1m
              else ExecutionSource.FIVE_MINUTE_FALLBACK)
    confidence = ExecutionConfidence.HIGH if use_1m else ExecutionConfidence.LOW
    notes = "" if use_1m else "1분봉 없음 — 5분봉 fallback, 체결 정확도 낮음(LOW)."

    exit_reason = ExitReason.MAX_HOLD_EXIT
    exit_price = entry_price
    exit_ts: datetime | None = None

    for b in bars:
        if eod is not None and b["ts"] is not None and b["ts"] >= eod:
            exit_reason = ExitReason.EOD_EXIT
            exit_price = b["close"]
            exit_ts = b["ts"]
            break
        stop_t, target_t = _touches(s, b, stop_price, target_price)
        if stop_t and target_t:
            # 같은 캔들 내 동시 터치 → 보수적 stop-first.
            exit_reason = ExitReason.AMBIGUOUS_STOP_FIRST
            exit_price = stop_price
            exit_ts = b["ts"]
            if not use_1m:
                source = ExecutionSource.AMBIGUOUS_CONSERVATIVE
            notes = (notes + " 동시터치 → 보수적 stop-first.").strip()
            break
        if stop_t:
            exit_reason = ExitReason.STOP_HIT
            exit_price = stop_price
            exit_ts = b["ts"]
            break
        if target_t:
            exit_reason = ExitReason.TARGET_HIT
            exit_price = target_price
            exit_ts = b["ts"]
            break
    else:
        # 루프 정상 종료(touch 없음) → 마지막 bar close 로 max-hold 청산.
        if bars:
            exit_price = bars[-1]["close"]
            exit_ts = bars[-1]["ts"]
        exit_reason = ExitReason.MAX_HOLD_EXIT

    gross, commission, slippage, tax, net = _compute_costs(s, entry_price, exit_price, cost)
    hold_min = 0.0
    if et is not None and exit_ts is not None:
        hold_min = max(0.0, (exit_ts - et).total_seconds() / 60.0)

    return IntrabarExecutionResult(
        exit_time=exit_ts.isoformat() if exit_ts else None,
        exit_price=exit_price,
        exit_reason=exit_reason.value,
        gross_pnl=gross, net_pnl=net,
        cost_paid=commission, slippage_paid=slippage, tax_paid=tax,
        hold_minutes=hold_min,
        execution_source=source.value,
        execution_confidence=confidence.value,
        intrabar_notes=notes,
    )

"""P-24: KIS 모의 주문·체결 품질 로그.

KIS 모의 API 자동주문에서 주문 요청 → 접수 → 체결/미체결/부분체결/거절까지의
품질(체결 지연 / 슬리피지 / 거절 사유)을 decision episode 에 기록한다. 실전
전환 전 체결 품질 분석용.

## 절대 invariant (CLAUDE.md)
- 본 모듈은 *기록 전용* — broker / OrderExecutor / route_order / 외부 HTTP
  import 0건, 주문을 만들지 않는다.
- KIS 응답 *원문 전체* 저장 0건 — code/message 만 allowlist 추출.
- API key / app_secret / account_no / token 저장 0건 (allowlist + sanitize).
- `OrderQualityLog.is_live_authorization=False` / `contains_secret=False` 영구.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

# ── 주문 상태 표준 ──
STATUS_REQUESTED        = "REQUESTED"
STATUS_SUBMITTED        = "SUBMITTED"
STATUS_ACCEPTED         = "ACCEPTED"
STATUS_FILLED           = "FILLED"
STATUS_PARTIALLY_FILLED = "PARTIALLY_FILLED"
STATUS_UNFILLED         = "UNFILLED"
STATUS_REJECTED         = "REJECTED"
STATUS_CANCELLED        = "CANCELLED"
STATUS_EXPIRED          = "EXPIRED"
STATUS_DRY_RUN          = "DRY_RUN"
STATUS_ERROR            = "ERROR"


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _iso(dt: datetime | None) -> str | None:
    if dt is None:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.isoformat()


def _f(v: Any) -> float | None:
    try:
        return float(v) if v is not None else None
    except (TypeError, ValueError):
        return None


@dataclass(frozen=True)
class OrderQualityLog:
    """주문·체결 품질 — episode.kis_order_result.order_quality 에 저장되는 payload.

    KIS 응답은 code/message 만 allowlist 로 carry — 원문/계좌/secret 0건.
    """

    order_status:   str
    fill_status:    str | None = None
    requested_at:   str | None = None
    submitted_at:   str | None = None
    filled_at:      str | None = None
    request_price:  float | None = None
    avg_fill_price: float | None = None
    quantity:       int = 0
    filled_quantity: int = 0
    unfilled_quantity: int = 0
    partial_fill:   bool = False
    broker_order_no: str | None = None
    kis_response_code:    str | None = None    # allowlist (reason_code)
    kis_response_message: str | None = None    # allowlist (reason_message)
    latency_ms:     int | None = None
    slippage_bps:   float | None = None
    rejection_reason_code:    str | None = None
    rejection_reason_message: str | None = None
    data_status:    str = "OK"
    fill_polling:   dict[str, Any] = field(default_factory=dict)

    contains_secret:       bool = False
    is_live_authorization: bool = False

    def to_dict(self) -> dict[str, Any]:
        return {
            "order_status":      self.order_status,
            "fill_status":       self.fill_status,
            "requested_at":      self.requested_at,
            "submitted_at":      self.submitted_at,
            "filled_at":         self.filled_at,
            "request_price":     self.request_price,
            "avg_fill_price":    self.avg_fill_price,
            "quantity":          int(self.quantity),
            "filled_quantity":   int(self.filled_quantity),
            "unfilled_quantity": int(self.unfilled_quantity),
            "partial_fill":      bool(self.partial_fill),
            "broker_order_no":   self.broker_order_no,
            "kis_response_code":    self.kis_response_code,
            "kis_response_message": self.kis_response_message,
            "latency_ms":        self.latency_ms,
            "slippage_bps":      self.slippage_bps,
            "rejection_reason_code":    self.rejection_reason_code,
            "rejection_reason_message": self.rejection_reason_message,
            "data_status":       self.data_status,
            "fill_polling":      dict(self.fill_polling),
            "contains_secret":       False,
            "is_live_authorization": False,
        }


def _compute_slippage_bps(request_price, avg_fill_price, side) -> float | None:
    rp = _f(request_price)
    fp = _f(avg_fill_price)
    if rp is None or fp is None or rp <= 0:
        return None
    raw = (fp - rp) / rp * 10000.0
    # BUY 는 체결가 > 요청가 가 불리(양수 slippage), SELL 은 반대 — 부호를
    # *불리한 방향이 양수* 가 되도록 정규화.
    if str(side).upper() == "SELL":
        raw = -raw
    return round(raw, 2)


def _map_status(*, reason_code: str, dry_run: bool, submitted: bool,
                fill_status: str | None, filled_quantity: int, quantity: int) -> str:
    """KIS Paper 결과 → 표준 order_status."""
    from app.kis_paper.auto_executor import (
        BLOCKED_BY_PERMISSION_GATE,
        BLOCKED_BY_RISK_MANAGER,
        KIS_PAPER_DRY_RUN_OK,
        KIS_PAPER_ERROR,
    )
    rc = str(reason_code or "")
    if dry_run or rc == KIS_PAPER_DRY_RUN_OK:
        return STATUS_DRY_RUN
    if rc == KIS_PAPER_ERROR:
        return STATUS_ERROR
    if rc in (BLOCKED_BY_RISK_MANAGER, BLOCKED_BY_PERMISSION_GATE) or "REJECT" in rc:
        return STATUS_REJECTED
    if submitted:
        if fill_status == "FILLED" or (quantity > 0 and filled_quantity >= quantity):
            return STATUS_FILLED
        if filled_quantity > 0:
            return STATUS_PARTIALLY_FILLED
        return STATUS_SUBMITTED
    return STATUS_UNFILLED


def placeholder_fill_polling(*, enabled: bool = False) -> dict[str, Any]:
    """fill polling placeholder (체결조회 mock/제한 단계용 구조)."""
    return {
        "enabled":           bool(enabled),
        "poll_count":        0,
        "last_polled_at":    None,
        "final_status":      None,
        "filled_quantity":   0,
        "unfilled_quantity": 0,
        "partial_fill":      False,
    }


def build_order_quality_log(
    *,
    result: Any,
    decision: Any,
    requested_at: datetime | None = None,
    responded_at: datetime | None = None,
    fill_polling: dict[str, Any] | None = None,
) -> OrderQualityLog:
    """KisPaperAutoResult + 타이밍 → OrderQualityLog (best-effort, 예외 0).

    `result` 는 KisPaperAutoResult (duck-typed), `decision` 는 KisPaperAutoDecision.
    KIS 응답은 reason_code/reason_message 만 allowlist 로 사용 (secret 0건).
    """
    requested_at = requested_at or _now()
    responded_at = responded_at or _now()

    reason_code = getattr(result, "reason_code", "") or ""
    reason_message = getattr(result, "reason_message", "") or ""
    dry_run = bool(getattr(result, "dry_run", False))
    submitted = bool(getattr(result, "submitted", False))
    fill_status = getattr(result, "fill_status", None)
    quantity = int(getattr(decision, "quantity", 0) or getattr(result, "quantity", 0) or 0)
    filled_quantity = int(getattr(result, "filled_quantity", 0) or 0)
    request_price = _f(getattr(decision, "price", None))
    avg_fill_price = _f(getattr(result, "avg_fill_price", None))

    status = _map_status(
        reason_code=reason_code, dry_run=dry_run, submitted=submitted,
        fill_status=fill_status, filled_quantity=filled_quantity, quantity=quantity,
    )
    unfilled = max(0, quantity - filled_quantity)
    partial = status == STATUS_PARTIALLY_FILLED or (0 < filled_quantity < quantity)
    latency_ms = max(0, int((responded_at - requested_at).total_seconds() * 1000))
    slippage = (_compute_slippage_bps(request_price, avg_fill_price,
                                      getattr(decision, "side", "BUY"))
                if status in (STATUS_FILLED, STATUS_PARTIALLY_FILLED) else None)
    is_rejected = status == STATUS_REJECTED
    filled_at = _iso(responded_at) if status in (STATUS_FILLED, STATUS_PARTIALLY_FILLED) else None

    return OrderQualityLog(
        order_status=status,
        fill_status=fill_status,
        requested_at=_iso(requested_at),
        submitted_at=_iso(responded_at) if (submitted or status == STATUS_DRY_RUN) else None,
        filled_at=filled_at,
        request_price=request_price,
        avg_fill_price=avg_fill_price,
        quantity=quantity,
        filled_quantity=filled_quantity,
        unfilled_quantity=unfilled,
        partial_fill=partial,
        broker_order_no=getattr(result, "broker_order_no", None),
        kis_response_code=reason_code,
        kis_response_message=reason_message,
        latency_ms=latency_ms,
        slippage_bps=slippage,
        rejection_reason_code=(reason_code if is_rejected else None),
        rejection_reason_message=(reason_message if is_rejected else None),
        data_status="OK",
        fill_polling=(fill_polling or placeholder_fill_polling()),
    )


def order_quality_summary(quality: dict[str, Any] | None) -> dict[str, Any]:
    """order_quality dict → 목록 표시용 요약."""
    if not isinstance(quality, dict):
        return {}
    return {
        "broker_order_no": quality.get("broker_order_no"),
        "order_status":    quality.get("order_status"),
        "fill_status":     quality.get("fill_status"),
        "latency_ms":      quality.get("latency_ms"),
        "slippage_bps":    quality.get("slippage_bps"),
        "partial_fill":    quality.get("partial_fill"),
    }


__all__ = [
    "OrderQualityLog",
    "build_order_quality_log",
    "order_quality_summary",
    "placeholder_fill_polling",
    "STATUS_REQUESTED", "STATUS_SUBMITTED", "STATUS_ACCEPTED", "STATUS_FILLED",
    "STATUS_PARTIALLY_FILLED", "STATUS_UNFILLED", "STATUS_REJECTED",
    "STATUS_CANCELLED", "STATUS_EXPIRED", "STATUS_DRY_RUN", "STATUS_ERROR",
]

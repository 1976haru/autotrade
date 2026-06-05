"""KIS Paper Auto Trading 주문 권한 게이트 — *한투 모의투자 전용*.

자동매매가 KIS 모의투자 API 로 주문을 보내기 *전* 통과해야 하는 보수적 pre-gate.
실거래 권한이 아니다 — KIS_IS_PAPER=true + ENABLE_LIVE_TRADING=false 가 *반드시*
필요하며, 둘 중 하나라도 어긋나면 즉시 차단한다. 본 게이트를 통과해도 주문은
다시 기존 sanctioned 경로(route_order → RiskManager → PermissionGate →
OrderExecutor)를 거치므로 어떤 안전 가드도 우회하지 않는다.

설계 원칙 (CLAUDE.md 절대 원칙 상속):
- 본 모듈은 *순수 함수* — broker / OrderExecutor / route_order / kis_client /
  외부 HTTP import 0건. settings 는 caller 가 *입력 DTO* 로 주입(실제값↔입력값
  혼선 차단).
- API key / app_secret / 계좌번호 *값* 을 받지 않는다 — `credentials_present:
  bool` 라벨만. (AI 가 secret 을 직접 다루지 않도록.)
- `KisPaperOrderPermission.is_live_authorization=False` 영구.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, time, timezone
from enum import StrEnum
from typing import Any

from app.scheduler.market_clock import MarketPhase, current_market_phase, to_kst


class KisPaperPermReason(StrEnum):
    """KIS 모의 자동주문 허용/차단 사유 — frontend / ledger 가 그대로 emit."""

    KIS_PAPER_ORDER_ALLOWED        = "KIS_PAPER_ORDER_ALLOWED"
    KIS_PAPER_AUTO_DISABLED        = "KIS_PAPER_AUTO_DISABLED"
    KIS_PAPER_MODE_REQUIRED        = "KIS_PAPER_MODE_REQUIRED"
    LIVE_TRADING_MUST_BE_DISABLED  = "LIVE_TRADING_MUST_BE_DISABLED"
    KIS_PAPER_CREDENTIALS_MISSING  = "KIS_PAPER_CREDENTIALS_MISSING"
    KIS_PAPER_ORDER_WINDOW_CLOSED  = "KIS_PAPER_ORDER_WINDOW_CLOSED"
    KIS_PAPER_ORDER_LIMIT_EXCEEDED = "KIS_PAPER_ORDER_LIMIT_EXCEEDED"
    KIS_PAPER_NOTIONAL_LIMIT_EXCEEDED = "KIS_PAPER_NOTIONAL_LIMIT_EXCEEDED"
    EMERGENCY_STOP_ENABLED         = "EMERGENCY_STOP_ENABLED"
    MARKET_CLOSED                  = "MARKET_CLOSED"
    NO_STRATEGY_SIGNAL             = "NO_STRATEGY_SIGNAL"
    LOW_CONFIDENCE                 = "LOW_CONFIDENCE"
    LOW_QUALITY_SCORE              = "LOW_QUALITY_SCORE"
    MISSING_EXIT_PLAN              = "MISSING_EXIT_PLAN"
    # V2: 실시간 KIS 시세가 아니면 실제 KIS 모의주문 전송 금지 (mock 시세 차단).
    KIS_REALTIME_PRICE_REQUIRED    = "KIS_REALTIME_PRICE_REQUIRED"
    KIS_PRICE_STALE                = "KIS_PRICE_STALE"


_REASON_MESSAGE_KO: dict[str, str] = {
    KisPaperPermReason.KIS_PAPER_ORDER_ALLOWED:
        "KIS 모의투자 주문 권한 확인 완료",
    KisPaperPermReason.KIS_PAPER_AUTO_DISABLED:
        "KIS 모의투자 자동주문이 비활성화되어 있습니다.",
    KisPaperPermReason.KIS_PAPER_MODE_REQUIRED:
        "KIS 모의투자 모드에서만 자동주문이 가능합니다 (KIS_IS_PAPER=true 필요).",
    KisPaperPermReason.LIVE_TRADING_MUST_BE_DISABLED:
        "실거래 OFF 상태에서만 KIS 모의 자동주문이 허용됩니다 (ENABLE_LIVE_TRADING=false 필요).",
    KisPaperPermReason.KIS_PAPER_CREDENTIALS_MISSING:
        "KIS 모의투자 API 자격(App Key / Secret / 계좌번호)이 설정되지 않았습니다.",
    KisPaperPermReason.KIS_PAPER_ORDER_WINDOW_CLOSED:
        "현재는 KIS 모의 자동주문 허용 시간창이 아닙니다.",
    KisPaperPermReason.KIS_PAPER_ORDER_LIMIT_EXCEEDED:
        "오늘 KIS 모의 자동주문 한도에 도달했습니다.",
    KisPaperPermReason.KIS_PAPER_NOTIONAL_LIMIT_EXCEEDED:
        "주문 금액이 KIS 모의 자동주문 1회 한도를 초과했습니다.",
    KisPaperPermReason.EMERGENCY_STOP_ENABLED:
        "긴급정지 ON — KIS 모의 자동주문이 차단됩니다.",
    KisPaperPermReason.MARKET_CLOSED:
        "장 시간이 아니라 KIS 모의 자동주문이 진행되지 않습니다.",
    KisPaperPermReason.NO_STRATEGY_SIGNAL:
        "전략 매수/매도 신호가 없어 주문하지 않습니다 (HOLD).",
    KisPaperPermReason.LOW_CONFIDENCE:
        "신호 confidence 가 자동주문 최소 기준 미달입니다.",
    KisPaperPermReason.LOW_QUALITY_SCORE:
        "신호 quality_score 가 자동주문 최소 기준 미달입니다.",
    KisPaperPermReason.MISSING_EXIT_PLAN:
        "청산 계획(stop/target)이 없어 자동주문을 진행하지 않습니다.",
    KisPaperPermReason.KIS_REALTIME_PRICE_REQUIRED:
        "실시간 KIS 시세 기준이 아니라 KIS 모의주문을 전송하지 않습니다 (mock 시세 주문 금지).",
    KisPaperPermReason.KIS_PRICE_STALE:
        "KIS 시세가 오래되어(stale) KIS 모의주문을 전송하지 않습니다.",
}


def perm_message_ko(code: str) -> str:
    return _REASON_MESSAGE_KO.get(code, code)


@dataclass(frozen=True)
class KisPaperOrderPermissionInput:
    """게이트 입력 DTO — caller(API/driver)가 settings + 결정 라벨을 명시 주입.

    secret *값* 없음 — `credentials_present: bool` 라벨만.
    """

    # 안전 flag.
    enable_kis_paper_auto_trading: bool
    dry_run:                       bool
    kis_is_paper:                  bool
    enable_live_trading:           bool
    broker_is_kis_paper:           bool
    credentials_present:           bool
    # 운영 상태.
    emergency_stop:                bool = False
    # 결정.
    side:                          str  = "HOLD"   # BUY / SELL / HOLD
    notional_krw:                  int  = 0
    confidence:                    float = 0.0      # 0~1
    quality_score:                 int  = 0         # 0~100
    has_exit_plan:                 bool = False
    # 한도.
    max_order_notional:            int  = 1_000_000
    daily_order_count:             int  = 0
    max_orders_per_day:            int  = 10
    # 시간창 (KST "HH:MM").
    window_start:                  str  = "09:05"
    window_end:                    str  = "14:50"
    # 품질 임계.
    min_confidence:                float = 0.6
    min_quality_score:             int  = 60
    # V2: 시세 출처 가드 — 실제 전송(not dry_run)은 KIS 실시간 시세만 허용.
    # default "kis" 로 backward-compat (기존 caller 무회귀); bridge/executor 가
    # 결정의 실제 price_source 를 명시 주입한다.
    price_source:                  str  = "kis"
    price_is_stale:                bool = False
    # 시각 (UTC) — None 이면 now(UTC).
    now:                           datetime | None = None


@dataclass(frozen=True)
class KisPaperOrderPermission:
    """게이트 결과 — *advisory*, broker 호출 0건. is_live_authorization=False 영구."""

    allowed:        bool
    dry_run:        bool
    reason_code:    str
    reason_message: str
    side:           str
    notional_krw:   int
    metadata:       dict[str, Any]
    is_live_authorization: bool = False
    is_order_signal:       bool = False

    def __post_init__(self) -> None:
        if self.is_live_authorization is not False:
            raise ValueError("KisPaperOrderPermission.is_live_authorization must be False")
        if self.is_order_signal is not False:
            raise ValueError("KisPaperOrderPermission.is_order_signal must be False")

    def to_dict(self) -> dict[str, Any]:
        return {
            "allowed":        bool(self.allowed),
            "dry_run":        bool(self.dry_run),
            "reason_code":    self.reason_code,
            "reason_message": self.reason_message,
            "side":           self.side,
            "notional_krw":   int(self.notional_krw),
            "metadata":       dict(self.metadata),
            "is_live_authorization": self.is_live_authorization,
            "is_order_signal":       self.is_order_signal,
        }


_BUY_SELL = frozenset({"BUY", "SELL"})


def _parse_hhmm(s: str) -> time | None:
    try:
        hh, mm = str(s).strip().split(":")
        return time(hour=int(hh), minute=int(mm))
    except Exception:  # noqa: BLE001
        return None


def evaluate_kis_paper_order_permission(
    inp: KisPaperOrderPermissionInput,
) -> KisPaperOrderPermission:
    """KIS 모의 자동주문 허용 여부 + 사유. 첫 차단 사유에서 멈춤.

    조건 순서 (사용자 요청서 §3): flag → KIS paper 모드 → LIVE off → 자격 →
    긴급정지 → 장 OPEN → 시간창 → 신호(BUY/SELL) → confidence → quality →
    exit plan → notional 한도 → 일일 한도 → 허용.
    """
    now = inp.now or datetime.now(timezone.utc)

    def _block(code: KisPaperPermReason) -> KisPaperOrderPermission:
        return KisPaperOrderPermission(
            allowed=False, dry_run=bool(inp.dry_run), reason_code=code.value,
            reason_message=perm_message_ko(code.value), side=inp.side,
            notional_krw=int(inp.notional_krw),
            metadata={"window_start": inp.window_start, "window_end": inp.window_end},
        )

    if not inp.enable_kis_paper_auto_trading:
        return _block(KisPaperPermReason.KIS_PAPER_AUTO_DISABLED)
    # KIS 모의 모드 필수 — paper 가 아니면 절대 진행 0건.
    if not inp.kis_is_paper or not inp.broker_is_kis_paper:
        return _block(KisPaperPermReason.KIS_PAPER_MODE_REQUIRED)
    # 실거래는 무조건 OFF 여야 함.
    if inp.enable_live_trading:
        return _block(KisPaperPermReason.LIVE_TRADING_MUST_BE_DISABLED)
    if not inp.credentials_present:
        return _block(KisPaperPermReason.KIS_PAPER_CREDENTIALS_MISSING)
    if inp.emergency_stop:
        return _block(KisPaperPermReason.EMERGENCY_STOP_ENABLED)

    # 장 OPEN.
    if current_market_phase(now) != MarketPhase.OPEN:
        return _block(KisPaperPermReason.MARKET_CLOSED)

    # 시간창 (KST).
    kst_t = to_kst(now).time()
    ws = _parse_hhmm(inp.window_start)
    we = _parse_hhmm(inp.window_end)
    if ws is not None and we is not None and not (ws <= kst_t <= we):
        return _block(KisPaperPermReason.KIS_PAPER_ORDER_WINDOW_CLOSED)

    # 신호.
    side = (inp.side or "").strip().upper()
    if side not in _BUY_SELL:
        return _block(KisPaperPermReason.NO_STRATEGY_SIGNAL)
    if inp.confidence < inp.min_confidence:
        return _block(KisPaperPermReason.LOW_CONFIDENCE)
    if inp.quality_score < inp.min_quality_score:
        return _block(KisPaperPermReason.LOW_QUALITY_SCORE)
    # BUY 는 청산 계획 필수 (SELL 은 청산 자체이므로 면제).
    if side == "BUY" and not inp.has_exit_plan:
        return _block(KisPaperPermReason.MISSING_EXIT_PLAN)

    # V2: 실제 KIS 모의주문 전송(not dry_run)은 *실시간 KIS 시세* 기준에서만.
    # mock/yfinance 시세로는 KIS 모의주문을 전송하지 않는다 (silent fallback 금지).
    if not inp.dry_run:
        if str(inp.price_source).strip().lower() != "kis":
            return _block(KisPaperPermReason.KIS_REALTIME_PRICE_REQUIRED)
        if inp.price_is_stale:
            return _block(KisPaperPermReason.KIS_PRICE_STALE)

    # 한도.
    #   notional 1회 한도: 신규 진입(BUY)·청산(SELL) 모두 적용 유지.
    if inp.max_order_notional > 0 and inp.notional_krw > inp.max_order_notional:
        return _block(KisPaperPermReason.KIS_PAPER_NOTIONAL_LIMIT_EXCEEDED)
    #   일일 주문 횟수 한도: *신규 진입(BUY)에만* 적용. 청산(SELL: 손절/익절)은
    #   한도 소진으로 막히면 안 되므로 면제한다 — 단 본 게이트 통과 후에도 주문은
    #   route_order → RiskManager → PermissionGate → OrderExecutor 를 그대로
    #   거친다(횟수 한도만 면제, 다른 가드 우회 아님). 회귀 근거: 2026-06-05 첫
    #   실체결일에 한도(10건) 소진 상태에서 005935 약세 청산 신호 399건이 전송
    #   차단돼 손실 종목을 못 판 사례.
    if (
        side == "BUY"
        and inp.max_orders_per_day > 0
        and inp.daily_order_count >= inp.max_orders_per_day
    ):
        return _block(KisPaperPermReason.KIS_PAPER_ORDER_LIMIT_EXCEEDED)

    return KisPaperOrderPermission(
        allowed=True, dry_run=bool(inp.dry_run),
        reason_code=KisPaperPermReason.KIS_PAPER_ORDER_ALLOWED.value,
        reason_message=perm_message_ko(KisPaperPermReason.KIS_PAPER_ORDER_ALLOWED.value),
        side=side, notional_krw=int(inp.notional_krw),
        metadata={"window_start": inp.window_start, "window_end": inp.window_end,
                  "daily_order_count": inp.daily_order_count,
                  "max_orders_per_day": inp.max_orders_per_day},
    )


__all__ = [
    "KisPaperPermReason",
    "perm_message_ko",
    "KisPaperOrderPermissionInput",
    "KisPaperOrderPermission",
    "evaluate_kis_paper_order_permission",
]

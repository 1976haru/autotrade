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

from app.scheduler.intraday_session import (
    BUY_ALLOWED,
    BUY_BLOCKED_BEFORE_ENTRY,
    CalendarStatus,
    IntradayPolicy,
    load_krx_calendar,
)
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
    KIS_PAPER_FILL_POLLING_REQUIRED = "KIS_PAPER_FILL_POLLING_REQUIRED"
    # INTRADAY-FLAT-BY-CLOSE-V1: BUY/SELL 목적별 게이트 분리.
    BUY_CUTOFF_ACTIVE              = "BUY_CUTOFF_ACTIVE"
    BUY_BLOCKED_NON_TRADING_DAY    = "BUY_BLOCKED_NON_TRADING_DAY"
    BUY_BLOCKED_CALENDAR_UNKNOWN   = "BUY_BLOCKED_CALENDAR_UNKNOWN"
    BUY_BLOCKED_AFTER_CLOSE        = "BUY_BLOCKED_AFTER_CLOSE"
    BUY_BLOCKED_SPECIAL_SESSION_UNCONFIRMED = "BUY_BLOCKED_SPECIAL_SESSION_UNCONFIRMED"
    BUY_BLOCKED_BY_EOD_INCIDENT    = "BUY_BLOCKED_BY_EOD_INCIDENT"
    EXIT_WINDOW_CLOSED             = "EXIT_WINDOW_CLOSED"
    EXIT_NOTIONAL_LIMIT_EXCEEDED   = "EXIT_NOTIONAL_LIMIT_EXCEEDED"
    SELL_QUANTITY_INVALID          = "SELL_QUANTITY_INVALID"


# 주문 목적 — 게이트가 목적별로 다른 한도/시간창을 적용한다.
PURPOSE_BUY_ENTRY       = "BUY_ENTRY"
PURPOSE_NORMAL_EXIT     = "NORMAL_EXIT"
PURPOSE_EOD_FORCED_EXIT = "EOD_FORCED_EXIT"


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
    KisPaperPermReason.KIS_PAPER_FILL_POLLING_REQUIRED:
        "실제 KIS 모의 매수 전송에는 체결 폴링 활성화가 필요합니다.",
    KisPaperPermReason.BUY_CUTOFF_ACTIVE:
        "신규 매수 마감(기본 14:30 KST) 이후라 신규 매수를 하지 않습니다. 보유 청산은 계속됩니다.",
    KisPaperPermReason.BUY_BLOCKED_NON_TRADING_DAY:
        "휴장일/주말이라 신규 매수를 하지 않습니다.",
    KisPaperPermReason.BUY_BLOCKED_CALENDAR_UNKNOWN:
        "거래일 여부를 확인할 수 없어(휴장일 달력 미등록 연도) 신규 매수를 차단합니다.",
    KisPaperPermReason.BUY_BLOCKED_AFTER_CLOSE:
        "정규장이 끝나 신규 매수를 하지 않습니다.",
    KisPaperPermReason.BUY_BLOCKED_SPECIAL_SESSION_UNCONFIRMED:
        "거래시간이 바뀌는 특수일(수능일 등)인데 KRX 공지가 확인되지 않아 신규 매수를 차단합니다.",
    KisPaperPermReason.BUY_BLOCKED_BY_EOD_INCIDENT:
        "직전 거래일 장마감 청산이 완료되지 않아(FLATTEN_INCOMPLETE) 운영자 확인 전까지 신규 매수를 차단합니다.",
    KisPaperPermReason.EXIT_WINDOW_CLOSED:
        "현재는 청산(SELL) 주문 가능 시간이 아닙니다 (정규장 연속매매 구간 밖).",
    KisPaperPermReason.EXIT_NOTIONAL_LIMIT_EXCEEDED:
        "청산 주문 금액이 청산 1회 한도를 초과했습니다.",
    KisPaperPermReason.SELL_QUANTITY_INVALID:
        "매도 수량이 0 이하이거나 보유 수량을 초과합니다 (무보유/초과 매도/숏 금지).",
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
    # ★위험 청산 면제(옵션 A, 2026-06-12): 손절(STOP_LOSS)·익절(TAKE_PROFIT) 도달로
    #   강제된 청산 SELL 은 *품질/확신 게이트만* 면제한다(설계 의도 "위험 청산은 강제,
    #   품질 무관" 부합). 청산은 위험 *축소* 라 votes 가 약해도 나가야 한다. 면제 범위는
    #   엄격히 손절/익절 청산뿐 — 일반 SELL·BUY 는 그대로. *다른* 게이트(긴급정지·시간창·
    #   시세출처·notional·일일횟수 등)는 면제 안 됨(품질/확신만).
    is_risk_exit:                  bool = False
    # V2: 시세 출처 가드 — 실제 전송(not dry_run)은 KIS 실시간 시세만 허용.
    # default "kis" 로 backward-compat (기존 caller 무회귀); bridge/executor 가
    # 결정의 실제 price_source 를 명시 주입한다.
    price_source:                  str  = "kis"
    price_is_stale:                bool = False
    # The production builder always injects the effective setting.  The
    # backward-compatible default avoids changing advisory-only callers.
    fill_polling_enabled:          bool = True
    # 시각 (UTC) — None 이면 now(UTC).
    now:                           datetime | None = None
    # ── INTRADAY-FLAT-BY-CLOSE-V1: 목적별 게이트 ────────────────────────────
    # "" 이면 side 로 유도 (BUY→BUY_ENTRY, SELL→NORMAL_EXIT).
    order_purpose:                 str  = ""
    # 청산 SELL 1회 명목 한도 — 0 = 보유수량이 유일한 상한 (진입 한도와 분리).
    max_exit_order_notional:       int  = 0
    # SELL 수량 검증 — held_quantity >= 0 이면 0 < quantity <= held_quantity 강제.
    #   -1 = 미상(호출자가 상위에서 이미 클램프) → 검사 생략 (backward-compat).
    quantity:                      int  = 0
    held_quantity:                 int  = -1
    # 정규장 close 기준 오프셋 / 달력 / 단일가 청산 허용.
    buy_cutoff_minutes_before_close: int = 60
    flatten_minutes_before_close:  int  = 15
    allow_closing_auction_exit:    bool = False
    calendar_override_path:        str  = ""
    # 외부(DB) 사유로 신규 BUY 차단 — 예: 직전 거래일 FLATTEN_INCOMPLETE 미확인.
    buy_blocked_by_incident:       bool = False


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


def _resolve_purpose(raw: str, side: str) -> str:
    """주문 목적 — BUY 는 어떤 라벨이든 진입 게이트, SELL 은 EOD 라벨만 강제청산."""
    if side == "BUY":
        return PURPOSE_BUY_ENTRY
    if (raw or "").strip().upper() == PURPOSE_EOD_FORCED_EXIT:
        return PURPOSE_EOD_FORCED_EXIT
    return PURPOSE_NORMAL_EXIT


def evaluate_kis_paper_order_permission(
    inp: KisPaperOrderPermissionInput,
) -> KisPaperOrderPermission:
    """KIS 모의 자동주문 허용 여부 + 사유. 첫 차단 사유에서 멈춤.

    조건 순서: flag → KIS paper 모드 → LIVE off → 자격 → 긴급정지 → 장 OPEN →
    신호(BUY/SELL) → *목적별* 시간창(BUY 14:30 컷오프 / 청산 ~15:20 / EOD 15:15~) →
    confidence·quality(진입만) → exit plan → 시세 출처 → *목적별* notional 한도 →
    일일 한도(BUY 만) → 허용.
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

    # 장 OPEN (KRX 달력 기준 거래일 정규장). 휴장일은 아래 intraday 정책이 더 구체적인
    #   사유(BUY_BLOCKED_NON_TRADING_DAY / EXIT_WINDOW_CLOSED)로 차단하도록 통과시킨다.
    _calendar = load_krx_calendar(inp.calendar_override_path or None)
    if (current_market_phase(now, calendar=_calendar) != MarketPhase.OPEN
            and _calendar.status(to_kst(now).date()) != CalendarStatus.HOLIDAY):
        return _block(KisPaperPermReason.MARKET_CLOSED)

    # 신호.
    side = (inp.side or "").strip().upper()
    if side not in _BUY_SELL:
        return _block(KisPaperPermReason.NO_STRATEGY_SIGNAL)
    purpose = _resolve_purpose(inp.order_purpose, side)

    # 시간창 — *목적별* (INTRADAY-FLAT-BY-CLOSE-V1). 단일 서버 시계(intraday_session).
    #   BUY_ENTRY       : entry_start ≤ t < buy_cutoff(14:30). 14:30:00 부터 무조건 차단.
    #   NORMAL_EXIT     : entry_start ≤ t < auction_start(15:20) — 14:30 이후에도 청산 가능.
    #   EOD_FORCED_EXIT : flatten_start(15:15) ≤ t < auction_start (+단일가는 플래그 ON 시).
    policy = IntradayPolicy(
        window_start=inp.window_start, window_end=inp.window_end,
        buy_cutoff_minutes_before_close=inp.buy_cutoff_minutes_before_close,
        flatten_minutes_before_close=inp.flatten_minutes_before_close,
        calendar=_calendar,
    )
    if purpose == PURPOSE_BUY_ENTRY:
        ok, code = policy.buy_entry_decision(now)
        if not ok:
            if code == BUY_BLOCKED_BEFORE_ENTRY:
                return _block(KisPaperPermReason.KIS_PAPER_ORDER_WINDOW_CLOSED)
            return _block(KisPaperPermReason(code))
        assert code == BUY_ALLOWED
        if inp.buy_blocked_by_incident:
            return _block(KisPaperPermReason.BUY_BLOCKED_BY_EOD_INCIDENT)
    else:
        if not policy.exit_window_open(
                now, forced=(purpose == PURPOSE_EOD_FORCED_EXIT),
                allow_closing_auction=inp.allow_closing_auction_exit):
            return _block(KisPaperPermReason.EXIT_WINDOW_CLOSED)
        # 무보유/초과 매도/숏 금지 — 보유 수량이 주어지면 강제.
        if inp.held_quantity >= 0 and not (0 < inp.quantity <= inp.held_quantity):
            return _block(KisPaperPermReason.SELL_QUANTITY_INVALID)

    # 품질/확신 — *신규 진입* 게이트. 청산은 위험 축소라 면제:
    #   - is_risk_exit (실보유 청산 / 손절·익절 등) SELL
    #   - EOD_FORCED_EXIT (시간 기반 강제청산 — Council HOLD/신호 부재와 무관)
    #   ★방어적 범위 강제: 면제는 *SELL* 에만 — 게이트 단에서도 side 재확인
    #   (플래그가 어쩌다 BUY 에 켜져도 BUY 는 품질/확신 게이트 적용, 악용/오작동 방지).
    _quality_exempt = side == "SELL" and (
        inp.is_risk_exit or purpose == PURPOSE_EOD_FORCED_EXIT)
    if not _quality_exempt:
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
        if side == "BUY" and not inp.fill_polling_enabled:
            return _block(KisPaperPermReason.KIS_PAPER_FILL_POLLING_REQUIRED)

    # 한도 — 목적별 분리.
    #   notional 1회 한도: BUY 는 max_order_notional, 청산은 max_exit_order_notional
    #   (0 = 보유수량이 유일한 상한). 예전엔 SELL 에도 진입 한도가 적용돼 평가액이
    #   한도를 넘게 오른 포지션을 *못 팔았다* (익절/강제청산 불가 결함).
    if side == "BUY":
        if inp.max_order_notional > 0 and inp.notional_krw > inp.max_order_notional:
            return _block(KisPaperPermReason.KIS_PAPER_NOTIONAL_LIMIT_EXCEEDED)
    elif inp.max_exit_order_notional > 0 and inp.notional_krw > inp.max_exit_order_notional:
        return _block(KisPaperPermReason.EXIT_NOTIONAL_LIMIT_EXCEEDED)
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
                  "max_orders_per_day": inp.max_orders_per_day,
                  "order_purpose": purpose},
    )


__all__ = [
    "PURPOSE_BUY_ENTRY",
    "PURPOSE_NORMAL_EXIT",
    "PURPOSE_EOD_FORCED_EXIT",
    "KisPaperPermReason",
    "perm_message_ko",
    "KisPaperOrderPermissionInput",
    "KisPaperOrderPermission",
    "evaluate_kis_paper_order_permission",
]

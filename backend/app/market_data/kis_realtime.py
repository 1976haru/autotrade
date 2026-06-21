"""KIS 실시간 시세 read-only 어댑터 — Paper Auto Loop V2.

CONNECT-KIS-REALTIME-PRICE-TO-PAPER-AUTO-LOOP-V2:
AutoPaperLoop 가 mock 합성가가 아니라 *실시간 KIS 시세* 로 판단하도록, KIS 의
**read-only 시세 조회**(현재가 inquire-price[FHKST01010100] + 분봉
inquire-time-dailychartprice[FHKST03010230])만 사용해 `StrategyMarketInput`
을 구성한다.

절대 원칙:
- **주문 API 호출 0건** — 본 모듈은 `KisClient.place_order` / `route_order` /
  `OrderExecutor` 를 import 하지도 호출하지도 않는다 (정적 grep 가드). KIS read-
  only 시세 메서드(`get_price` / `inquire_time_dailychartprice`)만 사용.
- **mock 으로 silent fallback 금지** — 시세 조회 실패 시 명확한 reason_code
  (`KIS_MARKET_DATA_UNAVAILABLE` / `KIS_PRICE_STALE` / `KIS_PRICE_INVALID`)
  를 반환하고, caller(파이프라인/bridge)는 HOLD/BLOCK 한다.
- `price_source="kis"` 영구. `is_live_authorization=False` / `is_order_signal=
  False` 불변(dataclass `__post_init__` 가드).

client 는 *주입 가능* — 테스트는 fake client(async `get_price` /
`inquire_time_dailychartprice`)를 주입해 실 KIS 호출 0건으로 검증한다.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from datetime import datetime, timezone
from enum import StrEnum
from typing import Any, Optional

from app.agents.agent_council import StrategyMarketInput
from app.scheduler.market_clock import to_kst


_log = logging.getLogger("autotrade.market_data.kis_realtime")


# ── reason codes (사용자 요청서 §2) ──────────────────────────────────────────
KIS_PRICE_OK                = "KIS_PRICE_OK"
KIS_MARKET_DATA_UNAVAILABLE = "KIS_MARKET_DATA_UNAVAILABLE"
KIS_PRICE_STALE             = "KIS_PRICE_STALE"
KIS_PRICE_INVALID           = "KIS_PRICE_INVALID"


class KisRealtimeStatus(StrEnum):
    OK          = KIS_PRICE_OK
    UNAVAILABLE = KIS_MARKET_DATA_UNAVAILABLE
    STALE       = KIS_PRICE_STALE
    INVALID     = KIS_PRICE_INVALID


_STATUS_MESSAGE_KO: dict[str, str] = {
    KIS_PRICE_OK:                "KIS 실시간 현재가 조회 성공",
    KIS_MARKET_DATA_UNAVAILABLE: "KIS 실시간 시세 조회에 실패해 판단을 진행하지 않습니다 (mock 대체 안 함).",
    KIS_PRICE_STALE:             "KIS 시세가 오래되어(stale) 매수/매도 판단을 보류합니다.",
    KIS_PRICE_INVALID:           "KIS 현재가가 0 이하/비정상이라 판단을 차단합니다.",
}

# 에러 메시지에서 secret 추정 토큰 마스킹 (Bearer / appkey 등 노출 차단).
_SECRET_PATTERNS = (
    re.compile(r"Bearer\s+[A-Za-z0-9._\-]{12,}"),
    re.compile(r"PST[A-Za-z0-9]{20,}"),
    re.compile(r"sk-[A-Za-z0-9_\-]{12,}"),
    re.compile(r"\b\d{6,}-\d{2,}\b"),
)


def _redact(text: str) -> str:
    out = str(text)
    for pat in _SECRET_PATTERNS:
        out = pat.sub("[REDACTED]", out)
    return out[:300]


def status_message_ko(code: str) -> str:
    return _STATUS_MESSAGE_KO.get(code, code)


def _to_float(value: Any) -> float | None:
    """KIS 응답 문자열 숫자를 float 로. 빈 값/파싱 실패는 None."""
    if value is None:
        return None
    try:
        s = str(value).replace(",", "").strip()
        if s == "" or s in ("-", "."):
            return None
        return float(s)
    except (TypeError, ValueError):
        return None


@dataclass(frozen=True)
class KisRealtimeQuote:
    """KIS 실시간 현재가 스냅샷 — *시세*, 주문 아님.

    `price_source="kis"` 영구. `is_live_authorization=False` 불변.
    """

    symbol:         str
    status:         str
    price:          float | None
    timestamp:      str | None  = None      # 조회 시점 ISO UTC
    is_stale:       bool        = False
    reason_message: str         = ""
    open_price:     float | None = None
    prev_close:     float | None = None
    high:           float | None = None
    low:            float | None = None
    volume:         float | None = None
    vwap:           float | None = None
    price_source:   str         = "kis"
    is_order_signal:        bool = False
    is_live_authorization:  bool = False

    def __post_init__(self) -> None:
        if self.price_source != "kis":
            raise ValueError("KisRealtimeQuote.price_source must be 'kis'")
        if self.is_order_signal is not False:
            raise ValueError("KisRealtimeQuote.is_order_signal must be False")
        if self.is_live_authorization is not False:
            raise ValueError("KisRealtimeQuote.is_live_authorization must be False")

    @property
    def ok(self) -> bool:
        return self.status == KIS_PRICE_OK and not self.is_stale

    def to_dict(self) -> dict[str, Any]:
        return {
            "symbol":         self.symbol,
            "status":         self.status,
            "price":          self.price,
            "timestamp":      self.timestamp,
            "is_stale":       bool(self.is_stale),
            "reason_message": self.reason_message or status_message_ko(self.status),
            "open_price":     self.open_price,
            "prev_close":     self.prev_close,
            "high":           self.high,
            "low":            self.low,
            "volume":         self.volume,
            "vwap":           self.vwap,
            "price_source":   self.price_source,
            "is_order_signal":       self.is_order_signal,
            "is_live_authorization": self.is_live_authorization,
        }


def _extract_snapshot(raw: dict) -> dict[str, float | None]:
    """inquire-price output → 표준 필드. 키 부재는 None (관용 파싱)."""
    out = (raw or {}).get("output") or {}
    return {
        "price":      _to_float(out.get("stck_prpr")),
        "open_price": _to_float(out.get("stck_oprc")),
        "high":       _to_float(out.get("stck_hgpr")),
        "low":        _to_float(out.get("stck_lwpr")),
        "prev_close": _to_float(out.get("stck_sdpr")),   # 기준가 (전일 종가/기준)
        "volume":     _to_float(out.get("acml_vol")),
        "vwap":       _to_float(out.get("wghn_avrg_stck_prc")),
        # #9: 거래정지 플래그(Y/N) — KIS inquire-price 에 거래소 타임스탬프가 없어
        #   stale 를 시각으로 못 재는 대신, 정지종목을 fail-closed 차단하는 근거.
        "temp_stop_yn": str(out.get("temp_stop_yn") or "").upper(),
    }


async def fetch_realtime_quote(
    symbol: str,
    *,
    client: Any,
    now: datetime | None = None,
    market_is_open: bool = True,
) -> KisRealtimeQuote:
    """KIS read-only 현재가 1회 조회. 실패/비정상은 reason_code 로 반환.

    `client.get_price(symbol)` (read-only quote) 만 호출 — 주문 API 0건.
    `market_is_open=False` 면 스냅샷을 STALE 로 표시(장중이 아니므로 의사결정용
    실시세가 아님). mock 대체 0건.
    """
    now = now or datetime.now(timezone.utc)
    ts = now.isoformat()

    try:
        raw = await client.get_price(symbol)
    except Exception as exc:  # noqa: BLE001 — 어떤 시세 오류도 mock 대체하지 않음.
        _log.warning("[kis-realtime] get_price failed for %s: %s",
                     symbol, _redact(f"{type(exc).__name__}: {exc}"))
        return KisRealtimeQuote(
            symbol=symbol, status=KIS_MARKET_DATA_UNAVAILABLE, price=None,
            timestamp=ts, reason_message=status_message_ko(KIS_MARKET_DATA_UNAVAILABLE),
        )

    snap = _extract_snapshot(raw if isinstance(raw, dict) else {})
    price = snap["price"]
    if price is None:
        return KisRealtimeQuote(
            symbol=symbol, status=KIS_MARKET_DATA_UNAVAILABLE, price=None,
            timestamp=ts, reason_message=status_message_ko(KIS_MARKET_DATA_UNAVAILABLE),
        )
    if price <= 0:
        return KisRealtimeQuote(
            symbol=symbol, status=KIS_PRICE_INVALID, price=price,
            timestamp=ts, reason_message=status_message_ko(KIS_PRICE_INVALID),
        )
    # #9 fail-closed: 거래정지(temp_stop_yn=Y) 종목은 시세가 actionable 하지 않다 →
    #   INVALID 로 차단(caller 가 skip). 정상종목(N/빈값)은 그대로 통과 — 거짓차단 0.
    if snap.get("temp_stop_yn") == "Y":
        return KisRealtimeQuote(
            symbol=symbol, status=KIS_PRICE_INVALID, price=price, timestamp=ts,
            is_stale=True, reason_message="거래정지 종목 — 판단 보류",
        )
    if not market_is_open:
        return KisRealtimeQuote(
            symbol=symbol, status=KIS_PRICE_STALE, price=price, timestamp=ts,
            is_stale=True, reason_message=status_message_ko(KIS_PRICE_STALE),
            open_price=snap["open_price"], prev_close=snap["prev_close"],
            high=snap["high"], low=snap["low"], volume=snap["volume"], vwap=snap["vwap"],
        )
    return KisRealtimeQuote(
        symbol=symbol, status=KIS_PRICE_OK, price=price, timestamp=ts, is_stale=False,
        reason_message=status_message_ko(KIS_PRICE_OK),
        open_price=snap["open_price"], prev_close=snap["prev_close"],
        high=snap["high"], low=snap["low"], volume=snap["volume"], vwap=snap["vwap"],
    )


def _parse_intraday_closes(raw: dict) -> list[float]:
    """inquire-time-dailychartprice output2 → 종가 리스트(시간 오름차순)."""
    rows = (raw or {}).get("output2") or []
    closes: list[tuple[str, float]] = []
    for row in rows:
        c = _to_float(row.get("stck_prpr"))
        t = str(row.get("stck_cntg_hour") or row.get("stck_bsop_date") or "")
        if c is not None and c > 0:
            closes.append((t, c))
    # KIS 분봉은 보통 최신→과거 순. 시간 문자열로 정렬해 오름차순(마지막=최신).
    closes.sort(key=lambda kv: kv[0])
    return [c for _, c in closes]


async def build_kis_market_input(
    symbol: str,
    *,
    client: Any,
    now: datetime | None = None,
    market_is_open: bool = True,
    market_regime: str = "UNKNOWN",
    regime_decision: str = "ALLOW",
) -> tuple[Optional[StrategyMarketInput], KisRealtimeQuote]:
    """KIS 실시세로 `StrategyMarketInput` 구성. 실패 시 (None, quote) 반환.

    현재가는 inquire-price 스냅샷, 시리즈(recent_closes / opening range / vwap)
    는 분봉(inquire-time-dailychartprice)에서 구성한다. 분봉 미가용 시 스냅샷
    필드로 최소 구성(전일종가·시가·고저)하고 council 이 None 을 안전 처리.
    모두 read-only 시세 — 주문 호출 0건.
    """
    now = now or datetime.now(timezone.utc)
    quote = await fetch_realtime_quote(
        symbol, client=client, now=now, market_is_open=market_is_open,
    )
    if not quote.ok:
        return None, quote

    closes: list[float] = []
    try:
        date = to_kst(now).strftime("%Y%m%d")
        raw_bars = await client.inquire_time_dailychartprice(symbol, date=date)
        closes = _parse_intraday_closes(raw_bars if isinstance(raw_bars, dict) else {})
    except Exception as exc:  # noqa: BLE001 — 분봉 실패는 스냅샷 기반 최소 구성으로 진행.
        _log.info("[kis-realtime] intraday bars unavailable for %s: %s",
                  symbol, _redact(f"{type(exc).__name__}"))
        closes = []

    if len(closes) >= 5:
        recent_closes = tuple(closes[-30:])
        opening = closes[:6] if len(closes) >= 6 else closes
        orh: float | None = max(opening)
        orl: float | None = min(opening)
        vwap = quote.vwap if quote.vwap else (sum(recent_closes) / len(recent_closes))
        avg_vol = quote.volume
    else:
        # 분봉 부족 — 스냅샷 필드로 최소 구성. ORB 는 일중 고저로 근사(주석).
        base = quote.prev_close or quote.open_price or quote.price
        recent_closes = tuple(x for x in (base, quote.open_price, quote.price) if x)
        orh = quote.high
        orl = quote.low
        vwap = quote.vwap
        avg_vol = quote.volume

    mi = StrategyMarketInput(
        symbol=symbol,
        current_price=quote.price,
        prev_close=quote.prev_close,
        open_price=quote.open_price,
        vwap=vwap,
        opening_range_high=orh,
        opening_range_low=orl,
        recent_closes=recent_closes,
        current_volume=quote.volume,
        avg_volume=avg_vol,
        market_regime=market_regime,
        regime_decision=regime_decision,
    )
    return mi, quote


__all__ = [
    "KIS_PRICE_OK",
    "KIS_MARKET_DATA_UNAVAILABLE",
    "KIS_PRICE_STALE",
    "KIS_PRICE_INVALID",
    "KisRealtimeStatus",
    "KisRealtimeQuote",
    "status_message_ko",
    "fetch_realtime_quote",
    "build_kis_market_input",
]

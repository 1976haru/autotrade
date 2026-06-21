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

import asyncio
import logging
import re
import time as _time
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


# ── ⓑ 봉주기 신호 모드 (2026-06-18 60분봉 → 2026-06-21 5/30/60 일반화) ─────────
# 라이브 신호 입력을 1분봉 당일 → BAR_INTERVAL_MINUTES분봉 멀티데이로 전환. 청산(30초
# 틱·실시간가)은 driver_bridge 가 그대로 — 본 모듈은 *진입 voter 입력*만 N분봉으로 구성.
# ★봉주기 전환 = 아래 한 줄(BAR_INTERVAL_MINUTES). 5/30/60 = 리샘플 경로, 0 = legacy 1분봉
#   경로(원 코드 보존, 롤백/하위호환). git checkout 으로도 복귀.
BAR_INTERVAL_MINUTES = 30   # ← 봉주기(5/30/60) 또는 0=legacy 1분봉. 30분봉 전환(드라이런 검증완료).
_RECENT_BARS = 30           # momentum/vwap 룩백 *봉 수*(N무관 유지; 실제기간=30×N분)
_SESSION_MIN = 390          # 장중 09:00~15:30 = 390분
_ORB_START, _ORB_END = "090000", "093000"   # ORB 레인지 09:00–09:30(봉주기 무관, 1분봉서 계산)
# 과거일 N분봉 캐시: (symbol, asof_date_kst, N) -> list[dict OHLCV]. 하루 1회만 채움.
_prior_bars_cache: dict[tuple[str, str, int], list[dict]] = {}

# 5/30/60 = 멀티데이 리샘플 경로, 그 외(0/1) = legacy 1분봉 당일 경로.
_USE_INTRADAY_RESAMPLE = BAR_INTERVAL_MINUTES in (5, 30, 60)


def _bars_per_day() -> int:
    """장중 1일 N분봉 개수 (60→6, 30→13, 5→78)."""
    return max(1, _SESSION_MIN // BAR_INTERVAL_MINUTES)


def _prior_days() -> int:
    """룩백 _RECENT_BARS 봉을 채우는 데 필요한 과거 거래일 수 = ceil(30/봉수per일)+1 버퍼."""
    return max(1, -(-_RECENT_BARS // _bars_per_day()) + 1)


def _prior_max_cal() -> int:
    """과거 거래일 채우기 위한 최대 역행 달력일(주말·공휴일 흡수)."""
    return _prior_days() * 3


def reset_bars_cache() -> None:
    """테스트/드라이런용 — 과거일 N분봉 캐시 비우기."""
    _prior_bars_cache.clear()


# 하위호환 alias(기존 호출부 보존).
reset_60m_cache = reset_bars_cache


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


# ── ⓑ 60분봉: 분봉 OHLCV 파싱 / 리샘플 / 멀티데이 fetch + 캐시 ────────────────
def _parse_intraday_ohlcv(raw: dict) -> list[dict]:
    """inquire-time-dailychartprice output2 → 분봉 OHLCV(시간 오름차순).

    필드: stck_bsop_date(일자) / stck_cntg_hour(HHMMSS) / stck_oprc·hgpr·lwpr·prpr(OHLC) /
    cntg_vol(체결량). 일부 필드 누락 시 종가(prpr)로 보정.
    """
    rows = (raw or {}).get("output2") or []
    out: list[dict] = []
    for row in rows:
        c = _to_float(row.get("stck_prpr"))
        if c is None or c <= 0:
            continue
        out.append({
            "d": str(row.get("stck_bsop_date") or ""),
            "t": str(row.get("stck_cntg_hour") or ""),
            "o": _to_float(row.get("stck_oprc")) or c,
            "h": _to_float(row.get("stck_hgpr")) or c,
            "l": _to_float(row.get("stck_lwpr")) or c,
            "c": c,
            "v": _to_float(row.get("cntg_vol")) or 0.0,
        })
    out.sort(key=lambda b: (b["d"], b["t"]))
    return out


def _resample_bars(minute_bars: list[dict]) -> list[dict]:
    """1분봉 → BAR_INTERVAL_MINUTES분봉(OHLCV). 버킷=(일자, N분슬롯). 부분봉 최신가 반영.

    N=60 이면 slot=(HH*60+MM)//60=HH → 기존 *시 단위* 버킷과 비트-동일(회귀 보존).
    """
    n = BAR_INTERVAL_MINUTES
    buckets: dict[tuple[str, int], dict] = {}
    order: list[tuple[str, int]] = []
    for b in minute_bars:
        t = b["t"] or "000000"
        slot = (int(t[:2]) * 60 + int(t[2:4])) // n     # N분 슬롯(60→시단위 동일)
        key = (b["d"], slot)
        if key not in buckets:
            hhmm = f"{(slot * n) // 60:02d}{(slot * n) % 60:02d}00"
            buckets[key] = {"d": b["d"], "t": hhmm,
                            "o": b["o"], "h": b["h"], "l": b["l"], "c": b["c"], "v": b["v"]}
            order.append(key)
        else:
            agg = buckets[key]
            agg["h"] = max(agg["h"], b["h"]); agg["l"] = min(agg["l"], b["l"])
            agg["c"] = b["c"]; agg["v"] += b["v"]      # close=최신 → 부분봉도 갱신
    return [buckets[k] for k in order]


# ── ⓑ 분봉 fetch 레이트 안전판 (≤2/s + EGW00201 백오프) ──────────────────────
#   프로브① 실측: KIS 모의 분봉 조회는 ~3/s 에서 EGW00201. 멀티콜 fetch 가 한도를
#   넘지 않도록 전역 직렬 페이싱 + 초당한도 초과(EGW00201) 시 지수 백오프 재시도.
_FETCH_MIN_INTERVAL = 0.5            # ≤2/s
_fetch_lock = asyncio.Lock()
_last_fetch_ts = [0.0]


async def _paced_inquire(client: Any, symbol: str, *, date: str, hour: str) -> dict:
    """inquire-time-dailychartprice 를 ≤2/s 페이싱 + EGW00201 백오프(최대 4회)로 호출."""
    for attempt in range(4):
        async with _fetch_lock:                        # 전역 직렬화(초당한도 보호)
            gap = _time.perf_counter() - _last_fetch_ts[0]
            if gap < _FETCH_MIN_INTERVAL:
                await asyncio.sleep(_FETCH_MIN_INTERVAL - gap)
            _last_fetch_ts[0] = _time.perf_counter()
        try:
            raw = await client.inquire_time_dailychartprice(symbol, date=date, hour=hour)
            return raw if isinstance(raw, dict) else {}
        except Exception as exc:  # noqa: BLE001
            if "EGW00201" in str(exc) and attempt < 3:
                await asyncio.sleep(0.6 * (attempt + 1))   # 0.6/1.2/1.8s 백오프
                continue
            raise
    return {}


async def _fetch_full_day_minutes(client: Any, symbol: str, date: str) -> list[dict]:
    """하루 전체 분봉 — hour 를 과거로 옮기며 여러 콜로 덮음. ≤2/s 페이싱 + EGW00201 백오프."""
    seen: dict[str, dict] = {}
    hour = "153000"
    for _ in range(5):                                 # 09:00~15:30 커버(여유 5콜)
        raw = await _paced_inquire(client, symbol, date=date, hour=hour)
        bars = _parse_intraday_ohlcv(raw if isinstance(raw, dict) else {})
        if not bars:
            break
        for b in bars:
            seen[b["t"]] = b
        earliest = min(b["t"] for b in bars)
        if earliest <= "090100":
            break
        hour = f"{earliest[:4]}00"                      # 더 과거 창으로
    return sorted(seen.values(), key=lambda b: b["t"])


async def _prior_days_bars(client: Any, symbol: str, now: datetime) -> list[dict]:
    """과거 _prior_days() *데이터 있는* 거래일의 N분봉(캐시). 하루 1회만 채움.

    ★달력 캘린더 불필요 — 날짜를 역행하며 분봉이 *실제로 있는 날* 만 카운트(주말·공휴일 자동 skip).
    캐시 키에 BAR_INTERVAL_MINUTES 포함 → 봉주기 바꾸면 캐시 자동 분리.
    """
    from datetime import timedelta
    asof = to_kst(now)
    asof_key = asof.strftime("%Y%m%d")
    ck = (symbol, asof_key, BAR_INTERVAL_MINUTES)
    if ck in _prior_bars_cache:
        return _prior_bars_cache[ck]
    out: list[dict] = []
    collected = 0
    need_days = _prior_days()
    for back in range(1, _prior_max_cal() + 1):
        if collected >= need_days:
            break
        d = (asof - timedelta(days=back)).strftime("%Y%m%d")
        try:
            mins = await _fetch_full_day_minutes(client, symbol, d)
        except Exception as exc:  # noqa: BLE001 — 그 날 실패는 skip, 다른 날로.
            _log.info("[kis-%dm] prior %s %s unavailable: %s", BAR_INTERVAL_MINUTES, symbol, d, _redact(str(type(exc).__name__)))
            continue
        if not mins:                                   # 데이터 없는 날(주말/공휴일) → skip
            continue
        out.extend(_resample_bars(mins))
        collected += 1
    out.sort(key=lambda b: (b["d"], b["t"]))
    _prior_bars_cache[ck] = out
    return out


# 하위호환 alias.
_prior_days_60m = _prior_days_bars


async def warmup_bars_cache(symbols: list[str], *, client: Any, now: datetime | None = None) -> dict[str, int]:
    """개장 전(예: 08:40) 과거일 N분봉 캐시 워밍업. 종목→과거 N분봉 수. 봇 미가동·주문 0.

    ★스케줄러 연결(08:40 트리거)은 background_driver/scheduler 변경이 필요해 *별도 1파일 작업*.
    미연결 시에도 각 종목 첫 틱에서 lazy 워밍(첫 틱만 느림).
    """
    now = now or datetime.now(timezone.utc)
    result: dict[str, int] = {}
    for sym in symbols:
        try:
            bars = await _prior_days_bars(client, sym, now)
            result[sym] = len(bars)
        except Exception as exc:  # noqa: BLE001
            _log.info("[kis-%dm] warmup %s 실패: %s", BAR_INTERVAL_MINUTES, sym, _redact(str(type(exc).__name__)))
            result[sym] = 0
    return result


# 하위호환 alias.
warmup_60m_cache = warmup_bars_cache


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

    if _USE_INTRADAY_RESAMPLE:
        # ── ⓑ N분봉(BAR_INTERVAL_MINUTES) 멀티데이 경로 ──
        date = to_kst(now).strftime("%Y%m%d")
        try:
            today_min = await _fetch_full_day_minutes(client, symbol, date)
            today_60m = _resample_bars(today_min)           # 부분봉 포함
            prior_60m = await _prior_days_bars(client, symbol, now)  # 캐시(하루 1회)
        except Exception as exc:  # noqa: BLE001 — N분봉 실패 = skip(임의 매수 금지).
            _log.info("[kis-%dm] bars unavailable for %s: %s",
                      BAR_INTERVAL_MINUTES, symbol, _redact(f"{type(exc).__name__}"))
            return None, quote
        bars60 = prior_60m + today_60m
        if len(bars60) < _RECENT_BARS:                      # 데이터 부족 → skip
            _log.info("[kis-%dm] %s %d분봉 %d개<%d — 신호 skip(진입 금지).",
                      BAR_INTERVAL_MINUTES, symbol, BAR_INTERVAL_MINUTES, len(bars60), _RECENT_BARS)
            return None, quote
        recent_closes = tuple(b["c"] for b in bars60[-_RECENT_BARS:])
        # ORB = 당일 09:00–09:30 1분봉 레인지(09:30부터 돌파). 없으면 첫 N분봉으로 근사.
        orb_min = [b for b in today_min if _ORB_START <= b["t"] < _ORB_END]
        if orb_min:
            orh: float | None = max(b["h"] for b in orb_min)
            orl: float | None = min(b["l"] for b in orb_min)
        elif today_60m:
            orh = today_60m[0]["h"]; orl = today_60m[0]["l"]
        else:
            orh = quote.high; orl = quote.low
        vwap = quote.vwap if quote.vwap else (sum(recent_closes) / len(recent_closes))
        avg_vol = quote.volume
    else:
        # ── legacy 1분봉 당일 경로(BAR_INTERVAL_MINUTES=0/1, 롤백·하위호환 보존) ──
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
            orh = max(opening)
            orl = min(opening)
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

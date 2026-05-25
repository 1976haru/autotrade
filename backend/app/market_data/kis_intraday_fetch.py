"""KIS-INTRADAY-100-VALIDATION-01 — KIS 분봉 응답 파싱 / resample (순수 함수, read-only).

주식일별분봉조회 [국내주식-213] (TR FHKST03010230) 의 `output2` 분봉 배열을 표준 OHLCV
레코드로 매핑하고, 1분봉을 5분봉으로 resample 한다.

**본 모듈은 네트워크 / broker / OrderExecutor / route_order / httpx / requests 를
import 하지 않는다** — 순수 파싱 / 집계 함수만 둔다. 실제 KIS read-only 호출은
`scripts/collect_kis_intraday_ohlcv.py` 가 `KisClient.inquire_time_dailychartprice`
(read-only 시세) 로 수행한다. **주문 API 호출 0건.**

KIS output2 row 필드 (확인됨):
- stck_bsop_date  : 영업일자 (YYYYMMDD)
- stck_cntg_hour  : 체결시간 (HHMMSS)
- stck_oprc       : 시가
- stck_hgpr       : 고가
- stck_lwpr       : 저가
- stck_prpr       : 현재가(=해당 분봉 종가)
- cntg_vol        : 체결 거래량
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any

_KST = timezone(timedelta(hours=9))


@dataclass(frozen=True)
class ParseResult:
    records: list[dict[str, Any]]
    raw_rows: int
    dropped_rows: int
    min_hour: str | None  # 이 호출에서 가장 이른 체결시간 (다음 backward 호출 기준)


def _to_float(v: Any) -> float | None:
    if v is None:
        return None
    s = str(v).strip().replace(",", "")
    if not s:
        return None
    try:
        return float(s)
    except ValueError:
        return None


def _iso_kst(date_yyyymmdd: str, hour_hhmmss: str) -> str | None:
    d = str(date_yyyymmdd).strip()
    h = str(hour_hhmmss).strip().zfill(6)
    if len(d) != 8 or len(h) != 6 or not d.isdigit() or not h.isdigit():
        return None
    try:
        dt = datetime(
            int(d[0:4]), int(d[4:6]), int(d[6:8]),
            int(h[0:2]), int(h[2:4]), int(h[4:6]), tzinfo=_KST)
    except ValueError:
        return None
    return dt.isoformat()


def parse_minute_rows(output2: Any, symbol: str) -> ParseResult:
    """KIS output2 분봉 배열 → 표준 OHLCV 레코드 리스트.

    가격이 0 이하이거나 파싱 불가한 padding/허봉 row 는 드롭(보정 아님)하고 카운트한다.
    가짜 값 생성 0 — 드롭만 한다.
    """
    rows = output2 if isinstance(output2, list) else []
    out: list[dict[str, Any]] = []
    dropped = 0
    min_hour: str | None = None
    for r in rows:
        if not isinstance(r, dict):
            dropped += 1
            continue
        date = str(r.get("stck_bsop_date", "")).strip()
        hour = str(r.get("stck_cntg_hour", "")).strip()
        o = _to_float(r.get("stck_oprc"))
        h = _to_float(r.get("stck_hgpr"))
        lo = _to_float(r.get("stck_lwpr"))
        c = _to_float(r.get("stck_prpr"))
        vol = _to_float(r.get("cntg_vol"))
        ts = _iso_kst(date, hour) if date and hour else None
        if ts is None or None in (o, h, lo, c) or min(o, h, lo, c) <= 0:  # type: ignore[arg-type]
            dropped += 1
            continue
        if min_hour is None or hour < min_hour:
            min_hour = hour
        out.append({
            "timestamp": ts, "symbol": symbol,
            "open": o, "high": h, "low": lo, "close": c,
            "volume": vol if vol is not None else 0.0,
        })
    return ParseResult(records=out, raw_rows=len(rows), dropped_rows=dropped, min_hour=min_hour)


def prev_minute_hour(hhmmss: str) -> str:
    """주어진 HHMMSS 의 1분 전 HHMMSS — backward 분봉 호출용. 09:00 미만이면 '085900'."""
    s = str(hhmmss).strip().zfill(6)
    try:
        t = datetime(2000, 1, 1, int(s[0:2]), int(s[2:4]), int(s[4:6]))
    except ValueError:
        return "085900"
    t = t - timedelta(minutes=1)
    return f"{t.hour:02d}{t.minute:02d}{t.second:02d}"


def resample_1m_to_5m(records: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """1분봉 레코드 → 5분봉. 같은 날 5분 버킷(00,05,10,...) 단위로 OHLCV 집계.

    open=버킷 첫 bar open, high=max, low=min, close=마지막 bar close, volume=합.
    timestamp 는 버킷 시작 분 (예: 09:00, 09:05).
    """
    buckets: dict[str, list[dict[str, Any]]] = {}
    order: list[str] = []
    for rec in records:
        try:
            dt = datetime.fromisoformat(str(rec["timestamp"]))
        except (ValueError, TypeError, KeyError):
            continue
        kst = dt.astimezone(_KST)
        bucket_min = (kst.minute // 5) * 5
        key_dt = kst.replace(minute=bucket_min, second=0, microsecond=0)
        key = key_dt.isoformat()
        if key not in buckets:
            buckets[key] = []
            order.append(key)
        buckets[key].append(rec)

    out: list[dict[str, Any]] = []
    for key in sorted(order):
        group = sorted(buckets[key], key=lambda r: r["timestamp"])
        sym = group[0].get("symbol", "")
        out.append({
            "timestamp": key,
            "symbol": sym,
            "open": group[0]["open"],
            "high": max(g["high"] for g in group),
            "low": min(g["low"] for g in group),
            "close": group[-1]["close"],
            "volume": sum(g.get("volume", 0.0) for g in group),
        })
    return out


def dedupe_by_timestamp(records: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """(symbol, timestamp) 중복 제거 + 시간순 정렬."""
    seen: set[tuple[str, str]] = set()
    out: list[dict[str, Any]] = []
    for r in records:
        key = (str(r.get("symbol", "")), str(r.get("timestamp", "")))
        if key in seen:
            continue
        seen.add(key)
        out.append(r)
    out.sort(key=lambda r: str(r.get("timestamp", "")))
    return out

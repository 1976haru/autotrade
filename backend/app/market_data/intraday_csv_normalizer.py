"""INTRADAY-DATA-02 — 증권사/HTS 분봉 CSV → 표준 OHLCV 스키마 정규화 (read-only).

운영자가 증권사 HTS / 외부 데이터에서 받은 분봉 CSV 는 컬럼명이 한글이거나 숫자에 쉼표/
"원" 이 섞여 있을 수 있다. 본 모듈은 그것을 표준 컬럼(timestamp,open,high,low,close,
volume)으로 *정규화* 한다. **값을 임의로 만들어내지 않으며**(가짜 데이터 생성 0), 파싱
불가 row 는 드롭(보정 아님)하고 카운트한다.

표준 스키마: timestamp,open,high,low,close,volume (+ 선택 symbol,vwap,market_regime,
time_phase,source). 본 모듈은 broker / OrderExecutor / route_order / KIS 주문 API 를
import·호출하지 않는다.
"""

from __future__ import annotations

import csv
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

# 한글/변형 컬럼명 → 표준 컬럼 매핑 (소문자/공백제거 후 비교).
_COLUMN_ALIASES: dict[str, str] = {
    # timestamp
    "timestamp": "timestamp", "datetime": "timestamp", "date": "timestamp",
    "time": "timestamp", "일자": "timestamp", "일시": "timestamp", "시간": "timestamp",
    "체결시간": "timestamp", "체결시각": "timestamp", "거래시간": "timestamp",
    "일자시간": "timestamp", "날짜": "timestamp",
    # open
    "open": "open", "시가": "open", "시작가": "open",
    # high
    "high": "high", "고가": "high", "최고가": "high",
    # low
    "low": "low", "저가": "low", "최저가": "low",
    # close
    "close": "close", "종가": "close", "현재가": "close", "체결가": "close", "종료가": "close",
    # volume
    "volume": "volume", "거래량": "volume", "체결량": "volume", "누적거래량": "volume",
    # optional passthrough
    "symbol": "symbol", "종목코드": "symbol", "종목": "symbol", "code": "symbol",
    "vwap": "vwap", "market_regime": "market_regime", "time_phase": "time_phase",
    "source": "source",
}

_REQUIRED = ("timestamp", "open", "high", "low", "close", "volume")


@dataclass(frozen=True)
class NormalizeReport:
    total_rows: int
    valid_rows: int
    dropped_rows: int
    dropped_ratio: float
    column_mapping: dict[str, str]
    missing_required: tuple[str, ...]
    reasons: tuple[str, ...] = ()
    contains_secret: bool = False
    _extra: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if self.contains_secret:
            raise ValueError("NormalizeReport.contains_secret must be False")


def _norm_key(col: str) -> str:
    return re.sub(r"\s+", "", str(col).strip().lower())


def _clean_number(val: Any) -> float | None:
    """쉼표 / "원" / 공백 제거 후 float. 빈 값/파싱불가 → None."""
    if val is None:
        return None
    s = str(val).strip()
    if not s:
        return None
    s = s.replace(",", "").replace("원", "").replace("주", "").replace("%", "").strip()
    s = s.replace("+", "")
    try:
        return float(s)
    except ValueError:
        return None


def build_column_mapping(header: list[str]) -> dict[str, str]:
    """원본 헤더 → 표준 컬럼 매핑 (인식된 것만)."""
    mapping: dict[str, str] = {}
    for col in header:
        std = _COLUMN_ALIASES.get(_norm_key(col))
        if std and std not in mapping.values():
            mapping[col] = std
    return mapping


def normalize_rows(
    header: list[str], rows: list[dict[str, Any]], *, default_symbol: str | None = None,
) -> tuple[list[dict[str, Any]], NormalizeReport]:
    """원본 행을 표준 OHLCV dict 리스트로 정규화."""
    mapping = build_column_mapping(header)
    std_cols = set(mapping.values())
    missing = tuple(c for c in _REQUIRED if c not in std_cols)
    reasons: list[str] = []
    if missing:
        reasons.append(f"필수 컬럼 누락: {', '.join(missing)}")
        return [], NormalizeReport(
            len(rows), 0, len(rows), 1.0 if rows else 0.0, mapping, missing, tuple(reasons))

    out: list[dict[str, Any]] = []
    dropped = 0
    for row in rows:
        rec: dict[str, Any] = {}
        for orig, std in mapping.items():
            rec[std] = row.get(orig)
        ts = rec.get("timestamp")
        ts = str(ts).strip() if ts is not None else ""
        nums = {k: _clean_number(rec.get(k)) for k in ("open", "high", "low", "close", "volume")}
        if not ts or any(nums[k] is None for k in ("open", "high", "low", "close")):
            dropped += 1
            continue
        norm = {
            "timestamp": ts,
            "open": nums["open"], "high": nums["high"], "low": nums["low"],
            "close": nums["close"], "volume": nums["volume"] if nums["volume"] is not None else 0,
        }
        if "symbol" in rec and rec["symbol"]:
            norm["symbol"] = str(rec["symbol"]).strip()
        elif default_symbol:
            norm["symbol"] = default_symbol
        for opt in ("vwap", "market_regime", "time_phase", "source"):
            if opt in rec and rec[opt] not in (None, ""):
                norm[opt] = rec[opt]
        out.append(norm)

    total = len(rows)
    if dropped:
        reasons.append(f"파싱 불가 row {dropped}건 드롭(값 보정 아님)")
    return out, NormalizeReport(
        total_rows=total, valid_rows=len(out), dropped_rows=dropped,
        dropped_ratio=round(dropped / max(1, total), 4), column_mapping=mapping,
        missing_required=(), reasons=tuple(reasons))


def normalize_intraday_csv(
    path: str | Path, *, default_symbol: str | None = None,
) -> tuple[list[dict[str, Any]], NormalizeReport]:
    """CSV 파일을 읽어 표준 OHLCV dict 리스트로 정규화."""
    p = Path(path)
    if not p.exists():
        return [], NormalizeReport(0, 0, 0, 0.0, {}, _REQUIRED, (f"파일 없음: {p}",))
    # 인코딩: HTS CSV 는 cp949/utf-8-sig 가능 — 둘 다 시도.
    text = None
    for enc in ("utf-8-sig", "cp949", "utf-8"):
        try:
            text = p.read_text(encoding=enc)
            break
        except (UnicodeDecodeError, OSError):
            continue
    if text is None:
        return [], NormalizeReport(0, 0, 0, 0.0, {}, _REQUIRED, ("인코딩 파싱 불가",))
    reader = csv.DictReader(text.splitlines())
    header = list(reader.fieldnames or [])
    rows = list(reader)
    sym = default_symbol or _symbol_from_filename(p)
    return normalize_rows(header, rows, default_symbol=sym)


def _symbol_from_filename(p: Path) -> str | None:
    """005930_5m.csv / 005930.csv → '005930'."""
    stem = p.stem
    m = re.match(r"^(\d{6})", stem)
    return m.group(1) if m else None


def bar_size_from_filename(path: str | Path) -> str | None:
    """005930_5m.csv → '5m', 005930_1m.csv → '1m'. 못 찾으면 None."""
    stem = Path(path).stem.lower()
    m = re.search(r"_(\d+)(m|min|h)$", stem)
    if not m:
        return None
    n = m.group(1)
    unit = "m" if m.group(2) in ("m", "min") else "h"
    return f"{n}{unit}"


def to_dict(r: NormalizeReport) -> dict[str, Any]:
    return {
        "total_rows": r.total_rows, "valid_rows": r.valid_rows,
        "dropped_rows": r.dropped_rows, "dropped_ratio": r.dropped_ratio,
        "column_mapping": r.column_mapping, "missing_required": list(r.missing_required),
        "reasons": list(r.reasons), "contains_secret": r.contains_secret,
    }

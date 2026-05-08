"""Watchlist 비즈니스 로직 (#18).

심볼 정규화, 200개 한도, CSV 파싱을 routes에서 분리. 모든 함수는 사용자에게
한국어로 표시 가능한 에러 메시지를 raise한다 (`WatchlistError`).

CLAUDE.md 절대 원칙 5/7 — Watchlist는 universe 후보군이며, RiskManager /
PermissionGate / OrderExecutor 분기에는 영향을 주지 않는다. 본 모듈은
broker / RiskManager / 주문 라우터를 import하지 않는다.
"""

from __future__ import annotations

import csv
import io
from dataclasses import dataclass

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.db.models import Watchlist, WatchlistItem


# 한 watchlist에 등록 가능한 종목 수 한도 (#18). 운영자가 50~200을 권장 범위로
# 가져가되, 본 코드는 200을 절대 한도로 강제한다 — universe가 너무 넓어지면
# Strategy/Agent 후보군 의미가 흐려진다.
WATCHLIST_MAX_ITEMS         = 200
WATCHLIST_RECOMMENDED_ITEMS = 50

# 종목 코드 길이 한도. KRX 6자리(예: '005930') + 외국 ticker 여유(예: 'AAPL')
# + 일부 ETF/ETN 케이스 → 16자로 충분. MarketBar.symbol과 동일.
SYMBOL_MAX_LENGTH = 16

# Watchlist 이름 한도 — DB 컬럼 String(64)와 일치.
NAME_MAX_LENGTH = 64


class WatchlistError(ValueError):
    """사용자에게 그대로 표시 가능한 한국어 에러 메시지."""


@dataclass
class CsvImportResult:
    added:              int
    skipped:            int
    invalid:            int
    total_after_import: int
    errors:             list[str]


def normalize_symbol(raw: str | None) -> str:
    """trim + uppercase + 길이 검증.

    - None / 빈 문자열 / 공백만 → WatchlistError("종목코드를 입력해 주세요.")
    - 16자 초과 → WatchlistError("종목코드가 너무 깁니다 (최대 16자).")
    """
    if raw is None:
        raise WatchlistError("종목코드를 입력해 주세요.")
    s = raw.strip().upper()
    if not s:
        raise WatchlistError("종목코드를 입력해 주세요.")
    if len(s) > SYMBOL_MAX_LENGTH:
        raise WatchlistError(f"종목코드가 너무 깁니다 (최대 {SYMBOL_MAX_LENGTH}자).")
    return s


def normalize_name(raw: str | None) -> str:
    """Watchlist 이름 정규화 — trim + 비어있지 않음 + 길이 검증."""
    if raw is None:
        raise WatchlistError("관심종목 목록 이름을 입력해 주세요.")
    s = raw.strip()
    if not s:
        raise WatchlistError("관심종목 목록 이름을 입력해 주세요.")
    if len(s) > NAME_MAX_LENGTH:
        raise WatchlistError(f"관심종목 목록 이름이 너무 깁니다 (최대 {NAME_MAX_LENGTH}자).")
    return s


def _trim_optional(raw: str | None, *, max_length: int) -> str | None:
    if raw is None:
        return None
    s = raw.strip()
    if not s:
        return None
    return s[:max_length]


def list_watchlists(db: Session) -> list[Watchlist]:
    """전체 watchlist 목록 — 생성 순(역순)."""
    rows = db.execute(select(Watchlist).order_by(Watchlist.created_at.desc())).scalars().all()
    return list(rows)


def get_watchlist(db: Session, watchlist_id: int) -> Watchlist:
    row = db.get(Watchlist, watchlist_id)
    if row is None:
        raise WatchlistError("해당 관심종목 목록을 찾을 수 없습니다.")
    return row


def list_items(db: Session, watchlist_id: int) -> list[WatchlistItem]:
    rows = db.execute(
        select(WatchlistItem)
        .where(WatchlistItem.watchlist_id == watchlist_id)
        .order_by(WatchlistItem.created_at.asc())
    ).scalars().all()
    return list(rows)


def count_items(db: Session, watchlist_id: int) -> int:
    return len(list_items(db, watchlist_id))


def create_watchlist(
    db:          Session,
    *,
    name:        str,
    description: str | None = None,
    is_active:   bool = False,
) -> Watchlist:
    nm = normalize_name(name)
    desc = _trim_optional(description, max_length=255)
    if is_active:
        _deactivate_all(db)
    row = Watchlist(name=nm, description=desc, is_active=bool(is_active))
    db.add(row)
    db.flush()
    return row


def update_watchlist(
    db:           Session,
    watchlist_id: int,
    *,
    name:         str | None = None,
    description:  str | None = None,
    is_active:    bool | None = None,
) -> Watchlist:
    from datetime import datetime, timezone
    row = get_watchlist(db, watchlist_id)
    if name is not None:
        row.name = normalize_name(name)
    if description is not None:
        row.description = _trim_optional(description, max_length=255)
    if is_active is True and not row.is_active:
        _deactivate_all(db)
        row.is_active = True
    elif is_active is False:
        row.is_active = False
    row.updated_at = datetime.now(timezone.utc)
    db.flush()
    return row


def delete_watchlist(db: Session, watchlist_id: int) -> None:
    row = get_watchlist(db, watchlist_id)
    # ondelete=CASCADE는 PG에서 자동, SQLite에서는 ORM cascade가 안전.
    for item in list_items(db, watchlist_id):
        db.delete(item)
    db.delete(row)
    db.flush()


def _deactivate_all(db: Session) -> None:
    rows = db.execute(select(Watchlist).where(Watchlist.is_active.is_(True))).scalars().all()
    for r in rows:
        r.is_active = False


def add_item(
    db:           Session,
    watchlist_id: int,
    *,
    symbol:       str,
    name:         str | None = None,
    market:       str | None = None,
    sector:       str | None = None,
    note:         str | None = None,
) -> WatchlistItem:
    get_watchlist(db, watchlist_id)  # exists?
    sym = normalize_symbol(symbol)

    if count_items(db, watchlist_id) >= WATCHLIST_MAX_ITEMS:
        raise WatchlistError(
            f"관심종목은 한 목록당 최대 {WATCHLIST_MAX_ITEMS}개까지 등록할 수 있습니다."
        )

    existing = db.execute(
        select(WatchlistItem)
        .where(WatchlistItem.watchlist_id == watchlist_id, WatchlistItem.symbol == sym)
    ).scalar_one_or_none()
    if existing is not None:
        raise WatchlistError("이미 등록된 종목입니다.")

    row = WatchlistItem(
        watchlist_id=watchlist_id,
        symbol=sym,
        name=_trim_optional(name,   max_length=64),
        market=_trim_optional(market, max_length=32),
        sector=_trim_optional(sector, max_length=64),
        note=_trim_optional(note,   max_length=255),
    )
    db.add(row)
    db.flush()
    return row


def remove_item(db: Session, watchlist_id: int, item_id: int) -> None:
    row = db.get(WatchlistItem, item_id)
    if row is None or row.watchlist_id != watchlist_id:
        raise WatchlistError("해당 종목을 찾을 수 없습니다.")
    db.delete(row)
    db.flush()


def import_csv(
    db:           Session,
    watchlist_id: int,
    csv_text:     str,
) -> CsvImportResult:
    """text/csv 본문을 파싱해 한 watchlist에 batch 추가.

    - symbol 컬럼만 필수
    - 중복(이미 등록된 symbol) → skipped
    - symbol 누락/너무 김 → invalid + errors[]
    - 200개 한도 초과 시 추가 거부 (errors에 표시)

    헤더가 없는 CSV는 첫 컬럼을 symbol로 간주.
    """
    get_watchlist(db, watchlist_id)

    if csv_text is None or not csv_text.strip():
        raise WatchlistError("CSV 내용이 비어있습니다.")

    # 한국 운영자가 Excel에서 export하면 BOM이 붙는 경우가 있음 — 제거.
    if csv_text.startswith("﻿"):
        csv_text = csv_text[1:]

    reader = csv.DictReader(io.StringIO(csv_text))
    if not reader.fieldnames:
        raise WatchlistError("CSV 헤더(첫 줄)가 비어있습니다. 최소 'symbol' 컬럼이 필요합니다.")

    # 헤더는 lower-cased로 비교 — 'Symbol' / 'SYMBOL'도 허용.
    fieldnames_lower = {(fn or "").strip().lower(): fn for fn in reader.fieldnames}
    if "symbol" not in fieldnames_lower:
        raise WatchlistError("CSV에 'symbol' 컬럼이 필요합니다.")

    added = 0
    skipped = 0
    invalid = 0
    errors: list[str] = []

    # 매번 count_items 쿼리는 비효율. 본 import 시작 시점 한 번 + 추가될 때마다 +1.
    current = count_items(db, watchlist_id)

    for row_index, raw_row in enumerate(reader, start=2):
        symbol_raw = raw_row.get(fieldnames_lower["symbol"])
        try:
            sym = normalize_symbol(symbol_raw)
        except WatchlistError as e:
            invalid += 1
            errors.append(f"{row_index}행: {e}")
            continue

        if current >= WATCHLIST_MAX_ITEMS:
            invalid += 1
            errors.append(
                f"{row_index}행: 관심종목은 한 목록당 최대 {WATCHLIST_MAX_ITEMS}개까지 "
                "등록할 수 있습니다 (이후 행 무시)."
            )
            # 한도 초과 후엔 남은 행을 단순 카운트만 — DB에 추가하지 않는다.
            remaining = sum(1 for _ in reader)
            invalid += remaining
            break

        existing = db.execute(
            select(WatchlistItem)
            .where(WatchlistItem.watchlist_id == watchlist_id, WatchlistItem.symbol == sym)
        ).scalar_one_or_none()
        if existing is not None:
            skipped += 1
            continue

        def _opt(key: str) -> str | None:
            real = fieldnames_lower.get(key)
            return raw_row.get(real) if real else None

        item = WatchlistItem(
            watchlist_id=watchlist_id,
            symbol=sym,
            name=_trim_optional(_opt("name"),   max_length=64),
            market=_trim_optional(_opt("market"), max_length=32),
            sector=_trim_optional(_opt("sector"), max_length=64),
            note=_trim_optional(_opt("note"),   max_length=255),
        )
        db.add(item)
        added += 1
        current += 1

    db.flush()

    return CsvImportResult(
        added=added,
        skipped=skipped,
        invalid=invalid,
        total_after_import=current,
        errors=errors,
    )


def watchlist_summary(db: Session) -> dict:
    """Dashboard 요약 — active watchlist + 대표 5종목.

    active가 없거나 비어있으면 None 필드 — 호출자가 빈 상태 표시.
    """
    active = db.execute(
        select(Watchlist).where(Watchlist.is_active.is_(True))
    ).scalar_one_or_none()
    watchlist_count = db.execute(select(func.count(Watchlist.id))).scalar_one() or 0
    if active is None:
        return {
            "active":             None,
            "active_item_count":  0,
            "top_symbols":        [],
            "watchlist_count":    int(watchlist_count),
            "max_items":          WATCHLIST_MAX_ITEMS,
            "recommended_items":  WATCHLIST_RECOMMENDED_ITEMS,
        }
    items = list_items(db, active.id)
    return {
        "active":            {"id": active.id, "name": active.name},
        "active_item_count": len(items),
        "top_symbols":       [it.symbol for it in items[:5]],
        "watchlist_count":   int(watchlist_count),
        "max_items":         WATCHLIST_MAX_ITEMS,
        "recommended_items": WATCHLIST_RECOMMENDED_ITEMS,
    }

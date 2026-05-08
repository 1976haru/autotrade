"""Watchlist CRUD + CSV import (#18).

Watchlist는 universe 후보군이며 주문 신호가 아니다. RiskManager / PermissionGate /
OrderExecutor 단일 진입점은 본 라우트의 영향을 받지 않는다 (CLAUDE.md 절대 원칙).

엔드포인트:
- GET    /api/watchlists                        — 전체 목록 + 각 종목 수
- POST   /api/watchlists                        — 생성
- GET    /api/watchlists/summary                — Dashboard 요약 (active + top 5)
- GET    /api/watchlists/{id}                   — 단건 + items
- PATCH  /api/watchlists/{id}                   — 이름/설명/활성 토글
- DELETE /api/watchlists/{id}                   — 삭제 (cascading)
- POST   /api/watchlists/{id}/items             — 종목 추가
- DELETE /api/watchlists/{id}/items/{item_id}   — 종목 삭제
- POST   /api/watchlists/{id}/import-csv        — CSV 일괄 추가 (text/csv body)
"""

from __future__ import annotations

from datetime import datetime

from fastapi import APIRouter, Body, Depends, HTTPException, Request
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.db.models import Watchlist, WatchlistItem
from app.db.session import get_db
from app.watchlist.service import (
    WATCHLIST_MAX_ITEMS,
    WATCHLIST_RECOMMENDED_ITEMS,
    WatchlistError,
    add_item,
    create_watchlist,
    delete_watchlist,
    get_watchlist,
    import_csv,
    list_items,
    list_watchlists,
    remove_item,
    update_watchlist,
    watchlist_summary,
)


router = APIRouter(prefix="/watchlists", tags=["watchlists"])


# ---------- DTOs ----------


class WatchlistItemOut(BaseModel):
    id:         int
    symbol:     str
    name:       str | None = None
    market:     str | None = None
    sector:     str | None = None
    note:       str | None = None
    created_at: datetime

    @classmethod
    def from_model(cls, m: WatchlistItem) -> "WatchlistItemOut":
        return cls(
            id=m.id, symbol=m.symbol, name=m.name, market=m.market,
            sector=m.sector, note=m.note, created_at=m.created_at,
        )


class WatchlistOut(BaseModel):
    id:          int
    name:        str
    description: str | None = None
    is_active:   bool
    item_count:  int
    created_at:  datetime
    updated_at:  datetime

    @classmethod
    def from_model(cls, m: Watchlist, item_count: int) -> "WatchlistOut":
        return cls(
            id=m.id, name=m.name, description=m.description, is_active=m.is_active,
            item_count=item_count, created_at=m.created_at, updated_at=m.updated_at,
        )


class WatchlistDetailOut(WatchlistOut):
    items: list[WatchlistItemOut] = []


class WatchlistListOut(BaseModel):
    watchlists:        list[WatchlistOut]
    max_items:         int = WATCHLIST_MAX_ITEMS
    recommended_items: int = WATCHLIST_RECOMMENDED_ITEMS


class WatchlistSummaryOut(BaseModel):
    active:            dict | None = None
    active_item_count: int
    top_symbols:       list[str]
    watchlist_count:   int
    max_items:         int
    recommended_items: int


class CreateWatchlistIn(BaseModel):
    name:        str
    description: str | None = None
    is_active:   bool = False


class UpdateWatchlistIn(BaseModel):
    name:        str  | None = None
    description: str  | None = None
    is_active:   bool | None = None


class AddItemIn(BaseModel):
    symbol: str = Field(..., description="종목코드 (자동으로 trim+uppercase)")
    name:   str | None = None
    market: str | None = None
    sector: str | None = None
    note:   str | None = None


class CsvImportOut(BaseModel):
    added:              int
    skipped:            int
    invalid:            int
    total_after_import: int
    errors:             list[str] = []


# ---------- helpers ----------


def _user_error(e: WatchlistError) -> HTTPException:
    return HTTPException(status_code=400, detail=str(e))


def _not_found(e: WatchlistError) -> HTTPException:
    return HTTPException(status_code=404, detail=str(e))


# ---------- endpoints ----------


@router.get("", response_model=WatchlistListOut)
def list_all(db: Session = Depends(get_db)) -> WatchlistListOut:
    rows = list_watchlists(db)
    out: list[WatchlistOut] = []
    for w in rows:
        out.append(WatchlistOut.from_model(w, item_count=len(list_items(db, w.id))))
    return WatchlistListOut(
        watchlists=out,
        max_items=WATCHLIST_MAX_ITEMS,
        recommended_items=WATCHLIST_RECOMMENDED_ITEMS,
    )


@router.get("/summary", response_model=WatchlistSummaryOut)
def get_summary(db: Session = Depends(get_db)) -> WatchlistSummaryOut:
    return WatchlistSummaryOut(**watchlist_summary(db))


@router.post("", response_model=WatchlistDetailOut, status_code=201)
def create(payload: CreateWatchlistIn, db: Session = Depends(get_db)) -> WatchlistDetailOut:
    try:
        row = create_watchlist(
            db,
            name=payload.name,
            description=payload.description,
            is_active=payload.is_active,
        )
    except WatchlistError as e:
        raise _user_error(e)
    db.commit()
    return WatchlistDetailOut(
        **WatchlistOut.from_model(row, item_count=0).model_dump(),
        items=[],
    )


@router.get("/{watchlist_id}", response_model=WatchlistDetailOut)
def get_one(watchlist_id: int, db: Session = Depends(get_db)) -> WatchlistDetailOut:
    try:
        row = get_watchlist(db, watchlist_id)
    except WatchlistError as e:
        raise _not_found(e)
    items = list_items(db, watchlist_id)
    return WatchlistDetailOut(
        **WatchlistOut.from_model(row, item_count=len(items)).model_dump(),
        items=[WatchlistItemOut.from_model(it) for it in items],
    )


@router.patch("/{watchlist_id}", response_model=WatchlistDetailOut)
def patch(watchlist_id: int, payload: UpdateWatchlistIn,
          db: Session = Depends(get_db)) -> WatchlistDetailOut:
    try:
        row = update_watchlist(
            db, watchlist_id,
            name=payload.name,
            description=payload.description,
            is_active=payload.is_active,
        )
    except WatchlistError as e:
        # 명시적으로 not-found vs validation 분기는 메시지가 같지 않으면 어렵다 —
        # 본 함수는 "찾을 수 없습니다"만 not-found, 나머지는 validation.
        if "찾을 수 없" in str(e):
            raise _not_found(e)
        raise _user_error(e)
    db.commit()
    items = list_items(db, watchlist_id)
    return WatchlistDetailOut(
        **WatchlistOut.from_model(row, item_count=len(items)).model_dump(),
        items=[WatchlistItemOut.from_model(it) for it in items],
    )


@router.delete("/{watchlist_id}", status_code=204)
def delete_one(watchlist_id: int, db: Session = Depends(get_db)) -> None:
    try:
        delete_watchlist(db, watchlist_id)
    except WatchlistError as e:
        raise _not_found(e)
    db.commit()


@router.post("/{watchlist_id}/items", response_model=WatchlistItemOut, status_code=201)
def post_item(watchlist_id: int, payload: AddItemIn,
              db: Session = Depends(get_db)) -> WatchlistItemOut:
    try:
        item = add_item(
            db, watchlist_id,
            symbol=payload.symbol, name=payload.name,
            market=payload.market, sector=payload.sector, note=payload.note,
        )
    except WatchlistError as e:
        if "찾을 수 없" in str(e):
            raise _not_found(e)
        raise _user_error(e)
    db.commit()
    return WatchlistItemOut.from_model(item)


@router.delete("/{watchlist_id}/items/{item_id}", status_code=204)
def delete_item(watchlist_id: int, item_id: int, db: Session = Depends(get_db)) -> None:
    try:
        remove_item(db, watchlist_id, item_id)
    except WatchlistError as e:
        raise _not_found(e)
    db.commit()


@router.post("/{watchlist_id}/import-csv", response_model=CsvImportOut)
async def csv_import(
    watchlist_id: int,
    request:      Request,
    db:           Session = Depends(get_db),
    csv_text:     str | None = Body(default=None, embed=True, alias="csv"),
) -> CsvImportOut:
    """CSV 일괄 import.

    두 입력 형태 모두 허용:
    - `Content-Type: text/csv` 본문 raw — 운영자가 CLI로 보낼 때 자연스럽다.
    - `application/json` 본문 `{"csv": "..."}` — frontend textarea가 보낸다.
    """
    body_text = csv_text
    if body_text is None:
        # JSON이 아니라 raw text/csv로 들어온 경우.
        raw = await request.body()
        try:
            body_text = raw.decode("utf-8")
        except UnicodeDecodeError:
            raise HTTPException(status_code=400, detail="CSV는 UTF-8 인코딩이어야 합니다.")

    try:
        result = import_csv(db, watchlist_id, body_text)
    except WatchlistError as e:
        if "찾을 수 없" in str(e):
            raise _not_found(e)
        raise _user_error(e)
    db.commit()
    return CsvImportOut(
        added=result.added,
        skipped=result.skipped,
        invalid=result.invalid,
        total_after_import=result.total_after_import,
        errors=result.errors,
    )

"""Theme signals routes (#22).

CLAUDE.md 절대 원칙 — 본 라우트는 *데이터 후보 필터*만 다룬다. 어떤
endpoint도 BUY/SELL/HOLD 결정을 반환하지 않으며, 주문 흐름과 분리되어 있다.

엔드포인트:
- GET  /api/themes/signals             — 최근 ThemeSignal 행 (grade/provider 필터)
- GET  /api/themes/summary             — Dashboard 요약 (grade 분포 + top STRONG)
- POST /api/themes/scan                — Mock provider로 신호 생성 + DB 영구화
                                         (provider=mock만 활성, alpha는 stub)
"""

from __future__ import annotations

from datetime import datetime

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.db.models import ThemeSignal
from app.db.session import get_db
from app.themes.filter import (
    CandidateSymbol,
    ThemeFilter,
    list_recent_signals,
    signals_summary,
)
from app.themes.providers import default_provider


router = APIRouter(prefix="/themes", tags=["themes"])


# ---------- DTO ----------


class ThemeSignalOut(BaseModel):
    id:              int
    created_at:      datetime
    theme:           str
    keywords:        list[str]
    related_symbols: list[str]
    score:           int
    grade:           str
    confidence:      int
    source:          str
    provider:        str
    summary:         str | None = None
    used_for_order:  bool

    @classmethod
    def from_model(cls, m: ThemeSignal) -> "ThemeSignalOut":
        return cls(
            id=m.id, created_at=m.created_at, theme=m.theme,
            keywords=list(m.keywords or []),
            related_symbols=list(m.related_symbols or []),
            score=m.score, grade=m.grade, confidence=m.confidence,
            source=m.source, provider=m.provider, summary=m.summary,
            used_for_order=m.used_for_order,
        )


class ThemeSignalsListOut(BaseModel):
    signals:        list[ThemeSignalOut]
    used_for_order: bool = Field(default=False,
        description="invariant — 본 데이터는 주문에 사용되지 않음 (#22)")


class ThemeSummaryOut(BaseModel):
    total:          int
    by_grade:       dict[str, int]
    top_themes:     list[dict]
    used_for_order: bool


class CandidateSymbolOut(BaseModel):
    symbol:     str
    themes:     list[str]
    best_score: int
    best_grade: str


class ThemeScanIn(BaseModel):
    universe: list[str] | None = None
    limit:    int = 20


class ThemeScanOut(BaseModel):
    persisted:           int
    records:             list[ThemeSignalOut]
    candidate_symbols:   list[CandidateSymbolOut]
    used_for_order:      bool = False
    provider:            str
    is_provider_enabled: bool


# ---------- routes ----------


@router.get("/signals", response_model=ThemeSignalsListOut)
def list_signals(
    limit:    int = Query(50, ge=1, le=500),
    grade:    str | None = Query(None, pattern="^(STRONG|WATCH|WEAK|IGNORE)$"),
    provider: str | None = Query(None, max_length=32),
    db:       Session = Depends(get_db),
) -> ThemeSignalsListOut:
    rows = list_recent_signals(db, limit=limit, grade=grade, provider=provider)
    return ThemeSignalsListOut(
        signals=[ThemeSignalOut.from_model(r) for r in rows],
        used_for_order=False,
    )


@router.get("/summary", response_model=ThemeSummaryOut)
def get_summary(db: Session = Depends(get_db)) -> ThemeSummaryOut:
    return ThemeSummaryOut(**signals_summary(db))


@router.post("/scan", response_model=ThemeScanOut)
def scan(
    payload: ThemeScanIn,
    db:      Session = Depends(get_db),
) -> ThemeScanOut:
    """기본 provider로 테마 신호를 생성·영구화. 본 PR 단계에서는 Mock 전용.

    universe가 주어지면 그 안의 종목만 candidate로 좁힌다. 빈 universe([])는
    "후보 없음"으로 명시적 처리. None은 "전체 universe".

    절대 원칙 — 결과는 BUY/SELL/HOLD가 아닌 candidate symbol 리스트.
    `used_for_order=False` invariant 유지.
    """
    provider = default_provider()
    filt = ThemeFilter(provider)

    records = filt.scan(universe=payload.universe, limit=payload.limit)
    persisted = filt.persist(db, records)
    candidates: list[CandidateSymbol] = filt.candidate_symbols(
        universe=payload.universe, limit=payload.limit,
    )

    if persisted > 0:
        db.commit()

    return ThemeScanOut(
        persisted=persisted,
        records=[
            ThemeSignalOut(
                id=0,  # records는 in-memory representation, persist 후 별도 조회 필요시 list_signals 사용
                created_at=r.created_at or datetime.now(),
                theme=r.theme, keywords=r.keywords,
                related_symbols=r.related_symbols,
                score=r.score, grade=r.grade, confidence=r.confidence,
                source=r.source, provider=r.provider,
                summary=r.summary, used_for_order=False,
            ) for r in records
        ],
        candidate_symbols=[
            CandidateSymbolOut(**c.to_dict()) for c in candidates
        ],
        used_for_order=False,
        provider=provider.name,
        is_provider_enabled=provider.is_enabled(),
    )

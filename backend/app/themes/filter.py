"""ThemeFilter — 후보 종목 필터 (#22).

CLAUDE.md 절대 원칙 — ThemeFilter는 **BUY/SELL/HOLD를 반환하지 않는다**.
candidate symbol 리스트만 반환하며, 주문 결정은 RiskManager → PermissionGate →
OrderExecutor 단일 경로에서만 만들어진다.

본 모듈은:
- broker / RiskManager / PermissionGate / OrderExecutor를 import하지 않는다.
- Provider abstraction(`providers.py`)을 입력으로 받아 candidate를 만든다.
- 결과는 *제안*일 뿐이며, ThemeSignal.used_for_order는 항상 False로 시작.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.models import ThemeSignal
from app.themes.providers import ThemeProvider, ThemeRecord


# ThemeFilter가 candidate로 채택할 grade. STRONG / WATCH 만 universe에 포함.
DEFAULT_CANDIDATE_GRADES = ("STRONG", "WATCH")


@dataclass(frozen=True)
class CandidateSymbol:
    symbol:     str
    themes:     list[str]   # 후보로 만든 테마들 (한 종목이 여러 테마에 속할 수 있음)
    best_score: int         # 그 종목에 매핑된 최대 score
    best_grade: str         # 그 종목에 매핑된 최고 grade (STRONG > WATCH > WEAK)

    def to_dict(self) -> dict:
        return {
            "symbol":     self.symbol,
            "themes":     list(self.themes),
            "best_score": self.best_score,
            "best_grade": self.best_grade,
        }


class ThemeFilter:
    """Provider → ThemeRecord → candidate symbol 변환기.

    호출자가 watchlist universe(또는 None=전체)를 주입하면 그 안에서만 후보를
    좁힌다. 결과는 BUY/SELL/HOLD가 아닌 candidate symbol 리스트.
    """

    def __init__(
        self,
        provider: ThemeProvider,
        *,
        candidate_grades: tuple[str, ...] = DEFAULT_CANDIDATE_GRADES,
    ):
        self.provider = provider
        self.candidate_grades = tuple(candidate_grades)

    def scan(
        self,
        *,
        universe: list[str] | None = None,
        limit:    int = 20,
        now:      datetime | None = None,
    ) -> list[ThemeRecord]:
        """Provider 호출 + universe 적용 결과 반환. 빈 universe면 빈 리스트."""
        if universe is not None and not universe:
            return []
        if not self.provider.is_enabled():
            return []
        return self.provider.scan(universe=universe, limit=limit, now=now)

    def candidate_symbols(
        self,
        *,
        universe: list[str] | None = None,
        limit:    int = 20,
        now:      datetime | None = None,
    ) -> list[CandidateSymbol]:
        """ThemeRecord에서 candidate symbol 리스트 추출.

        같은 symbol이 여러 테마에 등장하면 합산 — best_score / best_grade로 표면화.
        BUY/SELL/HOLD 신호 절대 반환 안 함.
        """
        records = self.scan(universe=universe, limit=limit, now=now)
        eligible = [r for r in records if r.grade in self.candidate_grades]

        by_symbol: dict[str, dict] = {}
        for r in eligible:
            for sym in r.related_symbols:
                slot = by_symbol.setdefault(sym, {
                    "themes": [], "best_score": 0, "best_grade": "WEAK",
                })
                slot["themes"].append(r.theme)
                if r.score > slot["best_score"]:
                    slot["best_score"] = r.score
                if _grade_rank(r.grade) > _grade_rank(slot["best_grade"]):
                    slot["best_grade"] = r.grade

        out = [
            CandidateSymbol(
                symbol=sym,
                themes=info["themes"],
                best_score=info["best_score"],
                best_grade=info["best_grade"],
            )
            for sym, info in by_symbol.items()
        ]
        out.sort(key=lambda c: (-c.best_score, c.symbol))
        return out

    def persist(
        self,
        db:       Session,
        records:  list[ThemeRecord],
        *,
        now:      datetime | None = None,
    ) -> int:
        """ThemeRecord를 `theme_signals` 테이블에 영구화. used_for_order는 영구히 False.

        같은 (provider, theme, created_at)이라도 본 PR은 별행 추가 — 중복 제거는
        후속 작업 (`docs/backlog.md`).
        """
        if not records:
            return 0
        if now is None:
            now = datetime.now(timezone.utc)
        for r in records:
            db.add(ThemeSignal(
                created_at=r.created_at or now,
                theme=r.theme,
                keywords=list(r.keywords),
                related_symbols=list(r.related_symbols),
                score=r.score,
                grade=r.grade,
                confidence=r.confidence,
                source=r.source,
                provider=r.provider,
                summary=r.summary,
                raw=r.raw,
                used_for_order=False,
            ))
        db.flush()
        return len(records)


def _grade_rank(grade: str) -> int:
    return {"STRONG": 3, "WATCH": 2, "WEAK": 1, "IGNORE": 0}.get(grade, 0)


# ---------- DB 조회 helper (라우트용) ----------


def list_recent_signals(
    db:    Session,
    *,
    limit:    int = 50,
    grade:    str | None = None,
    provider: str | None = None,
) -> list[ThemeSignal]:
    """최근 ThemeSignal 행. grade/provider로 옵션 필터."""
    stmt = select(ThemeSignal).order_by(ThemeSignal.created_at.desc())
    if grade:
        stmt = stmt.where(ThemeSignal.grade == grade)
    if provider:
        stmt = stmt.where(ThemeSignal.provider == provider)
    stmt = stmt.limit(limit)
    return list(db.execute(stmt).scalars().all())


def signals_summary(db: Session) -> dict:
    """Dashboard용 요약 — grade별 카운트 + 상위 STRONG 테마."""
    rows = db.execute(
        select(ThemeSignal).order_by(ThemeSignal.created_at.desc()).limit(100)
    ).scalars().all()
    by_grade: dict[str, int] = {}
    for r in rows:
        by_grade[r.grade] = by_grade.get(r.grade, 0) + 1
    strong = [r for r in rows if r.grade == "STRONG"][:5]
    return {
        "total":          len(rows),
        "by_grade":       by_grade,
        "top_themes":     [
            {"theme": r.theme, "score": r.score, "grade": r.grade,
             "related_symbols": r.related_symbols, "provider": r.provider}
            for r in strong
        ],
        "used_for_order": False,  # invariant — 본 데이터는 주문에 사용되지 않음
    }

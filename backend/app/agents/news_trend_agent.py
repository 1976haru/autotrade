"""#53: News / Trend Agent.

`theme_signals` 테이블(#22)을 읽어 테마별 관심도, 키워드 증가, 뉴스/공시
이벤트, 관련 종목 후보를 *요약*만 한다. **주문 신호가 아님** —
BUY/SELL/HOLD 반환 금지, approval queue 등록 금지, broker 호출 금지.

뉴스 / 트렌드 / 공시 데이터는 *후보 필터*와 Agent context로만 사용된다.
뉴스 해석 오류(루머 / 과열 테마 / 악재·호재 오판 / AI 요약 오류)가 직접 주문
으로 이어지지 않도록 본 Agent는 read-only 요약 전용.

## 절대 invariant

1. 본 모듈은 broker / OrderExecutor / route_order / kis / mock_broker /
   permission.gate 어떤 모듈도 import하지 않는다 (정적 grep 가드).
2. 외부 네트워크 호출 0건 — 실제 Google Trends / 유료 News API / 공시 API
   호출 코드 0건. provider stub은 mock-only.
3. `NewsTrendOutput.is_order_signal = False` 불변 (`__post_init__` 가드).
4. `recommended_action` enum에 BUY/SELL/HOLD 값 0개 — `MONITOR` /
   `RESEARCH` / `CAUTION` / `OVERHEAT_WARN` 같은 advisory만.
5. DB 조회는 read-only SELECT only. INSERT / UPDATE / DELETE 0건.
6. `used_for_order=True`인 row가 발견되면 *경고만* — 주문에 사용하지 않음
   (caller가 본 invariant를 절대 위반해서는 안 된다는 명시).

## 다른 Agent와의 관계

| Agent | 본 NewsTrend를 어떻게 사용 |
|---|---|
| MarketObserverAgent (#52) | `top_themes`를 `leading_themes` 입력으로 활용 가능 |
| StrategySelectionAgent | `caution_themes`에 속한 종목은 신규 진입 회피 |
| ChiefTradingAgent | 운영자 요약 + 후보 종목 카운트 참고 |
| ExecutionRecommender (#51) | `related_candidates`를 후보로 *참고만* — 직접 진입 X |

자세한 정책: [`docs/news_trend_agent.md`](../../../docs/news_trend_agent.md).
"""

from __future__ import annotations

from collections import Counter, defaultdict
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import StrEnum
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.agents.base import (
    AgentBase,
    AgentContext,
    AgentDecision,
    AgentMetadata,
    AgentOutput,
    AgentRole,
)
from app.db.models import ThemeSignal


# ====================================================================
# Enums
# ====================================================================


class NewsTrendAction(StrEnum):
    """본 Agent가 *advisory*로 반환할 수 있는 카테고리. 주문 결정 X.

    - MONITOR        : 평소대로 후보 모니터링
    - RESEARCH       : 새 테마/키워드 — 운영자 검토 권장
    - CAUTION        : confidence 낮음 또는 used_for_order 위반 의심 — 사용 자제
    - OVERHEAT_WARN  : score + news 급증 — 과열 가능성, 추격 매수 자제 권고
    - NO_DATA        : 입력 ThemeSignal 0건
    """
    MONITOR       = "MONITOR"
    RESEARCH      = "RESEARCH"
    CAUTION       = "CAUTION"
    OVERHEAT_WARN = "OVERHEAT_WARN"
    NO_DATA       = "NO_DATA"


# ====================================================================
# Dataclasses
# ====================================================================


@dataclass(frozen=True)
class ThemeSummary:
    """테마별 요약 — top_themes 리스트의 항목."""
    theme:                 str
    score:                 int
    grade:                 str        # STRONG / WATCH / WEAK / IGNORE
    confidence:            int
    related_symbols:       list[str]
    keywords:              list[str]
    sample_summary:        str | None
    provider:              str
    signal_count:          int        # 본 윈도우 내 row 수


@dataclass(frozen=True)
class CandidateSymbol:
    """관련 종목 후보 — `related_symbols` 빈도 기반."""
    symbol:        str
    occurrence:    int        # 등장 횟수
    themes:        list[str]  # 관련 테마들


@dataclass(frozen=True)
class NewsTrendOutput:
    """본 Agent의 표준 출력. *주문 신호가 아니다*.

    절대 invariant:
    - `is_order_signal = False` — `__post_init__` ValueError 가드.
    - 응답 어디에도 BUY/SELL/HOLD 키 / 값 없음.
    - `recommended_action`은 advisory 카테고리.
    """

    recommended_action:    NewsTrendAction
    summary_lines:         list[str]                  # 운영자용 자연어 요약 (3~5줄)
    top_themes:            list[ThemeSummary]
    rising_keywords:       list[tuple[str, int]]      # (keyword, occurrence)
    related_candidates:    list[CandidateSymbol]
    caution_themes:        list[ThemeSummary]         # confidence 낮거나 의심
    overheating_warnings:  list[str]
    used_for_order_warnings: list[str]                # invariant 위반 경고
    total_signal_count:    int
    window_seconds:        int | None = None
    is_order_signal:       bool       = False
    created_at:            datetime   = field(
        default_factory=lambda: datetime.now(timezone.utc)
    )

    def __post_init__(self) -> None:
        # 절대 invariant — 본 출력은 주문 신호가 아니다.
        if self.is_order_signal:
            raise ValueError(
                "NewsTrendOutput.is_order_signal must be False — News/Trend "
                "Agent is context-only (CLAUDE.md 절대 원칙 1, 2). "
                "BUY/SELL/HOLD는 RiskManager + PermissionGate + OrderExecutor "
                "흐름에서만 만들어진다."
            )

    def to_dict(self) -> dict[str, Any]:
        return {
            "recommended_action": self.recommended_action.value,
            "summary_lines":      list(self.summary_lines),
            "top_themes":         [
                {
                    "theme":           t.theme,
                    "score":           t.score,
                    "grade":           t.grade,
                    "confidence":      t.confidence,
                    "related_symbols": list(t.related_symbols),
                    "keywords":        list(t.keywords),
                    "sample_summary":  t.sample_summary,
                    "provider":        t.provider,
                    "signal_count":    t.signal_count,
                }
                for t in self.top_themes
            ],
            "rising_keywords":    [
                {"keyword": kw, "occurrence": n}
                for kw, n in self.rising_keywords
            ],
            "related_candidates": [
                {"symbol": c.symbol, "occurrence": c.occurrence,
                 "themes": list(c.themes)}
                for c in self.related_candidates
            ],
            "caution_themes":     [
                {
                    "theme":      t.theme, "score": t.score,
                    "grade":      t.grade, "confidence": t.confidence,
                    "provider":   t.provider,
                }
                for t in self.caution_themes
            ],
            "overheating_warnings":     list(self.overheating_warnings),
            "used_for_order_warnings":  list(self.used_for_order_warnings),
            "total_signal_count":       self.total_signal_count,
            "window_seconds":           self.window_seconds,
            "is_order_signal":          self.is_order_signal,
            "created_at":               self.created_at.isoformat(),
        }


# ====================================================================
# Provider stubs (외부 호출 0건)
# ====================================================================


class _DisabledProvider:
    """모든 외부 provider의 default — 호출되지 않는다.

    실제 Google Trends API alpha / 유료 News API / 공시 DART 연동은 별도
    옵트인 PR이 필요하며, 본 PR에서는 본 stub만 export한다.
    """

    name: str = "disabled"
    enabled: bool = False

    def fetch_keywords(self, *_args, **_kwargs) -> list[str]:
        # invariant: 어떤 외부 호출도 발생하지 않는다.
        return []

    def fetch_news(self, *_args, **_kwargs) -> list[dict]:
        return []

    def fetch_disclosures(self, *_args, **_kwargs) -> list[dict]:
        return []


# 공개 alias — 운영자/caller가 본 stub만 사용. 실 provider 추가는 별도 PR.
GoogleTrendsAlphaProvider = _DisabledProvider
NewsProvider              = _DisabledProvider
DisclosureProvider        = _DisabledProvider


class MockTrendProvider(_DisabledProvider):
    """deterministic mock — 테스트 / Demo Mode 전용. 외부 호출 0건."""
    name = "mock"

    def __init__(self, fixtures: list[dict] | None = None):
        self._fixtures = fixtures or []

    def fetch_keywords(self, *_args, **_kwargs) -> list[str]:
        return [f["keyword"] for f in self._fixtures if "keyword" in f]


# ====================================================================
# DB read-only helpers
# ====================================================================


def load_recent_theme_signals(
    db:          Session,
    *,
    limit:       int = 100,
    since:       datetime | None = None,
    min_score:   int | None = None,
) -> list[ThemeSignal]:
    """최근 ThemeSignal row 조회 — read-only.

    - `limit`: 최대 반환 row 수 (created_at desc)
    - `since`: 본 시각 이후만 (None이면 시간 제한 X)
    - `min_score`: score >= 이 값만 (None이면 필터 X)

    INSERT / UPDATE / DELETE 0건 — 본 함수는 SELECT만 수행 (정적 grep 가드).
    """
    if limit <= 0:
        return []
    stmt = select(ThemeSignal).order_by(ThemeSignal.id.desc())
    if since is not None:
        stmt = stmt.where(ThemeSignal.created_at >= since)
    if min_score is not None:
        stmt = stmt.where(ThemeSignal.score >= int(min_score))
    stmt = stmt.limit(int(limit))
    return list(db.execute(stmt).scalars().all())


def load_theme_signals_by_theme(
    db:          Session,
    theme:       str,
    *,
    limit:       int = 50,
) -> list[ThemeSignal]:
    """특정 테마의 최근 row 조회 — read-only."""
    if limit <= 0 or not theme:
        return []
    stmt = (
        select(ThemeSignal)
        .where(ThemeSignal.theme == theme)
        .order_by(ThemeSignal.id.desc())
        .limit(int(limit))
    )
    return list(db.execute(stmt).scalars().all())


# ====================================================================
# Pure summarizer
# ====================================================================


# 임계값 (조정 가능, 본 PR은 보수적 default).
_OVERHEAT_SCORE_THRESHOLD       = 90
_OVERHEAT_GRADE                 = "STRONG"
_CAUTION_CONFIDENCE_THRESHOLD   = 30   # 이하 → caution
_TOP_THEME_LIMIT                = 5
_TOP_KEYWORD_LIMIT              = 10
_TOP_CANDIDATE_LIMIT            = 10


def summarize_themes(
    signals: list[ThemeSignal],
    *,
    window_seconds: int | None = None,
) -> NewsTrendOutput:
    """`ThemeSignal` 리스트를 받아 advisory summary 생성. 순수 함수.

    DB / 외부 호출 0건 — caller가 미리 조회한 row를 전달해야 한다.
    데이터 부족 시 NO_DATA + friendly fallback (예외 X).
    """
    if not signals:
        return NewsTrendOutput(
            recommended_action=NewsTrendAction.NO_DATA,
            summary_lines=[
                "테마 신호 데이터가 아직 없습니다.",
                "Google Trends API alpha / 뉴스 / 공시 provider는 disabled 상태입니다.",
                "본 화면은 후보 필터 전용 — 주문 신호로 사용하지 마세요.",
            ],
            top_themes=[],
            rising_keywords=[],
            related_candidates=[],
            caution_themes=[],
            overheating_warnings=[],
            used_for_order_warnings=[],
            total_signal_count=0,
            window_seconds=window_seconds,
        )

    # 1. 테마별 그룹 — 최신 row 한 개를 대표로 + count 카운트.
    by_theme: dict[str, list[ThemeSignal]] = defaultdict(list)
    for s in signals:
        by_theme[s.theme].append(s)

    # 2. 테마별 대표 row + 점수 평균(또는 max) 산출.
    theme_rows: list[ThemeSummary] = []
    for theme, rows in by_theme.items():
        # 최신 row를 대표로 사용 (id desc로 정렬되어 있음을 가정 — caller 책임).
        # caller가 정렬 안 했으면 created_at desc로 재정렬.
        sorted_rows = sorted(
            rows, key=lambda r: r.id or 0, reverse=True,
        )
        rep = sorted_rows[0]
        # 점수는 윈도우 내 max — 가장 강한 신호를 우선.
        max_score = max(int(r.score or 0) for r in rows)
        max_conf  = max(int(r.confidence or 0) for r in rows)
        # related_symbols는 모든 row 합집합.
        all_symbols: list[str] = []
        for r in rows:
            for s in (r.related_symbols or []):
                if s and s not in all_symbols:
                    all_symbols.append(s)
        all_keywords: list[str] = []
        for r in rows:
            for k in (r.keywords or []):
                if k and k not in all_keywords:
                    all_keywords.append(k)
        theme_rows.append(ThemeSummary(
            theme=theme,
            score=max_score,
            grade=rep.grade,
            confidence=max_conf,
            related_symbols=all_symbols,
            keywords=all_keywords,
            sample_summary=rep.summary,
            provider=rep.provider,
            signal_count=len(rows),
        ))

    # 3. top_themes — score desc + signal_count desc.
    theme_rows.sort(key=lambda t: (t.score, t.signal_count), reverse=True)
    top_themes = theme_rows[:_TOP_THEME_LIMIT]

    # 4. caution — confidence 낮음.
    caution_themes = [
        t for t in theme_rows
        if t.confidence < _CAUTION_CONFIDENCE_THRESHOLD
    ][:_TOP_THEME_LIMIT]

    # 5. rising_keywords — Counter 집계.
    keyword_counter: Counter[str] = Counter()
    for s in signals:
        for k in (s.keywords or []):
            if k:
                keyword_counter[k] += 1
    rising_keywords = keyword_counter.most_common(_TOP_KEYWORD_LIMIT)

    # 6. related_candidates — symbol 빈도 + 관련 테마 carry.
    symbol_themes: dict[str, set[str]] = defaultdict(set)
    symbol_count: Counter[str] = Counter()
    for s in signals:
        for sym in (s.related_symbols or []):
            if sym:
                symbol_count[sym] += 1
                symbol_themes[sym].add(s.theme)
    candidates = [
        CandidateSymbol(
            symbol=sym, occurrence=cnt,
            themes=sorted(symbol_themes[sym]),
        )
        for sym, cnt in symbol_count.most_common(_TOP_CANDIDATE_LIMIT)
    ]

    # 7. overheating warnings — score 매우 높음 + 본 윈도우에 다수 신호.
    overheating: list[str] = []
    for t in theme_rows:
        is_strong = (t.score >= _OVERHEAT_SCORE_THRESHOLD
                       or t.grade == _OVERHEAT_GRADE)
        # signal_count >= 5는 같은 테마가 여러 번 등장 → 뉴스/트렌드 급증 추정.
        if is_strong and t.signal_count >= 5:
            overheating.append(
                f"{t.theme}: score={t.score} signal_count={t.signal_count} — "
                "추격 매수 자제 권장"
            )

    # 8. used_for_order=True 위반 경고 — invariant 위반 의심.
    invariant_warns: list[str] = []
    for s in signals:
        if bool(getattr(s, "used_for_order", False)):
            invariant_warns.append(
                f"theme_signal id={s.id} (theme={s.theme}) "
                "used_for_order=True — 주문 사용 의심, 본 Agent는 "
                "후보 필터로만 사용해야 합니다."
            )

    # 9. recommended_action 결정.
    if overheating:
        action = NewsTrendAction.OVERHEAT_WARN
    elif invariant_warns:
        action = NewsTrendAction.CAUTION
    elif caution_themes and not top_themes:
        action = NewsTrendAction.CAUTION
    elif not top_themes:
        action = NewsTrendAction.NO_DATA
    elif any(t.grade == "STRONG" for t in top_themes):
        action = NewsTrendAction.MONITOR
    else:
        action = NewsTrendAction.RESEARCH

    # 10. 운영자 요약 (3~5줄).
    summary_lines = _build_summary_lines(
        action=action, top_themes=top_themes,
        candidates=candidates, overheating=overheating,
        caution=caution_themes, invariant_warns=invariant_warns,
        total=len(signals),
    )

    return NewsTrendOutput(
        recommended_action=action,
        summary_lines=summary_lines,
        top_themes=top_themes,
        rising_keywords=rising_keywords,
        related_candidates=candidates,
        caution_themes=caution_themes,
        overheating_warnings=overheating,
        used_for_order_warnings=invariant_warns,
        total_signal_count=len(signals),
        window_seconds=window_seconds,
    )


def _build_summary_lines(
    *,
    action:           NewsTrendAction,
    top_themes:       list[ThemeSummary],
    candidates:       list[CandidateSymbol],
    overheating:      list[str],
    caution:          list[ThemeSummary],
    invariant_warns:  list[str],
    total:            int,
) -> list[str]:
    lines: list[str] = []

    head = {
        NewsTrendAction.MONITOR:       "테마 신호 모니터링 중.",
        NewsTrendAction.RESEARCH:      "새 테마 후보 — 운영자 검토 권장.",
        NewsTrendAction.CAUTION:       "신호 신뢰도 주의 — 사용 자제.",
        NewsTrendAction.OVERHEAT_WARN: "테마 과열 경고 — 추격 매수 자제.",
        NewsTrendAction.NO_DATA:       "테마 신호 데이터 없음.",
    }[action]
    lines.append(head)

    if total > 0:
        lines.append(
            f"전체 신호 {total}건, 상위 테마 {len(top_themes)}개, "
            f"후보 종목 {len(candidates)}개."
        )
    if top_themes:
        names = [t.theme for t in top_themes[:3]]
        lines.append(f"상위 테마: {', '.join(names)}")
    if overheating:
        lines.append(f"과열 경고: {overheating[0]}")
    if caution:
        names = [t.theme for t in caution[:3]]
        lines.append(f"신뢰도 주의 테마: {', '.join(names)}")
    if invariant_warns:
        lines.append(
            f"⚠ used_for_order=True 의심 row {len(invariant_warns)}건 발견 "
            "— 주문에 사용하지 마세요."
        )
    # 항상 마지막에 invariant 안내.
    lines.append("본 요약은 *주문 신호가 아닙니다*. 후보 필터 / Agent context 전용.")
    return lines


# ====================================================================
# AgentBase implementation (#51 호환)
# ====================================================================


class NewsTrendAgent(AgentBase):
    """`AgentBase` 호환 implementation.

    `context.extra["theme_signals"]`로 caller가 ThemeSignal 리스트를 주입할
    수 있다 (tests / mock 흐름). 그 외에는 `context.market_state`에서 raw
    데이터를 받지 않는다 — caller가 `summarize_themes(signals)`를 직접
    호출하는 패턴 권장.
    """

    @property
    def metadata(self) -> AgentMetadata:
        return AgentMetadata(
            name="news_trend_agent",
            role=AgentRole.OBSERVER,
            description=(
                "theme_signals 테이블을 읽어 테마별 관심도 / 키워드 / 뉴스 / "
                "공시 / 관련 종목 후보를 요약. 주문 신호가 아니다 — "
                "BUY/SELL/HOLD 반환 금지, approval queue 등록 금지."
            ),
            inputs=[
                "context.extra.theme_signals (list[ThemeSignal])",
                "또는 caller가 summarize_themes(signals) 직접 호출",
            ],
            outputs=[
                "NewsTrendOutput (is_order_signal=False)",
                "AgentOutput(decision=OBSERVE, metadata=output.to_dict())",
            ],
            forbidden=[
                "BUY / SELL / HOLD 주문 신호 반환 금지",
                "approval queue 등록 금지",
                "broker / OrderExecutor / route_order 호출 금지",
                "외부 Google Trends / News / 공시 API 호출 금지",
                "DB INSERT / UPDATE / DELETE 금지 (read-only SELECT만)",
            ],
            can_execute_order=False,
        )

    def run(self, context: AgentContext) -> AgentOutput:
        signals = list((context.extra or {}).get("theme_signals") or [])
        out = summarize_themes(signals)
        return AgentOutput(
            role=AgentRole.OBSERVER,
            decision=AgentDecision.OBSERVE,
            summary=(
                out.summary_lines[0] if out.summary_lines else "no signals"
            ),
            reasons=list(out.summary_lines[1:4])
                if len(out.summary_lines) > 1 else [],
            confidence=None,
            risk_flags=(
                ["overheating"] if out.overheating_warnings else []
            ) + (
                ["used_for_order_violation"] if out.used_for_order_warnings else []
            ),
            metadata=out.to_dict(),
        )


# ====================================================================
# Module invariants
# ====================================================================
#
# - 본 모듈은 broker / OrderExecutor / route_order / KIS / mock_broker /
#   permission.gate 어떤 모듈도 import하지 않는다 (정적 grep 가드).
# - 외부 HTTP client(httpx, requests, urllib3) import 0건.
# - DB INSERT / UPDATE / DELETE 0건 — SELECT만 (정적 grep 가드).
# - `NewsTrendOutput.is_order_signal = False` 불변 (__post_init__ 가드).
# - `NewsTrendAction` enum에 BUY/SELL/HOLD 값 0개.

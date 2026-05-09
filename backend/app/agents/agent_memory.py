"""Agent Memory — 과거 손실 원인 / 전략 변경 이력 / 운영 사례 검색 저장소.

Agent와 운영자가 과거 리포트 / 위험 사례 / 전략 성과를 *검색 가능*한 형태로
보관한다. 반복 실수를 줄이고 전략 개선 이력을 축적하기 위한 *학습 자료* 계층.

## 본 메모리는 *주문 신호가 아닙니다*.

검색 결과로 직접 BUY/SELL/HOLD 결정을 만들지 않으며, RiskManager /
PermissionGate / OrderExecutor 우회에 사용 X. 모든 실 주문 흐름은 기존 sanctioned
경로(`route_order` → RiskManager → PermissionGate → OrderExecutor)를 거친다.

## 핵심 invariant (절대 원칙, 정적 grep 가드)

1. **API key / Secret / 계좌번호 / 인증 토큰 / 개인정보 저장 금지** —
   `sanitize_text()`가 INSERT 전 모든 텍스트에서 패턴 제거. 패턴 적중 시
   `SecretLeakError`로 raise (테스트로 lock).
2. **broker / OrderExecutor / route_order import 0건** — 본 메모리는 검색
   저장소이며 주문 경로와 *완전히 분리*.
3. **주문 신호 생성 0건** — `MemoryRecord`에 BUY/SELL/HOLD 결정 필드 없음;
   `MemoryType` / `Severity` enum에 BUY/SELL/HOLD 값 0개.
4. **approval queue 등록 0건** — `submit_candidate(` / `route_order(` 호출 0건.
5. **외부 AI / HTTP 호출 0건** — 본 모듈은 read-only 검색 + DB INSERT만.

자세한 정책: [`docs/agent_memory.md`](../../../docs/agent_memory.md).
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import StrEnum
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.models import AgentMemory


# ====================================================================
# Enums (NEVER BUY/SELL/HOLD)
# ====================================================================


class MemoryType(StrEnum):
    """Agent Memory 분류. 검색 필터로 사용.

    BUY/SELL/HOLD 같은 *주문 결정 값* 0개 — 본 메모리는 학습 자료.
    """
    DAILY_REPORT       = "daily_report"
    RISK_INCIDENT      = "risk_incident"
    STRATEGY_RESEARCH  = "strategy_research"
    BACKTEST_REVIEW    = "backtest_review"
    AGENT_DECISION     = "agent_decision"
    OPERATOR_NOTE      = "operator_note"
    LOSS_POST_MORTEM   = "loss_post_mortem"
    LESSON_LEARNED     = "lesson_learned"


class SourceKind(StrEnum):
    DAILY_REPORT       = "daily_report"
    RISK_AUDIT         = "risk_audit"
    STRATEGY_RESEARCH  = "strategy_research"
    AGENT_DECISION_LOG = "agent_decision_log"
    ORDER_AUDIT        = "order_audit"
    BACKTEST_RUN       = "backtest_run"
    OPERATOR           = "operator"


class MemorySeverity(StrEnum):
    INFO     = "INFO"
    WARN     = "WARN"
    HIGH     = "HIGH"
    CRITICAL = "CRITICAL"


# ====================================================================
# Sanitization — 민감정보 저장 차단
# ====================================================================


class SecretLeakError(ValueError):
    """본 텍스트에 민감 정보가 포함되어 있어 저장 차단."""


# 민감정보 detection patterns. caller가 sanitize_text를 *반드시* 통과시킨 후에만
# `save_memory()`를 호출한다. *fail-closed* — 의심 패턴 발견 시 raise.
_SECRET_PATTERNS: tuple[tuple[str, "re.Pattern[str]"], ...] = (
    # 일반 API key / token (Anthropic / OpenAI 등 32+ char hex/base64)
    ("api_key_long",      re.compile(r"sk-(?:[A-Za-z0-9_-]){20,}", re.I)),
    ("anthropic_key",     re.compile(r"sk-ant-[A-Za-z0-9_-]{20,}", re.I)),
    ("openai_key",        re.compile(r"sk-[A-Za-z0-9]{32,}", re.I)),
    # 계좌번호 추정 (한국 증권 — 자릿수 8-14, 하이픈 포함 가능)
    ("kr_account",        re.compile(r"\b\d{2,4}-\d{4,8}-\d{2,3}\b")),
    ("kr_account_long",   re.compile(r"\b\d{10,14}\b")),
    # 신용카드 (16 digits with optional spaces)
    ("credit_card",       re.compile(r"\b(?:\d[ -]*?){13,19}\b")),
    # 주민등록번호 패턴
    ("kr_resident",       re.compile(r"\b\d{6}-?[1-4]\d{6}\b")),
    # KIS App key / secret (영숫자 32+ characters labeled)
    ("kis_app_key",       re.compile(
        r"(?:app[_ ]?key|app[_ ]?secret|access[_ ]?token)\s*[:=]\s*[A-Za-z0-9_-]{16,}",
        re.I,
    )),
    # JWT-like
    ("jwt",               re.compile(
        r"\beyJ[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+\b"
    )),
    # 이메일 주소 (개인정보; 본 메모리는 시스템 학습 자료이므로 이메일 금지)
    ("email",             re.compile(
        r"\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}\b"
    )),
    # 한국 휴대전화 번호 (010-XXXX-XXXX 등)
    ("kr_phone",          re.compile(r"\b01[016-9][- ]?\d{3,4}[- ]?\d{4}\b")),
)


def sanitize_text(text: str | None, *, field_name: str = "text") -> str:
    """문자열에서 민감정보 패턴을 검출하면 *raise* — fail-closed.

    *입력 시점*에 차단해서 저장 자체를 막는다 (저장 후 redaction은 신뢰성 X).
    """
    if text is None:
        return ""
    s = str(text)
    for label, pat in _SECRET_PATTERNS:
        if pat.search(s):
            raise SecretLeakError(
                f"AgentMemory {field_name} contains forbidden pattern '{label}'. "
                f"본 메모리에는 API key / Secret / 계좌번호 / 개인정보를 저장할 수 없습니다."
            )
    return s


def sanitize_dict(d: dict[str, Any] | None,
                  *, field_name: str = "meta") -> dict[str, Any]:
    """dict의 모든 string value를 sanitize. 중첩 dict / list 재귀."""
    if d is None:
        return {}
    out: dict[str, Any] = {}
    for k, v in d.items():
        out[k] = _sanitize_value(v, field_name=f"{field_name}.{k}")
    return out


def _sanitize_value(v: Any, *, field_name: str) -> Any:
    if isinstance(v, str):
        return sanitize_text(v, field_name=field_name)
    if isinstance(v, dict):
        return sanitize_dict(v, field_name=field_name)
    if isinstance(v, list):
        return [_sanitize_value(x, field_name=field_name) for x in v]
    if isinstance(v, tuple):
        return tuple(_sanitize_value(x, field_name=field_name) for x in v)
    return v   # int / float / bool / None / etc.


def sanitize_tags(tags: list[str] | tuple[str, ...] | None) -> list[str]:
    if not tags:
        return []
    return [sanitize_text(str(t), field_name="tag") for t in tags]


# ====================================================================
# Dataclasses
# ====================================================================


@dataclass(frozen=True)
class MemoryRecord:
    """`AgentMemory` row의 dataclass 표현 — API / search 결과 carry."""
    id:           int
    created_at:   datetime
    updated_at:   datetime
    memory_type:  MemoryType
    source_kind:  SourceKind | None
    source_id:    int | None
    strategy:     str | None
    symbol:       str | None
    mode:         str | None
    severity:     MemorySeverity
    title:        str
    summary:      str
    lessons:      str | None
    next_action:  str | None
    tags:         tuple[str, ...]
    meta:         dict[str, Any]
    author:       str | None
    archived:     bool

    # 본 dataclass는 *주문 객체가 아니다* — 검색 결과로 직접 결정 X
    is_order_signal: bool = False

    def __post_init__(self) -> None:
        if self.is_order_signal is not False:
            raise ValueError(
                "MemoryRecord.is_order_signal must be False — "
                "Agent Memory 검색 결과는 주문 신호가 아닙니다."
            )

    @classmethod
    def from_row(cls, row: AgentMemory) -> "MemoryRecord":
        return cls(
            id=row.id,
            created_at=row.created_at,
            updated_at=row.updated_at,
            memory_type=MemoryType(row.memory_type),
            source_kind=SourceKind(row.source_kind) if row.source_kind else None,
            source_id=row.source_id,
            strategy=row.strategy,
            symbol=row.symbol,
            mode=row.mode,
            severity=MemorySeverity(row.severity),
            title=row.title,
            summary=row.summary,
            lessons=row.lessons,
            next_action=row.next_action,
            tags=tuple(row.tags or []),
            meta=dict(row.meta or {}),
            author=row.author,
            archived=bool(row.archived),
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "id":           self.id,
            "created_at":   self.created_at.isoformat() if self.created_at else None,
            "updated_at":   self.updated_at.isoformat() if self.updated_at else None,
            "memory_type":  self.memory_type.value,
            "source_kind":  self.source_kind.value if self.source_kind else None,
            "source_id":    self.source_id,
            "strategy":     self.strategy,
            "symbol":       self.symbol,
            "mode":         self.mode,
            "severity":     self.severity.value,
            "title":        self.title,
            "summary":      self.summary,
            "lessons":      self.lessons,
            "next_action":  self.next_action,
            "tags":         list(self.tags),
            "meta":         dict(self.meta),
            "author":       self.author,
            "archived":     self.archived,
            "is_order_signal": self.is_order_signal,
        }


@dataclass(frozen=True)
class MemoryWriteRequest:
    """`save_memory` 입력 — 모든 텍스트 필드는 sanitize_text 통과 *예정*."""
    memory_type:  MemoryType
    title:        str
    summary:      str
    source_kind:  SourceKind | None = None
    source_id:    int | None = None
    strategy:     str | None = None
    symbol:       str | None = None
    mode:         str | None = None
    severity:     MemorySeverity = MemorySeverity.INFO
    lessons:      str | None = None
    next_action:  str | None = None
    tags:         tuple[str, ...] = ()
    meta:         dict[str, Any] = field(default_factory=dict)
    author:       str | None = None


@dataclass(frozen=True)
class MemorySearchFilter:
    memory_type:  MemoryType | None = None
    source_kind:  SourceKind | None = None
    strategy:     str | None = None
    symbol:       str | None = None
    mode:         str | None = None
    severity:     MemorySeverity | None = None
    tag:          str | None = None
    keyword:      str | None = None  # title/summary/lessons/next_action LIKE
    include_archived: bool = False
    limit:        int = 50
    offset:       int = 0


# ====================================================================
# DB write / read — agent 모듈은 INSERT/UPDATE만 (DELETE 없음 — archive로 대체)
# ====================================================================


def save_memory(db: Session, req: MemoryWriteRequest) -> MemoryRecord:
    """sanitize 후 INSERT. caller(API / 자동 흐름)는 본 함수만 호출."""
    title = sanitize_text(req.title, field_name="title")
    summary = sanitize_text(req.summary, field_name="summary")
    lessons = sanitize_text(req.lessons, field_name="lessons") if req.lessons else None
    next_action = (
        sanitize_text(req.next_action, field_name="next_action")
        if req.next_action else None
    )
    strategy = sanitize_text(req.strategy, field_name="strategy") if req.strategy else None
    symbol = sanitize_text(req.symbol, field_name="symbol") if req.symbol else None
    mode = sanitize_text(req.mode, field_name="mode") if req.mode else None
    author = sanitize_text(req.author, field_name="author") if req.author else None
    tags = sanitize_tags(list(req.tags))
    meta = sanitize_dict(req.meta or {}, field_name="meta")

    if not title.strip():
        raise ValueError("AgentMemory.title must be non-empty after sanitization.")
    if not summary.strip():
        raise ValueError("AgentMemory.summary must be non-empty after sanitization.")

    row = AgentMemory(
        memory_type=req.memory_type.value,
        source_kind=req.source_kind.value if req.source_kind else None,
        source_id=req.source_id,
        strategy=strategy,
        symbol=symbol,
        mode=mode,
        severity=req.severity.value,
        title=title[:200],
        summary=summary,
        lessons=lessons,
        next_action=next_action,
        tags=tags,
        meta=meta,
        author=author,
    )
    db.add(row)
    db.commit()
    db.refresh(row)
    return MemoryRecord.from_row(row)


def search_memory(db: Session, flt: MemorySearchFilter) -> list[MemoryRecord]:
    """검색 — 모든 필터는 *AND*. tag는 JSON contains, keyword는 LIKE."""
    stmt = select(AgentMemory)
    if flt.memory_type is not None:
        stmt = stmt.where(AgentMemory.memory_type == flt.memory_type.value)
    if flt.source_kind is not None:
        stmt = stmt.where(AgentMemory.source_kind == flt.source_kind.value)
    if flt.strategy:
        stmt = stmt.where(AgentMemory.strategy == flt.strategy)
    if flt.symbol:
        stmt = stmt.where(AgentMemory.symbol == flt.symbol)
    if flt.mode:
        stmt = stmt.where(AgentMemory.mode == flt.mode)
    if flt.severity is not None:
        stmt = stmt.where(AgentMemory.severity == flt.severity.value)
    if not flt.include_archived:
        stmt = stmt.where(AgentMemory.archived == False)  # noqa: E712
    if flt.keyword:
        kw = f"%{flt.keyword}%"
        from sqlalchemy import or_
        stmt = stmt.where(or_(
            AgentMemory.title.like(kw),
            AgentMemory.summary.like(kw),
            AgentMemory.lessons.like(kw),
            AgentMemory.next_action.like(kw),
        ))
    stmt = stmt.order_by(AgentMemory.created_at.desc())
    stmt = stmt.offset(max(0, flt.offset)).limit(max(1, min(500, flt.limit)))
    rows = list(db.execute(stmt).scalars())
    if flt.tag:
        # JSON contains는 DB별로 다름 — 본 PR에서는 in-memory filter (작은 집합 가정).
        rows = [r for r in rows if flt.tag in (r.tags or [])]
    return [MemoryRecord.from_row(r) for r in rows]


def get_memory(db: Session, memory_id: int) -> MemoryRecord | None:
    row = db.execute(
        select(AgentMemory).where(AgentMemory.id == memory_id).limit(1)
    ).scalar_one_or_none()
    if row is None:
        return None
    return MemoryRecord.from_row(row)


def archive_memory(db: Session, memory_id: int,
                   *, archived: bool = True) -> MemoryRecord | None:
    """archived flag toggle — *DELETE는 사용하지 않는다* (audit 보존)."""
    row = db.execute(
        select(AgentMemory).where(AgentMemory.id == memory_id).limit(1)
    ).scalar_one_or_none()
    if row is None:
        return None
    row.archived = bool(archived)
    row.updated_at = datetime.now(timezone.utc).replace(tzinfo=None)
    db.commit()
    db.refresh(row)
    return MemoryRecord.from_row(row)


# ====================================================================
# Ingest helpers — 다른 Agent 출력 → MemoryRecord 자동 변환 (sanitize 후)
# ====================================================================


def memory_from_daily_report_markdown(
    *,
    report_date: str,
    markdown: str,
    findings_count: int = 0,
    warnings_count: int = 0,
    operator_note: str | None = None,
) -> MemoryWriteRequest:
    """#57 Daily Report markdown → MemoryWriteRequest.

    리포트 *전체*를 그대로 저장하지 않는다 — title + 요약 (앞 1000 chars) +
    operator_note만 carry. 자세한 raw 데이터는 원본 reports/daily_*.md 참조.
    """
    summary_excerpt = (markdown or "")[:1000]
    severity = (
        MemorySeverity.HIGH if warnings_count >= 3 else
        MemorySeverity.WARN if warnings_count >= 1 else
        MemorySeverity.INFO
    )
    return MemoryWriteRequest(
        memory_type=MemoryType.DAILY_REPORT,
        source_kind=SourceKind.DAILY_REPORT,
        title=f"Daily Report — {report_date}",
        summary=summary_excerpt,
        lessons=operator_note,
        severity=severity,
        tags=("daily_report", report_date),
        meta={
            "report_date":     report_date,
            "findings_count":  findings_count,
            "warnings_count":  warnings_count,
        },
    )


def memory_from_strategy_research_report(
    *,
    strategy: str,
    run_id: int,
    audit_level: str,
    summary: str,
    findings_count: int = 0,
    suggestions_count: int = 0,
    operator_note: str | None = None,
) -> MemoryWriteRequest:
    """#55 Strategy Researcher → MemoryWriteRequest."""
    severity = {
        "HEALTHY":  MemorySeverity.INFO,
        "CAUTION":  MemorySeverity.WARN,
        "WARNING":  MemorySeverity.HIGH,
        "CRITICAL": MemorySeverity.CRITICAL,
    }.get(audit_level.upper(), MemorySeverity.INFO)
    return MemoryWriteRequest(
        memory_type=MemoryType.STRATEGY_RESEARCH,
        source_kind=SourceKind.STRATEGY_RESEARCH,
        source_id=run_id,
        strategy=strategy,
        title=f"Strategy Research — {strategy} (run {run_id})",
        summary=summary[:4000],
        lessons=operator_note,
        severity=severity,
        tags=("strategy_research", strategy, audit_level.lower()),
        meta={
            "audit_level":       audit_level,
            "findings_count":    findings_count,
            "suggestions_count": suggestions_count,
        },
    )


def memory_from_risk_audit_report(
    *,
    audit_level: str,
    risk_score: int,
    summary: str,
    pause_recommended: bool = False,
    stop_recommended: bool = False,
    events_count: int = 0,
    operator_note: str | None = None,
) -> MemoryWriteRequest:
    """#54 Risk Auditor → MemoryWriteRequest."""
    severity = (
        MemorySeverity.CRITICAL if stop_recommended else
        MemorySeverity.HIGH if pause_recommended else
        MemorySeverity.WARN if events_count >= 3 else
        MemorySeverity.INFO
    )
    title = f"Risk Audit — {audit_level} (score {risk_score})"
    next_action = None
    if stop_recommended:
        next_action = "EMERGENCY_STOP_RECOMMENDED — 운영자 수동 토글 검토"
    elif pause_recommended:
        next_action = "PAUSE_TRADING_RECOMMENDED — 신규 진입 회피 검토"
    return MemoryWriteRequest(
        memory_type=MemoryType.RISK_INCIDENT,
        source_kind=SourceKind.RISK_AUDIT,
        title=title,
        summary=summary[:4000],
        lessons=operator_note,
        next_action=next_action,
        severity=severity,
        tags=("risk_audit", audit_level.lower()),
        meta={
            "audit_level":         audit_level,
            "risk_score":          risk_score,
            "pause_recommended":   pause_recommended,
            "stop_recommended":    stop_recommended,
            "events_count":        events_count,
        },
    )


# ====================================================================
# Module invariants
# ====================================================================
#
# - 본 모듈은 broker / OrderExecutor / route_order / PermissionGate /
#   approval queue 어떤 것도 import하지 않는다. 검색 저장소 + sanitize 만.
# - MemoryRecord에 BUY/SELL/HOLD / order_intent / can_execute_order
#   필드 0개 — `is_order_signal=False` 불변.
# - DELETE 미사용 (audit 보존) — archive flag로 대체.
# - 외부 HTTP / AI SDK import 0건.
#
# 위 invariant는 `tests/test_agent_memory.py`의 정적 grep 가드로 강제.

"""#56 / 7-04 — 통합 오류/이벤트 로그 뷰어 (read-only).

EXE 장중 문제 원인을 한 화면에서 파악할 수 있도록 RuntimeEvent /
AgentDecisionEpisode(AI 판단) / OrderAuditLog(KIS 주문) 를 단일 표준 모양으로
모아 최근 100건 반환한다. source / severity / keyword 필터 지원.

절대 invariant:
  - broker / OrderExecutor / route_order 호출 0건, 주문/POST 0건, DB write 0건
    (read-only SELECT 만).
  - Secret / API key / 계좌번호 / access_token *원문* 표시 0건 — free-text 는
    `redact_text()` 로 마스킹([REDACTED]) 후 emit (로그를 *드롭하지 않고* 가린다).
  - `is_live_authorization=False`, `contains_secret=False` 불변.
"""

from __future__ import annotations

import re
from typing import Any, Optional

LOG_SOURCES = ("RUNTIME_EVENT", "AGENT_DECISION", "KIS_ORDER")
SEVERITIES = ("DEBUG", "INFO", "WARN", "ERROR", "CRITICAL")

# 마스킹 대상 패턴 (event_log / agent_memory 의 fail-closed 패턴과 동일 계열).
# 로그 뷰어는 *드롭이 아니라 마스킹* — 매칭 구간을 [REDACTED] 로 치환.
_REDACT_PATTERNS = [
    re.compile(r"sk-ant-[A-Za-z0-9\-_]{20,}", re.I),
    re.compile(r"sk-[A-Za-z0-9]{20,}", re.I),
    re.compile(r"ghp_[A-Za-z0-9]{20,}"),
    re.compile(r"xox[bpaoist]-[A-Za-z0-9-]{10,}"),
    re.compile(r"Bearer\s+[A-Za-z0-9\.\-_]{20,}", re.I),
    re.compile(r"eyJ[A-Za-z0-9\-_]{10,}\.[A-Za-z0-9\-_]{10,}\.[A-Za-z0-9\-_]{10,}"),
    re.compile(r"PS[A-Za-z0-9]{20,}"),  # KIS app key
    re.compile(r"(?:access_token|refresh_token|app_key|app_secret|api_key)\s*[:=]\s*[A-Za-z0-9\-_]{8,}", re.I),
    re.compile(r"\b\d{4}[-\s]\d{4}[-\s]\d{4}[-\s]\d{4}\b"),  # credit card
    re.compile(r"\b\d{6}-[1-4]\d{6}\b"),  # 주민등록번호
    re.compile(r"\b\d{8}-\d{2}\b"),  # 한국 계좌번호 8-2
]

_REDACTED = "[REDACTED]"


def redact_text(value: Any) -> str:
    """free-text 에서 secret/계좌 패턴을 [REDACTED] 로 마스킹.

    None / 비문자열은 안전하게 str 化. 로그를 드롭하지 않고 *값만* 가린다.
    """
    if value is None:
        return ""
    text = value if isinstance(value, str) else str(value)
    for pat in _REDACT_PATTERNS:
        text = pat.sub(_REDACTED, text)
    return text


def _entry(*, timestamp, source, severity, reason_code, message,
           symbol=None, action=None, broker_order_no=None,
           episode_id=None) -> dict:
    # free-text(message / reason_code) 는 마스킹. 구조화 식별자(broker_order_no /
    # episode_id / symbol / action)는 secret 이 아니므로 그대로 표시.
    return {
        "timestamp": timestamp or "",
        "source": source,
        "severity": severity or "INFO",
        "reason_code": redact_text(reason_code) if reason_code else None,
        "message": redact_text(message),
        "symbol": symbol,
        "action": action,
        "broker_order_no": broker_order_no,
        "episode_id": episode_id,
    }


def _safe(fn, default=None):
    try:
        return fn()
    except Exception:  # noqa: BLE001
        return default


# ── source 별 normalizer ─────────────────────────────────────────────────────


def _from_runtime_events(limit: int) -> list[dict]:
    from app.system.event_log import get_runtime_event_log
    elog = get_runtime_event_log()
    out = []
    for ev in elog.recent(limit=limit):
        d = ev.to_dict()
        out.append(_entry(
            timestamp=d.get("timestamp"),
            source="RUNTIME_EVENT",
            severity=str(d.get("level") or "INFO"),
            reason_code=d.get("code"),
            message=d.get("message") or "",
            symbol=(d.get("details") or {}).get("symbol"),
        ))
    return out


def _episode_severity(ep: dict) -> str:
    rc = str(ep.get("reason_code") or "").upper()
    if any(k in rc for k in ("REJECT", "BLOCK", "ERROR", "FAIL", "VETO")):
        return "WARN"
    return "INFO"


def _from_decision_episodes(db, limit: int) -> list[dict]:
    from app.agents.decision_episode import list_episodes
    out = []
    for ep in list_episodes(db, limit=limit):
        out.append(_entry(
            timestamp=ep.get("created_at"),
            source="AGENT_DECISION",
            severity=_episode_severity(ep),
            reason_code=ep.get("reason_code"),
            message=str(ep.get("market_summary") or ep.get("reason_code") or "AI 판단"),
            symbol=ep.get("symbol"),
            action=ep.get("final_action"),
            broker_order_no=ep.get("broker_order_no"),
            episode_id=ep.get("episode_id"),
        ))
    return out


def _from_order_audit(db, limit: int) -> list[dict]:
    from sqlalchemy import select
    from app.db.models import OrderAuditLog
    rows = db.execute(
        select(OrderAuditLog)
        .where(OrderAuditLog.archived.is_(False))
        .order_by(OrderAuditLog.created_at.desc())
        .limit(max(1, int(limit)))
    ).scalars().all()
    out = []
    for r in rows:
        decision = str(getattr(r, "decision", "") or "")
        broker_status = str(getattr(r, "broker_status", "") or "")
        severity = "INFO"
        if decision == "REJECTED" or "REJECT" in broker_status.upper():
            severity = "WARN"
        msg = getattr(r, "message", "") or getattr(r, "trade_reason", "") or decision
        out.append(_entry(
            timestamp=(r.created_at.isoformat() if getattr(r, "created_at", None) else None),
            source="KIS_ORDER",
            severity=severity,
            reason_code=decision or None,
            message=str(msg),
            symbol=getattr(r, "symbol", None),
            action=getattr(r, "side", None),
            broker_order_no=getattr(r, "broker_order_id", None),
        ))
    return out


# ── 통합 수집 ────────────────────────────────────────────────────────────────


def collect_recent_logs(db=None, *, source: str = "ALL", severity: str = "ALL",
                        q: Optional[str] = None, limit: int = 100) -> dict:
    """RuntimeEvent + AgentDecision + KIS order 를 모아 최근 N건 반환 (read-only).

    각 source 는 방어적으로 수집 — 하나가 실패해도 예외 없이 진행.
    """
    limit = max(1, min(int(limit or 100), 100))  # 최근 100건 상한 강제.
    src = (source or "ALL").upper()
    sev = (severity or "ALL").upper()

    entries: list[dict] = []
    # 각 source 에서 넉넉히 모은 뒤 병합 정렬 → 상한 적용.
    gather_n = limit if src != "ALL" else min(limit, 100)
    if src in ("ALL", "RUNTIME_EVENT"):
        entries += _safe(lambda: _from_runtime_events(gather_n), []) or []
    if src in ("ALL", "AGENT_DECISION"):
        if db is not None:
            entries += _safe(lambda: _from_decision_episodes(db, gather_n), []) or []
    if src in ("ALL", "KIS_ORDER"):
        if db is not None:
            entries += _safe(lambda: _from_order_audit(db, gather_n), []) or []

    # severity 필터.
    if sev in SEVERITIES:
        entries = [e for e in entries if str(e["severity"]).upper() == sev]

    # keyword 필터 (message / reason_code / symbol / source, 마스킹된 텍스트 기준).
    if q:
        ql = str(q).strip().lower()
        if ql:
            def _hit(e):
                hay = " ".join(str(e.get(k) or "") for k in
                               ("message", "reason_code", "symbol", "source", "action"))
                return ql in hay.lower()
            entries = [e for e in entries if _hit(e)]

    # 최신순 정렬 + 최근 100건 상한.
    entries.sort(key=lambda e: e.get("timestamp") or "", reverse=True)
    entries = entries[:limit]

    by_source: dict[str, int] = {}
    by_severity: dict[str, int] = {}
    for e in entries:
        by_source[e["source"]] = by_source.get(e["source"], 0) + 1
        by_severity[e["severity"]] = by_severity.get(e["severity"], 0) + 1

    return {
        "logs": entries,
        "count": len(entries),
        "limit": limit,
        "filters": {"source": src, "severity": sev, "q": q or ""},
        "summary": {"by_source": by_source, "by_severity": by_severity},
        "sources": list(LOG_SOURCES),
        "severities": list(SEVERITIES),
        "is_live_authorization": False,
        "contains_secret": False,
    }

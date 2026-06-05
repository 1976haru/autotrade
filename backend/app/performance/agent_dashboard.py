"""AG3/AG4: 결정 깔때기 + 확신도 보정 — *읽기 전용*, 기존 기록만(KIS 호출 0).

소스: AgentDecisionLog (votes/confidence/reason_code/broker_order_sent/audit_id) +
order_audit_log (체결). 새 카운터 시스템 0 — 기존 기록 집계만.
연결 키: AgentDecisionLog.meta.audit_id == OrderAuditLog.id.
"""

from __future__ import annotations

from collections import defaultdict
from datetime import date, datetime, timedelta, timezone
from typing import Any

from sqlalchemy.orm import Session

from app.db.models import AgentDecisionLog, OrderAuditLog
from app.performance.performance import SMALL_SAMPLE_THRESHOLD, compute_round_trips

KST = timezone(timedelta(hours=9))


def _kst_date(dt: datetime) -> date:
    if dt is None:
        return date(1970, 1, 1)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(KST).date()


def _paper_decisions_in_period(db: Session, start: date, end: date) -> list[AgentDecisionLog]:
    rows = db.query(AgentDecisionLog).filter(AgentDecisionLog.meta.isnot(None)).all()
    return [r for r in rows if start <= _kst_date(r.created_at) <= end]


def _has_buy_vote(meta: dict) -> bool:
    return any(str(v.get("signal", "")).upper() == "BUY" for v in (meta.get("votes") or []))


# ── AG3: 결정 깔때기 ───────────────────────────────────────────────────────────

def compute_funnel(db: Session, *, start: date, end: date) -> dict[str, Any]:
    decisions = _paper_decisions_in_period(db, start, end)

    # 체결 판정용: audit_id → broker_status (연결).
    audit_status: dict[int, str] = {}
    for r in db.query(OrderAuditLog).all():
        audit_status[int(r.id)] = str(r.broker_status or "").upper()

    signal, passed, submitted, filled = [], [], [], []
    drop_council, drop_order, drop_fill = defaultdict(int), defaultdict(int), defaultdict(int)

    for d in decisions:
        meta = d.meta or {}
        final = str(meta.get("final_action", d.decision) or "").upper()
        if not _has_buy_vote(meta) and final != "BUY":
            continue  # BUY 신호와 무관한 결정(순수 SELL/HOLD) — 매수 깔때기 밖.
        signal.append(d)
        if final != "BUY":
            drop_council[str(meta.get("reason_code") or "사유 미기록")] += 1
            continue
        passed.append(d)
        if not bool(meta.get("broker_order_sent")):
            drop_order[str(meta.get("reason_code") or "사유 미기록")] += 1
            continue
        submitted.append(d)
        aid = meta.get("audit_id")
        if aid is not None and audit_status.get(int(aid)) == "FILLED":
            filled.append(d)
        else:
            drop_fill[str(meta.get("reason_code") or "사유 미기록")] += 1

    def _top(dd, n=3):
        return [{"reason_code": k, "count": v}
                for k, v in sorted(dd.items(), key=lambda kv: -kv[1])[:n]]

    return {
        "stages": [
            {"key": "signal",    "label": "신호 발생",    "count": len(signal)},
            {"key": "council",   "label": "council 통과", "count": len(passed)},
            {"key": "submitted", "label": "주문 제출",    "count": len(submitted)},
            {"key": "filled",    "label": "체결",         "count": len(filled)},
        ],
        "drops": [
            {"from": "signal", "to": "council",   "count": len(signal) - len(passed),       "reasons": _top(drop_council)},
            {"from": "council", "to": "submitted", "count": len(passed) - len(submitted),    "reasons": _top(drop_order)},
            {"from": "submitted", "to": "filled",  "count": len(submitted) - len(filled),    "reasons": _top(drop_fill)},
        ],
        "no_data": len(signal) == 0,
        "period_start_kst": start.isoformat(),
        "period_end_kst":   end.isoformat(),
    }


# ── AG4: 확신도 보정 ───────────────────────────────────────────────────────────

_BUCKETS = [(0.6, 0.7, "0.6~0.7"), (0.7, 0.8, "0.7~0.8"), (0.8, 1.01, "0.8+")]


def _confidence_by_audit_id(db: Session) -> dict[int, float]:
    out: dict[int, float] = {}
    for r in db.query(AgentDecisionLog).filter(AgentDecisionLog.meta.isnot(None)).all():
        meta = r.meta or {}
        aid = meta.get("audit_id")
        conf = meta.get("confidence")
        if aid is not None and conf is not None:
            out[int(aid)] = float(conf)
    return out


def compute_calibration(db: Session, *, start: date, end: date, mode: str | None = None) -> dict[str, Any]:
    trips = [t for t in compute_round_trips(db, mode=mode) if start <= t.closed_at_kst <= end]
    conf_map = _confidence_by_audit_id(db)

    buckets = {lbl: {"trades": 0, "wins": 0} for _, _, lbl in _BUCKETS}
    for t in trips:
        # 진입 confidence = 첫 진입 BUY 결정의 council confidence.
        conf = None
        for aid in t.entry_audit_ids:
            if aid in conf_map:
                conf = conf_map[aid]
                break
        if conf is None:
            continue  # 스냅샷 없는 진입 — 보정 대상 아님(추정 금지).
        for lo, hi, lbl in _BUCKETS:
            if lo <= conf < hi:
                buckets[lbl]["trades"] += 1
                if t.net_pnl > 0:
                    buckets[lbl]["wins"] += 1
                break

    out_buckets = []
    total_attr = 0
    for _, _, lbl in _BUCKETS:
        b = buckets[lbl]
        n = b["trades"]
        total_attr += n
        if n == 0:
            continue  # 0건 구간은 표시 제외.
        out_buckets.append({
            "bucket":      lbl,
            "trade_count": n,
            "win_rate":    round(b["wins"] / n, 4),
            "small_sample": n < 5,
        })
    return {
        "buckets":   out_buckets,
        "no_data":   total_attr == 0,
        "period_start_kst": start.isoformat(),
        "period_end_kst":   end.isoformat(),
    }

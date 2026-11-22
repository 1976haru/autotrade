"""S4: 기법별 성적 집계 — *읽기 전용*. P1의 FIFO 라운드트립·33bps 비용 재사용.

귀속 규칙:
  기법 X의 성적 = "진입 스냅샷(AgentDecisionLog.meta.votes)에서 X가 *매수 찬성표*를
  던진 거래"의 청산 결과. 한 거래가 여러 기법에 동시 귀속될 수 있다(4기법 찬성이면
  4곳에 집계) — 합계가 전체 거래 수와 다를 수 있음(화면에 명시).

연결 키: round-trip 의 진입 BUY order_audit_log.id  ↔  AgentDecisionLog.meta.audit_id.
스냅샷 없는 과거 거래(audit_id 매칭 없음) → 귀속 불가 → 집계 제외(추정 소급 금지).
"""

from __future__ import annotations

from collections import defaultdict
from datetime import date
from typing import Any

from sqlalchemy.orm import Session

from app.core.runtime_config import effective_active_profile
from app.performance.performance import SMALL_SAMPLE_THRESHOLD, compute_round_trips

TECHNIQUES = ("ORB", "MOMENTUM", "VWAP", "GAP")


def _buy_voters_by_audit_id(db: Session) -> dict[int, set[str]]:
    """order_audit_log.id → 그 진입에 *매수 찬성표*를 던진 기법 집합 (decision 스냅샷)."""
    from app.db.models import AgentDecisionLog
    out: dict[int, set[str]] = {}
    rows = db.query(AgentDecisionLog).filter(AgentDecisionLog.meta.isnot(None)).all()
    for r in rows:
        meta = r.meta or {}
        aid = meta.get("audit_id")
        if aid is None:
            continue
        techs = set()
        for v in (meta.get("votes") or []):
            if str(v.get("signal", "")).upper() == "BUY":
                t = str(v.get("strategy", "")).upper()
                if t:
                    techs.add(t)
        if techs:
            out.setdefault(int(aid), set()).update(techs)
    return out


def compute_by_technique(db: Session, *, start: date, end: date,
                         mode: str | None = None) -> dict[str, Any]:
    trips = [t for t in compute_round_trips(db, mode=mode) if start <= t.closed_at_kst <= end]
    voters = _buy_voters_by_audit_id(db)

    stat = defaultdict(lambda: {"trades": 0, "wins": 0, "losses": 0, "net": 0})
    attributed = 0
    unattributed = 0
    for t in trips:
        techs: set[str] = set()
        for aid in t.entry_audit_ids:
            techs |= voters.get(aid, set())
        if not techs:
            unattributed += 1   # 스냅샷 없는 진입 → 귀속 불가(집계 제외)
            continue
        attributed += 1
        for x in techs:
            s = stat[x]
            s["trades"] += 1
            if t.net_pnl > 0:
                s["wins"] += 1
            elif t.net_pnl < 0:
                s["losses"] += 1
            s["net"] += t.net_pnl

    techniques = []
    for name in TECHNIQUES:
        s = stat.get(name, {"trades": 0, "wins": 0, "losses": 0, "net": 0})
        n = s["trades"]
        techniques.append({
            "technique":             name,
            "trade_count":           n,
            "win_count":             s["wins"],
            "loss_count":            s["losses"],
            "win_rate":              (round(s["wins"] / n, 4) if n else None),
            "net_contribution_krw":  int(s["net"]),
        })

    return {
        "techniques":          techniques,
        "attributed_count":    attributed,
        "unattributed_count":  unattributed,
        "no_data":             attributed == 0,
        "small_sample":        0 < attributed < SMALL_SAMPLE_THRESHOLD,
        "active_profile":      effective_active_profile(),  # 현재 활성 성향 기준임을 명시
        "period_start_kst":    start.isoformat(),
        "period_end_kst":      end.isoformat(),
        "multi_attribution_note": "한 거래에 여러 기법이 함께 찬성할 수 있어 합계가 전체 거래 수와 다를 수 있어요.",
    }

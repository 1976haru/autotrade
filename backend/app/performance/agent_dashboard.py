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

    # W3(2026-06-10): '주문 제출'·'체결' 은 *order_audit_log 하루 누적*(칩과 동일 소스)
    #   에서 — AgentDecisionLog meta.broker_order_sent/audit_id 링크가 누락돼도 실제
    #   broker 접수(broker_order_id 발급)·체결(FILLED)을 정직하게 반영(BUY/SELL 무관).
    #   예전엔 BUY 결정 링크만 봐서 실제 15건 접수·체결이 깔때기에 0 으로 보였다.
    audit_submitted = 0
    audit_filled = 0
    for r in db.query(OrderAuditLog).filter(
            OrderAuditLog.trade_reason == "kis_paper_auto").all():
        if not (start <= _kst_date(r.created_at) <= end):
            continue
        # 제출 = broker 로 전송된 주문(executed) — 칩의 '오늘 주문'과 동일 모집단.
        if bool(getattr(r, "executed", False)):
            audit_submitted += 1
        if str(r.broker_status or "").upper() == "FILLED":
            audit_filled += 1
    n_signal, n_council = len(signal), len(passed)
    n_submitted = max(len(submitted), audit_submitted)   # 실제 접수 누락 방지
    n_filled = max(len(filled), audit_filled)
    _clamp = lambda x: max(0, x)

    return {
        "stages": [
            {"key": "signal",    "label": "신호 발생",    "count": n_signal},
            {"key": "council",   "label": "council 통과", "count": n_council},
            {"key": "submitted", "label": "주문 제출",    "count": n_submitted},
            {"key": "filled",    "label": "체결",         "count": n_filled},
        ],
        "drops": [
            {"from": "signal", "to": "council",   "count": _clamp(n_signal - n_council),    "reasons": _top(drop_council)},
            {"from": "council", "to": "submitted", "count": _clamp(n_council - n_submitted), "reasons": _top(drop_order)},
            {"from": "submitted", "to": "filled",  "count": _clamp(n_submitted - n_filled),  "reasons": _top(drop_fill)},
        ],
        "no_data": n_signal == 0 and n_submitted == 0,
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


# ── AG5: 다음 학습 방향 (규칙 기반 관찰 — LLM/추측 0) ──────────────────────────
#
# 임계값과 문구는 *코드 상수*. 조건 미충족 시 문장 자체를 생성하지 않는다(억지 금지).
TECHNIQUE_MIN_SAMPLE   = 10     # 기법 관찰 최소 표본
TECHNIQUE_WINRATE_GAP  = 0.15   # 기법 승률이 전체보다 이만큼 낮으면 발화
OVERCONF_BUCKET        = "0.7~0.8"
OVERCONF_MIN_SAMPLE    = 5
OVERCONF_FLOOR         = 0.70   # 70%대 확신인데 실제 승률이 이 밑이면 과신
SHADOW_CORRECT_MIN     = 0.60   # 기각 적중률
SHADOW_MIN_COMPLETED   = 5

LEARNING_FOOTER = "이 관찰을 바탕으로 한 설정 변경은 운영자 승인으로 진행돼요."


def compute_learning(db: Session, *, start: date, end: date) -> dict[str, Any]:
    """관찰 문장 + 근거 수치. 자동 학습 아님 — 사람이 읽고 가설을 세우는 1단계."""
    from app.performance.by_technique import compute_by_technique
    from app.performance.performance import compute_performance
    from app.performance.shadow import compute_shadow

    perf = compute_performance(db, start=start, end=end)
    tech = compute_by_technique(db, start=start, end=end)
    cal = compute_calibration(db, start=start, end=end)
    shadow = compute_shadow(db, start=start, end=end)

    overall_wr = perf.get("win_rate")
    observations: list[dict[str, Any]] = []

    # 1. 기법 승률 저조.
    if overall_wr is not None:
        for t in tech["techniques"]:
            if t["trade_count"] >= TECHNIQUE_MIN_SAMPLE and t["win_rate"] is not None \
                    and t["win_rate"] <= overall_wr - TECHNIQUE_WINRATE_GAP:
                observations.append({
                    "code": "TECHNIQUE_LOW_WINRATE",
                    "text": f"{t['technique']} 찬성 거래의 승률이 낮아요 — 가중치 재검토 후보",
                    "evidence": {"technique": t["technique"], "trades": t["trade_count"],
                                 "win_rate": t["win_rate"], "overall_win_rate": overall_wr},
                })

    # 2. 과신 구간.
    for b in cal["buckets"]:
        if b["bucket"] == OVERCONF_BUCKET and b["trade_count"] >= OVERCONF_MIN_SAMPLE \
                and b["win_rate"] < OVERCONF_FLOOR:
            observations.append({
                "code": "OVERCONFIDENCE",
                "text": "확신 70%대 거래의 실제 승률이 그에 못 미쳐요 — 과신 구간",
                "evidence": {"bucket": b["bucket"], "trades": b["trade_count"], "win_rate": b["win_rate"]},
            })

    # 3. 기각 적중.
    if shadow["completed_count"] >= SHADOW_MIN_COMPLETED and shadow["correct_rate"] is not None \
            and shadow["correct_rate"] >= SHADOW_CORRECT_MIN:
        observations.append({
            "code": "GOOD_REJECTION",
            "text": "council의 기각이 손실을 잘 걸러내고 있어요",
            "evidence": {"completed": shadow["completed_count"], "correct_rate": shadow["correct_rate"],
                         "avoided_loss_krw": shadow["avoided_loss_krw"]},
        })

    # 4. 기본(표본 부족) — 다른 관찰이 하나도 없을 때만.
    if not observations:
        observations.append({
            "code": "INSUFFICIENT_SAMPLE",
            "text": "아직 표본이 부족해요 — 판단 보류",
            "evidence": {"closed_count": perf.get("closed_count", 0)},
        })

    return {
        "observations": observations,
        "footer":       LEARNING_FOOTER,
        "period_start_kst": start.isoformat(),
        "period_end_kst":   end.isoformat(),
    }

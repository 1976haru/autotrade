"""P-21: Agent Decision Episode 저장소 — 판단→주문→체결→성과를 episode 단위 연결.

하나의 BUY/SELL/HOLD 판단마다 `episode_id` 를 발급하고, 그 판단의 전 과정(시장
스냅샷 / 4전략 vote / Agent Council 최종판단 / RiskManager 결과 / PermissionGate
결과 / KIS Paper 주문 결과 / 체결 / 포트폴리오 변화 / 사후 성과 라벨)을 단일
`agent_decision_episode` 행에 기록한다. 에이전트 성능 개선용 데이터셋.

## 절대 invariant (CLAUDE.md)
- 본 모듈은 *기록 전용* — broker / OrderExecutor / route_order import 0건,
  주문을 만들지 않는다.
- 모든 행 `is_live_authorization=False` 영구.
- secret / API key / 계좌번호 carry 0건 — 저장 *전* `sanitize_dict`(fail-closed)
  로 검사, 적중 시 `SecretLeakError` raise (저장 거부).
- DB 는 본 모듈 내에서 INSERT/UPDATE 만 (DELETE 0건 — append + outcome update).
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.agents.agent_memory import sanitize_dict
from app.db.models import AgentDecisionEpisode

_VALID_ACTIONS = ("BUY", "SELL", "HOLD")


def new_episode_id() -> str:
    """episode 추적 키 발급 — AgentDecisionLog.chain_id 와 동일 값으로 연결."""
    return f"ep-{uuid.uuid4().hex[:16]}"


def _scrub(d: Any, *, field: str) -> Any:
    """JSON 블록을 fail-closed sanitize (secret 적중 시 SecretLeakError)."""
    if d is None:
        return None
    if isinstance(d, dict):
        return sanitize_dict(d, field_name=field)
    if isinstance(d, list):
        return sanitize_dict({"_": d}, field_name=field)["_"]
    return d


def _int_or_none(v: Any) -> int | None:
    try:
        return int(v) if v is not None else None
    except (TypeError, ValueError):
        return None


def record_episode(
    db: Session,
    *,
    episode_id: str,
    final_action: str,
    symbol: str | None = None,
    mode: str = "PAPER",
    confidence: Any = None,
    quality_score: Any = None,
    reason_code: str | None = None,
    market_snapshot: dict | None = None,
    votes: list | None = None,
    council: dict | None = None,
    risk_result: dict | None = None,
    permission_result: dict | None = None,
    kis_order_result: dict | None = None,
    fill_result: dict | None = None,
    portfolio_delta: dict | None = None,
    outcome: dict | None = None,
    sell_reason: dict | None = None,
    broker_order_no: str | None = None,
    audit_id: int | None = None,
    decision_log_id: int | None = None,
) -> AgentDecisionEpisode:
    """단일 episode 행 기록 — 저장 전 secret sanitize. caller 가 commit.

    `final_action` 은 BUY/SELL/HOLD. 그 외 값은 그대로 저장하되 index 용도이므로
    상한 16자. `is_live_authorization` 은 항상 False (모델 default).

    P-26: `sell_reason`(dict) 가 주어지면 council JSON 에 nest + kis_order_result
    에 sell_reason_code carry. SELL 인데 reason_code 가 비어있으면 sell_reason 의
    reason_code 로 연결 (마이그레이션 0건 — 기존 JSON 컬럼 재사용).
    """
    action = (final_action or "HOLD").upper()
    # P-26: SELL sell_reason 을 council / kis_order_result JSON 에 nest (no migration).
    if isinstance(sell_reason, dict) and sell_reason.get("reason_code"):
        if isinstance(council, dict):
            council = {**council, "sell_reason": sell_reason}
        else:
            council = {"sell_reason": sell_reason}
        if isinstance(kis_order_result, dict):
            kis_order_result = {**kis_order_result,
                                "sell_reason_code": sell_reason.get("reason_code")}
        if action == "SELL" and not reason_code:
            reason_code = sell_reason.get("reason_code")
    row = AgentDecisionEpisode(
        episode_id=str(episode_id),
        symbol=(symbol[:16] if isinstance(symbol, str) else symbol),
        mode=str(mode or "PAPER")[:32],
        final_action=action[:16],
        confidence=_int_or_none(confidence),
        quality_score=_int_or_none(quality_score),
        reason_code=(str(reason_code)[:64] if reason_code else None),
        market_snapshot=_scrub(market_snapshot, field="market_snapshot"),
        votes=_scrub(votes, field="votes"),
        council=_scrub(council, field="council"),
        risk_result=_scrub(risk_result, field="risk_result"),
        permission_result=_scrub(permission_result, field="permission_result"),
        kis_order_result=_scrub(kis_order_result, field="kis_order_result"),
        fill_result=_scrub(fill_result, field="fill_result"),
        portfolio_delta=_scrub(portfolio_delta, field="portfolio_delta"),
        outcome=_scrub(outcome, field="outcome"),
        broker_order_no=(str(broker_order_no)[:64] if broker_order_no else None),
        audit_id=_int_or_none(audit_id),
        decision_log_id=_int_or_none(decision_log_id),
        is_live_authorization=False,
    )
    db.add(row)
    db.flush()
    return row


def update_episode_with_portfolio_snapshot(
    db: Session, episode_id: str, portfolio_delta: dict | None,
) -> AgentDecisionEpisode | None:
    """episode 에 포트폴리오 변화 스냅샷을 사후 연결."""
    row = _get_row(db, episode_id)
    if row is None:
        return None
    row.portfolio_delta = _scrub(portfolio_delta, field="portfolio_delta")
    row.updated_at = datetime.now(timezone.utc)
    db.flush()
    return row


def attach_outcome(
    db: Session, episode_id: str, outcome: dict | None,
) -> AgentDecisionEpisode | None:
    """사후 성과 라벨을 episode 에 연결 (P-28 성과 대시보드용 placeholder update)."""
    row = _get_row(db, episode_id)
    if row is None:
        return None
    row.outcome = _scrub(outcome, field="outcome")
    row.updated_at = datetime.now(timezone.utc)
    db.flush()
    return row


def attach_order_quality(
    db: Session, episode_id: str, quality: dict | None,
) -> AgentDecisionEpisode | None:
    """P-24: 주문·체결 품질 로그를 episode.kis_order_result.order_quality 에 연결.

    별도 컬럼 없이 기존 kis_order_result JSON 내부에 nest (마이그레이션 0건).
    JSON 변경 추적을 위해 dict 를 *재할당*. best-effort — secret sanitize 적용.
    """
    row = _get_row(db, episode_id)
    if row is None:
        return None
    base = dict(row.kis_order_result) if isinstance(row.kis_order_result, dict) else {}
    base["order_quality"] = _scrub(quality, field="order_quality")
    row.kis_order_result = base
    row.updated_at = datetime.now(timezone.utc)
    db.flush()
    return row


def _get_row(db: Session, episode_id: str) -> AgentDecisionEpisode | None:
    return db.execute(
        select(AgentDecisionEpisode).where(
            AgentDecisionEpisode.episode_id == str(episode_id)
        )
    ).scalar_one_or_none()


def episode_to_dict(row: AgentDecisionEpisode) -> dict[str, Any]:
    return {
        "id":              row.id,
        "episode_id":      row.episode_id,
        "created_at":      row.created_at.isoformat() if row.created_at else None,
        "updated_at":      row.updated_at.isoformat() if row.updated_at else None,
        "symbol":          row.symbol,
        "mode":            row.mode,
        "final_action":    row.final_action,
        "confidence":      row.confidence,
        "quality_score":   row.quality_score,
        "reason_code":     row.reason_code,
        "market_snapshot": row.market_snapshot,
        "votes":           row.votes,
        "council":         row.council,
        "risk_result":     row.risk_result,
        "permission_result": row.permission_result,
        "kis_order_result":  row.kis_order_result,
        "fill_result":     row.fill_result,
        "portfolio_delta": row.portfolio_delta,
        "outcome":         row.outcome,
        "broker_order_no": row.broker_order_no,
        "audit_id":        row.audit_id,
        "decision_log_id": row.decision_log_id,
        "selected_strategies": (
            (row.council or {}).get("selected_strategies", [])
            if isinstance(row.council, dict) else []
        ),
        # P-22: market_snapshot 요약 (목록 표시용 — 전체는 market_snapshot 에).
        "market_summary":  _market_summary(row.market_snapshot),
        # P-23: 4전략 vote 요약 (목록 표시용 — 전체는 votes 에).
        "vote_summary":    _vote_summary(row.votes),
        # P-24: 주문·체결 품질 요약 (전체는 kis_order_result.order_quality 에).
        "order_quality_summary": _order_quality_summary(row.kis_order_result),
        # P-25: 사후 성과 요약 (전체는 outcome 에).
        "outcome_summary": _outcome_summary_for(row.outcome),
        # P-26: 매도 사유 (SELL 일 때만 비어있지 않음 — 전체는 council.sell_reason 에).
        "sell_reason":         _sell_reason_for(row),
        "sell_reason_summary": _sell_reason_summary_for(row),
        # invariant carry.
        "is_live_authorization": False,
        "is_order_signal":       False,
    }


def _sell_reason_for(row: AgentDecisionEpisode) -> dict[str, Any]:
    """council.sell_reason → 상세 dict (SELL 이 아니면 빈 dict)."""
    c = row.council if isinstance(row.council, dict) else {}
    sr = c.get("sell_reason")
    return sr if isinstance(sr, dict) and sr.get("reason_code") else {}


def _sell_reason_summary_for(row: AgentDecisionEpisode) -> dict[str, Any]:
    """council.sell_reason → 목록 표시용 요약 (sell_reason.sell_reason_summary 위임)."""
    from app.agents.sell_reason import sell_reason_summary
    return sell_reason_summary(_sell_reason_for(row) or None)


def _outcome_summary_for(outcome: Any) -> dict[str, Any]:
    """outcome dict → 목록 표시용 요약 (post_trade_outcome.outcome_summary 위임)."""
    from app.agents.post_trade_outcome import outcome_summary
    return outcome_summary(outcome if isinstance(outcome, dict) else None)


def _order_quality_summary(kis_order_result: Any) -> dict[str, Any]:
    """kis_order_result.order_quality → 목록 표시용 요약."""
    if not isinstance(kis_order_result, dict):
        return {}
    q = kis_order_result.get("order_quality")
    if not isinstance(q, dict):
        return {}
    return {
        "broker_order_no": q.get("broker_order_no"),
        "order_status":    q.get("order_status"),
        "fill_status":     q.get("fill_status"),
        "latency_ms":      q.get("latency_ms"),
        "slippage_bps":    q.get("slippage_bps"),
        "partial_fill":    q.get("partial_fill"),
    }


def _vote_summary(votes: Any) -> dict[str, Any]:
    """4전략 vote 목록에서 요약 (strategies/signal 카운트/top)."""
    if not isinstance(votes, list) or not votes:
        return {"strategies": [], "buy_vote_count": 0, "sell_vote_count": 0,
                "hold_vote_count": 0, "top_strategy": None, "top_score": 0}
    buy = sell = hold = 0
    top_strategy = None
    top_score = -1
    strategies: list[str] = []
    for v in votes:
        if not isinstance(v, dict):
            continue
        strat = v.get("strategy")
        if strat:
            strategies.append(strat)
        sig = str(v.get("signal", "")).upper()
        if sig == "BUY":
            buy += 1
        elif sig == "SELL":
            sell += 1
        else:
            hold += 1
        sc = v.get("score") or 0
        if isinstance(sc, (int, float)) and sc > top_score:
            top_score = sc
            top_strategy = strat
    return {
        "strategies":      strategies,
        "buy_vote_count":  buy,
        "sell_vote_count": sell,
        "hold_vote_count": hold,
        "top_strategy":    top_strategy,
        "top_score":       int(top_score) if top_score >= 0 else 0,
    }


def _market_summary(snap: Any) -> dict[str, Any]:
    """market_snapshot 에서 목록 표시용 핵심 필드만 추출."""
    if not isinstance(snap, dict):
        return {"data_status": "NO_MARKET_DATA"}
    return {
        "price":             snap.get("price"),
        "market_regime":     snap.get("market_regime"),
        "data_status":       snap.get("data_status"),
        "reason_code":       snap.get("reason_code"),
        "price_age_seconds": snap.get("price_age_seconds"),
        "vwap":              snap.get("vwap"),
        "rsi":               snap.get("rsi"),
        "gap_pct":           snap.get("gap_pct"),
    }


def get_episode(db: Session, episode_id: str) -> dict[str, Any] | None:
    row = _get_row(db, episode_id)
    return episode_to_dict(row) if row is not None else None


def list_episodes(
    db: Session,
    *,
    limit: int = 20,
    symbol: str | None = None,
    action: str | None = None,
) -> list[dict[str, Any]]:
    """최근 episode 목록 (최신순). symbol / action AND 필터."""
    stmt = select(AgentDecisionEpisode)
    if symbol:
        stmt = stmt.where(AgentDecisionEpisode.symbol == symbol)
    if action:
        stmt = stmt.where(AgentDecisionEpisode.final_action == action.upper())
    stmt = stmt.order_by(AgentDecisionEpisode.created_at.desc()).limit(max(1, int(limit)))
    rows = db.execute(stmt).scalars().all()
    return [episode_to_dict(r) for r in rows]


def summarize_episodes(db: Session, *, limit: int = 200) -> dict[str, Any]:
    """episode 집계 — by_action / submitted_count / by_reason_code."""
    stmt = (
        select(AgentDecisionEpisode)
        .order_by(AgentDecisionEpisode.created_at.desc())
        .limit(max(1, int(limit)))
    )
    rows = db.execute(stmt).scalars().all()
    by_action: dict[str, int] = {}
    by_reason: dict[str, int] = {}
    by_data_status: dict[str, int] = {}
    by_strategy: dict[str, int] = {}     # P-23: 전략별 vote 출현 수
    by_signal: dict[str, int] = {}       # P-23: signal 별 vote 수
    by_order_status: dict[str, int] = {}  # P-24
    by_fill_status: dict[str, int] = {}   # P-24
    by_outcome_label: dict[str, int] = {}   # P-25
    by_outcome_status: dict[str, int] = {}  # P-25
    by_sell_reason: dict[str, int] = {}      # P-26: SELL 사유별 카운트
    by_sell_category: dict[str, int] = {}    # P-26: SELL 사유 카테고리별
    latencies: list[int] = []
    slippages: list[float] = []
    rejected_count = 0
    partial_fill_count = 0
    submitted = 0
    with_order_no = 0
    for r in rows:
        by_action[r.final_action] = by_action.get(r.final_action, 0) + 1
        if r.reason_code:
            by_reason[r.reason_code] = by_reason.get(r.reason_code, 0) + 1
        if isinstance(r.kis_order_result, dict) and r.kis_order_result.get("submitted"):
            submitted += 1
        if r.broker_order_no:
            with_order_no += 1
        # P-22: market_snapshot data_status 집계.
        ds = (r.market_snapshot or {}).get("data_status") if isinstance(
            r.market_snapshot, dict) else None
        ds = ds or "UNKNOWN"
        by_data_status[ds] = by_data_status.get(ds, 0) + 1
        # P-23: 전략별 / signal 별 vote 집계.
        if isinstance(r.votes, list):
            for v in r.votes:
                if not isinstance(v, dict):
                    continue
                strat = v.get("strategy")
                if strat:
                    by_strategy[strat] = by_strategy.get(strat, 0) + 1
                sig = str(v.get("signal", "")).upper() or "UNKNOWN"
                by_signal[sig] = by_signal.get(sig, 0) + 1
        # P-24: order_quality 집계.
        q = (r.kis_order_result or {}).get("order_quality") if isinstance(
            r.kis_order_result, dict) else None
        if isinstance(q, dict):
            os_ = q.get("order_status") or "UNKNOWN"
            by_order_status[os_] = by_order_status.get(os_, 0) + 1
            fs_ = q.get("fill_status") or "NONE"
            by_fill_status[fs_] = by_fill_status.get(fs_, 0) + 1
            if isinstance(q.get("latency_ms"), (int, float)):
                latencies.append(q["latency_ms"])
            if isinstance(q.get("slippage_bps"), (int, float)):
                slippages.append(q["slippage_bps"])
            if os_ == "REJECTED":
                rejected_count += 1
            if q.get("partial_fill"):
                partial_fill_count += 1
        # P-25: outcome 집계.
        if isinstance(r.outcome, dict):
            ol = r.outcome.get("label") or "NONE"
            by_outcome_label[ol] = by_outcome_label.get(ol, 0) + 1
            ost = r.outcome.get("status") or "PENDING"
            by_outcome_status[ost] = by_outcome_status.get(ost, 0) + 1
        # P-26: SELL 사유 집계 (council.sell_reason).
        if r.final_action == "SELL" and isinstance(r.council, dict):
            sr = r.council.get("sell_reason")
            if isinstance(sr, dict) and sr.get("reason_code"):
                code = sr.get("reason_code")
                by_sell_reason[code] = by_sell_reason.get(code, 0) + 1
                cat = sr.get("category") or "UNKNOWN"
                by_sell_category[cat] = by_sell_category.get(cat, 0) + 1
    return {
        "total":           len(rows),
        "by_action":       by_action,
        "by_reason_code":  by_reason,
        "by_data_status":  by_data_status,
        "by_strategy":     by_strategy,
        "by_signal":       by_signal,
        "by_order_status": by_order_status,
        "by_fill_status":  by_fill_status,
        "by_outcome_label":  by_outcome_label,
        "by_outcome_status": by_outcome_status,
        "by_sell_reason":    by_sell_reason,
        "by_sell_category":  by_sell_category,
        "avg_latency_ms":  (round(sum(latencies) / len(latencies), 1) if latencies else None),
        "avg_slippage_bps": (round(sum(slippages) / len(slippages), 2) if slippages else None),
        "rejected_count":     rejected_count,
        "partial_fill_count": partial_fill_count,
        "submitted_count": submitted,
        "with_broker_order_no": with_order_no,
        "is_live_authorization": False,
    }


__all__ = [
    "new_episode_id",
    "record_episode",
    "update_episode_with_portfolio_snapshot",
    "attach_outcome",
    "get_episode",
    "list_episodes",
    "summarize_episodes",
    "episode_to_dict",
]

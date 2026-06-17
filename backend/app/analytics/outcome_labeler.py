"""청산 round-trip → 진입 episode 에 사후 성과(outcome) 라벨링. *집계/측정 계층*.

배경: 학습(설계 A)의 기법별 expectancy 가 산출 불가였던 근본원인 — outcome 라벨이
라이브 청산에 안 붙음(`attach_outcome` 의 유일 호출자가 API 엔드포인트뿐, 라이브 루프 미연결).
본 모듈은 FIFO round-trip 을 그 *진입 episode*(selected_strategies 보유)에 연결해 net 수익률을
attach 한다 → strategy_performance 가 기법별 승률·손익비·expectancy 를 정상 산출.

★범위: *앞으로 청산되는 거래부터* 라벨(최근 lookback 일만). 과거 9,259건 소급 안 함.
★학습 자동조정 0 — 라벨링 인프라일 뿐. agent_council/주문경로/driver_bridge import 0(정적 가드).
봇 거래 동작에 영향 0(측정·기록만).
"""

from __future__ import annotations

import logging
from datetime import date, datetime, timedelta, timezone
from typing import Any

_log = logging.getLogger("autotrade.outcome_labeler")
_KST = timezone(timedelta(hours=9))

# 본 라벨러가 붙인 outcome 의 출처 표식(중복 라벨 방지).
OUTCOME_SOURCE = "fifo_round_trip"


def _outcome_from_round_trip(t: Any) -> dict[str, Any]:
    buy_cost = float(getattr(t, "buy_cost", 0) or 0)
    net = float(getattr(t, "net_pnl", 0) or 0)
    ret_pct = round(net / buy_cost * 100.0, 4) if buy_cost > 0 else 0.0
    return {
        "status": "FILLED",                       # episode_return: PENDING/UNAVAILABLE 가 아니어야 집계됨
        "label": "WIN" if net > 0 else ("LOSS" if net < 0 else "FLAT"),
        "realized_pnl": ret_pct,                  # episode_return 이 1순위로 읽는 키(%)
        "realized_pnl_krw": int(round(net)),
        "quantity": int(getattr(t, "quantity", 0) or 0),
        "source": OUTCOME_SOURCE,
    }


def label_closed_round_trips(db: Any, *, now: datetime | None = None,
                             lookback_days: int = 2) -> int:
    """최근 청산된 FIFO round-trip 을 진입 episode 에 outcome 라벨링. 라벨한 건수 반환.

    멱등(이미 fifo_round_trip outcome 있으면 skip). read-mostly + episode.outcome write 만.
    """
    now = now or datetime.now(timezone.utc)
    labeled = 0
    try:
        from app.db.models import AgentDecisionEpisode
        from app.performance.performance import compute_round_trips
        from app.agents.decision_episode import attach_outcome

        cutoff = (now.astimezone(_KST).date() - timedelta(days=max(0, lookback_days)))
        trips = compute_round_trips(db)
        for t in trips:
            closed = getattr(t, "closed_at_kst", None)
            if isinstance(closed, date) and closed < cutoff:
                continue  # 오래된 청산은 skip(앞으로분만 — 소급 안 함).
            eids = list(getattr(t, "entry_audit_ids", None) or [])
            if not eids:
                continue
            ep = (db.query(AgentDecisionEpisode)
                  .filter(AgentDecisionEpisode.audit_id.in_(eids))
                  .first())
            if ep is None:
                continue
            # 멱등 — 이미 본 라벨러가 붙였으면 skip.
            cur = ep.outcome if isinstance(ep.outcome, dict) else None
            if cur and str(cur.get("source") or "") == OUTCOME_SOURCE:
                continue
            attach_outcome(db, ep.episode_id, _outcome_from_round_trip(t))
            labeled += 1
        if labeled:
            db.commit()
            _log.info("[outcome-labeler] 라운드트립 outcome 라벨 %d건(진입 episode 연결)", labeled)
    except Exception as exc:  # noqa: BLE001 — 라벨링 실패는 봇/거래에 영향 0.
        try:
            db.rollback()
        except Exception:  # noqa: BLE001
            pass
        _log.warning("[outcome-labeler] 실패(무시): %s: %s", type(exc).__name__, exc)
    return labeled

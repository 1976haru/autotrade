"""#4-10: Paper AI 판단 로그 read-only API.

`GET /api/auto-paper/decision-log` — `AgentDecisionLog` (mode=PAPER) 의 최근 N개
row 를 운영자 친화 JSON 으로 반환.

*broker 호출 0건* — read-only SELECT. INSERT / UPDATE / DELETE 0건 — 본 모듈
정적 grep 가드 (테스트로 lock).

응답에는 secret / API key / 계좌번호 / Anthropic Key / OpenAI Key 포함 0건 —
decision_log 모듈의 sanitizer 가 *기록 시점* 에 차단하고, 본 endpoint 는
저장된 row 만 그대로 반환.
"""

from __future__ import annotations

from typing import Optional

from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from app.auto_paper.decision_log import (
    DECISION_LOG_SCHEMA_VERSION,
    PAPER_DECISION_LOG_MODE,
    PAPER_DECISION_LOG_SOURCE,
    query_paper_decision_log,
    summarize_paper_decisions,
)
from app.db.session import get_db


router = APIRouter(prefix="/auto-paper", tags=["auto-paper"])


@router.get("/decision-log")
def get_paper_decision_log(
    limit:    int = Query(50, ge=1, le=1000),
    strategy: Optional[str] = Query(None),
    symbol:   Optional[str] = Query(None),
    action:   Optional[str] = Query(None,
        description="필터: BUY/SELL/HOLD/EXIT/NO_OP — 정확 일치"),
    db: Session = Depends(get_db),
) -> dict:
    """Paper AI 판단 로그 — *read-only*.

    응답 schema:
    ```jsonc
    {
      "mode": "PAPER",
      "source_module": "paper_decision_bridge",
      "schema_version": "1.0",
      "entries": [
        {
          "decision_id": "...",
          "timestamp": "2026-05-18T01:00:00+00:00",
          "agent_name": "PaperDecisionBridge",
          "strategy": "sma_crossover",
          "symbol": "005930",
          "mode": "PAPER",
          "decision_action": "BUY",
          "confidence": 75,
          "reason": "[추천] ...",
          "risk_flags": [],
          "market_regime": "TREND_UP",
          "overfit_flag": false,
          "risk_veto": false,
          "risk_veto_reasons": [],
          "risk_veto_severity": null,
          "position_size": 5,
          "sizing_verdict": "SIZED",
          "paper_order_id": "...",
          "paper_fill_status": "PAPER_FILLED",
          "chain_id": "uuid",
          "source_module": "paper_decision_bridge",
          "is_order_signal": false,
          "auto_apply_allowed": false,
          "is_live_authorization": false
        }
      ],
      "summary": {
        "by_action": { "BUY": 1, "HOLD": 2, ... },
        "veto_count": 0,
        "sizing_reduced": 0
      },
      "is_order_signal": false,
      "auto_apply_allowed": false,
      "is_live_authorization": false
    }
    ```
    """
    # Graceful fallback: if the `agent_decision_log` table is missing (e.g.
    # fresh CI DB that hasn't run migrations), return an empty envelope
    # instead of a 500. The table will exist as soon as alembic upgrade or
    # Base.metadata.create_all runs. Operationally, the lifespan hook
    # creates it on real startups — this guard exists only to make the
    # read-only endpoint robust during test bootstrap.
    try:
        entries = query_paper_decision_log(
            db,
            limit=int(limit),
            strategy=strategy,
            symbol=symbol,
            action=action,
        )
    except Exception as exc:  # noqa: BLE001 — SQLAlchemy ProgrammingError / OperationalError
        if "no such table" in str(exc).lower() or "does not exist" in str(exc).lower():
            entries = []
        else:
            raise
    summary = summarize_paper_decisions(entries)
    return {
        "mode":              PAPER_DECISION_LOG_MODE,
        "source_module":     PAPER_DECISION_LOG_SOURCE,
        "schema_version":    DECISION_LOG_SCHEMA_VERSION,
        "entry_count":       len(entries),
        "entries":           [e.to_dict() for e in entries],
        "summary":           summary,
        "advisory_disclaimer": (
            "본 로그는 *advisory* — Paper AI 판단의 영구 기록만 carry. "
            "실거래 주문 0건, broker 호출 0건. "
            "is_order_signal=False / auto_apply_allowed=False / "
            "is_live_authorization=False."
        ),
        "is_order_signal":       False,
        "auto_apply_allowed":    False,
        "is_live_authorization": False,
    }

"""R2: 런타임 설정 변경을 *활동 피드*("오늘 AI가 한 일")에 기록.

기존 이벤트 기록 경로(AgentDecisionLog, paper decision-log)를 재사용한다 — 새 기록
시스템을 만들지 않는다. 운영자 변경 1건당 AgentDecisionLog row 1개를 남기고,
프론트 livePanelLine 이 decision='CONFIG_CHANGE' 를 감지해 reason 문구를 그대로 표시한다.

주문/리스크 로직과 무관 — 단순 기록.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import Any

from sqlalchemy.orm import Session

OPERATOR_CONFIG_CHANGE_ACTION = "CONFIG_CHANGE"
OPERATOR_CONFIG_CHANGE_REASON_CODE = "OPERATOR_CONFIG_CHANGE"


def _man_won(v: int) -> str:
    """원 → '100만 원' 표기 (만 단위)."""
    return f"{int(v) // 10_000}만 원"


def _message_ko(change: dict[str, Any]) -> str | None:
    key = change.get("key")
    before = change.get("before")
    after = change.get("after")
    if before is None or after is None:
        return None
    if key == "per_stock_budget":
        return f"운영자가 종목당 투자금을 {_man_won(before)} → {_man_won(after)}으로 바꿨어요"
    if key == "max_concurrent_positions":
        return f"운영자가 동시진입 종목 수를 {int(before)}개 → {int(after)}개로 바꿨어요"
    return None


def record_runtime_config_changes(db: Session, changes: list[dict[str, Any]]) -> int:
    """변경 목록 → AgentDecisionLog row(들). 기록 건수 반환. 변경 0이면 0."""
    from app.auto_paper.decision_log import (
        PAPER_DECISION_LOG_MODE,
        PAPER_DECISION_LOG_SOURCE,
    )
    from app.db.models import AgentDecisionLog

    n = 0
    for ch in changes or []:
        msg = _message_ko(ch)
        if not msg:
            continue
        db.add(AgentDecisionLog(
            created_at=datetime.now(timezone.utc),
            agent_name="Operator",
            symbol="SYSTEM",                      # 운영자 이벤트 — 종목 아님(화면은 reason 표시)
            mode=PAPER_DECISION_LOG_MODE,
            decision=OPERATOR_CONFIG_CHANGE_ACTION,
            confidence=None,
            reasons=[msg],
            meta={
                "source_module": PAPER_DECISION_LOG_SOURCE,
                "reason_code":   OPERATOR_CONFIG_CHANGE_REASON_CODE,
                "decision_id":   f"opcfg-{uuid.uuid4().hex[:10]}",
                "strategy":      "",
            },
            chain_id=None,
        ))
        n += 1
    if n:
        db.commit()
    return n


__all__ = [
    "OPERATOR_CONFIG_CHANGE_ACTION",
    "OPERATOR_CONFIG_CHANGE_REASON_CODE",
    "record_runtime_config_changes",
]

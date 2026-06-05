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


_PROFILE_KO = {"conservative": "보수적", "balanced": "안정적", "aggressive": "공격적"}


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
    if key == "active_profile":
        b = _PROFILE_KO.get(str(before), str(before))
        a = _PROFILE_KO.get(str(after), str(after))
        return f"운영자가 운용 성향을 {b} → {a}으로 바꿨어요"
    return None


def record_runtime_config_changes(db: Session, changes: list[dict[str, Any]]) -> int:
    """변경 목록 → AgentDecisionLog row(들). 기록 건수 반환. 변경 0이면 0."""
    n = 0
    for ch in changes or []:
        msg = _message_ko(ch)
        if not msg:
            continue
        _add_operator_event(db, message=msg, action=OPERATOR_CONFIG_CHANGE_ACTION,
                            reason_code=OPERATOR_CONFIG_CHANGE_REASON_CODE)
        n += 1
    if n:
        db.commit()
    return n


# ── 공용 operator 이벤트 writer (R2 경로 재사용) ───────────────────────────────

MANUAL_SELL_ACTION = "MANUAL_SELL"
MANUAL_SELL_REASON_CODE = "OPERATOR_MANUAL_SELL"


def _add_operator_event(db: Session, *, message: str, action: str,
                        reason_code: str, prefix: str = "opev") -> None:
    """운영자 이벤트 1건을 활동 피드(AgentDecisionLog/paper decision-log)에 적재.
    commit 은 호출자 책임(여러 건 묶을 수 있게)."""
    from app.auto_paper.decision_log import (
        PAPER_DECISION_LOG_MODE,
        PAPER_DECISION_LOG_SOURCE,
    )
    from app.db.models import AgentDecisionLog
    db.add(AgentDecisionLog(
        created_at=datetime.now(timezone.utc),
        agent_name="Operator",
        symbol="SYSTEM",
        mode=PAPER_DECISION_LOG_MODE,
        decision=action,
        confidence=None,
        reasons=[message],
        meta={
            "source_module": PAPER_DECISION_LOG_SOURCE,
            "reason_code":   reason_code,
            "decision_id":   f"{prefix}-{uuid.uuid4().hex[:10]}",
            "strategy":      "",
        },
        chain_id=None,
    ))


def record_manual_sell_submitted(db: Session, *, symbol_name: str, quantity: int) -> None:
    """수동 전량 매도 제출 기록 — '주문을 보냈어요'(체결 단정 금지)."""
    _add_operator_event(
        db, message=f"운영자가 {symbol_name} {int(quantity)}주 전량 매도 주문을 보냈어요",
        action=MANUAL_SELL_ACTION, reason_code=MANUAL_SELL_REASON_CODE, prefix="opsell")
    db.commit()


def record_manual_sell_rejected(db: Session, *, symbol_name: str, reason_ko: str) -> None:
    _add_operator_event(
        db, message=f"수동 매도가 거절됐어요 — {reason_ko}",
        action=MANUAL_SELL_ACTION, reason_code=MANUAL_SELL_REASON_CODE, prefix="opsell")
    db.commit()


__all__ = [
    "OPERATOR_CONFIG_CHANGE_ACTION",
    "OPERATOR_CONFIG_CHANGE_REASON_CODE",
    "MANUAL_SELL_ACTION",
    "MANUAL_SELL_REASON_CODE",
    "record_runtime_config_changes",
    "record_manual_sell_submitted",
    "record_manual_sell_rejected",
]

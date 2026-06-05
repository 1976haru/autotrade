"""장중 런타임 설정 변경 API — 동시진입 종목 수 / 종목당 투자금 *2개 전용*.

GET  /api/runtime-config → 실효값 + 출처(override/env) + 마지막 변경 시각(KST)
PUT  /api/runtime-config → 검증 통과 시 저장, "저장 후 다시 읽은 실효값" 반환

안전: 주문 경로 / 안전 플래그 미접촉. 본 모듈은 runtime_config(config 모듈)만 호출.
변경 가능한 값은 max_concurrent_positions, per_stock_budget *둘 뿐*.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.core.runtime_config import (
    RuntimeConfigValidationError,
    get_runtime_config,
    set_runtime_overrides,
)
from app.db.session import get_db

router = APIRouter(tags=["runtime-config"])


class _RuntimeConfigBody(BaseModel):
    # 둘 다 optional — 하나만 바꿔도 됨. 서버에서 범위 검증(프론트 검증 불충분).
    max_concurrent_positions: int | None = Field(None)
    per_stock_budget:         int | None = Field(None)


@router.get("/runtime-config")
def get_runtime_config_endpoint() -> dict:
    return get_runtime_config()


@router.put("/runtime-config")
def put_runtime_config_endpoint(
    body: _RuntimeConfigBody,
    db: Session = Depends(get_db),
) -> dict:
    if body.max_concurrent_positions is None and body.per_stock_budget is None:
        raise HTTPException(
            status_code=400,
            detail="바꿀 값을 하나 이상 보내주세요 (동시진입 종목 수 또는 종목당 투자금).",
        )
    try:
        result = set_runtime_overrides(
            max_concurrent_positions=body.max_concurrent_positions,
            per_stock_budget=body.per_stock_budget,
        )
    except RuntimeConfigValidationError as exc:
        # 일상 한국어 메시지 그대로 400 으로 전달.
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    # R2: 변경 이력을 활동 피드("오늘 AI가 한 일")에 기록 — best-effort.
    try:
        from app.core.runtime_config_activity import record_runtime_config_changes
        record_runtime_config_changes(db, result.get("changes") or [])
    except Exception:  # noqa: BLE001 — 기록 실패가 저장을 무효화하지 않는다.
        pass

    return result

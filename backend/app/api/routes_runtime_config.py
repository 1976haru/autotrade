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

from app.core.config import get_settings
from app.core.runtime_config import (
    VALID_PROFILES,
    RuntimeConfigValidationError,
    effective_active_profile,
    get_runtime_config,
    set_runtime_overrides,
)
from app.db.session import get_db

router = APIRouter(tags=["runtime-config"])


class _RuntimeConfigBody(BaseModel):
    # 둘 다 optional — 하나만 바꿔도 됨. 서버에서 범위 검증(프론트 검증 불충분).
    max_concurrent_positions: int | None = Field(None)
    per_stock_budget:         int | None = Field(None)


class _ProfileBody(BaseModel):
    profile: str


def _profile_effective(profile: str) -> dict:
    """프리셋 → *클램프 적용 후* 실효값(U3 원칙: 프리셋 원값 표시 금지).

    effective_min_confidence = max(council 프리셋 임계, config floor) — e158704 클램프.
    어떤 프리셋도 config floor 밑으로 내려가지 못한다.
    """
    from app.agents.agent_council import _PROFILE_THRESHOLDS
    from app.agents.risk_profile import RiskProfile
    rp = RiskProfile(profile.upper())
    thr = _PROFILE_THRESHOLDS[rp]
    floor = float(getattr(get_settings(), "kis_paper_auto_min_confidence", 0.6))
    return {
        "profile":                 profile,
        "preset_min_confidence":   round(float(thr["min_confidence"]), 2),
        "config_floor":            round(floor, 2),
        "effective_min_confidence": round(max(float(thr["min_confidence"]), floor), 2),
        "max_risk_flags":          int(thr["max_risk_flags"]),
    }


def _bot_is_running() -> bool:
    """봇 실행 여부 — 기존 Auto Paper Loop 상태 소스 재사용(새 상태 추적 0)."""
    try:
        from app.auto_paper.loop import get_auto_paper_loop
        return str(get_auto_paper_loop().status().state).upper() == "RUNNING"
    except Exception:  # noqa: BLE001 — 상태 조회 실패는 보수적으로 '안 돎' 취급.
        return False


@router.get("/runtime-config")
def get_runtime_config_endpoint() -> dict:
    cfg = get_runtime_config()
    cfg["active_profile"]["effective"] = _profile_effective(effective_active_profile())
    return cfg


@router.put("/runtime-config/profile")
def put_runtime_profile_endpoint(body: _ProfileBody, db: Session = Depends(get_db)) -> dict:
    profile = (body.profile or "").strip().lower()
    if profile not in VALID_PROFILES:
        raise HTTPException(status_code=400, detail="운용 성향은 보수/안정/공격 중 하나여야 해요.")
    # ★게이트: 봇 실행 중이면 변경 금지(정지 상태에서만).
    if _bot_is_running():
        raise HTTPException(status_code=409, detail="자동매매를 먼저 멈춘 뒤 바꿔주세요.")

    before = effective_active_profile()
    try:
        result = set_runtime_overrides(active_profile=profile)
    except RuntimeConfigValidationError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    if before != profile:
        try:
            from app.core.runtime_config_activity import record_runtime_config_changes
            record_runtime_config_changes(db, result.get("changes") or [])
        except Exception:  # noqa: BLE001
            pass

    result["profile_effective"] = _profile_effective(profile)
    return result


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

from datetime import datetime

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel, field_validator
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.deps import get_broker, get_risk_manager
from app.brokers.base import BrokerAdapter
from app.core.config import get_settings
from app.core.modes import OperationMode
from app.db.models import EmergencyStopEvent
from app.db.session import get_db
from app.risk.ai_permission_gate import (
    AiPermissionFlags,
    build_status as build_ai_permission_status,
)
from app.risk.emergency_reasons import EMERGENCY_STOP_REASONS, EmergencyStopReason
from app.risk.emergency_stop import (
    KillSwitchLevel,
    apply_kill_switch_to_risk,
    build_status,
    compute_cancel_candidates,
    compute_liquidation_candidates,
    normalize_legacy_level,
    normalize_level,
)
from app.risk.risk_manager import RiskManager, RiskPolicy

router = APIRouter(prefix="/risk", tags=["risk"])


class EmergencyStopRequest(BaseModel):
    enabled:    bool
    decided_by: str | None = None
    note:       str | None = None
    # 153: 구조화 사유 코드. None은 legacy / 미명시 호환을 위해 허용.
    reason_code: str | None = None
    # #37: Kill Switch level (OFF/LEVEL_1/LEVEL_2/LEVEL_3). 미지정 + enabled=True
    # 는 기존 의미 보존을 위해 LEVEL_1로 매핑. enabled=False는 OFF로 강제.
    level:       str | None = None

    @field_validator("reason_code")
    @classmethod
    def _validate_reason(cls, v: str | None) -> str | None:
        if v is None:
            return v
        if v not in EMERGENCY_STOP_REASONS:
            raise ValueError(
                f"reason_code must be one of {sorted(EMERGENCY_STOP_REASONS)} or null"
            )
        return v

    @field_validator("level")
    @classmethod
    def _validate_level(cls, v: str | None) -> str | None:
        if v is None:
            return v
        try:
            KillSwitchLevel(v)
        except ValueError:
            raise ValueError(
                f"level must be one of {[lvl.value for lvl in KillSwitchLevel]} or null"
            ) from None
        return v


class EmergencyStopEventOut(BaseModel):
    id:          int
    created_at:  datetime
    enabled:     bool
    decided_by:  str | None = None
    note:        str | None = None
    reason_code: str | None = None  # 153
    # #37: 3단계 level. legacy(NULL) row는 enabled=True/False에 따라 LEVEL_1/OFF로
    # 정규화되어 응답에 노출 — 기존 history 응답 호환.
    level:       str | None = None


@router.get("/policy")
def get_policy(risk: RiskManager = Depends(get_risk_manager)) -> RiskPolicy:
    return risk.policy


@router.post("/emergency-stop")
def set_emergency_stop(
    payload: EmergencyStopRequest,
    risk:    RiskManager = Depends(get_risk_manager),
    db:      Session     = Depends(get_db),
) -> dict:
    """Toggle emergency stop and log a row to the audit trail.

    #37 추가: payload.level이 주어지면 3단계 Kill Switch 모델로 매핑된다.
    - enabled=True + level=None  → LEVEL_1 (기존 의미 보존).
    - enabled=True + level=LEVEL_2/LEVEL_3 → 해당 level.
    - enabled=False               → OFF (level 무시).
    `RiskManager.emergency_stop` boolean은 LEVEL_1+ 일 때 True로 동기화.

    No-op 토글(현재 level과 동일 요청)은 audit row를 건너뛴다 — 노이즈 회피.
    """
    # 1. 요청을 level로 정규화.
    if payload.enabled:
        target_level = normalize_level(payload.level) or KillSwitchLevel.LEVEL_1
        if target_level == KillSwitchLevel.OFF:
            target_level = KillSwitchLevel.LEVEL_1
    else:
        target_level = KillSwitchLevel.OFF

    # 2. 현재 level 추정 — risk.kill_switch_level이 없으면 emergency_stop boolean
    #    에서 legacy 매핑 (LEVEL_1 또는 OFF).
    current_level = getattr(risk, "kill_switch_level", None)
    if current_level is None:
        current_level = (
            KillSwitchLevel.LEVEL_1 if risk.emergency_stop else KillSwitchLevel.OFF
        )

    state_changed = (current_level != target_level)
    apply_kill_switch_to_risk(risk, target_level)

    if state_changed:
        db.add(EmergencyStopEvent(
            enabled=(target_level != KillSwitchLevel.OFF),
            decided_by=payload.decided_by,
            note=payload.note,
            reason_code=payload.reason_code,  # 153
            level=target_level.value,         # #37
        ))
        db.commit()

        # #64: state 변경 시 알림 발송. 본 hook은 *반드시* try/except 로 감싸서
        # 알림 실패가 emergency_stop 응답을 망가뜨리지 않게 한다 (절대 원칙 #7).
        # send도 raise하지 않지만 방어용 한 겹 더.
        try:
            from app.notifications import build_emergency_stop_event
            from app.notifications.service import build_service_from_settings
            service = build_service_from_settings(get_settings())
            event = build_emergency_stop_event(
                enabled=(target_level != KillSwitchLevel.OFF),
                level=target_level.value,
                reason_code=payload.reason_code,
                decided_by=payload.decided_by,
                note=payload.note,
            )
            service.notify(event)
        except Exception:  # noqa: BLE001 — notification must never affect response
            pass

        # #68: 통합 audit_event 추가. 본 hook은 *반드시* try/except로 감싸
        # 감사 facade 실패가 emergency_stop 응답을 깨지 않게 한다 (절대 원칙 #7).
        # SecretLeakError는 payload(note/reason_code)에 Secret 패턴이 우연히
        # 끼었을 때만 발생 — fail-closed로 catch.
        try:
            from app.audit.events import (
                build_emergency_stop_event as build_emergency_audit_event,
                log_audit_event,
            )
            audit_input = build_emergency_audit_event(
                enabled=(target_level != KillSwitchLevel.OFF),
                level=target_level.value,
                reason_code=payload.reason_code,
                decided_by=payload.decided_by,
                note=payload.note,
            )
            log_audit_event(
                db,
                event_type=audit_input.event_type,
                summary=audit_input.summary,
                severity=audit_input.severity,
                source=audit_input.source,
                actor=audit_input.actor,
                reason=audit_input.reason,
                details=audit_input.details,
            )
        except Exception:  # noqa: BLE001 — audit hook must never affect response
            pass

    return {
        "emergency_stop": risk.emergency_stop,
        "level":          target_level.value,
    }


@router.get("/emergency-stop/reasons")
def emergency_stop_reasons() -> list[str]:
    """153: 허용되는 reason_code 목록 — frontend가 dropdown 생성 시 사용."""
    return [r.value for r in EmergencyStopReason]


@router.get("/emergency-stop/history", response_model=list[EmergencyStopEventOut])
def emergency_stop_history(
    limit:  int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
    db:     Session = Depends(get_db),
) -> list[EmergencyStopEventOut]:
    """Most recent emergency-stop toggles first.

    The runtime flag is in-memory and resets to OFF on restart by design
    (operators must explicitly re-assert), but the audit trail persists so
    "who toggled when, and why" survives across restarts.
    """
    rows = db.execute(
        select(EmergencyStopEvent)
        .order_by(EmergencyStopEvent.id.desc())
        .limit(limit).offset(offset)
    ).scalars().all()
    return [
        EmergencyStopEventOut(
            id=r.id, created_at=r.created_at, enabled=r.enabled,
            decided_by=r.decided_by, note=r.note,
            reason_code=r.reason_code,  # 153
            # #37: legacy NULL row는 enabled=True/False에 따라 LEVEL_1/OFF로
            # 정규화되어 응답 — 기존 history client 호환.
            level=normalize_legacy_level(
                getattr(r, "level", None), enabled=bool(r.enabled),
            ).value,
        )
        for r in rows
    ]


@router.get("/emergency-stop/summary")
def emergency_stop_summary(
    risk: RiskManager = Depends(get_risk_manager),
    db:   Session = Depends(get_db),
) -> dict:
    """208: emergency-stop 집계.

    - `currently_active`: 런타임 in-memory flag.
    - `active_since`: 가장 최근 enabled=True row의 created_at (active일 때만 의미).
    - `by_reason`: {reason_code | "(none)": count} — enabled=True row만 집계
      해서 "어떤 사유로 가장 자주 stop이 켜졌나"를 보여준다. enabled=False
      (해제) row는 사유로 분리하지 않는다.
    - `total_toggles`: 전체 row 수 (on/off 모두).
    - `total_activations`: enabled=True row 수.

    CLAUDE.md 절대 원칙 준수: read-only, 가드 / 결정에 영향 X.
    """
    from sqlalchemy import func
    by_reason: dict[str, int] = {}
    rows = db.execute(
        select(EmergencyStopEvent.reason_code, func.count(EmergencyStopEvent.id))
        .where(EmergencyStopEvent.enabled.is_(True))
        .group_by(EmergencyStopEvent.reason_code)
    ).all()
    for reason, n in rows:
        key = reason if reason is not None else "(none)"
        by_reason[key] = int(n or 0)
    total_toggles = db.execute(
        select(func.count(EmergencyStopEvent.id))
    ).scalar_one() or 0
    total_activations = sum(by_reason.values())

    active_since = None
    if risk.emergency_stop:
        latest_active = db.execute(
            select(EmergencyStopEvent)
            .where(EmergencyStopEvent.enabled.is_(True))
            .order_by(EmergencyStopEvent.id.desc()).limit(1)
        ).scalar_one_or_none()
        if latest_active is not None and latest_active.created_at is not None:
            active_since = latest_active.created_at.isoformat()

    return {
        "currently_active":   bool(risk.emergency_stop),
        "active_since":       active_since,
        "by_reason":          by_reason,
        "total_toggles":      int(total_toggles),
        "total_activations":  total_activations,
    }


# ====================================================================
# #37: 3-level Kill Switch — read-only status + candidate endpoints
# ====================================================================
#
# 본 endpoint들은 *주문을 만들지 않는다*. broker.cancel_order /
# broker.place_order / route_order 어떤 함수도 호출하지 않으며 candidate
# list만 read-only로 surface — 실제 취소 / 청산은 운영자 수동 승인이
# 별도 phase로 도입된다 (CLAUDE.md '손실 방어 우선' + 호가 공백 / 급락 시
# 자동 시장가 전량청산 위험성).


@router.get("/emergency-stop/status")
def emergency_stop_status(
    risk: RiskManager  = Depends(get_risk_manager),
    db:   Session      = Depends(get_db),
) -> dict:
    """현재 Kill Switch level + 후보 카운트.

    Frontend 3단계 UI가 이 endpoint로 한 번에 status + 카운트를 가져간다.
    무거운 broker 호출은 본 endpoint에서 *수행하지 않음* — liquidation_candidate
    _count는 cancel candidate(DB 기반)만 carry. broker 기반 청산 후보는
    별도 endpoint에서 명시 호출.
    """
    cancel_candidates = compute_cancel_candidates(db)
    status = build_status(
        risk=risk, db=db,
        cancel_candidates=cancel_candidates,
        liquidation_candidates=None,  # status는 broker 호출 회피 — 별도 endpoint에서
    )
    return status.to_dict()


@router.get("/emergency-stop/cancel-candidates")
def emergency_stop_cancel_candidates(
    db: Session = Depends(get_db),
) -> dict:
    """LEVEL_2가 표시할 미체결 / 승인 대기 주문 후보 list.

    Read-only. PendingApproval(PENDING) + OrderAuditLog(NEEDS_APPROVAL drift)
    를 합쳐 반환. 실제 취소는 본 endpoint가 *하지 않는다* — 운영자가 후보를
    보고 별도 cancel API로 수동 승인.
    """
    candidates = compute_cancel_candidates(db)
    return {
        "candidates": [c.to_dict() for c in candidates],
        "count":      len(candidates),
        "note": (
            "본 endpoint는 read-only candidate list만 반환합니다. "
            "실제 주문 취소는 운영자 수동 승인 흐름에서만 진행됩니다."
        ),
    }


@router.get("/emergency-stop/liquidation-candidates")
async def emergency_stop_liquidation_candidates(
    broker: BrokerAdapter = Depends(get_broker),
) -> dict:
    """LEVEL_3가 표시할 청산 후보 list (현재 보유 포지션).

    **자동 청산 금지** — 본 endpoint는 read-only candidate만 반환. 운영자가
    호가 / 시장 상황을 확인하고 *수동 승인 후* 청산. CLAUDE.md '손실 방어
    우선' + 호가 공백 / 급락 시 시장가 전량청산 위험.
    """
    positions = await broker.get_positions()
    candidates = compute_liquidation_candidates(positions)
    total_unrealized = sum(c.unrealized_pnl for c in candidates)
    return {
        "candidates":         [c.to_dict() for c in candidates],
        "count":              len(candidates),
        "total_unrealized_pnl": total_unrealized,
        "note": (
            "자동 청산은 비활성화되어 있습니다. 청산은 운영자가 후보를 확인한 뒤 "
            "수동 승인으로 진행해야 합니다."
        ),
    }


# ====================================================================
# #39: AI Permission Gate — read-only status surface
# ====================================================================
#
# UI / Agent / audit가 "현재 mode에서 AI에게 어떤 행동이 허용되는지" 한 곳에서
# 조회. 본 endpoint는 *판정 결과 표시*만 한다 — 권한 행사 / 주문 흐름 변경 X.
# 기존 RiskManager의 disable_ai_orders / min_ai_confidence / enforce_ai_reasoning
# / can_ai_execute 검사들은 그대로 유지된다.


@router.get("/ai-permission/status")
def ai_permission_status(
    risk: RiskManager = Depends(get_risk_manager),
) -> dict:
    """현재 AI 권한 상태 + 매트릭스. read-only.

    응답:
    - `mode`: 현재 운용모드.
    - `level`: AiPermissionLevel (FULL_STOP / RECOMMEND_ONLY / APPROVAL_REQUIRED
      / VIRTUAL_EXECUTION / LIMITED_LIVE_EXECUTION).
    - `allowed_actions` / `blocked_actions`: 현재 level에서 허용/차단되는
      AiAction 목록.
    - `requires_human_approval` / `virtual_only` / `live_execution_disabled` /
      `futures_live_disabled`: UI 배지 boolean.
    - `flags`: 현재 적용 중인 5개 안전 flag.
    - `matrix`: mode × action default 허용 매트릭스 (dashboard 표시용).
    - `notice`: "AI API Key는 주문 권한이 아닙니다" 안내 문구.
    """
    settings = get_settings()
    flags = AiPermissionFlags(
        enable_live_trading=settings.enable_live_trading,
        enable_ai_execution=settings.enable_ai_execution,
        enable_futures_live_trading=settings.enable_futures_live_trading,
        emergency_stop=bool(getattr(risk, "emergency_stop", False)),
        disable_ai_orders=bool(getattr(risk.policy, "disable_ai_orders", False)),
    )
    mode = OperationMode(settings.default_mode)
    return build_ai_permission_status(mode=mode, flags=flags)

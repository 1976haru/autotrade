from fastapi import APIRouter

from app.core.config import get_settings
from app.core.modes import MODE_CAPABILITIES
from app.db.migration_runner import db_is_ready, get_migration_status

router = APIRouter(prefix="/status", tags=["status"])


@router.get("")
def get_status() -> dict:
    settings = get_settings()
    mig = get_migration_status()
    return {
        "app": settings.app_name,
        "env": settings.app_env,
        "default_mode": settings.default_mode,
        "enable_live_trading": settings.enable_live_trading,
        "enable_ai_execution": settings.enable_ai_execution,
        "mode_capabilities": MODE_CAPABILITIES[settings.default_mode],
        # 201: 전체 safety flag 매트릭스 — frontend SafetyFlagsCard에서 한 번에
        # 보여 운영자가 런타임이 어느 모드인지 즉시 파악할 수 있도록 한다.
        # 모든 값은 read-only로 백엔드 환경변수의 라이브 스냅샷이다.
        "safety_flags": {
            "default_mode":                settings.default_mode.value,
            "enable_live_trading":         settings.enable_live_trading,
            "enable_ai_execution":         settings.enable_ai_execution,
            "enable_futures_live_trading": settings.enable_futures_live_trading,
            "kis_is_paper":                settings.kis_is_paper,
            "market_data_provider":        settings.market_data_provider,
            "enable_fill_polling":         settings.enable_fill_polling,
            "stale_price_max_age_seconds": settings.stale_price_max_age_seconds,
        },
        # fix/desktop-nonblocking-migration-health: DB readiness + migration
        # phase 정보. frontend launcher 가 `db_ready=false` 면 "백엔드 offline"
        # 으로 오인하지 않고 "초기 DB 준비 중" UI 를 그리도록 carry.
        # Secret 노출 0건 — error_summary 는 redact + 200 char truncate 된 1줄.
        # 전체 traceback 은 `backend-YYYYMMDD.log` 에만 존재.
        "db_ready":                    db_is_ready(),
        "migration_status":            mig.state.value,
        "migration_started_at":        mig.started_at,
        "migration_completed_at":      mig.completed_at,
        "migration_duration_seconds":  mig.duration_seconds,
        "migration_error_type":        mig.error_type,
        "migration_error_summary":     mig.error_summary,
    }


@router.get("/live-safety")
def get_live_safety_status() -> dict:
    """#70/#71/#72: 실매매 기본 OFF + KIS Paper/Live 분리 + Live Capital Review 상태.

    read-only — broker / OrderExecutor / route_order 호출 0건, DB write 0건,
    secret/계좌번호 원문 0건. is_live_authorization/broker_order_sent/order_created
    항상 False. 어떤 flag 하나로도 실전 주문은 허용되지 않는다.
    """
    from app.kis.endpoints import resolve_kis_endpoint
    from app.permission.live_capital_review import (
        build_live_capital_review,
    )
    from app.permission.live_manual_approval_gate import LiveManualApprovalInput
    from app.permission.live_trading_off_policy import evaluate_live_off_policy

    settings = get_settings()

    live_policy = evaluate_live_off_policy(
        enable_live_trading=bool(settings.enable_live_trading),
        enable_ai_execution=bool(settings.enable_ai_execution),
        enable_futures_live_trading=bool(settings.enable_futures_live_trading),
        kis_is_paper=bool(settings.kis_is_paper),
        default_mode=str(settings.default_mode.value),
    )
    # endpoint 선택 — explicit live gate 는 *주입 안 함* (기본 차단 상태 표시).
    kis_endpoint = resolve_kis_endpoint(
        kis_is_paper=bool(settings.kis_is_paper),
        explicit_live_gate_passed=False,
    )
    # 현재(기본)에는 운영자 승인 입력이 없으므로 review 는 MISSING 으로 표시.
    review = build_live_capital_review(LiveManualApprovalInput(
        mode=settings.default_mode,
        kis_is_paper=bool(settings.kis_is_paper),
        enable_live_trading=bool(settings.enable_live_trading),
        enable_ai_execution=bool(settings.enable_ai_execution),
    ))
    return {
        "live_policy":          live_policy.to_dict(),
        "kis_endpoint":         kis_endpoint.to_dict(),
        "live_capital_review":  review.to_dict(),
        "is_live_authorization": False,
        "is_order_signal":       False,
        "advisory_note": (
            "실전매매 기본 OFF. KIS Paper/Live 경로 분리. Live Capital Review 는 주문 "
            "승인이 아닙니다. 현재 실전 주문은 차단 상태입니다."
        ),
    }

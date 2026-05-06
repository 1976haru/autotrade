from fastapi import APIRouter

from app.core.config import get_settings
from app.core.modes import MODE_CAPABILITIES

router = APIRouter(prefix="/status", tags=["status"])


@router.get("")
def get_status() -> dict:
    settings = get_settings()
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
    }

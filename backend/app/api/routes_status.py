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
    }

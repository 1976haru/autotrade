"""테마별 신규 진입 필터 런타임 API."""
from __future__ import annotations

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from app.core.runtime_config import (
    RuntimeConfigValidationError,
    set_theme_enabled,
)
from app.theme_filter.service import theme_filter_status


router = APIRouter(prefix="/theme-filter", tags=["theme-filter"])


class ThemeToggleBody(BaseModel):
    enabled: bool
    duration: str | None = None


@router.get("")
def get_theme_filter() -> dict:
    return theme_filter_status()


@router.patch("/{theme_id}")
def patch_theme_filter(theme_id: str, body: ThemeToggleBody) -> dict:
    try:
        set_theme_enabled(
            theme_id,
            enabled=body.enabled,
            duration=body.duration,
        )
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="등록되지 않은 테마입니다.") from exc
    except RuntimeConfigValidationError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return theme_filter_status()

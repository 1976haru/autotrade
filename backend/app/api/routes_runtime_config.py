"""Read and update runtime settings and report the current PAPER entry ceilings."""

from __future__ import annotations

from fastapi import APIRouter, Depends, Header, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.core.runtime_config import (
    TOTAL_PAPER_BUDGET_MAX,
    TOTAL_PAPER_BUDGET_MIN,
    VALID_PROFILES,
    RuntimeConfigValidationError,
    effective_active_profile,
    get_runtime_config,
    set_runtime_overrides,
)
from app.db.session import get_db
from app.kis_paper.one_click_session import account_fingerprint, entry_budget_caps
from app.kis_paper.order_limits import resolve_kis_paper_order_limits

router = APIRouter(tags=["runtime-config"])


class _RuntimeConfigBody(BaseModel):
    # 모두 optional — 하나만 바꿔도 됨. 서버에서 범위 검증(프론트 검증 불충분).
    max_concurrent_positions: int | None = Field(None)
    per_stock_budget:         int | None = Field(None)
    # C1(2026-06-10): 손절/익절 % (양수 magnitude — 손절 2.0=−2%).
    stop_loss_pct:            float | None = Field(None)
    take_profit_pct:          float | None = Field(None)
    # T2(2026-06-12): 일일 매수금액 한도(원) — 종목수·투자금과 동일 메커니즘.
    daily_buy_limit_krw:      int | None = Field(None)
    total_paper_budget_krw:  int | None = Field(None, ge=TOTAL_PAPER_BUDGET_MIN, le=TOTAL_PAPER_BUDGET_MAX)
    # 종목 풀 확장 — 단계(100/200/400) + 소스(auto/watchlist). cap 은 미포함(고정).
    universe_size:            int | None = Field(None)
    universe_mode:            str | None = Field(None)


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


def _with_paper_entry_limits(cfg: dict, db: Session) -> dict:
    settings = get_settings()
    limits = resolve_kis_paper_order_limits(settings)
    approved_daily, approved_total = entry_budget_caps(
        db, fingerprint=account_fingerprint(settings.kis_account_no, settings.kis_product_code),
    )
    cfg["paper_entry_limits"] = {
        "daily_buy_krw": min(limits.daily_buy_krw, approved_daily),
        "total_budget_krw": min(limits.total_budget_krw, approved_total),
        "max_order_krw": limits.max_order_krw,
        "max_positions": limits.max_positions,
        "daily_buy_count": limits.daily_buy_count,
    }
    return cfg


@router.get("/runtime-config")
def get_runtime_config_endpoint(db: Session = Depends(get_db)) -> dict:
    cfg = get_runtime_config()
    cfg["active_profile"]["effective"] = _profile_effective(effective_active_profile())
    # 모든 성향의 *클램프 적용 후* 실효값 — 프론트 전환 다이얼로그가 대상 성향을
    #   미리 정직하게 보여줄 수 있게(프리셋 원값 표시 금지, U3).
    cfg["profiles_effective"] = {p: _profile_effective(p) for p in VALID_PROFILES}
    return _with_paper_entry_limits(cfg, db)


@router.put("/runtime-config/profile")
def put_runtime_profile_endpoint(body: _ProfileBody, db: Session = Depends(get_db),
                                 x_event_source: str = Header("operator")) -> dict:
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
            record_runtime_config_changes(db, result.get("changes") or [], source=x_event_source)
        except Exception:  # noqa: BLE001
            pass

    result["profile_effective"] = _profile_effective(profile)
    return result


@router.put("/runtime-config")
def put_runtime_config_endpoint(
    body: _RuntimeConfigBody,
    db: Session = Depends(get_db),
    x_event_source: str = Header("operator"),
) -> dict:
    if (body.max_concurrent_positions is None and body.per_stock_budget is None
            and body.stop_loss_pct is None and body.take_profit_pct is None
            and body.daily_buy_limit_krw is None
            and body.total_paper_budget_krw is None
            and body.universe_size is None and body.universe_mode is None):
        raise HTTPException(
            status_code=400,
            detail="바꿀 값을 하나 이상 보내주세요 (동시진입 종목 수 / 종목당 투자금 / 손절 / 익절 / 일일 매수 한도 / 종목 풀 / 유니버스 소스).",
        )
    try:
        result = set_runtime_overrides(
            max_concurrent_positions=body.max_concurrent_positions,
            per_stock_budget=body.per_stock_budget,
            stop_loss_pct=body.stop_loss_pct,
            take_profit_pct=body.take_profit_pct,
            daily_buy_limit_krw=body.daily_buy_limit_krw,
            total_paper_budget_krw=body.total_paper_budget_krw,
            universe_size=body.universe_size,
            universe_mode=body.universe_mode,
        )
    except RuntimeConfigValidationError as exc:
        # 일상 한국어 메시지 그대로 400 으로 전달.
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    # R2: 변경 이력을 활동 피드("오늘 AI가 한 일")에 기록 — best-effort.
    try:
        from app.core.runtime_config_activity import record_runtime_config_changes
        record_runtime_config_changes(db, result.get("changes") or [], source=x_event_source)
    except Exception:  # noqa: BLE001 — 기록 실패가 저장을 무효화하지 않는다.
        pass

    return _with_paper_entry_limits(result, db)

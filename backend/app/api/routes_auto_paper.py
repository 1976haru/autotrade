"""AI Paper Auto Loop API + Desktop health.

EXE 의 시작/정지/긴급정지 3 버튼이 호출하는 endpoints + desktop launcher 가
polling 하는 health. PAPER/SIMULATION 한정 — live broker / OrderExecutor /
route_order import 0건.

응답은 Secret / API key / 계좌번호 0건. 안전 flag 라벨만 carry.

feat/step2-05-pre-market-gate: `POST /api/auto-paper/start` 는 optional
body `{ pre_market: { start_allowed, verdict, blocking_reasons, warnings } }`
를 받아 `start_allowed=False` 면 409 + blocking_reasons 로 차단. body 가
없으면 (legacy compat) 게이트 건너뜀 — frontend 는 항상 동봉.
"""

from __future__ import annotations

from typing import Optional

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from app.auto_paper.loop import (
    LoopAlreadyRunningError,
    LoopBlockedError,
    LoopNotRunningError,
    LoopPreMarketBlockedError,
    PreMarketSummary,
    get_auto_paper_loop,
)
from app.auto_paper.ledger import get_ledger
from app.auto_paper.events import DecisionAction
from app.auto_paper.decisions import (
    AIRecommendationInput,
    process_ai_recommendation,
)
from app.auto_paper.capital_config import (
    ALLOWED_MAX_CONCURRENT_POSITIONS,
    ALLOWED_PAPER_INITIAL_CASH,
    ALLOWED_PER_SYMBOL_MAX_KRW,
    ALLOWED_PER_SYMBOL_MAX_PCT,
    InvalidMaxConcurrentPositionsError,
    InvalidPaperCapitalError,
    InvalidPerSymbolAllocationError,
    PerSymbolAllocationMode,
    get_paper_capital_config,
    set_max_concurrent_positions,
    set_paper_capital_config,
    set_per_symbol_allocation,
)
from app.auto_paper.concurrent_positions_guard import (
    check_concurrent_buy_allowed,
)
from app.auto_paper.affordability_check import (
    check_paper_affordability,
)
from app.auto_paper.min_lot_check import (
    compute_paper_affordable_lot,
    validate_paper_min_lot,
)
from app.auto_paper.affordability import (
    DEFAULT_HIGH_PRICE_POLICY,
    HighPricePolicy,
    evaluate_high_price,
)
from app.auto_paper.capital_state import (
    check_buy_cash_sufficient,
    get_capital_state,
)
from app.agents.risk_profile import (
    DEFAULT_RISK_PROFILE,
    RiskProfile,
    capital_allocation_for,
    list_capital_allocations,
)
from app.risk.loss_limits import (
    DEFAULT_DAILY_BUY_LIMIT_KRW,
    check_daily_buy_limit,
)
from app.risk.position_limits import (
    DEFAULT_MAX_SYMBOL_WEIGHT_PCT,
    check_symbol_weight_limit,
)
from app.auto_paper.capital_config import (
    resolve_daily_buy_limit,
    resolve_symbol_weight_limit_pct,
)
from app.auto_paper.position_sizer import (
    QuantityByPriceVerdict,
    compute_paper_quantity_by_price,
)
from app.core.config import get_settings


router = APIRouter(tags=["auto-paper"])


@router.get("/desktop/health")
def desktop_health() -> dict:
    """EXE launcher 가 connectivity 확인용으로 호출. Secret 0건."""
    settings = get_settings()
    loop = get_auto_paper_loop()
    return {
        "ok": True,
        "app": settings.app_name,
        "env": settings.app_env,
        "default_mode": settings.default_mode.value,
        "safety_flags": {
            "enable_live_trading":         settings.enable_live_trading,
            "enable_ai_execution":         settings.enable_ai_execution,
            "enable_futures_live_trading": settings.enable_futures_live_trading,
            "kis_is_paper":                settings.kis_is_paper,
        },
        "auto_paper": loop.status().to_dict(),
        "advisory_only": True,
    }


_AP = APIRouter(prefix="/auto-paper", tags=["auto-paper"])


# ─────────────────────────────────────────────────────────────────────
# Pre-market gate payload schema
# ─────────────────────────────────────────────────────────────────────


class _PreMarketBody(BaseModel):
    """Pre-market checklist 결과의 compact carry — frontend → start 호출.

    full `PreMarketCheckResult` 의 부분집합. `app.governance.pre_market_check`
    와 결합도 분리.
    """
    start_allowed:    bool       = Field(..., description="False 면 start() 차단")
    verdict:          str        = Field(
        default="",
        description="READY_TO_START / WARN_BUT_START_ALLOWED / DO_NOT_START",
    )
    blocking_reasons: list[str]  = Field(default_factory=list)
    warnings:         list[str]  = Field(default_factory=list)


class _StartBody(BaseModel):
    """`POST /auto-paper/start` body. 모두 optional — body 없이도 호출 가능."""
    pre_market: Optional[_PreMarketBody] = None


@_AP.get("/status")
def get_status() -> dict:
    return get_auto_paper_loop().status().to_dict()


@_AP.post("/start")
def post_start(body: _StartBody | None = None) -> dict:
    """자동 시작.

    feat/step2-05-pre-market-gate: `body.pre_market.start_allowed=False` 면
    `409 Conflict` + detail.blocking_reasons 로 차단. blocking_reasons 는
    Secret 0건 (pre_market_check 모듈이 라벨만 emit). frontend 가 표시.
    """
    loop = get_auto_paper_loop()
    pm: PreMarketSummary | None = None
    if body is not None and body.pre_market is not None:
        pm = PreMarketSummary(
            start_allowed=body.pre_market.start_allowed,
            verdict=body.pre_market.verdict,
            blocking_reasons=list(body.pre_market.blocking_reasons),
            warnings=list(body.pre_market.warnings),
        )
    try:
        snap = loop.start(pre_market=pm)
    except LoopPreMarketBlockedError as e:
        # Pre-market BLOCK — 차단 사유 구조화 응답.
        raise HTTPException(
            status_code=409,
            detail={
                "error":            "pre_market_blocked",
                "message":          str(e),
                "verdict":          e.verdict,
                "blocking_reasons": e.blocking_reasons,
            },
        )
    except LoopAlreadyRunningError as e:
        raise HTTPException(status_code=409, detail=str(e))
    except LoopBlockedError as e:
        # EMERGENCY_STOP 상태에서 start() 차단 — 운영자가 reset() 호출 후
        # 재시도해야 함. 409 Conflict 로 표현 (이미 다른 상태에 잠겨 있음).
        raise HTTPException(status_code=409, detail=str(e))
    return snap.to_dict()


@_AP.post("/stop")
def post_stop() -> dict:
    loop = get_auto_paper_loop()
    try:
        snap = loop.stop()
    except LoopNotRunningError as e:
        raise HTTPException(status_code=409, detail=str(e))
    return snap.to_dict()


@_AP.post("/emergency-stop")
def post_emergency_stop() -> dict:
    return get_auto_paper_loop().emergency_stop().to_dict()


@_AP.post("/reset")
def post_reset() -> dict:
    return get_auto_paper_loop().reset().to_dict()


# ─────────────────────────────────────────────────────────────────────────────
# #2-09: Paper Auto Loop ledger (read-only)
# ─────────────────────────────────────────────────────────────────────────────


def _serialize_ledger_response(
    *,
    limit:    int            = 50,
    state:    str | None     = None,
    strategy: str | None     = None,
    symbol:   str | None     = None,
    action:   str | None     = None,
) -> dict:
    """단일 직렬화 — `/ledger` 와 `/events` alias 가 공유."""
    ledger = get_ledger()
    # decision_action filter — enum 값 검증.
    action_enum: DecisionAction | None = None
    if action is not None:
        try:
            action_enum = DecisionAction(action.upper())
        except ValueError:
            raise HTTPException(
                status_code=400,
                detail=f"invalid decision_action: {action!r}",
            )
    if any(v is not None for v in (state, strategy, symbol, action_enum)):
        events = ledger.filter_by(
            loop_state=state, strategy=strategy, symbol=symbol,
            decision_action=action_enum,
        )
        events = events[-max(1, int(limit)):]
    else:
        events = ledger.recent(limit=max(1, int(limit)))
    return {
        "is_order_signal":        False,
        "auto_apply_allowed":     False,
        "is_live_authorization":  False,
        "advisory_disclaimer": (
            "Paper Auto Loop 의 advisory ledger — Paper 가상 체결 / AI 판단만 "
            "기록. 실 broker 호출 0건."
        ),
        "events":      [e.to_dict() for e in events],
        "event_count": len(events),
        "stats":       ledger.stats(),
        "filters": {
            "limit":    int(limit),
            "state":    state,
            "strategy": strategy,
            "symbol":   symbol,
            "action":   action,
        },
    }


@_AP.get("/ledger")
def get_ledger_endpoint(
    limit:    int            = 50,
    state:    str | None     = None,
    strategy: str | None     = None,
    symbol:   str | None     = None,
    action:   str | None     = None,
) -> dict:
    """Paper Auto Loop ledger — 최근 event read-only.

    응답 invariant: `is_order_signal=False` / `auto_apply_allowed=False` /
    `is_live_authorization=False` carry. Secret / API key / 계좌번호 필드 0건.
    """
    return _serialize_ledger_response(
        limit=limit, state=state, strategy=strategy, symbol=symbol, action=action,
    )


@_AP.get("/events")
def get_events_endpoint(
    limit:    int            = 50,
    state:    str | None     = None,
    strategy: str | None     = None,
    symbol:   str | None     = None,
    action:   str | None     = None,
) -> dict:
    """ledger alias — 운영자 친화 두 번째 경로 (`/events`)."""
    return _serialize_ledger_response(
        limit=limit, state=state, strategy=strategy, symbol=symbol, action=action,
    )


# ─────────────────────────────────────────────────────────────────────────────
# #2-10: AI Paper 자동매수/매도 skeleton — tick + decision/latest
# ─────────────────────────────────────────────────────────────────────────────


class _AIRecommendationBody(BaseModel):
    """단일 AI advisory recommendation 입력 (paper-only — broker 호출 0건)."""
    strategy:         str
    symbol:           str
    direction:        str                         # "BUY" / "SELL" / "EXIT" / "HOLD" / "NO_OP"
    reason:           str
    confidence:       Optional[float]             = None
    risk_flags:       Optional[list[str]]         = None
    params:           Optional[dict]              = None
    current_position: int                         = 0
    # 캘러가 metadata 에 secret 패턴 넣으면 ledger 가 거부 (SecretInLedgerError → 400).
    metadata:         Optional[dict]              = None


class _TickBody(BaseModel):
    """`POST /tick` 입력 — N 개 recommendation 일괄 처리."""
    recommendations:    list[_AIRecommendationBody]
    virtual_trade_size: int                         = 1
    auto_fill:          bool                        = True


@_AP.post("/tick")
def post_tick(body: _TickBody) -> dict:
    """AI advisory recommendation 일괄 처리 → Paper ledger 기록.

    *Paper 전용* — 실 broker 호출 0건. 본 endpoint 가 호출하는 모든 흐름:
    - `convert_to_paper_decision()` — dataclass 변환만
    - `record_paper_event()` — in-memory ledger append

    loop_state 는 항상 현재 loop 의 state 를 사용 (caller 가 별도 주입 불가) —
    `RUNNING` 이 아닐 때 BUY/SELL/EXIT 시도 시 ledger 가 거부 (`LedgerStateError`
    → 409).
    """
    loop = get_auto_paper_loop()
    state = loop.status().state

    decisions_out: list[dict] = []
    errors_out:    list[dict] = []
    for r in body.recommendations:
        try:
            rec = AIRecommendationInput(
                strategy=r.strategy,
                symbol=r.symbol,
                direction=r.direction,
                reason=r.reason,
                confidence=r.confidence,
                risk_flags=list(r.risk_flags or []),
                params=dict(r.params or {}),
                current_position=int(r.current_position),
                metadata=dict(r.metadata or {}),
            )
        except ValueError as e:
            errors_out.append({
                "strategy":   r.strategy,
                "symbol":     r.symbol,
                "direction":  r.direction,
                "error":      f"invalid_input: {e}",
            })
            continue
        try:
            decision, _event = process_ai_recommendation(
                rec,
                loop_state=state,
                virtual_trade_size=int(body.virtual_trade_size),
                auto_fill=bool(body.auto_fill),
                record=True,
            )
            decisions_out.append(decision.to_dict())
        except Exception as e:   # noqa: BLE001 — ledger guards (state / secret) 둘 다 포함.
            errors_out.append({
                "strategy":   r.strategy,
                "symbol":     r.symbol,
                "direction":  r.direction,
                "error":      f"{type(e).__name__}: {e}",
            })

    return {
        "is_order_signal":        False,
        "auto_apply_allowed":     False,
        "is_live_authorization":  False,
        "advisory_disclaimer": (
            "AI Paper 자동매수/매도 skeleton — Paper 가상 체결만, 실 broker 호출 0건."
        ),
        "loop_state":    state,
        "decision_count": len(decisions_out),
        "decisions":     decisions_out,
        "error_count":   len(errors_out),
        "errors":        errors_out,
    }


@_AP.get("/decision/latest")
def get_latest_decision() -> dict:
    """가장 최근 ledger event 단일 반환 — 운영자 카드용."""
    events = get_ledger().recent(limit=1)
    latest = events[-1].to_dict() if events else None
    return {
        "is_order_signal":        False,
        "auto_apply_allowed":     False,
        "is_live_authorization":  False,
        "advisory_disclaimer": (
            "최근 AI Paper 판단 — advisory, 실 broker 호출 0건."
        ),
        "has_decision": latest is not None,
        "decision":     latest,
    }


# ─────────────────────────────────────────────────────────────────────────────
# #PaperCandidateWire: 최종 Paper 후보 ↔ Auto Paper Loop 승인 endpoint.
# ─────────────────────────────────────────────────────────────────────────────


from app.auto_paper.candidate_registry import (   # noqa: E402
    ApprovalBlockedError,
    CandidateNotFoundError,
    get_candidate_registry,
)


class _ApprovalBody(BaseModel):
    approved_by:  str
    note:         Optional[str] = None


class _RejectBody(BaseModel):
    rejected_by:  str
    note:         Optional[str] = None


@_AP.get("/candidates")
def get_candidates() -> dict:
    """등록된 모든 Paper 후보 + readiness 상태 — *read-only*.

    `recommended_for_paper=True` 라도 `requires_operator_approval=True` 영구
    — 본 endpoint 가 반환하는 어떤 후보도 자동으로 사용되지 않는다.
    """
    return get_candidate_registry().to_dict()


@_AP.post("/candidates/{candidate_id}/approve-paper")
def approve_candidate(candidate_id: str, body: _ApprovalBody) -> dict:
    """후보를 *Paper* 용으로 승인 — 위험 라벨 carry 시 차단.

    승인된 후보는 active_candidate 로 사용 가능하지만 실거래로 이어지지 않는다
    — `is_live_authorization=False` 영구.
    """
    try:
        m = get_candidate_registry().approve(
            candidate_id, body.approved_by, body.note,
        )
    except CandidateNotFoundError as e:
        raise HTTPException(404, detail={
            "error": "candidate_not_found",
            "candidate_id": candidate_id,
            "message": str(e),
        })
    except ApprovalBlockedError as e:
        raise HTTPException(409, detail={
            "error": "approval_blocked_risk",
            "candidate_id": candidate_id,
            "message": str(e),
        })
    except RuntimeError as e:
        raise HTTPException(409, detail={
            "error": "approval_state_conflict",
            "candidate_id": candidate_id,
            "message": str(e),
        })
    return {
        "candidate":             m.to_dict(),
        "is_order_signal":       False,
        "auto_apply_allowed":    False,
        "is_live_authorization": False,
        "advisory_disclaimer": (
            "승인된 후보는 Paper Auto Loop input 으로 사용 가능 — 실거래 활성화는 "
            "별도 Live Manual Gate / Live Activation PR 후에만."
        ),
    }


@_AP.post("/candidates/{candidate_id}/reject")
def reject_candidate(candidate_id: str, body: _RejectBody) -> dict:
    try:
        m = get_candidate_registry().reject(
            candidate_id, body.rejected_by, body.note,
        )
    except CandidateNotFoundError as e:
        raise HTTPException(404, detail={
            "error": "candidate_not_found",
            "candidate_id": candidate_id,
            "message": str(e),
        })
    except RuntimeError as e:
        raise HTTPException(409, detail={
            "error": "reject_state_conflict",
            "candidate_id": candidate_id,
            "message": str(e),
        })
    return {
        "candidate":             m.to_dict(),
        "is_order_signal":       False,
        "auto_apply_allowed":    False,
        "is_live_authorization": False,
    }


@_AP.get("/active-candidate")
def get_active_candidate() -> dict:
    """현재 active_candidate — 없으면 has_active=False."""
    reg = get_candidate_registry()
    active = reg.active_candidate()
    return {
        "has_active":            active is not None,
        "readiness_state":       reg.readiness_state().value,
        "active":                active.to_dict() if active else None,
        "is_order_signal":       False,
        "auto_apply_allowed":    False,
        "is_live_authorization": False,
    }


# ============================================================================
# P-01: Paper 시드머니 설정 endpoints (in-memory; P-16 에서 영구화)
# ============================================================================


class _PaperCapitalBody(BaseModel):
    """`POST /auto-paper/capital-config` 입력 — 시드머니 변경.

    `fallback_to_default=False` (기본) — 허용되지 않은 값은 400 으로 거부.
    `True` — 허용되지 않은 값을 받으면 default 로 fallback 하고 응답에
    `fallback_used=True` carry (silent 가 아닌 *명시* 알림).
    """

    initial_cash:        int  = Field(..., description="허용 옵션 중 하나의 KRW 정수")
    fallback_to_default: bool = Field(False, description="True 면 비허용 값을 default 로 대체")


@_AP.get("/capital-config")
def get_capital_config_endpoint() -> dict:
    """현재 Paper 시드머니 설정 (read-only).

    P-01 시점 in-memory store — 프로세스 재시작 시 default (10,000,000) 로
    복귀. 영구 저장은 P-16 에서 별도 PR.
    """
    cfg = get_paper_capital_config()
    return {
        **cfg.to_dict(),
        # 사용자 안내 — UI 가 그대로 표시 가능.
        "notice": (
            "Paper 시드머니는 *모의매매 전용* 이며 실전 계좌와 무관합니다. "
            "어떤 broker / 실거래 API 와도 결합되지 않습니다."
        ),
    }


@_AP.post("/capital-config")
def set_capital_config_endpoint(body: _PaperCapitalBody) -> dict:
    """Paper 시드머니 설정 변경.

    허용 옵션 (`ALLOWED_PAPER_INITIAL_CASH`) 외 값은 기본 400 으로 거부.
    `fallback_to_default=True` 면 default 로 대체 후 `fallback_used=True`
    carry. broker / OrderExecutor / route_order 호출 0건 — in-memory 갱신만.
    """
    try:
        cfg, fallback_used = set_paper_capital_config(
            body.initial_cash,
            fallback_to_default=body.fallback_to_default,
        )
    except InvalidPaperCapitalError as exc:
        raise HTTPException(
            status_code=400,
            detail={
                "error":   "invalid_paper_initial_cash",
                "message": str(exc),
                "allowed_initial_cash_options": list(ALLOWED_PAPER_INITIAL_CASH),
            },
        )
    return {
        **cfg.to_dict(),
        "fallback_used": fallback_used,
        "notice": (
            "Paper 시드머니는 *모의매매 전용* 이며 실전 계좌와 무관합니다."
        ),
    }


# ============================================================================
# P-02: 종목당 한도 설정 endpoints (in-memory; P-16 에서 영구화)
# ============================================================================


class _PerSymbolAllocationBody(BaseModel):
    """`POST /auto-paper/per-symbol-allocation` 입력.

    세 필드 모두 *옵션* — None 이면 현재 값 유지. caller 는 mode 만 바꾸거나
    값 한쪽만 바꿔도 됨. 허용되지 않은 값은 기본 400 으로 거부 —
    `fallback_to_default=True` 면 default 로 대체.
    """

    mode:                str   | None = Field(
        None, description="'FIXED_KRW' or 'PCT_OF_EQUITY'",
    )
    per_symbol_max_krw:  int   | None = Field(
        None, description="허용 KRW 옵션 중 하나 (예: 1000000, 2000000)",
    )
    per_symbol_max_pct:  float | None = Field(
        None, description="허용 비율 옵션 중 하나 (예: 0.10)",
    )
    fallback_to_default: bool         = Field(
        False, description="True 면 허용 외 값을 default 로 대체",
    )


@_AP.post("/per-symbol-allocation")
def set_per_symbol_allocation_endpoint(body: _PerSymbolAllocationBody) -> dict:
    """P-02: 종목당 최대 투자금 설정 변경.

    *Paper 전용* — 실전 주문 한도와 결합 0건. broker / OrderExecutor /
    route_order 호출 0건 — in-memory 갱신만. partial update 지원.
    """
    try:
        cfg, fallback_used = set_per_symbol_allocation(
            mode=body.mode,
            per_symbol_max_krw=body.per_symbol_max_krw,
            per_symbol_max_pct=body.per_symbol_max_pct,
            fallback_to_default=body.fallback_to_default,
        )
    except InvalidPerSymbolAllocationError as exc:
        raise HTTPException(
            status_code=400,
            detail={
                "error":   "invalid_per_symbol_allocation",
                "message": str(exc),
                "allowed_modes":                      [m.value for m in PerSymbolAllocationMode],
                "allowed_per_symbol_max_krw_options": list(ALLOWED_PER_SYMBOL_MAX_KRW),
                "allowed_per_symbol_max_pct_options": list(ALLOWED_PER_SYMBOL_MAX_PCT),
            },
        )
    return {
        **cfg.to_dict(),
        "fallback_used": fallback_used,
        "notice": (
            "종목당 최대 투자금은 Paper 모의매매 전용이며 실전 주문금액이 "
            "아닙니다."
        ),
    }


# ============================================================================
# P-03: 최대 동시 보유 종목 수 설정 endpoints (in-memory)
# ============================================================================


class _MaxConcurrentPositionsBody(BaseModel):
    """`POST /auto-paper/max-concurrent-positions` 입력."""

    max_concurrent_positions: int  = Field(..., description="허용 옵션 중 하나 (3 / 5 / 10)")
    fallback_to_default:      bool = Field(False, description="True 면 허용 외 값을 default 로 대체")


@_AP.post("/max-concurrent-positions")
def set_max_concurrent_positions_endpoint(body: _MaxConcurrentPositionsBody) -> dict:
    """P-03: 최대 동시 보유 종목 수 변경.

    *Paper 전용* — 실전 주문 한도와 결합 0건. broker / OrderExecutor /
    route_order 호출 0건 — in-memory 갱신만.
    """
    try:
        cfg, fallback_used = set_max_concurrent_positions(
            body.max_concurrent_positions,
            fallback_to_default=body.fallback_to_default,
        )
    except InvalidMaxConcurrentPositionsError as exc:
        raise HTTPException(
            status_code=400,
            detail={
                "error":   "invalid_max_concurrent_positions",
                "message": str(exc),
                "allowed_max_concurrent_positions_options": list(
                    ALLOWED_MAX_CONCURRENT_POSITIONS
                ),
            },
        )
    return {
        **cfg.to_dict(),
        "fallback_used": fallback_used,
        "notice": (
            "최대 동시 보유 종목 수는 Paper 모의매매 전용이며 실전 주문 한도가 "
            "아닙니다."
        ),
    }


class _ConcurrentBuyPreviewBody(BaseModel):
    """advisory preview — caller 가 BUY 시도 *전* 한도 도달 여부 사전 시뮬."""

    action:               str            = Field("BUY", description="caller 의 매매 의도 — 'BUY' 만 한도 체크")
    symbol:               str | None     = Field(None, description="신규 진입 후보 종목 코드")
    current_held_symbols: list[str]      = Field(
        default_factory=list,
        description="현재 보유 중인 고유 종목 코드 목록 — 중복 제외 권장",
    )


@_AP.post("/max-concurrent-positions/preview")
def preview_concurrent_buy_endpoint(body: _ConcurrentBuyPreviewBody) -> dict:
    """advisory preview — 신규 BUY 가 한도에 걸리는지 사전 체크.

    Returns:
        ConcurrentBuyCheckResult.to_dict() — verdict / reason / 보유 카운트 carry.

    broker / route_order 호출 0건. 본 응답은 *advisory* — 실 주문은 별도 흐름.
    """
    cfg = get_paper_capital_config()
    result = check_concurrent_buy_allowed(
        action=body.action,
        symbol=body.symbol,
        current_held_symbols=body.current_held_symbols,
        max_concurrent_positions=cfg.max_concurrent_positions,
    )
    return {
        **result.to_dict(),
        "notice": (
            "본 결과는 advisory — 실 주문은 별도 흐름 (RiskManager / route_order). "
            "Paper 전용이며 실전 주문 한도가 아닙니다."
        ),
    }


# ============================================================================
# P-04: Paper 매수 가능성 (affordability) preview endpoint
# ============================================================================


class _AffordabilityPreviewBody(BaseModel):
    """advisory preview — caller 가 BUY 후보 *전* affordability 사전 시뮬.

    `effective_per_symbol_cap_krw` / `max_concurrent_positions` 가 입력에
    없으면 *현재 PaperCapitalConfig 값* 을 자동 사용. 명시 override 도 허용
    (테스트 / what-if 시뮬용).
    """

    action:                       str             = Field("BUY", description="caller 의 매매 의도 — BUY 만 검사")
    symbol:                       str | None      = Field(None, description="후보 종목 코드")
    price:                        float | None    = Field(None, description="1주 가격 (KRW)")
    available_cash_krw:           int             = Field(0, description="남은 Paper 현금")
    current_held_symbols:         list[str]       = Field(default_factory=list)
    # 옵션 — 미주입 시 현재 PaperCapitalConfig 자동 적용.
    effective_per_symbol_cap_krw: int | None      = Field(None)
    max_concurrent_positions:     int | None      = Field(None)


@_AP.post("/affordability/preview")
def preview_paper_affordability_endpoint(body: _AffordabilityPreviewBody) -> dict:
    """Paper BUY 후보 affordability 사전 advisory check.

    Returns:
        AffordabilityResult.to_dict() — verdict / reason_ko / affordable_quantity
        / 사용된 cap + cash + held + max_positions carry.

    broker / route_order 호출 0건. 응답은 advisory — 실 주문은 별도 흐름.
    """
    cfg = get_paper_capital_config()
    cap = (
        int(body.effective_per_symbol_cap_krw)
        if body.effective_per_symbol_cap_krw is not None
        else int(cfg.effective_per_symbol_cap_krw)
    )
    max_pos = (
        int(body.max_concurrent_positions)
        if body.max_concurrent_positions is not None
        else int(cfg.max_concurrent_positions)
    )
    result = check_paper_affordability(
        action=body.action,
        symbol=body.symbol,
        price=body.price,
        available_cash_krw=int(body.available_cash_krw),
        effective_per_symbol_cap_krw=cap,
        current_held_symbols=body.current_held_symbols,
        max_concurrent_positions=max_pos,
    )
    return {
        **result.to_dict(),
        "notice": (
            "본 결과는 advisory — Paper 전용이며 실전 주문 결정과 결합되지 "
            "않습니다. 실 주문은 별도 흐름 (RiskManager / route_order)."
        ),
    }


# ============================================================================
# P-05: Paper 최소 1주 매수 가드 endpoints
# ============================================================================


class _MinLotValidateBody(BaseModel):
    """advisory — 운영자/caller 가 결정한 quantity 가 유효한 정수 ≥ 1 인지."""

    action:   str            = Field("BUY", description="caller 매매 의도")
    symbol:   str | None     = Field(None)
    quantity: float | None   = Field(None, description="검증 대상 수량 (정수 ≥ 1 만 ALLOWED)")


@_AP.post("/min-lot/validate")
def validate_paper_min_lot_endpoint(body: _MinLotValidateBody) -> dict:
    """Paper BUY 수량의 *정수 ≥ 1* 가드 advisory.

    소수점 / 음수 / 0 / 비-숫자 입력은 모두 BLOCKED_*. SELL/EXIT/HOLD →
    SKIP_NON_BUY. broker / route_order 호출 0건.
    """
    result = validate_paper_min_lot(
        action=body.action,
        quantity=body.quantity,
        symbol=body.symbol,
    )
    return {
        **result.to_dict(),
        "notice": (
            "Paper 모의매매 전용 advisory — 정수 1주 이상만 매수 후보. 소수점 "
            "주식 불가. broker / OrderExecutor 호출 0건."
        ),
    }


@_AP.get("/min-lot/preview")
def preview_min_lot_endpoint() -> dict:
    """현재 PaperCapitalConfig 기준 *예시 1주 가격* 별 매수 가능 수량.

    UI 가 "현재 cap 기준 예상 가능 수량 예시" 를 노출할 때 사용. broker / DB
    호출 0건. 예시 가격: 5만 / 10만 / 50만 / 100만 / 200만 / 500만 KRW.
    """
    cfg = get_paper_capital_config()
    sample_prices = [50_000, 100_000, 500_000, 1_000_000, 2_000_000, 5_000_000]
    examples = []
    for p in sample_prices:
        qty = compute_paper_affordable_lot(
            effective_per_symbol_cap_krw=cfg.effective_per_symbol_cap_krw,
            available_cash_krw=cfg.initial_cash,  # *시드머니 기준* — 미사용 가정
            price=p,
        )
        examples.append({
            "price":                p,
            "affordable_quantity":  qty,
            "is_affordable":        qty >= 1,
        })
    return {
        "effective_per_symbol_cap_krw":  int(cfg.effective_per_symbol_cap_krw),
        "initial_cash":                  int(cfg.initial_cash),
        "examples":                      examples,
        "min_lot_quantity":              1,
        "fractional_share_supported":    False,
        "rounding_policy":               "floor",
        "is_paper_only":                 True,
        "is_live_authorization":         False,
        "notice": (
            "예시 표는 현재 시드머니 전체가 가용하다는 가정. 실제 매수 가능 "
            "수량은 P-04 affordability_check 에서 cash 잔액까지 함께 본다. "
            "Paper 전용 advisory."
        ),
    }


# ============================================================================
# P-06: 고가주 처리 정책 preview endpoint (EXCLUDE / HOLD / INCREASE_BUDGET_HINT)
# ============================================================================


class _HighPricePreviewBody(BaseModel):
    """advisory preview — caller 가 BUY 후보 *전* 고가주 정책 사전 시뮬.

    `effective_per_symbol_cap_krw` 가 입력에 없으면 *현재 PaperCapitalConfig
    값* 을 자동 사용. policy 미지정 시 default EXCLUDE.
    """

    action:                       str            = Field("BUY", description="caller 의 매매 의도 — BUY 만 검사")
    symbol:                       str | None     = Field(None, description="후보 종목 코드")
    price:                        float | None   = Field(None, description="1주 가격 (KRW)")
    policy:                       str | None     = Field(
        None,
        description="고가주 정책 — 'EXCLUDE' / 'HOLD' / 'INCREASE_BUDGET_HINT'. None → default EXCLUDE",
    )
    effective_per_symbol_cap_krw: int | None     = Field(None)


@_AP.post("/high-price/preview")
def preview_high_price_endpoint(body: _HighPricePreviewBody) -> dict:
    """고가주 처리 정책 사전 advisory check (P-06).

    Returns:
        HighPriceCheckResult.to_dict() — verdict / reason_ko / policy /
        suggested_min_cap_krw carry.

    broker / route_order 호출 0건. 응답은 advisory — 실 주문은 별도 흐름.
    """
    cfg = get_paper_capital_config()
    cap = (
        int(body.effective_per_symbol_cap_krw)
        if body.effective_per_symbol_cap_krw is not None
        else int(cfg.effective_per_symbol_cap_krw)
    )
    try:
        result = evaluate_high_price(
            action=body.action,
            symbol=body.symbol,
            price=body.price,
            effective_per_symbol_cap_krw=cap,
            policy=body.policy,
        )
    except ValueError as exc:
        raise HTTPException(
            status_code=400,
            detail={
                "error":   "invalid_high_price_policy",
                "message": str(exc),
                "allowed_policies": [p.value for p in HighPricePolicy],
            },
        )
    return {
        **result.to_dict(),
        "default_policy": DEFAULT_HIGH_PRICE_POLICY.value,
        "allowed_policies": [p.value for p in HighPricePolicy],
        "notice": (
            "본 결과는 advisory — Paper 전용이며 실전 주문 결정과 결합되지 "
            "않습니다. 고가주 정책은 운영자가 선택 가능 (default EXCLUDE)."
        ),
    }


# ============================================================================
# P-07: Paper 현금 잔고 (CapitalState) endpoints
# ============================================================================


class _CashCheckPreviewBody(BaseModel):
    """advisory preview — BUY 후보의 (price × quantity) 가 현재 *남은 Paper
    현금* 으로 살 수 있는지 사전 시뮬.

    `available_cash_krw` 미주입 시 *현재 CapitalState singleton* 의 잔고를
    자동 사용. 명시 override 도 허용 (테스트 / what-if 시뮬용).
    """

    action:             str           = Field("BUY", description="caller 매매 의도 — BUY 만 검사")
    symbol:             str | None    = Field(None, description="후보 종목 코드")
    price:              float | None  = Field(None, description="1주 가격 (KRW)")
    quantity:           int           = Field(0, description="요청 수량 (정수 ≥ 1)")
    available_cash_krw: int | None    = Field(
        None,
        description="남은 Paper 현금. None 이면 현재 CapitalState singleton 자동 사용.",
    )


@_AP.post("/cash-check/preview")
def preview_paper_cash_check_endpoint(body: _CashCheckPreviewBody) -> dict:
    """Paper BUY 후보의 *현금 잔고 충분성* 사전 advisory check (P-07).

    Returns:
        CashCheckResult.to_dict() — verdict / reason_ko / required_krw /
        available_cash_krw / shortfall_krw carry.

    종목당 한도와는 *별개* — 한도가 충분해도 누적 BUY 로 현금이 부족하면
    INSUFFICIENT_PAPER_CASH 반환. SELL/HOLD 는 SKIP_NON_BUY.

    broker / route_order 호출 0건. *상태 변경 0건* — 본 endpoint 는
    `CapitalState.precheck_buy` 만 호출 (reservation / commit 없음).
    """
    state = get_capital_state()
    if body.available_cash_krw is not None:
        result = check_buy_cash_sufficient(
            action=body.action,
            symbol=body.symbol,
            price=body.price,
            quantity=body.quantity,
            available_cash_krw=int(body.available_cash_krw),
        )
    else:
        result = state.precheck_buy(
            action=body.action,
            symbol=body.symbol,
            price=body.price,
            quantity=body.quantity,
        )
    return {
        **result.to_dict(),
        "notice": (
            "본 결과는 advisory — Paper 전용이며 실거래 주문 결정과 결합되지 "
            "않습니다. 차단된 BUY 는 Paper 현금을 차감하지 않습니다."
        ),
    }


@_AP.get("/cash-state")
def get_paper_cash_state_endpoint() -> dict:
    """현재 Paper 현금 잔고 (CapitalState) snapshot — read-only.

    `available_cash_krw` 가 BUY 가능성의 *실제 기준*. broker / 실 계좌와
    *결합 0건* — `is_paper_only=True` carry.
    """
    snap = get_capital_state().snapshot()
    return {
        **snap.to_dict(),
        "notice": (
            "Paper 모의매매 전용 현금 잔고. 실거래 계좌와 무관합니다. "
            "broker / OrderExecutor 호출 0건."
        ),
    }


@_AP.post("/cash-state/reset")
def reset_paper_cash_state_endpoint() -> dict:
    """현재 PaperCapitalConfig.initial_cash 로 CapitalState 초기화.

    운영자가 *Paper 시드머니* 를 변경한 뒤 누적 BUY/SELL 이력을 리셋할 때
    사용. broker 호출 0건, 실거래 영향 0건.
    """
    cfg = get_paper_capital_config()
    state = get_capital_state()
    snap = state.reset(initial_cash_krw=int(cfg.initial_cash))
    return {
        **snap.to_dict(),
        "notice": (
            "Paper 현금 잔고 리셋 완료 — 누적 BUY/SELL 카운트와 invested 초기화."
        ),
    }


# ============================================================================
# P-08: Paper position sizing by price (preview)
# ============================================================================


class _SizingPreviewBody(BaseModel):
    """advisory preview — `quantity = floor(max_amount / price)`.

    `max_amount_krw` 미주입 시 *현재 PaperCapitalConfig*  의
    `effective_per_symbol_cap_krw` 자동 사용.
    """

    action:          str            = Field("BUY", description="caller 의 매매 의도 — BUY 만 sizing")
    symbol:          str | None     = Field(None, description="후보 종목 코드")
    price:           float | None   = Field(None, description="1주 가격 (KRW)")
    max_amount_krw:  int | None     = Field(
        None,
        description="종목당 투자금 한도 (KRW). None 이면 현재 PaperCapitalConfig 자동 사용.",
    )


@_AP.post("/sizing/preview")
def preview_paper_sizing_endpoint(body: _SizingPreviewBody) -> dict:
    """P-08: Paper BUY 후보의 *수량 계산* 사전 advisory.

    Returns:
        QuantityByPriceResult.to_dict() — verdict / quantity / notional_krw /
        remainder_krw / reason_code / reason_ko carry.

    호출 순서 (사용자 요청서 §11):
      현재가 확인 → 종목당 투자금 기준 quantity 계산 (본 endpoint) →
      quantity ≥ 1 확인 → Paper 현금 잔고 확인 (P-07) → RiskManager →
      PermissionGate → VirtualOrder / PaperOrder 후보 생성.

    broker / route_order 호출 0건. *상태 변경 0건* — 단순 계산만.
    """
    cfg = get_paper_capital_config()
    cap = (
        int(body.max_amount_krw)
        if body.max_amount_krw is not None
        else int(cfg.effective_per_symbol_cap_krw)
    )
    result = compute_paper_quantity_by_price(
        action=body.action,
        symbol=body.symbol,
        price=body.price,
        max_amount_krw=cap,
    )
    return {
        **result.to_dict(),
        "allowed_reason_codes": [v.value for v in QuantityByPriceVerdict],
        "notice": (
            "본 결과는 advisory — Paper 전용이며 실거래 주문 결정과 결합되지 "
            "않습니다. 다음 단계는 P-07 현금 잔고 검사 → RiskManager → "
            "PermissionGate."
        ),
    }


# ============================================================================
# P-09: Risk-profile-based capital allocation (preview + catalog)
# ============================================================================


class _RiskProfileAllocationBody(BaseModel):
    """advisory preview — profile + total → per_symbol / max_positions / daily.

    `total_paper_capital_krw` 미주입 시 *현재 PaperCapitalConfig.initial_cash*
    자동 사용. `manual_per_symbol_krw` 가 있으면 *수동값 우선*.
    """

    profile:                  str | None = Field(
        None,
        description="CONSERVATIVE / BALANCED / AGGRESSIVE. None → BALANCED 기본값.",
    )
    total_paper_capital_krw:  int | None = Field(
        None,
        description="총 Paper 자금. None 이면 현재 PaperCapitalConfig.initial_cash 자동 사용.",
    )
    manual_per_symbol_krw:    int | None = Field(
        None,
        description="사용자가 직접 입력한 종목당 한도. None 이면 profile 자동값 사용.",
    )


@_AP.post("/risk-profile/allocation/preview")
def preview_risk_profile_allocation_endpoint(
    body: _RiskProfileAllocationBody,
) -> dict:
    """P-09: profile + 총 자금 → 종목당 / max_positions / 일일 매수 한도.

    Returns:
        CapitalAllocationResult.to_dict() — profile / display_name /
        per_symbol_allocation / max_positions / max_daily_buy_amount /
        is_manual_override / reason_message carry.

    broker / route_order 호출 0건. *상태 변경 0건* — 단순 계산만.
    """
    cfg = get_paper_capital_config()
    total = (
        int(body.total_paper_capital_krw)
        if body.total_paper_capital_krw is not None
        else int(cfg.initial_cash)
    )
    result = capital_allocation_for(
        body.profile,
        total_paper_capital_krw=total,
        manual_per_symbol_krw=body.manual_per_symbol_krw,
    )
    return {
        **result.to_dict(),
        "default_profile":   DEFAULT_RISK_PROFILE.value,
        "allowed_profiles":  [p.value for p in RiskProfile],
        "notice": (
            "본 결과는 advisory — Paper 전용이며 실거래 결정과 결합되지 "
            "않습니다. 다음 단계 P-07 현금 잔고 검사 → P-08 수량 계산 → "
            "RiskManager → PermissionGate."
        ),
    }


@_AP.get("/risk-profile/catalog")
def get_risk_profile_catalog_endpoint() -> dict:
    """3 risk profile 카탈로그 — UI / 운영자 read-only."""
    return {
        "profiles":         list_capital_allocations(),
        "default_profile":  DEFAULT_RISK_PROFILE.value,
        "allowed_profiles": [p.value for p in RiskProfile],
        "is_paper_only":    True,
        "is_live_authorization": False,
        "notice": (
            "운용 성향은 Paper 전용이며 실거래 권한 부여가 아닙니다. "
            "AGGRESSIVE 도 Paper 한정."
        ),
    }


# ============================================================================
# P-10: 일일 최대 신규 매수금액 한도 (Daily Buy Limit) — preview + resolve
# ============================================================================


class _DailyBuyLimitPreviewBody(BaseModel):
    """advisory preview — 일일 매수 한도 사전 시뮬.

    `max_daily_buy_amount_krw` 미주입 시 *resolve* 흐름 (manual → P-09 →
    system default) 적용. `today_buy_used_amount_krw` 미주입 시 0 으로 시작
    (테스트 / what-if 시뮬 권장).
    """

    side:                       str            = Field("BUY", description="매매 의도 — BUY 만 평가")
    symbol:                     str | None     = Field(None)
    price:                      float | None   = Field(None)
    quantity:                   int            = Field(0, description="수량 (정수 ≥ 1)")
    today_buy_used_amount_krw:  int            = Field(
        0,
        description="오늘 누적된 신규 BUY 사용금액 (KRW). caller 가 ledger 에서 집계 후 전달.",
    )
    max_daily_buy_amount_krw:   int | None     = Field(
        None,
        description="None → resolve_daily_buy_limit 자동 적용",
    )
    # resolve 입력 — max 미주입 시만 사용.
    manual_daily_buy_limit_krw: int | None     = Field(None)
    risk_profile:               str | None     = Field(
        None, description="CONSERVATIVE/BALANCED/AGGRESSIVE",
    )
    total_paper_capital_krw:    int | None     = Field(None)


@_AP.post("/daily-buy-limit/preview")
def preview_daily_buy_limit_endpoint(body: _DailyBuyLimitPreviewBody) -> dict:
    """P-10: 일일 매수 한도 사전 advisory check.

    Returns:
        DailyBuyLimitResult.to_dict() — allowed / reason_code /
        reason_message / max / today_used / new_notional /
        projected / remaining carry. 추가로 `resolved_source` 안내.

    호출 순서 (사용자 요청서 §4):
      현재가 → P-08 sizing → P-06 → P-07 cash → *P-10 (본 endpoint)* →
      RiskManager → PermissionGate → VirtualOrder.

    broker / route_order 호출 0건. *상태 변경 0건* — 단순 계산만.
    """
    cfg = get_paper_capital_config()
    if body.max_daily_buy_amount_krw is not None:
        max_amount = int(body.max_daily_buy_amount_krw)
        source = "explicit"
    else:
        total = (
            int(body.total_paper_capital_krw)
            if body.total_paper_capital_krw is not None
            else int(cfg.initial_cash)
        )
        max_amount, source = resolve_daily_buy_limit(
            manual_daily_buy_limit_krw=body.manual_daily_buy_limit_krw,
            risk_profile=body.risk_profile,
            total_paper_capital_krw=total,
        )

    result = check_daily_buy_limit(
        side=body.side,
        symbol=body.symbol,
        price=body.price,
        quantity=body.quantity,
        today_buy_used_amount=int(body.today_buy_used_amount_krw),
        max_daily_buy_amount=max_amount,
    )
    return {
        **result.to_dict(),
        "resolved_source":        source,
        "default_max_krw":        DEFAULT_DAILY_BUY_LIMIT_KRW,
        "notice": (
            "본 결과는 advisory — Paper 전용이며 실거래 결정과 결합되지 "
            "않습니다. 다음 단계는 RiskManager → PermissionGate."
        ),
    }


class _ResolveDailyBuyLimitBody(BaseModel):
    """일일 매수 한도 resolve — 어느 source 가 사용되는지 advisory 표시."""

    manual_daily_buy_limit_krw: int | None     = Field(None)
    risk_profile:               str | None     = Field(None)
    total_paper_capital_krw:    int | None     = Field(None)


@_AP.post("/daily-buy-limit/resolve")
def resolve_daily_buy_limit_endpoint(body: _ResolveDailyBuyLimitBody) -> dict:
    """우선순위 (사용자 요청서 §2): manual → P-09 → system default."""
    cfg = get_paper_capital_config()
    total = (
        int(body.total_paper_capital_krw)
        if body.total_paper_capital_krw is not None
        else int(cfg.initial_cash)
    )
    amount, source = resolve_daily_buy_limit(
        manual_daily_buy_limit_krw=body.manual_daily_buy_limit_krw,
        risk_profile=body.risk_profile,
        total_paper_capital_krw=total,
    )
    return {
        "max_daily_buy_amount_krw":   int(amount),
        "source":                     source,
        "default_max_krw":            DEFAULT_DAILY_BUY_LIMIT_KRW,
        "total_paper_capital_krw":    total,
        "is_paper_only":              True,
        "is_live_authorization":      False,
        "is_order_signal":            False,
        "notice": (
            "Paper 전용 advisory — 실거래 한도와 결합되지 않습니다."
        ),
    }


# ============================================================================
# P-11: 종목별 최대 비중 제한 — preview + resolve
# ============================================================================


class _SymbolWeightLimitPreviewBody(BaseModel):
    """advisory preview — 종목별 비중 한도 사전 시뮬.

    `max_symbol_weight_pct` 미주입 시 resolve 흐름 (manual → P-09 → system
    default) 자동 적용. `total_paper_equity_krw` 미주입 시 현재
    `PaperCapitalConfig.initial_cash` 자동 사용.
    """

    side:                            str           = Field("BUY", description="매매 의도 — BUY 만 평가")
    symbol:                          str           = Field(..., description="종목 코드")
    price:                           float | None  = Field(None)
    quantity:                        int           = Field(0)
    current_symbol_exposure_amount:  int           = Field(
        0,
        description="현재 해당 종목 Paper 보유 평가금액 (KRW). caller 가 ledger / VirtualPosition 에서 집계 후 전달.",
    )
    total_paper_equity_krw:          int | None    = Field(
        None,
        description="총 Paper 자산 (KRW). None 이면 PaperCapitalConfig.initial_cash 자동 사용.",
    )
    max_symbol_weight_pct:           float | None  = Field(
        None,
        description="None → resolve_symbol_weight_limit_pct 자동 적용",
    )
    manual_max_symbol_weight_pct:    float | None  = Field(None)
    risk_profile:                    str | None    = Field(None)


@_AP.post("/symbol-weight-limit/preview")
def preview_symbol_weight_limit_endpoint(
    body: _SymbolWeightLimitPreviewBody,
) -> dict:
    """P-11: 종목별 비중 한도 사전 advisory check.

    Returns:
        SymbolWeightLimitResult.to_dict() — allowed / reason_code /
        reason_message / total_paper_equity / max_symbol_weight_pct /
        max_symbol_exposure_amount / current / new_buy_notional /
        projected / remaining_symbol_buy_capacity carry. 추가로
        `resolved_source` 안내.

    호출 순서 (사용자 요청서 §4):
      현재가 → P-08 sizing → P-06 → P-07 cash → P-10 daily → *P-11 (본)* →
      RiskManager → PermissionGate → VirtualOrder.

    broker / route_order 호출 0건. *상태 변경 0건* — 단순 계산만.
    """
    cfg = get_paper_capital_config()
    total = (
        int(body.total_paper_equity_krw)
        if body.total_paper_equity_krw is not None
        else int(cfg.initial_cash)
    )
    if body.max_symbol_weight_pct is not None:
        pct = float(body.max_symbol_weight_pct)
        source = "explicit"
    else:
        pct, source = resolve_symbol_weight_limit_pct(
            manual_max_symbol_weight_pct=body.manual_max_symbol_weight_pct,
            risk_profile=body.risk_profile,
        )

    result = check_symbol_weight_limit(
        side=body.side,
        symbol=body.symbol,
        price=body.price,
        quantity=body.quantity,
        total_paper_equity=total,
        current_symbol_exposure_amount=int(body.current_symbol_exposure_amount),
        max_symbol_weight_pct=pct,
    )
    return {
        **result.to_dict(),
        "resolved_source":  source,
        "default_pct":      DEFAULT_MAX_SYMBOL_WEIGHT_PCT,
        "notice": (
            "본 결과는 advisory — Paper 전용이며 실거래 결정과 결합되지 "
            "않습니다. 다음 단계는 RiskManager → PermissionGate."
        ),
    }


class _ResolveSymbolWeightBody(BaseModel):
    """종목별 비중 한도 resolve — 어느 source 가 사용되는지 확인."""

    manual_max_symbol_weight_pct: float | None = Field(None)
    risk_profile:                 str | None   = Field(None)


@_AP.post("/symbol-weight-limit/resolve")
def resolve_symbol_weight_limit_endpoint(body: _ResolveSymbolWeightBody) -> dict:
    """우선순위 (사용자 요청서 §2): manual → P-09 → system default."""
    pct, source = resolve_symbol_weight_limit_pct(
        manual_max_symbol_weight_pct=body.manual_max_symbol_weight_pct,
        risk_profile=body.risk_profile,
    )
    return {
        "max_symbol_weight_pct":  float(pct),
        "source":                 source,
        "default_pct":            DEFAULT_MAX_SYMBOL_WEIGHT_PCT,
        "is_paper_only":          True,
        "is_live_authorization":  False,
        "is_order_signal":        False,
        "notice": (
            "Paper 전용 advisory — 실거래 한도와 결합되지 않습니다."
        ),
    }


router.include_router(_AP)

"""Background tick ↔ KIS Paper Auto 주문 bridge.

BackgroundTickDriver 는 broker / route_order 를 import 하지 않는다(정적 가드).
KIS 모의 자동주문은 broker + route_order + DB 가 필요하므로, 그 결합을 본
bridge 모듈에 격리한다. driver 는 본 모듈의 `kis_paper_auto_tick` 를 *주입된
콜러블* 로만 호출한다.

흐름: run-once 파이프라인으로 후보(symbol/price/quantity) 도출 → AI/전략 결정
래핑 → `execute_kis_paper_auto_order` 로 위임(게이트 → route_order → KIS 모의
주문). 실거래 0건 — KIS_IS_PAPER=true + ENABLE_LIVE_TRADING=false 강제.
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any, Callable, Optional

from app.auto_paper.run_once import run_paper_pipeline_once
from app.kis_paper.auto_executor import (
    KisPaperAutoDecision,
    execute_kis_paper_auto_order,
)
from app.kis_paper.readiness import evaluate_readiness


_log = logging.getLogger("autotrade.kis_paper.bridge")


def _broker_is_kis_paper(broker) -> bool:
    return (
        type(broker).__name__ == "KisBrokerAdapter"
        and bool(getattr(broker, "is_paper", False))
    )


def _market_input_from_pipeline(symbol: str, price: float):
    """파이프라인 후보(단일 현재가)로부터 Agent Council 입력 구성.

    mock 시세는 단일 현재가만 주므로, 데모/검증용 *결정론적 상승* 시리즈를
    합성해 council 이 실제 4전략 투표를 수행하게 한다 (실 KIS 시세 연동 시에는
    caller 가 실제 series 를 채운 StrategyMarketInput 을 council 에 직접 전달).
    """
    from app.agents.agent_council import StrategyMarketInput
    p = float(price)
    closes = tuple(round(p * (1 + 0.01 * (i - 4)), 2) for i in range(5))  # 완만한 상승
    return StrategyMarketInput(
        symbol=symbol, current_price=p, prev_close=round(p * 0.985, 2),
        open_price=round(p * 0.99, 2), vwap=round(p * 0.995, 2),
        opening_range_high=round(p * 0.997, 2), opening_range_low=round(p * 0.985, 2),
        recent_closes=closes, current_volume=120.0, avg_volume=100.0,
        market_regime="TREND_UP", regime_decision="ALLOW",
    )


def build_decision_from_pipeline(
    *, settings: Any, now: datetime, risk_profile: Any = None,
) -> tuple[KisPaperAutoDecision, Any]:
    """run-once 파이프라인 후보 → Agent Council 투표 → KIS 자동주문 결정.

    4 전략(ORB/Momentum/Gap/VWAP) 투표를 Agent Council 이 종합해 BUY/SELL/HOLD
    를 결정하고, BUY/SELL 이면 KisPaperAutoDecision 으로 변환. HOLD 면 side=HOLD
    (게이트가 NO_STRATEGY_SIGNAL 로 차단). council 결정 객체도 함께 반환해
    AgentDecisionLog / UI 가 vote 내역을 기록·표시할 수 있게 한다.
    """
    from app.agents.agent_council import CouncilAction, run_agent_council
    provider = str(getattr(settings, "market_data_provider", "mock"))
    ro = run_paper_pipeline_once(
        symbol=None, force_mock_market_data=(provider.strip().lower() == "mock"),
        dry_run=False, market_data_provider=provider, record=False, now=now,
    )
    if not ro.ok or ro.symbol is None or ro.price is None or ro.quantity < 1:
        return (
            KisPaperAutoDecision(
                symbol=ro.symbol or "NONE", side="HOLD",
                quantity=0, price=int(ro.price or 0),
                entry_reason=ro.reason_message,
            ),
            None,
        )
    council = run_agent_council(
        _market_input_from_pipeline(ro.symbol, ro.price),
        risk_profile=risk_profile, held_position=False,
    )
    if council.final_action == CouncilAction.HOLD:
        return (
            KisPaperAutoDecision(
                symbol=ro.symbol, side="HOLD", quantity=0, price=int(ro.price),
                entry_reason=council.reason or "council HOLD",
            ),
            council,
        )
    kd = council.to_kis_paper_decision(quantity=int(ro.quantity), price=int(ro.price))
    return (kd, council)


async def kis_paper_auto_tick(
    *,
    now: datetime | None = None,
    session_factory: Optional[Callable[[], Any]] = None,
    broker: Any = None,
    risk: Any = None,
    decision: KisPaperAutoDecision | None = None,
    route_order_fn: Optional[Callable[..., Any]] = None,
    settings: Any = None,
) -> dict[str, Any]:
    """1 tick KIS 모의 자동주문 — driver 가 주입 콜러블로 호출.

    broker.place_order 직접 호출 0건 (route_order 위임). 실패해도 예외를
    던지지 않고 결과 dict 반환 (driver tick 이 죽지 않게).
    """
    if now is None:
        now = datetime.now(timezone.utc)
    if settings is None:
        from app.core.config import get_settings
        settings = get_settings()
    if session_factory is None:
        from app.db.session import SessionLocal
        session_factory = SessionLocal
    if route_order_fn is None:
        from app.execution.order_router import route_order
        route_order_fn = route_order

    db = session_factory()
    try:
        if broker is None:
            from app.api.deps import get_broker
            broker = get_broker()
        if risk is None:
            from app.api.deps import get_risk_manager
            risk = get_risk_manager()
        rd = evaluate_readiness(settings)
        creds = bool(rd.kis_key_present and rd.kis_secret_present and rd.kis_account_present)
        council = None
        if decision is not None:
            dec = decision
        else:
            dec, council = build_decision_from_pipeline(settings=settings, now=now)
        result = await execute_kis_paper_auto_order(
            db, decision=dec, settings=settings, broker=broker, risk=risk,
            broker_is_kis_paper=_broker_is_kis_paper(broker),
            credentials_present=creds, route_order_fn=route_order_fn, now=now,
        )
        try:
            db.commit()
        except Exception:  # noqa: BLE001
            db.rollback()
        out = result.to_dict()
        if council is not None:
            # Agent Council vote 내역을 결과에 carry (AgentDecisionLog 는 executor
            # 가 selected_strategies 를 이미 기록; 여기선 UI/디버그용 votes 동봉).
            out["council"] = council.to_dict()
        return out
    except Exception as exc:  # noqa: BLE001 — driver tick 보호.
        try:
            db.rollback()
        except Exception:  # noqa: BLE001
            pass
        _log.warning("[kis-paper-bridge] tick error: %s: %s", type(exc).__name__, exc)
        return {
            "reason_code": "KIS_PAPER_ERROR", "submitted": False,
            "broker_order_sent": False, "is_live_authorization": False,
            "broker_order_type": "KIS_PAPER",
            "reason_message": f"{type(exc).__name__}",
        }
    finally:
        try:
            db.close()
        except Exception:  # noqa: BLE001
            pass


__all__ = [
    "kis_paper_auto_tick",
    "build_decision_from_pipeline",
]

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
from dataclasses import replace
from datetime import datetime, timezone
from typing import Any, Callable, Optional

from app.auto_paper.paper_auto_mode import resolve_paper_auto_mode
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
) -> tuple[KisPaperAutoDecision, Any, Any]:
    """run-once 파이프라인 후보 → Agent Council 투표 → KIS 자동주문 결정.

    4 전략(ORB/Momentum/Gap/VWAP) 투표를 Agent Council 이 종합해 BUY/SELL/HOLD
    를 결정하고, BUY/SELL 이면 KisPaperAutoDecision 으로 변환. HOLD 면 side=HOLD
    (게이트가 NO_STRATEGY_SIGNAL 로 차단). council 결정 객체 + 시장 입력
    (StrategyMarketInput) 도 함께 반환해 AgentDecisionLog / episode market_snapshot
    / UI 가 vote 내역과 시장상태를 기록·표시할 수 있게 한다.

    Returns: (decision, council|None, market_input|None).
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
                entry_reason=ro.reason_message, price_source="mock",
            ),
            None,
            None,
        )
    market_input = _market_input_from_pipeline(ro.symbol, ro.price)
    council = run_agent_council(
        market_input, risk_profile=risk_profile, held_position=False,
        min_confidence_floor=float(getattr(settings, "kis_paper_auto_min_confidence", 0.6)),
    )
    if council.final_action == CouncilAction.HOLD:
        # 2-12: HOLD 도 판단 근거(votes/risk_flags/risk_veto/exit_plan_validation)를
        #       carry 해 AgentDecisionLog 에 보존 (HOLD 는 실패가 아니라 정상 판단).
        cd = council.to_dict()
        return (
            KisPaperAutoDecision(
                symbol=ro.symbol, side="HOLD", quantity=0, price=int(ro.price),
                entry_reason=council.reason or "council HOLD",
                selected_strategies=list(cd.get("selected_strategies", [])),
                confidence=float(cd.get("confidence", 0.0) or 0.0),
                quality_score=int(cd.get("quality_score", 0) or 0),
                risk_profile=cd.get("risk_profile"),
                risk_veto_result=dict(cd.get("risk_veto_result", {}) or {}),
                exit_plan_validation=dict(cd.get("exit_plan_validation", {}) or {}),
                votes=list(cd.get("votes", []) or []),
                risk_flags=list(cd.get("risk_flags", []) or []),
                price_source="mock",
            ),
            council,
            market_input,
        )
    kd = council.to_kis_paper_decision(quantity=int(ro.quantity), price=int(ro.price))
    # mock 파이프라인 결정은 price_source="mock" — 실제 KIS 모의주문 전송은
    # auto_permission 의 KIS_REALTIME_PRICE_REQUIRED 가드가 차단한다.
    kd = replace(kd, price_source="mock")
    return (kd, council, market_input)


def _record_episode_best_effort(
    db, *, episode_id, decision, council, result, market_input=None, now=None,
    requested_at=None, responded_at=None,
) -> None:
    """P-21/P-22: 한 tick 의 판단→주문 결과 + 시장 스냅샷을 episode 행으로 기록.

    best-effort — 실패(secret sanitize / DB 오류 등)해도 예외를 던지지 않는다
    (tick 결과 유지 우선). broker / route_order 호출 0건 (순수 기록).
    """
    try:
        from app.agents.decision_episode import record_episode
        from app.agents.market_snapshot import build_market_snapshot

        final_action = (
            council.final_action.value if council is not None
            else getattr(decision, "side", "HOLD")
        )
        conf = (council.confidence if council is not None
                else getattr(decision, "confidence", 0.0))
        confidence_pct = int(round(float(conf or 0.0) * 100)) if conf is not None else None
        quality = (council.quality_score if council is not None
                   else getattr(decision, "quality_score", None))
        rc = getattr(result, "reason_code", None)
        regime = council.market_regime if council is not None else None
        # P-22: 판단 당시 시장 스냅샷 (market_input 우선, 없으면 decision 가격으로
        # 최소 구성). best-effort.
        market_snapshot = build_market_snapshot(
            market_input=market_input, now=now, market_regime=regime,
            symbol=getattr(decision, "symbol", None),
            price=getattr(decision, "price", None),
        ).to_dict()
        # P-23: episode.votes 에 ORB/MOMENTUM/GAP/VWAP 4개가 *항상* 존재하도록 —
        # council 이 있으면 그 vote(4개), 없으면 placeholder 4개.
        if council is not None and getattr(council, "votes", None):
            votes = [v.to_dict() for v in council.votes]
        else:
            from app.agents.agent_council import placeholder_strategy_votes
            votes = placeholder_strategy_votes()
        council_dict = council.to_dict() if council is not None else None
        risk_result = {
            "reason_code":     rc,
            "blocked_by_risk": rc == "BLOCKED_BY_RISK_MANAGER",
            "approved":        rc == "KIS_PAPER_SUBMITTED",
        }
        permission_result = {
            "reason_code":    rc,
            "needs_approval": rc == "BLOCKED_BY_PERMISSION_GATE",
        }
        # P-24: 주문·체결 품질 로그 — kis_order_result 에 nest.
        kis_order_result = result.to_dict() if hasattr(result, "to_dict") else None
        try:
            from app.kis_paper.order_quality import build_order_quality_log
            quality = build_order_quality_log(
                result=result, decision=decision,
                requested_at=requested_at, responded_at=responded_at,
            ).to_dict()
            if isinstance(kis_order_result, dict):
                kis_order_result["order_quality"] = quality
        except Exception:  # noqa: BLE001 — 품질 로그 실패는 episode 기록을 막지 않음.
            pass
        # P-26: SELL 이면 매도 사유를 표준 reason_code 로 산출해 episode 에 carry.
        sell_reason = None
        if str(final_action).upper() == "SELL":
            try:
                from app.agents.sell_reason import infer_sell_reason
                sr = (council.sell_reason if council is not None
                      and getattr(council, "sell_reason", None) else None)
                if isinstance(sr, dict) and sr.get("reason_code"):
                    sell_reason = sr
                else:
                    sell_reason = infer_sell_reason(
                        decision=decision, council=council,
                        market_snapshot=market_snapshot, votes=votes,
                    ).to_dict()
            except Exception:  # noqa: BLE001 — sell_reason 실패는 episode 를 막지 않음.
                sell_reason = None
        record_episode(
            db,
            episode_id=episode_id,
            final_action=final_action,
            symbol=getattr(decision, "symbol", None),
            mode="PAPER",
            confidence=confidence_pct,
            quality_score=quality,
            reason_code=rc,
            market_snapshot=market_snapshot,
            votes=votes,
            council=council_dict,
            risk_result=risk_result,
            permission_result=permission_result,
            kis_order_result=kis_order_result,
            broker_order_no=getattr(result, "broker_order_no", None),
            audit_id=getattr(result, "audit_id", None),
            decision_log_id=getattr(result, "decision_log_id", None),
            outcome=None,
            sell_reason=sell_reason,
        )
        db.commit()
    except Exception as exc:  # noqa: BLE001 — episode 기록 실패는 tick 을 막지 않음.
        try:
            db.rollback()
        except Exception:  # noqa: BLE001
            pass
        _log.warning("[kis-paper-bridge] episode record failed: %s: %s",
                     type(exc).__name__, exc)


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

    # V2: provider=kis + 자동(주입 decision 없음) → 다종목 실시간 스캔 경로로 위임.
    # mock/yfinance provider 거나 caller 가 decision 을 명시 주입하면 기존 단일
    # 경로 유지 (단일 mock 결정은 price_source="mock" → 실제 전송은 가드가 차단).
    if decision is None and resolve_paper_auto_mode(settings).kis_realtime:
        return await kis_paper_realtime_scan_tick(
            now=now, session_factory=session_factory, broker=broker, risk=risk,
            route_order_fn=route_order_fn, settings=settings,
        )

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
        market_input = None
        if decision is not None:
            dec = decision
        else:
            dec, council, market_input = build_decision_from_pipeline(
                settings=settings, now=now,
            )
        # P-21: episode_id 발급 — chain_id 로 전달해 AgentDecisionLog / OrderRequest /
        # episode 행을 동일 키로 연결.
        from app.agents.decision_episode import new_episode_id
        episode_id = new_episode_id()
        # P-24: 주문 요청/응답 시각 — latency 측정용.
        requested_at = datetime.now(timezone.utc)
        result = await execute_kis_paper_auto_order(
            db, decision=dec, settings=settings, broker=broker, risk=risk,
            broker_is_kis_paper=_broker_is_kis_paper(broker),
            credentials_present=creds, route_order_fn=route_order_fn, now=now,
            chain_id=episode_id,
        )
        responded_at = datetime.now(timezone.utc)
        try:
            db.commit()
        except Exception:  # noqa: BLE001
            db.rollback()
        # P-21/P-22/P-24: episode 기록 + 시장 스냅샷 + 주문 품질 (best-effort).
        _record_episode_best_effort(
            db, episode_id=episode_id, decision=dec, council=council,
            result=result, market_input=market_input, now=now,
            requested_at=requested_at, responded_at=responded_at,
        )
        out = result.to_dict()
        out["decision_episode_id"] = episode_id
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


def _scan_universe_symbols(settings: Any, *, override: list[str] | None) -> list[str]:
    """스캔 대상 종목 — smoke mode 면 단일, 아니면 universe(상한 적용)."""
    if override is not None:
        return list(override)
    if bool(getattr(settings, "kis_paper_smoke_mode", False)):
        return [str(getattr(settings, "kis_paper_smoke_symbol", "005930"))]
    from app.universe.default_universe import get_default_universe
    uni = get_default_universe(None)
    syms = list(uni.symbols)
    cap = int(getattr(settings, "kis_paper_scan_max_symbols", 10) or 0)
    return syms[:cap] if cap > 0 else syms


def _today_kis_paper_buy_state(db: Any, now: datetime) -> tuple[set[str], int]:
    """오늘 체결된 kis_paper_auto 주문의 *net 보유* 종목 + 누적 매수금액.

    PART1-4 (2026-06-01 잔고부족 거부 fix):
      기존엔 BUY 한 종목을 전부 `held` 에 넣고 SELL 청산을 빼지 않아, 청산된
      종목(005380)이 계속 보유로 잡혀 SELL 신호가 broker 로 가 "잔고부족"
      거부가 반복됐다. 이제 종목별 BUY 수량 − SELL 수량(REJECTED 제외)을
      집계해 *net > 0* 인 종목만 `held` 로 본다.
    """
    held: set[str] = set()
    used = 0
    try:
        from app.db.models import OrderAuditLog
        start = now.replace(hour=0, minute=0, second=0, microsecond=0)
        rows = (
            db.query(OrderAuditLog)
            .filter(OrderAuditLog.trade_reason == "kis_paper_auto")
            .filter(OrderAuditLog.created_at >= start)
            .filter(OrderAuditLog.executed.is_(True))
            .all()
        )
        # 종목별 net 수량 (BUY +qty, SELL -qty). broker_status REJECTED 는 제외 —
        # 거부된 주문은 실제 잔고를 바꾸지 않으므로 보유 계산에 넣지 않는다.
        net_qty: dict[str, int] = {}
        for r in rows:
            sym = getattr(r, "symbol", None)
            if not sym:
                continue
            bstat = str(getattr(r, "broker_status", "") or "").upper()
            if bstat == "REJECTED":
                continue
            side = str(getattr(r, "side", "") or "").upper()
            qty = int(getattr(r, "quantity", 0) or 0)
            px = int(getattr(r, "avg_fill_price", 0) or getattr(r, "limit_price", 0) or 0)
            if side in ("BUY", "B"):
                net_qty[sym] = net_qty.get(sym, 0) + qty
                used += qty * px
            elif side in ("SELL", "S"):
                net_qty[sym] = net_qty.get(sym, 0) - qty
        held = {sym for sym, q in net_qty.items() if q > 0}
    except Exception:  # noqa: BLE001 — 상태 집계 실패는 스캔을 막지 않음 (보수적 0).
        return set(), 0
    return held, used


async def _kis_held_symbols(broker: Any, *, fallback: set[str]) -> set[str]:
    """현재 보유 종목 = *KIS 잔고(get_positions)* 의 net>0 종목 — 브로커 진실.

    today-only DB net(`_today_kis_paper_buy_state`)은 *전일 캐리오버* 포지션을
    못 잡아, 다음날 held 가 비어 (1) 보유 청산 불가(SELL_NO_HELD_POSITION),
    (2) 기존 보유 위에 중복 매수해 max_concurrent 초과를 유발했다(2026-06-05
    발견: KIS 5종목 보유인데 봇은 0으로 인식). 보유는 브로커 진실에서 읽는다.

    조회 *성공* 시 KIS 를 신뢰한다(빈 결과 = 실제 무보유). 조회 *실패* 시에만
    보수적으로 fallback(오늘 DB net)으로 — 스캔 자체는 막지 않는다.
    """
    try:
        if broker is None or not hasattr(broker, "get_positions"):
            return set(fallback)
        positions = await broker.get_positions()
    except Exception:  # noqa: BLE001 — 조회 실패는 스캔을 막지 않음 (fallback).
        return set(fallback)
    held = {
        str(getattr(p, "symbol", "") or "")
        for p in (positions or [])
        if int(getattr(p, "quantity", 0) or 0) > 0
    }
    held.discard("")
    return held


async def kis_paper_realtime_scan_tick(
    *,
    now: datetime | None = None,
    session_factory: Optional[Callable[[], Any]] = None,
    broker: Any = None,
    risk: Any = None,
    route_order_fn: Optional[Callable[..., Any]] = None,
    settings: Any = None,
    market_input_fn: Optional[Callable[..., Any]] = None,
    universe_symbols: list[str] | None = None,
    client: Any = None,
    risk_profile: Any = None,
) -> dict[str, Any]:
    """KIS 실시간 시세 기반 *다종목* 스캔 1 tick.

    universe 를 순회하며 KIS read-only 현재가로 `StrategyMarketInput` 구성 →
    Agent Council 판단 → BUY/SELL 후보는 리스크 한도 내에서
    `execute_kis_paper_auto_order` 로 위임(게이트 → route_order → KIS 모의주문).
    broker.place_order 직접 호출 0건. mock 으로 fallback 0건 (실패 종목은 skip).

    DRYRUN 모드(`kis_paper_auto_order_dry_run=true`)면 게이트가 dry-run 경로로
    돌아 broker_order_sent=false. SMOKE 모드면 단일 종목 / qty=1.
    """
    from app.agents.agent_council import CouncilAction, run_agent_council
    from app.scheduler.market_clock import MarketPhase, current_market_phase

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
    _fn_injected = market_input_fn is not None
    if market_input_fn is None:
        from app.market_data.kis_realtime import build_kis_market_input
        market_input_fn = build_kis_market_input

    mode = resolve_paper_auto_mode(settings)
    market_is_open = current_market_phase(now) == MarketPhase.OPEN
    smoke = bool(getattr(settings, "kis_paper_smoke_mode", False))
    # R1(2026-06-06): 종목당 투자금/동시진입 종목 수는 *런타임 오버라이드 > env*.
    #   봇은 매 사이클 config 모듈(runtime_config)의 effective getter 를 호출 —
    #   오버라이드 파일을 직접 읽지 않는다. 저장 즉시 다음 신규 매수 판단부터 반영.
    #   (주문 경로 route_order/RiskManager/Gate/Executor 는 미수정.)
    from app.core.runtime_config import (
        effective_max_concurrent_positions,
        effective_per_stock_budget,
    )
    per_symbol_notional = int(effective_per_stock_budget())
    max_concurrent = int(effective_max_concurrent_positions())
    daily_buy_limit = int(getattr(settings, "kis_paper_daily_buy_limit_krw", 3_000_000))
    max_new_per_tick = max(1, int(getattr(settings, "kis_paper_max_new_positions_per_tick", 1) or 1))
    if smoke:
        max_new_per_tick = 1
    smoke_qty = max(1, int(getattr(settings, "kis_paper_smoke_qty", 1) or 1))

    symbols = _scan_universe_symbols(settings, override=universe_symbols)

    db = session_factory()
    out_orders: list[dict[str, Any]] = []
    skipped: list[dict[str, Any]] = []
    symbols_scanned = 0
    candidates_found = 0
    orders_attempted = 0
    orders_submitted = 0
    try:
        if broker is None:
            from app.api.deps import get_broker
            broker = get_broker()
        if risk is None:
            from app.api.deps import get_risk_manager
            risk = get_risk_manager()
        if client is None:
            client = getattr(broker, "client", None)

        rd = evaluate_readiness(settings)
        creds = bool(rd.kis_key_present and rd.kis_secret_present and rd.kis_account_present)
        # 일일 주문 횟수 한도는 executor 의 gate (max_orders_per_day) 가 강제.
        #   daily_buy_used(일일 매수금액 한도용)는 오늘 DB 기준 유지.
        #   held_symbols(보유)는 *KIS 잔고 = 브로커 진실* 에서 — 전일 캐리오버
        #   포지션을 봇이 인식해 청산/중복매수 가드가 올바로 작동하게 한다.
        _db_held, daily_buy_used = _today_kis_paper_buy_state(db, now)
        held_symbols = await _kis_held_symbols(broker, fallback=_db_held)

        if client is None and not _fn_injected:
            # KIS read-only client 가 없으면 실시세 조회 불가 — mock 대체 금지.
            return _scan_summary(
                mode=mode, symbols_scanned=0, candidates_found=0,
                orders_attempted=0, orders_submitted=0, orders=[], skipped=[],
                reason_code="KIS_MARKET_DATA_UNAVAILABLE",
                reason_message="KIS read-only 시세 client 가 없어 실시간 스캔을 진행하지 않습니다.",
            )

        for symbol in symbols:
            mi, quote = await market_input_fn(
                symbol, client=client, now=now, market_is_open=market_is_open,
            )
            symbols_scanned += 1
            if mi is None:
                skipped.append({"symbol": symbol, "reason_code": quote.status,
                                "price_source": "kis"})
                continue

            council = run_agent_council(
                mi, risk_profile=risk_profile,
                held_position=(symbol in held_symbols),
                min_confidence_floor=float(getattr(settings, "kis_paper_auto_min_confidence", 0.6)),
            )
            final = council.final_action
            if final not in (CouncilAction.BUY, CouncilAction.SELL):
                skipped.append({"symbol": symbol, "reason_code": "HOLD_NO_SIGNAL",
                                "price_source": "kis"})
                continue
            candidates_found += 1

            price = int(quote.price or 0)
            if final == CouncilAction.BUY:
                qty = smoke_qty if smoke else max(1, per_symbol_notional // max(1, price))
            else:
                qty = smoke_qty if smoke else max(1, per_symbol_notional // max(1, price))
            notional = price * qty

            # ── bridge 사전 가드 (RiskManager 전, BUY 신규 진입에만) ──────────
            if final == CouncilAction.BUY:
                if symbol in held_symbols:
                    skipped.append({"symbol": symbol, "reason_code": "DUPLICATE_POSITION_BLOCKED"})
                    continue
                if (len(held_symbols) + orders_submitted) >= max_concurrent:
                    skipped.append({"symbol": symbol, "reason_code": "MAX_CONCURRENT_POSITIONS_REACHED"})
                    continue
                if daily_buy_limit > 0 and (daily_buy_used + notional) > daily_buy_limit:
                    skipped.append({"symbol": symbol, "reason_code": "DAILY_BUY_LIMIT_REACHED"})
                    continue
                if orders_submitted >= max_new_per_tick:
                    skipped.append({"symbol": symbol, "reason_code": "MAX_NEW_POSITIONS_PER_TICK_REACHED"})
                    continue

            # ── PART1-4: SELL 사전 가드 (보유 0 종목 SELL 차단) ───────────────
            # SELL 은 *보유 청산만* — net 보유에 없는 종목은 broker 로 보내지
            # 않는다. (2026-06-01: 청산된 005380 에 SELL 이 반복 전송돼 KIS
            # "잔고부족" 거부 3건 발생. 보유 0 이면 여기서 skip.)
            if final == CouncilAction.SELL and symbol not in held_symbols:
                skipped.append({"symbol": symbol,
                                "reason_code": "SELL_NO_HELD_POSITION",
                                "price_source": "kis"})
                continue

            decision = replace(
                council.to_kis_paper_decision(quantity=int(qty), price=price),
                price_source="kis", price_is_stale=bool(quote.is_stale),
            )

            from app.agents.decision_episode import new_episode_id
            episode_id = new_episode_id()
            orders_attempted += 1
            result = await execute_kis_paper_auto_order(
                db, decision=decision, settings=settings, broker=broker, risk=risk,
                broker_is_kis_paper=_broker_is_kis_paper(broker),
                credentials_present=creds, route_order_fn=route_order_fn, now=now,
                chain_id=episode_id,
            )
            try:
                db.commit()
            except Exception:  # noqa: BLE001
                db.rollback()
            _record_episode_best_effort(
                db, episode_id=episode_id, decision=decision, council=council,
                result=result, market_input=mi, now=now,
            )
            rdict = result.to_dict()
            rdict["symbol"] = symbol
            rdict["price_source"] = "kis"
            rdict["decision_episode_id"] = episode_id
            out_orders.append(rdict)
            if result.submitted:
                orders_submitted += 1
                if final == CouncilAction.BUY:
                    held_symbols.add(symbol)
                    daily_buy_used += notional

        return _scan_summary(
            mode=mode, symbols_scanned=symbols_scanned,
            candidates_found=candidates_found, orders_attempted=orders_attempted,
            orders_submitted=orders_submitted, orders=out_orders, skipped=skipped,
            reason_code="KIS_REALTIME_SCAN_DONE",
            reason_message=(
                f"KIS 실시간 스캔 완료 — {symbols_scanned}종목 조회, "
                f"{candidates_found}후보, {orders_submitted}건 전송."
            ),
        )
    except Exception as exc:  # noqa: BLE001 — 스캔 오류는 loop 를 죽이지 않음.
        try:
            db.rollback()
        except Exception:  # noqa: BLE001
            pass
        _log.warning("[kis-paper-scan] tick error: %s: %s", type(exc).__name__, exc)
        return _scan_summary(
            mode=mode, symbols_scanned=symbols_scanned,
            candidates_found=candidates_found, orders_attempted=orders_attempted,
            orders_submitted=orders_submitted, orders=out_orders, skipped=skipped,
            reason_code="KIS_PAPER_ERROR", reason_message=f"{type(exc).__name__}",
        )
    finally:
        try:
            db.close()
        except Exception:  # noqa: BLE001
            pass


def _scan_summary(
    *, mode, symbols_scanned, candidates_found, orders_attempted,
    orders_submitted, orders, skipped, reason_code, reason_message,
) -> dict[str, Any]:
    """다종목 스캔 집계 결과 — driver / UI 가 읽는 표준 shape."""
    first_submitted = next((o for o in orders if o.get("submitted")), None)
    return {
        "reason_code":      reason_code,
        "reason_message":   reason_message,
        "mode":             mode.mode,
        "price_source":     "kis",
        "market_data_provider": mode.market_data_provider,
        "dry_run":          bool(mode.dry_run),
        "smoke_mode":       bool(mode.smoke_mode),
        "broker_order_enabled": bool(mode.broker_order_enabled),
        "symbols_scanned":  int(symbols_scanned),
        "candidates_found": int(candidates_found),
        "orders_attempted": int(orders_attempted),
        "orders_submitted": int(orders_submitted),
        "submitted":        orders_submitted > 0,
        "broker_order_sent": orders_submitted > 0,
        "broker_order_no":  (first_submitted or {}).get("broker_order_no"),
        "order_status":     (first_submitted or {}).get("order_status"),
        "fill_status":      (first_submitted or {}).get("fill_status"),
        "broker_order_type": "KIS_PAPER",
        "orders":           orders,
        "skipped":          skipped,
        "is_live_authorization": False,
        "is_order_signal":  False,
    }


__all__ = [
    "kis_paper_auto_tick",
    "kis_paper_realtime_scan_tick",
    "build_decision_from_pipeline",
]

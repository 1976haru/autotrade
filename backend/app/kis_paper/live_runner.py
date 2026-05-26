"""KIS 모의 자동매매 *실제* tick runner — Start 버튼이 호출하는 실행 흐름.

engine 의 default `_default_tick_runner` 는 카운터만 갱신(broker 호출 0건)이라
Start(quick/slow) 가 'hollow loop' 였다. 본 모듈은 매 tick 마다:

  1. broker.get_price(symbol)        — read-only KIS 모의 시세 (주문 아님)
  2. run_agent_council(...)          — advisory 4전략 + RiskOfficer 판단
  3. decision.to_kis_paper_decision(...) → execute_kis_paper_auto_order(...)
     → route_order (RiskManager → PermissionGate → OrderExecutor →
       KisBrokerAdapter.place_order[is_paper=True])

절대 안전 (CLAUDE.md 절대 원칙 / #89 invariant 유지):

  - broker 주문 메서드 직접 호출 0건 — execute_kis_paper_auto_order 가 sanctioned
    route_order 경로로만 위임 (정적 grep 가드로 broker 주문 호출 0건 강제).
  - 실전 주문 0건 — KisBrokerAdapter 의 live 주문 경로는 NotImplementedError,
    주문 직전 assert_paper_broker 백스톱, ENABLE_LIVE_TRADING 기본 false.
  - dry_run 기본 True (enable_kis_paper_auto_trading=False 또는
    kis_paper_auto_order_dry_run=True) → 게이트만 통과 검증, KIS 주문 API 0건.
  - 장 시작 전/마감 후 → 권한 게이트의 order-window 검사가 차단 → dry-run/no-order.
  - 최초 검증 수량 = 1주 (quantity_per_order, config override 가능).

본 모듈은 engine 의 *non-counter-only* runner 이므로 route_order 위임이 허용된다
(`test_kis_paper_engine.py::_NO_ROUTE_ORDER_MODULES` 에 engine/scoring/readiness/
__init__ 만 포함 — live_runner 는 sanctioned 위임 모듈).
"""

from __future__ import annotations

import logging
from collections import defaultdict
from typing import Any, Awaitable, Callable

from app.agents.agent_council import StrategyMarketInput, run_agent_council
from app.execution.order_router import route_order
from app.kis_paper.auto_executor import (
    KIS_PAPER_NEEDS_APPROVAL,
    KIS_PAPER_REJECTED,
    KIS_PAPER_SUBMITTED,
    execute_kis_paper_auto_order,
)

_log = logging.getLogger(__name__)

# 최초 검증 기본 종목 (시총 상위 1종목) · 1주. config 로 override 가능.
_DEFAULT_UNIVERSE: tuple[str, ...] = ("005930",)
# council 에 넘길 최근 종가 window — tick 마다 누적 (look-ahead 0, 실시간 시세만).
_RECENT_WINDOW = 6

# route_order 위임 + 권한 게이트가 NEEDS_APPROVAL 로 큐 이동시킨 경우의 reason_code.
_BLOCKED_BY_PERMISSION_GATE = "BLOCKED_BY_PERMISSION_GATE"

# tick 결과 dict — engine._merge_tick_result 가 읽는 카운터 키.
TickResult = dict[str, Any]
TickRunner = Callable[[Any, Any, int], Awaitable[TickResult]]


def _zero_result() -> TickResult:
    return {
        "ai_decisions":         0,
        "ai_buy_signals":       0,
        "ai_sell_signals":      0,
        "ai_hold_signals":      0,
        "orders_attempted":     0,
        "orders_executed":      0,
        "orders_rejected":      0,
        "orders_needs_approval": 0,
        "risk_blocks":          0,
        "fills_observed":       0,
        "unfilled_count":       0,
        "errors":               0,
        "rate_limit_hit":       False,
        "failures":             [],
    }


def _is_rate_limit(exc: Exception) -> bool:
    """KIS 초당 호출 제한(EGW00201) / 일반 rate limit 메시지 감지."""
    msg = f"{type(exc).__name__} {exc}".upper()
    return "EGW00201" in msg or "RATE LIMIT" in msg or "RATE_LIMIT" in msg


def _broker_is_kis_paper(broker: Any) -> bool:
    """broker 가 KIS *모의투자* 어댑터인지 — live KIS / Mock 은 False."""
    return (
        type(broker).__name__ == "KisBrokerAdapter"
        and bool(getattr(broker, "is_paper", False))
    )


def _resolve_universe(settings: Any, override: tuple[str, ...] | None) -> tuple[str, ...]:
    if override:
        return tuple(override)
    raw = getattr(settings, "kis_paper_auto_symbols", None)
    if raw:
        syms = tuple(s.strip() for s in str(raw).split(",") if s.strip())
        if syms:
            return syms
    return _DEFAULT_UNIVERSE


def build_kis_paper_tick_runner(
    *,
    db: Any,
    broker: Any,
    risk: Any,
    settings: Any,
    credentials_present: bool,
    universe: tuple[str, ...] | None = None,
    quantity_per_order: int = 1,
) -> tuple[TickRunner, Callable[[], None]]:
    """실제 KIS 모의 자동매매 tick runner + cleanup 콜백을 만든다.

    runner(engine, mode, tick_idx) -> TickResult dict (engine 카운터 키).
    cleanup() 은 루프 종료 후 db 세션 정리용.

    *주입식* — broker / risk / db 를 caller(route)가 request scope 밖에서
    구성해 넘긴다. engine 은 broker/route_order 를 import 하지 않는다.
    """
    syms = _resolve_universe(settings, universe)
    broker_is_kis_paper = _broker_is_kis_paper(broker)
    qty = max(1, int(quantity_per_order))
    history: dict[str, list[float]] = defaultdict(list)

    async def runner(engine: Any, mode: Any, tick_idx: int) -> TickResult:
        out = _zero_result()
        symbol = syms[tick_idx % len(syms)]

        # 1. read-only 시세 조회 (주문 아님).
        try:
            quote = await broker.get_price(symbol)
            price = int(getattr(quote, "price", 0) or 0)
        except Exception as exc:  # noqa: BLE001
            if _is_rate_limit(exc):
                out["rate_limit_hit"] = True
            out["errors"] = 1
            out["failures"] = [
                f"{symbol} 시세 조회 실패: {type(exc).__name__}: {str(exc)[:120]}"
            ]
            return out

        if price <= 0:
            out["failures"] = [f"{symbol} 시세가 0 이하 — 이번 tick skip"]
            return out

        hist = history[symbol]
        hist.append(float(price))
        if len(hist) > _RECENT_WINDOW:
            del hist[: len(hist) - _RECENT_WINDOW]

        # 2. advisory 판단 (4전략 + RiskOfficer). broker 호출 0건.
        market_input = StrategyMarketInput(
            symbol=symbol,
            current_price=float(price),
            open_price=hist[0],
            recent_closes=tuple(hist),
            market_regime="UNKNOWN",
            regime_decision="ALLOW",
        )
        decision = run_agent_council(market_input)
        out["ai_decisions"] = 1
        action = decision.final_action.value
        if action == "BUY":
            out["ai_buy_signals"] = 1
        elif action == "SELL":
            out["ai_sell_signals"] = 1
        else:
            out["ai_hold_signals"] = 1

        # 3. BUY/SELL 결정만 주문 흐름 진입 — HOLD → 주문 0건.
        kpd = decision.to_kis_paper_decision(quantity=qty, price=price)
        if kpd is None:
            return out

        out["orders_attempted"] = 1
        try:
            result = await execute_kis_paper_auto_order(
                db,
                decision=kpd,
                settings=settings,
                broker=broker,
                risk=risk,
                broker_is_kis_paper=broker_is_kis_paper,
                credentials_present=credentials_present,
                route_order_fn=route_order,
            )
            db.commit()
        except Exception as exc:  # noqa: BLE001 — 루프가 죽지 않게.
            try:
                db.rollback()
            except Exception:  # noqa: BLE001
                pass
            if _is_rate_limit(exc):
                out["rate_limit_hit"] = True
            out["errors"] = 1
            out["failures"] = [
                f"{symbol} 자동주문 실패: {type(exc).__name__}: {str(exc)[:120]}"
            ]
            return out

        rc = result.reason_code
        if rc == KIS_PAPER_SUBMITTED:
            out["orders_executed"] = 1
            if result.fill_status == "FILLED":
                out["fills_observed"] = 1
            elif result.submitted:
                out["unfilled_count"] = 1
        elif rc in (KIS_PAPER_NEEDS_APPROVAL, _BLOCKED_BY_PERMISSION_GATE):
            out["orders_needs_approval"] = 1
        elif rc == KIS_PAPER_REJECTED:
            out["orders_rejected"] = 1
            out["risk_blocks"] = 1
            out["failures"] = [f"{symbol} 주문 거부: {result.reason_message[:120]}"]
        # KIS_PAPER_DRY_RUN_OK / KIS_PAPER_AUTO_DISABLED / window-blocked →
        # attempted 만 카운트 (정상 안전 기본값, 오류 아님 · 실패 메시지 0건).
        return out

    def cleanup() -> None:
        try:
            db.close()
        except Exception:  # noqa: BLE001
            pass

    return runner, cleanup

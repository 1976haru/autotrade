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


def _emit_decision_event(*, symbol, price, action, dry_run, force_dry_run) -> None:
    """매 tick 판단을 RuntimeEvent 로 기록 — HOLD/BUY/SELL 모두 남긴다.

    in-memory event log (log_event) 사용 — broker / DB 세션 0건. 실패해도 루프를
    깨지 않는다. details 에 secret 0건 (symbol/price/action 만).
    """
    try:
        from app.system.event_log import log_event
        log_event(
            level="INFO",
            category="PAPER",
            code=f"KIS_PAPER_DECISION_{action}",
            message=f"{symbol} {action} @ {price} (price_source=kis, dry_run={bool(dry_run)})",
            details={
                "symbol": symbol,
                "price": int(price),
                "price_source": "kis",
                "final_action": action,
                "dry_run": bool(dry_run),
                "force_dry_run": bool(force_dry_run),
                "order_submitted": False,
                "broker_order_sent": False,
                "is_live_authorization": False,
            },
        )
    except Exception:  # noqa: BLE001 — 이벤트 기록 실패가 루프를 깨지 않게.
        pass

_log = logging.getLogger(__name__)

# 최초 검증 기본 종목 (시총 상위 1종목) · 1주. config 로 override 가능.
_DEFAULT_UNIVERSE: tuple[str, ...] = ("005930",)
# council 에 넘길 최근 종가 window — tick 마다 누적 (look-ahead 0, 실시간 시세만).
_RECENT_WINDOW = 6

# route_order 위임 + 권한 게이트가 NEEDS_APPROVAL 로 큐 이동시킨 경우의 reason_code.
_BLOCKED_BY_PERMISSION_GATE = "BLOCKED_BY_PERMISSION_GATE"
# exit_plan 없는 BUY 차단 (auto_permission.KisPaperPermReason.MISSING_EXIT_PLAN) —
# 권한 게이트가 *주문 전* 차단하므로 broker 호출 0건. 운영자 가시성을 위해
# risk_block 으로 카운트.
_MISSING_EXIT_PLAN = "MISSING_EXIT_PLAN"


class _ForceDryRunSettings:
    """settings 래퍼 — `kis_paper_auto_order_dry_run` 만 True 로 강제.

    KIS_PAPER_REAL_MARKET_DRYRUN 모드용. .env 의 `dry_run=false` 를 *파일 변경
    없이* 런타임에서 True 로만 덮어쓴다(더 보수적 방향). 나머지 모든 속성은
    원본 settings 로 위임. 이 래퍼가 적용되면 execute_kis_paper_auto_order 는
    `KIS_PAPER_DRY_RUN_OK` 로 종료 — route_order / broker 주문 호출 0건 보장.
    """

    __slots__ = ("_base",)

    def __init__(self, base: Any):
        object.__setattr__(self, "_base", base)

    def __getattr__(self, name: str) -> Any:
        if name == "kis_paper_auto_order_dry_run":
            return True
        return getattr(object.__getattribute__(self, "_base"), name)

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
    force_dry_run: bool = False,
) -> tuple[TickRunner, Callable[[], None]]:
    """실제 KIS 모의 자동매매 tick runner + cleanup 콜백을 만든다.

    runner(engine, mode, tick_idx) -> TickResult dict (engine 카운터 키).
    cleanup() 은 루프 종료 후 db 세션 정리용.

    *주입식* — broker / risk / db 를 caller(route)가 request scope 밖에서
    구성해 넘긴다. engine 은 broker/route_order 를 import 하지 않는다.

    force_dry_run=True (KIS_PAPER_REAL_MARKET_DRYRUN) — 실 KIS 시세는 그대로
    흘리되 dry_run 을 *강제* True 로 덮어써 BUY/SELL 결정이 나와도 주문을
    전송하지 않고 결정만 기록한다(route_order/broker 주문 호출 0건). .env 미변경.
    """
    syms = _resolve_universe(settings, universe)
    broker_is_kis_paper = _broker_is_kis_paper(broker)
    qty = max(1, int(quantity_per_order))
    history: dict[str, list[float]] = defaultdict(list)
    # force_dry_run 이면 dry_run 강제 True 래퍼 사용 (주문 전송 원천 차단).
    exec_settings = _ForceDryRunSettings(settings) if force_dry_run else settings

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
        # 실 KIS 시세 + 판단 결과 carry (이벤트/UI 표시용). dry_run/주문 불변값 명시.
        out["last_symbol"] = symbol
        out["last_price"] = price
        out["price_source"] = "kis"
        out["last_action"] = action
        out["dry_run"] = bool(force_dry_run) or bool(
            getattr(settings, "kis_paper_auto_order_dry_run", False))
        out["order_submitted"] = False
        out["broker_order_sent"] = False
        out["is_live_authorization"] = False
        if action == "BUY":
            out["ai_buy_signals"] = 1
        elif action == "SELL":
            out["ai_sell_signals"] = 1
        else:
            out["ai_hold_signals"] = 1

        # 매 tick 판단을 RuntimeEvent 로 기록 — HOLD/BUY/SELL 모두 (price_source=kis).
        _emit_decision_event(symbol=symbol, price=price, action=action,
                             dry_run=out["dry_run"], force_dry_run=force_dry_run)

        # 3. BUY/SELL 결정만 주문 흐름 진입 — HOLD → 주문 0건.
        kpd = decision.to_kis_paper_decision(quantity=qty, price=price)
        if kpd is None:
            return out

        out["orders_attempted"] = 1
        try:
            result = await execute_kis_paper_auto_order(
                db,
                decision=kpd,
                settings=exec_settings,
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
        elif rc == _MISSING_EXIT_PLAN:
            # exit_plan 없는 BUY 차단 — 권한 게이트가 주문 전 막음 (broker 호출 0건).
            out["risk_blocks"] = 1
            out["failures"] = [f"{symbol} BUY 차단: exit_plan 없음 (MISSING_EXIT_PLAN)"]
        # KIS_PAPER_DRY_RUN_OK / KIS_PAPER_AUTO_DISABLED / MARKET_CLOSED / window-blocked
        # → attempted 만 카운트 (정상 안전 기본값, 오류 아님 · 실패 메시지 0건).
        return out

    def cleanup() -> None:
        try:
            db.close()
        except Exception:  # noqa: BLE001
            pass

    return runner, cleanup

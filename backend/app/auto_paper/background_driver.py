"""AI Paper background tick driver — *opt-in*, PAPER 전용 자동 반복 실행.

AutoPaperLoop 가 RUNNING 이고 장중이며 운영자가 명시적으로 opt-in flag
(`ENABLE_AI_PAPER_BACKGROUND_TICK=true`) 를 켰을 때만, N초마다 *기존*
run-once 진단 파이프라인(`run_paper_pipeline_once`)을 자동 실행한다.

핵심 원칙 (사용자 요청서 + CLAUDE.md 절대 원칙 1~5):
- **실거래 활성화가 아니다.** Paper / VirtualOrder / diagnostic 파이프라인의
  자동 반복 실행일 뿐. 본 모듈은 `app.brokers.*` / `app.execution.executor` /
  `app.execution.order_router` / `OrderExecutor` / `route_order` 를 *어떤
  경로로도 import 하지 않는다* (정적 grep 가드).
- `enable_live_trading=true` 면 driver 는 *절대 실행되지 않는다*
  (`LIVE_DISABLED_SAFE`).
- 새 매매 로직 0건 — 매 tick 은 기존 `run_paper_pipeline_once` 를 *재사용*.
- "거래 0건은 가능하나 기록 0건은 불가" — 실행/차단 모든 tick 이 reason_code 를
  남긴다 (RuntimeEvent + 실행 tick 은 ledger NO_OP 도 기록).

driver tick 실행 조건 (모두 충족):
  1. ENABLE_AI_PAPER_BACKGROUND_TICK=true
  2. ENABLE_LIVE_TRADING=false
  3. mode ∈ {SIMULATION, PAPER} + (kis_is_paper 또는 SIMULATION)
  4. AutoPaperLoop 상태가 EMERGENCY_STOP 이 아님
  5. market_session 이 OPEN
  6. AutoPaperLoop 상태가 RUNNING
  7. 일일 tick 한도 미초과

조건 불충족 시 tick 미실행 + 차단 reason_code 기록.
"""

from __future__ import annotations

import asyncio
import logging
import re
from dataclasses import dataclass
from datetime import datetime, timezone
from enum import StrEnum
from functools import lru_cache
from typing import Any, Callable, Optional

from app.auto_paper.loop import get_auto_paper_loop
from app.auto_paper.run_once import RunOnceResult, run_paper_pipeline_once
from app.core.modes import OperationMode
from app.scheduler.market_clock import MarketPhase, current_market_phase, to_kst


_log = logging.getLogger("autotrade.auto_paper.bg_tick")


class BackgroundTickReason(StrEnum):
    """background tick 실행/차단 사유 라벨 — frontend / API 가 그대로 emit.

    BUY/SELL/HOLD 값 0개. PAPER_ONLY_DRIVER 는 비-paper-safe 모드 차단,
    실행 tick 의 last_reason_code 는 *파이프라인 result_code* 를 carry.
    """

    TICK_EXECUTED                = "TICK_EXECUTED"   # 게이트 통과 (실제 reason 은 pipeline)
    BACKGROUND_TICK_DISABLED     = "BACKGROUND_TICK_DISABLED"
    LIVE_DISABLED_SAFE           = "LIVE_DISABLED_SAFE"
    PAPER_ONLY_DRIVER            = "PAPER_ONLY_DRIVER"
    EMERGENCY_STOP_ENABLED       = "EMERGENCY_STOP_ENABLED"
    MARKET_CLOSED                = "MARKET_CLOSED"
    AUTO_LOOP_NOT_RUNNING        = "AUTO_LOOP_NOT_RUNNING"
    BACKGROUND_TICK_MAX_PER_DAY  = "BACKGROUND_TICK_MAX_PER_DAY"
    BACKGROUND_TICK_ERROR        = "BACKGROUND_TICK_ERROR"


_REASON_MESSAGE_KO: dict[str, str] = {
    BackgroundTickReason.TICK_EXECUTED:
        "자동 tick 실행 — Paper 판단 파이프라인을 1회 수행했습니다.",
    BackgroundTickReason.BACKGROUND_TICK_DISABLED:
        "자동 tick driver 비활성 — 현재는 run-once 진단만 수동 실행됩니다.",
    BackgroundTickReason.LIVE_DISABLED_SAFE:
        "실거래(LIVE)가 활성화되어 있어 Paper 자동 tick driver 를 실행하지 않습니다.",
    BackgroundTickReason.PAPER_ONLY_DRIVER:
        "본 driver 는 PAPER / SIMULATION 모드 전용입니다 (현재 모드에서는 미실행).",
    BackgroundTickReason.EMERGENCY_STOP_ENABLED:
        "긴급정지 ON — driver tick 차단.",
    BackgroundTickReason.MARKET_CLOSED:
        "장 시작 전 / 장 종료 / 휴장 — driver tick 대기 중.",
    BackgroundTickReason.AUTO_LOOP_NOT_RUNNING:
        "AutoPaperLoop 정지 상태 — driver tick 중단.",
    BackgroundTickReason.BACKGROUND_TICK_MAX_PER_DAY:
        "오늘 자동 tick 한도에 도달하여 추가 tick 을 실행하지 않습니다.",
    BackgroundTickReason.BACKGROUND_TICK_ERROR:
        "자동 tick 실행 중 오류가 발생했습니다 — 다음 주기에 재시도합니다 (실거래 영향 0건).",
}


def reason_message_ko(code: str) -> str:
    return _REASON_MESSAGE_KO.get(code, code)


# secret 추정 패턴 — 에러 메시지를 RuntimeEvent 로 남기기 전 마스킹.
_SECRET_PATTERNS: tuple[re.Pattern[str], ...] = (
    re.compile(r"sk-[A-Za-z0-9_\-]{12,}"),
    re.compile(r"sk-ant-[A-Za-z0-9_\-]{12,}"),
    re.compile(r"ghp_[A-Za-z0-9]{20,}"),
    re.compile(r"Bearer\s+[A-Za-z0-9._\-]{12,}"),
    re.compile(r"PST[A-Za-z0-9]{20,}"),
    re.compile(r"\b\d{6,}-\d{2,}\b"),     # 한국 계좌번호 형태.
)


def _redact_secret(text: str) -> str:
    """에러 메시지의 secret 추정 토큰을 [REDACTED] 로 치환."""
    out = str(text)
    for pat in _SECRET_PATTERNS:
        out = pat.sub("[REDACTED]", out)
    return out[:500]


async def _trailing_shadow_tick(now: datetime) -> None:
    """트레일링 1·2단계(측정+섀도) 1틱 — ★거래 동작에 영향 0(관찰만, 매도 0).

    get_positions(브로커 어댑터 *20s 캐시* — 스캔이 직전에 호출했으면 캐시 히트, EGW 부하 ~0)
    로 보유 종목을 읽어 최고가(hwm) 영속 추적 + "트레일링이었다면 청산했을지" 섀도 로그.
    driver_bridge/주문경로 미접촉 — 독립 측정 계층.
    """
    from app.api.deps import get_broker
    from app.db.session import SessionLocal
    from app.positions.high_watermark import update_and_shadow
    broker = get_broker()
    if broker is None or not hasattr(broker, "get_positions"):
        return
    positions = await broker.get_positions()
    db = SessionLocal()
    try:
        update_and_shadow(db, positions, now=now)
    finally:
        db.close()


def _label_outcomes_tick(now: datetime) -> None:
    """청산 round-trip → 진입 episode 에 outcome 라벨링 1틱 — ★거래 동작에 영향 0(집계만).

    학습(설계 A)의 기법별 expectancy 산출이 가능해지도록, 라이브 청산 거래에 사후 성과를
    붙인다. 멱등 + 최근분만(소급 X). 학습 자동조정은 여전히 0.
    """
    from app.analytics.outcome_labeler import label_closed_round_trips
    from app.db.session import SessionLocal
    db = SessionLocal()
    try:
        label_closed_round_trips(db, now=now)
    finally:
        db.close()


@dataclass(frozen=True)
class DriverTickResult:
    """단일 tick 결과 — *advisory*, broker 호출 0건."""

    executed:            bool
    reason_code:         str
    reason_message:      str
    cycle_count:         int
    pipeline_result_code: str | None = None
    recorded_event:      bool = False
    # tick 실행 모드 + 모의 체결 결과 carry.
    tick_mode:           str = "DIAGNOSTIC_DRY_RUN"   # DIAGNOSTIC_DRY_RUN / SIMULATED_TRADE
    simulated_fills_enabled: bool = False
    order_created:       bool = False
    order_id:            int | None = None
    fill_status:         str | None = None            # FILLED / None
    quantity:            int = 0
    notional_krw:        int = 0
    cash_before:         int | None = None
    cash_after:          int | None = None
    position_quantity:   int = 0
    # KIS_PAPER_AUTO carry.
    broker_order_no:     str | None = None
    order_status:        str | None = None

    # 절대 invariant.
    is_order_signal:       bool = False
    is_live_authorization: bool = False
    broker_order_sent:     bool = False

    def __post_init__(self) -> None:
        # is_order_signal / is_live_authorization 는 *항상* False (실거래/주문
        # 신호 아님). broker_order_sent 는 KIS_PAPER_AUTO 모드에서 *모의* 주문이
        # 실제 전송되면 True 가 될 수 있다 (한투 모의투자 API — 실거래 아님);
        # DIAGNOSTIC / SIMULATED_TRADE 모드에서는 False.
        if self.is_order_signal is not False:
            raise ValueError("DriverTickResult.is_order_signal must be False")
        if self.is_live_authorization is not False:
            raise ValueError("DriverTickResult.is_live_authorization must be False")

    def to_dict(self) -> dict[str, Any]:
        return {
            "executed":             bool(self.executed),
            "reason_code":          self.reason_code,
            "reason_message":       self.reason_message,
            "cycle_count":          int(self.cycle_count),
            "pipeline_result_code": self.pipeline_result_code,
            "recorded_event":       bool(self.recorded_event),
            "tick_mode":            self.tick_mode,
            "simulated_fills_enabled": bool(self.simulated_fills_enabled),
            "order_created":        bool(self.order_created),
            "order_id":             self.order_id,
            "fill_status":          self.fill_status,
            "quantity":             int(self.quantity),
            "notional_krw":         int(self.notional_krw),
            "cash_before":          self.cash_before,
            "cash_after":           self.cash_after,
            "position_quantity":    int(self.position_quantity),
            "broker_order_no":      self.broker_order_no,
            "order_status":         self.order_status,
            "is_order_signal":       self.is_order_signal,
            "is_live_authorization": self.is_live_authorization,
            "broker_order_sent":     self.broker_order_sent,
        }


_PAPER_SAFE_MODES = frozenset({OperationMode.SIMULATION, OperationMode.PAPER})


class BackgroundTickDriver:
    """AutoPaperLoop 위에서 N초마다 run-once 파이프라인을 자동 실행하는 driver.

    asyncio task 기반 — FastAPI lifespan 이 `start()` / `stop()` 으로 관리.
    모든 의존성은 주입 가능 (테스트 용이성). 본 클래스는 broker / OrderExecutor /
    route_order 를 import 하지 않는다.
    """

    def __init__(
        self,
        *,
        settings_provider: Optional[Callable[[], Any]] = None,
        loop_provider:     Optional[Callable[[], Any]] = None,
        pipeline_runner:   Optional[Callable[..., RunOnceResult]] = None,
        event_sink:        Optional[Callable[..., None]] = None,
        now_provider:      Optional[Callable[[], datetime]] = None,
        # simulated trade mode 의존성 — 모두 주입 가능 (테스트 용이성).
        # session_factory(): DB session 반환 (default app.db.session.SessionLocal).
        # trade_flow_runner(db, ...): execute_paper_trade_flow 호환.
        # risk_check_builder(risk_manager, db, available_cash_krw): paper risk_check.
        # risk_manager_provider(): RiskManager 인스턴스.
        session_factory:   Optional[Callable[[], Any]] = None,
        trade_flow_runner: Optional[Callable[..., Any]] = None,
        risk_check_builder: Optional[Callable[..., Any]] = None,
        risk_manager_provider: Optional[Callable[[], Any]] = None,
        # KIS_PAPER_AUTO mode 의존성 — broker / route_order 를 import 하지 않기
        # 위해 *주입된 async 콜러블* 로만 KIS 주문 흐름을 호출한다. 미주입 시
        # `app.kis_paper.driver_bridge.kis_paper_auto_tick` 를 lazy import.
        kis_auto_tick_fn:  Optional[Callable[..., Any]] = None,
        # 개장 전(PRE_OPEN) 1회 universe 캐시 warmup 콜러블 — 미주입 시
        # driver_bridge.premarket_warmup_tick lazy import(broker import 회피).
        premarket_warmup_fn: Optional[Callable[..., Any]] = None,
    ):
        self._settings_provider = settings_provider
        self._loop_provider = loop_provider
        self._pipeline_runner = pipeline_runner or run_paper_pipeline_once
        self._event_sink = event_sink
        self._now_provider = now_provider or (lambda: datetime.now(timezone.utc))
        self._session_factory = session_factory
        self._trade_flow_runner = trade_flow_runner
        self._risk_check_builder = risk_check_builder
        self._risk_manager_provider = risk_manager_provider
        self._kis_auto_tick_fn = kis_auto_tick_fn
        self._premarket_warmup_fn = premarket_warmup_fn
        self._warmup_date: str | None = None    # 당일 1회 warmup 가드 (KST date)
        # 상태.
        self._task: asyncio.Task | None = None
        self._stop_event: asyncio.Event | None = None
        self._last_tick_at: str | None = None
        self._last_reason_code: str | None = None
        self._last_reason_message: str | None = None
        self._last_pipeline_result_code: str | None = None
        self._tick_count_today: int = 0
        self._tick_count_date: str | None = None    # KST date (YYYY-MM-DD)
        self._last_emitted_block_reason: str | None = None
        # simulated trade mode 의 최근 결과 carry (run-readiness / UI 표시용).
        self._last_tick_mode: str = "DIAGNOSTIC_DRY_RUN"
        self._last_order_id: int | None = None
        self._last_fill_status: str | None = None
        self._last_quantity: int = 0
        self._last_notional_krw: int = 0
        self._last_cash_before: int | None = None
        self._last_cash_after: int | None = None
        self._last_position_quantity: int = 0
        # KIS_PAPER_AUTO 최근 주문 결과 carry.
        self._last_broker_order_no: str | None = None
        self._last_order_status: str | None = None

    # ── settings / loop accessors ──────────────────────────────────────────

    def _settings(self):
        if self._settings_provider is not None:
            return self._settings_provider()
        from app.core.config import get_settings
        return get_settings()

    def _loop(self):
        if self._loop_provider is not None:
            return self._loop_provider()
        return get_auto_paper_loop()

    def _open_session(self):
        """DB session 1개 열기 — caller 가 close 책임. lazy import (broker 무관)."""
        if self._session_factory is not None:
            return self._session_factory()
        from app.db.session import SessionLocal
        return SessionLocal()

    def _trade_flow(self):
        if self._trade_flow_runner is not None:
            return self._trade_flow_runner
        from app.auto_paper.paper_trade_flow import execute_paper_trade_flow
        return execute_paper_trade_flow

    def _build_risk_check(self, db, available_cash_krw: int):
        """주입된 RiskManager 로 paper risk_check 콜러블 생성 (broker 무접촉)."""
        try:
            if self._risk_check_builder is not None:
                rm = (self._risk_manager_provider() if self._risk_manager_provider
                      else None)
                return self._risk_check_builder(rm, db, available_cash_krw)
            from app.auto_paper.paper_risk_check import build_paper_risk_check
            if self._risk_manager_provider is not None:
                rm = self._risk_manager_provider()
            else:
                from app.api.deps import get_risk_manager
                rm = get_risk_manager()
            return build_paper_risk_check(rm, db, available_cash_krw)
        except Exception:  # noqa: BLE001 — risk_check 미가용 시 paper 가드만 적용.
            return None

    def _emit(self, *, level: str, code: str, message: str,
              details: dict[str, Any] | None = None) -> bool:
        """RuntimeEvent 기록 — 실패해도 driver 를 깨지 않는다."""
        try:
            if self._event_sink is not None:
                self._event_sink(level=level, category="PAPER", code=code,
                                 message=message, details=details or {})
            else:
                from app.system.event_log import log_event
                log_event(level=level, category="PAPER", code=code,
                          message=message, details=details or {})
            return True
        except Exception:  # noqa: BLE001
            return False

    # ── 일일 카운터 ──────────────────────────────────────────────────────────

    def _roll_day_if_needed(self, now: datetime) -> str:
        today = to_kst(now).strftime("%Y-%m-%d")
        if self._tick_count_date != today:
            self._tick_count_date = today
            self._tick_count_today = 0
        return today

    # ── gate 평가 ────────────────────────────────────────────────────────────

    def evaluate_gate(self, now: datetime | None = None) -> tuple[bool, str, str]:
        """tick 실행 가능 여부 + 사유. (allowed, reason_code, reason_message).

        allowed=True 면 reason_code=TICK_EXECUTED (실제 사유는 pipeline 이 결정).
        """
        if now is None:
            now = self._now_provider()
        s = self._settings()

        if not bool(getattr(s, "enable_ai_paper_background_tick", False)):
            return self._gate(False, BackgroundTickReason.BACKGROUND_TICK_DISABLED)
        if bool(getattr(s, "enable_live_trading", False)):
            return self._gate(False, BackgroundTickReason.LIVE_DISABLED_SAFE)

        mode = getattr(s, "default_mode", None)
        kis_is_paper = bool(getattr(s, "kis_is_paper", True))
        paper_safe = (
            mode in _PAPER_SAFE_MODES
            and (kis_is_paper or mode == OperationMode.SIMULATION)
        )
        if not paper_safe:
            return self._gate(False, BackgroundTickReason.PAPER_ONLY_DRIVER)

        # loop.status(now) 는 lazy market-phase promote/demote 를 수행.
        state = self._loop().status(now=now).state
        if state == "EMERGENCY_STOP":
            return self._gate(False, BackgroundTickReason.EMERGENCY_STOP_ENABLED)

        phase = current_market_phase(now)
        if phase != MarketPhase.OPEN:
            return self._gate(False, BackgroundTickReason.MARKET_CLOSED)

        if state != "RUNNING":
            return self._gate(False, BackgroundTickReason.AUTO_LOOP_NOT_RUNNING)

        max_per_day = int(getattr(s, "ai_paper_tick_max_per_day", 0) or 0)
        if max_per_day > 0:
            self._roll_day_if_needed(now)
            if self._tick_count_today >= max_per_day:
                return self._gate(
                    False, BackgroundTickReason.BACKGROUND_TICK_MAX_PER_DAY,
                )

        return self._gate(True, BackgroundTickReason.TICK_EXECUTED)

    @staticmethod
    def _gate(allowed: bool, code: BackgroundTickReason) -> tuple[bool, str, str]:
        return allowed, code.value, reason_message_ko(code.value)

    # ── tick 1회 ─────────────────────────────────────────────────────────────

    def tick_once(self, now: datetime | None = None) -> DriverTickResult:
        """단일 tick — gate 평가 후 통과 시 run-once 파이프라인 1회 실행.

        broker / route_order / OrderExecutor 호출 0건. 차단 / 실행 *모든* 경우
        reason_code 를 기록한다 (RuntimeEvent).
        """
        if now is None:
            now = self._now_provider()
        s = self._settings()
        loop = self._loop()
        self._last_tick_at = now.isoformat()

        allowed, reason_code, reason_msg = self.evaluate_gate(now)
        self._last_reason_code = reason_code
        self._last_reason_message = reason_msg

        if not allowed:
            # 차단 — pipeline 미실행, cycle 미증가. reason 기록.
            recorded = self._emit(
                level="INFO", code=f"AI_PAPER_TICK_{reason_code}",
                message=reason_msg, details={"executed": False},
            )
            self._last_pipeline_result_code = None
            return DriverTickResult(
                executed=False, reason_code=reason_code, reason_message=reason_msg,
                cycle_count=int(loop.status(now=now).cycle_count),
                recorded_event=recorded,
            )

        # 게이트 통과 — cycle 증가 (loop.tick) + run-once 파이프라인.
        dry_run = bool(getattr(s, "ai_paper_tick_dry_run", True))
        provider = str(getattr(s, "market_data_provider", "mock"))
        force_mock = provider.strip().lower() == "mock"
        cycle = 0
        try:
            snap = loop.tick()   # cycle += 1, last_tick_at 갱신 (RUNNING 보장됨)
            cycle = int(snap.cycle_count)
        except Exception as exc:  # noqa: BLE001 — loop race (다른 thread stop 등).
            reason = BackgroundTickReason.AUTO_LOOP_NOT_RUNNING
            self._last_reason_code = reason.value
            self._last_reason_message = reason_message_ko(reason.value)
            self._emit(level="WARN", code=f"AI_PAPER_TICK_{reason.value}",
                       message=f"{type(exc).__name__}: {exc}",
                       details={"executed": False})
            return DriverTickResult(
                executed=False, reason_code=reason.value,
                reason_message=self._last_reason_message,
                cycle_count=int(loop.status(now=now).cycle_count),
                recorded_event=True,
            )

        # tick 실행 모드 분리:
        #  - dry_run=True 또는 allow_simulated_fills=False → DIAGNOSTIC_DRY_RUN
        #    (기존 run_paper_pipeline_once — 판단/사유만, 주문/체결/현금 반영 0건)
        #  - dry_run=False AND allow_simulated_fills=True → SIMULATED_TRADE
        #    (execute_paper_trade_flow — VirtualOrder + Paper 체결 + 현금/포지션)
        allow_fills = bool(getattr(s, "ai_paper_allow_simulated_fills", False))
        simulated = (not dry_run) and allow_fills
        self._tick_count_today += 1

        if not simulated:
            return self._tick_diagnostic(
                cycle=cycle, force_mock=force_mock, provider=provider,
                dry_run=dry_run, allow_fills=allow_fills, now=now,
            )
        return self._tick_simulated_trade(
            cycle=cycle, force_mock=force_mock, provider=provider,
            allow_fills=allow_fills, now=now,
        )

    # ── 모드 A: 진단 dry-run ──────────────────────────────────────────────────

    def _tick_diagnostic(self, *, cycle, force_mock, provider, dry_run,
                         allow_fills, now) -> DriverTickResult:
        self._last_tick_mode = "DIAGNOSTIC_DRY_RUN"
        result: RunOnceResult = self._pipeline_runner(
            symbol=None, force_mock_market_data=force_mock, dry_run=dry_run,
            market_data_provider=provider, record=True, now=now,
        )
        self._last_pipeline_result_code = result.result_code.value
        self._last_reason_code = result.result_code.value
        self._last_reason_message = result.reason_message
        self._last_order_id = None
        self._last_fill_status = None
        self._last_quantity = 0
        self._last_notional_krw = 0
        self._last_cash_before = None
        self._last_cash_after = None
        recorded = self._emit(
            level="INFO", code=f"AI_PAPER_TICK_{result.result_code.value}",
            message=result.reason_message,
            details={"executed": True, "cycle": cycle, "tick_mode": "DIAGNOSTIC_DRY_RUN",
                     "result_code": result.result_code.value,
                     "dry_run": True, "broker_order_sent": False},
        )
        return DriverTickResult(
            executed=True, reason_code=result.result_code.value,
            reason_message=result.reason_message, cycle_count=cycle,
            pipeline_result_code=result.result_code.value, recorded_event=recorded,
            tick_mode="DIAGNOSTIC_DRY_RUN", simulated_fills_enabled=allow_fills,
        )

    # ── 모드 B: Paper 모의 체결 ────────────────────────────────────────────────

    def _tick_simulated_trade(self, *, cycle, force_mock, provider,
                              allow_fills, now) -> DriverTickResult:
        self._last_tick_mode = "SIMULATED_TRADE"
        db = None
        try:
            db = self._open_session()
            from app.auto_paper.capital_state import get_capital_state
            avail = int(get_capital_state().snapshot().available_cash_krw)
            risk_check = self._build_risk_check(db, avail)
            slippage = float(getattr(self._settings(), "ai_paper_fill_slippage_bps", 0.0))
            flow = self._trade_flow()(
                db,
                symbol=None, force_mock_market_data=force_mock,
                dry_run=False, allow_simulated_fills=True,
                market_data_provider=provider, slippage_bps=slippage,
                risk_check=risk_check, now=now,
            )
            try:
                db.commit()
            except Exception:  # noqa: BLE001
                db.rollback()
            # 포지션 수량 (FIFO) carry.
            pos_qty = 0
            try:
                from app.virtual.position_engine import compute_open_positions
                for p in compute_open_positions(db, now=now):
                    if p.symbol == flow.symbol:
                        pos_qty += int(p.quantity)
            except Exception:  # noqa: BLE001
                pos_qty = 0
        except Exception as exc:  # noqa: BLE001 — trade flow 오류는 task 를 죽이지 않음.
            if db is not None:
                try:
                    db.rollback()
                except Exception:  # noqa: BLE001
                    pass
            safe = _redact_secret(f"{type(exc).__name__}: {exc}")
            reason = BackgroundTickReason.BACKGROUND_TICK_ERROR
            self._last_reason_code = reason.value
            self._last_reason_message = reason_message_ko(reason.value)
            self._last_pipeline_result_code = None
            self._emit(level="WARN", code=f"AI_PAPER_TICK_{reason.value}",
                       message=safe,
                       details={"executed": True, "cycle": cycle,
                                "tick_mode": "SIMULATED_TRADE", "broker_order_sent": False})
            return DriverTickResult(
                executed=True, reason_code=reason.value,
                reason_message=self._last_reason_message, cycle_count=cycle,
                recorded_event=True, tick_mode="SIMULATED_TRADE",
                simulated_fills_enabled=allow_fills,
            )
        finally:
            if db is not None:
                try:
                    db.close()
                except Exception:  # noqa: BLE001
                    pass

        self._last_pipeline_result_code = flow.run_once_result_code
        self._last_reason_code = flow.reason_code
        self._last_reason_message = flow.reason_message
        self._last_order_id = flow.order_id
        self._last_fill_status = ("FILLED" if flow.filled else None)
        self._last_quantity = int(flow.filled_quantity or flow.quantity or 0)
        self._last_notional_krw = int(flow.fill_notional_krw or flow.notional_krw or 0)
        self._last_cash_before = flow.cash_before
        self._last_cash_after = flow.cash_after
        self._last_position_quantity = pos_qty
        recorded = self._emit(
            level="INFO", code=f"AI_PAPER_TICK_{flow.reason_code}",
            message=flow.reason_message,
            details={"executed": True, "cycle": cycle, "tick_mode": "SIMULATED_TRADE",
                     "reason_code": flow.reason_code, "order_id": flow.order_id,
                     "filled": bool(flow.filled), "broker_order_sent": False},
        )
        return DriverTickResult(
            executed=True, reason_code=flow.reason_code,
            reason_message=flow.reason_message, cycle_count=cycle,
            pipeline_result_code=flow.run_once_result_code, recorded_event=recorded,
            tick_mode="SIMULATED_TRADE", simulated_fills_enabled=allow_fills,
            order_created=bool(flow.order_created), order_id=flow.order_id,
            fill_status=("FILLED" if flow.filled else None),
            quantity=int(flow.filled_quantity or flow.quantity or 0),
            notional_krw=int(flow.fill_notional_krw or flow.notional_krw or 0),
            cash_before=flow.cash_before, cash_after=flow.cash_after,
            position_quantity=pos_qty,
        )

    # ── 모드 C: KIS Paper Auto (한투 모의투자 API 주문) ────────────────────────

    def _kis_auto_tick_callable(self):
        """주입된 KIS tick 콜러블 — 미주입 시 bridge lazy import (broker 무관)."""
        if self._kis_auto_tick_fn is not None:
            return self._kis_auto_tick_fn
        from app.kis_paper.driver_bridge import kis_paper_auto_tick
        return kis_paper_auto_tick

    def _premarket_warmup_callable(self):
        """주입된 warmup 콜러블 — 미주입 시 bridge lazy import (broker 무관)."""
        if self._premarket_warmup_fn is not None:
            return self._premarket_warmup_fn
        from app.kis_paper.driver_bridge import premarket_warmup_tick
        return premarket_warmup_tick

    async def _maybe_premarket_warmup(self, now: datetime) -> None:
        """PRE_OPEN 이고 오늘 아직 안 했으면 universe 캐시 1회 warmup. ★주문 0.

        게이트(MARKET_CLOSED)로 tick 은 미실행이라, warmup 은 그와 별개로 개장 전
        실행돼 장 초반 콜드 스파이크를 평탄화한다. 실패는 무시(lazy 폴백)."""
        try:
            if current_market_phase(now) != MarketPhase.PRE_OPEN:
                return
            today = to_kst(now).strftime("%Y-%m-%d")
            if self._warmup_date == today:
                return
            ret = self._premarket_warmup_callable()(now=now)
            res = (await ret) if asyncio.iscoroutine(ret) else ret
            self._warmup_date = today
            _log.info("[bg-tick] premarket warmup 완료: %s", res)
        except Exception as exc:  # noqa: BLE001 — warmup 실패는 거래 무관.
            _log.warning("[bg-tick] premarket warmup 실패(무시): %s: %s",
                         type(exc).__name__, exc)

    async def kis_paper_tick(self, now: datetime | None = None) -> DriverTickResult:
        """KIS_PAPER_AUTO 1 tick — 게이트 통과 시 주입 콜러블로 KIS 주문 흐름 위임.

        broker / route_order 직접 호출 0건 (콜러블 격리). 차단/실행 모든 경우
        reason 기록. 실거래 0건.
        """
        if now is None:
            now = self._now_provider()
        self._last_tick_at = now.isoformat()
        self._last_tick_mode = "KIS_PAPER_AUTO"

        allowed, reason_code, reason_msg = self.evaluate_gate(now)
        if not allowed:
            self._last_reason_code = reason_code
            self._last_reason_message = reason_msg
            self._emit(level="INFO", code=f"KIS_PAPER_TICK_{reason_code}",
                       message=reason_msg,
                       details={"executed": False, "tick_mode": "KIS_PAPER_AUTO"})
            return DriverTickResult(
                executed=False, reason_code=reason_code, reason_message=reason_msg,
                cycle_count=int(self._loop().status(now=now).cycle_count),
                recorded_event=True, tick_mode="KIS_PAPER_AUTO",
            )

        # cycle 증가.
        try:
            cycle = int(self._loop().tick().cycle_count)
        except Exception:  # noqa: BLE001
            cycle = int(self._loop().status(now=now).cycle_count)

        self._tick_count_today += 1
        try:
            res = await self._kis_auto_tick_callable()(now=now)
        except Exception as exc:  # noqa: BLE001 — KIS tick 오류는 loop 를 죽이지 않음.
            safe = _redact_secret(f"{type(exc).__name__}: {exc}")
            self._last_reason_code = "KIS_PAPER_ERROR"
            self._last_reason_message = safe
            self._emit(level="WARN", code="KIS_PAPER_TICK_ERROR", message=safe,
                       details={"executed": True, "tick_mode": "KIS_PAPER_AUTO"})
            return DriverTickResult(
                executed=True, reason_code="KIS_PAPER_ERROR", reason_message=safe,
                cycle_count=cycle, recorded_event=True, tick_mode="KIS_PAPER_AUTO",
            )

        res = dict(res or {})
        rc = str(res.get("reason_code") or "KIS_PAPER_ERROR")
        self._last_reason_code = rc
        self._last_reason_message = str(res.get("reason_message") or rc)
        self._last_broker_order_no = res.get("broker_order_no")
        self._last_order_status = res.get("order_status")
        self._last_fill_status = res.get("fill_status")
        self._last_quantity = int(res.get("quantity") or 0)
        self._last_notional_krw = int(res.get("notional_krw") or 0)
        recorded = self._emit(
            level="INFO", code=f"KIS_PAPER_TICK_{rc}",
            message=self._last_reason_message,
            details={"executed": True, "cycle": cycle, "tick_mode": "KIS_PAPER_AUTO",
                     "reason_code": rc, "broker_order_no": res.get("broker_order_no"),
                     "submitted": bool(res.get("submitted")),
                     "broker_order_sent": bool(res.get("broker_order_sent")),
                     "is_live_authorization": False},
        )
        # 트레일링 1·2단계(측정+섀도) — ★봇 매도 결정·거래 동작에 영향 0(hwm 기록 + 섀도 로그만).
        #   스캔/주문이 모두 끝난 *뒤* 관찰만. 실패해도 tick/거래 불변(try/except 격리).
        try:
            await _trailing_shadow_tick(now)
        except Exception:  # noqa: BLE001 — 측정 실패는 거래에 영향 0.
            pass
        # 학습용 청산 outcome 라벨링(집계) — ★거래 동작에 영향 0(round-trip→진입 episode 기록만).
        try:
            _label_outcomes_tick(now)
        except Exception:  # noqa: BLE001
            pass
        return DriverTickResult(
            executed=True, reason_code=rc, reason_message=self._last_reason_message,
            cycle_count=cycle, recorded_event=recorded, tick_mode="KIS_PAPER_AUTO",
            fill_status=res.get("fill_status"),
            quantity=int(res.get("quantity") or 0),
            notional_krw=int(res.get("notional_krw") or 0),
            broker_order_no=res.get("broker_order_no"),
            order_status=res.get("order_status"),
            broker_order_sent=bool(res.get("broker_order_sent")),
        )

    # ── async run loop ───────────────────────────────────────────────────────

    async def run_forever(self) -> None:
        """asyncio task body — interval 마다 tick. stop() 으로 취소.

        ENABLE_KIS_PAPER_AUTO_TRADING=true 면 KIS_PAPER_AUTO async tick,
        아니면 sync tick_once (diagnostic / simulated trade).
        """
        assert self._stop_event is not None
        _log.info("[bg-tick] driver loop started")
        try:
            while not self._stop_event.is_set():
                # 개장 전(PRE_OPEN) 당일 1회 universe 캐시 warmup — 콜드 스파이크 평탄화.
                #   게이트와 무관(주문 0). 실패해도 loop 무영향.
                await self._maybe_premarket_warmup(self._now_provider())
                try:
                    if bool(getattr(self._settings(), "enable_kis_paper_auto_trading", False)):
                        await self.kis_paper_tick()
                    else:
                        self.tick_once()
                except Exception as exc:  # noqa: BLE001 — tick 실패가 loop 를 죽이지 않음.
                    _log.warning("[bg-tick] tick raised: %s: %s",
                                 type(exc).__name__, exc)
                interval = max(1, int(getattr(
                    self._settings(), "ai_paper_tick_interval_seconds", 30) or 30))
                try:
                    await asyncio.wait_for(self._stop_event.wait(), timeout=interval)
                except asyncio.TimeoutError:
                    pass
        finally:
            _log.info("[bg-tick] driver loop exited")

    def start(self) -> bool:
        """asyncio task 시작 — flag OFF 면 시작하지 않고 False 반환.

        flag 가 OFF 여도 *task 를 만들지 않는다* (자원 절약). 운영자가 런타임에
        flag 를 켜고 backend 를 재시작하면 lifespan 이 다시 start() 호출.
        """
        s = self._settings()
        if not bool(getattr(s, "enable_ai_paper_background_tick", False)):
            _log.info("[bg-tick] driver disabled (flag OFF) — not starting")
            return False
        if bool(getattr(s, "enable_live_trading", False)):
            _log.warning("[bg-tick] enable_live_trading=true — driver NOT started "
                         "(LIVE_DISABLED_SAFE)")
            return False
        if self._task is not None and not self._task.done():
            return True   # 이미 실행 중.
        self._stop_event = asyncio.Event()
        self._task = asyncio.create_task(self.run_forever())
        _log.info("[bg-tick] driver task created")
        return True

    async def stop(self) -> None:
        """task 안전 취소 — shutdown / 테스트 cleanup 용."""
        if self._stop_event is not None:
            self._stop_event.set()
        if self._task is not None:
            self._task.cancel()
            try:
                await self._task
            except (asyncio.CancelledError, Exception):  # noqa: BLE001
                pass
            self._task = None
        self._stop_event = None

    @property
    def is_running(self) -> bool:
        return self._task is not None and not self._task.done()

    # ── status (run-readiness 용) ────────────────────────────────────────────

    def status(self) -> dict[str, Any]:
        s = self._settings()
        enabled = bool(getattr(s, "enable_ai_paper_background_tick", False))
        return {
            "enabled":          enabled,
            "running":          self.is_running,
            "interval_seconds": int(getattr(s, "ai_paper_tick_interval_seconds", 30)),
            "dry_run":          bool(getattr(s, "ai_paper_tick_dry_run", True)),
            "max_per_day":      int(getattr(s, "ai_paper_tick_max_per_day", 0) or 0),
            "allow_simulated_fills": bool(getattr(s, "ai_paper_allow_simulated_fills", False)),
            "tick_count_today": int(self._tick_count_today),
            "last_tick_at":     self._last_tick_at,
            "last_reason_code": self._last_reason_code,
            "last_reason_message": self._last_reason_message,
            "last_pipeline_result_code": self._last_pipeline_result_code,
            # tick 모드 + 최근 모의 체결 결과 carry (UI / run-readiness 표시).
            "tick_mode":              self._last_tick_mode,
            "simulated_fills_enabled": bool(getattr(s, "ai_paper_allow_simulated_fills", False)),
            "last_order_id":          self._last_order_id,
            "last_fill_status":       self._last_fill_status,
            "last_quantity":          int(self._last_quantity),
            "last_notional_krw":      int(self._last_notional_krw),
            "last_cash_before":       self._last_cash_before,
            "last_cash_after":        self._last_cash_after,
            "last_position_quantity": int(self._last_position_quantity),
            # KIS_PAPER_AUTO carry.
            "kis_paper_auto_enabled": bool(getattr(s, "enable_kis_paper_auto_trading", False)),
            "kis_paper_auto_dry_run": bool(getattr(s, "kis_paper_auto_order_dry_run", True)),
            "last_broker_order_no":   self._last_broker_order_no,
            "last_order_status":      self._last_order_status,
            # 절대 invariant.
            "is_order_signal":       False,
            "is_live_authorization": False,
            "broker_order_sent":     False,
        }


@lru_cache
def get_background_tick_driver() -> BackgroundTickDriver:
    """프로세스-wide singleton — lifespan + routes 가 공유."""
    return BackgroundTickDriver()


def reset_background_tick_driver_for_tests() -> None:
    """테스트 격리용 — singleton cache clear."""
    get_background_tick_driver.cache_clear()


__all__ = [
    "BackgroundTickReason",
    "reason_message_ko",
    "DriverTickResult",
    "BackgroundTickDriver",
    "get_background_tick_driver",
    "reset_background_tick_driver_for_tests",
]

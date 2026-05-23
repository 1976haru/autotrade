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
}


def reason_message_ko(code: str) -> str:
    return _REASON_MESSAGE_KO.get(code, code)


@dataclass(frozen=True)
class DriverTickResult:
    """단일 tick 결과 — *advisory*, broker 호출 0건."""

    executed:            bool
    reason_code:         str
    reason_message:      str
    cycle_count:         int
    pipeline_result_code: str | None = None
    recorded_event:      bool = False

    # 절대 invariant.
    is_order_signal:       bool = False
    is_live_authorization: bool = False
    broker_order_sent:     bool = False

    def __post_init__(self) -> None:
        if self.is_order_signal is not False:
            raise ValueError("DriverTickResult.is_order_signal must be False")
        if self.is_live_authorization is not False:
            raise ValueError("DriverTickResult.is_live_authorization must be False")
        if self.broker_order_sent is not False:
            raise ValueError("DriverTickResult.broker_order_sent must be False")

    def to_dict(self) -> dict[str, Any]:
        return {
            "executed":             bool(self.executed),
            "reason_code":          self.reason_code,
            "reason_message":       self.reason_message,
            "cycle_count":          int(self.cycle_count),
            "pipeline_result_code": self.pipeline_result_code,
            "recorded_event":       bool(self.recorded_event),
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
    ):
        self._settings_provider = settings_provider
        self._loop_provider = loop_provider
        self._pipeline_runner = pipeline_runner or run_paper_pipeline_once
        self._event_sink = event_sink
        self._now_provider = now_provider or (lambda: datetime.now(timezone.utc))
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

        result: RunOnceResult = self._pipeline_runner(
            symbol=None,
            force_mock_market_data=force_mock,
            dry_run=dry_run,
            market_data_provider=provider,
            record=True,                # ledger NO_OP heartbeat 기록
            now=now,
        )
        self._last_pipeline_result_code = result.result_code.value
        # 실행 tick 의 last_reason_code 는 *파이프라인 result_code* 를 carry.
        self._last_reason_code = result.result_code.value
        self._last_reason_message = result.reason_message
        self._tick_count_today += 1

        recorded = self._emit(
            level="INFO", code=f"AI_PAPER_TICK_{result.result_code.value}",
            message=result.reason_message,
            details={"executed": True, "cycle": cycle,
                     "result_code": result.result_code.value,
                     "dry_run": dry_run, "broker_order_sent": False},
        )
        return DriverTickResult(
            executed=True,
            reason_code=result.result_code.value,
            reason_message=result.reason_message,
            cycle_count=cycle,
            pipeline_result_code=result.result_code.value,
            recorded_event=recorded,
        )

    # ── async run loop ───────────────────────────────────────────────────────

    async def run_forever(self) -> None:
        """asyncio task body — interval 마다 tick_once. stop() 으로 취소."""
        assert self._stop_event is not None
        _log.info("[bg-tick] driver loop started")
        try:
            while not self._stop_event.is_set():
                try:
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
            "tick_count_today": int(self._tick_count_today),
            "last_tick_at":     self._last_tick_at,
            "last_reason_code": self._last_reason_code,
            "last_reason_message": self._last_reason_message,
            "last_pipeline_result_code": self._last_pipeline_result_code,
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

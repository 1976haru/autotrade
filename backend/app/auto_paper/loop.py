"""AI Paper Auto Loop — stateful service for one-click EXE.

EXE 의 *시작/정지/긴급정지* 3 버튼이 호출하는 API 의 backing service.
*PAPER/SIMULATION 한정* — live broker / OrderExecutor / route_order import 0건.

상태 머신:
    IDLE         — 초기, 아직 한 번도 시작 안 됨
    RUNNING      — 시작됨, tick 진행 중
    STOPPED      — 사용자가 정지 (재시작 가능)
    EMERGENCY    — 긴급정지 (재시작 가능, 단 운영자 명시 reset 필요)

tick 동작 (본 PR placeholder):
- cycle 카운트 증가, last_tick_at 갱신
- 실제 주문 / 시장 데이터 / AI 호출 0건 — 향후 PR 에서 plug

invariants (테스트로 lock):
- AutoPaperStatus.is_order_signal=False / auto_apply_allowed=False
- start() / tick() / stop() / emergency_stop() 모두 broker import 안 함
- ENABLE_LIVE_TRADING / ENABLE_AI_EXECUTION 안전 flag 변경 0건
"""

from __future__ import annotations

import asyncio
import threading
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import StrEnum
from functools import lru_cache
from typing import Any


class AutoPaperState(StrEnum):
    IDLE       = "IDLE"
    RUNNING    = "RUNNING"
    STOPPED    = "STOPPED"
    EMERGENCY  = "EMERGENCY"


class LoopAlreadyRunningError(RuntimeError):
    """이미 RUNNING 상태인데 start() 호출."""


class LoopNotRunningError(RuntimeError):
    """RUNNING 이 아닌데 stop()/tick() 호출."""


@dataclass(frozen=True)
class AutoPaperStatus:
    """외부 노출용 read-only 스냅샷. Secret / API key 포함 0건."""
    state:               str           # AutoPaperState value
    cycle_count:         int
    last_tick_at:        str | None    # ISO datetime
    started_at:          str | None
    stopped_at:          str | None
    emergency_at:        str | None
    last_error:          str | None
    tick_interval_sec:   float
    forced_paper:        bool          # 항상 True — live trading 진행 0건
    # invariants
    is_order_signal:     bool = False
    auto_apply_allowed:  bool = False

    def __post_init__(self) -> None:
        if self.is_order_signal is not False:
            raise ValueError("AutoPaperStatus.is_order_signal must be False")
        if self.auto_apply_allowed is not False:
            raise ValueError("AutoPaperStatus.auto_apply_allowed must be False")
        if self.forced_paper is not True:
            raise ValueError("AutoPaperStatus.forced_paper must be True")

    def to_dict(self) -> dict[str, Any]:
        return {
            "state":               self.state,
            "cycle_count":         self.cycle_count,
            "last_tick_at":        self.last_tick_at,
            "started_at":          self.started_at,
            "stopped_at":          self.stopped_at,
            "emergency_at":        self.emergency_at,
            "last_error":          self.last_error,
            "tick_interval_sec":   self.tick_interval_sec,
            "forced_paper":        self.forced_paper,
            "is_order_signal":     self.is_order_signal,
            "auto_apply_allowed":  self.auto_apply_allowed,
        }


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


class AutoPaperLoop:
    """AI Paper Auto Loop service.

    스레드-safe (threading.Lock). 실제 tick 은 background asyncio task 가 아닌
    *외부 호출* (예: APScheduler / FastAPI BackgroundTasks) 에서 발생할 수
    있도록 `tick()` 을 public 으로 노출. 향후 PR 에서 자동 스케줄링 추가.

    본 PR 시점 tick 은 placeholder — cycle 카운트 증가 + last_tick_at 갱신.
    실제 strategy / AI / RiskManager 호출은 미포함 (별도 PR 에서 plug).
    """

    def __init__(self, *, tick_interval_sec: float = 30.0):
        self._lock = threading.Lock()
        self._state: AutoPaperState = AutoPaperState.IDLE
        self._cycle_count: int = 0
        self._last_tick_at: datetime | None = None
        self._started_at: datetime | None = None
        self._stopped_at: datetime | None = None
        self._emergency_at: datetime | None = None
        self._last_error: str | None = None
        self._tick_interval_sec: float = float(tick_interval_sec)

    # ------------------------------------------------------------------
    # public API
    # ------------------------------------------------------------------

    def start(self) -> AutoPaperStatus:
        """RUNNING 으로 전환.

        IDLE / STOPPED / EMERGENCY → RUNNING 모두 허용 (재시작 가능).
        이미 RUNNING 이면 LoopAlreadyRunningError.

        본 메서드는 broker / OrderExecutor 호출 0건 — 상태만 변경.
        """
        with self._lock:
            if self._state == AutoPaperState.RUNNING:
                raise LoopAlreadyRunningError(
                    "AutoPaperLoop is already RUNNING; call stop() first"
                )
            self._state = AutoPaperState.RUNNING
            self._started_at = datetime.now(timezone.utc)
            self._last_error = None
            return self._snapshot_unlocked()

    def stop(self) -> AutoPaperStatus:
        """RUNNING → STOPPED 전환. 신규 tick 차단.

        RUNNING 이 아니면 LoopNotRunningError.

        본 메서드는 broker 호출 0건 — 상태만 변경. 기존 paper 포지션 정리는
        본 메서드 책임이 아님 (별도 reconciliation/paper_trader flow).
        """
        with self._lock:
            if self._state != AutoPaperState.RUNNING:
                raise LoopNotRunningError(
                    f"AutoPaperLoop not RUNNING (state={self._state.value}); "
                    f"nothing to stop"
                )
            self._state = AutoPaperState.STOPPED
            self._stopped_at = datetime.now(timezone.utc)
            return self._snapshot_unlocked()

    def emergency_stop(self) -> AutoPaperStatus:
        """모든 상태에서 EMERGENCY 로 전환. 멱등 — 이미 EMERGENCY 여도 OK.

        본 메서드는 broker 호출 0건. kill switch 의미는 상태 라벨로 carry —
        실제 미체결 주문 cancel / 청산은 별도 RiskManager.emergency_stop 흐름.
        """
        with self._lock:
            self._state = AutoPaperState.EMERGENCY
            self._emergency_at = datetime.now(timezone.utc)
            return self._snapshot_unlocked()

    def reset(self) -> AutoPaperStatus:
        """EMERGENCY 등을 IDLE 로 되돌림 — 운영자 명시 호출 전용.

        다른 상태에서도 호출 가능 (멱등). cycle_count / history 는 보존.
        """
        with self._lock:
            self._state = AutoPaperState.IDLE
            self._stopped_at = None
            self._emergency_at = None
            self._last_error = None
            return self._snapshot_unlocked()

    def tick(self) -> AutoPaperStatus:
        """단일 cycle 진행. RUNNING 일 때만 cycle 증가.

        본 PR placeholder — 향후 PR 에서 strategy / AI / RiskManager 통합.
        broker.place_order 호출 절대 금지 (테스트로 lock).
        """
        with self._lock:
            if self._state != AutoPaperState.RUNNING:
                raise LoopNotRunningError(
                    f"AutoPaperLoop not RUNNING (state={self._state.value}); "
                    f"cannot tick"
                )
            self._cycle_count += 1
            self._last_tick_at = datetime.now(timezone.utc)
            return self._snapshot_unlocked()

    def status(self) -> AutoPaperStatus:
        with self._lock:
            return self._snapshot_unlocked()

    # ------------------------------------------------------------------
    # 내부
    # ------------------------------------------------------------------

    def _snapshot_unlocked(self) -> AutoPaperStatus:
        return AutoPaperStatus(
            state=self._state.value,
            cycle_count=self._cycle_count,
            last_tick_at=self._last_tick_at.isoformat() if self._last_tick_at else None,
            started_at=self._started_at.isoformat() if self._started_at else None,
            stopped_at=self._stopped_at.isoformat() if self._stopped_at else None,
            emergency_at=self._emergency_at.isoformat() if self._emergency_at else None,
            last_error=self._last_error,
            tick_interval_sec=self._tick_interval_sec,
            forced_paper=True,
        )


@lru_cache
def get_auto_paper_loop() -> AutoPaperLoop:
    """프로세스 단일 인스턴스. 테스트에서 cache_clear() 호출 후 fresh instance."""
    return AutoPaperLoop()

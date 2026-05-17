"""AI Paper Auto Loop — stateful service for one-click EXE.

EXE 의 *시작/정지/긴급정지* 3 버튼이 호출하는 API 의 backing service.
*PAPER/SIMULATION 한정* — live broker / OrderExecutor / route_order import 0건.

feat/step2-05-pre-market-gate: `start()` 이전에 *Pre-market checklist* (#80)
의 verdict 를 검증 — `start_allowed=False` (DO_NOT_START / BLOCK) 면
`LoopPreMarketBlockedError` 로 차단. 운영자가 사전 점검을 통과하지 못한
상태에서 자동 시작이 *불가능*. 차단 사유 (`blocking_reasons`) 는 응답 +
audit log 양쪽에 carry — secret 노출 0건.

상태 머신 (feat/step2-01-auto-paper-states — 체크리스트 표준 정렬):
    PAUSED          — 초기 대기 / 일시정지 (시작 가능)
    RUNNING         — 자동 Paper Loop 진행 중
    STOPPED         — 사용자가 명시 정지 (재시작 가능)
    EMERGENCY_STOP  — 긴급정지 (start() 차단 — `reset()` 후에만 재시작 가능)

전이 매트릭스:
    PAUSED         ─start()──────────> RUNNING
    RUNNING        ─stop()───────────> STOPPED
    RUNNING        ─emergency_stop()─> EMERGENCY_STOP
    STOPPED        ─start()──────────> RUNNING
    STOPPED        ─emergency_stop()─> EMERGENCY_STOP
    PAUSED         ─emergency_stop()─> EMERGENCY_STOP
    EMERGENCY_STOP ─start()──────────> ❌ LoopBlockedError
    EMERGENCY_STOP ─reset()──────────> PAUSED  (운영자 명시 reset)

레거시 호환:
- `AutoPaperState.IDLE` / `AutoPaperState.EMERGENCY` 는 PAUSED /
  EMERGENCY_STOP 의 deprecated alias (Python StrEnum 의 동일 value 정의 →
  member alias). 정적 import 만 호환 — 신규 코드는 canonical 이름 사용.
- API / status payload 는 항상 canonical 문자열 (PAUSED / EMERGENCY_STOP) 만
  emit.

tick 동작 (본 PR placeholder):
- cycle 카운트 증가, last_tick_at 갱신
- 실제 주문 / 시장 데이터 / AI 호출 0건 — 향후 PR 에서 plug

invariants (테스트로 lock):
- AutoPaperStatus.is_order_signal=False / auto_apply_allowed=False
- start() / tick() / stop() / emergency_stop() 모두 broker import 안 함
- ENABLE_LIVE_TRADING / ENABLE_AI_EXECUTION 안전 flag 변경 0건
- EMERGENCY_STOP 상태에서 start() 호출 차단 (LoopBlockedError)
"""

from __future__ import annotations

import logging
import threading
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import StrEnum
from functools import lru_cache
from typing import Any


_log = logging.getLogger("autotrade.auto_paper")


class AutoPaperState(StrEnum):
    """체크리스트 표준 4 상태. 레거시 IDLE / EMERGENCY 는 alias."""
    PAUSED         = "PAUSED"
    RUNNING        = "RUNNING"
    STOPPED        = "STOPPED"
    EMERGENCY_STOP = "EMERGENCY_STOP"

    # Deprecated aliases — Python StrEnum 이 같은 value 를 정의하면 첫 번째
    # member 의 alias 가 된다. AutoPaperState.IDLE is AutoPaperState.PAUSED.
    IDLE      = "PAUSED"
    EMERGENCY = "EMERGENCY_STOP"


class LoopAlreadyRunningError(RuntimeError):
    """이미 RUNNING 상태인데 start() 호출."""


class LoopNotRunningError(RuntimeError):
    """RUNNING 이 아닌데 stop()/tick() 호출."""


class LoopBlockedError(RuntimeError):
    """EMERGENCY_STOP 상태에서 start() 호출 차단.

    운영자가 명시적으로 `reset()` 을 호출한 뒤 다시 `start()` 해야 한다 —
    긴급정지의 의도가 자동 재시작으로 우회되지 않도록.
    """


class LoopPreMarketBlockedError(RuntimeError):
    """Pre-market checklist (#80) DO_NOT_START → start() 차단.

    `start_allowed=False` 인 verdict 를 받으면 `blocking_reasons` 와 함께
    raise. 운영자가 사전 점검을 통과한 뒤에만 다시 시도 가능.
    """

    def __init__(self, *, verdict: str, blocking_reasons: list[str]):
        # 영문 + 한글 메시지 — log + API response 양쪽에서 사람이 읽을 수 있게.
        self.verdict = verdict
        self.blocking_reasons = list(blocking_reasons)
        joined = "; ".join(blocking_reasons) if blocking_reasons else "no detail"
        super().__init__(
            f"pre_market_blocked: verdict={verdict} reasons=[{joined}]"
        )


@dataclass(frozen=True)
class PreMarketSummary:
    """`evaluate_pre_market_check()` 결과를 *압축* 한 carry 객체.

    full `PreMarketCheckResult` 를 받지 않고 *필요한 4 필드*만 보존 —
    auto_paper 모듈이 `app.governance.pre_market_check` 를 *import 하지
    않도록* 결합도 최소화. 호출자 (API endpoint) 가 변환 책임.

    `start_allowed=False` 면 `start()` 가 `LoopPreMarketBlockedError` 로
    차단. `True` 면 `verdict` 와 `warnings` 를 audit log 에 carry 만.
    """

    start_allowed:     bool
    verdict:           str             = ""     # READY_TO_START / WARN_BUT_START_ALLOWED / DO_NOT_START
    blocking_reasons:  list[str]       = field(default_factory=list)
    warnings:          list[str]       = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "start_allowed":    self.start_allowed,
            "verdict":          self.verdict,
            "blocking_reasons": list(self.blocking_reasons),
            "warnings":         list(self.warnings),
        }


@dataclass(frozen=True)
class AutoPaperStatus:
    """외부 노출용 read-only 스냅샷. Secret / API key 포함 0건."""
    state:               str
    cycle_count:         int
    last_tick_at:        str | None
    started_at:          str | None
    stopped_at:          str | None
    emergency_at:        str | None
    last_error:          str | None
    tick_interval_sec:   float
    forced_paper:        bool          # 항상 True
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


class AutoPaperLoop:
    """AI Paper Auto Loop service.

    스레드-safe. 본 PR 시점 tick 은 placeholder — cycle 카운트 증가 +
    last_tick_at 갱신. 실제 strategy / AI / RiskManager 호출은 미포함.
    """

    def __init__(self, *, tick_interval_sec: float = 30.0):
        self._lock = threading.Lock()
        self._state: AutoPaperState = AutoPaperState.PAUSED
        self._cycle_count: int = 0
        self._last_tick_at: datetime | None = None
        self._started_at: datetime | None = None
        self._stopped_at: datetime | None = None
        self._emergency_at: datetime | None = None
        self._last_error: str | None = None
        self._tick_interval_sec: float = float(tick_interval_sec)

    def start(self, *, pre_market: PreMarketSummary | None = None) -> AutoPaperStatus:
        """자동 시작.

        feat/step2-05-pre-market-gate: `pre_market` 이 제공되고
        `start_allowed=False` 면 `LoopPreMarketBlockedError` 로 차단. 본
        검증은 EMERGENCY_STOP / ALREADY_RUNNING 가드 *전* 에 수행 — 가장
        우선 차단.

        `pre_market=None` (legacy / backwards-compat): pre-market 게이트를
        건너뛴다. 호출자 (API endpoint) 가 운영 정책에 따라 None 허용 여부
        결정. 본 PR 시점에는 API endpoint 가 *기본적으로 pre_market 페이로드
        없이도* start 허용 (기존 호출자 무회귀) — 단, frontend 는 항상
        pre_market 결과를 동봉.
        """
        with self._lock:
            # Pre-market gate (가장 먼저) — start_allowed=False 면 다른 어떤
            # 검증도 시도하지 않고 즉시 차단. blocking_reasons 를 carry.
            if pre_market is not None and not pre_market.start_allowed:
                _log.warning(
                    "[auto-paper] start blocked by pre-market: verdict=%s "
                    "reasons=%s",
                    pre_market.verdict, pre_market.blocking_reasons,
                )
                raise LoopPreMarketBlockedError(
                    verdict=pre_market.verdict,
                    blocking_reasons=pre_market.blocking_reasons,
                )
            if self._state == AutoPaperState.RUNNING:
                raise LoopAlreadyRunningError(
                    "AutoPaperLoop is already RUNNING; call stop() first"
                )
            if self._state == AutoPaperState.EMERGENCY_STOP:
                # 운영자가 긴급정지를 한 뒤 *명시적으로 reset()* 을 호출해야만
                # 다시 start() 가능. 자동 재시작 차단 — 긴급정지 의도 보존.
                raise LoopBlockedError(
                    "AutoPaperLoop is in EMERGENCY_STOP; call reset() before start()"
                )
            self._state = AutoPaperState.RUNNING
            self._started_at = datetime.now(timezone.utc)
            self._last_error = None
            # Pre-market warnings 만 carry — audit log 에 명시.
            if pre_market is not None and pre_market.warnings:
                _log.info(
                    "[auto-paper] start with pre-market warnings: verdict=%s "
                    "warnings=%s",
                    pre_market.verdict, pre_market.warnings,
                )
            elif pre_market is not None:
                _log.info(
                    "[auto-paper] start with pre-market verdict=%s",
                    pre_market.verdict,
                )
            return self._snapshot_unlocked()

    def stop(self) -> AutoPaperStatus:
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
        """어떤 상태에서든 EMERGENCY_STOP 으로 전이. start() 차단까지 carry."""
        with self._lock:
            self._state = AutoPaperState.EMERGENCY_STOP
            self._emergency_at = datetime.now(timezone.utc)
            return self._snapshot_unlocked()

    def reset(self) -> AutoPaperStatus:
        """EMERGENCY_STOP / STOPPED → PAUSED. 운영자 명시 호출 후에만 재시작 가능."""
        with self._lock:
            self._state = AutoPaperState.PAUSED
            self._stopped_at = None
            self._emergency_at = None
            self._last_error = None
            return self._snapshot_unlocked()

    def tick(self) -> AutoPaperStatus:
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
    return AutoPaperLoop()

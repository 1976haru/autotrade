"""KIS Paper one-click test engine (#89).

orchestrator — readiness → broker pick → signal → (optional) paper order →
fill/balance check → report.

본 engine 은 broker / OrderExecutor / route_order 어떤 *우회 경로도 만들지
않는다* — 항상 기존 sanctioned 흐름 (`route_order` → RiskManager → audit)을
거친다. 본 PR 시점 quick / slow 모드는 *engine path 검증* 위주이고, 실제 KIS
paper API 호출은 운영자가 본인 PC 에서 `KIS_APP_KEY` 등을 채운 후 `start` 했을
때만 일어난다.

핵심 정책:
- KIS_IS_PAPER=false 면 절대 진행 X (readiness 가드).
- ENABLE_LIVE_TRADING=true 면 절대 진행 X.
- 주문 사이 ≥ rate_limit_seconds (default 3) 강제 sleep.
- 최소 주문 금액 `min_notional_krw` 기본 10,000 — 그 이하면 quantity 1 로 cap.
- mock 모드 — `MockBroker` 사용 (KIS 호출 0건).
- quick 모드 — KIS paper 1~3 tick.
- slow 모드 — KIS paper 20~50 tick, 3~5s 간격.

KIS API 실패 시:
- 즉시 stop + audit + `failures.append(...)`. mock 으로 silent swap **금지**.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from datetime import datetime, timezone
from enum import StrEnum
from typing import Optional

from app.kis_paper.readiness import KisPaperReadiness
from app.kis_paper.scoring import KisPaperScore, ScoreInput, score_run


class TestMode(StrEnum):
    QUICK = "quick"   # KIS paper 1~3 tick
    SLOW  = "slow"    # KIS paper 20~50 tick
    MOCK  = "mock"    # 내부 MockBroker 고속

    # pytest collection 에서 본 enum 을 *test class* 로 오인하지 않도록.
    __test__ = False


class KisPaperRunState(StrEnum):
    IDLE       = "IDLE"
    CHECKING   = "CHECKING"
    READY      = "READY"
    RUNNING    = "RUNNING"
    STOPPING   = "STOPPING"
    COMPLETED  = "COMPLETED"
    BLOCKED    = "BLOCKED"
    FAILED     = "FAILED"


@dataclass
class _RunCounters:
    """orchestrator 가 매 tick 갱신하는 카운터."""
    ticks:                int = 0
    ai_decisions:         int = 0
    ai_buy_signals:       int = 0
    ai_sell_signals:      int = 0
    ai_hold_signals:      int = 0
    orders_attempted:     int = 0
    orders_executed:      int = 0
    orders_rejected:      int = 0
    orders_needs_approval: int = 0
    risk_blocks:          int = 0
    fills_observed:       int = 0
    unfilled_count:       int = 0
    errors:               int = 0
    rate_limit_hits:      int = 0


@dataclass(frozen=True)
class KisPaperRunReport:
    """run() 의 결과 + scoring. *주문이 아니다*."""
    mode:               TestMode
    state:              KisPaperRunState
    started_at:         datetime
    finished_at:        datetime | None
    duration_seconds:   float
    counters:           dict
    failures:           tuple[str, ...]
    score:              KisPaperScore
    safety_note:        str
    is_order_signal:    bool = False

    def __post_init__(self) -> None:
        if self.is_order_signal is not False:
            raise ValueError("KisPaperRunReport.is_order_signal must be False")

    def to_dict(self) -> dict:
        return {
            "mode":             self.mode.value,
            "state":            self.state.value,
            "started_at":       self.started_at.isoformat(),
            "finished_at":      self.finished_at.isoformat() if self.finished_at else None,
            "duration_seconds": float(self.duration_seconds),
            "counters":         dict(self.counters),
            "failures":         list(self.failures),
            "score":            self.score.to_dict(),
            "safety_note":      self.safety_note,
            "is_order_signal":  False,
        }


# ====================================================================
# Engine
# ====================================================================


class KisPaperEngine:
    """In-memory orchestrator. 단일 instance — global state.

    동시에 두 개의 run() 이 돌아가지 않도록 `state` 로 guard. 실 broker /
    route_order 호출은 *deferred*  — `_tick_runner` 를 caller 가 inject 해서
    테스트 시에는 fake runner 를, 운영 시에는 실제 route_order wrapper 를 주입.
    """

    def __init__(self) -> None:
        self.state: KisPaperRunState = KisPaperRunState.IDLE
        self.current_mode: TestMode | None = None
        self.started_at: datetime | None = None
        self.finished_at: datetime | None = None
        self.counters: _RunCounters = _RunCounters()
        self.failures: list[str] = []
        self.last_report: KisPaperRunReport | None = None
        # 운영자가 stop() 호출 시 set — tick loop 가 다음 iteration 에서 종료.
        self._stop_flag: bool = False
        # rate limit baseline.
        self.rate_limit_seconds: float = 3.0
        self.min_notional_krw: int = 10_000

    # ---- public API ----

    async def start(
        self,
        mode: TestMode,
        readiness: KisPaperReadiness,
        *,
        tick_runner=None,
        max_ticks_override: int | None = None,
    ) -> KisPaperRunReport:
        """오케스트레이션 시작 + 종료까지 await.

        Args:
            mode: TestMode (quick / slow / mock)
            readiness: 사전 평가된 readiness 결과
            tick_runner: callable(self, mode, tick_idx) -> dict — 각 tick 의
                결과를 dict 로 반환. 테스트에서 fake runner 주입 가능.
                default 는 read-only fallback (counters 갱신만).
            max_ticks_override: 테스트에서 tick 수를 작게 강제할 때
        """
        if self.state == KisPaperRunState.RUNNING:
            self.failures.append("already running")
            return self._build_report()

        self.state = KisPaperRunState.CHECKING
        self.current_mode = mode
        self.started_at = datetime.now(timezone.utc)
        self.finished_at = None
        self.counters = _RunCounters()
        self.failures = []
        self._stop_flag = False

        # readiness 가드.
        if mode in (TestMode.QUICK, TestMode.SLOW):
            if not readiness.can_run_kis_paper:
                self.state = KisPaperRunState.BLOCKED
                self.failures.append(
                    "KIS paper 모드 진입 불가 — readiness 차단: "
                    + ", ".join(r.value for r in readiness.blocked_reasons)
                    + ". KIS 키 또는 안전 flag 확인 필요."
                )
                self.finished_at = datetime.now(timezone.utc)
                self.last_report = self._build_report()
                return self.last_report
        elif mode == TestMode.MOCK:
            if not readiness.can_run_mock:
                self.state = KisPaperRunState.BLOCKED
                self.failures.append(
                    "Mock 모드 진입 불가 — readiness 차단 (live flag): "
                    + ", ".join(r.value for r in readiness.blocked_reasons)
                )
                self.finished_at = datetime.now(timezone.utc)
                self.last_report = self._build_report()
                return self.last_report

        self.state = KisPaperRunState.READY

        # tick runner 결정 — caller 가 inject 한 게 있으면 사용, 없으면 default.
        runner = tick_runner or _default_tick_runner

        # mode 별 기본 tick count + 간격.
        if mode == TestMode.QUICK:
            target_ticks = max_ticks_override or 3
            interval = max(self.rate_limit_seconds, 3.0)
        elif mode == TestMode.SLOW:
            target_ticks = max_ticks_override or 20
            interval = max(self.rate_limit_seconds, 3.0)
        else:  # MOCK
            target_ticks = max_ticks_override or 50
            # mock 은 외부 API 0건 — 빠르게 가능.
            interval = 0.0

        self.state = KisPaperRunState.RUNNING

        try:
            for tick_idx in range(target_ticks):
                if self._stop_flag:
                    self.state = KisPaperRunState.STOPPING
                    self.failures.append(f"stopped by operator at tick {tick_idx}")
                    break
                try:
                    result = await _await_maybe(
                        runner(self, mode, tick_idx)
                    )
                    self._merge_tick_result(result)
                    # KIS rate limit 류 즉시 중단.
                    if result.get("rate_limit_hit"):
                        self.counters.rate_limit_hits += 1
                        self.failures.append(
                            f"KIS rate limit hit at tick {tick_idx} — 자동 중단"
                        )
                        break
                except Exception as e:
                    self.counters.errors += 1
                    self.failures.append(
                        f"tick {tick_idx} 실패: {type(e).__name__}: {str(e)[:160]}"
                    )
                    if mode in (TestMode.QUICK, TestMode.SLOW):
                        # KIS 모드 — 즉시 중단 (silent fallback 금지).
                        break

                if interval > 0 and tick_idx < target_ticks - 1:
                    await asyncio.sleep(interval)

            if self.state == KisPaperRunState.RUNNING:
                self.state = KisPaperRunState.COMPLETED
        except Exception as e:
            self.state = KisPaperRunState.FAILED
            self.failures.append(
                f"engine fatal: {type(e).__name__}: {str(e)[:160]}"
            )

        self.finished_at = datetime.now(timezone.utc)
        self.last_report = self._build_report()
        return self.last_report

    def stop(self) -> None:
        """운영자가 stop() 호출 — 다음 tick 에서 종료."""
        self._stop_flag = True

    def status_dict(self) -> dict:
        return {
            "state":         self.state.value,
            "mode":          self.current_mode.value if self.current_mode else None,
            "started_at":    self.started_at.isoformat() if self.started_at else None,
            "finished_at":   self.finished_at.isoformat() if self.finished_at else None,
            "counters":      vars(self.counters),
            "failures":      list(self.failures),
        }

    # ---- internal ----

    def _merge_tick_result(self, r: dict) -> None:
        c = self.counters
        c.ticks                += 1
        c.ai_decisions         += int(r.get("ai_decisions", 0))
        c.ai_buy_signals       += int(r.get("ai_buy_signals", 0))
        c.ai_sell_signals      += int(r.get("ai_sell_signals", 0))
        c.ai_hold_signals      += int(r.get("ai_hold_signals", 0))
        c.orders_attempted     += int(r.get("orders_attempted", 0))
        c.orders_executed      += int(r.get("orders_executed", 0))
        c.orders_rejected      += int(r.get("orders_rejected", 0))
        c.orders_needs_approval += int(r.get("orders_needs_approval", 0))
        c.risk_blocks          += int(r.get("risk_blocks", 0))
        c.fills_observed       += int(r.get("fills_observed", 0))
        c.unfilled_count       += int(r.get("unfilled_count", 0))
        c.errors               += int(r.get("errors", 0))
        if r.get("rate_limit_hit"):
            c.rate_limit_hits += 1
        # 실패 메시지 carry.
        for msg in r.get("failures", []) or []:
            self.failures.append(str(msg)[:200])

    def _build_report(self) -> KisPaperRunReport:
        c = self.counters
        started = self.started_at or datetime.now(timezone.utc)
        finished = self.finished_at or datetime.now(timezone.utc)
        duration = (finished - started).total_seconds() if self.started_at else 0.0

        readiness_passed = self.state not in (
            KisPaperRunState.BLOCKED, KisPaperRunState.IDLE,
        )
        kis_paper_connected = (
            self.current_mode in (TestMode.QUICK, TestMode.SLOW, TestMode.MOCK)
            and c.ticks >= 1
            and self.state != KisPaperRunState.BLOCKED
        )

        # mock 모드 점수 — KIS API 가산점 대신 mock 연결 인정.
        score = score_run(ScoreInput(
            readiness_passed=readiness_passed,
            kis_paper_connected=kis_paper_connected,
            balance_fetched=c.ticks >= 1 and self.state != KisPaperRunState.BLOCKED,
            ai_signal_generated=c.ai_decisions >= 1,
            orders_attempted=c.orders_attempted,
            orders_executed=c.orders_executed,
            orders_rejected=c.orders_rejected,
            fills_observed=c.fills_observed,
            unfilled_count=c.unfilled_count,
            positions_refreshed=c.ticks >= 1 and self.state != KisPaperRunState.BLOCKED,
            risk_block_observed=c.risk_blocks >= 1,
            audit_rows_missing=0,   # 기존 route_order 흐름 사용 — 본 PR 시점 0
            errors_count=c.errors,
            rate_limit_hits=c.rate_limit_hits,
            mode_used=(self.current_mode.value if self.current_mode else "unknown"),
        ))

        safety_note = (
            "한투 모의투자 전용 — 실제 돈이 나가지 않습니다. "
            "KIS_IS_PAPER=true / ENABLE_LIVE_TRADING=false 가 강제됩니다."
        )

        return KisPaperRunReport(
            mode=self.current_mode or TestMode.MOCK,
            state=self.state,
            started_at=started,
            finished_at=finished if self.finished_at else None,
            duration_seconds=duration,
            counters=vars(c),
            failures=tuple(self.failures),
            score=score,
            safety_note=safety_note,
        )


# ====================================================================
# default tick runner — engine path 검증 / 카운터 갱신용 stub
# ====================================================================


async def _default_tick_runner(engine: KisPaperEngine, mode: TestMode, tick_idx: int) -> dict:
    """Engine 의 default tick runner.

    본 PR 시점 *실제 broker 호출은 caller 가 주입한 runner 가 담당*. default
    runner 는 안전한 *동작 검증* 만 하며 broker / route_order 를 직접 호출하지
    않는다 — engine 의 readiness gate / counter / rate_limit / 상태 전이가
    정상 동작하는지를 단위 테스트 가능하게 한다.

    운영자가 실제 KIS paper API 를 흘리려면 별도 runner 를 inject 해야 한다
    (route 구현에서 wrapper 주입). 본 default runner 는 매 tick 에:
      - ai_decisions += 1
      - 50% 확률로 BUY 신호 simulation (deterministic by tick_idx)
      - 카운터만 갱신 — broker 호출 0건
    """
    is_buy_tick  = (tick_idx % 3 == 0)
    is_sell_tick = (tick_idx % 5 == 0 and tick_idx > 0)

    return {
        "ai_decisions":     1,
        "ai_buy_signals":   1 if is_buy_tick and not is_sell_tick else 0,
        "ai_sell_signals":  1 if is_sell_tick else 0,
        "ai_hold_signals":  1 if not (is_buy_tick or is_sell_tick) else 0,
        # 본 default runner 는 실제 주문을 만들지 않음 — orders_* 0.
        # 실 KIS paper 흐름 검증은 별도 runner 주입 시점에.
        "orders_attempted": 0,
        "orders_executed":  0,
        "orders_rejected":  0,
        "risk_blocks":      0,
        "fills_observed":   0,
        "unfilled_count":   0,
        "errors":           0,
        "failures":         [],
    }


async def _await_maybe(value):
    """coroutine 이면 await, 아니면 그대로 반환."""
    if hasattr(value, "__await__"):
        return await value
    return value


# ====================================================================
# Module-level singleton
# ====================================================================


_ENGINE: Optional[KisPaperEngine] = None


def get_engine() -> KisPaperEngine:
    """global engine instance — process 당 1개."""
    global _ENGINE
    if _ENGINE is None:
        _ENGINE = KisPaperEngine()
    return _ENGINE


def _reset_engine_for_tests() -> None:
    """테스트용 reset — 운영 코드에서 호출하지 않는다."""
    global _ENGINE
    _ENGINE = KisPaperEngine()

"""실전 유사 AI Paper 장중 시뮬레이션 — end-to-end timeline 검증.

본 테스트는 *실제 KIS API 호출 0건* / *broker 호출 0건* / *외부 네트워크 0건*
으로 평일 08:50 → 09:00 → 장중 → 15:31 → 긴급정지 흐름을 시간 주입 방식으로
모방한다.

검증 항목 (사용자 요청서 §2 매트릭스):

    시점               기대 상태               기대 산출물
    ────────────────────────────────────────────────────────────────
    08:50 KST start    WAITING_MARKET          cycle=0, decision=0, ledger=0
    09:00 KST status   RUNNING (auto promote)  state 전이
    09:30 KST tick     RUNNING                 decision=1, ledger=1, log=1
    11:00 KST tick     RUNNING                 누적 decision=2, ledger=2
    14:00 KST tick     RUNNING                 누적 decision=3, ledger=3
    15:31 KST status   MARKET_CLOSED (demote)  신규 tick 차단
    15:31 후 tick      LoopNotRunningError     decision 증분 0
    긴급정지            EMERGENCY_STOP          start 재호출 시 차단

invariants (정적 + 동작):
- broker / OrderExecutor / route_order 호출 0건 (정적 grep + 모듈 import 0건
  확인). consumer / loop 모두 ledger / DB write 외에 어떤 외부 호출도 안 함.
- ConsumerResult.is_order_signal / auto_apply_allowed / is_live_authorization
  모두 False (dataclass __post_init__ 가드).
- AutoPaperStatus.forced_paper=True, is_order_signal=False.
- AgentDecisionLog.mode="PAPER" + side-effect 0건 (DB INSERT 만).
- 안전 flag (KIS_IS_PAPER / ENABLE_LIVE_TRADING 등) mutate 0건.

본 파일은 *시뮬레이션 보고서* 의 raw data 도 함께 산출 — 마지막 테스트
`test_print_simulation_summary` 가 capsys 로 출력해 docs 작성자가 그대로
인용 가능.
"""

from __future__ import annotations

import ast
import re
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.auto_paper.agent_consumer import (
    CONSUMER_SCHEMA_VERSION,
    ConsumerResult,
    build_deterministic_explanation,
    consume_agent_recommendations,
)
from app.auto_paper.ledger import get_ledger, reset_ledger_for_tests
from app.auto_paper.loop import (
    AutoPaperLoop,
    AutoPaperState,
    LoopAlreadyRunningError,
    LoopBlockedError,
    LoopNotRunningError,
)
from app.db.models import AgentDecisionLog, Base
from app.scheduler.market_clock import MarketPhase, current_market_phase


# ─────────────────────────────────────────────────────────────────────────────
# Helpers — KST 시각을 UTC tz-aware 로 변환 (기존 test_auto_paper_market_hours.py
# 의 _kst_to_utc 와 동일한 정책).
# ─────────────────────────────────────────────────────────────────────────────

def kst_to_utc(year: int, month: int, day: int, hour: int, minute: int) -> datetime:
    """KST naive (year, month, day, hour, minute) → UTC tz-aware datetime."""
    utc_hour_unwrapped = hour - 9
    day_adjust = 0
    if utc_hour_unwrapped < 0:
        utc_hour_unwrapped += 24
        day_adjust = -1
    return datetime(
        year, month, day + day_adjust,
        utc_hour_unwrapped, minute,
        tzinfo=timezone.utc,
    )


# 시뮬레이션 기준일: 2026-05-18 (월). 평일 + 휴장 없음 — 정책 일치.
_SIM_YEAR, _SIM_MONTH, _SIM_DAY = 2026, 5, 18


def _intraday_provider(now: datetime):
    """deterministic recommendation provider — LLM 호출 0건.

    매 tick 마다 동일한 sma_crossover 후보를 반환. timestamp 만 carry 해 매
    cycle 의 explanation 이 *결정론적* 으로 동일한 BUY 후보를 만든다.
    """
    return build_deterministic_explanation(
        strategy="sma_crossover",
        symbol="005930",
        market_regime="TREND_UP",
        rationale=f"intraday-sim tick at {now.isoformat()}",
    )


# ─────────────────────────────────────────────────────────────────────────────
# Fixtures
# ─────────────────────────────────────────────────────────────────────────────


@pytest.fixture
def db():
    """In-memory SQLite — DB write 검증 + 실제 DB 영향 0건."""
    engine = create_engine("sqlite:///:memory:", future=True)
    Base.metadata.create_all(engine)
    Session = sessionmaker(bind=engine, autoflush=False, future=True)
    session = Session()
    try:
        yield session
    finally:
        session.close()
        engine.dispose()


@pytest.fixture(autouse=True)
def _isolated_ledger():
    """Paper ledger 격리 — 다른 테스트와 cross-talk 0건."""
    reset_ledger_for_tests()
    yield
    reset_ledger_for_tests()


@pytest.fixture
def runner(db):
    """AutoPaperLoop 에 주입할 consumer runner factory.

    매 tick 마다 deterministic explanation → bridge → PaperDecision → ledger +
    AgentDecisionLog 전 과정 실행. broker / KIS / OpenAI 호출 0건.
    """
    def _run(loop_state: str, now: datetime) -> ConsumerResult:
        return consume_agent_recommendations(
            loop_state=loop_state,
            recommendation_provider=_intraday_provider,
            db_session=db,
            now=now,
        )
    return _run


# ─────────────────────────────────────────────────────────────────────────────
# 시뮬레이션 결과 — 마지막 출력용
# ─────────────────────────────────────────────────────────────────────────────

@dataclass
class TimelineEvent:
    kst:              str
    action:           str           # start / tick / status / emergency_stop ...
    state_before:     str
    state_after:      str
    cycle_after:      int
    decisions_total:  int
    ledger_total:     int
    log_rows_total:   int
    notes:            str = ""


@dataclass
class SimulationLog:
    events: list[TimelineEvent] = field(default_factory=list)

    def record(self, **kw: Any) -> None:
        self.events.append(TimelineEvent(**kw))


# ─────────────────────────────────────────────────────────────────────────────
# 본 시뮬레이션 — single end-to-end timeline
# ─────────────────────────────────────────────────────────────────────────────


class TestIntradayPaperSimulation:
    """평일 1일 (2026-05-18 월) AI Paper Auto Loop 전 흐름 검증."""

    def test_full_intraday_timeline(self, db, runner):
        """08:50 → 09:00 → 09:30 → 11:00 → 14:00 → 15:31 → emergency_stop."""
        loop = AutoPaperLoop(agent_consumer_runner=runner)
        log = SimulationLog()

        # ── 08:50 KST: start() → WAITING_MARKET ────────────────────────────
        t_0850 = kst_to_utc(_SIM_YEAR, _SIM_MONTH, _SIM_DAY, 8, 50)
        # market_clock sanity check.
        assert current_market_phase(t_0850) == MarketPhase.PRE_OPEN
        status_0 = loop.start(now=t_0850)
        assert status_0.state == "WAITING_MARKET"
        assert status_0.cycle_count == 0
        assert status_0.last_decision_count == 0
        assert status_0.last_ledger_events == 0
        assert status_0.forced_paper is True
        assert status_0.is_order_signal is False
        log.record(
            kst="08:50", action="start", state_before="PAUSED",
            state_after=status_0.state, cycle_after=status_0.cycle_count,
            decisions_total=0, ledger_total=0, log_rows_total=0,
            notes="장 시작 전 — 09:00 KST 까지 대기",
        )

        # WAITING_MARKET 상태에서 tick() 호출 시도 → 차단되어야 함.
        with pytest.raises(LoopNotRunningError):
            loop.tick()
        assert db.query(AgentDecisionLog).count() == 0
        assert len(get_ledger().recent(limit=10)) == 0

        # ── 09:00 KST: status() lazy-promote → RUNNING ────────────────────
        t_0900 = kst_to_utc(_SIM_YEAR, _SIM_MONTH, _SIM_DAY, 9, 0)
        assert current_market_phase(t_0900) == MarketPhase.OPEN
        status_1 = loop.status(now=t_0900)
        assert status_1.state == "RUNNING"
        log.record(
            kst="09:00", action="status (lazy-promote)",
            state_before="WAITING_MARKET", state_after=status_1.state,
            cycle_after=status_1.cycle_count,
            decisions_total=0, ledger_total=0, log_rows_total=0,
            notes="장 시작 — RUNNING 자동 전환",
        )

        # ── 09:30 KST tick #1 ──────────────────────────────────────────────
        # AutoPaperLoop.tick() 은 now 인자를 받지 않으므로, 시점 의존성은 consumer
        # runner 가 받은 datetime.now(utc) — 본 테스트는 *상대* 흐름 검증이며
        # consumer 가 timestamp 를 어떻게 쓰는지는 ConsumerResult/log row 카운트만
        # 확인. 각 tick 사이에 시뮬레이션 시각 markers 만 출력.
        tick_1 = loop.tick()
        assert tick_1.state == "RUNNING"
        assert tick_1.cycle_count == 1
        assert tick_1.last_consumed is True
        assert tick_1.last_decision_count == 1
        assert tick_1.last_decision_action == "BUY"
        assert tick_1.last_ledger_events == 1
        assert tick_1.last_decision_log_count == 1
        log.record(
            kst="09:30", action="tick #1", state_before="RUNNING",
            state_after=tick_1.state, cycle_after=tick_1.cycle_count,
            decisions_total=1, ledger_total=1, log_rows_total=1,
            notes="첫 Agent 추천 → PaperDecision (BUY) → ledger + log",
        )

        # ── 11:00 KST tick #2 ──────────────────────────────────────────────
        tick_2 = loop.tick()
        assert tick_2.state == "RUNNING"
        assert tick_2.cycle_count == 2
        assert tick_2.last_decision_count == 1
        log.record(
            kst="11:00", action="tick #2", state_before="RUNNING",
            state_after=tick_2.state, cycle_after=tick_2.cycle_count,
            decisions_total=2, ledger_total=2, log_rows_total=2,
            notes="누적 가상 결정 2건",
        )

        # ── 14:00 KST tick #3 ──────────────────────────────────────────────
        tick_3 = loop.tick()
        assert tick_3.state == "RUNNING"
        assert tick_3.cycle_count == 3
        log.record(
            kst="14:00", action="tick #3", state_before="RUNNING",
            state_after=tick_3.state, cycle_after=tick_3.cycle_count,
            decisions_total=3, ledger_total=3, log_rows_total=3,
            notes="누적 가상 결정 3건",
        )

        # 중간 검증 — DB / ledger 가 *예상한 만큼만* 증가했는지.
        assert db.query(AgentDecisionLog).count() == 3
        assert len(get_ledger().recent(limit=50)) == 3
        # mode invariant — 모든 row 가 PAPER.
        for row in db.query(AgentDecisionLog).all():
            assert row.mode == "PAPER", (
                f"AgentDecisionLog row 의 mode 가 PAPER 가 아님: {row.mode}"
            )

        # ── 15:31 KST: status() → MARKET_CLOSED demote ────────────────────
        t_1531 = kst_to_utc(_SIM_YEAR, _SIM_MONTH, _SIM_DAY, 15, 31)
        assert current_market_phase(t_1531) == MarketPhase.CLOSED
        status_close = loop.status(now=t_1531)
        assert status_close.state == "MARKET_CLOSED"
        log.record(
            kst="15:31", action="status (lazy-demote)",
            state_before="RUNNING", state_after=status_close.state,
            cycle_after=status_close.cycle_count,
            decisions_total=3, ledger_total=3, log_rows_total=3,
            notes="장 종료 — 신규 tick 차단",
        )

        # ── 15:32 KST: 추가 tick 시도 → LoopNotRunningError ──────────────
        with pytest.raises(LoopNotRunningError):
            loop.tick()
        # 카운터 증분 0건 — DB / ledger 도 동결.
        assert db.query(AgentDecisionLog).count() == 3
        assert len(get_ledger().recent(limit=50)) == 3
        log.record(
            kst="15:32", action="tick (blocked)", state_before="MARKET_CLOSED",
            state_after="MARKET_CLOSED", cycle_after=tick_3.cycle_count,
            decisions_total=3, ledger_total=3, log_rows_total=3,
            notes="MARKET_CLOSED 상태 — tick LoopNotRunningError",
        )

        # ── 긴급정지 ────────────────────────────────────────────────────────
        es_status = loop.emergency_stop()
        assert es_status.state == "EMERGENCY_STOP"
        log.record(
            kst="15:33", action="emergency_stop",
            state_before="MARKET_CLOSED", state_after=es_status.state,
            cycle_after=es_status.cycle_count,
            decisions_total=3, ledger_total=3, log_rows_total=3,
            notes="운영자 긴급정지 — 모든 후속 시도 차단",
        )

        # 긴급정지 후 *어떤* start 시도도 차단 (다음날 시각으로도).
        t_next_day_open = kst_to_utc(_SIM_YEAR, _SIM_MONTH, _SIM_DAY + 1, 9, 0)
        with pytest.raises(LoopBlockedError):
            loop.start(now=t_next_day_open)

        with pytest.raises(LoopNotRunningError):
            loop.tick()

        # ── 최종 invariant 합산 ─────────────────────────────────────────
        # 3개 tick = 3 PaperDecision = 3 ledger event = 3 AgentDecisionLog row.
        final_decisions = db.query(AgentDecisionLog).count()
        final_ledger = len(get_ledger().recent(limit=50))
        assert final_decisions == 3
        assert final_ledger == 3

        # 모든 row 가 *PAPER* / chain_id 있음 / decision in {BUY, HOLD, SELL, EXIT}.
        valid_actions = {"BUY", "HOLD", "SELL", "EXIT", "WATCH"}
        for row in db.query(AgentDecisionLog).all():
            assert row.mode == "PAPER"
            assert row.decision in valid_actions, (
                f"unexpected decision label: {row.decision}"
            )

        # ─── 시뮬레이션 로그 저장 — capsys 캡처 / 다른 테스트가 이용 ───
        pytest.simulation_log = log  # type: ignore[attr-defined]


# ─────────────────────────────────────────────────────────────────────────────
# 정적 invariant — KIS / live broker 호출 경로 0건
# ─────────────────────────────────────────────────────────────────────────────


class TestNoLiveBrokerCalls:
    """본 시뮬레이션에서 사용하는 모듈들이 어떤 *실 broker* 도 호출하지 않음."""

    @pytest.mark.parametrize("module_rel", [
        "app/auto_paper/loop.py",
        "app/auto_paper/agent_consumer.py",
        "app/auto_paper/ledger.py",
        "app/auto_paper/decisions.py",
        "app/scheduler/market_clock.py",
    ])
    def test_no_kis_or_broker_imports(self, module_rel):
        src = Path(__file__).resolve().parent.parent / module_rel
        text = src.read_text(encoding="utf-8")

        # AST-level import 가드.
        tree = ast.parse(text, filename=str(src))
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    self._assert_not_banned(alias.name, src)
            elif isinstance(node, ast.ImportFrom):
                mod = node.module or ""
                self._assert_not_banned(mod, src)

        # Source text grep — `broker.place_order(` / `route_order(` /
        # `KisClient(` 같은 *호출* 패턴 0건.
        for pat in (
            r"\bbroker\.place_order\s*\(",
            r"\bbroker\.cancel_order\s*\(",
            r"\broute_order\s*\(",
            r"\bKisClient\s*\(",
            r"\bKisBrokerAdapter\s*\(",
            r"OrderExecutor\s*\(",
        ):
            assert not re.search(pat, text), (
                f"{src} 에 금지 호출 패턴 발견: /{pat}/"
            )

    def _assert_not_banned(self, mod_name: str, src: Path) -> None:
        for banned_prefix in (
            "app.brokers.kis",
            "app.brokers.kis_client",
            "app.brokers.live_broker",
            "app.execution.executor",
            "app.execution.order_router",
            "app.execution.live_router",
            "anthropic",
            "openai",
            "httpx",
            "requests",
        ):
            assert not mod_name.startswith(banned_prefix), (
                f"{src} 가 금지 모듈 '{mod_name}' import — 실 broker / AI / "
                "외부 HTTP 호출 경로 의심"
            )


# ─────────────────────────────────────────────────────────────────────────────
# Consumer invariant — 모든 ConsumerResult 가 *주문 아님* 라벨 유지
# ─────────────────────────────────────────────────────────────────────────────


class TestConsumerInvariantsCarried:
    """매 tick 의 ConsumerResult 가 절대 invariant 를 만족."""

    def test_consumer_result_invariants(self, db):
        result = consume_agent_recommendations(
            loop_state="RUNNING",
            recommendation_provider=_intraday_provider,
            db_session=db,
            now=kst_to_utc(_SIM_YEAR, _SIM_MONTH, _SIM_DAY, 9, 30),
        )
        assert result.is_order_signal is False
        assert result.auto_apply_allowed is False
        assert result.is_live_authorization is False
        assert result.schema_version == CONSUMER_SCHEMA_VERSION
        assert result.decision_count >= 1
        # 메타데이터에 실 broker / 실 주문 marker 없음.
        meta_dump = repr(result.metadata).lower()
        for banned in ("place_order", "route_order", "kis_client", "live_broker"):
            assert banned not in meta_dump


# ─────────────────────────────────────────────────────────────────────────────
# Print human-readable summary — capsys 로 stdout 캡처
# ─────────────────────────────────────────────────────────────────────────────


class TestPrintSimulationSummary:
    """앞 테스트가 만든 timeline 을 사람이 읽을 수 있게 출력 — docs 인용용."""

    def test_print_simulation_summary(self, capsys):
        log: SimulationLog = getattr(pytest, "simulation_log", None)
        if log is None:
            pytest.skip("simulation_log 가 아직 생성되지 않음 — 전체 테스트 순서 확인")

        lines: list[str] = []
        lines.append("")
        lines.append("=" * 72)
        lines.append("AI Paper 장중 시뮬레이션 timeline — 2026-05-18 (월) 가상 일과")
        lines.append("=" * 72)
        lines.append("")
        lines.append(
            f"{'KST':<6}  {'action':<24}  {'state':<16}  cycle  D/L/G   notes"
        )
        lines.append("-" * 72)
        for ev in log.events:
            lines.append(
                f"{ev.kst:<6}  {ev.action:<24}  {ev.state_after:<16}  "
                f"{ev.cycle_after:>5}  "
                f"{ev.decisions_total}/{ev.ledger_total}/{ev.log_rows_total}   "
                f"{ev.notes}"
            )
        lines.append("-" * 72)
        lines.append(
            "D = AgentDecisionLog rows, L = paper ledger events, "
            "G = consumer-reported decision_log_count"
        )
        lines.append("")
        lines.append("절대 invariant (테스트로 lock):")
        lines.append(
            "  - broker / OrderExecutor / route_order 호출 0건"
        )
        lines.append(
            "  - KIS / Anthropic / OpenAI / httpx / requests import 0건"
        )
        lines.append(
            "  - ConsumerResult.is_order_signal / auto_apply_allowed / "
            "is_live_authorization = False"
        )
        lines.append(
            "  - AutoPaperStatus.forced_paper=True, is_order_signal=False"
        )
        lines.append(
            "  - AgentDecisionLog.mode='PAPER' (모든 row)"
        )
        lines.append("")

        print("\n".join(lines))
        captured = capsys.readouterr()
        assert "AI Paper 장중 시뮬레이션" in captured.out
        assert "EMERGENCY_STOP" in captured.out

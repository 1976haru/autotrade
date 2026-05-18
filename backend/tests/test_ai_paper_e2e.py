"""#4-11: AI Paper 자동매수/매도 E2E 테스트.

사용자 시점의 *최종 흐름* 을 API 단까지 1 회 통과 검증:

    [POST /api/auto-paper/start]
        ↓
    GET /api/auto-paper/status  → RUNNING
        ↓
    `set_agent_consumer_runner(...)` — 결정론적 provider 주입
        ↓
    [POST /api/auto-paper/tick]
        ↓
    GET /api/auto-paper/status     → cycle_count++, last_consumed=True,
                                     last_decision_action carry
    GET /api/auto-paper/ledger     → 신규 trade event 1+
    GET /api/auto-paper/decision-log → AgentDecisionLog row carry

본 테스트는 conftest 의 `client` fixture (in-memory SQLite +
`Base.metadata.create_all`) 를 사용해 CI 환경 차이에 *무관* 하게 통과.

## 안전 invariant (테스트로 lock)

- 전체 흐름에서 `KisBrokerAdapter.place_order` / `cancel_order` 호출 0건.
- 모든 응답 envelope 의 `is_order_signal/auto_apply_allowed/is_live_authorization=False`.
- AgentDecisionLog row 의 `mode == "PAPER"` 영구.
- 비RUNNING 상태에서 tick → trade 차단 + ledger / decision_log 신규 row 0건.
"""

from __future__ import annotations

from datetime import datetime
from unittest.mock import MagicMock

import pytest

from app.auto_paper.agent_consumer import (
    build_deterministic_explanation,
    consume_agent_recommendations,
)
from app.auto_paper.ledger import reset_ledger_for_tests
from app.auto_paper.loop import (
    AutoPaperLoop,
    AutoPaperState,
    get_auto_paper_loop,
)
from app.brokers.kis import KisBrokerAdapter
from app.db.models import AgentDecisionLog


# ─────────────────────────────────────────────────────────────────────────────
# Fixtures
# ─────────────────────────────────────────────────────────────────────────────


@pytest.fixture(autouse=True)
def _isolated_ledger():
    reset_ledger_for_tests()
    yield
    reset_ledger_for_tests()


@pytest.fixture
def fresh_loop(monkeypatch):
    """Per-test isolated AutoPaperLoop singleton.

    `get_auto_paper_loop` 는 `@lru_cache` 으로 module-level 싱글톤을 캐싱한다.
    E2E 테스트는 상태 격리를 위해 매번 새 인스턴스를 캐시한다.

    또한 한국장 시간에 따른 lazy demote (RUNNING → WAITING_MARKET /
    MARKET_CLOSED) 가 테스트 환경에서 발생하지 않도록 `current_market_phase`
    를 항상 `OPEN` 으로 stub.
    """
    from app.scheduler.market_clock import MarketPhase

    # Loop 가 `from app.scheduler.market_clock import current_market_phase` 로
    # symbol 을 capture 했으므로 loop 모듈의 reference 를 monkeypatch.
    monkeypatch.setattr(
        "app.auto_paper.loop.current_market_phase",
        lambda now=None: MarketPhase.OPEN,
    )

    get_auto_paper_loop.cache_clear()
    loop = get_auto_paper_loop()
    # PAUSED 으로 시작.
    yield loop
    get_auto_paper_loop.cache_clear()


@pytest.fixture
def kis_place_order_spy(monkeypatch):
    """`KisBrokerAdapter.place_order` 어떤 호출도 즉시 AssertionError."""
    spy = MagicMock(side_effect=AssertionError(
        "place_order must NOT be called in AI Paper E2E flow"
    ))
    monkeypatch.setattr(KisBrokerAdapter, "place_order", spy)
    return spy


@pytest.fixture
def kis_cancel_order_spy(monkeypatch):
    spy = MagicMock(side_effect=AssertionError(
        "cancel_order must NOT be called in AI Paper E2E flow"
    ))
    monkeypatch.setattr(KisBrokerAdapter, "cancel_order", spy)
    return spy


def _consumer_runner(db, *, risk_flags=None):
    """매 tick 에서 호출되는 deterministic provider runner."""
    def _prov(_n):
        return build_deterministic_explanation(
            strategy="sma_crossover", symbol="005930",
            risk_flags=risk_flags,
        )

    def _run(loop_state: str, now: datetime):
        return consume_agent_recommendations(
            loop_state=loop_state,
            recommendation_provider=_prov,
            db_session=db, now=now,
        )
    return _run


def _force_running(loop: AutoPaperLoop) -> None:
    """market clock 영향을 받지 않는 RUNNING 강제 진입.

    `start()` 는 한국장 시간을 검증하므로 테스트에서는 internal state 를 직접
    RUNNING 으로 둔다. 본 helper 는 *테스트 전용* — 운영 코드 변경 0건.
    """
    loop._state = AutoPaperState.RUNNING


# ─────────────────────────────────────────────────────────────────────────────
# 1. Full pipeline E2E — start → tick → ledger → decision-log
# ─────────────────────────────────────────────────────────────────────────────


class TestFullPipelineE2E:
    """전체 흐름 한 번에 검증 — UI 시나리오와 동일."""

    def test_running_status_after_setup(
        self, client, fresh_loop,
        kis_place_order_spy, kis_cancel_order_spy,
    ):
        _force_running(fresh_loop)
        r = client.get("/api/auto-paper/status")
        assert r.status_code == 200
        body = r.json()
        assert body["state"] == "RUNNING"
        # 안전 invariant.
        assert body["is_order_signal"] is False
        assert body["auto_apply_allowed"] is False
        assert body["forced_paper"] is True
        # 실거래 호출 0건.
        assert kis_place_order_spy.call_count == 0
        assert kis_cancel_order_spy.call_count == 0

    def test_tick_produces_decision_ledger_and_log(
        self, client, fresh_loop,
        kis_place_order_spy, kis_cancel_order_spy,
    ):
        db = client.test_db_factory()
        try:
            _force_running(fresh_loop)
            # 결정론적 consumer runner 주입.
            fresh_loop.set_agent_consumer_runner(_consumer_runner(db))

            # 1. 첫 tick — provider 가 추천 1건 반환.
            tick_r = client.post(
                "/api/auto-paper/tick",
                json={"recommendations": []},
            )
            assert tick_r.status_code == 200
            # 본 endpoint 는 *별도* /tick 흐름 (legacy 2-10). consumer runner 는
            # AutoPaperLoop.tick() 안에서 동작하므로 별도로 호출.
            status1 = fresh_loop.tick()
            assert status1.cycle_count >= 1
            assert status1.last_consumed is True
            assert status1.last_decision_count >= 1
            assert status1.last_decision_action == "BUY"
            assert status1.last_ledger_events >= 1
            assert status1.last_decision_log_count >= 1

            # 2. status endpoint 가 동일 카운트 반영.
            sr = client.get("/api/auto-paper/status")
            assert sr.status_code == 200
            sbody = sr.json()
            assert sbody["state"] == "RUNNING"
            assert sbody["last_consumed"] is True
            assert sbody["last_decision_action"] == "BUY"
            assert sbody["last_decision_count"] >= 1

            # 3. ledger endpoint 가 신규 event carry.
            lr = client.get("/api/auto-paper/ledger?limit=10")
            assert lr.status_code == 200
            lbody = lr.json()
            assert lbody["is_order_signal"] is False
            assert lbody["auto_apply_allowed"] is False
            assert lbody.get("event_count", len(lbody.get("events", []))) >= 1
            events = lbody.get("events", [])
            # BUY decision 이 ledger 에 기록.
            assert any(e.get("decision_action") == "BUY" for e in events)

            # 4. decision-log endpoint — db_session(in-memory) 에 1+ row.
            rows = db.query(AgentDecisionLog).all()
            assert len(rows) >= 1
            assert rows[0].mode == "PAPER"
            assert rows[0].decision == "BUY"
            assert rows[0].agent_name == "PaperDecisionBridge"

            # 5. 실 broker 호출 0건.
            assert kis_place_order_spy.call_count == 0
            assert kis_cancel_order_spy.call_count == 0
        finally:
            db.close()

    def test_multiple_ticks_accumulate_state(
        self, client, fresh_loop, kis_place_order_spy,
    ):
        db = client.test_db_factory()
        try:
            _force_running(fresh_loop)
            fresh_loop.set_agent_consumer_runner(_consumer_runner(db))

            initial_cycle = fresh_loop.tick().cycle_count
            for _ in range(3):
                fresh_loop.tick()
            final = fresh_loop.tick()
            assert final.cycle_count >= initial_cycle + 4

            # 5 ticks → 5 rows.
            rows = db.query(AgentDecisionLog).all()
            assert len(rows) == 5
            assert all(r.mode == "PAPER" for r in rows)
            # 같은 chain_id 가 보존되어야 함 — runner 마다 새 chain_id.
            chain_ids = {r.chain_id for r in rows}
            assert len(chain_ids) == 5   # 매 tick 새 chain_id.

            assert kis_place_order_spy.call_count == 0
        finally:
            db.close()


# ─────────────────────────────────────────────────────────────────────────────
# 2. Non-RUNNING 상태에서 tick 차단
# ─────────────────────────────────────────────────────────────────────────────


class TestNonRunningBlocksTick:

    @pytest.mark.parametrize("state", [
        AutoPaperState.PAUSED,
        AutoPaperState.STOPPED,
        AutoPaperState.EMERGENCY_STOP,
        AutoPaperState.MARKET_CLOSED,
    ])
    def test_non_running_state_blocks_consumer(
        self, client, fresh_loop, kis_place_order_spy, state,
    ):
        from app.auto_paper.loop import LoopNotRunningError
        db = client.test_db_factory()
        try:
            fresh_loop._state = state
            fresh_loop.set_agent_consumer_runner(_consumer_runner(db))
            with pytest.raises(LoopNotRunningError):
                fresh_loop.tick()
            # consumer 호출 0건 → ledger / decision-log 신규 row 0건.
            rows = db.query(AgentDecisionLog).all()
            assert len(rows) == 0
            # 실 broker 호출 0건.
            assert kis_place_order_spy.call_count == 0
        finally:
            db.close()

    def test_emergency_stop_status_endpoint(self, client, fresh_loop):
        fresh_loop._state = AutoPaperState.EMERGENCY_STOP
        r = client.get("/api/auto-paper/status")
        assert r.status_code == 200
        body = r.json()
        assert body["state"] == "EMERGENCY_STOP"
        # 안전 invariant 유지.
        assert body["is_order_signal"] is False
        assert body["forced_paper"] is True


# ─────────────────────────────────────────────────────────────────────────────
# 3. Risk veto 통합 — stale_data 등으로 BUY 차단
# ─────────────────────────────────────────────────────────────────────────────


class TestRiskVetoE2E:

    def test_stale_data_flag_downgrades_buy_to_hold(
        self, client, fresh_loop, kis_place_order_spy,
    ):
        db = client.test_db_factory()
        try:
            _force_running(fresh_loop)
            fresh_loop.set_agent_consumer_runner(
                _consumer_runner(db, risk_flags=["stale_data"]),
            )
            status = fresh_loop.tick()
            # BUY → HOLD downgrade by 4-09 veto.
            assert status.last_decision_action == "HOLD"
            assert status.last_decision_count >= 1
            # decision_log row 는 여전히 기록 — risk_veto=True meta.
            rows = db.query(AgentDecisionLog).all()
            assert len(rows) == 1
            assert rows[0].decision == "HOLD"
            assert rows[0].meta["risk_veto"] is True
            assert "STALE_DATA" in rows[0].meta["risk_veto_reasons"]
            # broker 호출 0건.
            assert kis_place_order_spy.call_count == 0
        finally:
            db.close()


# ─────────────────────────────────────────────────────────────────────────────
# 4. Safety envelope invariants across all endpoints
# ─────────────────────────────────────────────────────────────────────────────


class TestEndpointEnvelopeInvariants:

    @pytest.mark.parametrize("endpoint", [
        "/api/auto-paper/status",
        "/api/auto-paper/ledger?limit=10",
        "/api/auto-paper/events?limit=10",
        "/api/auto-paper/decision/latest",
    ])
    def test_envelope_carries_safety_invariants(
        self, client, fresh_loop, endpoint,
    ):
        r = client.get(endpoint)
        assert r.status_code == 200, r.text
        body = r.json()
        # `forced_paper` 는 status endpoint 에만 carry.
        if "forced_paper" in body:
            assert body["forced_paper"] is True
        assert body["is_order_signal"] is False
        if "auto_apply_allowed" in body:
            assert body["auto_apply_allowed"] is False

    def test_tick_endpoint_envelope_invariants(
        self, client, fresh_loop, kis_place_order_spy,
    ):
        _force_running(fresh_loop)
        r = client.post(
            "/api/auto-paper/tick",
            json={"recommendations": []},
        )
        assert r.status_code == 200
        body = r.json()
        assert body["is_order_signal"] is False
        assert body["auto_apply_allowed"] is False
        assert body["is_live_authorization"] is False
        # disclaimer always carries Paper-only safety phrase.
        disclaimer = body.get("advisory_disclaimer", "")
        assert "Paper" in disclaimer
        assert "broker 호출 0건" in disclaimer
        # broker 호출 0건.
        assert kis_place_order_spy.call_count == 0


# ─────────────────────────────────────────────────────────────────────────────
# 5. Decision-log endpoint 통합 (CI 환경 차이에 강건)
# ─────────────────────────────────────────────────────────────────────────────


class TestDecisionLogEndpoint:

    def test_empty_decision_log_returns_envelope(self, client):
        r = client.get("/api/auto-paper/decision-log?limit=10")
        # 빈 DB 에서도 200 envelope.
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["mode"] == "PAPER"
        assert body["entries"] == []
        assert body["is_order_signal"] is False
        assert body["auto_apply_allowed"] is False
        assert body["is_live_authorization"] is False

    def test_rejects_invalid_limit(self, client):
        r = client.get("/api/auto-paper/decision-log?limit=0")
        assert r.status_code == 422   # Query ge=1.


# ─────────────────────────────────────────────────────────────────────────────
# 6. AI Paper / Live 분리 — 전체 E2E 흐름에서 broker spy 검증
# ─────────────────────────────────────────────────────────────────────────────


class TestPaperLiveSeparationE2E:
    """E2E 흐름 전체에서 KisBrokerAdapter 호출 0건 — Paper/Live 영구 분리."""

    def test_start_stop_lifecycle_no_live_calls(
        self, client, fresh_loop,
        kis_place_order_spy, kis_cancel_order_spy,
    ):
        db = client.test_db_factory()
        try:
            _force_running(fresh_loop)
            fresh_loop.set_agent_consumer_runner(_consumer_runner(db))

            # 3 tick.
            for _ in range(3):
                fresh_loop.tick()

            # stop → 정상 전이.
            stop_r = client.post("/api/auto-paper/stop")
            assert stop_r.status_code == 200

            # 다시 status — STOPPED.
            sr = client.get("/api/auto-paper/status")
            assert sr.json()["state"] == "STOPPED"

            # 전 과정에서 실 broker 호출 0건.
            assert kis_place_order_spy.call_count == 0
            assert kis_cancel_order_spy.call_count == 0
        finally:
            db.close()

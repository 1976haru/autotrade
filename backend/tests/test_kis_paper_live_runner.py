"""KIS 모의 자동매매 실제 tick runner (live_runner) 테스트.

broker / KIS 실 API / route_order 호출 0건 — broker 는 fake(get_price 만),
executor 는 monkeypatch 로 대체해 runner orchestration / counter 매핑만 검증.
정적 가드로 broker.place_order 직접 호출 0건도 확인.
"""

from __future__ import annotations

import asyncio
import pathlib
import types

from app.kis_paper import live_runner as lr


# ─────────── fakes ───────────


class _FakeQuote:
    def __init__(self, price: int):
        self.price = price


class _FakeBroker:
    """KIS 모의 어댑터 흉내 — get_price 만, place_order 없음."""

    def __init__(self, price: int = 70000, raise_exc: Exception | None = None):
        self.is_paper = True
        self._price = price
        self._raise = raise_exc
        self.calls: list[str] = []

    async def get_price(self, symbol: str):
        self.calls.append(symbol)
        if self._raise is not None:
            raise self._raise
        return _FakeQuote(self._price)


class _FakeDB:
    def __init__(self):
        self.committed = 0
        self.closed = False

    def commit(self):
        self.committed += 1

    def rollback(self):
        pass

    def close(self):
        self.closed = True


class _Action:
    def __init__(self, value: str):
        self.value = value


class _FakeDecision:
    def __init__(self, action: str):
        self.final_action = _Action(action)

    def to_kis_paper_decision(self, *, quantity, price):
        if self.final_action.value == "HOLD":
            return None
        return types.SimpleNamespace(
            symbol="005930", side=self.final_action.value,
            quantity=quantity, price=price,
        )


class _FakeResult:
    def __init__(self, reason_code, *, submitted=False, fill_status=None,
                 reason_message="ok"):
        self.reason_code = reason_code
        self.submitted = submitted
        self.fill_status = fill_status
        self.reason_message = reason_message


def _settings():
    return types.SimpleNamespace(kis_paper_auto_symbols="005930")


def _build(monkeypatch, *, action="HOLD", result=None, broker=None, db=None):
    """runner 를 만들고, council / executor 를 monkeypatch."""
    monkeypatch.setattr(lr, "run_agent_council",
                        lambda mi, **kw: _FakeDecision(action))

    async def _fake_exec(db_, **kwargs):
        return result

    monkeypatch.setattr(lr, "execute_kis_paper_auto_order", _fake_exec)

    db = db or _FakeDB()
    broker = broker or _FakeBroker()
    runner, cleanup = lr.build_kis_paper_tick_runner(
        db=db, broker=broker, risk=object(), settings=_settings(),
        credentials_present=True,
    )
    return runner, cleanup, db, broker


# ─────────── tests ───────────


def test_hold_decision_attempts_no_order(monkeypatch):
    runner, cleanup, db, broker = _build(monkeypatch, action="HOLD")
    out = asyncio.run(runner(None, None, 0))
    assert out["ai_decisions"] == 1
    assert out["ai_hold_signals"] == 1
    assert out["orders_attempted"] == 0
    assert db.committed == 0
    assert broker.calls == ["005930"]
    cleanup()
    assert db.closed is True


def test_buy_submitted_maps_executed_and_fill(monkeypatch):
    runner, _c, db, _b = _build(
        monkeypatch, action="BUY",
        result=_FakeResult(lr.KIS_PAPER_SUBMITTED, submitted=True,
                           fill_status="FILLED"),
    )
    out = asyncio.run(runner(None, None, 0))
    assert out["ai_buy_signals"] == 1
    assert out["orders_attempted"] == 1
    assert out["orders_executed"] == 1
    assert out["fills_observed"] == 1
    assert db.committed == 1


def test_buy_submitted_unfilled(monkeypatch):
    runner, _c, _db, _b = _build(
        monkeypatch, action="BUY",
        result=_FakeResult(lr.KIS_PAPER_SUBMITTED, submitted=True,
                           fill_status=None),
    )
    out = asyncio.run(runner(None, None, 0))
    assert out["orders_executed"] == 1
    assert out["unfilled_count"] == 1
    assert out["fills_observed"] == 0


def test_rejected_maps_risk_block(monkeypatch):
    runner, _c, _db, _b = _build(
        monkeypatch, action="BUY",
        result=_FakeResult(lr.KIS_PAPER_REJECTED, reason_message="한도 초과"),
    )
    out = asyncio.run(runner(None, None, 0))
    assert out["orders_rejected"] == 1
    assert out["risk_blocks"] == 1
    assert any("거부" in f for f in out["failures"])


def test_dry_run_counts_attempted_only(monkeypatch):
    runner, _c, _db, _b = _build(
        monkeypatch, action="BUY",
        result=_FakeResult("KIS_PAPER_DRY_RUN_OK"),
    )
    out = asyncio.run(runner(None, None, 0))
    assert out["orders_attempted"] == 1
    assert out["orders_executed"] == 0
    assert out["orders_rejected"] == 0
    assert out["failures"] == []


def test_needs_approval_maps(monkeypatch):
    runner, _c, _db, _b = _build(
        monkeypatch, action="BUY",
        result=_FakeResult(lr.KIS_PAPER_NEEDS_APPROVAL),
    )
    out = asyncio.run(runner(None, None, 0))
    assert out["orders_needs_approval"] == 1


def test_quote_error_carried_not_raised(monkeypatch):
    broker = _FakeBroker(raise_exc=RuntimeError("network down"))
    runner, _c, _db, _b = _build(monkeypatch, action="HOLD", broker=broker)
    out = asyncio.run(runner(None, None, 0))
    assert out["errors"] == 1
    assert out["orders_attempted"] == 0
    assert any("시세 조회 실패" in f for f in out["failures"])


def test_rate_limit_detected(monkeypatch):
    broker = _FakeBroker(raise_exc=RuntimeError("KIS API EGW00201 초당 제한"))
    runner, _c, _db, _b = _build(monkeypatch, action="HOLD", broker=broker)
    out = asyncio.run(runner(None, None, 0))
    assert out["rate_limit_hit"] is True
    assert out["errors"] == 1


def test_broker_is_kis_paper_helper():
    assert lr._broker_is_kis_paper(_FakeBroker()) is False  # 클래스명 다름

    class KisBrokerAdapter:  # noqa: N801 — 클래스명 기반 판정 검증용
        is_paper = True

    class KisBrokerAdapterLive:  # 라이브 흉내
        pass

    assert lr._broker_is_kis_paper(KisBrokerAdapter()) is True
    live = KisBrokerAdapter()
    live.is_paper = False
    assert lr._broker_is_kis_paper(live) is False


def test_resolve_universe_default():
    s = types.SimpleNamespace()
    assert lr._resolve_universe(s, None) == lr._DEFAULT_UNIVERSE
    assert lr._resolve_universe(s, ("000660",)) == ("000660",)
    s2 = types.SimpleNamespace(kis_paper_auto_symbols="005930, 000660")
    assert lr._resolve_universe(s2, None) == ("005930", "000660")


# ─────────── engine 통합: runner 주입 시 카운터가 흐른다 ───────────


def test_engine_runs_injected_runner(monkeypatch):
    from app.kis_paper.engine import (
        KisPaperRunState,
        TestMode,
        _reset_engine_for_tests,
        get_engine,
    )
    from app.kis_paper.readiness import evaluate_readiness

    _reset_engine_for_tests()
    runner, _c, _db, broker = _build(
        monkeypatch, action="BUY",
        result=_FakeResult(lr.KIS_PAPER_SUBMITTED, submitted=True,
                           fill_status="FILLED"),
    )
    rd = evaluate_readiness({
        "kis_is_paper": True, "enable_live_trading": False,
        "enable_ai_execution": False, "enable_futures_live_trading": False,
        "default_mode": "PAPER", "kis_app_key": "PAPER_KEY",
        "kis_app_secret": "PAPER_SECRET", "kis_account_no": "12345678-01",
    })
    engine = get_engine()
    asyncio.run(engine.start(TestMode.QUICK, rd, tick_runner=runner,
                             max_ticks_override=2))
    assert engine.state == KisPaperRunState.COMPLETED
    assert engine.counters.ai_decisions == 2
    assert engine.counters.orders_executed == 2
    assert engine.counters.fills_observed == 2
    _reset_engine_for_tests()


# ─────────── 정적 가드: place_order 직접 호출 0건 ───────────


def test_live_runner_no_direct_place_order():
    src = pathlib.Path(lr.__file__).read_text(encoding="utf-8")
    for line in src.splitlines():
        stripped = line.split("#", 1)[0]
        for banned in (".place_order(", ".cancel_order(", "broker.place_order("):
            assert banned not in stripped, f"banned token: {line!r}"


# ─────────── /start 라우트가 background 루프를 *실제로* 실행한다 ───────────
# (회귀 가드: 이전 구현 background_tasks.add_task(asyncio.create_task, ...) 는
#  Starlette 가 비-async callable 을 threadpool 에서 호출 → "no running event
#  loop" 로 루프가 시작조차 안 됐다. 본 테스트는 mock Start 가 COMPLETED 까지
#  도달함을 실제 ASGI 이벤트 루프 위에서 확인한다.)


def test_start_route_runs_mock_loop_to_completion(monkeypatch):
    import httpx

    from app.kis_paper.engine import _reset_engine_for_tests

    # 안전 paper 설정으로 readiness PASS.
    from app.core import config as cfg
    monkeypatch.setattr(cfg.Settings, "kis_app_key", "PAPER_KEY", raising=False)

    async def _drive():
        from app.main import app
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport,
                                     base_url="http://test") as ac:
            rd = await ac.get("/api/kis-paper/readiness")
            if not rd.json().get("can_run_mock"):
                return "SKIP"  # 로컬 자격 미설정 — mock 가용성만 확인
            r = await ac.post("/api/kis-paper/start",
                              json={"mode": "mock", "confirm": True})
            assert r.status_code == 200, r.text
            # background task 가 같은 루프에서 돌도록 양보 + polling.
            for _ in range(50):
                await asyncio.sleep(0.05)
                s = (await ac.get("/api/kis-paper/status")).json()
                if s["state"] in ("COMPLETED", "FAILED"):
                    return s
            return (await ac.get("/api/kis-paper/status")).json()

    _reset_engine_for_tests()
    try:
        result = asyncio.run(_drive())
    finally:
        _reset_engine_for_tests()

    if result == "SKIP":
        return
    assert result["state"] == "COMPLETED", result
    assert result["counters"]["ticks"] >= 1
    assert result["counters"]["ai_decisions"] >= 1

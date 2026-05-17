"""Pre-market checklist (#80) gate 가 Auto Paper Loop start() 를 차단.

feat/step2-05-pre-market-gate: `start_allowed=False` (DO_NOT_START / BLOCK) 인
사전 점검 결과로 start() 가 호출되면 `LoopPreMarketBlockedError` + API 409 +
`blocking_reasons` carry.

검증 항목:
- PASS (READY_TO_START / start_allowed=True) → start 가능 → RUNNING
- WARN (WARN_BUT_START_ALLOWED / start_allowed=True, warnings 존재) → start 가능
- BLOCK (DO_NOT_START / start_allowed=False) → start 차단, 409 + blocking_reasons
- 차단 후 상태는 PAUSED 유지 (RUNNING 으로 *전이되지 않음*)
- 차단 사유 audit log 기록 (autotrade.auto_paper logger)
- 실거래 / broker 호출 0건 (정적 grep)
- Secret 노출 0건
"""

from __future__ import annotations

import logging

import pytest
from fastapi.testclient import TestClient

from app.auto_paper.loop import (
    AutoPaperLoop,
    LoopPreMarketBlockedError,
    PreMarketSummary,
    get_auto_paper_loop,
)
from app.main import app


@pytest.fixture(autouse=True)
def _force_market_open(monkeypatch):
    """feat/step2-market-waiting-mode 도입 후 호환 — pre-market gate 자체의
    검증이 목적이므로 market_clock 분기는 OPEN 으로 고정 (시장 시간 분기
    자체는 `test_auto_paper_market_hours.py` 에서 검증).
    """
    from app.scheduler.market_clock import MarketPhase
    monkeypatch.setattr(
        "app.auto_paper.loop.current_market_phase",
        lambda *args, **kwargs: MarketPhase.OPEN,
    )


def _fresh_loop() -> AutoPaperLoop:
    get_auto_paper_loop.cache_clear()
    return get_auto_paper_loop()


@pytest.fixture
def client():
    get_auto_paper_loop.cache_clear()
    return TestClient(app)


# ─────────────────────────────────────────────────────────────────────
# 1. PreMarketSummary dataclass 동작
# ─────────────────────────────────────────────────────────────────────


class TestPreMarketSummary:
    def test_to_dict_carries_all_fields(self):
        s = PreMarketSummary(
            start_allowed=True,
            verdict="READY_TO_START",
            blocking_reasons=[],
            warnings=["test warning"],
        )
        d = s.to_dict()
        assert d == {
            "start_allowed":    True,
            "verdict":          "READY_TO_START",
            "blocking_reasons": [],
            "warnings":         ["test warning"],
        }

    def test_default_empty_lists(self):
        """blocking_reasons / warnings default mutable 공유 X."""
        s1 = PreMarketSummary(start_allowed=True)
        s2 = PreMarketSummary(start_allowed=True)
        # dataclass field(default_factory=list) — independent instances.
        assert s1.blocking_reasons is not s2.blocking_reasons


# ─────────────────────────────────────────────────────────────────────
# 2. PASS / WARN → start 허용
# ─────────────────────────────────────────────────────────────────────


class TestStartWithPassPreMarket:
    def test_start_with_ready_to_start_verdict(self):
        """PASS → start_allowed=True → start RUNNING."""
        loop = _fresh_loop()
        pm = PreMarketSummary(
            start_allowed=True,
            verdict="READY_TO_START",
            blocking_reasons=[],
            warnings=[],
        )
        s = loop.start(pre_market=pm)
        assert s.state == "RUNNING"

    def test_start_with_warn_verdict(self):
        """WARN → start_allowed=True (warnings 존재) → start RUNNING."""
        loop = _fresh_loop()
        pm = PreMarketSummary(
            start_allowed=True,
            verdict="WARN_BUT_START_ALLOWED",
            blocking_reasons=[],
            warnings=["watchlist 적음", "전략 등록 0건"],
        )
        s = loop.start(pre_market=pm)
        assert s.state == "RUNNING"

    def test_start_without_pre_market_is_backwards_compat(self):
        """`pre_market=None` (legacy) — 차단 없음, RUNNING. 기존 호출자 무회귀."""
        loop = _fresh_loop()
        s = loop.start()  # pre_market 미제공
        assert s.state == "RUNNING"


# ─────────────────────────────────────────────────────────────────────
# 3. BLOCK → start 차단
# ─────────────────────────────────────────────────────────────────────


class TestStartBlockedByPreMarket:
    def test_block_verdict_raises_pre_market_blocked_error(self):
        """DO_NOT_START → LoopPreMarketBlockedError + blocking_reasons carry."""
        loop = _fresh_loop()
        pm = PreMarketSummary(
            start_allowed=False,
            verdict="DO_NOT_START",
            blocking_reasons=[
                "API 미응답",
                "DB 연결 실패",
                "긴급정지 활성화 상태",
            ],
            warnings=[],
        )
        with pytest.raises(LoopPreMarketBlockedError) as exc_info:
            loop.start(pre_market=pm)
        assert exc_info.value.verdict == "DO_NOT_START"
        assert exc_info.value.blocking_reasons == [
            "API 미응답", "DB 연결 실패", "긴급정지 활성화 상태",
        ]

    def test_state_not_transitioned_on_block(self):
        """차단 후 상태는 *PAUSED 유지* — RUNNING 으로 전이 X."""
        loop = _fresh_loop()
        assert loop.status().state == "PAUSED"
        pm = PreMarketSummary(
            start_allowed=False,
            verdict="DO_NOT_START",
            blocking_reasons=["test"],
        )
        with pytest.raises(LoopPreMarketBlockedError):
            loop.start(pre_market=pm)
        # 상태 보존.
        assert loop.status().state == "PAUSED"

    def test_block_logged_with_blocking_reasons(self, caplog):
        """차단 시 autotrade.auto_paper logger 가 warning 로그 emit."""
        loop = _fresh_loop()
        # 본 logger 가 alembic fileConfig 로 disabled 될 수 있어 강제 enable.
        log = logging.getLogger("autotrade.auto_paper")
        prev = log.disabled
        log.disabled = False
        try:
            pm = PreMarketSummary(
                start_allowed=False,
                verdict="DO_NOT_START",
                blocking_reasons=["sentinel-block-reason-xyz"],
            )
            with caplog.at_level(logging.WARNING, logger="autotrade.auto_paper"):
                with pytest.raises(LoopPreMarketBlockedError):
                    loop.start(pre_market=pm)
        finally:
            log.disabled = prev

        msgs = " ".join(r.getMessage() for r in caplog.records
                        if r.name == "autotrade.auto_paper")
        assert "start blocked by pre-market" in msgs
        assert "DO_NOT_START" in msgs
        assert "sentinel-block-reason-xyz" in msgs

    def test_block_takes_priority_over_already_running(self):
        """이미 RUNNING 이라도 pre_market BLOCK 이 *먼저* 차단."""
        loop = _fresh_loop()
        # 먼저 RUNNING 만든다.
        loop.start()
        assert loop.status().state == "RUNNING"
        # 두 번째 start 호출 + BLOCK pre_market — pre-market 가 우선 차단.
        pm = PreMarketSummary(
            start_allowed=False,
            verdict="DO_NOT_START",
            blocking_reasons=["test"],
        )
        with pytest.raises(LoopPreMarketBlockedError):
            loop.start(pre_market=pm)

    def test_block_takes_priority_over_emergency_stop(self):
        """EMERGENCY_STOP 상태에서도 pre_market BLOCK 가 *먼저* 차단 — 둘 다
        차단이지만 사용자에게 가장 즉각적인 원인을 제공."""
        loop = _fresh_loop()
        loop.emergency_stop()
        assert loop.status().state == "EMERGENCY_STOP"
        pm = PreMarketSummary(
            start_allowed=False,
            verdict="DO_NOT_START",
            blocking_reasons=["test"],
        )
        with pytest.raises(LoopPreMarketBlockedError):
            loop.start(pre_market=pm)


# ─────────────────────────────────────────────────────────────────────
# 4. API 통합 — /api/auto-paper/start with pre_market body
# ─────────────────────────────────────────────────────────────────────


class TestApiPreMarketGate:
    def test_start_pass_returns_running(self, client):
        body = {
            "pre_market": {
                "start_allowed":    True,
                "verdict":          "READY_TO_START",
                "blocking_reasons": [],
                "warnings":         [],
            },
        }
        r = client.post("/api/auto-paper/start", json=body)
        assert r.status_code == 200
        assert r.json()["state"] == "RUNNING"

    def test_start_warn_returns_running(self, client):
        body = {
            "pre_market": {
                "start_allowed": True,
                "verdict":       "WARN_BUT_START_ALLOWED",
                "warnings":      ["data freshness WARN"],
            },
        }
        r = client.post("/api/auto-paper/start", json=body)
        assert r.status_code == 200
        assert r.json()["state"] == "RUNNING"

    def test_start_block_returns_409_with_blocking_reasons(self, client):
        body = {
            "pre_market": {
                "start_allowed":    False,
                "verdict":          "DO_NOT_START",
                "blocking_reasons": [
                    "watchlist 항목 0개",
                    "DB 미응답",
                ],
            },
        }
        r = client.post("/api/auto-paper/start", json=body)
        assert r.status_code == 409
        detail = r.json()["detail"]
        assert detail["error"] == "pre_market_blocked"
        assert detail["verdict"] == "DO_NOT_START"
        assert detail["blocking_reasons"] == [
            "watchlist 항목 0개", "DB 미응답",
        ]

    def test_start_block_does_not_change_state(self, client):
        """BLOCK 응답 후 GET /api/auto-paper/status 는 여전히 PAUSED."""
        body = {
            "pre_market": {
                "start_allowed":    False,
                "verdict":          "DO_NOT_START",
                "blocking_reasons": ["test"],
            },
        }
        r = client.post("/api/auto-paper/start", json=body)
        assert r.status_code == 409
        r2 = client.get("/api/auto-paper/status")
        assert r2.json()["state"] == "PAUSED"

    def test_start_block_response_does_not_return_running(self, client):
        """BLOCK 시 *어떤 응답에도 'RUNNING' 문자열 0건* — 의도 보존."""
        body = {
            "pre_market": {
                "start_allowed":    False,
                "verdict":          "DO_NOT_START",
                "blocking_reasons": ["block-sentinel"],
            },
        }
        r = client.post("/api/auto-paper/start", json=body)
        assert "RUNNING" not in r.text

    def test_start_without_body_is_backwards_compat(self, client):
        """기존 호출 (body 없음) — 게이트 건너뜀, RUNNING."""
        r = client.post("/api/auto-paper/start")
        assert r.status_code == 200
        assert r.json()["state"] == "RUNNING"

    def test_start_with_pre_market_null_is_backwards_compat(self, client):
        """body 가 있지만 pre_market 이 null — 게이트 건너뜀."""
        r = client.post("/api/auto-paper/start", json={"pre_market": None})
        assert r.status_code == 200
        assert r.json()["state"] == "RUNNING"

    def test_block_response_carries_no_secrets(self, client):
        """차단 응답에 secret-shape 패턴 0건."""
        import re
        body = {
            "pre_market": {
                "start_allowed":    False,
                "verdict":          "DO_NOT_START",
                "blocking_reasons": ["test"],
            },
        }
        r = client.post("/api/auto-paper/start", json=body)
        text = r.text
        # secret-shape 패턴.
        forbidden = [
            r"sk-[a-zA-Z0-9]{20,}",
            r"ghp_[A-Za-z0-9]{36,}",
            r"AKIA[0-9A-Z]{16}",
        ]
        for pat in forbidden:
            assert re.search(pat, text) is None

"""KIS Paper engine + scoring 테스트 (#89).

본 파일은 broker / KIS 실 API 호출 0건 — engine 의 tick runner 를 *fake* 로
주입해 orchestration / counter / state 전이를 검증.
"""

from __future__ import annotations

import asyncio
import pathlib

import pytest

from app.kis_paper.engine import (
    KisPaperRunState,
    TestMode,
    _reset_engine_for_tests,
    get_engine,
)
from app.kis_paper.readiness import evaluate_readiness
from app.kis_paper.scoring import (
    Grade,
    KisPaperScore,
    ScoreInput,
    score_run,
)


def _safe_settings(**overrides) -> dict:
    base = {
        "kis_is_paper":                True,
        "enable_live_trading":         False,
        "enable_ai_execution":         False,
        "enable_futures_live_trading": False,
        "default_mode":                "PAPER",
        "kis_app_key":                 "PAPER_KEY",
        "kis_app_secret":              "PAPER_SECRET",
        "kis_account_no":              "12345678-01",
    }
    base.update(overrides)
    return base


@pytest.fixture(autouse=True)
def _reset_engine():
    _reset_engine_for_tests()
    yield
    _reset_engine_for_tests()


# ====================================================================
# Engine — start/stop/status 기본 전이
# ====================================================================


def test_mock_mode_completes_with_default_runner():
    engine = get_engine()
    rd = evaluate_readiness(_safe_settings())
    asyncio.run(engine.start(TestMode.MOCK, rd, max_ticks_override=5))

    assert engine.state == KisPaperRunState.COMPLETED
    assert engine.counters.ticks == 5
    assert engine.counters.ai_decisions == 5
    assert engine.last_report is not None
    assert engine.last_report.mode == TestMode.MOCK


def test_kis_paper_blocked_when_keys_missing():
    engine = get_engine()
    rd = evaluate_readiness(_safe_settings(kis_app_key=""))
    asyncio.run(engine.start(TestMode.QUICK, rd, max_ticks_override=1))

    assert engine.state == KisPaperRunState.BLOCKED
    # 카운터는 진행 안 되어야.
    assert engine.counters.ticks == 0
    assert any("KIS paper 모드 진입 불가" in f for f in engine.failures)


def test_kis_paper_blocked_when_live_flag_true():
    engine = get_engine()
    rd = evaluate_readiness(_safe_settings(enable_live_trading=True))
    asyncio.run(engine.start(TestMode.MOCK, rd, max_ticks_override=1))

    assert engine.state == KisPaperRunState.BLOCKED


def test_kis_paper_blocked_when_ai_execution_flag_true():
    engine = get_engine()
    rd = evaluate_readiness(_safe_settings(enable_ai_execution=True))
    asyncio.run(engine.start(TestMode.MOCK, rd, max_ticks_override=1))

    assert engine.state == KisPaperRunState.BLOCKED


# ====================================================================
# Engine — fake tick runner 로 카운터 검증
# ====================================================================


def test_fake_runner_carries_counters_through_engine():
    engine = get_engine()
    rd = evaluate_readiness(_safe_settings())

    async def fake_runner(eng, mode, tick_idx):
        return {
            "ai_decisions":     1,
            "ai_buy_signals":   1 if tick_idx % 2 == 0 else 0,
            "orders_attempted": 1 if tick_idx % 2 == 0 else 0,
            "orders_executed":  1 if tick_idx % 2 == 0 else 0,
            "fills_observed":   1 if tick_idx % 2 == 0 else 0,
            "risk_blocks":      1 if tick_idx == 1 else 0,
        }

    asyncio.run(engine.start(
        TestMode.MOCK, rd, tick_runner=fake_runner, max_ticks_override=4,
    ))

    c = engine.counters
    assert c.ticks == 4
    assert c.ai_decisions == 4
    assert c.ai_buy_signals == 2
    assert c.orders_attempted == 2
    assert c.orders_executed == 2
    assert c.fills_observed == 2
    assert c.risk_blocks == 1


def test_rate_limit_hit_stops_engine_immediately():
    engine = get_engine()
    rd = evaluate_readiness(_safe_settings())

    async def rate_limit_runner(eng, mode, tick_idx):
        if tick_idx == 2:
            return {"rate_limit_hit": True, "ai_decisions": 0}
        return {"ai_decisions": 1}

    asyncio.run(engine.start(
        TestMode.MOCK, rd, tick_runner=rate_limit_runner, max_ticks_override=10,
    ))

    # tick 2 에서 rate limit — 그 전 2 tick + 자기 자신 1 = 3 tick 까지.
    assert engine.counters.ticks == 3
    assert engine.counters.rate_limit_hits >= 1
    assert any("KIS rate limit hit" in f for f in engine.failures)


def test_kis_mode_does_not_silent_fallback_to_mock_on_error():
    """KIS quick / slow 모드에서 tick runner 가 예외를 던지면 *즉시 중단*,
    mock 으로 silent 우회 0건.
    """
    engine = get_engine()
    rd = evaluate_readiness(_safe_settings())

    call_count = {"n": 0}

    async def boom_runner(eng, mode, tick_idx):
        call_count["n"] += 1
        if tick_idx == 1:
            raise RuntimeError("KIS API EGW00201 rate limit")
        return {"ai_decisions": 1}

    asyncio.run(engine.start(
        TestMode.QUICK, rd, tick_runner=boom_runner, max_ticks_override=5,
    ))

    # tick 1 에서 raise — 그 후 더 진행 안 함.
    assert call_count["n"] == 2  # tick 0 정상 + tick 1 예외
    assert engine.counters.errors >= 1
    assert any("EGW00201" in f for f in engine.failures)


def test_stop_flag_terminates_loop_at_next_tick():
    engine = get_engine()
    rd = evaluate_readiness(_safe_settings())

    async def slow_runner(eng, mode, tick_idx):
        if tick_idx == 1:
            eng.stop()
        return {"ai_decisions": 1}

    asyncio.run(engine.start(
        TestMode.MOCK, rd, tick_runner=slow_runner, max_ticks_override=10,
    ))

    # tick 0 (정상) + tick 1 (stop 호출) → tick 2 진입 X.
    assert engine.counters.ticks == 2
    assert engine.state == KisPaperRunState.STOPPING
    assert any("stopped by operator" in f for f in engine.failures)


# ====================================================================
# Engine — 보고서 + 점수
# ====================================================================


def test_report_carries_score_and_safety_note():
    engine = get_engine()
    rd = evaluate_readiness(_safe_settings())
    asyncio.run(engine.start(TestMode.MOCK, rd, max_ticks_override=3))

    rep = engine.last_report
    assert rep is not None
    d = rep.to_dict()
    assert "score" in d and isinstance(d["score"], dict)
    assert d["safety_note"].startswith("한투 모의투자 전용")
    assert d["is_order_signal"] is False


def test_report_score_is_zero_when_blocked():
    engine = get_engine()
    rd = evaluate_readiness(_safe_settings(enable_live_trading=True))
    asyncio.run(engine.start(TestMode.MOCK, rd, max_ticks_override=1))

    rep = engine.last_report
    assert rep is not None
    # BLOCKED 상태에서는 readiness_passed=False → 점수 매우 낮음.
    assert rep.score.total <= 30
    assert rep.score.grade == Grade.DO_NOT_PROMOTE_TO_LIVE


# ====================================================================
# Scoring
# ====================================================================


def test_score_full_health_yields_long_term_candidate():
    s = score_run(ScoreInput(
        readiness_passed=True,
        kis_paper_connected=True,
        balance_fetched=True,
        ai_signal_generated=True,
        orders_attempted=2,
        orders_executed=2,
        orders_rejected=0,
        fills_observed=2,
        unfilled_count=0,
        positions_refreshed=True,
        risk_block_observed=True,
        audit_rows_missing=0,
        errors_count=0,
        rate_limit_hits=0,
        mode_used="mock",
    ))
    assert s.total == 100
    assert s.grade == Grade.LONG_TERM_PAPER_CANDIDATE


def test_score_with_errors_lowers_grade():
    s = score_run(ScoreInput(
        readiness_passed=True,
        kis_paper_connected=True,
        balance_fetched=True,
        ai_signal_generated=True,
        orders_attempted=2,
        orders_executed=1,
        orders_rejected=1,
        fills_observed=0,
        unfilled_count=1,
        positions_refreshed=True,
        risk_block_observed=False,
        audit_rows_missing=0,
        errors_count=3,
        rate_limit_hits=1,
        mode_used="slow",
    ))
    assert s.total < 90
    assert "rate_limit_hit" in s.attention_flags
    assert "api_error_burst" in s.attention_flags


def test_score_one_liner_does_not_contain_live_trading_phrases():
    """'실거래 가능' / 'LIVE 시작' 같은 단어가 점수 문구에 들어가면 dataclass
    가드가 ValueError — 본 테스트는 4 등급 모두에서 trigger 되지 않음을 확인.
    """
    for total_target, brick in [(95, "all True"), (80, "mid"), (65, "low"), (40, "very low")]:
        # 적당한 input 으로 각 등급 도달.
        s = score_run(ScoreInput(
            readiness_passed=(total_target >= 60),
            kis_paper_connected=(total_target >= 50),
            balance_fetched=(total_target >= 50),
            ai_signal_generated=(total_target >= 40),
            orders_attempted=2,
            orders_executed=(2 if total_target >= 70 else 0),
            orders_rejected=0,
            fills_observed=(2 if total_target >= 70 else 0),
            unfilled_count=0,
            positions_refreshed=(total_target >= 60),
            risk_block_observed=(total_target >= 60),
            audit_rows_missing=0,
            errors_count=0,
            rate_limit_hits=0,
            mode_used="mock",
        ))
        for banned in ("실거래 가능", "LIVE 시작", "지금 매수", "지금 매도",
                       "Place Order"):
            assert banned not in s.one_liner


def test_score_is_live_authorization_always_false():
    """KisPaperScore.is_live_authorization=True 생성 시도하면 ValueError."""
    with pytest.raises(ValueError):
        KisPaperScore(
            total=100, grade=Grade.LONG_TERM_PAPER_CANDIDATE,
            grade_label="x", breakdown={}, one_liner="ok",
            is_live_authorization=True,
        )


def test_score_rejects_banned_phrase_in_one_liner():
    """'실거래 가능' 류 단어가 one_liner 에 들어가면 ValueError."""
    with pytest.raises(ValueError):
        KisPaperScore(
            total=100, grade=Grade.LONG_TERM_PAPER_CANDIDATE,
            grade_label="x", breakdown={},
            one_liner="실거래 가능 - 모든 흐름 정상",
        )


# ====================================================================
# 정적 grep 가드 — engine 가 잘못된 모듈 import 0건
# ====================================================================


def _read(path: pathlib.Path) -> str:
    return path.read_text(encoding="utf-8")


def test_kis_paper_modules_no_direct_broker_place_order_call():
    """본 패키지의 어떤 파일도 broker.place_order( 를 *직접* 호출하지 않는다 —
    실제 주문 흐름은 route_order 가 통제. 본 PR 시점 본 모듈은 broker /
    OrderExecutor / route_order 를 *runtime 호출* 자체가 없다.
    """
    root = pathlib.Path(__file__).parent.parent / "app" / "kis_paper"
    for f in root.glob("*.py"):
        src = _read(f)
        for banned in (
            ".place_order(",
            "broker.cancel_order(",
            "route_order(",
            "= route_order",
        ):
            # docstring 안의 *언급* 은 OK — 실제 호출 라인은 0건.
            for line in src.splitlines():
                stripped = line.split("#", 1)[0].strip()
                # docstring 내부 줄은 따옴표로 시작/감싸이는 패턴 — 단순 skip.
                if stripped.startswith('"') or stripped.startswith("'"):
                    continue
                assert banned not in stripped, (
                    f"{f.name}: banned call '{banned}' at line: {line!r}"
                )


def test_kis_paper_engine_does_not_import_kis_adapter_at_top_level():
    """engine.py 가 top-level 에서 KisBrokerAdapter / MockBroker 를 import 하지
    않는다 — broker 인스턴스는 caller (route / runner) 가 주입.
    """
    src = _read(
        pathlib.Path(__file__).parent.parent
        / "app" / "kis_paper" / "engine.py"
    )
    for banned in (
        "from app.brokers.kis import",
        "from app.brokers.mock_broker import",
        "from app.brokers.base import OrderRequest",
        "from app.execution.order_router import",
        "from app.execution.order_executor import",
    ):
        assert banned not in src, f"engine.py contains '{banned}'"

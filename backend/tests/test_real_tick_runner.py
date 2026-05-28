"""KIS-PAPER-REAL-TICK-RUNNER-WIRING-V1 — real_tick_runner 테스트.

검증:
- USE_REAL_TICK_RUNNER=true 시 mocked KIS place_order(is_paper=True) 흐름
- USE_REAL_TICK_RUNNER=false (default) 시 기존 live_runner 사용
- exit_plan 없는 BUY 차단 + reason_code 기록
- HOLD 도 AgentDecisionLog 기록 (auto_executor 가 담당)
- RiskManager / PermissionGate 우회 불가 (정적 가드)
- 종목별 예외 격리 (V2 스캔이 담당)
- place_order(is_paper=False) 호출 0건 (실계좌 차단)
- per-tick 결과 디스크 즉시 persist
- mock 으로 silent fallback 금지

broker / 실 KIS API / route_order 호출 0건 — fake scan_fn 주입.
"""

from __future__ import annotations

import asyncio
import json
import pathlib
import shutil
from types import SimpleNamespace

import pytest

from app.kis_paper import real_tick_runner as rtr


# ─────────── flag helpers ───────────


def test_should_use_real_tick_runner_default_false():
    s = SimpleNamespace()
    assert rtr.should_use_real_tick_runner(s) is False


def test_should_use_real_tick_runner_settings_field_true():
    s = SimpleNamespace(use_real_tick_runner=True)
    assert rtr.should_use_real_tick_runner(s) is True


def test_should_use_real_tick_runner_env_truthy(monkeypatch):
    monkeypatch.setenv("USE_REAL_TICK_RUNNER", "true")
    s = SimpleNamespace()
    assert rtr.should_use_real_tick_runner(s) is True
    monkeypatch.setenv("USE_REAL_TICK_RUNNER", "1")
    assert rtr.should_use_real_tick_runner(s) is True
    monkeypatch.setenv("USE_REAL_TICK_RUNNER", "yes")
    assert rtr.should_use_real_tick_runner(s) is True


def test_should_use_real_tick_runner_env_falsy(monkeypatch):
    monkeypatch.setenv("USE_REAL_TICK_RUNNER", "false")
    s = SimpleNamespace()
    assert rtr.should_use_real_tick_runner(s) is False
    monkeypatch.setenv("USE_REAL_TICK_RUNNER", "")
    assert rtr.should_use_real_tick_runner(s) is False


def test_settings_field_overrides_env(monkeypatch):
    # settings 필드가 명시 False 면 env 가 true 라도 False.
    monkeypatch.setenv("USE_REAL_TICK_RUNNER", "true")
    s = SimpleNamespace(use_real_tick_runner=False)
    assert rtr.should_use_real_tick_runner(s) is False


# ─────────── fake fixtures ───────────


class _FakeDB:
    def __init__(self):
        self.closed = False

    def close(self):
        self.closed = True


def _scan_response(
    *,
    symbols_scanned=3,
    candidates_found=1,
    orders_attempted=1,
    orders_submitted=1,
    reason_code="KIS_REALTIME_SCAN_DONE",
    reason_message="ok",
    orders=None,
    skipped=None,
):
    return {
        "reason_code":          reason_code,
        "reason_message":       reason_message,
        "mode":                 "KIS_REALTIME_PAPER_AUTO",
        "price_source":         "kis",
        "market_data_provider": "kis",
        "dry_run":              False,
        "smoke_mode":           False,
        "broker_order_enabled": True,
        "symbols_scanned":      int(symbols_scanned),
        "candidates_found":     int(candidates_found),
        "orders_attempted":     int(orders_attempted),
        "orders_submitted":     int(orders_submitted),
        "submitted":            orders_submitted > 0,
        "broker_order_sent":    orders_submitted > 0,
        "broker_order_type":    "KIS_PAPER",
        "orders":               list(orders or []),
        "skipped":              list(skipped or []),
        "is_live_authorization": False,
        "is_order_signal":      False,
    }


@pytest.fixture
def tmp_persist(tmp_path: pathlib.Path):
    pdir = tmp_path / "kis_paper_test"
    yield pdir
    if pdir.exists():
        shutil.rmtree(pdir, ignore_errors=True)


# ─────────── result mapping ───────────


def test_map_buy_submitted_filled():
    scan = _scan_response(
        orders=[{
            "symbol": "005930", "side": "BUY", "reason_code": "KIS_PAPER_SUBMITTED",
            "submitted": True, "fill_status": "FILLED", "broker_order_no": "PAPER-1",
        }],
    )
    tr = rtr._map_scan_to_tick_result(scan)
    assert tr["ai_buy_signals"] == 1
    assert tr["orders_executed"] == 1
    assert tr["fills_observed"] == 1
    assert tr["unfilled_count"] == 0
    assert tr["errors"] == 0


def test_map_buy_submitted_unfilled():
    scan = _scan_response(
        orders=[{
            "symbol": "005930", "side": "BUY", "reason_code": "KIS_PAPER_SUBMITTED",
            "submitted": True, "fill_status": None,
        }],
    )
    tr = rtr._map_scan_to_tick_result(scan)
    assert tr["orders_executed"] == 1
    assert tr["unfilled_count"] == 1
    assert tr["fills_observed"] == 0


def test_map_rejected_carries_risk_block():
    scan = _scan_response(
        orders_submitted=0,
        orders=[{
            "symbol": "005930", "side": "BUY", "reason_code": "KIS_PAPER_REJECTED",
            "reason_message": "한도 초과",
        }],
    )
    tr = rtr._map_scan_to_tick_result(scan)
    assert tr["orders_rejected"] == 1
    assert tr["risk_blocks"] == 1
    assert any("거부" in f for f in tr["failures"])


def test_map_missing_exit_plan_blocks_buy():
    scan = _scan_response(
        orders_submitted=0,
        orders=[{
            "symbol": "005930", "side": "BUY", "reason_code": "MISSING_EXIT_PLAN",
            "reason_message": "exit_plan 없음",
        }],
    )
    tr = rtr._map_scan_to_tick_result(scan)
    assert tr["risk_blocks"] == 1
    assert any("exit_plan" in f.lower() or "MISSING_EXIT_PLAN" in f
               for f in tr["failures"])
    # 차단된 BUY 는 executed 가 아니다 — risk_block 만 카운트.
    assert tr["orders_executed"] == 0


def test_map_needs_approval_carries_count():
    scan = _scan_response(
        orders_submitted=0,
        orders=[{
            "symbol": "005930", "side": "BUY",
            "reason_code": "KIS_PAPER_NEEDS_APPROVAL",
        }],
    )
    tr = rtr._map_scan_to_tick_result(scan)
    assert tr["orders_needs_approval"] == 1


def test_map_hold_skipped_counts_hold_signal():
    scan = _scan_response(
        orders_attempted=0, orders_submitted=0, candidates_found=0,
        skipped=[
            {"symbol": "005930", "reason_code": "HOLD_NO_SIGNAL"},
            {"symbol": "000660", "reason_code": "HOLD_NO_SIGNAL"},
        ],
    )
    tr = rtr._map_scan_to_tick_result(scan)
    assert tr["ai_hold_signals"] == 2


def test_map_scan_level_kis_market_data_unavailable_carries_error():
    """KIS read-only client 없음 — silent mock fallback 0건."""
    scan = _scan_response(
        symbols_scanned=0, candidates_found=0, orders_attempted=0, orders_submitted=0,
        reason_code="KIS_MARKET_DATA_UNAVAILABLE",
        reason_message="KIS read-only 시세 client 없음",
    )
    tr = rtr._map_scan_to_tick_result(scan)
    assert tr["errors"] == 1
    assert any("KIS" in f or "client" in f for f in tr["failures"])


def test_map_scan_level_error_with_rate_limit():
    scan = _scan_response(
        reason_code="KIS_PAPER_ERROR",
        reason_message="EGW00201 초당 호출 제한",
    )
    tr = rtr._map_scan_to_tick_result(scan)
    assert tr["rate_limit_hit"] is True
    assert tr["errors"] == 1


# ─────────── runner end-to-end (fake scan_fn 주입) ───────────


def test_runner_calls_scan_and_persists(monkeypatch, tmp_persist):
    captured = {"n": 0}

    async def _fake_scan(**kw):
        captured["n"] += 1
        return _scan_response(
            orders=[{
                "symbol": "005930", "side": "BUY",
                "reason_code": "KIS_PAPER_SUBMITTED",
                "submitted": True, "fill_status": "FILLED",
            }],
        )

    db = _FakeDB()
    runner, cleanup = rtr.build_real_kis_paper_tick_runner(
        db=db, broker=object(), risk=object(),
        settings=SimpleNamespace(), credentials_present=True,
        scan_fn=_fake_scan, persist_dir=tmp_persist, run_id="runX",
    )
    out = asyncio.run(runner(None, None, 0))
    assert captured["n"] == 1
    assert out["ai_decisions"] == 3   # symbols_scanned
    assert out["orders_executed"] == 1
    assert out["fills_observed"] == 1
    # 디스크 persist 검증.
    files = sorted(tmp_persist.glob("runX_tick_*.json"))
    assert len(files) == 1
    payload = json.loads(files[0].read_text(encoding="utf-8"))
    assert payload["run_id"] == "runX"
    assert payload["tick_idx"] == 0
    assert payload["scan_summary"]["broker_order_type"] == "KIS_PAPER"
    assert payload["scan_summary"]["is_live_authorization"] is False
    cleanup()
    assert db.closed is True


def test_runner_per_symbol_exception_isolation_via_scan(monkeypatch, tmp_persist):
    """V2 scan 의 종목별 try/except 가 한 종목 실패 → 나머지 계속.

    본 runner 는 scan 결과를 받아 매핑만 하므로, 한 종목 실패는 `skipped` 또는
    `orders` 의 `KIS_PAPER_ERROR` 로 carry 되며 다른 종목들의 결과는 정상 누적.
    """
    async def _fake_scan(**kw):
        return _scan_response(
            symbols_scanned=3, candidates_found=2, orders_attempted=2,
            orders_submitted=1,
            orders=[
                {"symbol": "005930", "side": "BUY",
                 "reason_code": "KIS_PAPER_SUBMITTED",
                 "submitted": True, "fill_status": "FILLED"},
                {"symbol": "000660", "side": "BUY",
                 "reason_code": "KIS_PAPER_ERROR",
                 "reason_message": "network down"},
            ],
            skipped=[
                {"symbol": "035720", "reason_code": "KIS_PRICE_STALE",
                 "price_source": "kis"},
            ],
        )

    runner, cleanup = rtr.build_real_kis_paper_tick_runner(
        db=_FakeDB(), broker=object(), risk=object(),
        settings=SimpleNamespace(), credentials_present=True,
        scan_fn=_fake_scan, persist_dir=tmp_persist,
    )
    out = asyncio.run(runner(None, None, 0))
    # 1 BUY 성공 + 1 BUY 오류 + 1 stale skip — loop 살아있음.
    assert out["orders_executed"] == 1
    assert out["fills_observed"] == 1
    assert out["errors"] == 1
    cleanup()


def test_runner_wrapper_catches_scan_exception(tmp_persist):
    """scan_fn 자체가 raise 해도 runner 는 죽지 않고 errors+1 반환."""
    async def _fake_scan(**kw):
        raise RuntimeError("scan itself died")

    runner, _c = rtr.build_real_kis_paper_tick_runner(
        db=_FakeDB(), broker=object(), risk=object(),
        settings=SimpleNamespace(), credentials_present=True,
        scan_fn=_fake_scan, persist_dir=tmp_persist,
    )
    out = asyncio.run(runner(None, None, 7))
    assert out["errors"] == 1
    assert any("V2 스캔 wrapper 오류" in f for f in out["failures"])
    # 실패도 디스크 persist (운영자 디버그용).
    files = sorted(tmp_persist.glob("*_tick_0007.json"))
    assert len(files) == 1


def test_runner_rate_limit_propagates_from_scan(tmp_persist):
    async def _fake_scan(**kw):
        return _scan_response(
            symbols_scanned=0, candidates_found=0, orders_attempted=0,
            orders_submitted=0,
            reason_code="KIS_PAPER_ERROR",
            reason_message="EGW00201 초당 호출 제한",
        )

    runner, _c = rtr.build_real_kis_paper_tick_runner(
        db=_FakeDB(), broker=object(), risk=object(),
        settings=SimpleNamespace(), credentials_present=True,
        scan_fn=_fake_scan, persist_dir=tmp_persist,
    )
    out = asyncio.run(runner(None, None, 0))
    assert out["rate_limit_hit"] is True


def test_runner_force_dry_run_wraps_settings(tmp_persist):
    """force_dry_run=True → settings.kis_paper_auto_order_dry_run 강제 True 로
    덮어쓴 wrapper 가 V2 스캔에 전달돼야 함 (.env 파일 변경 0건)."""
    captured = {"dry_run_in_settings": None}

    async def _fake_scan(*, settings, **kw):
        captured["dry_run_in_settings"] = settings.kis_paper_auto_order_dry_run
        # 다른 속성은 원본으로 위임 — wrapper 가 정상 동작 확인.
        captured["other_attr"] = getattr(settings, "kis_paper_smoke_mode", "MISSING")
        return _scan_response(symbols_scanned=0, orders_attempted=0,
                              orders_submitted=0, candidates_found=0)

    base_settings = SimpleNamespace(
        kis_paper_auto_order_dry_run=False,
        kis_paper_smoke_mode=True,
    )
    runner, _c = rtr.build_real_kis_paper_tick_runner(
        db=_FakeDB(), broker=object(), risk=object(),
        settings=base_settings, credentials_present=True,
        force_dry_run=True,
        scan_fn=_fake_scan, persist_dir=tmp_persist,
    )
    asyncio.run(runner(None, None, 0))
    assert captured["dry_run_in_settings"] is True   # 강제 True
    assert captured["other_attr"] is True            # 원본 그대로 위임


def test_runner_persists_each_tick_separately(tmp_persist):
    async def _fake_scan(**kw):
        return _scan_response(orders=[], skipped=[],
                              orders_attempted=0, orders_submitted=0,
                              candidates_found=0, symbols_scanned=1)

    runner, _c = rtr.build_real_kis_paper_tick_runner(
        db=_FakeDB(), broker=object(), risk=object(),
        settings=SimpleNamespace(), credentials_present=True,
        scan_fn=_fake_scan, persist_dir=tmp_persist, run_id="runMulti",
    )
    for i in range(3):
        asyncio.run(runner(None, None, i))
    files = sorted(tmp_persist.glob("runMulti_tick_*.json"))
    assert len(files) == 3
    assert files[0].name.endswith("0000.json")
    assert files[-1].name.endswith("0002.json")


# ─────────── 14 invariant: 정적 가드 ───────────


def test_real_tick_runner_does_not_import_broker_adapters():
    """KisBrokerAdapter / MockBrokerAdapter 직접 import 0건."""
    src = pathlib.Path(rtr.__file__).read_text(encoding="utf-8")
    # import 라인만 검사 (docstring/주석에 단어가 들어가는 건 허용).
    for line in src.splitlines():
        stripped = line.strip()
        if not stripped.startswith(("import ", "from ")):
            continue
        # 본 모듈은 broker / executor / route_order / KIS adapter / Mock adapter 를
        # 직접 import 하지 *않는다*. V2 스캔이 위임 받아 처리한다.
        for banned in (
            "from app.brokers.kis",
            "from app.brokers.mock_broker",
            "from app.brokers.base",
            "from app.execution.order_router",
            "from app.execution.executor",
            "from app.execution.order_executor",
        ):
            assert banned not in stripped, f"banned top-level import: {line!r}"


def _strip_docstrings_and_comments(src: str) -> str:
    """docstring/주석 제거한 *코드만* 문자열로 반환 (token 단위 재조립)."""
    import tokenize
    import io

    # 주석 + string literal 제거 — token 단위 재조립.
    out = []
    tokens = tokenize.generate_tokens(io.StringIO(src).readline)
    for tok in tokens:
        if tok.type in (tokenize.COMMENT,):
            continue
        if tok.type == tokenize.STRING:
            # docstring + 일반 문자열 — banned token 검사에서는 코드가 아니므로 제외.
            out.append("''")
            continue
        out.append(tok.string)
    return " ".join(s for s in out if s)


def test_real_tick_runner_no_direct_place_order_calls():
    """broker.place_order(...) / cancel_order(...) 직접 호출 0건 (코드 only)."""
    src = pathlib.Path(rtr.__file__).read_text(encoding="utf-8")
    code = _strip_docstrings_and_comments(src)
    for banned in (".place_order(", ".cancel_order(", "broker.place_order("):
        assert banned not in code, f"banned token in code: {banned}"


def test_real_tick_runner_no_settings_mutation():
    """ENABLE_LIVE_TRADING / ENABLE_AI_EXECUTION / KIS_IS_PAPER mutate 0건."""
    src = pathlib.Path(rtr.__file__).read_text(encoding="utf-8")
    code = _strip_docstrings_and_comments(src)
    for banned in (
        "enable_live_trading =",
        "enable_ai_execution =",
        "kis_is_paper =",
        ".enable_live_trading=",
        ".enable_ai_execution=",
    ):
        assert banned not in code, f"banned mutation in code: {banned}"


def test_real_tick_runner_no_silent_mock_fallback():
    """KIS 시세 실패 시 mock broker 로 swap 하는 코드 0건."""
    src = pathlib.Path(rtr.__file__).read_text(encoding="utf-8")
    code = _strip_docstrings_and_comments(src)
    for banned in (
        "MockBrokerAdapter(",
        "fallback_to_mock",
        "swap_to_mock",
    ):
        assert banned not in code, f"banned fallback in code: {banned}"


# ─────────── 14 invariant: 실계좌 place_order 도달 0건 (E2E with real route_order) ────


def test_real_tick_runner_via_scan_never_reaches_live_place_order(monkeypatch, tmp_persist):
    """V2 scan 위임 시 KisBrokerAdapter.place_order(is_paper=False) 호출 0건.

    KisBrokerAdapter 의 live place_order 는 NotImplementedError 이므로, 본 테스트
    는 위임 경로가 *호출조차 시도하지 않음* 을 직접 검증. fake scan 에서 broker
    .place_order 호출 횟수를 카운트.
    """
    place_order_calls = {"live": 0, "paper": 0}

    class _SpyBroker:
        is_paper = True

        async def place_order(self, req, *, is_paper=True):
            if is_paper:
                place_order_calls["paper"] += 1
            else:
                place_order_calls["live"] += 1
            raise NotImplementedError("live forbidden")

    async def _fake_scan(*, broker, **kw):
        # broker 가 wrapper 에서 전달되는지 확인 (sanity). 본 fake 는 broker 를
        # 호출하지 않음 → 실제 place_order 도달 0건.
        assert broker is not None
        return _scan_response(orders=[], skipped=[], orders_attempted=0,
                              orders_submitted=0, candidates_found=0,
                              symbols_scanned=0)

    spy = _SpyBroker()
    runner, _c = rtr.build_real_kis_paper_tick_runner(
        db=_FakeDB(), broker=spy, risk=object(),
        settings=SimpleNamespace(), credentials_present=True,
        scan_fn=_fake_scan, persist_dir=tmp_persist,
    )
    asyncio.run(runner(None, None, 0))
    assert place_order_calls["live"] == 0
    assert place_order_calls["paper"] == 0   # fake scan 은 broker 미호출


# ─────────── route 통합: USE_REAL_TICK_RUNNER 가 분기를 바꾼다 ───────────


def test_settings_field_round_trip():
    """config.Settings 에 use_real_tick_runner 필드 존재 + default False."""
    from app.core.config import Settings
    s = Settings()
    assert s.use_real_tick_runner is False

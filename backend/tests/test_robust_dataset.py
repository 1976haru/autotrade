"""KIS-INTRADAY-ROBUST-DATASET-COLLECTION-01 — robust 데이터셋 메타 단위 테스트.

종목군 / 시간분할 / 장세 regime(look-ahead 금지) / 품질 / manifest + 안전 invariant +
CLI orchestration(fake collect_fn) + endpoint. 실제 KIS 수집/네트워크/heavy backtest 비의존.
"""

from __future__ import annotations

import datetime as dt
import json
import re
import sys
from dataclasses import dataclass
from pathlib import Path

import pytest

from app.market_data import robust_dataset as rd

_SRC = Path(__file__).resolve().parents[1] / "app"
_SCRIPTS = Path(__file__).resolve().parents[2] / "scripts"
_KST = dt.timezone(dt.timedelta(hours=9))


# ──────────────────────────── helpers ────────────────────────────
@dataclass
class _Bar:
    symbol: str
    timestamp: object
    open: float
    high: float
    low: float
    close: float
    volume: float


def _trading_days(n: int, start: dt.date = dt.date(2024, 1, 2)) -> list[str]:
    out: list[str] = []
    d = start
    while len(out) < n:
        if d.weekday() < 5:
            out.append(d.isoformat())
        d += dt.timedelta(days=1)
    return out


def _make_bars(sym: str, days: list[str], *, base_px: float = 100.0) -> list[_Bar]:
    bars: list[_Bar] = []
    px = base_px
    for d in days:
        y, m, day = (int(x) for x in d.split("-"))
        for k in range(78):  # 09:00~15:25 5분봉
            ts = dt.datetime(y, m, day, 9, 0, tzinfo=_KST) + dt.timedelta(minutes=5 * k)
            o = px
            c = px * 1.001
            bars.append(_Bar(sym, ts, o, c * 1.002, o * 0.998, c, 1000.0))
            px = c
    return bars


# ──────────────────────────── symbol groups ────────────────────────────
def test_symbol_groups_structure():
    g = rd.build_robust_symbol_groups()
    assert g.total_symbols == 35
    assert g.counts_by_group == {
        rd.LARGE_CAP: 10, rd.MID_CAP: 10, rd.HIGH_VOL_THEME: 10, rd.ETF_PROXY: 5}
    # 모든 종목코드 6자리 + 그룹 간 중복 없음.
    syms = g.all_symbols()
    assert all(re.match(r"^\d{6}$", s) for s in syms)
    assert len(syms) == len(set(syms))


def test_symbol_group_invariants():
    g = rd.build_robust_symbol_groups()
    assert g.is_order_signal is False and g.is_investment_advice is False
    with pytest.raises(ValueError):
        rd.SymbolGroupManifest(entries=(), counts_by_group={}, symbols_by_group={},
                               total_symbols=0, etf_proxy_note="x", is_order_signal=True)


def test_representative_10():
    assert len(rd.REPRESENTATIVE_10) == 10
    assert "005930" in rd.REPRESENTATIVE_10


# ──────────────────────────── time split ────────────────────────────
def test_time_split_insufficient():
    s = rd.build_time_split(_trading_days(150))
    assert s.split_method == rd.SPLIT_INSUFFICIENT
    assert s.split_quality_status == "FAIL"
    assert s.train_days == 0


def test_time_split_two_year():
    s = rd.build_time_split(_trading_days(410))
    assert s.split_method == rd.SPLIT_2Y
    assert s.split_quality_status == "PASS"
    # 50/25/25 근사 + 합 == 총합.
    assert s.train_days + s.validation_days + s.test_days == 410
    # test 는 마지막 구간 — validation_end < test_start (구간 비겹침).
    assert s.validation_end < s.test_start
    assert s.test_used_for_selection is False


def test_time_split_one_year_warn_when_short():
    s = rd.build_time_split(_trading_days(230))
    assert s.split_method == rd.SPLIT_1Y
    assert s.split_quality_status == "WARN"  # 240 미만은 WARN


def test_time_split_unsafe_invariant():
    with pytest.raises(ValueError):
        rd.TimeSplitManifest(
            split_method="x", split_quality_status="PASS", train_start=None, train_end=None,
            validation_start=None, validation_end=None, test_start=None, test_end=None,
            train_days=0, validation_days=0, test_days=0, total_days=0,
            test_used_for_selection=True)


# ──────────────────────────── regime labels ────────────────────────────
def _proxy_series(n: int, *, crash_at: int | None = None) -> list[rd.DailyPoint]:
    pts: list[rd.DailyPoint] = []
    price = 100.0
    for i, d in enumerate(_trading_days(n)):
        drift = 0.004
        if crash_at is not None and i == crash_at:
            drift = -0.06
        op = price * 1.0005
        price = price * (1 + drift)
        pts.append(rd.DailyPoint(date=d, open=op, high=max(op, price) * 1.01,
                                 low=min(op, price) * 0.99, close=price, volume=1000.0))
    return pts


def test_regime_detects_crash():
    rm = rd.label_market_regimes(_proxy_series(120, crash_at=80))
    crash = [x for x in rm.days if rd.CRASH_LIKE_DAY in x.regime_labels]
    assert crash and crash[0].regime_primary == rd.CRASH_LIKE_DAY
    assert rm.regime_label_status in ("OK", "WARN")


def test_regime_no_look_ahead():
    """앞 구간만으로 라벨한 결과가 전체로 라벨한 결과와 동일해야 한다(미래 미사용)."""
    full = rd.label_market_regimes(_proxy_series(120, crash_at=80))
    partial = rd.label_market_regimes(_proxy_series(60))  # 처음 60일만
    for i in range(60):
        a, b = full.days[i], partial.days[i]
        assert a.regime_primary == b.regime_primary
        assert a.regime_labels == b.regime_labels


def test_regime_empty_fail():
    rm = rd.label_market_regimes([])
    assert rm.regime_label_status == "FAIL"
    assert rm.proxy_kind == "UNAVAILABLE"
    assert rm.no_look_ahead is True


def test_regime_unsafe_invariant():
    with pytest.raises(ValueError):
        rd.RegimeManifest(proxy_kind="x", formula="f", days=(), counts_by_regime={},
                          regime_label_status="OK", no_look_ahead=False)


# ──────────────────────────── daily aggregation / proxy ────────────────────────────
def test_daily_from_intraday():
    bars = _make_bars("005930", _trading_days(3))
    daily = rd.daily_from_intraday_bars(bars)
    assert len(daily) == 3
    # open == 첫 bar open, high>=low.
    assert daily[0].high >= daily[0].low > 0


def test_equal_weight_proxy():
    a = rd.daily_from_intraday_bars(_make_bars("A", _trading_days(3), base_px=100))
    b = rd.daily_from_intraday_bars(_make_bars("B", _trading_days(3), base_px=200))
    proxy = rd.build_equal_weight_proxy({"A": a, "B": b})
    assert len(proxy) == 3
    assert proxy[0].close == pytest.approx((a[0].close + b[0].close) / 2)


# ──────────────────────────── quality ────────────────────────────
def test_quality_pass_when_rich():
    g = rd.build_robust_symbol_groups()
    days = _trading_days(225)
    s2b = {s: _make_bars(s, days) for s in g.all_symbols()}
    q = rd.evaluate_dataset_quality(g, s2b)
    assert q.symbols_present == 35
    assert q.trading_days == 225
    assert q.split_feasible is True
    assert q.status == rd.PASS


def test_quality_missing_symbols_marked():
    g = rd.build_robust_symbol_groups()
    days = _trading_days(225)
    present = g.all_symbols()[:5]
    s2b = {s: _make_bars(s, days) for s in present}
    q = rd.evaluate_dataset_quality(g, s2b)
    assert q.symbols_present == 5
    missing = [p for p in q.per_symbol if not p["present"]]
    assert len(missing) == 30
    assert q.status in (rd.WARN, rd.FAIL)


def test_quality_fail_when_empty():
    g = rd.build_robust_symbol_groups()
    q = rd.evaluate_dataset_quality(g, {})
    assert q.status == rd.FAIL
    assert q.symbols_present == 0


# ──────────────────────────── manifest ────────────────────────────
def _full_manifest(n_days: int, n_present: int):
    g = rd.build_robust_symbol_groups()
    days = _trading_days(n_days)
    s2b = {s: _make_bars(s, days) for s in g.all_symbols()[:n_present]}
    q = rd.evaluate_dataset_quality(g, s2b)
    ts = rd.build_time_split(days)
    proxy = rd.build_equal_weight_proxy(
        {s: rd.daily_from_intraday_bars(b) for s, b in list(s2b.items())[:5]})
    rm = rd.label_market_regimes(proxy)
    return rd.build_dataset_manifest(
        group_manifest=g, quality=q, time_split=ts, regime=rm,
        requested_period="2y", one_minute_availability="UNAVAILABLE")


def test_manifest_ready_true():
    m = _full_manifest(225, 35)
    assert m.ready_for_robust_backtest is True
    assert "5m" in m.bar_intervals_available


def test_manifest_not_ready_few_symbols():
    m = _full_manifest(225, 10)
    assert m.ready_for_robust_backtest is False


def test_manifest_not_ready_short_period():
    m = _full_manifest(150, 35)  # split FAIL
    assert m.ready_for_robust_backtest is False
    assert m.time_split_status == "FAIL"


def test_manifest_safety_invariants():
    m = _full_manifest(225, 35)
    assert m.is_live_authorization is False
    assert m.real_order_allowed is False
    assert m.live_trading_recommendation is False
    assert m.kis_order_api_called is False
    assert m.broker_order_sent is False
    assert m.exe_build_executed is False
    assert m.do_not_auto_apply is True
    assert m.no_profit_guarantee is True


def test_manifest_rejects_unsafe():
    kw = dict(
        dataset_name="x", created_at="t", provider="p", requested_period="2y",
        actual_period="a", bar_intervals_available=(), symbol_count=0, symbols_by_group={},
        total_bars_5m=0, total_bars_1m_subset=0, start_date=None, end_date=None, trading_days=0,
        data_quality_status="FAIL", time_split_status="FAIL", regime_label_status="FAIL",
        one_minute_availability="UNAVAILABLE", ready_for_robust_backtest=False,
        warnings=(), next_recommended_task="x")
    rd.DatasetManifest(**kw)  # ok
    for bad in ({"is_live_authorization": True}, {"real_order_allowed": True},
                {"live_trading_recommendation": True}, {"kis_order_api_called": True},
                {"exe_build_executed": True}, {"do_not_auto_apply": False},
                {"no_profit_guarantee": False}, {"contains_secret": True}):
        with pytest.raises(ValueError):
            rd.DatasetManifest(**{**kw, **bad})


# ──────────────────────────── to_dict serializers ────────────────────────────
def test_to_dict_serializers():
    m = _full_manifest(225, 35)
    g = rd.build_robust_symbol_groups()
    assert rd.symbol_group_to_dict(g)["total_symbols"] == 35
    assert "split_method" in rd.time_split_to_dict(rd.build_time_split(_trading_days(410)))
    d = rd.manifest_to_dict(m)
    assert d["ready_for_robust_backtest"] is True and d["is_order_signal"] is False


# ──────────────────────────── static safety guards ────────────────────────────
_FORBIDDEN_IMPORTS = ("app.brokers", "app.execution", "order_router",
                      "anthropic", "openai", "httpx", "requests", "app.ai.client")
_FORBIDDEN_TOKENS = ("route_order(", ".place_order(", "OrderExecutor(", ".cancel_order(",
                     "order-cash", "tauri build", "cargo build", "cargo tauri")


def _assert_clean(src: str, *, allow_imports: tuple[str, ...] = ()):
    for ln in src.splitlines():
        if re.match(r"^\s*(from|import)\s", ln):
            for mod in _FORBIDDEN_IMPORTS:
                if mod in allow_imports:
                    continue
                assert mod not in ln, f"forbidden import: {ln.strip()}"
    for bad in _FORBIDDEN_TOKENS:
        assert bad not in src, f"forbidden token: {bad}"


def test_module_no_order_or_exe():
    _assert_clean((_SRC / "market_data/robust_dataset.py").read_text(encoding="utf-8"))


def test_collection_script_no_order_or_exe():
    _assert_clean((_SCRIPTS / "collect_robust_intraday_dataset.py").read_text(encoding="utf-8"))


def test_validation_script_no_order_or_exe():
    _assert_clean((_SCRIPTS / "validate_robust_intraday_dataset.py").read_text(encoding="utf-8"))


def test_runner_script_no_order_or_exe():
    _assert_clean((_SCRIPTS / "run_robust50_collection_to_final_report.py").read_text(encoding="utf-8"))


# ──────────────────────────── explicit-symbol collect + failed extract ────────────────────────────
def test_failed_symbols_extract():
    C = _import_script("collect_robust_intraday_dataset")
    rep = {"per_symbol": [{"symbol": "A", "status": "FAILED"}, {"symbol": "B", "status": "OK"},
                          {"symbol": "C", "status": "NO_DATA"}, {"symbol": "D", "status": "SKIP"}]}
    assert C.failed_symbols_of(rep) == ["A", "C"]


def test_collect_explicit_symbols_with_fake():
    C = _import_script("collect_robust_intraday_dataset")
    called = {}

    def fake(ns):
        called["symbols"] = ns.symbols
        return {"status": "OK", "succeeded": 1, "requested": 1, "per_symbol": []}

    rep = C.collect_explicit_symbols(["005930"], num_days=5, collect_fn=fake)
    assert rep["stage"] == "5m" and rep["is_live_authorization"] is False
    assert called["symbols"] == "005930"
    # 빈 리스트는 collect_fn 호출 없이 short-circuit.
    empty = C.collect_explicit_symbols([], num_days=5, collect_fn=lambda ns: 1 / 0)
    assert empty["status"] == "OK" and empty["succeeded"] == 0


# ──────────────────────────── runner chain (fake collect + validate) ────────────────────────────
def test_runner_chain_collect_retry_validate(monkeypatch, tmp_path):
    R = _import_script("run_robust50_collection_to_final_report")
    calls = {"retry": 0}

    def fake_collect(**kw):
        return {"status": "PARTIAL_SUCCESS", "requested": 35, "succeeded": 33, "skipped": 0,
                "failed": 2, "total_bars": 5000,
                "per_symbol": [{"symbol": "000270", "status": "FAILED"},
                               {"symbol": "114800", "status": "NO_DATA"},
                               {"symbol": "005930", "status": "OK"}]}

    def fake_retry(symbols, **kw):
        calls["retry"] += 1
        return {"status": "OK", "per_symbol": [{"symbol": s, "status": "OK"} for s in symbols]}

    rdir = tmp_path / "reports"
    rdir.mkdir()
    (rdir / "robust_dataset_manifest.json").write_text(json.dumps({
        "trading_days": 250, "actual_period": "2025-05-15 ~ 2026-05-26 (250 거래일)",
        "start_date": "2025-05-15", "end_date": "2026-05-26", "symbol_count": 35,
        "data_quality_status": "WARN", "time_split_status": "PASS", "regime_label_status": "OK",
        "one_minute_availability": "UNAVAILABLE", "ready_for_robust_backtest": True,
        "warnings": [], "next_recommended_task": "x"}), encoding="utf-8")
    (rdir / "robust_dataset_quality.json").write_text(json.dumps({
        "total_bars": 99999, "symbols_present": 35,
        "per_symbol": [{"present": True, "day_count": 250}], "group_quality": {}}), encoding="utf-8")

    monkeypatch.setattr(R.C, "run_robust_collection", lambda **kw: fake_collect(**kw))
    monkeypatch.setattr(R.C, "collect_explicit_symbols", fake_retry)
    monkeypatch.setattr(R.V, "main", lambda argv=None: 0)

    s = R.run_to_final(report_dir=str(rdir), max_retry_failed=2, write_latest=True)
    assert s["collection_status"] == "PARTIAL_SUCCESS"
    assert calls["retry"] == 1  # 1회 재시도로 모두 회복 → 중단
    assert s["remaining_failed_symbols"] == []
    assert s["trading_days"] == 250 and s["ready_for_robust_backtest"] is True
    assert s["backtest_executed"] is False and s["is_live_authorization"] is False
    assert s["do_not_auto_apply"] is True and s["no_profit_guarantee"] is True
    assert (rdir / "robust50_final_collection_summary.json").exists()
    assert (rdir / "robust50_final_collection_summary.md").exists()


# ──────────────────────────── CLI orchestration (fake, no network) ────────────────────────────
def _import_script(name: str):
    if str(_SCRIPTS) not in sys.path:
        sys.path.insert(0, str(_SCRIPTS))
    import importlib
    return importlib.import_module(name)


def test_collection_orchestration_with_fake():
    C = _import_script("collect_robust_intraday_dataset")
    seen = {}

    def fake(ns):
        seen["bar"] = ns.bar_size
        n = len(ns.symbols.split(","))
        return {"status": "OK", "requested": n, "succeeded": n, "failed": 0,
                "total_bars": 99, "trading_day_count": 220, "per_symbol": []}

    rep = C.run_robust_collection(
        stage="5m", symbol_set="robust30", period="1y", num_days=None, end=None,
        resume=False, sleep_seconds=0, rate_max_calls=2, rate_window=1.1,
        max_calls_per_day=6, collect_fn=fake)
    assert rep["status"] == "OK" and rep["succeeded"] == 30
    assert rep["stage"] == "5m" and seen["bar"] == "5m"
    assert rep["is_live_authorization"] is False and rep["kis_order_api_called"] is False


def test_one_minute_availability_unavailable_not_fail():
    C = _import_script("collect_robust_intraday_dataset")
    rep = {"succeeded": 0, "requested": 10, "per_symbol": []}
    av = C.build_one_minute_availability(rep, "data/market/robust_intraday_1m_subset")
    assert av["availability"] == "UNAVAILABLE"  # FAIL 이 아님
    rep2 = {"succeeded": 10, "requested": 10, "per_symbol": []}
    assert C.build_one_minute_availability(rep2, "x")["availability"] == "AVAILABLE"


def test_validation_run_with_injected_loader():
    V = _import_script("validate_robust_intraday_dataset")
    g = rd.build_robust_symbol_groups()
    days = _trading_days(225)

    def loader(syms):
        return {s: _make_bars(s, days) for s in syms[:32]}

    res = V.run_validation(dir_5m="x", dir_1m="y", loader=loader)
    assert res["manifest"]["data_quality_status"] in (rd.PASS, rd.WARN)
    assert res["manifest"]["trading_days"] == 225
    st = V.build_status(res)
    assert st["is_live_authorization"] is False and st["do_not_auto_apply"] is True
    assert "ready_for_robust_backtest" in st


# ──────────────────────────── endpoint ────────────────────────────
def test_robust_dataset_endpoint(client, safe_default_flags):
    r = client.get("/api/system/robust-dataset/status")
    assert r.status_code == 200, r.text
    d = r.json()
    assert "ready_for_robust_backtest" in d
    assert d["is_live_authorization"] is False
    assert d["real_order_allowed"] is False
    assert d["kis_order_api_called"] is False
    assert d["broker_order_sent"] is False
    assert d["do_not_auto_apply"] is True
    assert d["no_profit_guarantee"] is True
    assert not re.search(r"sk-[A-Za-z0-9]{20,}", r.text)


def test_robust_dataset_endpoint_get_only(client, safe_default_flags):
    assert client.post("/api/system/robust-dataset/status", json={}).status_code in (404, 405)

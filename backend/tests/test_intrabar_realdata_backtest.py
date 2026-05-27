"""실제 1분봉 intrabar 백테스트 오케스트레이터 테스트 (fixture, 실주문 0건).

실제 production 1분봉은 없으므로(장 마감/미수집) tmp fixture 로 *실데이터 코드 경로*를
검증하고, 빈 데이터 → NOT_READY 도 확인. broker/KIS 주문 호출 0건.
"""

from __future__ import annotations

import re
from datetime import datetime, timedelta, timezone
from pathlib import Path

from app.system.intrabar_realdata_backtest import run_realdata_backtest

_KST = timezone(timedelta(hours=9))


def _write_csv(path: Path, rows: list[tuple]):
    path.parent.mkdir(parents=True, exist_ok=True)
    lines = ["timestamp,open,high,low,close,volume,symbol"]
    for ts, o, h, lo, c, v, sym in rows:
        lines.append(f"{ts},{o},{h},{lo},{c},{v},{sym}")
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def _gen_day_5m(sym, day, *, breakout=True):
    """18개 5분봉 — opening range [99.5,100.5], 09:30 돌파."""
    base = datetime.fromisoformat(f"{day}T09:00:00+09:00")
    rows = []
    for i in range(6):  # 09:00~09:25 opening range
        ts = (base + timedelta(minutes=5 * i)).isoformat()
        rows.append((ts, 100.0, 100.5, 99.5, 100.0, 1000, sym))
    # 09:30 돌파봉
    ts7 = (base + timedelta(minutes=30)).isoformat()
    rows.append((ts7, 100.5, 101.2 if breakout else 100.4, 100.0, 101.0 if breakout else 100.1,
                 3000, sym))
    for i in range(8, 18):  # 이후 봉
        ts = (base + timedelta(minutes=5 * i)).isoformat()
        rows.append((ts, 101.0, 101.6, 100.8, 101.4, 1200, sym))
    return rows


def _gen_day_1m(sym, day, *, target_first=True):
    """90개 1분봉 — 09:30 이후 target(101.5) 또는 stop(99.5) 먼저 닿게."""
    base = datetime.fromisoformat(f"{day}T09:00:00+09:00")
    rows = []
    for i in range(150):  # 09:00~11:30 (충분)
        ts = (base + timedelta(minutes=i)).isoformat()
        if i < 30:
            rows.append((ts, 100.0, 100.4, 99.7, 100.1, 200, sym))
        elif target_first:
            # 09:31부터 상승 → target 101.5 도달
            px = 100.5 + (i - 30) * 0.05
            rows.append((ts, px, min(px + 0.2, 102.0), px - 0.1, px, 300, sym))
        else:
            px = 100.5 - (i - 30) * 0.05
            rows.append((ts, px, px + 0.1, max(px - 0.2, 99.0), px, 300, sym))
    return rows


def _build_fixture(tmp_path, target_first=True):
    one = tmp_path / "1m"
    five = tmp_path / "5m"
    for sym in ("005930", "000660"):
        rows5 = []
        rows1 = []
        for day in ("2026-05-20", "2026-05-21"):
            rows5 += _gen_day_5m(sym, day)
            rows1 += _gen_day_1m(sym, day, target_first=target_first)
        _write_csv(five / f"{sym}_5m.csv", rows5)
        _write_csv(one / f"{sym}_1m.csv", rows1)
    return one, five


def test_realdata_backtest_runs_on_fixture(tmp_path):
    one, five = _build_fixture(tmp_path, target_first=True)
    r = run_realdata_backtest(one_min_dir=one, five_min_dir=five,
                              symbols=["005930", "000660"])
    assert r["available"] is True
    assert r["coverage"]["symbol_count_1m"] == 2
    assert r["coverage"]["replayable_trades"] >= 1
    # 5m vs 1m 비교 metrics 존재.
    assert r["execution_5m_vs_1m"]["intrabar_1m"]["trade_count"] >= 1
    assert "profit_factor" in r["execution_5m_vs_1m"]["intrabar_1m"]
    # ranking 비교.
    assert "earliest_first_selected" in r["ranking"]
    assert "composite_selected" in r["ranking"]
    # cost stress 4단계.
    slips = [s["slippage_bps"] for s in r["cost_slippage_stress"]]
    assert slips == [5.0, 7.0, 10.0, 15.0]
    # attribution + replay sample.
    assert isinstance(r["regime_attribution"], dict)
    assert isinstance(r["symbol_group_attribution"], dict)
    assert len(r["trade_replay_sample"]) >= 1
    # 안전 불변.
    assert r["is_live_authorization"] is False
    assert r["is_order_signal"] is False
    assert r["auto_apply_allowed"] is False
    assert r["no_profit_guarantee"] is True


def test_trade_replay_sample_fields(tmp_path):
    one, five = _build_fixture(tmp_path)
    r = run_realdata_backtest(one_min_dir=one, five_min_dir=five)
    sample = r["trade_replay_sample"][0]
    for k in ("symbol", "strategy", "entry_price", "stop_price", "target_price",
              "exit_price_1m", "exit_reason_1m", "exit_reason_5m", "execution_source",
              "net_pnl_1m", "net_pnl_5m", "regime", "symbol_group"):
        assert k in sample, f"missing replay field: {k}"


def test_slippage_stress_increases_cost(tmp_path):
    one, five = _build_fixture(tmp_path)
    r = run_realdata_backtest(one_min_dir=one, five_min_dir=five)
    stress = {s["slippage_bps"]: s["return_pct"] for s in r["cost_slippage_stress"]}
    # 슬리피지 클수록 return 낮음 (또는 같음).
    if stress.get(5.0) is not None and stress.get(15.0) is not None:
        assert stress[15.0] <= stress[5.0]


def test_aligned_mode_high_coverage_4way(tmp_path):
    """align_to_1m=True → 1분봉 날짜로 제한, coverage↑, 4-way 비교 산출."""
    one, five = _build_fixture(tmp_path, target_first=True)
    r = run_realdata_backtest(one_min_dir=one, five_min_dir=five,
                              symbols=["005930", "000660"], align_to_1m=True)
    assert r["align_to_1m"] is True
    ac = r["aligned_coverage"]
    assert ac is not None
    # aligned → 모든 거래가 1분봉 replay (coverage 100%).
    assert ac["coverage_pct"] == 100.0
    assert ac["one_minute_replayed_count"] == ac["aligned_total_trades"]
    fw = r["aligned_comparison"]
    for k in ("basic_5m_earliest_first", "basic_5m_composite",
              "intrabar_1m_earliest_first", "intrabar_1m_composite"):
        assert k in fw, f"missing 4-way key: {k}"
    # fixture 는 2 거래일뿐 → 기간 짧음 → LOW.
    assert r["verdict"] == "PERIOD_TOO_SHORT_LOW_CONFIDENCE"
    assert r["confidence_level"] == "LOW"
    assert r["is_live_authorization"] is False


def test_empty_data_not_ready(tmp_path):
    r = run_realdata_backtest(one_min_dir=tmp_path / "none", five_min_dir=tmp_path / "none5",
                              symbols=["005930"])
    assert r["available"] is False
    assert r["verdict"] == "REALDATA_BACKTEST_NOT_READY"
    assert r["confidence_level"] == "LOW"
    assert r["is_live_authorization"] is False


def test_no_profit_guarantee_phrases(tmp_path):
    import json
    one, five = _build_fixture(tmp_path)
    r = run_realdata_backtest(one_min_dir=one, five_min_dir=five)
    blob = json.dumps(r, ensure_ascii=False)
    for banned in ("수익 보장", "실전 가능", "실전 전환 승인"):
        assert banned not in blob, f"banned phrase: {banned}"


def test_module_no_order_imports():
    src = Path(__file__).resolve().parents[1] / "app/system/intrabar_realdata_backtest.py"
    txt = src.read_text(encoding="utf-8")
    for bad in (".place_order(", "route_order(", "OrderExecutor(", "brokers.kis import",
                "cargo build", "tauri build", "inquire-time-dailychartprice"):
        for line in txt.splitlines():
            assert bad not in line.split("#", 1)[0], f"banned: {bad}"

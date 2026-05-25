"""KIS-INTRADAY-50-6M-STRATEGY-AGENT-DECOMPOSITION-01 — sim_v2 확장 / 분해 / 안전 테스트.

합성 bar + 주입 신호. 실제 수집 데이터 비의존.
"""

from __future__ import annotations

import re
from datetime import datetime, timedelta, timezone
from pathlib import Path


from app.backtest.strategy_council_backtest import OHLCVBar
from app.system.wf_6m_sim_v2 import SimV2Config, run_sim_v2

_KST = timezone(timedelta(hours=9))
_SRC = Path(__file__).resolve().parents[1] / "app"


def _day(symbol, date, prices):
    y, m, d = int(date[:4]), int(date[4:6]), int(date[6:8])
    out = []
    for i, p in enumerate(prices):
        ts = datetime(y, m, d, 9, 0, tzinfo=_KST) + timedelta(minutes=5 * i)
        out.append(OHLCVBar(symbol=symbol, timestamp=ts, open=p, high=p * 1.001,
                            low=p * 0.999, close=p, volume=10000))
    return out


def _sig(mod, council="BUY", single=("ORB",), stop=1.5, target=3.0, conf=0.8,
         bucket="09:00-09:30", fwd=0.01, qs=70.0):
    return {"council_action": council, "confidence": conf, "quality_score": qs,
            "stop_pct": stop, "target_pct": target, "selected": single,
            "single_buys": tuple(single), "minute_of_day": mod, "time_bucket": bucket,
            "fwd_eod_return": fwd}


def _setup(symbols, date="20260120", n=12, signal_at=5, strat=("ORB",), council="BUY", rise=True):
    bars, signals = [], {}
    for s in symbols:
        if rise:
            prices = [10000.0] * (signal_at + 1) + [10300.0, 10400.0] + [10400.0] * (n - signal_at - 3)
        else:
            prices = [10000.0] * (signal_at + 1) + [9800.0, 9700.0] + [9700.0] * (n - signal_at - 3)
        b = _day(s, date, prices[:n])
        bars += b
        kst = b[signal_at].timestamp.astimezone(_KST)
        signals[(s, b[signal_at].timestamp.isoformat())] = _sig(
            kst.hour * 60 + kst.minute, council=council, single=strat)
    return bars, signals


# ─────────── 매매기법 필터 ───────────

def test_allowed_strategies_filters_signals():
    bars, signals = _setup(["005930"], strat=("VWAP",))
    r_gap = run_sim_v2(bars, signals, SimV2Config(agent_mode="AGENT_OFF",
                                                  allowed_strategies=frozenset({"GAP"})))
    r_vwap = run_sim_v2(bars, signals, SimV2Config(agent_mode="AGENT_OFF",
                                                   allowed_strategies=frozenset({"VWAP"})))
    assert r_gap.trade_count == 0     # VWAP 신호만 있는데 GAP 허용 → 진입 0.
    assert r_vwap.trade_count == 1


# ─────────── 7 Agent 역할 ───────────

def test_agent_review_only_equals_single_union():
    bars, signals = _setup(["005930"], council="HOLD", strat=("ORB",))
    r_review = run_sim_v2(bars, signals, SimV2Config(agent_mode="AGENT_REVIEW_ONLY"))
    r_off = run_sim_v2(bars, signals, SimV2Config(agent_mode="AGENT_OFF"))
    assert r_review.trade_count == r_off.trade_count == 1


def test_agent_risk_veto_only_blocks_exclude_grade():
    bars, signals = _setup(["005930", "000660"], strat=("ORB",))
    grade = {"005930": "GO", "000660": "EXCLUDE"}
    r = run_sim_v2(bars, signals, SimV2Config(agent_mode="AGENT_RISK_VETO_ONLY", grade_map=grade))
    # EXCLUDE(000660) 진입 차단 → 1 종목만.
    assert r.trade_count == 1


def test_agent_entry_selector_uses_council():
    bars, signals = _setup(["005930"], council="HOLD", strat=("ORB",))
    r = run_sim_v2(bars, signals, SimV2Config(agent_mode="AGENT_ENTRY_SELECTOR"))
    assert r.trade_count == 0  # council HOLD → 진입 0.


def test_position_sizer_scales_by_grade():
    bars, signals = _setup(["005930"], strat=("ORB",))
    # min notional 을 낮춰 GO/TUNE 모두 진입 가능하게 한 뒤, GO 비용(수량 비례)이 더 큼을 확인.
    cfg = dict(agent_mode="AGENT_POSITION_SIZER_ONLY", min_position_notional=400_000.0)
    r_go = run_sim_v2(bars, signals, SimV2Config(grade_map={"005930": "GO"}, **cfg))
    r_tune = run_sim_v2(bars, signals, SimV2Config(grade_map={"005930": "TUNE"}, **cfg))
    assert r_go.trade_count == 1 and r_tune.trade_count == 1
    # GO 사이즈 > TUNE 사이즈 → GO 의 매수 슬리피지/수수료가 더 큼.
    assert r_go.slippage_paid_total > r_tune.slippage_paid_total


# ─────────── 청산 실험 ───────────

def test_max_hold_forces_exit():
    # 신호 후 상승하지 않고 횡보 → max_hold 가 EOD 보다 먼저 청산.
    bars, signals = _setup(["005930"], rise=False, n=20, signal_at=3)
    r = run_sim_v2(bars, signals, SimV2Config(max_hold_min=30))
    # hold 30분 = 6 bar → EOD 전에 청산되어 avg_hold ≤ ~35분.
    assert r.trade_count == 1
    assert r.avg_hold_minutes is not None and r.avg_hold_minutes <= 40


def test_veto_exclude_grade_flag():
    bars, signals = _setup(["005930"], strat=("ORB",))
    r = run_sim_v2(bars, signals, SimV2Config(veto_exclude_grade=True,
                                              grade_map={"005930": "EXCLUDE"}))
    assert r.trade_count == 0


def test_size_scale_reduces_notional():
    bars, signals = _setup(["005930"], strat=("ORB",))
    r_full = run_sim_v2(bars, signals, SimV2Config(size_scale=1.0))
    r_half = run_sim_v2(bars, signals, SimV2Config(size_scale=0.5))
    # 둘 다 진입하지만 half 의 비용(수량 비례)이 더 작음.
    assert r_half.cost_paid_total <= r_full.cost_paid_total


# ─────────── 정적 가드 ───────────

def test_decomposition_module_no_order_calls():
    src = (_SRC / "system/wf_6m_strategy_agent_decomposition.py").read_text(encoding="utf-8")
    for ln in src.splitlines():
        if re.match(r"^\s*(from|import)\s", ln):
            for mod in ("app.brokers", "app.execution", "order_router",
                        "anthropic", "openai", "httpx", "requests", "app.ai.client"):
                assert mod not in ln, f"forbidden import: {ln.strip()}"
    for call in ("route_order(", ".place_order(", "OrderExecutor(", ".cancel_order("):
        assert call not in src
    # EXE 빌드 명령 0건.
    for exe in ("tauri build", "cargo build", "cargo tauri", "subprocess"):
        assert exe not in src


def test_decomposition_script_no_order_or_exe():
    src = (Path(__file__).resolve().parents[2] / "scripts"
           / "run_wf_6m_strategy_agent_decomposition.py").read_text(encoding="utf-8")
    for bad in ("route_order(", ".place_order(", "OrderExecutor(", "tauri build", "cargo build"):
        assert bad not in src


# ─────────── endpoint ───────────

def test_decomposition_endpoint(client, safe_default_flags):
    r = client.get("/api/system/strategy-agent-decomposition/latest")
    assert r.status_code == 200, r.text
    d = r.json()
    assert "final_verdict" in d
    assert "exe_rebuild_recommendation" in d
    assert d["is_live_authorization"] is False
    assert d["auto_apply_allowed"] is False
    assert d["exe_build_executed"] is False
    assert d["no_profit_guarantee"] is True
    assert not re.search(r"sk-[A-Za-z0-9]{20,}", r.text)
    assert not re.search(r"\b\d{8}-\d{2}\b", r.text)


def test_decomposition_endpoint_get_only(client, safe_default_flags):
    assert client.post("/api/system/strategy-agent-decomposition/latest",
                       json={}).status_code in (404, 405)

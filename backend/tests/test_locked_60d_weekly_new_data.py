"""KIS-INTRADAY-60D-WEEKLY-NEW-DATA — rule hash lock / 새 데이터 부족 / 안전 테스트.

합성 tmp CSV. 실제 수집/네트워크 비의존.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from app.system import locked_60d_weekly_new_data_validation as nd

_SRC = Path(__file__).resolve().parents[1] / "app"


def _csv(path: Path, dates: list[str]):
    lines = ["timestamp,open,high,low,close,volume,symbol"]
    for d in dates:
        lines.append(f"{d}T09:05:00+09:00,100,101,99,100,1000,005930")
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


# ─────────── rule hash lock (immutability) ───────────

def test_rule_hash_matches_expected():
    # LOCKED_PARAMS 가 바뀌면 이 테스트가 실패 → 룰 변경 감지.
    assert nd.compute_rule_hash() == nd.EXPECTED_LOCKED_RULE_HASH


# ─────────── new-data sufficiency (no heavy backtest) ───────────

def _dirs(tmp_path):
    ex = tmp_path / "existing"
    xt = tmp_path / "extra"
    ex.mkdir()
    xt.mkdir()
    return ex, xt


def test_insufficient_when_no_new_dates(tmp_path):
    ex, xt = _dirs(tmp_path)
    _csv(ex / "005930_5m.csv", ["2026-05-20", "2026-05-21", "2026-05-22"])
    _csv(xt / "005930_5m.csv", ["2026-05-21", "2026-05-22"])  # 전부 overlap → new=0.
    r = nd.run_new_data_validation(existing_dir=ex, extra_dir=xt)
    assert r.final_verdict == nd.NEW_DATA_INSUFFICIENT
    assert r.new_trading_days == 0
    assert r.rule_hash_match is True
    assert r.exe_build_executed is False
    assert r.live_trading_recommendation is False


def test_insufficient_when_few_new_dates(tmp_path):
    ex, xt = _dirs(tmp_path)
    _csv(ex / "005930_5m.csv", ["2026-05-20"])
    _csv(xt / "005930_5m.csv", [f"2026-06-{d:02d}" for d in range(1, 6)])  # 5 new < 20.
    r = nd.run_new_data_validation(existing_dir=ex, extra_dir=xt)
    assert r.final_verdict == nd.NEW_DATA_INSUFFICIENT
    assert r.new_trading_days == 5


def test_empty_extra_dir_insufficient(tmp_path):
    ex, xt = _dirs(tmp_path)
    _csv(ex / "005930_5m.csv", ["2026-05-20", "2026-05-21"])
    r = nd.run_new_data_validation(existing_dir=ex, extra_dir=xt)
    assert r.final_verdict == nd.NEW_DATA_INSUFFICIENT
    assert r.new_trading_days == 0


# ─────────── verdict thresholds ───────────

def _h(**kw):
    base = {"forward_return_pct": 0.0, "median_pf": 1.0, "forward_mdd_pct": 10.0, "total_trades": 80}
    base.update(kw)
    return base


def test_verdict_new_thresholds():
    assert nd._verdict_new(_h(forward_return_pct=-1), slip_ok=True,
                           risk_veto_better=True) == nd.NEW_DATA_FAIL
    assert nd._verdict_new(_h(forward_return_pct=4, median_pf=1.12, forward_mdd_pct=14),
                           slip_ok=False, risk_veto_better=False) == nd.NEW_DATA_WATCH
    assert nd._verdict_new(_h(forward_return_pct=6, median_pf=1.18, forward_mdd_pct=13),
                           slip_ok=True, risk_veto_better=True) == nd.NEW_DATA_PAPER_CANDIDATE
    assert nd._verdict_new(_h(forward_return_pct=9, median_pf=1.25, forward_mdd_pct=10, total_trades=120),
                           slip_ok=True, risk_veto_better=True) == nd.NEW_DATA_RESEARCH_CONFIRMED
    # PAPER 조건이라도 RISK_VETO 열위 또는 slippage 붕괴면 WATCH.
    assert nd._verdict_new(_h(forward_return_pct=6, median_pf=1.18, forward_mdd_pct=13),
                           slip_ok=False, risk_veto_better=True) == nd.NEW_DATA_WATCH
    assert nd._verdict_new(_h(forward_return_pct=6, median_pf=1.18, forward_mdd_pct=13),
                           slip_ok=True, risk_veto_better=False) == nd.NEW_DATA_WATCH


# ─────────── dataclass invariants ───────────

def _report(**kw):
    base = dict(
        generated_at="t", data_source="x", locked_rule_name="L", locked_rule_hash="h",
        rule_hash_match=True, rule_locked_before_new_data_validation=True, no_parameter_change=True,
        no_look_ahead=True, additional_data_collected=False, new_data_quality={}, new_trading_days=0,
        new_data_only={}, extended_walk_forward={}, slippage_stress={}, agent_mode_compare={},
        breadth_stress={}, old_vs_new_decay={}, repeated_selected_change={}, overfit_warning={},
        final_verdict=nd.NEW_DATA_INSUFFICIENT, paper_rehearsal_recommendation="x",
        exe_rebuild_recommendation="x", next_steps=(), conclusions=())
    base.update(kw)
    return nd.NewDataReport(**base)


def test_report_rejects_unsafe():
    _report()
    for bad in ({"is_live_authorization": True}, {"live_trading_recommendation": True},
                {"real_order_allowed": True}, {"dry_run_required": False},
                {"exe_build_executed": True}, {"auto_apply_allowed": True},
                {"final_verdict": "BOGUS"}):
        with pytest.raises(ValueError):
            _report(**bad)


# ─────────── static guards ───────────

def test_module_no_order_or_exe():
    src = (_SRC / "system/locked_60d_weekly_new_data_validation.py").read_text(encoding="utf-8")
    for ln in src.splitlines():
        if re.match(r"^\s*(from|import)\s", ln):
            for mod in ("app.brokers", "app.execution", "order_router",
                        "anthropic", "openai", "httpx", "requests", "app.ai.client"):
                assert mod not in ln, f"forbidden import: {ln.strip()}"
    for bad in ("route_order(", ".place_order(", "OrderExecutor(", ".cancel_order(",
                "tauri build", "cargo build", "cargo tauri"):
        assert bad not in src


def test_scripts_no_order_or_exe():
    for script in ("run_locked_60d_weekly_new_data_validation.py", "collect_forward_extra_ohlcv.py"):
        src = (Path(__file__).resolve().parents[2] / "scripts" / script).read_text(encoding="utf-8")
        for bad in ("route_order(", ".place_order(", "/trading/order-cash", "OrderExecutor(",
                    "tauri build", "cargo build"):
            assert bad not in src


# ─────────── endpoint ───────────

def test_new_data_endpoint(client, safe_default_flags):
    r = client.get("/api/system/locked-60d-weekly-new-data/latest")
    assert r.status_code == 200, r.text
    d = r.json()
    assert "final_verdict" in d
    assert d["is_live_authorization"] is False
    assert d["live_trading_recommendation"] is False
    assert d["real_order_allowed"] is False
    assert d["dry_run_required"] is True
    assert d["exe_build_executed"] is False
    assert d["no_profit_guarantee"] is True
    assert not re.search(r"sk-[A-Za-z0-9]{20,}", r.text)


def test_new_data_endpoint_get_only(client, safe_default_flags):
    assert client.post("/api/system/locked-60d-weekly-new-data/latest",
                       json={}).status_code in (404, 405)

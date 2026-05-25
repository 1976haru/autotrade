"""KIS-INTRADAY-1Y-SCALED-VALIDATION-01 — rule hash / 단계 verdict / merge / 안전 테스트.

합성 tmp CSV + pure-helper 단위. 실제 수집/네트워크/heavy backtest 비의존.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from app.system import intraday_1y_scaled_validation as sv

_SRC = Path(__file__).resolve().parents[1] / "app"


def _csv(path: Path, sym: str, dates: list[str]):
    lines = ["timestamp,open,high,low,close,volume,symbol"]
    for d in dates:
        lines.append(f"{d}T09:05:00+09:00,100,101,99,100,1000,{sym}")
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


# ─────────── rule hash lock ───────────

def test_rule_hash_match():
    assert sv.compute_rule_hash() == sv.EXPECTED_LOCKED_RULE_HASH


# ─────────── load_year merge / dedup ───────────

def test_load_year_merges_and_dedups(tmp_path):
    d1 = tmp_path / "recent"
    d2 = tmp_path / "old"
    d1.mkdir()
    d2.mkdir()
    _csv(d1 / "005930_5m.csv", "005930", ["2025-11-25", "2025-11-26"])
    _csv(d2 / "005930_5m.csv", "005930", ["2025-11-24", "2025-11-25"])  # 11-25 overlap.
    bars = sv._load_year([d1, d2])
    ts = {b.timestamp.isoformat() for b in bars}
    assert len(ts) == 3  # 24,25,26 — 25 중복 제거.


# ─────────── quality ───────────

def test_quality_status(tmp_path):
    d = tmp_path / "y"
    d.mkdir()
    _csv(d / "005930_5m.csv", "005930", [f"2025-{m:02d}-{day:02d}"
                                         for m in range(1, 13) for day in (5, 15, 25)])
    bars = sv._load_year([d])
    q = sv._quality(bars)
    assert q["symbol_count"] == 1
    assert q["total_trading_days"] == 36


# ─────────── stage verdict ───────────

def _r(**kw):
    base = {"forward_return_pct": 0.0, "median_pf": 1.0, "forward_mdd_pct": 10.0,
            "total_trades": 200, "positive_ratio": 0.5}
    base.update(kw)
    return base


def test_stage_verdict_thresholds():
    assert sv._stage_verdict(_r(forward_return_pct=-1), slip_ok=True,
                             risk_veto_better=True) == sv.SCALE_FAIL
    assert sv._stage_verdict(_r(forward_return_pct=4, median_pf=1.12, forward_mdd_pct=14),
                             slip_ok=False, risk_veto_better=False) == sv.SCALE_WATCH
    assert sv._stage_verdict(_r(forward_return_pct=6, median_pf=1.18, forward_mdd_pct=13, total_trades=200),
                             slip_ok=True, risk_veto_better=True) == sv.SCALE_PAPER_CANDIDATE
    assert sv._stage_verdict(_r(forward_return_pct=9, median_pf=1.25, forward_mdd_pct=10, total_trades=200),
                             slip_ok=True, risk_veto_better=True) == sv.SCALE_RESEARCH_CONFIRMED
    # 거래 부족(<150) 이면 PAPER 미만.
    assert sv._stage_verdict(_r(forward_return_pct=6, median_pf=1.18, forward_mdd_pct=13, total_trades=80),
                             slip_ok=True, risk_veto_better=True) == sv.SCALE_WATCH


# ─────────── insufficient data short-circuit ───────────

def test_insufficient_year_data(tmp_path):
    d = tmp_path / "y"
    d.mkdir()
    _csv(d / "005930_5m.csv", "005930", ["2025-11-25", "2025-11-26", "2025-11-27"])  # 3일 < 200.
    r = sv.run_scaled_1y_validation(dirs=[d])
    assert r.final_verdict == sv.SCALE_FAIL
    assert r.rule_hash_match is True
    assert r.exe_build_executed is False
    assert r.live_trading_recommendation is False


# ─────────── dataclass invariants ───────────

def _report(**kw):
    base = dict(
        generated_at="t", data_source="x", locked_rule_name="L", locked_rule_hash="h",
        rule_hash_match=True, rule_locked_before_validation=True, no_parameter_change=True,
        no_look_ahead=True, data_quality={}, trading_days=0, stage_10={}, stage_25={}, stage_50={},
        scale_analysis={}, monthly_quarterly={}, half_split={}, worst_month={}, symbol_split={},
        agent_compare={}, slippage_stress={}, defense_analysis={}, repeated_selected=(),
        repeated_excluded=(), breadth_dependency={}, final_verdict=sv.SCALE_FAIL,
        paper_rehearsal_recommendation="x", exe_rebuild_recommendation="x", next_steps=(),
        conclusions=())
    base.update(kw)
    return sv.Scaled1YReport(**base)


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
    src = (_SRC / "system/intraday_1y_scaled_validation.py").read_text(encoding="utf-8")
    for ln in src.splitlines():
        if re.match(r"^\s*(from|import)\s", ln):
            for mod in ("app.brokers", "app.execution", "order_router",
                        "anthropic", "openai", "httpx", "requests", "app.ai.client"):
                assert mod not in ln, f"forbidden import: {ln.strip()}"
    for bad in ("route_order(", ".place_order(", "OrderExecutor(", ".cancel_order(",
                "tauri build", "cargo build", "cargo tauri"):
        assert bad not in src


def test_script_no_order_or_exe():
    src = (Path(__file__).resolve().parents[2] / "scripts"
           / "run_intraday_1y_scaled_validation.py").read_text(encoding="utf-8")
    for bad in ("route_order(", ".place_order(", "OrderExecutor(", "tauri build", "cargo build"):
        assert bad not in src


# ─────────── endpoint ───────────

def test_1y_endpoint(client, safe_default_flags):
    r = client.get("/api/system/intraday-1y-scaled-validation/latest")
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


def test_1y_endpoint_get_only(client, safe_default_flags):
    assert client.post("/api/system/intraday-1y-scaled-validation/latest",
                       json={}).status_code in (404, 405)

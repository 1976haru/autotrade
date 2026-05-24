"""REAL-DATA-INPUT-01 — real_ohlcv_dataset 오케스트레이터 + CLI 테스트."""

from __future__ import annotations

import json
import re
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

from app.system import strategy_potential as sp
from app.system.real_ohlcv_dataset import (
    RealOhlcvDatasetReport,
    evaluate_real_ohlcv_dataset,
    render_markdown,
    to_dict,
)

_REPO = Path(__file__).resolve().parents[2]
_CLEAN = _REPO / "backend" / "tests" / "fixtures" / "real_data_clean"
_REAL = _REPO / "backend" / "tests" / "fixtures" / "real_data"
_SCRIPT = _REPO / "scripts" / "run_real_ohlcv_backtest_walkforward.py"

_VERDICTS = {sp.STRONG, sp.CAUTIOUS, sp.RESEARCH_ONLY, sp.NOT_READY, sp.BLOCKED}


def _run(args, cwd=None):
    return subprocess.run(
        [sys.executable, str(_SCRIPT), *args],
        capture_output=True, text=True, encoding="utf-8", errors="replace",
        cwd=str(cwd or _REPO))


# --------------------------- module ----------------------------------------

def test_dataset_clean_dir_all_pass():
    r = evaluate_real_ohlcv_dataset(_CLEAN)
    assert r.symbols_count == 10
    assert len(r.pass_symbols) == 10
    assert len(r.blocked_symbols) == 0
    assert r.real_data_used is True
    assert r.sample_fixture_only is False
    assert r.overall_verdict in _VERDICTS


def test_dataset_not_strong_without_paper_or_trades():
    r = evaluate_real_ohlcv_dataset(_CLEAN)
    # paper 0 + (대개) trades 부족 → STRONG 불가.
    assert r.overall_verdict != sp.STRONG


def test_dataset_broken_symbol_blocked(tmp_path):
    # clean 1개 + 깨진 005930 → 005930 은 blocked, clean 은 pass.
    shutil.copy(_CLEAN / "000660.csv", tmp_path / "000660.csv")
    shutil.copy(_REAL / "005930.csv", tmp_path / "005930.csv")
    r = evaluate_real_ohlcv_dataset(tmp_path)
    assert "005930" in r.blocked_symbols
    assert "000660" in r.pass_symbols


def test_dataset_empty_dir_blocked(tmp_path):
    r = evaluate_real_ohlcv_dataset(tmp_path)
    assert r.overall_verdict == sp.BLOCKED
    assert len(r.pass_symbols) == 0


def test_dataset_median_and_agent_summary_fields():
    r = evaluate_real_ohlcv_dataset(_CLEAN)
    assert r.agent_value_summary in (
        "AGENT_ADDS_VALUE", "AGENT_UNDERPERFORMS",
        "AGENT_VALUE_INSUFFICIENT_SAMPLE", "AGENT_MIXED")
    assert isinstance(r.per_symbol, tuple) and len(r.per_symbol) == 10
    assert r.total_trades >= 0


def test_dataset_report_invariants():
    r = evaluate_real_ohlcv_dataset(_CLEAN)
    assert r.do_not_auto_apply is True
    assert r.auto_apply_allowed is False
    assert r.is_live_authorization is False
    assert r.is_order_signal is False
    assert r.contains_secret is False
    assert r.kis_historical_available is False


@pytest.mark.parametrize("bad", [
    {"do_not_auto_apply": False}, {"auto_apply_allowed": True},
    {"is_live_authorization": True}, {"contains_secret": True},
    {"kis_historical_available": True},
])
def test_dataset_report_guard(bad):
    base = dict(
        generated_at="x", data_source="CSV_REAL_FIXTURE", real_data_used=True,
        sample_fixture_only=False, symbols_count=1, bars_count=100, days_count=100,
        trades_count=0, quality={}, overall_verdict=sp.RESEARCH_ONLY, overall_score=10.0,
        backtest_score=None, walk_forward_score=None, stress_score=None,
        agent_value_score=None, data_sufficiency_score=None, agent_value_verdict="x",
        paper_sample_class="PAPER_NO_TRADES_YET")
    base.update(bad)
    with pytest.raises(ValueError):
        RealOhlcvDatasetReport(**base)


def test_dataset_to_dict_and_markdown():
    r = evaluate_real_ohlcv_dataset(_CLEAN)
    d = to_dict(r)
    for k in ("pass_symbols", "blocked_symbols", "per_symbol", "total_trades",
              "median_profit_factor", "agent_value_summary", "overall_verdict",
              "do_not_auto_apply", "is_live_authorization"):
        assert k in d
    md = render_markdown(r)
    assert "자동 적용 아님" in md and "실전 승인 아님" in md and "수익 보장 아님" in md
    assert not re.search(r"sk-[A-Za-z0-9]{20,}", md)
    assert not re.search(r"\b\d{8}-\d{2}\b", md)


def test_dataset_module_no_forbidden_imports():
    src = (_REPO / "backend" / "app" / "system" / "real_ohlcv_dataset.py").read_text(
        encoding="utf-8")
    for ln in src.splitlines():
        if re.match(r"^\s*(from|import)\s", ln):
            for mod in ("app.brokers", "app.execution", "order_router",
                        "anthropic", "openai", "httpx", "requests", "app.ai.client"):
                assert mod not in ln, f"forbidden import: {ln.strip()}"
    for call in ("route_order(", ".place_order(", "OrderExecutor("):
        assert call not in src


# --------------------------- CLI -------------------------------------------

def test_cli_writes_report_and_latest(tmp_path):
    out = tmp_path / "ds.json"
    md = tmp_path / "ds.md"
    r = _run(["--input-dir", str(_CLEAN), "--output", str(out), "--markdown", str(md),
              "--write-latest", "--quiet"], cwd=tmp_path)
    assert r.returncode == 0, r.stderr
    d = json.loads(out.read_text(encoding="utf-8"))
    assert d["overall_verdict"] in {v for v in _VERDICTS}
    assert d["overall_verdict"] != "STRONG_CANDIDATE"  # paper 0 → STRONG 불가
    assert d["is_live_authorization"] is False
    assert d["do_not_auto_apply"] is True
    latest = tmp_path / "reports" / "strategy_validation" / "real_data_strategy_latest.json"
    assert latest.exists()


def test_cli_empty_dir_blocked_exit_1(tmp_path):
    out = tmp_path / "ds.json"
    r = _run(["--input-dir", str(tmp_path / "empty"), "--output", str(out), "--quiet"])
    # 빈/없는 dir → PASS 0 → BLOCKED → exit 1.
    assert r.returncode == 1, r.stderr

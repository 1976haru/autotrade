"""REAL-DATA-INPUT-01 — ohlcv_collector 모듈 + collect_real_ohlcv_data.py CLI 테스트."""

from __future__ import annotations

import json
import re
import subprocess
import sys
from pathlib import Path

from app.market_data.ohlcv_collector import (
    CollectManifest,
    collect_ohlcv,
    manifest_to_dict,
)

_REPO = Path(__file__).resolve().parents[2]
_CLEAN = _REPO / "backend" / "tests" / "fixtures" / "real_data_clean"
_REAL = _REPO / "backend" / "tests" / "fixtures" / "real_data"  # 005930.csv 깨짐 포함
_SCRIPT = _REPO / "scripts" / "collect_real_ohlcv_data.py"

_SYMBOLS = ["005930", "000660", "035420", "035720", "005380",
            "000270", "006400", "373220", "005490", "068270"]


def _run(args, cwd=None):
    return subprocess.run(
        [sys.executable, str(_SCRIPT), *args],
        capture_output=True, text=True, encoding="utf-8", errors="replace",
        cwd=str(cwd or _REPO))


# --------------------------- module ----------------------------------------

def test_collect_existing_clean_all_pass(tmp_path):
    m = collect_ohlcv(_SYMBOLS, source="existing", source_dir=_CLEAN,
                      output_dir=tmp_path, min_days=28, recommended_days=100)
    assert len(m.pass_symbols) == 10
    assert len(m.fail_symbols) == 0
    # PASS 종목은 output_dir 에 기록.
    assert (tmp_path / "005930.csv").exists()


def test_collect_broken_ohlc_is_fail_not_written(tmp_path):
    m = collect_ohlcv(["005930"], source="existing", source_dir=_REAL,
                      output_dir=tmp_path, min_days=1, recommended_days=1)
    r = m.results[0]
    assert r.status == "FAIL"          # 잘못된 OHLC
    assert r.written_path is None       # FAIL 데이터는 기록 안 함
    assert not (tmp_path / "005930.csv").exists()


def test_collect_missing_symbol_is_fail_no_fake(tmp_path):
    m = collect_ohlcv(["999999"], source="existing", source_dir=_CLEAN,
                      output_dir=tmp_path, write=False)
    assert m.results[0].status == "FAIL"
    assert "CSV 없음" in m.results[0].reason


def test_collect_yfinance_unavailable_graceful(tmp_path, monkeypatch):
    """yfinance 미설치/실패 시 FAIL — sample/mock 대체 0건."""
    import app.market_data.ohlcv_collector as mod
    monkeypatch.setattr(mod, "_yfinance_available", lambda: False)
    m = collect_ohlcv(["005930"], source="yfinance", output_dir=tmp_path, write=False)
    assert m.results[0].status == "FAIL"
    assert "yfinance" in m.results[0].reason


def test_manifest_to_dict_and_guard():
    m = collect_ohlcv(["005930"], source="existing", source_dir=_CLEAN, write=False)
    d = manifest_to_dict(m)
    assert "pass_symbols" in d and "results" in d and d["contains_secret"] is False
    import pytest
    with pytest.raises(ValueError):
        CollectManifest(generated_at="x", requested_source="existing", symbols=(),
                        results=(), contains_secret=True)


def test_collector_module_no_forbidden_imports():
    src = (_REPO / "backend" / "app" / "market_data" / "ohlcv_collector.py").read_text(
        encoding="utf-8")
    for ln in src.splitlines():
        if re.match(r"^\s*(from|import)\s", ln):
            for mod in ("app.execution", "order_router", "OrderExecutor"):
                assert mod not in ln
    for call in ("route_order(", ".place_order(", "OrderExecutor("):
        assert call not in src


# --------------------------- gitignore -------------------------------------

def test_data_market_real_ohlcv_is_gitignored():
    r = subprocess.run(["git", "check-ignore", "data/market/real_ohlcv/005930.csv"],
                       cwd=str(_REPO), capture_output=True, text=True)
    assert r.returncode == 0, "data/market/real_ohlcv must be gitignored"


def test_reports_strategy_validation_is_gitignored():
    r = subprocess.run(["git", "check-ignore", "reports/strategy_validation/x.json"],
                       cwd=str(_REPO), capture_output=True, text=True)
    assert r.returncode == 0


def test_clean_fixtures_are_committable_and_tracked():
    """clean fixture 는 추적 가능(테스트용 소형)."""
    r = subprocess.run(["git", "check-ignore",
                        "backend/tests/fixtures/real_data_clean/005930.csv"],
                       cwd=str(_REPO), capture_output=True, text=True)
    assert r.returncode != 0, "clean fixtures must NOT be gitignored"


# --------------------------- CLI -------------------------------------------

def test_cli_existing_writes_manifest(tmp_path):
    out = tmp_path / "m.json"
    r = _run(["--source", "existing", "--source-dir", str(_CLEAN),
              "--output-dir", str(tmp_path / "out"), "--json", str(out), "--quiet"])
    assert r.returncode == 0, r.stderr
    d = json.loads(out.read_text(encoding="utf-8"))
    assert len(d["pass_symbols"]) == 10
    assert not re.search(r"sk-[A-Za-z0-9]{20,}", out.read_text(encoding="utf-8"))


def test_cli_all_fail_exit_1(tmp_path):
    out = tmp_path / "m.json"
    r = _run(["--source", "existing", "--source-dir", str(tmp_path),  # 빈 dir
              "--symbols", "005930", "--json", str(out), "--quiet"])
    assert r.returncode == 1

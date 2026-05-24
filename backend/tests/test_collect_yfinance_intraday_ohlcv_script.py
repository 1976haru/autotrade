"""REAL-INTRADAY-TEST-01 — collect_yfinance_intraday_ohlcv.py 테스트.

네트워크/실제 yfinance 호출 없이(미설치/실패 graceful) reason_code 동작 + 합성 대체 0건
+ 종목코드→ticker 변환 + script smoke 를 검증한다.
"""

from __future__ import annotations

import importlib.util
import subprocess
import sys
from pathlib import Path

_REPO = Path(__file__).resolve().parents[2]
_SCRIPT = _REPO / "scripts" / "collect_yfinance_intraday_ohlcv.py"


def _load_script_module():
    spec = importlib.util.spec_from_file_location("collect_yf_intraday", _SCRIPT)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_script_exists():
    assert _SCRIPT.exists()


def test_yahoo_ticker_conversion():
    mod = _load_script_module()
    assert mod._yahoo_ticker("005930") == "005930.KS"
    assert mod._yahoo_ticker("000660") == "000660.KS"
    assert mod._yahoo_ticker("005930.KS") == "005930.KS"  # 이미 변환된 것 유지


def test_fetch_one_yfinance_not_installed(monkeypatch):
    """yfinance 미설치 → YFINANCE_NOT_INSTALLED, records 비어있음(합성 대체 0)."""
    mod = _load_script_module()
    import builtins
    real_import = builtins.__import__

    def _fake_import(name, *a, **k):
        if name == "yfinance":
            raise ImportError("no yfinance")
        return real_import(name, *a, **k)

    monkeypatch.setattr(builtins, "__import__", _fake_import)
    records, reason = mod.fetch_one("005930", interval="5m", period="60d")
    assert records == []
    assert reason == "YFINANCE_NOT_INSTALLED"


def test_help_smoke():
    r = subprocess.run([sys.executable, str(_SCRIPT), "--help"],
                       capture_output=True, text=True, encoding="utf-8", errors="replace")
    assert r.returncode == 0
    assert "yfinance" in r.stdout.lower() or "분봉" in r.stdout


def test_script_no_forbidden_calls():
    src = _SCRIPT.read_text(encoding="utf-8")
    for call in ("route_order(", ".place_order(", "OrderExecutor(",
                 "broker.place_order"):
        assert call not in src

"""Intrabar 실데이터 백테스트 API + 1m 수집기 pure-logic 테스트 (실주문 0건)."""

from __future__ import annotations

import importlib.util
import re
from pathlib import Path

from fastapi.testclient import TestClient

from app.main import app

client = TestClient(app)


def _load_collector():
    root = Path(__file__).resolve().parents[2]
    spec = importlib.util.spec_from_file_location(
        "collect_1m_mod", root / "scripts" / "collect_intrabar_1m_subset.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


# ─────────── API ───────────


def test_api_latest_shape_invariants():
    r = client.get("/api/system/intrabar-realdata-backtest/latest")
    assert r.status_code == 200
    d = r.json()
    assert d.get("is_live_authorization") is False
    assert d.get("is_order_signal") is False
    assert d.get("no_profit_guarantee") is True
    assert "verdict" in d
    assert "_source" in d
    body = r.text
    assert not re.search(r"sk-[A-Za-z0-9]{20,}", body)
    assert not re.search(r"\b\d{8}-\d{2}\b", body)


def test_api_get_only():
    assert client.post("/api/system/intrabar-realdata-backtest/latest").status_code in (404, 405)


# ─────────── 1m 수집기 pure logic (네트워크 0) ───────────


def test_collector_universe_is_ten():
    m = _load_collector()
    assert len(m.SUBSET_UNIVERSE) == 10
    assert "005930" in m.SUBSET_UNIVERSE
    assert all(re.fullmatch(r"\d{6}", s) for s in m.SUBSET_UNIVERSE)


def test_collector_csv_path_naming(tmp_path):
    m = _load_collector()
    p = m.csv_path("005930", tmp_path)
    assert p.name == "005930_1m.csv"


def test_collector_build_summary():
    m = _load_collector()
    s = m.build_summary(["005930"], {"000660": "EMPTY_RESPONSE"},
                        ["005930", "000660", "005380"])
    assert s["collected_count"] == 1
    assert s["failed_count"] == 1
    assert s["status"] == "IN_PROGRESS"
    assert s["is_live_authorization"] is False
    assert s["kis_order_api_called"] is False


def test_collector_dry_run_no_network(tmp_path, monkeypatch):
    m = _load_collector()
    monkeypatch.chdir(tmp_path)
    rc = m.main(["--symbols", "005930,000660", "--dry-run", "--out-dir",
                 str(tmp_path / "1m")])
    assert rc == 0
    summary = tmp_path / "reports/backtest/intrabar_1m_collection_summary.json"
    assert summary.exists()


def test_collector_no_order_api_imports():
    root = Path(__file__).resolve().parents[2]
    txt = (root / "scripts" / "collect_intrabar_1m_subset.py").read_text(encoding="utf-8")
    for bad in (".place_order(", "route_order(", "OrderExecutor(", "order-cash",
                "VTTC0802U", "TTTC0802U", "cargo build", "tauri build"):
        for line in txt.splitlines():
            assert bad not in line.split("#", 1)[0], f"banned: {bad}"

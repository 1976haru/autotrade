"""Intrabar + signal ranking 리포트 + API 테스트 (read-only, 실주문 0건)."""

from __future__ import annotations

import json
import re
import subprocess
import sys
from pathlib import Path

from fastapi.testclient import TestClient

from app.main import app
from app.system.intrabar_signal_ranking import build_report, scan_1m_coverage

client = TestClient(app)


# ─────────── build_report ───────────


def test_build_report_has_required_sections():
    r = build_report()
    for k in ("baseline_5m", "one_minute_coverage", "execution_comparison",
              "ambiguous_trade_count", "conservative_stop_first_count",
              "earliest_first_selected", "composite_selected", "ranking_differs",
              "selected_avg_score", "rejected_avg_score", "cost_model",
              "interpretation", "next_steps", "disclaimer"):
        assert k in r, f"missing report section: {k}"


def test_report_composite_beats_earliest_and_invariants():
    r = build_report()
    # composite 가 선착순과 다른 선택을 하고, 선택 평균 score 가 더 높다.
    assert r["ranking_differs"] is True
    assert r["selected_avg_score"] > r["rejected_avg_score"]
    assert r["one_minute_coverage"]["ambiguous_trades"] >= 1
    assert r["conservative_stop_first_count"] >= 1
    assert r["is_live_authorization"] is False
    assert r["is_order_signal"] is False
    assert r["auto_apply_allowed"] is False
    assert r["no_profit_guarantee"] is True


def test_coverage_graceful_when_missing(tmp_path):
    cov = scan_1m_coverage(tmp_path / "nonexistent")
    assert cov["status"] == "WARN"          # 없으면 FAIL 아니라 WARN
    assert cov["symbol_count"] == 0
    assert "체결 정확도 낮음" in cov["warning"]


def test_report_no_profit_guarantee_phrases():
    r = build_report()
    blob = json.dumps(r, ensure_ascii=False)
    for banned in ("수익 보장", "실전 가능", "실전 전환 승인"):
        assert banned not in blob, f"banned phrase: {banned}"
    assert "연구용 백테스트" in r["disclaimer"]


# ─────────── API ───────────


def test_api_latest_shape_and_invariants():
    r = client.get("/api/system/intrabar-signal-ranking/latest")
    assert r.status_code == 200
    d = r.json()
    assert d.get("is_live_authorization") is False
    assert d.get("is_order_signal") is False
    assert d.get("no_profit_guarantee") is True
    assert "_source" in d
    # secret 패턴 0건.
    body = r.text
    assert not re.search(r"sk-[A-Za-z0-9]{20,}", body)
    assert not re.search(r"\b\d{8}-\d{2}\b", body)


def test_api_get_only():
    assert client.post("/api/system/intrabar-signal-ranking/latest").status_code in (404, 405)


# ─────────── CLI ───────────


def test_cli_writes_reports(tmp_path):
    root = Path(__file__).resolve().parents[2]
    r = subprocess.run(
        [sys.executable, str(root / "scripts" / "run_intrabar_signal_ranking.py"),
         "--out-dir", str(tmp_path), "--write-latest"],
        capture_output=True, text=True, timeout=60)
    assert r.returncode == 0, r.stdout + r.stderr
    assert (tmp_path / "intrabar_signal_ranking_result.json").exists()
    assert (tmp_path / "intrabar_signal_ranking_result.md").exists()
    assert (tmp_path / "intrabar_signal_ranking_latest.json").exists()
    data = json.loads((tmp_path / "intrabar_signal_ranking_latest.json").read_text(encoding="utf-8"))
    assert data["is_live_authorization"] is False

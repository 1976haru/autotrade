"""INTRADAY-DATA-01 — run_intraday_strategy_validation.py CLI 테스트 (subprocess)."""

from __future__ import annotations

import json
import re
import subprocess
import sys
from pathlib import Path

_REPO = Path(__file__).resolve().parents[2]
_SCRIPT = _REPO / "scripts" / "run_intraday_strategy_validation.py"
_INTRADAY = _REPO / "backend" / "tests" / "fixtures" / "intraday_clean"
_DAILY = _REPO / "backend" / "tests" / "fixtures" / "real_data_clean"

_VERDICTS = {"STRONG_CANDIDATE", "CAUTIOUS_CANDIDATE", "RESEARCH_ONLY", "NOT_READY", "BLOCKED"}


def _run(args, cwd=None):
    return subprocess.run(
        [sys.executable, str(_SCRIPT), *args],
        capture_output=True, text=True, encoding="utf-8", errors="replace",
        cwd=str(cwd or _REPO))


def test_script_exists():
    assert _SCRIPT.exists()


def test_help_works():
    r = _run(["--help"])
    assert r.returncode == 0
    assert "분봉" in r.stdout


def test_run_intraday_writes_report(tmp_path):
    out = tmp_path / "id.json"
    md = tmp_path / "id.md"
    r = _run(["--input-dir", str(_INTRADAY), "--output", str(out), "--markdown", str(md)])
    assert r.returncode == 0, r.stderr
    p = json.loads(out.read_text(encoding="utf-8"))
    assert p["intraday_data_used"] is True
    assert p["total_trades"] > 0
    assert p["overall_verdict"] in _VERDICTS
    assert p["overall_verdict"] != "STRONG_CANDIDATE"  # Paper 0 → STRONG 불가
    assert p["is_live_authorization"] is False
    assert p["do_not_auto_apply"] is True


def test_daily_dir_blocked_exit_1(tmp_path):
    out = tmp_path / "id.json"
    r = _run(["--input-dir", str(_DAILY), "--output", str(out)])
    assert r.returncode == 1, r.stderr   # 분봉 아님 → 전부 blocked → BLOCKED
    assert json.loads(out.read_text(encoding="utf-8"))["overall_verdict"] == "BLOCKED"


def test_empty_dir_blocked_exit_1(tmp_path):
    out = tmp_path / "id.json"
    r = _run(["--input-dir", str(tmp_path / "empty"), "--output", str(out)])
    assert r.returncode == 1


def test_write_latest_and_json_alias(tmp_path):
    r = _run(["--input-dir", str(_INTRADAY), "--json", str(tmp_path / "id.json"),
              "--write-latest", "--quiet"], cwd=tmp_path)
    assert r.returncode == 0, r.stderr
    assert (tmp_path / "id.json").exists()
    latest = tmp_path / "reports" / "strategy_validation" / "intraday_strategy_latest.json"
    assert latest.exists()


def test_output_no_secret_or_profit_guarantee(tmp_path):
    out = tmp_path / "id.json"
    md = tmp_path / "id.md"
    r = _run(["--input-dir", str(_INTRADAY), "--output", str(out), "--markdown", str(md)])
    assert r.returncode == 0, r.stderr
    text = out.read_text(encoding="utf-8") + md.read_text(encoding="utf-8")
    assert not re.search(r"sk-[A-Za-z0-9]{20,}", text)
    assert not re.search(r"\b\d{8}-\d{2}\b", text)
    assert "수익 보장" not in text.replace("수익 보장 아님", "")

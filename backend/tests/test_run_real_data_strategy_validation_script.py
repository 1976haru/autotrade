"""REAL-DATA-STRATEGY-01: run_real_data_strategy_validation.py CLI 테스트 (subprocess)."""

from __future__ import annotations

import json
import re
import subprocess
import sys
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parents[2]
_SCRIPT = _REPO_ROOT / "scripts" / "run_real_data_strategy_validation.py"
_DEMO = _REPO_ROOT / "backend" / "tests" / "fixtures" / "real_data" / "demo_quasi_real.csv"
_BROKEN = _REPO_ROOT / "backend" / "tests" / "fixtures" / "real_data" / "005930.csv"

_VERDICTS = {"STRONG_CANDIDATE", "CAUTIOUS_CANDIDATE", "RESEARCH_ONLY", "NOT_READY", "BLOCKED"}


def _run(args, cwd=None):
    return subprocess.run(
        [sys.executable, str(_SCRIPT), *args],
        capture_output=True, text=True, encoding="utf-8", errors="replace",
        cwd=str(cwd or _REPO_ROOT))


def test_script_exists():
    assert _SCRIPT.exists()


def test_help_works():
    r = _run(["--help"])
    assert r.returncode == 0
    assert "전략 가능성" in r.stdout or "데이터" in r.stdout


def test_run_demo_writes_report(tmp_path):
    out = tmp_path / "rd.json"
    md = tmp_path / "rd.md"
    r = _run(["--input-csv", str(_DEMO), "--output", str(out), "--markdown", str(md)])
    assert r.returncode == 0, r.stderr
    assert out.exists() and md.exists()
    p = json.loads(out.read_text(encoding="utf-8"))
    assert p["overall_verdict"] in _VERDICTS
    assert p["real_data_used"] is True
    assert p["sample_fixture_only"] is False
    assert p["overall_verdict"] != "STRONG_CANDIDATE"  # paper 0 → STRONG 불가
    assert p["is_live_authorization"] is False
    assert p["do_not_auto_apply"] is True


def test_broken_fixture_blocked_exit_1(tmp_path):
    out = tmp_path / "rd.json"
    r = _run(["--input-csv", str(_BROKEN), "--output", str(out)])
    assert r.returncode == 1, r.stderr
    assert json.loads(out.read_text(encoding="utf-8"))["overall_verdict"] == "BLOCKED"


def test_missing_input_exit_2():
    r = _run(["--input-csv", str(_REPO_ROOT / "no_such_file.csv")])
    assert r.returncode == 2


def test_write_latest_creates_file(tmp_path):
    r = _run(["--input-csv", str(_DEMO), "--output", str(tmp_path / "rd.json"),
              "--write-latest", "--quiet"], cwd=tmp_path)
    assert r.returncode == 0, r.stderr
    latest = tmp_path / "reports" / "strategy_validation" / "real_data_strategy_latest.json"
    assert latest.exists()


def test_default_demo_no_input(tmp_path):
    r = _run(["--output", str(tmp_path / "rd.json"), "--quiet"])
    assert r.returncode == 0, r.stderr
    p = json.loads((tmp_path / "rd.json").read_text(encoding="utf-8"))
    # 입력 미지정 → demo quasi-real fixture.
    assert p["data_source"] in ("CSV_REAL_FIXTURE", "SAMPLE_FIXTURE")


def test_output_no_secret_or_profit_guarantee(tmp_path):
    out = tmp_path / "rd.json"
    md = tmp_path / "rd.md"
    r = _run(["--input-csv", str(_DEMO), "--output", str(out), "--markdown", str(md)])
    assert r.returncode == 0, r.stderr
    text = out.read_text(encoding="utf-8") + md.read_text(encoding="utf-8")
    assert not re.search(r"sk-[A-Za-z0-9]{20,}", text)
    assert not re.search(r"\b\d{8}-\d{2}\b", text)
    assert "수익 보장" not in text.replace("수익 보장 아님", "")

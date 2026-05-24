"""STRATEGY-VALIDATION-01: run_strategy_potential_report.py CLI 테스트 (subprocess)."""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parents[2]
_SCRIPT = _REPO_ROOT / "scripts" / "run_strategy_potential_report.py"

_VERDICTS = {
    "STRONG_CANDIDATE", "CAUTIOUS_CANDIDATE", "RESEARCH_ONLY",
    "NOT_READY", "BLOCKED",
}


def _run(args, cwd=None):
    return subprocess.run(
        [sys.executable, str(_SCRIPT), *args],
        capture_output=True, text=True, encoding="utf-8", errors="replace",
        cwd=str(cwd or _REPO_ROOT),
    )


def test_script_exists():
    assert _SCRIPT.exists()


def test_help_works():
    r = _run(["--help"])
    assert r.returncode == 0
    assert "전략 가능성" in r.stdout


def test_run_sample_writes_report(tmp_path):
    out = tmp_path / "sp.json"
    md = tmp_path / "sp.md"
    r = _run(["--run-sample", "--output", str(out), "--markdown", str(md)])
    assert r.returncode == 0, r.stderr
    assert out.exists() and md.exists()
    payload = json.loads(out.read_text(encoding="utf-8"))
    assert payload["overall_verdict"] in _VERDICTS
    # sample fixture only → STRONG 불가 + 안전 불변값.
    assert payload["sample_fixture_only"] is True
    assert payload["overall_verdict"] != "STRONG_CANDIDATE"
    assert payload["is_live_authorization"] is False
    assert payload["do_not_auto_apply"] is True
    assert payload["is_order_signal"] is False


def test_stress_fail_json_returns_blocked(tmp_path):
    stress = tmp_path / "stress.json"
    stress.write_text(json.dumps({"counts": {"PASS": 8, "WARN": 0, "FAIL": 3}}),
                      encoding="utf-8")
    bt = tmp_path / "bt.json"
    bt.write_text(json.dumps({
        "insufficient_data": False, "bar_count": 200,
        "council": {"performance": {"win_rate": 0.6, "profit_factor": 1.5,
                    "expectancy": 3.0, "max_consecutive_losses": 3,
                    "by_market_regime": {}, "by_time_phase": {}}},
        "comparison": {"council_better_than_best_single": True}}), encoding="utf-8")
    out = tmp_path / "sp.json"
    r = _run(["--backtest-json", str(bt), "--stress-json", str(stress),
              "--has-real-data", "--output", str(out)])
    assert r.returncode == 1, r.stderr  # BLOCKED → exit 1
    payload = json.loads(out.read_text(encoding="utf-8"))
    assert payload["overall_verdict"] == "BLOCKED"


def test_bad_json_path_returns_2(tmp_path):
    r = _run(["--backtest-json", str(tmp_path / "nope.json"),
              "--output", str(tmp_path / "o.json")])
    assert r.returncode == 2


def test_default_output_dir(tmp_path):
    r = _run(["--run-sample", "--quiet"], cwd=tmp_path)
    assert r.returncode in (0, 1)
    produced = list((tmp_path / "reports" / "strategy_validation").glob("*.json"))
    assert produced, "default reports/strategy_validation/ JSON not created"


def test_output_has_no_secret_or_profit_guarantee(tmp_path):
    out = tmp_path / "sp.json"
    md = tmp_path / "sp.md"
    r = _run(["--run-sample", "--output", str(out), "--markdown", str(md)])
    assert r.returncode == 0, r.stderr
    import re
    text = out.read_text(encoding="utf-8") + md.read_text(encoding="utf-8")
    assert not re.search(r"sk-[A-Za-z0-9]{20,}", text)
    assert not re.search(r"\b\d{8}-\d{2}\b", text)
    # 단언형 "수익 보장" 금지 (부정형 "수익 보장 아님" 은 허용).
    assert "수익 보장" not in text.replace("수익 보장 아님", "")

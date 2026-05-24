"""BUILD-02A: run_premarket_readiness_gate.py CLI 테스트 (subprocess).

full mode 의 무거운 명령(pytest/npm)은 실행하지 않고 --dry-run 으로 plan 만 검증.
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parents[2]
_SCRIPT = _REPO_ROOT / "scripts" / "run_premarket_readiness_gate.py"


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
    assert "사전 검증" in r.stdout


def test_fast_mode_writes_reports(tmp_path):
    out_json = tmp_path / "pm.json"
    out_md = tmp_path / "pm.md"
    r = _run(["--output", str(out_json), "--markdown", str(out_md)])
    # 기본(자격 미상) → WARN 이지만 FAIL 0 → premarket_ready True → exit 0.
    assert r.returncode == 0, r.stderr
    assert out_json.exists() and out_md.exists()
    payload = json.loads(out_json.read_text(encoding="utf-8"))
    assert payload["mode"] == "fast"
    assert payload["is_live_authorization"] is False
    assert payload["broker_order_sent"] is False
    assert "premarket_ready" in payload
    assert "BUILD-02A" in out_md.read_text(encoding="utf-8")


def test_full_mode_dry_run_lists_plan(tmp_path):
    out_json = tmp_path / "pm_full.json"
    r = _run(["--mode", "full", "--dry-run", "--output", str(out_json)])
    assert r.returncode in (0, 1)
    assert "명령 plan" in r.stdout
    payload = json.loads(out_json.read_text(encoding="utf-8"))
    assert payload["mode"] == "full"
    assert len(payload["full_mode_command_plan"]) >= 5


def test_kis_credentials_present_flag(tmp_path):
    out_json = tmp_path / "pm_creds.json"
    r = _run(["--kis-credentials-present", "--output", str(out_json)])
    assert r.returncode == 0, r.stderr
    payload = json.loads(out_json.read_text(encoding="utf-8"))
    assert payload["ready_for_market_open_rehearsal"] is True


def test_default_output_dir(tmp_path):
    r = _run([], cwd=tmp_path)
    assert r.returncode in (0, 1)
    produced = list((tmp_path / "reports" / "prebuild").glob("premarket_readiness_*.json"))
    assert produced, "default reports/prebuild/ JSON not created"


def test_no_secret_or_forbidden_language(tmp_path):
    out_json = tmp_path / "pm.json"
    r = _run(["--output", str(out_json)])
    blob = out_json.read_text(encoding="utf-8") + r.stdout
    for forbidden in ("kis_app_secret", "access_token", "Bearer ", "sk-ant-"):
        assert forbidden not in blob


def test_script_has_no_broker_imports():
    src = _SCRIPT.read_text(encoding="utf-8")
    for tok in ("from app.brokers.kis", "from app.brokers.mock_broker",
                "from app.execution.order_router", "from app.execution.executor",
                "import anthropic", "import openai", "import httpx", "import requests"):
        assert tok not in src

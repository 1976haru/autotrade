"""BUILD-02B-0: run_kis_paper_autotrade_audit.py CLI 테스트 (subprocess)."""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parents[2]
_SCRIPT = _REPO_ROOT / "scripts" / "run_kis_paper_autotrade_audit.py"


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
    assert "코드 감사" in r.stdout


def test_run_writes_reports(tmp_path):
    out_json = tmp_path / "audit.json"
    out_md = tmp_path / "audit.md"
    r = _run(["--output", str(out_json), "--markdown", str(out_md)])
    assert r.returncode == 0, r.stderr   # FAIL 0 → paper_autotrade_ready → exit 0.
    payload = json.loads(out_json.read_text(encoding="utf-8"))
    assert len(payload["sections"]) >= 17
    assert payload["is_live_authorization"] is False
    assert payload["broker_order_sent"] is False
    assert payload["paper_autotrade_ready"] is True
    assert "BUILD-02B-0" in out_md.read_text(encoding="utf-8")


def test_kis_credentials_present_rehearsal(tmp_path):
    out_json = tmp_path / "audit_creds.json"
    r = _run(["--kis-credentials-present", "--output", str(out_json)])
    assert r.returncode == 0, r.stderr
    payload = json.loads(out_json.read_text(encoding="utf-8"))
    assert payload["ready_for_market_open_rehearsal"] is True


def test_default_output_dir(tmp_path):
    r = _run([], cwd=tmp_path)
    assert r.returncode in (0, 1)
    produced = list((tmp_path / "reports" / "prebuild").glob("kis_paper_autotrade_audit_*.json"))
    assert produced, "default reports/prebuild/ JSON not created"


def test_no_secret_or_forbidden_language(tmp_path):
    out_json = tmp_path / "audit.json"
    r = _run(["--output", str(out_json)])
    blob = out_json.read_text(encoding="utf-8") + r.stdout
    for forbidden in ("kis_app_secret", "access_token", "Bearer ", "sk-ant-",
                      "수익 보장", "실전 전환 승인"):
        assert forbidden not in blob


def test_script_has_no_broker_imports():
    src = _SCRIPT.read_text(encoding="utf-8")
    for tok in ("from app.brokers.kis", "from app.brokers.mock_broker",
                "from app.execution.order_router", "from app.execution.executor",
                "import anthropic", "import openai", "import httpx", "import requests"):
        assert tok not in src

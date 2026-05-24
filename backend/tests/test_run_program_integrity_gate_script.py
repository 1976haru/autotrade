"""BUILD-01: run_program_integrity_gate.py CLI 테스트 (subprocess)."""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parents[2]
_SCRIPT = _REPO_ROOT / "scripts" / "run_program_integrity_gate.py"


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
    assert "정합성 점검" in r.stdout
    assert "실전 아님" in r.stdout


def test_run_writes_json_and_markdown(tmp_path):
    out_json = tmp_path / "pi.json"
    out_md = tmp_path / "pi.md"
    r = _run(["--output", str(out_json), "--markdown", str(out_md)])
    # build_ready True → exit 0 (기본 안전 입력).
    assert r.returncode == 0, r.stderr
    assert out_json.exists() and out_md.exists()
    payload = json.loads(out_json.read_text(encoding="utf-8"))
    assert "sections" in payload and len(payload["sections"]) >= 16
    assert payload["is_live_authorization"] is False
    assert payload["broker_order_sent"] is False
    assert "build_ready" in payload
    md = out_md.read_text(encoding="utf-8")
    assert "BUILD-01" in md


def test_default_output_dir(tmp_path):
    r = _run([], cwd=tmp_path)
    assert r.returncode in (0, 1)
    produced = list((tmp_path / "reports" / "build").glob("program_integrity_*.json"))
    assert produced, "default reports/build/ JSON not created"


def test_no_secret_or_forbidden_language(tmp_path):
    out_json = tmp_path / "pi.json"
    r = _run(["--output", str(out_json)])
    blob = out_json.read_text(encoding="utf-8") + r.stdout
    for forbidden in ("수익 보장", "실전 전환 승인", "원금 보장",
                      "kis_app_secret", "access_token", "Bearer ", "sk-ant-"):
        assert forbidden not in blob


def test_script_has_no_broker_imports():
    src = _SCRIPT.read_text(encoding="utf-8")
    for tok in ("from app.brokers.kis", "from app.brokers.mock_broker",
                "from app.execution.order_router", "from app.execution.executor",
                "import anthropic", "import openai", "import httpx", "import requests"):
        assert tok not in src

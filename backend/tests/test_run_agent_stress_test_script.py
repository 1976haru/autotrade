"""#48 / 6-03: run_agent_stress_test.py CLI 테스트 (subprocess).

필수:
- --help 동작
- 전체 시나리오 실행 성공 + JSON / Markdown 리포트 생성
- 특정 scenario 실행
- FAIL 시 exit 1 (strict + WARN 시나리오)
- 설정 오류(알 수 없는 scenario) → exit 2
- secret / account 출력 없음, "수익 보장" / "실전 전환 승인" 문구 없음
- broker / 주문 endpoint import 없음 (정적 grep)
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parents[2]
_SCRIPT = _REPO_ROOT / "scripts" / "run_agent_stress_test.py"


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
    assert "스트레스" in r.stdout
    assert "실전 전환 아님" in r.stdout


def test_run_all_writes_json_and_markdown(tmp_path):
    out_json = tmp_path / "st.json"
    out_md = tmp_path / "st.md"
    r = _run(["--output", str(out_json), "--markdown", str(out_md)])
    assert r.returncode == 0, r.stderr
    assert out_json.exists() and out_md.exists()
    payload = json.loads(out_json.read_text(encoding="utf-8"))
    assert len(payload["scenarios"]) == 12
    assert payload["overall_verdict"] in ("PASS", "WARN")
    assert payload["is_live_authorization"] is False
    assert payload["is_order_signal"] is False
    assert payload["broker_order_sent"] is False
    md = out_md.read_text(encoding="utf-8")
    assert "스트레스 테스트 리포트" in md


def test_single_scenario(tmp_path):
    out_json = tmp_path / "crash.json"
    r = _run(["--scenario", "MARKET_CRASH", "--output", str(out_json)])
    assert r.returncode == 0, r.stderr
    payload = json.loads(out_json.read_text(encoding="utf-8"))
    assert len(payload["scenarios"]) == 1
    assert payload["scenarios"][0]["scenario"] == "MARKET_CRASH"


def test_default_output_dir(tmp_path):
    r = _run(["--scenario", "PRICE_STALE"], cwd=tmp_path)
    assert r.returncode == 0, r.stderr
    produced = list((tmp_path / "reports" / "stress").glob("stress_test_*.json"))
    assert produced, "default reports/stress/ JSON not created"


def test_strict_warn_exit_1(tmp_path):
    # SLIPPAGE_HIGH 는 WARN → strict 면 overall FAIL → exit 1.
    out_json = tmp_path / "s.json"
    r = _run(["--scenario", "SLIPPAGE_HIGH", "--strict", "--output", str(out_json)])
    assert r.returncode == 1


def test_unknown_scenario_exit_2(tmp_path):
    r = _run(["--scenario", "NOPE", "--output", str(tmp_path / "x.json")])
    assert r.returncode == 2
    assert "알 수 없는 scenario" in (r.stdout + r.stderr)


def test_no_secret_or_forbidden_language(tmp_path):
    out_json = tmp_path / "st.json"
    r = _run(["--output", str(out_json)])
    assert r.returncode == 0, r.stderr
    blob = out_json.read_text(encoding="utf-8") + r.stdout
    for forbidden in ("수익 보장", "실전 전환 승인", "원금 보장",
                      "kis_app_secret", "access_token", "Bearer ", "sk-ant-"):
        assert forbidden not in blob


def test_script_has_no_broker_or_order_imports():
    src = _SCRIPT.read_text(encoding="utf-8")
    for tok in ("from app.brokers.kis", "from app.brokers.mock_broker",
                "from app.execution.order_router", "from app.execution.executor",
                "import anthropic", "import openai", "import httpx", "import requests"):
        assert tok not in src

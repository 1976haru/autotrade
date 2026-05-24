"""#47 / 6-02: run_strategy_council_walk_forward.py CLI 테스트 (subprocess).

필수:
- --help 동작
- fixture input 실행 성공 + JSON / Markdown 리포트 생성
- rolling 옵션 동작
- 데이터 부족 → exit 1
- 입력 파일 없음 → exit 2
- secret / account 출력 없음, "수익 보장" / "실전 전환 승인" 문구 없음
- broker / 주문 endpoint import·호출 없음 (정적 grep)
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parents[2]
_SCRIPT = _REPO_ROOT / "scripts" / "run_strategy_council_walk_forward.py"
_SAMPLE_CSV = Path(__file__).resolve().parent / "fixtures" / "backtest" / "sample_ohlcv.csv"


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
    assert "Walk-forward" in r.stdout
    assert "실전 전환 아님" in r.stdout


def test_run_success_writes_json_and_markdown(tmp_path):
    out_json = tmp_path / "wf.json"
    out_md = tmp_path / "wf.md"
    r = _run(["--input", str(_SAMPLE_CSV), "--output", str(out_json),
              "--markdown", str(out_md)])
    assert r.returncode == 0, r.stderr
    assert out_json.exists() and out_md.exists()
    payload = json.loads(out_json.read_text(encoding="utf-8"))
    assert payload["mode"] == "THREE_WAY"
    assert "splits" in payload and payload["split_count"] >= 1
    assert "overall_stability_score" in payload
    assert payload["is_live_authorization"] is False
    assert payload["is_order_signal"] is False
    assert payload["broker_order_sent"] is False
    md = out_md.read_text(encoding="utf-8")
    assert "Walk-forward" in md


def test_rolling_mode_works(tmp_path):
    out_json = tmp_path / "wf_roll.json"
    r = _run(["--input", str(_SAMPLE_CSV), "--mode", "ROLLING",
              "--train-window-days", "3", "--validation-window-days", "1",
              "--test-window-days", "1", "--step-days", "1",
              "--output", str(out_json)])
    assert r.returncode == 0, r.stderr
    payload = json.loads(out_json.read_text(encoding="utf-8"))
    assert payload["mode"] == "ROLLING"
    assert payload["split_count"] >= 2


def test_insufficient_data_exit_1(tmp_path):
    # 2 거래일 미만의 짧은 CSV → split 불가 exit 1.
    csv = tmp_path / "tiny.csv"
    lines = ["symbol,timestamp,open,high,low,close,volume"]
    for i in range(5):
        lines.append(f"005930,2026-05-11T09:0{i}:00+09:00,70000,70100,69900,70050,8000000")
    csv.write_text("\n".join(lines), encoding="utf-8")
    r = _run(["--input", str(csv), "--output", str(tmp_path / "o.json")])
    assert r.returncode == 1
    assert "데이터 부족" in (r.stdout + r.stderr)


def test_missing_input_exit_2(tmp_path):
    r = _run(["--input", str(tmp_path / "nope.csv")])
    assert r.returncode == 2
    assert "찾을 수 없습니다" in (r.stdout + r.stderr)


def test_no_secret_or_forbidden_language(tmp_path):
    out_json = tmp_path / "wf.json"
    r = _run(["--input", str(_SAMPLE_CSV), "--output", str(out_json)])
    assert r.returncode == 0, r.stderr
    blob = out_json.read_text(encoding="utf-8") + r.stdout
    for forbidden in ("수익 보장", "실전 전환 승인", "원금 보장",
                      "kis_app_secret", "access_token", "Bearer ", "sk-ant-"):
        assert forbidden not in blob


def test_script_has_no_broker_or_order_imports():
    src = _SCRIPT.read_text(encoding="utf-8")
    for tok in ("from app.brokers", "from app.execution.order_router",
                "from app.execution.executor", "import anthropic", "import openai",
                "import httpx", "import requests"):
        assert tok not in src

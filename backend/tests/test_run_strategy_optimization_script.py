"""#46 / 6-01: run_strategy_optimization.py CLI 스크립트 테스트 (subprocess).

필수:
- --help 동작
- fixture input 실행 성공 + JSON / Markdown 리포트 생성
- 데이터 부족 → exit 1
- 입력 파일 없음 → exit 2
- secret / account 출력 없음, "수익 보장" / "실전 전환 승인" 문구 없음
- 주문 endpoint / broker 호출 없음 (정적 grep)
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parents[2]
_SCRIPT = _REPO_ROOT / "scripts" / "run_strategy_optimization.py"
_SAMPLE_CSV = Path(__file__).resolve().parent / "fixtures" / "backtest" / "sample_ohlcv.csv"


def _run(args, cwd=None):
    return subprocess.run(
        [sys.executable, str(_SCRIPT), *args],
        capture_output=True, text=True, encoding="utf-8", errors="replace",
        cwd=str(cwd or _REPO_ROOT),
    )


def test_help_works():
    r = _run(["--help"])
    assert r.returncode == 0
    assert "Agent Council" in r.stdout
    assert "실전 전환 아님" in r.stdout


def test_script_file_exists():
    assert _SCRIPT.exists()


def test_sample_csv_exists():
    assert _SAMPLE_CSV.exists()


def test_run_success_writes_json_and_markdown(tmp_path):
    out_json = tmp_path / "rep.json"
    out_md = tmp_path / "rep.md"
    r = _run(["--input", str(_SAMPLE_CSV), "--output", str(out_json),
              "--markdown", str(out_md)])
    assert r.returncode == 0, r.stderr
    assert out_json.exists() and out_md.exists()
    payload = json.loads(out_json.read_text(encoding="utf-8"))
    assert payload["reason_code"] == "BACKTEST_OK"
    # 4 전략 + council 포함.
    assert set(payload["strategies"]) == {"ORB", "MOMENTUM", "GAP", "VWAP"}
    assert payload["council"] is not None
    assert payload["is_live_authorization"] is False
    assert payload["is_order_signal"] is False
    md = out_md.read_text(encoding="utf-8")
    assert "Agent Council" in md


def test_default_output_dir(tmp_path):
    # --output 미지정 → reports/backtest/ 아래 timestamp 파일 (cwd=tmp_path).
    csv_copy = tmp_path / "in.csv"
    csv_copy.write_text(_SAMPLE_CSV.read_text(encoding="utf-8"), encoding="utf-8")
    r = _run(["--input", str(csv_copy)], cwd=tmp_path)
    assert r.returncode == 0, r.stderr
    produced = list((tmp_path / "reports" / "backtest").glob("strategy_backtest_*.json"))
    assert produced, "default reports/backtest/ JSON not created"


def test_insufficient_data_exit_1(tmp_path):
    # 4 bar 만 → 데이터 부족 exit 1.
    csv = tmp_path / "tiny.csv"
    lines = ["symbol,timestamp,open,high,low,close,volume"]
    for i in range(4):
        lines.append(f"005930,2026-05-11T0{9}:0{i}:00+09:00,70000,70100,69900,70050,8000000")
    csv.write_text("\n".join(lines), encoding="utf-8")
    r = _run(["--input", str(csv), "--output", str(tmp_path / "o.json")])
    assert r.returncode == 1
    assert "데이터 부족" in (r.stdout + r.stderr)


def test_missing_input_exit_2(tmp_path):
    r = _run(["--input", str(tmp_path / "nope.csv")])
    assert r.returncode == 2
    assert "찾을 수 없습니다" in (r.stdout + r.stderr)


def test_no_secret_or_forbidden_language_in_output(tmp_path):
    out_json = tmp_path / "rep.json"
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

"""체크리스트 11-00 — run_final_prebuild_gate.py CLI 테스트 (subprocess)."""

from __future__ import annotations

import json
import re
import subprocess
import sys
from pathlib import Path

_REPO = Path(__file__).resolve().parents[2]
_SCRIPT = _REPO / "scripts" / "run_final_prebuild_gate.py"


def _run(args, cwd=None):
    return subprocess.run(
        [sys.executable, str(_SCRIPT), *args],
        capture_output=True, text=True, encoding="utf-8", errors="replace",
        cwd=str(cwd or _REPO), timeout=420)


def test_script_exists():
    assert _SCRIPT.exists()


def test_help():
    r = _run(["--help"])
    assert r.returncode == 0
    assert "빌드" in r.stdout


def test_fast_run_writes_report(tmp_path):
    out = tmp_path / "g.json"
    md = tmp_path / "g.md"
    r = _run(["--output", str(out), "--markdown", str(md), "--quiet"])
    # 안전 기본 + ruff/security 통과 → BLOCKED 아님 (exit 0).
    assert r.returncode == 0, r.stderr
    d = json.loads(out.read_text(encoding="utf-8"))
    assert d["overall_status"] in ("BUILD_READY", "BUILD_READY_WITH_WARNINGS")
    assert d["exe_build_allowed"] is True
    assert d["is_live_authorization"] is False
    assert d["no_profit_guarantee"] is True
    # 모든 핵심 섹션 존재.
    names = {s["name"] for s in d["sections"]}
    assert "CONFIG_ENV" in names and "SECURITY_SECRET_SCAN" in names
    text = out.read_text(encoding="utf-8") + md.read_text(encoding="utf-8")
    assert not re.search(r"sk-[A-Za-z0-9]{20,}", text)
    assert "수익 보장" not in text.replace("수익 보장 아님", "")


def test_default_output_dir(tmp_path):
    r = _run(["--quiet"], cwd=tmp_path)
    assert r.returncode in (0, 1)
    produced = list((tmp_path / "reports" / "final_prebuild").glob("*.json"))
    assert produced, "default reports/final_prebuild/ JSON not created"

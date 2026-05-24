#!/usr/bin/env python3
"""BUILD-02A — 장 열리기 전 사전 검증 CLI (offline, read-only).

장이 열리기 전에도 확인 가능한 항목을 자동 점검한다. fast mode 는 in-process,
full mode 는 추가로 외부 명령(ruff/pytest/security_scan/npm lint·test·build)을
실행해 backend/frontend quality 섹션을 채운다.

**실제 KIS API 호출 0건, 실전/모의 주문 0건, 실전 승인 아님.** 장중 실제 KIS 모의
주문/체결 테스트는 BUILD-02B 에서 진행. KIS 자격은 present 여부만 — 원문 0건.

CLAUDE.md 절대 원칙:
- read-only. broker / 단일 주문 라우터 / OrderExecutor / KIS 실제 API / 외부 HTTP
  주문 호출 0건. 안전 flag · `.env` 변경 0건. secret / 계좌 원문 출력 0건.

사용:
    python scripts/run_premarket_readiness_gate.py                    # fast
    python scripts/run_premarket_readiness_gate.py --mode full        # full(명령 실행)
    python scripts/run_premarket_readiness_gate.py --mode full --dry-run   # 명령 plan만
    python scripts/run_premarket_readiness_gate.py --kis-credentials-present

exit code:
    0: premarket_ready (FAIL 0)
    1: FAIL 있음
    2: 실행 오류
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parents[1]
_BACKEND_DIR = _REPO_ROOT / "backend"
if str(_BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(_BACKEND_DIR))

from app.system.premarket_readiness_gate import (  # noqa: E402
    PremarketInputs,
    full_mode_command_plan,
    merge_full_mode_results,
    render_markdown_report,
    run_premarket_readiness_gate,
    summarize,
)

DEFAULT_OUTPUT_DIR = "reports/prebuild"


def _parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    p = argparse.ArgumentParser(
        description="장 열리기 전 사전 검증 (offline, 실전 아님). full mode 는 CLI 전용.")
    p.add_argument("--mode", default="fast", choices=["fast", "full"])
    p.add_argument("--dry-run", action="store_true",
                   help="full mode 명령을 실행하지 않고 plan 만 표시.")
    p.add_argument("--kis-credentials-present", action="store_true",
                   help="KIS 모의 자격 present 가정 (기본: 미상 → WARN).")
    p.add_argument("--output", default=None)
    p.add_argument("--markdown", default=None)
    p.add_argument("--quiet", action="store_true")
    return p.parse_args(argv)


def _timestamp_name(prefix: str, ext: str) -> str:
    return f"{prefix}_{datetime.now(timezone.utc):%Y%m%d_%H%M%S}.{ext}"


def _run_full_mode_commands() -> dict[str, str]:
    """full mode 명령을 실행하고 섹션별 PASS/FAIL 산정 (exit code 기준)."""
    section_results: dict[str, str] = {}
    for cmd in full_mode_command_plan():
        cwd = _REPO_ROOT if cmd.cwd == "." else _REPO_ROOT / cmd.cwd
        try:
            r = subprocess.run(list(cmd.argv), cwd=str(cwd), capture_output=True,
                               text=True, encoding="utf-8", errors="replace", timeout=1800)
            ok = r.returncode == 0
        except Exception as exc:  # noqa: BLE001 — 명령 실패는 FAIL (감추지 않음).
            ok = False
            print(f"[full] {cmd.name} 실행 오류: {type(exc).__name__}", file=sys.stderr)
        if not ok:
            print(f"[full] FAIL: {cmd.name} ({cmd.cwd})", file=sys.stderr)
        # 한 섹션 안에서 하나라도 FAIL 이면 섹션 FAIL.
        prev = section_results.get(cmd.section, "PASS")
        section_results[cmd.section] = "FAIL" if (not ok or prev == "FAIL") else "PASS"
    return section_results


def main(argv: list[str] | None = None) -> int:
    for _stream in (sys.stdout, sys.stderr):
        try:
            _stream.reconfigure(encoding="utf-8", errors="replace")
        except (AttributeError, ValueError):
            pass

    args = _parse_args(argv)
    try:
        report = run_premarket_readiness_gate(
            PremarketInputs(
                kis_credentials_present=(True if args.kis_credentials_present else None)),
            mode=args.mode)
        if args.mode == "full" and not args.dry_run:
            results = _run_full_mode_commands()
            report = merge_full_mode_results(report, results)
    except Exception as exc:  # noqa: BLE001
        print(f"[ERROR] 사전 검증 실행 실패: {type(exc).__name__}: {exc}", file=sys.stderr)
        return 2

    if args.output:
        json_path = Path(args.output)
    else:
        json_path = Path(DEFAULT_OUTPUT_DIR) / _timestamp_name("premarket_readiness", "json")
    md_path = Path(args.markdown) if args.markdown else json_path.with_suffix(".md")
    json_path.parent.mkdir(parents=True, exist_ok=True)
    md_path.parent.mkdir(parents=True, exist_ok=True)
    json_path.write_text(json.dumps(summarize(report), ensure_ascii=False, indent=2),
                         encoding="utf-8")
    md_path.write_text(render_markdown_report(report), encoding="utf-8")

    if not args.quiet:
        print(f"[OK] JSON 리포트: {json_path}")
        print(f"[OK] Markdown 리포트: {md_path}")
        print(f"mode={report.mode} overall={report.overall_status} "
              f"premarket_ready={report.premarket_ready}")
        print(f"build_ready_for_offline={report.build_ready_for_offline} "
              f"ready_for_market_open_rehearsal={report.ready_for_market_open_rehearsal}")
        print(f"PASS={report.counts.get('PASS',0)} WARN={report.counts.get('WARN',0)} "
              f"FAIL={report.counts.get('FAIL',0)} SKIP={report.counts.get('SKIP',0)}")
        if args.mode == "full" and args.dry_run:
            print("[full --dry-run] 명령 plan:")
            for c in full_mode_command_plan():
                print(f"  - {c.name}: {' '.join(c.argv)} (cwd={c.cwd})")
        print("NOTE: 사전 검증 전용 — 실제 KIS API 0건, 실전 승인 아님. 장중은 BUILD-02B.")

    return 0 if report.premarket_ready else 1


if __name__ == "__main__":
    raise SystemExit(main())

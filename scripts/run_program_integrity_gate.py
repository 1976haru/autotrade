#!/usr/bin/env python3
"""BUILD-01 — 최종 EXE 빌드 전 전체 프로그램 정합성 점검 CLI (offline/fake, read-only).

Universe → KIS readiness → 4전략 vote → Agent Council → RiskOfficer → exit_plan →
quality → BUY/SELL/HOLD → KIS Paper decision → fake 주문 결과 → order_quality →
portfolio → outcome/review → feedback/quality → UI/API → Live safety 를 하나의
리포트로 점검하고 build_ready 를 산출한다.

**본 점검은 실전 승인이 아니며**, 실제 주문 / KIS 실거래 호출 0건(주문 결과 fake).
장중 실제 KIS 모의 API 테스트는 BUILD-02 에서 진행.

CLAUDE.md 절대 원칙:
- read-only. broker / 단일 주문 라우터 / OrderExecutor / KIS 실제 API / AI SDK /
  외부 HTTP 호출 0건.
- `ENABLE_LIVE_TRADING` / `ENABLE_AI_EXECUTION` / `ENABLE_FUTURES_LIVE_TRADING` /
  `KIS_IS_PAPER` 변경 0건, `.env` 작성/갱신 0건.
- secret / API key / 계좌번호 출력 0건, "수익 보장" / "실전 전환 승인" 문구 0건.

사용:
    python scripts/run_program_integrity_gate.py
    python scripts/run_program_integrity_gate.py --output reports/build/integrity.json \\
        --markdown reports/build/integrity.md

exit code:
    0: build_ready (PASS 또는 WARN 만)
    1: build NOT ready (FAIL 있음)
    2: 실행 오류
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parents[1]
_BACKEND_DIR = _REPO_ROOT / "backend"
if str(_BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(_BACKEND_DIR))

from app.system.program_integrity_gate import (  # noqa: E402
    GateInputs,
    render_markdown_report,
    run_program_integrity_gate,
    summarize_program_integrity,
)

DEFAULT_OUTPUT_DIR = "reports/build"


def _parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    p = argparse.ArgumentParser(
        description="최종 빌드 전 전체 프로그램 정합성 점검 (offline/fake, 실전 아님).")
    p.add_argument("--output", default=None, help="JSON 리포트 경로 (미지정 시 reports/build/).")
    p.add_argument("--markdown", default=None, help="Markdown 리포트 경로 (미지정 시 JSON 옆 .md).")
    p.add_argument("--kis-credentials-present", action="store_true",
                   help="KIS 모의 자격 present 가정 (기본: 미설정 → WARN).")
    p.add_argument("--quiet", action="store_true")
    return p.parse_args(argv)


def _timestamp_name(prefix: str, ext: str) -> str:
    return f"{prefix}_{datetime.now(timezone.utc):%Y%m%d_%H%M%S}.{ext}"


def main(argv: list[str] | None = None) -> int:
    for _stream in (sys.stdout, sys.stderr):
        try:
            _stream.reconfigure(encoding="utf-8", errors="replace")
        except (AttributeError, ValueError):
            pass

    args = _parse_args(argv)
    try:
        report = run_program_integrity_gate(GateInputs(
            kis_credentials_present=(True if args.kis_credentials_present else None),
        ))
    except Exception as exc:  # noqa: BLE001 — 실행 오류는 exit 2.
        print(f"[ERROR] 정합성 점검 실행 실패: {type(exc).__name__}: {exc}", file=sys.stderr)
        return 2

    if args.output:
        json_path = Path(args.output)
    else:
        json_path = Path(DEFAULT_OUTPUT_DIR) / _timestamp_name("program_integrity", "json")
    md_path = Path(args.markdown) if args.markdown else json_path.with_suffix(".md")

    json_path.parent.mkdir(parents=True, exist_ok=True)
    md_path.parent.mkdir(parents=True, exist_ok=True)
    json_path.write_text(
        json.dumps(summarize_program_integrity(report), ensure_ascii=False, indent=2),
        encoding="utf-8")
    md_path.write_text(render_markdown_report(report), encoding="utf-8")

    if not args.quiet:
        print(f"[OK] JSON 리포트: {json_path}")
        print(f"[OK] Markdown 리포트: {md_path}")
        print(f"overall={report.overall_verdict} build_ready={report.build_ready} "
              f"reason={report.reason_code}")
        print(f"PASS={report.counts.get('PASS',0)} WARN={report.counts.get('WARN',0)} "
              f"FAIL={report.counts.get('FAIL',0)}")
        print("NOTE: 빌드 전 통합 검증 전용 — 실전 승인 아님, 실제 주문 0건 (결과 fake).")

    return 0 if report.build_ready else 1


if __name__ == "__main__":
    raise SystemExit(main())

#!/usr/bin/env python3
"""BUILD-02B-0 — KIS 모의 AI 자동매매 전체 코드 감사 CLI (offline/fake, read-only).

AI 판단 → KIS Paper 주문 결정 → fake 주문 결과 → order_quality → portfolio →
outcome/review/feedback 전 흐름 + 권한 게이트 + Live safety 를 fake 로 감사한다.

**실제 KIS API 호출 0건, 실전/모의 주문 0건, 실전 승인 아님.** 장중 실제 KIS 모의
주문/체결 리허설은 BUILD-02B 에서 진행. KIS 자격은 present 여부만 — 원문 0건.

CLAUDE.md 절대 원칙:
- read-only. broker / 단일 주문 라우터 / OrderExecutor / KIS 실제 API 호출 0건.
- 안전 flag · `.env` 변경 0건. secret / 계좌 원문 출력 0건.

사용:
    python scripts/run_kis_paper_autotrade_audit.py
    python scripts/run_kis_paper_autotrade_audit.py --markdown reports/prebuild/kis_audit.md
    python scripts/run_kis_paper_autotrade_audit.py --kis-credentials-present

exit code:
    0: paper_autotrade_ready (FAIL 0)
    1: FAIL 있음
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

from app.system.kis_paper_ai_autotrade_audit import (  # noqa: E402
    KisPaperAuditInputs,
    render_markdown_report,
    run_kis_paper_ai_autotrade_audit,
    summarize,
)

DEFAULT_OUTPUT_DIR = "reports/prebuild"


def _parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    p = argparse.ArgumentParser(
        description="KIS 모의 AI 자동매매 전체 코드 감사 (offline/fake, 실전 아님).")
    p.add_argument("--kis-credentials-present", action="store_true",
                   help="KIS 모의 자격 present 가정 (기본: 미상 → rehearsal 불가).")
    p.add_argument("--output", default=None)
    p.add_argument("--markdown", default=None)
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
        report = run_kis_paper_ai_autotrade_audit(KisPaperAuditInputs(
            kis_credentials_present=(True if args.kis_credentials_present else None)))
    except Exception as exc:  # noqa: BLE001
        print(f"[ERROR] 코드 감사 실행 실패: {type(exc).__name__}: {exc}", file=sys.stderr)
        return 2

    if args.output:
        json_path = Path(args.output)
    else:
        json_path = Path(DEFAULT_OUTPUT_DIR) / _timestamp_name("kis_paper_autotrade_audit", "json")
    md_path = Path(args.markdown) if args.markdown else json_path.with_suffix(".md")
    json_path.parent.mkdir(parents=True, exist_ok=True)
    md_path.parent.mkdir(parents=True, exist_ok=True)
    json_path.write_text(json.dumps(summarize(report), ensure_ascii=False, indent=2),
                         encoding="utf-8")
    md_path.write_text(render_markdown_report(report), encoding="utf-8")

    if not args.quiet:
        print(f"[OK] JSON 리포트: {json_path}")
        print(f"[OK] Markdown 리포트: {md_path}")
        print(f"overall={report.overall_verdict} "
              f"paper_autotrade_ready={report.paper_autotrade_ready}")
        print(f"ready_for_market_open_rehearsal={report.ready_for_market_open_rehearsal}")
        print(f"PASS={report.counts.get('PASS',0)} WARN={report.counts.get('WARN',0)} "
              f"FAIL={report.counts.get('FAIL',0)}")
        print("NOTE: 코드 감사 전용 — 실제 KIS API 0건, 실전 승인 아님. 장중은 BUILD-02B.")

    return 0 if report.paper_autotrade_ready else 1


if __name__ == "__main__":
    raise SystemExit(main())

#!/usr/bin/env python3
"""#48 / 6-03 — Agent / Risk Gate 스트레스 테스트 CLI (Paper 검증 전용).

실전과 유사한 악조건(슬리피지 / 부분체결 / 거절 / 미체결 / stale price / 급락 /
급등 / 데이터락 / 포트폴리오 drift / 일일 손실한도 / kill switch)을 재현하고, 기존
안전 가드가 정상 동작하는지 검증해 stress_test_report 를 생성한다.

**본 스크립트는 *검증* 전용이다 — 실제 주문을 생성/전송하지 않으며, broker /
OrderExecutor / 단일 주문 라우터 / KIS API 를 호출하지 않는다. 스트레스 결과만으로
실전 전환을 허가하지 않는다.**

CLAUDE.md 절대 원칙:
- read-only. broker / 단일 주문 라우터 / OrderExecutor / KIS API / AI SDK /
  외부 HTTP 호출 0건.
- `ENABLE_LIVE_TRADING` / `ENABLE_AI_EXECUTION` / `ENABLE_FUTURES_LIVE_TRADING` /
  `KIS_IS_PAPER` 환경 변수 수정 0건, `.env` 작성/갱신 0건.
- secret / API key / 계좌번호 출력 0건, "수익 보장" / "실전 전환 승인" 문구 0건.

사용:
    python scripts/run_agent_stress_test.py --output reports/stress/latest.json \\
        --markdown reports/stress/latest.md
    python scripts/run_agent_stress_test.py --scenario MARKET_CRASH
    python scripts/run_agent_stress_test.py --scenario PRICE_STALE --strict

옵션:
    --scenario ALL (기본) 또는 단일 시나리오 이름
    --output / --markdown / --strict / --seed

exit code:
    0: 모든 시나리오 PASS 또는 WARN (필수 가드 정상)
    1: FAIL 있음
    2: 입력/설정 오류 (알 수 없는 scenario 등)
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

from app.stress_test.agent_stress_test import (  # noqa: E402
    ALL_SCENARIOS,
    StressVerdict,
    render_markdown_report,
    run_agent_stress_test,
    summarize_stress_report,
)

DEFAULT_OUTPUT_DIR = "reports/stress"


def _parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    p = argparse.ArgumentParser(
        description="Agent / Risk Gate 스트레스 테스트 — Paper 검증 전용, 실전 전환 아님.",
    )
    p.add_argument("--scenario", default="ALL",
                   help="실행할 시나리오 ('ALL' 또는 단일 이름, 예: MARKET_CRASH).")
    p.add_argument("--output", default=None, help="JSON 리포트 경로 (미지정 시 reports/stress/).")
    p.add_argument("--markdown", default=None, help="Markdown 리포트 경로 (미지정 시 JSON 옆 .md).")
    p.add_argument("--strict", action="store_true", help="WARN 도 실패(exit 1)로 격상.")
    p.add_argument("--seed", type=int, default=0, help="결정론적 시드 (기본 0).")
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

    scenario = str(args.scenario or "ALL").strip().upper()
    if scenario == "ALL":
        scenarios = None
    elif scenario in ALL_SCENARIOS:
        scenarios = (scenario,)
    else:
        print(f"[ERROR] 알 수 없는 scenario: {args.scenario}. 가능한 값: ALL, "
              f"{', '.join(ALL_SCENARIOS)}", file=sys.stderr)
        return 2

    report = run_agent_stress_test(scenarios=scenarios, strict=bool(args.strict),
                                   seed=int(args.seed))

    if args.output:
        json_path = Path(args.output)
    else:
        json_path = Path(DEFAULT_OUTPUT_DIR) / _timestamp_name("stress_test", "json")
    md_path = Path(args.markdown) if args.markdown else json_path.with_suffix(".md")

    json_path.parent.mkdir(parents=True, exist_ok=True)
    md_path.parent.mkdir(parents=True, exist_ok=True)

    json_path.write_text(
        json.dumps(summarize_stress_report(report), ensure_ascii=False, indent=2),
        encoding="utf-8")
    md_path.write_text(render_markdown_report(report), encoding="utf-8")

    if not args.quiet:
        print(f"[OK] JSON 리포트: {json_path}")
        print(f"[OK] Markdown 리포트: {md_path}")
        print(f"overall_verdict={report.overall_verdict} reason_code={report.reason_code}")
        print(f"PASS={report.counts.get('PASS',0)} WARN={report.counts.get('WARN',0)} "
              f"FAIL={report.counts.get('FAIL',0)}")
        print(f"risk_gate_triggered={report.risk_gate_triggered_count} "
              f"kill_switch_triggered={report.kill_switch_triggered_count} "
              f"kill_switch_should_trigger={report.kill_switch_should_trigger_count}")
        print("NOTE: Paper 검증 전용 — 실전 전환 아님, 실제 주문 0건.")

    return 1 if report.overall_verdict == StressVerdict.FAIL.value else 0


if __name__ == "__main__":
    raise SystemExit(main())

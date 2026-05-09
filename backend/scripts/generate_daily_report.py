"""#57: Daily Report CLI - `reports/daily_YYYY-MM-DD.md` 생성기.

본 스크립트는 read-only DB SELECT + markdown 파일 작성만 수행한다 - broker /
OrderExecutor / route_order 호출 0건, 외부 HTTP / AI 호출 0건.

## 본 리포트는 *투자 조언이 아닙니다*.

자동매매 시스템 운영·검증·개선 자료이며, 종목 추천 / 매수 매도 신호를
포함하지 않습니다.

## 사용법

```
# 어제 (KST) 리포트 생성
python scripts/generate_daily_report.py --date 2026-05-09 --output-dir reports

# 가상 + 선물 audit 모두 포함
python scripts/generate_daily_report.py --date 2026-05-09 \\
    --include-virtual --include-futures

# 미리보기 (파일 작성 X - stdout만)
python scripts/generate_daily_report.py --date 2026-05-09 --dry-run
```
"""

from __future__ import annotations

import argparse
import sys
from datetime import date as Date, datetime, timezone
from pathlib import Path

# 본 스크립트가 `python scripts/generate_daily_report.py`로 직접 실행될 때
# `app` 패키지를 찾을 수 있게 backend/ 를 sys.path에 추가.
_BACKEND_ROOT = Path(__file__).resolve().parent.parent
if str(_BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(_BACKEND_ROOT))


def _parse_date(s: str) -> Date:
    return datetime.strptime(s, "%Y-%m-%d").date()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Generate daily system report (markdown) - read-only DB analysis. "
            "본 리포트는 *투자 조언이 아닙니다*."
        ),
    )
    parser.add_argument(
        "--date", type=_parse_date, default=None,
        help="리포트 대상 날짜 (KST, YYYY-MM-DD). 기본: 오늘.",
    )
    parser.add_argument(
        "--output-dir", type=Path, default=Path("reports"),
        help="출력 디렉토리 (기본: reports/).",
    )
    parser.add_argument(
        "--include-virtual", action="store_true", default=True,
        help="VirtualOrder 데이터 포함 (기본: 포함).",
    )
    parser.add_argument(
        "--include-futures", action="store_true", default=True,
        help="FuturesOrderAuditLog 데이터 포함 (기본: 포함).",
    )
    parser.add_argument(
        "--format", choices=["markdown"], default="markdown",
        help="출력 포맷 (현재 markdown만).",
    )
    parser.add_argument(
        "--dry-run", action="store_true",
        help="파일 작성 X - stdout으로만 출력.",
    )
    args = parser.parse_args(argv)

    # 본 함수에서만 import - argparse --help가 빠르게 응답하도록.
    from app.agents.daily_report_agent import (
        DailyReportInput,
        analyze_daily,
        load_agent_decisions_for_date,
        load_audit_rows_for_date,
        load_backtest_runs_for_date,
        load_emergency_events_for_date,
        load_futures_audit_for_date,
        load_pending_approvals_for_date,
        load_virtual_orders_for_date,
    )
    from app.core.config import get_settings
    from app.db.session import SessionLocal

    report_date = args.date or datetime.now(timezone.utc).date()
    settings = get_settings()

    db = SessionLocal()
    try:
        audit_rows = load_audit_rows_for_date(db, report_date)
        virtual_orders = (
            load_virtual_orders_for_date(db, report_date)
            if args.include_virtual else []
        )
        futures_audit = (
            load_futures_audit_for_date(db, report_date)
            if args.include_futures else []
        )
        agent_decisions = load_agent_decisions_for_date(db, report_date)
        emergency_events = load_emergency_events_for_date(db, report_date)
        pending_approvals = load_pending_approvals_for_date(db, report_date)
        backtest_runs = load_backtest_runs_for_date(db, report_date)
    finally:
        db.close()

    inp = DailyReportInput(
        report_date=report_date,
        operation_mode=settings.default_mode.value,
        audit_rows=tuple(audit_rows),
        virtual_orders=tuple(virtual_orders),
        futures_audit_rows=tuple(futures_audit),
        agent_decisions=tuple(agent_decisions),
        emergency_events=tuple(emergency_events),
        pending_approvals=tuple(pending_approvals),
        backtest_runs=tuple(backtest_runs),
    )
    report = analyze_daily(inp)

    if args.dry_run:
        # stdout - 인코딩 에러 회피 위해 utf-8 wrapping.
        try:
            sys.stdout.write(report.markdown_report + "\n")
            sys.stdout.flush()
        except UnicodeEncodeError:
            sys.stdout.buffer.write(report.markdown_report.encode("utf-8"))
        return 0

    # 파일 작성
    args.output_dir.mkdir(parents=True, exist_ok=True)
    out_path = args.output_dir / f"daily_{report_date.isoformat()}.md"
    out_path.write_text(report.markdown_report, encoding="utf-8")

    # 운영자 친화적 stderr 출력 (stdout은 markdown 미리보기용으로 비워둠)
    sys.stderr.write(
        f"[daily-report] 작성 완료 - {out_path} "
        f"(findings={len(report.findings)}, "
        f"warnings={len(report.tomorrow_warnings)}, "
        f"actions={len(report.action_items)})\n"
    )
    sys.stderr.write(
        "[daily-report] 본 리포트는 투자 조언이 아니며 시스템 운영 자료입니다.\n"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())

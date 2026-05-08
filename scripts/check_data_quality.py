#!/usr/bin/env python3
"""MarketBar 데이터 품질 리포트 CLI (#21).

실 broker API를 호출하지 않는다 — DB MarketBar 테이블 조회만 수행.
시크릿(.env)을 출력하지 않는다.

사용 예:
    # 단일 일자
    python scripts/check_data_quality.py --symbol 005930 --interval 1m \
        --date 2026-05-07

    # 기간
    python scripts/check_data_quality.py --symbol 005930 --interval 1m \
        --start-date 2026-05-01 --end-date 2026-05-07 --format json

    # 결과 파일 저장
    python scripts/check_data_quality.py --symbol 005930 --interval 1d \
        --start-date 2026-05-01 --end-date 2026-05-31 --format json \
        --output reports/quality.json

자세한 정책: docs/data_quality_report.md.
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import date, datetime, timezone
from pathlib import Path

# `python scripts/...` 실행 시 backend/app을 import할 수 있도록.
_ROOT = Path(__file__).resolve().parent.parent
_BACKEND = _ROOT / "backend"
if str(_BACKEND) not in sys.path:
    sys.path.insert(0, str(_BACKEND))

from app.db.session import SessionLocal, apply_migrations  # noqa: E402
from app.market.data_quality import (  # noqa: E402
    DailyQualityReport,
    evaluate_day,
    evaluate_range,
    fetch_bars_for_day,
    summarize_reports,
)


def _parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    p = argparse.ArgumentParser(
        description="MarketBar 데이터 품질 리포트 (체크리스트 #21)",
    )
    p.add_argument("--symbol",   required=True,
                   help="종목 코드 (예: 005930)")
    p.add_argument("--interval", required=True,
                   help="봉 간격 (1m / 5m / 1h / 1d)")
    g = p.add_mutually_exclusive_group(required=True)
    g.add_argument("--date", type=date.fromisoformat,
                   help="단일 일자 (YYYY-MM-DD, KST)")
    g.add_argument("--start-date", dest="start_date", type=date.fromisoformat,
                   help="기간 시작일 (KST)")
    p.add_argument("--end-date", dest="end_date", type=date.fromisoformat,
                   help="기간 종료일 (KST). --start-date와 함께 사용")
    p.add_argument("--min-score", dest="min_score", type=float, default=None,
                   help="이 점수 미만 일자만 출력 (text/json 공통)")
    p.add_argument("--format", choices=("text", "json"), default="text")
    p.add_argument("--include-out-of-session", action="store_true",
                   help="장외 데이터를 점수 감점 대상에서 제외")
    p.add_argument("--output", type=Path, default=None,
                   help="리포트 저장 경로 (미지정 시 stdout)")
    args = p.parse_args(argv)

    if args.start_date and not args.end_date:
        p.error("--end-date is required with --start-date")
    if args.end_date and not args.start_date:
        p.error("--start-date is required with --end-date")
    if args.start_date and args.end_date and args.start_date > args.end_date:
        p.error("--start-date must be <= --end-date")
    return args


def _filter_by_min_score(reports: list[DailyQualityReport], threshold: float | None) -> list[DailyQualityReport]:
    if threshold is None:
        return reports
    return [r for r in reports if r.score < threshold]


def _format_text(reports: list[DailyQualityReport]) -> str:
    lines: list[str] = []
    for r in reports:
        lines.append(
            f"[{r.report_date.isoformat()}] {r.symbol} {r.interval}  "
            f"score={r.score:.1f} grade={r.grade}  "
            f"actual={r.issues.actual_count}/{r.issues.expected_count} "
            f"missing={r.issues.missing_count} dup={r.issues.duplicate_count} "
            f"ohlc_bad={r.issues.ohlc_invalid_count} "
            f"oos={r.issues.out_of_session_count}"
        )
        if r.notes:
            for note in r.notes:
                lines.append(f"    · {note}")
    summary = summarize_reports(reports)
    lines.append("")
    lines.append(f"summary: {summary}")
    return "\n".join(lines)


def _format_json(reports: list[DailyQualityReport]) -> str:
    payload = {
        "reports": [r.to_dict() for r in reports],
        "summary": summarize_reports(reports),
    }
    return json.dumps(payload, ensure_ascii=False, indent=2)


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv)
    now = datetime.now(timezone.utc)

    # CLI는 backend lifespan을 거치지 않으므로 직접 alembic head를 적용한다.
    # 빈 SQLite를 가리키는 DATABASE_URL에서도 빈 리포트를 생성할 수 있도록.
    apply_migrations()

    with SessionLocal() as db:
        if args.date is not None:
            bars = fetch_bars_for_day(
                db, symbol=args.symbol, interval=args.interval, report_date=args.date,
            )
            reports = [evaluate_day(
                bars,
                symbol=args.symbol, interval=args.interval, report_date=args.date,
                now=now, include_out_of_session=args.include_out_of_session,
            )]
        else:
            reports = evaluate_range(
                db,
                symbol=args.symbol, interval=args.interval,
                start_date=args.start_date, end_date=args.end_date,
                now=now, include_out_of_session=args.include_out_of_session,
            )

    reports = _filter_by_min_score(reports, args.min_score)

    if args.format == "json":
        text = _format_json(reports)
    else:
        text = _format_text(reports)

    if args.output is not None:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(text + "\n", encoding="utf-8")
        print(f"wrote {len(reports)} report(s) to {args.output}")
    else:
        print(text)

    return 0


if __name__ == "__main__":
    raise SystemExit(main())

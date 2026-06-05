"""PART3: 하루 마감 1장 요약 CLI — `reports/daily/close_YYYY-MM-DD.md` 생성.

장 마감 후(또는 수동) 그날을 *초보자 1장* 으로 정리. read-only DB SELECT +
markdown 작성만 — broker / route_order / 외부 호출 0건.

사용법:
  # 오늘(KST) 요약
  python scripts/generate_daily_close_summary.py

  # 특정 날짜
  python scripts/generate_daily_close_summary.py --date 2026-06-01

  # 미리보기(파일 작성 X)
  python scripts/generate_daily_close_summary.py --date 2026-06-01 --dry-run

exit code: 0 정상 / 2 입력오류.
"""

from __future__ import annotations

import argparse
import sys
from datetime import date as Date
from datetime import datetime, timedelta, timezone
from pathlib import Path

_BACKEND_ROOT = Path(__file__).resolve().parent.parent
if str(_BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(_BACKEND_ROOT))

_KST = timezone(timedelta(hours=9))


def _today_kst() -> Date:
    return datetime.now(timezone.utc).astimezone(_KST).date()


def _parse_date(s: str) -> Date:
    return datetime.strptime(s, "%Y-%m-%d").date()


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(
        description="하루 마감 1장 요약 생성 (read-only). 투자 조언 아님.")
    p.add_argument("--date", type=str, default=None,
                   help="KST 날짜 YYYY-MM-DD (기본: 오늘)")
    p.add_argument("--output-dir", type=str, default="reports/daily",
                   help="저장 폴더 (기본: reports/daily)")
    p.add_argument("--dry-run", action="store_true",
                   help="파일 작성 없이 stdout 출력만")
    args = p.parse_args(argv)

    try:
        report_date = _parse_date(args.date) if args.date else _today_kst()
    except ValueError:
        print("ERROR: --date 는 YYYY-MM-DD 형식이어야 합니다.", file=sys.stderr)
        return 2

    from app.agents.daily_close_summary import (
        build_daily_close_summary,
        render_markdown,
    )
    from app.db.session import SessionLocal

    with SessionLocal() as db:
        summary = build_daily_close_summary(db, report_date)
    md = render_markdown(summary)

    if args.dry_run:
        print(md)
        return 0

    # 출력 폴더: backend/ 기준이 아니라 repo 루트 기준으로 resolve.
    out_dir = Path(args.output_dir)
    if not out_dir.is_absolute():
        out_dir = _BACKEND_ROOT.parent / out_dir
    out_dir.mkdir(parents=True, exist_ok=True)
    out_path = out_dir / f"close_{report_date.isoformat()}.md"
    out_path.write_text(md, encoding="utf-8")
    print(f"작성됨: {out_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

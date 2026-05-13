#!/usr/bin/env python3
"""체크리스트 #72 — Paper Gate 평가 CLI.

CLAUDE.md 절대 원칙:
- 본 스크립트는 *read-only*. broker / route_order / OrderExecutor /
  외부 API 호출 0건.
- `.env` 직접 읽기는 SQLAlchemy 엔진을 통해 *DATABASE_URL* 만 사용 — Secret /
  API Key / 계좌번호는 본 CLI가 다루지 않는다.
- `ENABLE_LIVE_TRADING` / `ENABLE_AI_EXECUTION` / `ENABLE_FUTURES_LIVE_TRADING`
  변경 0건.

사용법:
    # 운영 DB 조회 + 4주 자동 기간 + JSON 출력
    python scripts/evaluate_paper_gate.py --strategy sma_cross --format json

    # markdown 리포트 파일 저장
    python scripts/evaluate_paper_gate.py \\
        --strategy sma_cross \\
        --period-start 2026-04-15 --period-end 2026-05-13 \\
        --format markdown --output reports/paper_gate_sma_cross.md

    # DB 없이 dry-run (수동 메트릭 입력)
    python scripts/evaluate_paper_gate.py --dry-run --expectancy 350 \\
        --pf-numerator 200000 --pf-denominator 150000 \\
        --trade-count 120 --active-days 30 \\
        --max-drawdown-value 800000

본 스크립트는 *Paper Gate 평가 결과*만 출력한다. exit code:
- 0: PASS / CAUTION / UNKNOWN
- 1: FAIL
- 2: 실행 오류 (DB 연결 실패 등)
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path


def _add_backend_to_path() -> None:
    """`backend/` 디렉토리를 sys.path에 추가해 app.* import 가능하게."""
    project_root = Path(__file__).resolve().parents[1]
    backend = project_root / "backend"
    if backend.exists():
        sys.path.insert(0, str(backend))


_add_backend_to_path()


def _parse_date(s: str) -> datetime:
    # YYYY-MM-DD 또는 ISO 형식 둘 다 허용.
    try:
        if len(s) == 10:
            return datetime.strptime(s, "%Y-%m-%d").replace(tzinfo=timezone.utc)
        return datetime.fromisoformat(s).replace(tzinfo=timezone.utc)
    except Exception as exc:  # noqa: BLE001
        raise argparse.ArgumentTypeError(
            f"날짜 형식이 올바르지 않습니다: {s} ({exc})"
        )


def _ensure_utf8_stdout() -> None:
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:  # noqa: BLE001
        pass


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Paper Gate 평가 CLI (#72). read-only — broker 호출 0건.",
    )
    parser.add_argument("--strategy", default=None,
                        help="평가 대상 전략. 미명시 = paper 모드 전체")
    parser.add_argument("--period-start", type=_parse_date, default=None,
                        help="평가 기간 시작 (YYYY-MM-DD)")
    parser.add_argument("--period-end", type=_parse_date, default=None,
                        help="평가 기간 끝 (YYYY-MM-DD). 기본 = 오늘")
    parser.add_argument("--days", type=int, default=28,
                        help="period-start 미명시 시 자동 윈도우 일수 (default 28)")
    parser.add_argument("--format", choices=("markdown", "json"),
                        default="markdown")
    parser.add_argument("--output", default=None,
                        help="결과 저장 경로. 미명시 = stdout")
    parser.add_argument("--dry-run", action="store_true",
                        help="DB 미연결 — 수동 메트릭으로 평가")
    parser.add_argument("--initial-cash", type=int, default=10_000_000)
    parser.add_argument("--expectancy", type=float, default=None)
    parser.add_argument("--pf-numerator",   type=int, default=None,
                        help="profit factor 계산용 winning pnl 합")
    parser.add_argument("--pf-denominator", type=int, default=None,
                        help="profit factor 계산용 losing pnl 합 (절댓값)")
    parser.add_argument("--trade-count",        type=int, default=None)
    parser.add_argument("--active-days",        type=int, default=None)
    parser.add_argument("--max-drawdown-value", type=int, default=None)
    parser.add_argument("--loss-limit-violations", type=int, default=0)
    parser.add_argument("--audit-missing-count", type=int, default=0)
    parser.add_argument("--stale-or-duplicate-violations", type=int, default=0)
    parser.add_argument("--rejection-rate", type=float, default=None)
    parser.add_argument("--best-day-pnl-share", type=float, default=None)
    parser.add_argument("--hourly-loss-top-share", type=float, default=None)
    parser.add_argument("--paper-vs-backtest-pf-drift", type=float, default=None)
    parser.add_argument("--fill-polling-consistent",
                        action=argparse.BooleanOptionalAction, default=True)
    parser.add_argument("--client-order-id-idempotent",
                        action=argparse.BooleanOptionalAction, default=True)
    args = parser.parse_args()

    _ensure_utf8_stdout()

    try:
        from app.governance.paper_gate import (
            PaperGateInput,
            evaluate_paper_gate,
            render_markdown_report,
        )
    except Exception as exc:  # noqa: BLE001
        sys.stderr.write(
            f"paper_gate 모듈 import 실패 — backend가 설치되어 있어야 합니다. ({exc})\n"
        )
        return 2

    # 기간 결정.
    end   = args.period_end   or datetime.now(timezone.utc)
    start = args.period_start or (end - timedelta(days=args.days))

    # ---- input 구성 ----
    if args.dry_run:
        winning = int(args.pf_numerator or 0)
        losing  = int(args.pf_denominator or 0)
        inp = PaperGateInput(
            strategy_name=args.strategy or "(dry-run)",
            period_start=start,
            period_end=end,
            trade_count=int(args.trade_count or 0),
            active_days=int(args.active_days or 0),
            winning_pnl_sum=winning,
            losing_pnl_sum=losing,
            expectancy=float(args.expectancy or 0.0),
            max_drawdown_value=int(args.max_drawdown_value or 0),
            initial_cash=int(args.initial_cash),
            loss_limit_violations=int(args.loss_limit_violations),
            audit_missing_count=int(args.audit_missing_count),
            stale_or_duplicate_violations=int(args.stale_or_duplicate_violations),
            rejection_rate=float(args.rejection_rate or 0.0),
            best_day_pnl_share=args.best_day_pnl_share,
            hourly_loss_top_share=args.hourly_loss_top_share,
            paper_vs_backtest_pf_drift=args.paper_vs_backtest_pf_drift,
            fill_polling_consistent=bool(args.fill_polling_consistent),
            client_order_id_idempotent=bool(args.client_order_id_idempotent),
        )
    else:
        try:
            from app.db.session import SessionLocal
            from app.governance.paper_gate_collector import build_paper_gate_input
        except Exception as exc:  # noqa: BLE001
            sys.stderr.write(
                f"DB 모듈 import 실패. --dry-run 옵션 사용 가능. ({exc})\n"
            )
            return 2

        db = SessionLocal()
        try:
            inp = build_paper_gate_input(
                db,
                strategy=args.strategy,
                period_start=start,
                period_end=end,
                initial_cash=args.initial_cash,
                expectancy=args.expectancy,
                winning_pnl_sum=args.pf_numerator,
                losing_pnl_sum=args.pf_denominator,
                max_drawdown_value=args.max_drawdown_value,
                loss_limit_violations=args.loss_limit_violations,
                audit_missing_count=args.audit_missing_count,
                stale_or_duplicate_violations=args.stale_or_duplicate_violations,
                best_day_pnl_share=args.best_day_pnl_share,
                hourly_loss_top_share=args.hourly_loss_top_share,
                paper_vs_backtest_pf_drift=args.paper_vs_backtest_pf_drift,
                fill_polling_consistent=bool(args.fill_polling_consistent),
                client_order_id_idempotent=bool(args.client_order_id_idempotent),
            )
        except Exception as exc:  # noqa: BLE001
            sys.stderr.write(f"DB 조회 실패: {exc}\n")
            return 2
        finally:
            db.close()

    # ---- evaluate ----
    result = evaluate_paper_gate(inp)

    if args.format == "json":
        out = json.dumps(result.to_dict(), indent=2, ensure_ascii=False)
    else:
        out = render_markdown_report(result)

    if args.output:
        Path(args.output).write_text(out, encoding="utf-8")
        print(f"📄 리포트 저장: {args.output}")
        print(f"   판정: {result.verdict.value}")
    else:
        print(out)

    return 1 if result.verdict.value == "FAIL" else 0


if __name__ == "__main__":
    sys.exit(main())

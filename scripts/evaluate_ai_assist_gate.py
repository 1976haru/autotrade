#!/usr/bin/env python3
"""체크리스트 #74 — AI Assist Gate 평가 CLI.

CLAUDE.md 절대 원칙:
- 본 스크립트는 read-only. broker / OrderExecutor / route_order / AI provider /
  외부 HTTP 호출 0건.
- `.env` 직접 읽지 않는다 — DATABASE_URL 만 SQLAlchemy 엔진 경유.
- `ENABLE_LIVE_TRADING` / `ENABLE_AI_EXECUTION` / `ENABLE_FUTURES_LIVE_TRADING`
  변경 0건.

사용법:
    # 운영 DB + 자동 28일 윈도우 (JSON)
    python scripts/evaluate_ai_assist_gate.py --strategy ai_signals --format json

    # markdown 리포트 저장
    python scripts/evaluate_ai_assist_gate.py \\
        --strategy ai_signals \\
        --period-start 2026-04-15 --period-end 2026-05-13 \\
        --approved-expectancy 250 --approved-win-count 30 --approved-loss-count 20 \\
        --format markdown --output reports/ai_assist_ai_signals.md

    # DB 없이 dry-run (수동 메트릭)
    python scripts/evaluate_ai_assist_gate.py --dry-run \\
        --strategy ai_signals --proposal-count 150 --approved-proposals 60 \\
        --risk-rejected-proposals 30 --operator-rejected-proposals 40 \\
        --approved-expectancy 200 --approved-win-count 35 --approved-loss-count 25 \\
        --confidence-calibration 0.7

본 스크립트는 *AI Assist Gate 평가 결과*만 출력한다. exit code:
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
    project_root = Path(__file__).resolve().parents[1]
    backend = project_root / "backend"
    if backend.exists():
        sys.path.insert(0, str(backend))


_add_backend_to_path()


def _parse_date(s: str) -> datetime:
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
        description="AI Assist Gate 평가 CLI (#74). read-only — broker / AI provider 호출 0건.",
    )
    parser.add_argument("--strategy", default=None)
    parser.add_argument("--period-start", type=_parse_date, default=None)
    parser.add_argument("--period-end",   type=_parse_date, default=None)
    parser.add_argument("--days", type=int, default=28)
    parser.add_argument("--format", choices=("markdown", "json"), default="markdown")
    parser.add_argument("--output", default=None)
    parser.add_argument("--dry-run", action="store_true")

    # 수동 메트릭 (dry-run 또는 DB carry).
    parser.add_argument("--proposal-count",                type=int, default=None)
    parser.add_argument("--approved-proposals",            type=int, default=None)
    parser.add_argument("--risk-rejected-proposals",       type=int, default=None)
    parser.add_argument("--operator-rejected-proposals",   type=int, default=None)
    parser.add_argument("--expired-or-cancelled",          type=int, default=None)
    parser.add_argument("--approved-expectancy",           type=float, default=None)
    parser.add_argument("--approved-winning-pnl-sum",      type=int, default=None)
    parser.add_argument("--approved-losing-pnl-sum",       type=int, default=None)
    parser.add_argument("--approved-win-count",            type=int, default=None)
    parser.add_argument("--approved-loss-count",           type=int, default=None)
    parser.add_argument("--confidence-calibration",        type=float, default=None)
    parser.add_argument("--rejected-but-would-have-won",   type=int, default=0)
    parser.add_argument("--ai-decision-audit-drift",       type=int, default=0)
    parser.add_argument("--emergency-stops-in-period",     type=int, default=None)
    parser.add_argument("--active-days",                   type=int, default=None)
    args = parser.parse_args()

    _ensure_utf8_stdout()

    try:
        from app.governance.ai_assist_gate import (
            AIAssistGateInput,
            evaluate_ai_assist_gate,
            render_markdown_report,
        )
    except Exception as exc:  # noqa: BLE001
        sys.stderr.write(
            f"ai_assist_gate 모듈 import 실패 — backend 환경 확인. ({exc})\n"
        )
        return 2

    end   = args.period_end   or datetime.now(timezone.utc)
    start = args.period_start or (end - timedelta(days=args.days))

    if args.dry_run:
        inp = AIAssistGateInput(
            strategy_name=args.strategy or "(dry-run)",
            period_start=start,
            period_end=end,
            proposal_count=int(args.proposal_count or 0),
            approved_proposals=int(args.approved_proposals or 0),
            risk_rejected_proposals=int(args.risk_rejected_proposals or 0),
            operator_rejected_proposals=int(args.operator_rejected_proposals or 0),
            expired_or_cancelled=int(args.expired_or_cancelled or 0),
            approved_expectancy=float(args.approved_expectancy or 0.0),
            approved_winning_pnl_sum=int(args.approved_winning_pnl_sum or 0),
            approved_losing_pnl_sum=int(args.approved_losing_pnl_sum or 0),
            approved_win_count=int(args.approved_win_count or 0),
            approved_loss_count=int(args.approved_loss_count or 0),
            confidence_calibration=float(args.confidence_calibration or 0.0),
            rejected_but_would_have_won=int(args.rejected_but_would_have_won),
            ai_decision_audit_drift=int(args.ai_decision_audit_drift),
            emergency_stops_in_period=int(args.emergency_stops_in_period or 0),
            active_days=int(args.active_days or 0),
        )
    else:
        try:
            from app.db.session import SessionLocal
            from app.governance.ai_assist_gate_collector import build_ai_assist_gate_input
        except Exception as exc:  # noqa: BLE001
            sys.stderr.write(
                f"DB 모듈 import 실패. --dry-run 옵션 사용 가능. ({exc})\n"
            )
            return 2
        db = SessionLocal()
        try:
            inp = build_ai_assist_gate_input(
                db,
                strategy=args.strategy,
                period_start=start,
                period_end=end,
                approved_expectancy=args.approved_expectancy,
                approved_winning_pnl_sum=args.approved_winning_pnl_sum,
                approved_losing_pnl_sum=args.approved_losing_pnl_sum,
                approved_win_count=args.approved_win_count,
                approved_loss_count=args.approved_loss_count,
                rejected_but_would_have_won=args.rejected_but_would_have_won,
                active_days_override=args.active_days,
                ai_decision_audit_drift=args.ai_decision_audit_drift,
            )
        except Exception as exc:  # noqa: BLE001
            sys.stderr.write(f"DB 조회 실패: {exc}\n")
            return 2
        finally:
            db.close()

    result = evaluate_ai_assist_gate(inp)

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

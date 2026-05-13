#!/usr/bin/env python3
"""체크리스트 #80 — Pre-market Checklist CLI.

장 시작 전 자동 점검을 운영자 콘솔에서 직접 실행. read-only —
broker / OrderExecutor / route_order / 외부 API 호출 0건. `.env` /
Secret 출력 0건.

CLI:
    # SIMULATION 모드 dry-run (default 값).
    python scripts/pre_market_check.py --mode SIMULATION

    # PAPER 모드 + 운영자 입력.
    python scripts/pre_market_check.py --mode PAPER \\
        --broker-ready --kis-is-paper \\
        --data-freshness-ok --watchlist 5 --strategies 2 \\
        --format json

    # LIVE_MANUAL_APPROVAL + manual ack + strict.
    python scripts/pre_market_check.py --mode LIVE_MANUAL_APPROVAL \\
        --broker-ready --strict --manual-ack --manual-ack-by "operator"

exit code:
  0  : READY_TO_START / WARN_BUT_START_ALLOWED (start_allowed=True)
  1  : DO_NOT_START   (start_allowed=False)
  2  : 실행 오류 (import 실패 등)
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path


def _add_backend_to_path() -> None:
    project_root = Path(__file__).resolve().parents[1]
    backend = project_root / "backend"
    if backend.exists():
        sys.path.insert(0, str(backend))


_add_backend_to_path()


def _ensure_utf8_stdout() -> None:
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:  # noqa: BLE001
        pass


def _boolish(parser, name: str, *, default: bool = False, help_text: str = ""):
    parser.add_argument(
        f"--{name}", dest=name.replace("-", "_"),
        action=argparse.BooleanOptionalAction, default=default, help=help_text,
    )


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Pre-market Checklist CLI (#80). read-only — broker / 외부 API 호출 0건.",
    )
    parser.add_argument(
        "--mode", default="SIMULATION",
        choices=[
            "SIMULATION", "PAPER", "LIVE_SHADOW", "LIVE_MANUAL_APPROVAL",
            "LIVE_AI_ASSIST", "LIVE_AI_EXECUTION", "VIRTUAL_AI_EXECUTION",
        ],
    )
    parser.add_argument("--format", choices=("markdown", "json"), default="markdown")
    parser.add_argument("--strict", action="store_true",
                        help="strict 모드 — UNKNOWN(required)도 FAIL 취급")
    parser.add_argument("--output", default=None, help="결과 저장 경로 (선택)")

    # 환경 / 시스템 상태.
    _boolish(parser, "api-reachable", default=True)
    _boolish(parser, "db-reachable", default=True)
    _boolish(parser, "broker-ready", default=False)
    _boolish(parser, "kis-is-paper", default=True)
    _boolish(parser, "kis-credentials-present", default=False)

    parser.add_argument("--market-data-provider", default="mock")
    _boolish(parser, "data-freshness-ok", default=False)
    parser.add_argument("--stale-symbol-count", type=int, default=0)

    parser.add_argument("--watchlist", type=int, default=0, dest="watchlist_item_count")
    parser.add_argument("--strategies", type=int, default=0, dest="active_strategy_count")

    _boolish(parser, "risk-policy-configured", default=True)
    _boolish(parser, "daily-loss-limit-configured", default=True)
    parser.add_argument("--daily-loss-used-ratio", type=float, default=0.0)
    _boolish(parser, "position-limits-configured", default=True)

    _boolish(parser, "emergency-stop-active", default=False)
    parser.add_argument("--kill-switch-level", default="OFF",
                        choices=["OFF", "LEVEL_1", "LEVEL_2", "LEVEL_3"])

    _boolish(parser, "ai-permission-gate-active", default=True)
    _boolish(parser, "ai-execution-enabled", default=False)
    _boolish(parser, "enable-live-trading", default=False)
    _boolish(parser, "enable-futures-live-trading", default=False)

    _boolish(parser, "notification-configured", default=False)

    # Governance gates carry.
    parser.add_argument("--paper-gate-pass",       type=int, default=None,
                        choices=[0, 1], help="1=pass / 0=fail / 미지정=unknown")
    parser.add_argument("--live-manual-gate-pass", type=int, default=None,
                        choices=[0, 1])
    parser.add_argument("--ai-assist-gate-pass",   type=int, default=None,
                        choices=[0, 1])
    parser.add_argument("--ai-execution-gate-ready", type=int, default=None,
                        choices=[0, 1])

    # Operator manual ack.
    parser.add_argument("--manual-ack", action="store_true",
                        help="manual ack 기록 (FAIL 우회 불가)")
    parser.add_argument("--manual-ack-by",   default="")
    parser.add_argument("--manual-ack-note", default="")

    args = parser.parse_args()
    _ensure_utf8_stdout()

    try:
        from app.governance.pre_market_check import (
            PreMarketCheckInput,
            evaluate_pre_market_check,
            render_markdown_report,
        )
    except Exception as exc:  # noqa: BLE001
        sys.stderr.write(
            f"pre_market_check 모듈 import 실패. ({exc})\n"
        )
        return 2

    def _opt_bool(val):
        return None if val is None else bool(val)

    inp = PreMarketCheckInput(
        mode=args.mode,
        strict=args.strict,
        include_optional=True,
        api_reachable=args.api_reachable,
        db_reachable=args.db_reachable,
        broker_ready=args.broker_ready,
        kis_is_paper=args.kis_is_paper,
        kis_credentials_present=args.kis_credentials_present,
        market_data_provider=args.market_data_provider,
        data_freshness_ok=args.data_freshness_ok,
        stale_symbol_count=args.stale_symbol_count,
        watchlist_item_count=args.watchlist_item_count,
        active_strategy_count=args.active_strategy_count,
        risk_policy_configured=args.risk_policy_configured,
        daily_loss_limit_configured=args.daily_loss_limit_configured,
        daily_loss_used_ratio=args.daily_loss_used_ratio,
        position_limits_configured=args.position_limits_configured,
        emergency_stop_active=args.emergency_stop_active,
        kill_switch_level=args.kill_switch_level,
        ai_permission_gate_active=args.ai_permission_gate_active,
        ai_execution_enabled=args.ai_execution_enabled,
        enable_live_trading=args.enable_live_trading,
        enable_futures_live_trading=args.enable_futures_live_trading,
        notification_configured=args.notification_configured,
        paper_gate_pass=_opt_bool(args.paper_gate_pass),
        live_manual_gate_pass=_opt_bool(args.live_manual_gate_pass),
        ai_assist_gate_pass=_opt_bool(args.ai_assist_gate_pass),
        ai_execution_gate_ready=_opt_bool(args.ai_execution_gate_ready),
        manual_ack=args.manual_ack,
        manual_ack_by=args.manual_ack_by,
        manual_ack_note=args.manual_ack_note,
    )
    result = evaluate_pre_market_check(inp)

    if args.format == "json":
        out = json.dumps(result.to_dict(), indent=2, ensure_ascii=False)
    else:
        out = render_markdown_report(result)

    if args.output:
        Path(args.output).write_text(out, encoding="utf-8")
        print(f"📄 결과 저장: {args.output}")
        print(f"   verdict: {result.verdict.value}")
        print(f"   start_allowed: {result.start_allowed}")
    else:
        print(out)

    return 0 if result.start_allowed else 1


if __name__ == "__main__":
    sys.exit(main())

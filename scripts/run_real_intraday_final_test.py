#!/usr/bin/env python3
"""REAL-INTRADAY-TEST-01 — 실제 분봉 데이터로 전략 가능성 최종 테스트 + 사용자 판정 CLI.

data/market/intraday_ohlcv 의 분봉으로 backtest+walk-forward+Agent 비교를 실행하고,
실제 데이터 여부/표본 게이팅을 적용해 *사용자용 최종 판단* 을 산출한다. read-only.

CLAUDE.md 절대 원칙: broker / OrderExecutor / route_order / KIS 주문 API 호출 0건.
합성 fixture 결과를 실제 가능성으로 보고하지 않는다.

exit: 0(평가 완료) / 1(BLOCKED_BY_DATA) / 2(실행 오류)
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

DEFAULT_INPUT_DIR = "data/market/intraday_ohlcv"
DEFAULT_OUTPUT_DIR = "reports/strategy_validation"


def _parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    p = argparse.ArgumentParser(
        description="실제 분봉 데이터 전략 가능성 최종 테스트 (실전 아님, 주문 0건).")
    p.add_argument("--input-dir", default=DEFAULT_INPUT_DIR)
    p.add_argument("--symbols", default=None)
    p.add_argument("--data-source", default="yfinance_intraday")
    p.add_argument("--min-bars", type=int, default=100)
    p.add_argument("--min-days", type=int, default=5)
    p.add_argument("--strict", action="store_true")
    p.add_argument("--quiet", action="store_true")
    return p.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    for _stream in (sys.stdout, sys.stderr):
        try:
            _stream.reconfigure(encoding="utf-8", errors="replace")
        except (AttributeError, ValueError):
            pass
    try:
        from app.system.intraday_strategy_validation import (
            evaluate_intraday_strategy,
            to_dict as intraday_to_dict,
        )
        from app.system.real_intraday_final_result import (
            build_final_result,
            render_markdown,
            to_dict as final_to_dict,
        )

        args = _parse_args(argv)
        syms = [s.strip() for s in args.symbols.split(",") if s.strip()] if args.symbols else None
        gen = datetime.now(timezone.utc).isoformat()
        report = evaluate_intraday_strategy(
            args.input_dir, symbols=syms, strict=bool(args.strict),
            min_bars=int(args.min_bars), min_days=int(args.min_days), generated_at=gen)
        idict = intraday_to_dict(report)
        final = build_final_result(idict, data_source=args.data_source, generated_at=gen)
        fdict = final_to_dict(final)

        outdir = Path(DEFAULT_OUTPUT_DIR)
        outdir.mkdir(parents=True, exist_ok=True)
        (outdir / "real_intraday_final_result.json").write_text(
            json.dumps(fdict, ensure_ascii=False, indent=2), encoding="utf-8")
        (outdir / "real_intraday_final_result.md").write_text(
            render_markdown(final, per_symbol=idict.get("per_symbol")), encoding="utf-8")
        # 카드용 latest.
        (outdir / "intraday_final_latest.json").write_text(
            json.dumps(fdict, ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"[OK] 최종 리포트: {outdir / 'real_intraday_final_result.md'}")
        print(f"[OK] JSON: {outdir / 'real_intraday_final_result.json'}")

        if not args.quiet:
            print(f"actual_data_used={final.actual_data_used} data_source={final.data_source} "
                  f"total_trades={final.total_trades}")
            print(f"developer_verdict={final.developer_verdict} "
                  f"user_final_judgement={final.user_final_judgement}")
            print(f">>> {final.one_line_conclusion}")
            print("NOTE: 실제 분봉 전략 검증 — 자동 적용/실전 전환/주문 0건, 수익 보장 아님.")

        return 1 if final.user_final_judgement == "BLOCKED_BY_DATA" else 0
    except Exception as e:  # noqa: BLE001
        print(f"[ERROR] {type(e).__name__}: {e}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())

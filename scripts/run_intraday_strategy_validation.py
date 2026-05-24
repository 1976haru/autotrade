#!/usr/bin/env python3
"""INTRADAY-DATA-01 — 분봉 데이터 단타 전략 검증 CLI (read-only).

분봉 OHLCV 디렉토리의 PASS 종목만 backtest + walk-forward + Agent 비교 후 단타 전용
verdict 를 산출한다. **품질/시간프레임 FAIL(일봉 포함) 종목은 제외.**

CLAUDE.md 절대 원칙:
- read-only. broker / OrderExecutor / route_order / KIS 주문 API 호출 0건. 주문 0건.
- 결과가 좋아도 자동 적용 / 실전 전환 / live authorization 0건. secret/계좌 원문 0건.
- KIS 분봉 시세 API 미구현 → 분봉 CSV 입력 사용.

exit code:
    0: 평가 완료 (CAUTIOUS/RESEARCH_ONLY/NOT_READY)
    1: BLOCKED (사용 가능한 PASS 분봉 데이터 없음)
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

DEFAULT_OUTPUT_DIR = "reports/strategy_validation"
DEFAULT_INTRADAY_DIR = str(_REPO_ROOT / "backend" / "tests" / "fixtures" / "intraday_clean")


def _parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    p = argparse.ArgumentParser(
        description="분봉 데이터 단타 전략 검증 (실전 아님, 주문 0건).")
    p.add_argument("--input-dir", default=DEFAULT_INTRADAY_DIR,
                   help="분봉 {symbol}.csv 디렉토리 (기본: intraday clean fixture; "
                        "실데이터는 data/market/intraday)")
    p.add_argument("--symbols", default=None)
    p.add_argument("--strict", action="store_true")
    p.add_argument("--output", "--json", dest="output", default=None,
                   help="JSON 리포트 경로 (--json 별칭)")
    p.add_argument("--markdown", default=None)
    p.add_argument("--write-latest", action="store_true",
                   help="reports/strategy_validation/intraday_strategy_latest.json 갱신(카드)")
    p.add_argument("--quiet", action="store_true")
    return p.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    for _stream in (sys.stdout, sys.stderr):
        try:
            _stream.reconfigure(encoding="utf-8", errors="replace")
        except (AttributeError, ValueError):
            pass
    try:
        args = _parse_args(argv)
        from app.system.intraday_strategy_validation import (
            evaluate_intraday_strategy,
            render_markdown,
            to_dict,
        )
        syms = [s.strip() for s in args.symbols.split(",") if s.strip()] if args.symbols else None
        report = evaluate_intraday_strategy(
            args.input_dir, symbols=syms, strict=bool(args.strict),
            generated_at=datetime.now(timezone.utc).isoformat())
        data = to_dict(report)
        md = render_markdown(report)

        out = Path(args.output) if args.output else (
            Path(DEFAULT_OUTPUT_DIR) / "intraday_strategy.json")
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"[OK] JSON: {out}")
        if args.markdown:
            mp = Path(args.markdown)
            mp.parent.mkdir(parents=True, exist_ok=True)
            mp.write_text(md, encoding="utf-8")
            print(f"[OK] Markdown: {mp}")
        if args.write_latest:
            latest = Path(DEFAULT_OUTPUT_DIR) / "intraday_strategy_latest.json"
            latest.parent.mkdir(parents=True, exist_ok=True)
            latest.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
            print(f"[OK] latest 갱신: {latest}")

        if not args.quiet:
            print(f"overall_verdict={report.overall_verdict} intraday_used={report.intraday_data_used} "
                  f"bar_size={report.bar_size_minutes} PASS={len(report.pass_symbols)} "
                  f"total_trades={report.total_trades}")
            print(f"win_rate={report.win_rate} PF={report.profit_factor} WF={report.walk_forward_score} "
                  f"agent={report.agent_value_summary}")
            print("NOTE: 분봉 단타 검증 전용 — 자동 적용/실전 전환/주문 0건. 수익 보장 아님.")

        return 1 if report.overall_verdict == "BLOCKED" else 0
    except Exception as e:  # noqa: BLE001
        print(f"[ERROR] {type(e).__name__}: {e}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())

#!/usr/bin/env python3
"""REAL-DATA-INPUT-01 — 다종목 실제/준실제 OHLCV Backtest + Walk-forward CLI (read-only).

input-dir 의 PASS 종목만 backtest + walk-forward + stress + Agent 비교 후 종목별/전체
aggregate verdict 를 산출한다. **품질 FAIL 종목은 제외(자동 보정/통과 금지).**

CLAUDE.md 절대 원칙:
- read-only. broker / OrderExecutor / route_order / KIS 주문 API 호출 0건. 주문 0건.
- 결과가 좋아도 자동 적용 / 실전 전환 / live authorization 0건. secret/계좌 원문 0건.

exit code:
    0: 평가 완료 (STRONG/CAUTIOUS/RESEARCH_ONLY/NOT_READY)
    1: BLOCKED (사용 가능한 PASS 데이터 없음 / 품질 과반 FAIL)
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
DEFAULT_CLEAN_DIR = str(_REPO_ROOT / "backend" / "tests" / "fixtures" / "real_data_clean")


def _parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    p = argparse.ArgumentParser(
        description="다종목 실제/준실제 OHLCV Backtest+Walk-forward 집계 (실전 아님, 주문 0건).")
    p.add_argument("--input-dir", default=DEFAULT_CLEAN_DIR,
                   help="{symbol}.csv 디렉토리 (기본: clean fixture; 실데이터는 data/market/real_ohlcv)")
    p.add_argument("--symbols", default=None, help="콤마구분 (미지정 시 dir 전체)")
    p.add_argument("--min-trades", type=int, default=100)
    p.add_argument("--min-days", type=int, default=28)
    p.add_argument("--strict", action="store_true")
    p.add_argument("--output", default=None)
    p.add_argument("--markdown", default=None)
    p.add_argument("--write-latest", action="store_true",
                   help="reports/strategy_validation/real_data_strategy_latest.json 갱신(카드 표시)")
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
        from app.system.real_ohlcv_dataset import (
            evaluate_real_ohlcv_dataset,
            render_markdown,
            to_dict,
        )
        syms = [s.strip() for s in args.symbols.split(",") if s.strip()] if args.symbols else None
        report = evaluate_real_ohlcv_dataset(
            args.input_dir, symbols=syms, min_trades=int(args.min_trades),
            min_days=int(args.min_days), strict=bool(args.strict),
            generated_at=datetime.now(timezone.utc).isoformat())
        data = to_dict(report)
        md = render_markdown(report)

        out = Path(args.output) if args.output else (
            Path(DEFAULT_OUTPUT_DIR) / "real_ohlcv_backtest_walkforward.json")
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"[OK] JSON: {out}")
        if args.markdown:
            mp = Path(args.markdown)
            mp.parent.mkdir(parents=True, exist_ok=True)
            mp.write_text(md, encoding="utf-8")
            print(f"[OK] Markdown: {mp}")
        if args.write_latest:
            latest = Path(DEFAULT_OUTPUT_DIR) / "real_data_strategy_latest.json"
            latest.parent.mkdir(parents=True, exist_ok=True)
            latest.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
            print(f"[OK] latest 갱신: {latest}")

        if not args.quiet:
            print(f"overall_verdict={report.overall_verdict} score={report.overall_score} "
                  f"PASS={len(report.pass_symbols)} BLOCKED={len(report.blocked_symbols)} "
                  f"total_trades={report.total_trades}")
            print(f"agent_value_summary={report.agent_value_summary} "
                  f"real_data_used={report.real_data_used}")
            print("NOTE: 다종목 실데이터 검증 전용 — 자동 적용/실전 전환/주문 0건. 수익 보장 아님.")

        return 1 if report.overall_verdict == "BLOCKED" else 0
    except Exception as e:  # noqa: BLE001
        print(f"[ERROR] {type(e).__name__}: {e}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())

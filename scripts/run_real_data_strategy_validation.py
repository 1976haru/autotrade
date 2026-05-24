#!/usr/bin/env python3
"""REAL-DATA-STRATEGY-01 — 실제/준실제 OHLCV 기반 전략 가능성 검증 CLI (read-only).

sample fixture 가 아니라 실제/준실제 OHLCV(CSV / 디렉토리 / 심볼+yfinance)로 매매기법 +
Agent Council 전략을 backtest / walk-forward / stress 로 검증하고 종합 판정한다.

CLAUDE.md 절대 원칙:
- read-only. broker / OrderExecutor / route_order / KIS 주문 API 호출 0건. 주문 0건.
- KIS historical 시세 API 미구현 → CSV / yfinance(명시 옵션 시) 사용.
- 결과가 좋아도 자동 적용 / 실전 전환 / live authorization 0건. secret/계좌 원문 0건.

exit code:
    0: 평가 완료 (STRONG/CAUTIOUS/RESEARCH_ONLY/NOT_READY)
    1: BLOCKED (데이터 품질/안전 문제)
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
DEFAULT_DEMO_CSV = str(
    _REPO_ROOT / "backend" / "tests" / "fixtures" / "real_data" / "demo_quasi_real.csv")


def _parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    p = argparse.ArgumentParser(
        description="실제/준실제 데이터 기반 전략 가능성 검증 (advisory, 실전 아님).")
    p.add_argument("--input-csv", default=None, help="단일 OHLCV CSV")
    p.add_argument("--input-dir", default=None, help="{symbol}.csv 디렉토리 (다종목)")
    p.add_argument("--symbols", default=None, help="콤마구분 심볼 (real_data 로더 + 옵션 yfinance)")
    p.add_argument("--start", default="2025-01-01")
    p.add_argument("--end", default="2025-12-31")
    p.add_argument("--bar-type", choices=["daily", "minute"], default="daily")
    p.add_argument("--run-backtest", action="store_true", default=True)
    p.add_argument("--no-backtest", dest="run_backtest", action="store_false")
    p.add_argument("--run-walk-forward", action="store_true", default=True)
    p.add_argument("--no-walk-forward", dest="run_walk_forward", action="store_false")
    p.add_argument("--run-stress", action="store_true", default=True)
    p.add_argument("--no-stress", dest="run_stress", action="store_false")
    p.add_argument("--compare-agent", action="store_true", default=True)
    p.add_argument("--min-trades", type=int, default=100)
    p.add_argument("--min-days", type=int, default=28)
    p.add_argument("--allow-yfinance", action="store_true",
                   help="CSV 없을 때 yfinance(준실제) fallback 허용 (네트워크 필요)")
    p.add_argument("--kis-historical", action="store_true",
                   help="(미구현) KIS historical 시세 — 현재 항상 무시")
    p.add_argument("--strict", action="store_true")
    p.add_argument("--output", default=None, help="JSON 리포트 경로")
    p.add_argument("--markdown", default=None, help="Markdown 리포트 경로")
    p.add_argument("--write-latest", action="store_true",
                   help="reports/strategy_validation/real_data_strategy_latest.json 갱신(API용)")
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
        from app.market_data.real_ohlcv_loader import (
            load_for_symbols,
            load_from_csv,
            load_from_dir,
        )
        from app.system.real_data_strategy import (
            evaluate_real_data_strategy,
            render_markdown,
            to_dict,
        )

        if args.kis_historical:
            print("[NOTE] KIS historical 시세 API 는 미구현 — CSV/yfinance 사용.",
                  file=sys.stderr)

        if args.input_csv:
            loaded = load_from_csv(args.input_csv)
        elif args.input_dir:
            syms = args.symbols.split(",") if args.symbols else None
            loaded = load_from_dir(args.input_dir, symbols=syms)
        elif args.symbols:
            start = datetime.fromisoformat(args.start)
            end = datetime.fromisoformat(args.end)
            loaded = load_for_symbols(
                [s.strip() for s in args.symbols.split(",") if s.strip()],
                start=start, end=end, enable_yfinance=bool(args.allow_yfinance))
        else:
            # 입력 미지정 → 기능 시연용 quasi-real 데모 CSV.
            print("[NOTE] 입력 미지정 — quasi-real 데모 CSV 사용 (실제 사용자 데이터 아님).",
                  file=sys.stderr)
            loaded = load_from_csv(DEFAULT_DEMO_CSV)

        if not loaded.bars and loaded.data_source == "NONE":
            print(f"[ERROR] 입력 데이터를 적재하지 못했습니다: "
                  f"{', '.join(loaded.reasons) or '경로 확인'}", file=sys.stderr)
            return 2

        report = evaluate_real_data_strategy(
            loaded,
            run_backtest=bool(args.run_backtest),
            run_wf=bool(args.run_walk_forward),
            run_stress=bool(args.run_stress),
            min_trades=int(args.min_trades),
            min_days=int(args.min_days),
            strict=bool(args.strict),
            generated_at=datetime.now(timezone.utc).isoformat(),
        )
        data = to_dict(report)
        md = render_markdown(report)

        out = Path(args.output) if args.output else (
            Path(DEFAULT_OUTPUT_DIR) / "real_data_strategy.json")
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"[OK] JSON 리포트: {out}")
        if args.markdown:
            mdp = Path(args.markdown)
            mdp.parent.mkdir(parents=True, exist_ok=True)
            mdp.write_text(md, encoding="utf-8")
            print(f"[OK] Markdown 리포트: {mdp}")
        if args.write_latest:
            latest = Path(DEFAULT_OUTPUT_DIR) / "real_data_strategy_latest.json"
            latest.parent.mkdir(parents=True, exist_ok=True)
            latest.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
            print(f"[OK] latest 갱신: {latest}")

        if not args.quiet:
            print(f"overall_verdict={report.overall_verdict} "
                  f"score={report.overall_score} data_source={report.data_source} "
                  f"real_data_used={report.real_data_used}")
            print(f"quality={report.quality.get('status')} bars={report.bars_count} "
                  f"days={report.days_count} trades={report.trades_count} "
                  f"agent={report.agent_value_verdict}")
            print("NOTE: 실제 데이터 전략 검증 전용 — 자동 적용 / 실전 전환 / 주문 0건. 수익 보장 아님.")

        return 1 if report.overall_verdict == "BLOCKED" else 0
    except FileNotFoundError as e:
        print(f"[ERROR] 파일 없음: {e}", file=sys.stderr)
        return 2
    except Exception as e:  # noqa: BLE001
        print(f"[ERROR] {type(e).__name__}: {e}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())

#!/usr/bin/env python3
"""INTRADAY-DATA-02 — 증권사/HTS 분봉 CSV → 표준 OHLCV CSV 정규화 CLI (read-only).

한글 컬럼(일자/시가/고가/저가/종가/거래량) / 쉼표 숫자 / "원" 이 섞인 CSV 를 표준
컬럼(timestamp,open,high,low,close,volume)으로 변환해 data/market/intraday_ohlcv 에 저장한다.
**값을 만들어내지 않으며**(가짜 데이터 0), 파싱 불가 row 는 드롭(보정 아님)하고 카운트한다.

CLAUDE.md 절대 원칙: broker / OrderExecutor / route_order / KIS 주문 API 호출 0건. 주문 0건.

사용:
    python scripts/normalize_intraday_csv.py --input raw.csv \\
        --output data/market/intraday_ohlcv/005930_5m.csv --symbol 005930

exit: 0(valid_rows>0) / 1(필수 컬럼 누락 또는 valid 0) / 2(실행 오류)
"""

from __future__ import annotations

import argparse
import csv
import sys
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parents[1]
_BACKEND_DIR = _REPO_ROOT / "backend"
if str(_BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(_BACKEND_DIR))

_STD_COLS = ["timestamp", "open", "high", "low", "close", "volume"]


def _parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    p = argparse.ArgumentParser(
        description="증권사/HTS 분봉 CSV → 표준 OHLCV 정규화 (실전 아님, 주문 0건).")
    p.add_argument("--input", required=True, help="원본 CSV (한글 컬럼 허용)")
    p.add_argument("--output", required=True, help="표준 CSV 출력 경로")
    p.add_argument("--symbol", default=None, help="symbol 컬럼 없을 때 기본 종목코드")
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
        from app.market_data.intraday_csv_normalizer import normalize_intraday_csv

        records, report = normalize_intraday_csv(args.input, default_symbol=args.symbol)
        if report.missing_required:
            print(f"[FAIL] 필수 컬럼 누락: {', '.join(report.missing_required)}", file=sys.stderr)
            return 1
        if not records:
            print("[FAIL] 유효 row 0건 (모두 파싱 불가).", file=sys.stderr)
            return 1

        out = Path(args.output)
        out.parent.mkdir(parents=True, exist_ok=True)
        opt_cols = [c for c in ("symbol", "vwap", "market_regime", "time_phase", "source")
                    if any(c in r for r in records)]
        cols = _STD_COLS + opt_cols
        with out.open("w", newline="", encoding="utf-8") as f:
            w = csv.DictWriter(f, fieldnames=cols)
            w.writeheader()
            for r in records:
                w.writerow({c: r.get(c, "") for c in cols})

        if not args.quiet:
            print(f"[OK] 표준 CSV: {out}")
            print(f"total={report.total_rows} valid={report.valid_rows} "
                  f"dropped={report.dropped_rows} (보정 아님, 드롭만)")
            print(f"컬럼 매핑: {report.column_mapping}")
            print("NOTE: 분봉 데이터 정규화 전용 — 주문 0건, 실전 승인 아님, 수익 보장 아님.")
        return 0
    except FileNotFoundError as e:
        print(f"[ERROR] 파일 없음: {e}", file=sys.stderr)
        return 2
    except Exception as e:  # noqa: BLE001
        print(f"[ERROR] {type(e).__name__}: {e}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())

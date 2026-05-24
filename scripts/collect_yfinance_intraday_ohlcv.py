#!/usr/bin/env python3
"""REAL-INTRADAY-TEST-01 — yfinance 분봉(intraday) OHLCV 수집 CLI (read-only).

한국 종목(005930 → 005930.KS)의 분봉을 yfinance 로 수집해 표준 CSV 로 저장한다.
**수집 실패를 성공으로 보고하지 않으며, 합성 데이터로 몰래 대체하지 않는다.** 실패는
명확한 reason_code 로 기록한다. broker / 주문 API 호출 0건.

reason_code: YFINANCE_NOT_INSTALLED / NETWORK_ERROR / EMPTY_RESPONSE /
             YFINANCE_INTRADAY_LIMIT / QUALITY_FAILED / OK / PARTIAL_SUCCESS

사용:
    python scripts/collect_yfinance_intraday_ohlcv.py \\
        --symbols 005930,000660,035420 --period 60d --interval 5m \\
        --output-dir data/market/intraday_ohlcv \\
        --json reports/strategy_validation/intraday_collect.json

exit: 0(≥1 종목 OK) / 1(전부 실패) / 2(실행 오류)
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parents[1]
_BACKEND_DIR = _REPO_ROOT / "backend"
if str(_BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(_BACKEND_DIR))

DEFAULT_SYMBOLS = "005930,000660,035420,035720,005380,000270,006400,373220,005490,068270"
DEFAULT_OUTPUT_DIR = "data/market/intraday_ohlcv"


def _yahoo_ticker(symbol: str) -> str:
    """6자리 한국 종목코드 → yfinance ticker (KOSPI 기본 .KS)."""
    s = symbol.strip()
    if "." in s:
        return s
    return f"{s}.KS"


def fetch_one(symbol: str, *, interval: str, period: str) -> tuple[list[dict], str]:
    """한 종목 분봉 수집 → (records, reason_code). 실패 시 records=[]."""
    try:
        import yfinance as yf  # noqa: F401
    except Exception:  # noqa: BLE001
        return [], "YFINANCE_NOT_INSTALLED"
    try:
        import yfinance as yf
        df = yf.Ticker(_yahoo_ticker(symbol)).history(
            period=period, interval=interval, auto_adjust=False)
    except Exception as e:  # noqa: BLE001
        msg = str(e).lower()
        if "period" in msg or "interval" in msg or "range" in msg:
            return [], "YFINANCE_INTRADAY_LIMIT"
        return [], "NETWORK_ERROR"
    if df is None or len(df) == 0:
        return [], "EMPTY_RESPONSE"
    records: list[dict] = []
    for ts, row in df.iterrows():
        try:
            o = float(row["Open"])
            h = float(row["High"])
            lo = float(row["Low"])
            c = float(row["Close"])
            v = float(row.get("Volume", 0) or 0)
        except (KeyError, TypeError, ValueError):
            continue
        if o <= 0 or h <= 0 or lo <= 0 or c <= 0:
            continue
        records.append({
            "symbol": symbol,
            "timestamp": ts.isoformat() if hasattr(ts, "isoformat") else str(ts),
            "open": round(o), "high": round(h), "low": round(lo),
            "close": round(c), "volume": round(v),
        })
    if not records:
        return [], "EMPTY_RESPONSE"
    return records, "OK"


def _write_csv(records: list[dict], path: Path) -> None:
    import csv
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["symbol", "timestamp", "open", "high", "low", "close", "volume"])
        for r in records:
            w.writerow([r["symbol"], r["timestamp"], r["open"], r["high"],
                        r["low"], r["close"], r["volume"]])


def _parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    p = argparse.ArgumentParser(
        description="yfinance 분봉 수집 (read-only, 주문 0건, 합성 대체 0건).")
    p.add_argument("--symbols", default=DEFAULT_SYMBOLS)
    p.add_argument("--period", default="60d", help="5d/30d/60d (5m 은 최대 60d)")
    p.add_argument("--interval", default="5m", choices=["1m", "5m", "15m"])
    p.add_argument("--output-dir", default=DEFAULT_OUTPUT_DIR)
    p.add_argument("--json", dest="json_out", default=None)
    p.add_argument("--markdown", default=None)
    p.add_argument("--allow-partial", action="store_true", default=True)
    p.add_argument("--quiet", action="store_true")
    return p.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    for _stream in (sys.stdout, sys.stderr):
        try:
            _stream.reconfigure(encoding="utf-8", errors="replace")
        except (AttributeError, ValueError):
            pass
    try:
        from app.market_data.intraday_ohlcv import check_intraday_quality

        args = _parse_args(argv)
        symbols = [s.strip() for s in args.symbols.split(",") if s.strip()]
        odir = Path(args.output_dir)
        results = []
        ok_count = 0
        for sym in symbols:
            records, reason = fetch_one(sym, interval=args.interval, period=args.period)
            written = None
            quality = None
            if records:
                # 품질검증 — 표준 OHLCVBar 로 변환 후 분봉 품질 확인.
                from app.backtest.strategy_council_backtest import load_ohlcv_from_records
                bars = load_ohlcv_from_records(records, default_symbol=sym)
                q = check_intraday_quality(bars, min_bars=100, min_days=5)
                quality = q.status
                if q.status == "FAIL":
                    reason = "QUALITY_FAILED"
                else:
                    written = str(odir / f"{sym}_{args.interval}.csv")
                    _write_csv(records, Path(written))
                    ok_count += 1
            results.append({
                "symbol": sym, "reason_code": reason, "rows": len(records),
                "quality": quality, "written_path": written,
            })

        overall = "OK" if ok_count == len(symbols) else (
            "PARTIAL_SUCCESS" if ok_count else "ALL_FAILED")
        data = {
            "interval": args.interval, "period": args.period,
            "ok_count": ok_count, "total": len(symbols), "overall": overall,
            "results": results, "actual_data_used": ok_count > 0,
            "data_source": "yfinance_intraday",
            "is_live_authorization": False, "contains_secret": False,
            "disclaimer": "yfinance 분봉 수집 — 실패는 reason_code 로 기록, 합성 대체 0건. "
                          "자동 적용/실전 전환/주문 0건, 수익 보장 아님.",
        }
        if args.json_out:
            jp = Path(args.json_out)
            jp.parent.mkdir(parents=True, exist_ok=True)
            jp.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
            print(f"[OK] JSON: {jp}")
        if not args.quiet:
            print(f"interval={args.interval} period={args.period} overall={overall} "
                  f"OK={ok_count}/{len(symbols)}")
            for r in results:
                print(f"  {r['symbol']}: {r['reason_code']} rows={r['rows']} q={r['quality']}")
            print("NOTE: 분봉 수집 — 주문 0건, 합성 대체 0건, 실전 승인 아님, 수익 보장 아님.")
        return 0 if ok_count else 1
    except Exception as e:  # noqa: BLE001
        print(f"[ERROR] {type(e).__name__}: {e}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())

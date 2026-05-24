#!/usr/bin/env python3
"""REAL-DATA-INPUT-01 — 실제/준실제 OHLCV 데이터셋 수집 CLI (read-only).

데이터 소스: existing(사용자/clean fixture CSV) 또는 yfinance(준실제, 네트워크 필요).
품질검증을 통과(PASS/WARN)한 CSV 만 output-dir 에 기록한다. **품질 FAIL 데이터는
기록/통과시키지 않으며, 수집 실패를 성공처럼 보이지 않는다. sample/mock 대체 0건.**

CLAUDE.md 절대 원칙:
- read-only. broker / OrderExecutor / route_order / KIS 주문 API 호출 0건. 주문 0건.
- KIS historical 시세 API 미구현 → existing CSV / yfinance 사용.
- secret/계좌 원문 0건. 안전 flag·`.env` 변경 0건.

exit code:
    0: 최소 1개 이상 PASS
    1: 전부 FAIL (또는 --strict 에서 PASS 0)
    2: 실행 오류
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parents[1]
_BACKEND_DIR = _REPO_ROOT / "backend"
if str(_BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(_BACKEND_DIR))

DEFAULT_SYMBOLS = "005930,000660,035420,035720,005380,000270,006400,373220,005490,068270"
DEFAULT_CLEAN_DIR = str(_REPO_ROOT / "backend" / "tests" / "fixtures" / "real_data_clean")
DEFAULT_OUTPUT_DIR = "data/market/real_ohlcv"


def _parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    p = argparse.ArgumentParser(
        description="실제/준실제 OHLCV 데이터셋 수집 + 품질검증 (실전 아님, 주문 0건).")
    p.add_argument("--source", choices=["existing", "yfinance"], default="existing")
    p.add_argument("--symbols", default=DEFAULT_SYMBOLS)
    p.add_argument("--source-dir", default=DEFAULT_CLEAN_DIR,
                   help="existing 모드 입력 CSV 디렉토리 (기본: clean fixture)")
    p.add_argument("--output-dir", default=DEFAULT_OUTPUT_DIR,
                   help="PASS/WARN CSV 기록 위치 (gitignore)")
    p.add_argument("--start", default="2024-01-01")
    p.add_argument("--end", default="today")
    p.add_argument("--min-days", type=int, default=28)
    p.add_argument("--recommended-days", type=int, default=100)
    p.add_argument("--no-write", dest="write", action="store_false", default=True)
    p.add_argument("--strict", action="store_true")
    p.add_argument("--markdown", default=None)
    p.add_argument("--json", dest="json_out", default=None)
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
        from app.market_data.ohlcv_collector import (
            collect_ohlcv,
            manifest_to_dict,
            render_manifest_markdown,
        )

        symbols = [s.strip() for s in args.symbols.split(",") if s.strip()]
        start = datetime.fromisoformat(args.start)
        end = datetime.utcnow() if args.end == "today" else datetime.fromisoformat(args.end)

        if args.source == "yfinance":
            try:
                import yfinance  # noqa: F401
            except Exception:  # noqa: BLE001
                print("[WARN] yfinance 미설치 — 준실제 수집 불가. 네트워크 환경에서 "
                      "`pip install yfinance` 후 재시도하거나 --source existing 사용.",
                      file=sys.stderr)

        manifest = collect_ohlcv(
            symbols, source=args.source, source_dir=args.source_dir,
            output_dir=args.output_dir, start=start, end=end,
            min_days=int(args.min_days), recommended_days=int(args.recommended_days),
            write=bool(args.write))

        data = manifest_to_dict(manifest)
        md = render_manifest_markdown(manifest)
        if args.json_out:
            jp = Path(args.json_out)
            jp.parent.mkdir(parents=True, exist_ok=True)
            jp.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
            print(f"[OK] JSON: {jp}")
        if args.markdown:
            mp = Path(args.markdown)
            mp.parent.mkdir(parents=True, exist_ok=True)
            mp.write_text(md, encoding="utf-8")
            print(f"[OK] Markdown: {mp}")

        if not args.quiet:
            print(f"source={manifest.requested_source} "
                  f"yfinance_available={manifest.yfinance_available}")
            print(f"PASS={len(manifest.pass_symbols)} WARN={len(manifest.warn_symbols)} "
                  f"FAIL={len(manifest.fail_symbols)}")
            print("NOTE: 데이터 수집/검증 전용 — 주문 0건, 실전 승인 아님. 수익 보장 아님.")

        passed = len(manifest.pass_symbols)
        if passed == 0:
            return 1
        if args.strict and (manifest.warn_symbols or manifest.fail_symbols):
            return 1
        return 0
    except Exception as e:  # noqa: BLE001
        print(f"[ERROR] {type(e).__name__}: {e}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())

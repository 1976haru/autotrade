#!/usr/bin/env python3
"""WF-6M Forward Universe Rebuild CLI (read-only, Paper/Backtest only).

CLAUDE.md 절대 원칙: read-only · broker/OrderExecutor/route_order/KIS 주문 API 0건 ·
자동 적용/실전 전환 0건 · EXE 빌드 0건 · look-ahead universe 선택 0건 · secret 0건 ·
수익 보장 문구 0건.

exit code: 0 완료 / 2 오류
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

DEFAULT_INPUT_DIR = "data/market/intraday_ohlcv/kis_6m"
DEFAULT_OUTPUT_DIR = "reports/strategy_validation"


def main(argv: list[str] | None = None) -> int:
    for _s in (sys.stdout, sys.stderr):
        try:
            _s.reconfigure(encoding="utf-8", errors="replace")
        except (AttributeError, ValueError):
            pass
    try:
        p = argparse.ArgumentParser(description="WF-6M forward universe rebuild (실전 아님, 주문/EXE 빌드 0건).")
        p.add_argument("--input-dir", default=DEFAULT_INPUT_DIR)
        p.add_argument("--symbols", default=None)
        p.add_argument("--output-dir", default=DEFAULT_OUTPUT_DIR)
        p.add_argument("--write-latest", action="store_true")
        args = p.parse_args(argv)

        from app.system.forward_universe_validation import run_forward_universe, render_markdown, to_dict
        syms = [s.strip() for s in args.symbols.split(",") if s.strip()] if args.symbols else None
        r = run_forward_universe(args.input_dir, symbols=syms)
        data = to_dict(r)
        out = Path(args.output_dir)
        out.mkdir(parents=True, exist_ok=True)
        (out / "forward_universe_result.json").write_text(
            json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
        (out / "forward_universe_result.md").write_text(render_markdown(r), encoding="utf-8")
        print(f"[OK] JSON: {out / 'forward_universe_result.json'}")
        print(f"[OK] Markdown: {out / 'forward_universe_result.md'}")
        if args.write_latest:
            (out / "forward_universe_latest.json").write_text(
                json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
            print(f"[OK] latest: {out / 'forward_universe_latest.json'}")
        b = r.best_selector
        print(f"final_universe_verdict={r.final_universe_verdict} best={b.get('selector')} "
              f"forward={b.get('forward_return_pct')}% MDD={b.get('forward_mdd_pct')}% "
              f"trades={b.get('total_trades')} stability={b.get('universe_stability')}")
        print(f"EXE 재빌드 권고: {r.exe_rebuild_recommendation}")
        print("NOTE: 연구/백테스트 전용 · look-ahead universe 선택 0건 · 자동 적용/실전 전환/주문/EXE 빌드 0건 · 실전 금지.")
        return 0
    except Exception as e:  # noqa: BLE001
        import traceback
        traceback.print_exc()
        print(f"[ERROR] {type(e).__name__}: {e}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())

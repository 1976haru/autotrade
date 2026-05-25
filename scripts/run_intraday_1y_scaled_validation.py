#!/usr/bin/env python3
"""1년 데이터 10/25/50 확장 검증 CLI (read-only, Paper/Backtest only).

CLAUDE.md 절대 원칙: read-only · broker/OrderExecutor/route_order/KIS 주문 API 0건 ·
자동 적용/실전 전환 0건 · EXE 빌드 0건 · 검증 중 룰 변경 0건(hash) · look-ahead 0건 ·
secret 0건 · 수익 보장 문구 0건.

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

DEFAULT_OUTPUT_DIR = "reports/strategy_validation"


def main(argv: list[str] | None = None) -> int:
    for _s in (sys.stdout, sys.stderr):
        try:
            _s.reconfigure(encoding="utf-8", errors="replace")
        except (AttributeError, ValueError):
            pass
    try:
        p = argparse.ArgumentParser(description="1년 10/25/50 확장 검증 (실전 아님, 주문/EXE 빌드 0건).")
        p.add_argument("--dirs", default="data/market/intraday_ohlcv/kis_6m,data/market/intraday_5m_1y_old",
                       help="병합할 5분봉 디렉토리(콤마)")
        p.add_argument("--output-dir", default=DEFAULT_OUTPUT_DIR)
        p.add_argument("--write-latest", action="store_true")
        args = p.parse_args(argv)

        from app.system.intraday_1y_scaled_validation import (
            render_markdown,
            run_scaled_1y_validation,
            to_dict,
        )
        dirs = [d.strip() for d in args.dirs.split(",") if d.strip()]
        r = run_scaled_1y_validation(dirs=dirs)
        data = to_dict(r)
        out = Path(args.output_dir)
        out.mkdir(parents=True, exist_ok=True)
        (out / "intraday_1y_scaled_validation_result.json").write_text(
            json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
        (out / "intraday_1y_scaled_validation_result.md").write_text(render_markdown(r), encoding="utf-8")
        print(f"[OK] JSON: {out / 'intraday_1y_scaled_validation_result.json'}")
        print(f"[OK] Markdown: {out / 'intraday_1y_scaled_validation_result.md'}")
        if args.write_latest:
            (out / "intraday_1y_scaled_validation_latest.json").write_text(
                json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
            print(f"[OK] latest: {out / 'intraday_1y_scaled_validation_latest.json'}")
        print(f"final_verdict={r.final_verdict} hash_match={r.rule_hash_match} trading_days={r.trading_days}")
        print(f"stage10={r.stage_10.get('forward_return_pct')}% stage25={r.stage_25.get('forward_return_pct')}% "
              f"stage50={r.stage_50.get('forward_return_pct')}%")
        print(f"EXE 재빌드 권고: {r.exe_rebuild_recommendation}")
        print("NOTE: 연구/백테스트 전용 · 룰 변경 0건 · look-ahead 0건 · 자동 적용/실전 전환/주문/EXE 빌드 0건 · 실전 금지.")
        return 0
    except Exception as e:  # noqa: BLE001
        import traceback
        traceback.print_exc()
        print(f"[ERROR] {type(e).__name__}: {e}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())

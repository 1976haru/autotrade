#!/usr/bin/env python3
"""WF-6M Root Cause Analysis CLI (read-only, Paper/Backtest only).

CLAUDE.md 절대 원칙: read-only · broker/OrderExecutor/route_order/KIS 주문 API 0건 ·
자동 적용/실전 전환 0건 · secret/계좌 0건 · 수익 보장 문구 0건.

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
        p = argparse.ArgumentParser(description="WF-6M 손실 원인분해 (실전 아님, 주문 0건).")
        p.add_argument("--input-dir", default=DEFAULT_INPUT_DIR)
        p.add_argument("--symbols", default=None)
        p.add_argument("--output-dir", default=DEFAULT_OUTPUT_DIR)
        p.add_argument("--write-latest", action="store_true")
        args = p.parse_args(argv)

        from app.system.wf_6m_root_cause_analysis import analyze_root_cause, render_markdown, to_dict
        syms = [s.strip() for s in args.symbols.split(",") if s.strip()] if args.symbols else None
        r = analyze_root_cause(args.input_dir, symbols=syms)
        data = to_dict(r)
        out = Path(args.output_dir)
        out.mkdir(parents=True, exist_ok=True)
        (out / "wf_6m_root_cause_analysis.json").write_text(
            json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
        (out / "wf_6m_root_cause_analysis.md").write_text(render_markdown(r), encoding="utf-8")
        print(f"[OK] JSON: {out / 'wf_6m_root_cause_analysis.json'}")
        print(f"[OK] Markdown: {out / 'wf_6m_root_cause_analysis.md'}")
        if args.write_latest:
            (out / "wf_6m_root_cause_latest.json").write_text(
                json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
            print(f"[OK] latest: {out / 'wf_6m_root_cause_latest.json'}")
        b = r.baseline
        print(f"baseline={b['total_return_pct']}% PF={b['profit_factor']} MDD={b['max_drawdown_pct']}%")
        print(f"EXCLUDE제거 개선={r.symbol_group['improvement_pct_points']}%p · "
              f"AGENT_OFF={r.agent_damage['agent_off_return_pct']}% · "
              f"비용0={r.cost_sensitivity['zero_all_cost']['total_return_pct']}%")
        print("NOTE: 연구/백테스트 전용 · 자동 적용/실전 전환/주문 0건 · 수익 보장 아님.")
        return 0
    except Exception as e:  # noqa: BLE001
        import traceback
        traceback.print_exc()
        print(f"[ERROR] {type(e).__name__}: {e}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())

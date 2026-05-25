#!/usr/bin/env python3
"""WF-6M 매매기법 vs Agent 분해 CLI (read-only, Paper/Backtest only).

CLAUDE.md 절대 원칙: read-only · broker/OrderExecutor/route_order/KIS 주문 API 0건 ·
자동 적용/실전 전환 0건 · EXE 빌드 0건 · secret/계좌 0건 · 수익 보장 문구 0건.

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
        p = argparse.ArgumentParser(description="WF-6M 매매기법/Agent 분해 (실전 아님, 주문 0건, EXE 빌드 0건).")
        p.add_argument("--input-dir", default=DEFAULT_INPUT_DIR)
        p.add_argument("--symbols", default=None)
        p.add_argument("--root-cause-json", default=None, help="grade_map 재사용")
        p.add_argument("--output-dir", default=DEFAULT_OUTPUT_DIR)
        p.add_argument("--write-latest", action="store_true")
        args = p.parse_args(argv)

        from app.system.wf_6m_strategy_agent_decomposition import (
            baseline_snapshot,
            build_decomposition,
            render_markdown,
            to_dict,
        )
        syms = [s.strip() for s in args.symbols.split(",") if s.strip()] if args.symbols else None
        grade_map = None
        rc = args.root_cause_json or str(Path(DEFAULT_OUTPUT_DIR) / "wf_6m_root_cause_analysis.json")
        if Path(rc).exists():
            try:
                grade_map = json.loads(Path(rc).read_text(encoding="utf-8")).get("grade_map")
            except Exception:  # noqa: BLE001
                grade_map = None

        r = build_decomposition(args.input_dir, symbols=syms, grade_map=grade_map)
        data = to_dict(r)
        out = Path(args.output_dir)
        out.mkdir(parents=True, exist_ok=True)
        # immutable baseline snapshot (별도 저장).
        snap = baseline_snapshot(r)
        (out / "decomposition_baseline_snapshot.json").write_text(
            json.dumps(snap, ensure_ascii=False, indent=2), encoding="utf-8")
        (out / "decomposition_baseline_snapshot.md").write_text(
            "# WF-6M Baseline Snapshot (immutable)\n\n"
            + "\n".join(f"- {k}: {v}" for k, v in snap.items()) + "\n", encoding="utf-8")
        (out / "strategy_agent_decomposition_result.json").write_text(
            json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
        (out / "strategy_agent_decomposition_result.md").write_text(render_markdown(r), encoding="utf-8")
        print(f"[OK] JSON: {out / 'strategy_agent_decomposition_result.json'}")
        print(f"[OK] Markdown: {out / 'strategy_agent_decomposition_result.md'}")
        print(f"[OK] baseline snapshot: {out / 'decomposition_baseline_snapshot.json'}")
        if args.write_latest:
            (out / "strategy_agent_decomposition_latest.json").write_text(
                json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
            print(f"[OK] latest: {out / 'strategy_agent_decomposition_latest.json'}")
        print(f"final_verdict={r.final_verdict} paper_candidate={r.paper_rehearsal_candidate}")
        print(f"EXE 재빌드 권고: {r.exe_rebuild_recommendation}")
        bt = r.top_by_return[0] if r.top_by_return else None
        if bt:
            print(f"best_by_return={bt['_label']} {bt['total_return_pct']}% PF={bt['profit_factor']} MDD={bt['max_drawdown_pct']}%")
        print("NOTE: 연구/백테스트 전용 · 자동 적용/실전 전환/주문/EXE 빌드 0건 · 수익 보장 아님.")
        return 0
    except Exception as e:  # noqa: BLE001
        import traceback
        traceback.print_exc()
        print(f"[ERROR] {type(e).__name__}: {e}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())

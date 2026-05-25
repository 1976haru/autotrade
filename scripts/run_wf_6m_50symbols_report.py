#!/usr/bin/env python3
"""WF-6M-50SYMBOLS-01 — 6개월·50종목·1000만원 전략 종합 검증 CLI (read-only).

포트폴리오 자금곡선 시뮬 + 종목별 검증 + 전략 분해를 종합해 종목 등급화 + 실전 가능성 +
업그레이드 방향 + 최종 판정을 산출한다.

CLAUDE.md 절대 원칙: read-only · 실주문 0건 · KIS 주문 API 0건 · 자동 적용/실전 전환 0건 ·
secret/계좌 원문 0건 · 수익 보장 문구 0건. Paper/Backtest/Simulation only.

exit code: 0 평가 완료 / 2 실행 오류
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


def _parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    p = argparse.ArgumentParser(description="6개월·50종목·1000만원 전략 종합 검증 (실전 아님, 주문 0건).")
    p.add_argument("--input-dir", default=DEFAULT_INPUT_DIR)
    p.add_argument("--symbols", default=None)
    p.add_argument("--collect-json", default=None)
    p.add_argument("--output-dir", default=DEFAULT_OUTPUT_DIR)
    p.add_argument("--write-latest", action="store_true")
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
        from app.system.wf_6m_50symbols_report import build_wf_6m_report, render_markdown, to_dict
        collect = None
        if args.collect_json and Path(args.collect_json).exists():
            collect = json.loads(Path(args.collect_json).read_text(encoding="utf-8"))
        syms = [s.strip() for s in args.symbols.split(",") if s.strip()] if args.symbols else None

        r = build_wf_6m_report(args.input_dir, symbols=syms, collect_summary=collect)
        data = to_dict(r)
        md = render_markdown(r)
        out = Path(args.output_dir)
        out.mkdir(parents=True, exist_ok=True)
        (out / "wf_6m_50symbols_final_result.json").write_text(
            json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
        (out / "wf_6m_50symbols_final_result.md").write_text(md, encoding="utf-8")
        print(f"[OK] JSON: {out / 'wf_6m_50symbols_final_result.json'}")
        print(f"[OK] Markdown: {out / 'wf_6m_50symbols_final_result.md'}")
        if args.write_latest:
            (out / "wf_6m_50symbols_latest.json").write_text(
                json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
            print(f"[OK] latest 갱신: {out / 'wf_6m_50symbols_latest.json'}")

        if not args.quiet:
            print(f"final_verdict={r.final_verdict} live_possibility={r.live_possibility}")
            print(f"capital: {r.initial_capital:,.0f} -> {r.final_equity:,.0f} "
                  f"({r.total_return_pct}%) trading_days={r.trading_days} enough_history={r.enough_history}")
            print(f"win_rate={r.win_rate} PF={r.profit_factor} MDD={r.max_drawdown_pct}% "
                  f"WF={r.median_walk_forward_score} agent={r.agent_value_summary}")
            print(f"grades={r.grades}")
            print("NOTE: Paper/Backtest only · 실주문 0건 · 자동 적용/실전 전환 0건 · 수익 보장 아님.")
        return 0
    except Exception as e:  # noqa: BLE001
        import traceback
        traceback.print_exc()
        print(f"[ERROR] {type(e).__name__}: {e}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())

#!/usr/bin/env python3
"""KIS-INTRADAY-100-VALIDATION-01 — 실제 KIS 분봉 100종목 전략 최종 판정 CLI (read-only).

수집된 KIS 분봉 디렉토리를 품질검증 → PASS 종목 backtest + walk-forward + stress +
Agent 비교 → 개발자 verdict + 사용자 최종 판단을 산출하고 최종 보고서를 작성한다.

CLAUDE.md 절대 원칙:
- read-only. broker / OrderExecutor / route_order / KIS 주문 API 호출 0건.
- 결과가 좋아도 자동 적용 / 실전 전환 / live authorization 0건.
- secret / 계좌 원문 0건. 수익 보장 문구 0건.

exit code:
    0: 평가 완료
    1: DATA_NOT_RELIABLE (데이터 신뢰 불가)
    2: 실행 오류
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

DEFAULT_INPUT_DIR = "data/market/intraday_ohlcv/kis"
DEFAULT_OUTPUT_DIR = "reports/strategy_validation"


def _parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    p = argparse.ArgumentParser(description="KIS 분봉 100종목 전략 최종 판정 (실전 아님, 주문 0건).")
    p.add_argument("--input-dir", default=DEFAULT_INPUT_DIR)
    p.add_argument("--collect-json", default=None, help="수집 요약 JSON (requested/succeeded/failed)")
    p.add_argument("--symbols", default=None)
    p.add_argument("--no-stress", action="store_true", help="스트레스 테스트 생략")
    p.add_argument("--output-dir", default=DEFAULT_OUTPUT_DIR)
    p.add_argument("--write-latest", action="store_true",
                   help="kis_intraday_100_latest.json 갱신(카드 표시용)")
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
        from app.system.kis_intraday_100_final_result import (
            evaluate_kis_intraday_100,
            render_markdown,
            to_dict,
        )
        collect_summary = None
        if args.collect_json and Path(args.collect_json).exists():
            collect_summary = json.loads(Path(args.collect_json).read_text(encoding="utf-8"))
        syms = [s.strip() for s in args.symbols.split(",") if s.strip()] if args.symbols else None

        result = evaluate_kis_intraday_100(
            args.input_dir, collect_summary=collect_summary, symbols=syms,
            run_stress=not args.no_stress)
        data = to_dict(result)
        md = render_markdown(result)

        out_dir = Path(args.output_dir)
        out_dir.mkdir(parents=True, exist_ok=True)
        jpath = out_dir / "kis_intraday_100_final_result.json"
        mpath = out_dir / "kis_intraday_100_final_result.md"
        jpath.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
        mpath.write_text(md, encoding="utf-8")
        print(f"[OK] JSON: {jpath}")
        print(f"[OK] Markdown: {mpath}")
        if args.write_latest:
            latest = out_dir / "kis_intraday_100_latest.json"
            latest.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
            print(f"[OK] latest 갱신: {latest}")

        if not args.quiet:
            print(f"user_final_judgement={result.user_final_judgement} "
                  f"developer_verdict={result.developer_verdict}")
            print(f"PASS={result.pass_count} total_trades={result.total_trades} "
                  f"median_PF={result.median_profit_factor} WF={result.median_walk_forward_score} "
                  f"agent={result.agent_value_summary} stress_FAIL={result.stress_fail_count}")
            print(f"한 줄 결론: 현재 KIS 실제 분봉 {result.collected_symbols}종목 기준으로, "
                  f"{result.one_line_conclusion}")
            print("NOTE: 자동 적용/실전 전환/주문 0건 · 수익 보장 아님 · KIS 주문 API 호출 0건.")

        return 1 if result.user_final_judgement == "DATA_NOT_RELIABLE" else 0
    except Exception as e:  # noqa: BLE001
        print(f"[ERROR] {type(e).__name__}: {e}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())

"""ACUS 무인(unattended) 파이프라인 CLI — 1회 실행 후 결과 리포트만 확인.

사용 예:
    python scripts/run_acus_pipeline_unattended.py \\
        --input-dir data/market/intraday_ohlcv/kis_6m \\
        --reports-root reports/acus

옵션:
    --resume / --no-resume     마지막 체크포인트부터 재개 (기본 resume)
    --enable-claude            기본은 NEWS_UNKNOWN — 본 옵션 + ANTHROPIC_API_KEY 있을 때만 실 호출
    --news-cost-cap-usd 5.0    Claude 비용 한도 (도달 시 자동 중단, 잔여 NEWS_UNKNOWN)
    --symbols 005930,000660    특정 종목만 평가 (기본: 전체)
    --min-bars 100  --min-days 5  분봉 품질 임계

종료 코드:
    0  STRONG_CANDIDATE_POOL_FOUND  (Paper 리허설 진입 검토 가치 — 운영자 명시 승인 필요)
    1  WEAK / INSUFFICIENT          (추가 데이터/검토 필요)
    2  오류                         (입력/실행 실패)

⚠ 본 스크립트는 실거래 주문을 생성하지 않습니다. broker / OrderExecutor /
   route_order 호출 0건. ENABLE_LIVE_TRADING / ENABLE_AI_EXECUTION / KIS_IS_PAPER
   변경 0건. 결과는 운영자 검토 자료이며 실전 전환 승인이 아닙니다.
"""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

# repo-root import (scripts/ 에서 backend/app/* import)
_REPO = Path(__file__).resolve().parents[1]
_BACKEND = _REPO / "backend"
for p in (str(_BACKEND), str(_REPO)):
    if p not in sys.path:
        sys.path.insert(0, p)


def _parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    p = argparse.ArgumentParser(
        description="ACUS 무인 파이프라인 — Agent Council Universe Selection (research-only).")
    p.add_argument("--input-dir", default="data/market/intraday_ohlcv/kis_6m",
                   help="분봉 CSV 디렉토리 (기본: data/market/intraday_ohlcv/kis_6m)")
    p.add_argument("--reports-root", default="reports/acus",
                   help="결과/체크포인트 루트 (기본: reports/acus)")
    p.add_argument("--symbols", default=None,
                   help="쉼표 구분 종목코드 — 지정 시 해당만 평가 (기본: 디렉토리 전체)")
    p.add_argument("--enable-claude", action="store_true",
                   help="ANTHROPIC_API_KEY 가 있을 때만 NewsAgent 실 호출 활성 (기본: 비활성=UNKNOWN)")
    p.add_argument("--news-cost-cap-usd", type=float, default=5.0,
                   help="Claude API 비용 한도 USD (기본 5.0, 도달 시 자동 중단)")
    p.add_argument("--no-resume", dest="resume", action="store_false",
                   help="기존 체크포인트 무시하고 처음부터 실행")
    p.set_defaults(resume=True)
    p.add_argument("--min-bars", type=int, default=100)
    p.add_argument("--min-days", type=int, default=5)
    p.add_argument("--liquidity-max-spread", type=float, default=0.005,
                   help="Spread proxy 임계 (기본 0.005 = 0.5%%, spec). 0 입력 시 spread 검사 skip (turnover-only 진단 모드).")
    p.add_argument("--liquidity-min-avg-turnover-krw", type=float, default=3_000_000_000,
                   help="일평균 거래대금 임계 KRW (기본 30억).")
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
        from app.acus.deps import PipelineDeps, default_deps
        from app.acus.news_agent import build_claude_analyzer
        from app.acus.pipeline import run_pipeline

        symbols = [s.strip() for s in args.symbols.split(",") if s.strip()] if args.symbols else None
        deps: PipelineDeps = default_deps()
        if args.enable_claude:
            if os.environ.get("ANTHROPIC_API_KEY"):
                deps.analyze_news = build_claude_analyzer()
                if not args.quiet:
                    print("[INFO] NewsAgent: Claude 활성 (ANTHROPIC_API_KEY 감지). "
                          f"비용 한도 ${args.news_cost_cap_usd}.")
            else:
                if not args.quiet:
                    print("[WARN] --enable-claude 지정됐으나 ANTHROPIC_API_KEY 없음 → 전부 NEWS_UNKNOWN.")

        result = run_pipeline(
            input_dir=args.input_dir, symbols=symbols, deps=deps,
            root=args.reports_root, resume=args.resume,
            news_cost_cap_usd=args.news_cost_cap_usd,
            min_bars=args.min_bars, min_days=args.min_days,
            liquidity_max_spread=args.liquidity_max_spread,
            liquidity_min_avg_turnover_krw=args.liquidity_min_avg_turnover_krw,
        )

        fr = result["final_report"]
        verdict = fr.get("verdict", "?")
        n = fr.get("final_robust_count", 0)
        if not args.quiet:
            print(f"[OK] verdict={verdict} final_robust={n}  → {Path(args.reports_root) / 'acus_final_report.md'}")
            if result.get("errors"):
                print(f"[WARN] errors during run: {len(result['errors'])}")

        # 안전 invariant 재확인
        for fk in ("is_order_signal", "auto_apply_allowed", "applied_to_runtime",
                   "is_live_authorization", "contains_secret"):
            if fr.get(fk, False):
                print(f"[FATAL] safety invariant violated: {fk}=True", file=sys.stderr)
                return 2

        if verdict == "STRONG_CANDIDATE_POOL_FOUND":
            return 0
        return 1
    except KeyboardInterrupt:
        print("[INFO] interrupted by user", file=sys.stderr)
        return 2
    except Exception as exc:  # noqa: BLE001
        print(f"[ERROR] {type(exc).__name__}: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())

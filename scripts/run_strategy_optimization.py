#!/usr/bin/env python3
"""#46 / 6-01 — 4전략 + Agent Council 백테스트 CLI (Paper 성능 분석 전용).

ORB / Momentum / Gap / VWAP 4 전략 vote 와 Agent Council 의 final_action 을 과거
OHLCV(CSV) 로 평가해, 전략별 / Council 별 성과지표를 JSON / Markdown 리포트로
저장한다.

**본 스크립트는 *성능 검증용 백테스트* 다 — 실제 주문을 생성/전송하지 않으며,
백테스트 결과만으로 실전 전환을 허가하지 않는다.**

CLAUDE.md 절대 원칙:
- read-only. broker / 단일 주문 라우터 / OrderExecutor / KIS API / AI SDK /
  외부 HTTP 어떤 것도 호출하지 *않는다*.
- `ENABLE_LIVE_TRADING` / `ENABLE_AI_EXECUTION` / `ENABLE_FUTURES_LIVE_TRADING` /
  `KIS_IS_PAPER` 환경 변수 수정 0건, `.env` 작성/갱신 0건.
- secret / API key / 계좌번호 출력 0건, "수익 보장" / "실전 전환 승인" 문구 0건.

사용:
    python scripts/run_strategy_optimization.py \\
        --input data/backtest/sample_ohlcv.csv \\
        --output reports/backtest/latest.json
    python scripts/run_strategy_optimization.py \\
        --input data/backtest/sample_ohlcv.csv \\
        --markdown reports/backtest/latest.md

exit code:
    0: 정상
    1: 데이터 부족 / 검증 실패
    2: 입력 파일 오류 (없음 / 파싱 실패)
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parents[1]
_BACKEND_DIR = _REPO_ROOT / "backend"
if str(_BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(_BACKEND_DIR))

from app.backtest.strategy_council_backtest import (  # noqa: E402
    BACKTEST_INSUFFICIENT_DATA,
    BacktestInput,
    DEFAULT_HORIZON_BARS,
    load_ohlcv_from_csv,
    render_markdown_report,
    run_strategy_council_backtest,
    summarize_backtest_report,
)

DEFAULT_OUTPUT_DIR = "reports/backtest"


def _parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    p = argparse.ArgumentParser(
        description="4전략(ORB/Momentum/Gap/VWAP) + Agent Council 백테스트 "
                    "(Paper 성능 분석 전용 — 실전 전환 아님).",
    )
    p.add_argument("--input", required=True,
                   help="OHLCV CSV 경로 (헤더: timestamp,open,high,low,close,volume[,vwap,...])")
    p.add_argument("--output", default=None,
                   help="JSON 리포트 경로. 미지정 시 reports/backtest/ 에 timestamp 파일.")
    p.add_argument("--markdown", default=None,
                   help="Markdown 리포트 경로. 미지정 시 JSON 옆에 .md 생성.")
    p.add_argument("--risk-profile", default="BALANCED",
                   choices=["CONSERVATIVE", "BALANCED", "AGGRESSIVE"])
    p.add_argument("--primary-horizon", default="close",
                   help="headline 지표 horizon (예: close / h30).")
    p.add_argument("--quantity", type=int, default=1, help="신호당 수량 (성과 환산용).")
    p.add_argument("--horizons", default=",".join(str(h) for h in DEFAULT_HORIZON_BARS),
                   help="forward return horizon (bar offset) 콤마 목록. 예: 5,10,30,60")
    p.add_argument("--quiet", action="store_true", help="summary 표준출력 생략.")
    return p.parse_args(argv)


def _timestamp_name(prefix: str, ext: str) -> str:
    return f"{prefix}_{datetime.now(timezone.utc):%Y%m%d_%H%M%S}.{ext}"


def main(argv: list[str] | None = None) -> int:
    # Windows 콘솔(cp949)에서 한글/기호 출력 시 UnicodeEncodeError 방지.
    for _stream in (sys.stdout, sys.stderr):
        try:
            _stream.reconfigure(encoding="utf-8", errors="replace")
        except (AttributeError, ValueError):
            pass
    args = _parse_args(argv)

    in_path = Path(args.input)
    if not in_path.exists() or not in_path.is_file():
        print(f"[ERROR] 입력 파일을 찾을 수 없습니다: {in_path}", file=sys.stderr)
        return 2
    try:
        bars = load_ohlcv_from_csv(str(in_path))
    except Exception as exc:  # noqa: BLE001 — 파싱 오류는 exit 2.
        print(f"[ERROR] 입력 파일 파싱 실패: {type(exc).__name__}: {exc}", file=sys.stderr)
        return 2
    if not bars:
        print("[ERROR] 입력 파일에서 유효한 OHLCV bar 를 읽지 못했습니다.", file=sys.stderr)
        return 2

    try:
        horizons = tuple(int(x) for x in str(args.horizons).split(",") if x.strip())
    except ValueError:
        print("[ERROR] --horizons 는 콤마로 구분된 정수여야 합니다. 예: 5,10,30,60", file=sys.stderr)
        return 2

    report = run_strategy_council_backtest(BacktestInput(
        bars=tuple(bars), risk_profile=args.risk_profile, horizons=horizons,
        quantity=max(1, args.quantity), primary_horizon=args.primary_horizon,
    ))

    # 출력 경로 결정.
    if args.output:
        json_path = Path(args.output)
    else:
        json_path = Path(DEFAULT_OUTPUT_DIR) / _timestamp_name("strategy_backtest", "json")
    md_path = Path(args.markdown) if args.markdown else json_path.with_suffix(".md")

    json_path.parent.mkdir(parents=True, exist_ok=True)
    md_path.parent.mkdir(parents=True, exist_ok=True)

    payload = summarize_backtest_report(report)
    json_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    md_path.write_text(render_markdown_report(report), encoding="utf-8")

    if not args.quiet:
        print(f"[OK] JSON 리포트: {json_path}")
        print(f"[OK] Markdown 리포트: {md_path}")
        print(f"reason_code={report.reason_code} bars={report.bar_count} "
              f"risk_profile={report.risk_profile}")
        if report.council:
            comp = report.comparison
            print(f"Council expectancy={comp.get('council_expectancy')} vs "
                  f"best_single={comp.get('best_single_strategy')} "
                  f"({comp.get('best_single_expectancy')}) — "
                  f"council_better={comp.get('council_better_than_best_single')}")
        print("NOTE: Paper 성능 분석 전용 — 실전 전환 아님.")

    if report.insufficient_data or report.reason_code == BACKTEST_INSUFFICIENT_DATA:
        print(f"[WARN] 데이터 부족: {report.reason_code}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

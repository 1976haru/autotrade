#!/usr/bin/env python3
"""#47 / 6-02 — 4전략 + Agent Council Walk-forward 과최적화 방지 검증 CLI.

#46 의 4전략(ORB/Momentum/Gap/VWAP) + Agent Council 백테스트가 *특정 기간에만 맞는
착시(overfit)* 인지 검증한다. 과거 OHLCV(CSV)를 시간 순서로 train/validation/test 로
분리하고 walk-forward(rolling) 로 반복 검증해, 검증 구간에서도 성과가 유지되는지 본다.

> 참고: 본 스크립트는 #46 council 백테스트(`strategy_council_backtest`) 전용 walk-forward
> 검증이다. 6 전략 registry 파라미터 walk-forward 는 별도 `run_walk_forward_validation.py`
> (3-04) 를 사용한다 — 두 스크립트는 목적/입력이 다르다.

**본 스크립트는 과최적화 방지용 *검증* 자료다 — 실제 주문을 생성/전송하지 않으며,
walk-forward 결과만으로 실전 전환을 허가하지 않는다.**

CLAUDE.md 절대 원칙:
- read-only. broker / 단일 주문 라우터 / OrderExecutor / KIS API / AI SDK /
  외부 HTTP 호출 0건.
- `ENABLE_LIVE_TRADING` / `ENABLE_AI_EXECUTION` / `ENABLE_FUTURES_LIVE_TRADING` /
  `KIS_IS_PAPER` 환경 변수 수정 0건, `.env` 작성/갱신 0건.
- secret / API key / 계좌번호 출력 0건, "수익 보장" / "실전 전환 승인" 문구 0건.

사용:
    python scripts/run_strategy_council_walk_forward.py \\
        --input backend/tests/fixtures/backtest/sample_ohlcv.csv \\
        --output reports/backtest/walk_forward.json
    python scripts/run_strategy_council_walk_forward.py \\
        --input data/backtest/sample_ohlcv.csv --mode ROLLING \\
        --train-window-days 3 --validation-window-days 1 --test-window-days 1 \\
        --step-days 1 --markdown reports/backtest/walk_forward.md

exit code:
    0: 정상
    1: 데이터 부족 / split 불가
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
    DEFAULT_HORIZON_BARS,
    load_ohlcv_from_csv,
)
from app.backtest.walk_forward_validation import (  # noqa: E402
    WALK_FORWARD_INSUFFICIENT_DATA,
    WalkForwardInput,
    WalkForwardMode,
    render_markdown_report,
    run_walk_forward_validation,
    summarize_walk_forward_report,
)

DEFAULT_OUTPUT_DIR = "reports/backtest"


def _parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    p = argparse.ArgumentParser(
        description="4전략 + Agent Council Walk-forward 과최적화 방지 검증 — "
                    "Paper 분석 전용, 실전 전환 아님.",
    )
    p.add_argument("--input", required=True, help="OHLCV CSV 경로.")
    p.add_argument("--output", default=None, help="JSON 리포트 경로 (미지정 시 reports/backtest/).")
    p.add_argument("--markdown", default=None, help="Markdown 리포트 경로 (미지정 시 JSON 옆 .md).")
    p.add_argument("--mode", default=WalkForwardMode.THREE_WAY.value,
                   choices=[WalkForwardMode.THREE_WAY.value, WalkForwardMode.ROLLING.value])
    p.add_argument("--train-pct", type=float, default=0.6)
    p.add_argument("--validation-pct", type=float, default=0.2)
    p.add_argument("--train-window-days", type=int, default=3)
    p.add_argument("--validation-window-days", type=int, default=1)
    p.add_argument("--test-window-days", type=int, default=1)
    p.add_argument("--step-days", type=int, default=1)
    p.add_argument("--risk-profile", default="BALANCED",
                   choices=["CONSERVATIVE", "BALANCED", "AGGRESSIVE"])
    p.add_argument("--primary-horizon", default="close")
    p.add_argument("--quantity", type=int, default=1)
    p.add_argument("--horizons", default=",".join(str(h) for h in DEFAULT_HORIZON_BARS))
    p.add_argument("--quiet", action="store_true")
    return p.parse_args(argv)


def _timestamp_name(prefix: str, ext: str) -> str:
    return f"{prefix}_{datetime.now(timezone.utc):%Y%m%d_%H%M%S}.{ext}"


def main(argv: list[str] | None = None) -> int:
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
    except Exception as exc:  # noqa: BLE001
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

    report = run_walk_forward_validation(WalkForwardInput(
        bars=tuple(bars), mode=args.mode, train_pct=args.train_pct,
        validation_pct=args.validation_pct, train_window_days=args.train_window_days,
        validation_window_days=args.validation_window_days,
        test_window_days=args.test_window_days, step_days=args.step_days,
        risk_profile=args.risk_profile, horizons=horizons,
        quantity=max(1, args.quantity), primary_horizon=args.primary_horizon,
    ))

    if args.output:
        json_path = Path(args.output)
    else:
        json_path = Path(DEFAULT_OUTPUT_DIR) / _timestamp_name("walk_forward_report", "json")
    md_path = Path(args.markdown) if args.markdown else json_path.with_suffix(".md")

    json_path.parent.mkdir(parents=True, exist_ok=True)
    md_path.parent.mkdir(parents=True, exist_ok=True)

    json_path.write_text(
        json.dumps(summarize_walk_forward_report(report), ensure_ascii=False, indent=2),
        encoding="utf-8")
    md_path.write_text(render_markdown_report(report), encoding="utf-8")

    if not args.quiet:
        print(f"[OK] JSON 리포트: {json_path}")
        print(f"[OK] Markdown 리포트: {md_path}")
        print(f"mode={report.mode} splits={report.split_count} "
              f"reason_code={report.reason_code}")
        print(f"stability_score={report.overall_stability_score} "
              f"overfit_suspected={report.overall_overfit_suspected} "
              f"collapse_segments={len(report.collapse_segments)}")
        print("NOTE: Paper 과최적화 방지 검증 전용 — 실전 전환 아님.")

    if report.insufficient_data or report.reason_code == WALK_FORWARD_INSUFFICIENT_DATA:
        print(f"[WARN] 데이터 부족 / split 불가: {report.reason_code}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

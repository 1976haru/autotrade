#!/usr/bin/env python3
"""B1 — OHLCV 데이터 품질 검증 CLI (read-only).

시간축 스윕 입력(5m / 30m / 60m / 1d) 디렉토리의 종목 CSV 들을 검증한다:
결측 <1%, OHLC 정합성, 거래량 0 비율 <5%, 봉 간 20%+ 단봉 점프(권리락/분할 의심)
탐지. FAIL 종목은 백테스트에서 *제외 후보* 로 표시한다 (자동 보정/조작 0건).

`app.market_data.dataset_validation` (= `ohlcv_quality` 재사용) 위임. broker /
주문 API / 외부 HTTP import 0건.

산출: reports/backtest/data_quality_report.json (gitignore — PC 에서 확인).

DoD B1: 통과(FAIL 아님) 종목 ≥ 80%.

exit code:
    0: 모든 검증 디렉토리가 80% 통과 게이트 충족
    1: 하나 이상 디렉토리가 80% 미달
    2: 입력 디렉토리 없음 / 오류
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

from app.market_data.dataset_validation import validate_dir  # noqa: E402

# (라벨, 디렉토리, glob, min_bars, min_days)
DEFAULT_TARGETS = [
    ("5m",  "data/market/robust_intraday_5m",      "*_5m.csv",  500, 20),
    ("30m", "data/market/intraday_30m_resampled",  "*_30m.csv", 200, 20),
    ("60m", "data/market/intraday_60m_resampled",  "*_60m.csv", 100, 20),
    ("1d",  "data/market/intraday_1d_resampled",   "*.csv",     100, 20),
]


def _parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    p = argparse.ArgumentParser(
        description="OHLCV 데이터 품질 검증 — read-only, 실주문 없음.",
    )
    p.add_argument("--dir", action="append", default=None,
                   help="검증 디렉토리 (label:path:glob 형식, 반복 가능). 미지정 시 기본 4 시간축.")
    p.add_argument("--output", default="reports/backtest/data_quality_report.json")
    p.add_argument("--quiet", action="store_true")
    return p.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    for _stream in (sys.stdout, sys.stderr):
        try:
            _stream.reconfigure(encoding="utf-8", errors="replace")
        except (AttributeError, ValueError):
            pass

    args = _parse_args(argv)

    if args.dir:
        targets = []
        for spec in args.dir:
            parts = spec.split(":")
            if len(parts) < 2:
                print(f"[ERROR] --dir 형식 오류: {spec} (label:path[:glob])", file=sys.stderr)
                return 2
            label, path = parts[0], parts[1]
            glob_pat = parts[2] if len(parts) > 2 else "*.csv"
            targets.append((label, path, glob_pat, 100, 20))
    else:
        targets = DEFAULT_TARGETS

    report: dict = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "note": "OHLCV 품질 검증 — read-only. FAIL 종목은 백테스트 제외 후보 (자동 보정 0건).",
        "pass_gate_pct": 0.80,
        "dirs": {},
        "is_order_signal": False,
        "is_live_authorization": False,
        "contains_secret": False,
    }

    all_pass = True
    any_dir = False
    for label, path, glob_pat, min_bars, min_days in targets:
        abs_path = (_REPO_ROOT / path) if not Path(path).is_absolute() else Path(path)
        if not abs_path.exists():
            report["dirs"][label] = {"dir": str(path), "error": "디렉토리 없음", "symbol_count": 0}
            if not args.quiet:
                print(f"[WARN] {label}: 디렉토리 없음 ({path})", file=sys.stderr)
            continue
        any_dir = True
        res = validate_dir(str(abs_path), glob_pat=glob_pat, min_bars=min_bars, min_days=min_days)
        report["dirs"][label] = res
        gate = res["meets_80pct_gate"]
        all_pass = all_pass and gate
        if not args.quiet:
            print(f"[{label}] 종목 {res['symbol_count']} · OK {res['ok_count']} · "
                  f"WARN {res['warn_count']} · FAIL {res['fail_count']} · "
                  f"통과율 {res['pass_rate']} · 80%게이트 {'PASS' if gate else 'FAIL'}")
            if res["fail_symbols"]:
                print(f"        제외 후보(FAIL): {', '.join(res['fail_symbols'])}")

    out_path = (_REPO_ROOT / args.output) if not Path(args.output).is_absolute() else Path(args.output)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    if not args.quiet:
        print(f"[OK] data_quality_report.json → {out_path}")
        print("NOTE: read-only 데이터 검증 — 실주문/실전 전환 아님.")

    if not any_dir:
        print("[ERROR] 검증 가능한 디렉토리가 없습니다.", file=sys.stderr)
        return 2
    return 0 if all_pass else 1


if __name__ == "__main__":
    raise SystemExit(main())

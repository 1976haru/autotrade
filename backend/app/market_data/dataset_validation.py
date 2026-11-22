"""OHLCV 데이터셋 품질 검증 (read-only) — timeframe 스윕 전 입력 게이트.

`app.market_data.ohlcv_quality.check_ohlcv_quality` 를 *재사용* 하고, 단타 백테스트
입력에 필요한 추가 점검을 얹는다:

- 결측(파싱 실패 row) 비율
- OHLC 정합성 (위임)
- 거래량 0 비율
- 봉 간 20% 이상 단봉 점프 (권리락/분할 의심 — 탐지만, 자동 보정 0건)

broker / 주문 API / 외부 HTTP import 0건 — 순수 데이터 검증. 본 모듈은 데이터를
*수정하지 않으며*, FAIL 종목을 백테스트에서 제외할 판단 근거만 제공한다.
"""

from __future__ import annotations

import glob
import os
from typing import Any

from app.backtest.strategy_council_backtest import OHLCVBar, load_ohlcv_from_csv
from app.market_data.ohlcv_quality import OK, WARN, FAIL, check_ohlcv_quality, to_dict

# 단봉 점프 임계: 봉 간 종가 변화율 절댓값.
JUMP_WARN = 0.20      # 20%+ → 권리락/분할 의심 (WARN, 탐지만)
JUMP_FAIL = 0.50      # 50%+ → 데이터 오류 의심 (FAIL 후보)


def _raw_line_count(path: str) -> int:
    """CSV 데이터 row 수 (헤더 제외) — 결측 비율 계산용."""
    try:
        with open(path, encoding="utf-8-sig", newline="") as f:
            return max(0, sum(1 for _ in f) - 1)
    except OSError:
        return 0


def max_bar_jump_pct(bars: list[OHLCVBar]) -> float:
    """봉 간 종가 변화율 절댓값의 최댓값. bar < 2 또는 0 가격이면 0.0."""
    worst = 0.0
    prev: float | None = None
    for b in bars:
        c = float(b.close)
        if prev is not None and prev > 0:
            chg = abs(c - prev) / prev
            if chg > worst:
                worst = chg
        prev = c
    return worst


def zero_volume_ratio(bars: list[OHLCVBar]) -> float:
    if not bars:
        return 0.0
    z = sum(1 for b in bars if float(b.volume) <= 0)
    return z / len(bars)


def validate_symbol_csv(
    path: str, *, min_bars: int = 100, min_days: int = 20,
) -> dict[str, Any]:
    """단일 종목 CSV 품질 평가 → status(OK/WARN/FAIL) + 지표 dict."""
    # 파일명 stem 에서 시간축 접미사를 떼어 fallback symbol 로 사용.
    stem = os.path.splitext(os.path.basename(path))[0]
    symbol = stem.replace("_5m", "").replace("_30m", "").replace("_60m", "").replace("_1d", "")
    raw_rows = _raw_line_count(path)
    try:
        bars = load_ohlcv_from_csv(path)
    except Exception as exc:  # noqa: BLE001
        return {
            "symbol": symbol, "path": path, "status": FAIL,
            "reasons": [f"파싱 실패: {type(exc).__name__}: {exc}"],
            "bar_count": 0, "raw_rows": raw_rows, "missing_ratio": 1.0,
            "zero_volume_ratio": None, "max_bar_jump_pct": None,
            "passes": False,
        }

    # 데이터에 symbol 컬럼이 있으면 그 값을 권위 소스로 사용 (시간축 간 교집합 정합).
    if bars and getattr(bars[0], "symbol", None):
        symbol = bars[0].symbol
    q = check_ohlcv_quality(bars, min_bars=min_bars, min_days=min_days)
    parsed = len(bars)
    missing_ratio = (raw_rows - parsed) / raw_rows if raw_rows > 0 else 0.0
    zvr = zero_volume_ratio(bars)
    jump = max_bar_jump_pct(bars)

    status = q.status
    reasons = list(q.reasons)
    if missing_ratio >= 0.01:
        reasons.append(f"결측(파싱불가) {missing_ratio*100:.2f}% ≥ 1%")
        if status == OK:
            status = WARN
    if zvr >= 0.05:
        reasons.append(f"거래량 0 비율 {zvr*100:.2f}% ≥ 5%")
        if status == OK:
            status = WARN
    if jump >= JUMP_FAIL:
        reasons.append(f"단봉 점프 {jump*100:.1f}% ≥ {JUMP_FAIL*100:.0f}% (데이터 오류 의심)")
        status = FAIL
    elif jump >= JUMP_WARN:
        reasons.append(f"단봉 점프 {jump*100:.1f}% ≥ {JUMP_WARN*100:.0f}% (권리락/분할 의심 — 탐지만)")
        if status == OK:
            status = WARN

    return {
        "symbol": symbol, "path": path, "status": status,
        "reasons": reasons,
        "bar_count": parsed, "raw_rows": raw_rows,
        "day_count": q.day_count,
        "missing_ratio": round(missing_ratio, 6),
        "zero_volume_ratio": round(zvr, 6),
        "max_bar_jump_pct": round(jump, 6),
        "quality": to_dict(q),
        # 통과 = FAIL 아님 (WARN 은 표본/점프 경고이나 백테스트 가능).
        "passes": status != FAIL,
    }


def validate_dir(
    dir_path: str, *, glob_pat: str = "*.csv",
    min_bars: int = 100, min_days: int = 20,
) -> dict[str, Any]:
    """디렉토리 내 모든 종목 CSV 검증 → 종목별 결과 + 통과율 집계."""
    files = sorted(glob.glob(os.path.join(dir_path, glob_pat)))
    per_symbol: list[dict[str, Any]] = []
    for f in files:
        per_symbol.append(validate_symbol_csv(f, min_bars=min_bars, min_days=min_days))

    total = len(per_symbol)
    fails = [r for r in per_symbol if r["status"] == FAIL]
    warns = [r for r in per_symbol if r["status"] == WARN]
    oks = [r for r in per_symbol if r["status"] == OK]
    passed = total - len(fails)
    return {
        "dir": dir_path,
        "glob": glob_pat,
        "symbol_count": total,
        "ok_count": len(oks),
        "warn_count": len(warns),
        "fail_count": len(fails),
        "passed_count": passed,
        "pass_rate": round(passed / total, 6) if total else None,
        "meets_80pct_gate": (passed / total >= 0.80) if total else False,
        "fail_symbols": sorted(r["symbol"] for r in fails),
        "passing_symbols": sorted(r["symbol"] for r in per_symbol if r["passes"]),
        "per_symbol": per_symbol,
    }


__all__ = [
    "JUMP_WARN", "JUMP_FAIL",
    "max_bar_jump_pct", "zero_volume_ratio",
    "validate_symbol_csv", "validate_dir",
]

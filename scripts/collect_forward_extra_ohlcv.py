#!/usr/bin/env python3
"""KIS-INTRADAY-60D-WEEKLY-NEW-DATA — 추가 기간 5분봉 수집 (read-only, resume).

기존 6개월(data/market/intraday_ohlcv/kis) 을 *보존* 한 채, 추가 기간 데이터를 *별도 경로*
(data/market/intraday_5m_forward_extra)에 수집한다. 기존 read-only collector
(`collect_kis_intraday_ohlcv`)를 그대로 재사용 — resume / progress·failed json / .tmp→replace
원자적 저장 / EGW00201 backoff 포함. **KIS 주문 API 호출 0건 (시세 조회만).**

수집 후 데이터 품질 + 기존 6개월과의 날짜 overlap(중복) 을 계산해 *진짜 새 거래일* 수를 보고한다.

CLAUDE.md 절대 원칙: read-only · broker/OrderExecutor/route_order/KIS 주문 API 0건 ·
안전 flag 변경 0 · 기존 6개월 CSV 삭제 0 · secret 원문 출력 0.

exit: 0 수집(부분 포함) / 1 전 종목 실패 / 2 오류
"""

from __future__ import annotations

import csv
import json
import sys
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parents[1]
if str(_REPO_ROOT / "scripts") not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT / "scripts"))

EXISTING_DIR = _REPO_ROOT / "data" / "market" / "intraday_ohlcv" / "kis"
EXTRA_DIR = _REPO_ROOT / "data" / "market" / "intraday_5m_forward_extra"
RV = _REPO_ROOT / "reports" / "strategy_validation"
PROGRESS = RV / "locked_60d_weekly_new_data_progress.json"
FAILED = RV / "locked_60d_weekly_new_data_failed_symbols.json"
QUALITY = RV / "locked_60d_weekly_new_data_quality.json"
QUALITY_MD = RV / "locked_60d_weekly_new_data_quality.md"


def _csv_dates(path: Path) -> set[str]:
    out: set[str] = set()
    try:
        with path.open(encoding="utf-8") as f:
            for row in csv.DictReader(f):
                ts = row.get("timestamp") or ""
                if len(ts) >= 10:
                    out.add(ts[:10])
    except OSError:
        pass
    return out


def _existing_dates() -> set[str]:
    out: set[str] = set()
    if EXISTING_DIR.is_dir():
        for f in EXISTING_DIR.glob("*.csv"):
            out |= _csv_dates(f)
    return out


def _quality_report() -> dict:
    existing = _existing_dates()
    per_symbol = []
    all_new: set[str] = set()
    all_extra: set[str] = set()
    files = sorted(EXTRA_DIR.glob("*.csv")) if EXTRA_DIR.is_dir() else []
    for f in files:
        ds = _csv_dates(f)
        new = ds - existing
        all_extra |= ds
        all_new |= new
        nbars = max(0, sum(1 for _ in f.open(encoding="utf-8")) - 1)
        per_symbol.append({"symbol": f.stem.split("_")[0], "trading_days": len(ds),
                           "bars": nbars, "new_trading_days": len(new),
                           "date_range": [min(ds) if ds else None, max(ds) if ds else None]})
    new_days = sorted(all_new)
    status = "PASS" if len(new_days) >= 20 else ("WARN" if new_days else "FAIL")
    return {
        "existing_6m_distinct_days": len(existing),
        "extra_distinct_days": len(all_extra),
        "new_trading_days_count": len(new_days),
        "new_trading_days": new_days[:60],
        "duplicate_removed_count": len(all_extra & existing),
        "newest_existing": max(existing) if existing else None,
        "newest_extra": max(all_extra) if all_extra else None,
        "symbol_count": len(files), "per_symbol": per_symbol,
        "quality_status": status,
        "data_quality_warnings": ([] if len(new_days) >= 20 else
                                  [f"새 거래일 {len(new_days)}일(<20) — 추가 forward 기간 부족"]),
        "note": ("'new_trading_days' = 추가 수집 데이터 중 기존 6개월에 없던 거래일. "
                 "KIS 시세 최신일이 기존 데이터에 이미 포함되면 0 에 가깝다."),
        # 안전 불변값.
        "is_live_authorization": False, "broker_order_sent": False, "contains_secret": False,
    }


def main(argv: list[str] | None = None) -> int:
    for _s in (sys.stdout, sys.stderr):
        try:
            _s.reconfigure(encoding="utf-8", errors="replace")
        except (AttributeError, ValueError):
            pass
    try:
        import collect_kis_intraday_ohlcv as collector
    except Exception as e:  # noqa: BLE001
        print(f"[ERROR] collector import: {e}", file=sys.stderr)
        return 2
    EXTRA_DIR.mkdir(parents=True, exist_ok=True)
    RV.mkdir(parents=True, exist_ok=True)
    # 기존 read-only collector 재사용 (resume + progress/failed). 인자 그대로 전달, 기본값 보강.
    args = list(argv) if argv is not None else sys.argv[1:]
    if not any(a in ("--from-universe", "--symbols", "--symbols-file") for a in args):
        args += ["--from-universe"]
    defaults = {
        "--output-dir": str(EXTRA_DIR), "--num-days": "25", "--bar-size": "5m",
        "--sleep-seconds": "0.45", "--progress-json": str(PROGRESS),
        "--failed-json": str(FAILED),
        "--json": str(RV / "locked_60d_weekly_new_data_collect.json"),
    }
    for k, v in defaults.items():
        if k not in args:
            args += [k, v]
    if "--resume" not in args:
        args += ["--resume"]
    rc = collector.main(args)

    q = _quality_report()
    QUALITY.write_text(json.dumps(q, ensure_ascii=False, indent=2), encoding="utf-8")
    QUALITY_MD.write_text(
        "# 추가 기간 데이터 품질\n\n"
        + f"- 품질: **{q['quality_status']}**\n"
        + f"- 새 거래일(기존 미포함): **{q['new_trading_days_count']}**\n"
        + f"- 중복 제거: {q['duplicate_removed_count']}\n"
        + f"- 기존 최신일 {q['newest_existing']} / 추가 최신일 {q['newest_extra']}\n"
        + f"- 경고: {q['data_quality_warnings']}\n\n> {q['note']}\n", encoding="utf-8")
    print(f"[OK] quality: {QUALITY} (status={q['quality_status']}, "
          f"new_days={q['new_trading_days_count']}, dup_removed={q['duplicate_removed_count']})")
    print("NOTE: read-only 시세 수집 · 주문 API 0건 · 기존 6개월 보존.")
    return rc


if __name__ == "__main__":
    raise SystemExit(main())

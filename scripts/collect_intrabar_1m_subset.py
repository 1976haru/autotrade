#!/usr/bin/env python3
"""실제 1분봉 subset 수집 (CHECKLIST-04 P2) — read-only, 주문 API 0건.

대표 10종목의 *raw 1분봉*을 data/market/robust_intraday_1m_subset/ 에 저장한다.
KIS read-only 시세(`inquire_time_dailychartprice`)만 사용 — 주문/체결 API 0건.
resume(완료 skip) · 실패 기록 · .tmp→replace 원자 저장 · rate-limit backoff ·
progress/summary json. 장중(09:00~15:30 KST)에 실행.

  python scripts/collect_intrabar_1m_subset.py [--days 60] [--symbols ...]

기존 5분봉 수집기(collect_kis_intraday_ohlcv.py)와 별개 — 이쪽은 *1분봉 원본* 보존.
broker.place_order / route_order / OrderExecutor / 주문 TR 호출 0건.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

_BACKEND_DIR = Path(__file__).resolve().parents[1] / "backend"
sys.path.insert(0, str(_BACKEND_DIR))

# 대표 10종목.
SUBSET_UNIVERSE = ["005930", "000660", "005380", "000270", "012330",
                   "042700", "066570", "006400", "035420", "051910"]

OUT_DIR = Path("data/market/robust_intraday_1m_subset")
REPORT_DIR = Path("reports/backtest")
_RATE_LIMIT_MARKERS = ("초당 거래건수", "EGW00201")


def csv_path(symbol: str, out_dir: Path = OUT_DIR) -> Path:
    """종목 1분봉 CSV 경로 — `{symbol}_1m.csv`."""
    return out_dir / f"{symbol}_1m.csv"


def already_collected(symbol: str, out_dir: Path = OUT_DIR) -> bool:
    p = csv_path(symbol, out_dir)
    return p.exists() and p.stat().st_size > 0


def build_summary(collected: list[str], failed: dict[str, str],
                  target: list[str]) -> dict:
    return {
        "target_symbols": list(target),
        "target_count": len(target),
        "collected": list(collected),
        "collected_count": len(collected),
        "failed": dict(failed),
        "failed_count": len(failed),
        "status": "COMPLETE" if len(collected) + len(failed) >= len(target) else "IN_PROGRESS",
        "is_live_authorization": False,
        "kis_order_api_called": False,
        "note": "read-only 시세 수집 — 주문 API 0건. 1분봉 원본 보존.",
    }


def _write_atomic(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(text, encoding="utf-8")
    tmp.replace(path)


def _write_json(path: Path, payload: dict) -> None:
    _write_atomic(path, json.dumps(payload, ensure_ascii=False, indent=2))


def _rows_to_csv(rows: list[dict], symbol: str) -> str:
    lines = ["timestamp,open,high,low,close,volume,symbol"]
    for r in rows:
        lines.append(f"{r.get('timestamp')},{r.get('open')},{r.get('high')},"
                     f"{r.get('low')},{r.get('close')},{r.get('volume')},{symbol}")
    return "\n".join(lines) + "\n"


def _load_base_collector():
    """기존 5분봉 수집기의 *검증된* backward-walk 로직 재사용 (importlib)."""
    import importlib.util
    root = Path(__file__).resolve().parents[1]
    spec = importlib.util.spec_from_file_location(
        "kis_5m_collector", root / "scripts" / "collect_kis_intraday_ohlcv.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


async def _collect_one(client, symbol: str, *, end_date: str, oldest_date: str,
                       sleep: float, max_calls: int):
    """raw 1분봉 backward 수집 — 기존 _collect_symbol_backward 재사용 (resample 안 함)."""
    from app.market_data.kis_intraday_fetch import dedupe_by_timestamp

    base = _load_base_collector()
    recs, _calls = await base._collect_symbol_backward(
        client, symbol, end_date, oldest_date, sleep, max_calls)
    rows = dedupe_by_timestamp(list(recs or []))
    rows.sort(key=lambda r: str(r.get("timestamp")))
    return rows


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description="Collect raw 1m subset (read-only)")
    p.add_argument("--symbols", default=",".join(SUBSET_UNIVERSE))
    p.add_argument("--days", type=int, default=60)
    p.add_argument("--end", default=None, help="수집 종료(최신) 날짜 YYYYMMDD (default=오늘 KST)")
    p.add_argument("--max-calls-per-symbol", type=int, default=12)
    p.add_argument("--sleep", type=float, default=0.6)
    p.add_argument("--out-dir", default=str(OUT_DIR))
    p.add_argument("--resume", action="store_true", help="완료 종목 skip (기본 동작)")
    p.add_argument("--dry-run", action="store_true",
                   help="네트워크 호출 없이 대상/경로만 출력 (장외 점검용)")
    args = p.parse_args(argv)

    target = [s.strip() for s in args.symbols.split(",") if s.strip()]
    out_dir = Path(args.out_dir)
    prog_path = REPORT_DIR / "intrabar_1m_collection_progress.json"
    failed_path = REPORT_DIR / "intrabar_1m_failed_symbols.json"
    summary_path = REPORT_DIR / "intrabar_1m_collection_summary.json"

    collected: list[str] = [s for s in target if already_collected(s, out_dir)]
    failed: dict[str, str] = {}

    if args.dry_run:
        _write_json(prog_path, {"target": target, "pending":
                                [s for s in target if s not in collected],
                                "dry_run": True})
        print(f"[dry-run] target={len(target)} already={len(collected)} "
              f"out_dir={out_dir}")
        _write_json(summary_path, build_summary(collected, failed, target))
        return 0

    # 실제 수집 — 장중 KIS read-only. (KisClient 구성은 운영자 .env 자격으로.)
    import asyncio

    from app.brokers.kis_client import KisClient
    from app.core.config import get_settings

    from datetime import datetime, timedelta, timezone
    _KST = timezone(timedelta(hours=9))
    end_date = args.end or datetime.now(_KST).strftime("%Y%m%d")
    oldest_date = (datetime.strptime(end_date, "%Y%m%d")
                   - timedelta(days=int(args.days * 1.6))).strftime("%Y%m%d")

    async def _run():
        # cwd 무관하게 backend/.env 에서 KIS 자격 로드 (get_settings 는 cwd 상대).
        from app.core.config import Settings
        _env = _BACKEND_DIR / ".env"
        settings = Settings(_env_file=str(_env)) if _env.exists() else get_settings()
        if not (settings.kis_app_key and settings.kis_app_secret):
            failed["__all__"] = "KIS_CREDENTIALS_MISSING"
            return
        client = KisClient(settings.kis_app_key, settings.kis_app_secret,
                           is_paper=bool(getattr(settings, "kis_is_paper", True)))
        base = _load_base_collector()
        try:
            base._seed_cached_token(client, settings.kis_is_paper)
        except Exception:  # noqa: BLE001
            pass
        for sym in target:
            if already_collected(sym, out_dir):
                continue
            try:
                rows = await _collect_one(client, sym, end_date=end_date,
                                          oldest_date=oldest_date, sleep=args.sleep,
                                          max_calls=args.max_calls_per_symbol)
                if rows:
                    _write_atomic(csv_path(sym, out_dir), _rows_to_csv(rows, sym))
                    collected.append(sym)
                else:
                    failed[sym] = "EMPTY_RESPONSE"
            except Exception as exc:  # noqa: BLE001
                failed[sym] = f"{type(exc).__name__}: {str(exc)[:80]}"
            _write_json(prog_path, {"target": target, "collected": collected,
                                    "failed": failed,
                                    "pending": [s for s in target
                                                if s not in collected and s not in failed]})

    asyncio.run(_run())
    _write_json(failed_path, failed)
    _write_json(summary_path, build_summary(collected, failed, target))
    print(f"[ok] collected={len(collected)} failed={len(failed)} out_dir={out_dir}")
    return 0 if collected else 1


if __name__ == "__main__":
    raise SystemExit(main())

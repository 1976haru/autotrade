#!/usr/bin/env python3
"""KIS-INTRADAY-ROBUST-DATASET-COLLECTION-01 — robust 분봉 데이터셋 수집 CLI.

정확한 *장기* 전략 검증을 위한 robust 데이터셋(5분봉 + 1분봉 정밀 subset)을 수집한다.
기존 `collect_kis_intraday_ohlcv` 의 read-only KIS 분봉 수집 엔진(주식일별분봉조회
[국내주식-213], TR FHKST03010230)을 *그대로 재사용*하며, 별도 경로에 저장한다.

CLAUDE.md 절대 원칙 (본 스크립트):
- **read-only 시세 조회만.** broker.place_order / route_order / OrderExecutor 호출 0건.
- KIS 주문 API 호출 0건 — `inquire_time_dailychartprice` 만 사용(엔진 재사용).
- 안전 flag(ENABLE_LIVE_TRADING / ENABLE_AI_EXECUTION / KIS_IS_PAPER) 변경 0건.
- appkey / appsecret / access token / 계좌번호 원문 출력 0건.
- **백테스트 실행 0건** — 본 작업은 수집/메타데이터만.
- 기존 6개월/1년 데이터는 삭제하지 않는다 — 새 데이터는 별도 경로에 저장.
- 수집 실패를 성공처럼 보고 0건 — 실패는 reason 과 함께 기록.

exit code:
    0: 1종목 이상 수집 성공 (또는 resume skip)
    1: 전 종목 수집 실패
    2: 실행 오류 (인자 / 환경)
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parents[1]
_BACKEND_DIR = _REPO_ROOT / "backend"
_SCRIPTS_DIR = _REPO_ROOT / "scripts"
for _p in (_BACKEND_DIR, _SCRIPTS_DIR):
    if str(_p) not in sys.path:
        sys.path.insert(0, str(_p))

_KST = timezone(timedelta(hours=9))

# 별도 경로 — 기존 data/market/intraday_5m_1y* / kis_6m 과 혼동 금지.
ROBUST_5M_DIR = "data/market/robust_intraday_5m"
ROBUST_1M_SUBSET_DIR = "data/market/robust_intraday_1m_subset"
REPORT_DIR = "reports/strategy_validation"

# period → 거래일 수 매핑 (KIS 분봉은 ~1년 제공 — 2y 요청 시 가능한 만큼만 수집).
_PERIOD_DAYS = {"2y": 480, "1y": 240, "min": 240, "60d": 60}


def _eprint(*a: object) -> None:
    print(*a, file=sys.stderr, flush=True)


def _write_json(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def _resolve_symbol_set(symbol_set: str) -> list[str]:
    """robust 종목군에서 수집 대상 종목 리스트를 결정한다."""
    from app.market_data.robust_dataset import REPRESENTATIVE_10, build_robust_symbol_groups

    if symbol_set == "representative10":
        return list(REPRESENTATIVE_10)
    groups = build_robust_symbol_groups()
    syms = groups.all_symbols()
    if symbol_set == "robust30":
        return syms[:30]
    # robust50 / all — 현재 큐레이션 총합(≈35)을 그대로.
    return syms


def _build_collector_ns(
    *, symbols: list[str], end: str | None, num_days: int, bar_size: str,
    output_dir: str, resume: bool, progress_json: str, failed_json: str,
    sleep_seconds: float, rate_max_calls: int, rate_window: float,
    max_calls_per_day: int, min_complete_days: int,
) -> argparse.Namespace:
    """기존 collect_kis_intraday_ohlcv._run 이 기대하는 Namespace 를 구성한다."""
    return argparse.Namespace(
        symbols=",".join(symbols), symbols_file=None, from_universe=False,
        end=end, num_days=num_days, bar_size=bar_size, output_dir=output_dir,
        sleep_seconds=sleep_seconds, max_symbols=0, max_calls_per_day=max_calls_per_day,
        json_out=None, markdown=None, resume=resume,
        min_complete_days=min_complete_days, progress_json=progress_json,
        failed_json=failed_json, rate_max_calls=rate_max_calls, rate_window=rate_window,
    )


def _default_collect_fn(ns: argparse.Namespace) -> dict:
    """실제 KIS read-only 수집 — 기존 엔진(_run) 재사용. (주문 API 0건)"""
    import collect_kis_intraday_ohlcv as engine  # scripts/ 모듈

    return asyncio.run(engine._run(ns))


def run_robust_collection(
    *, stage: str, symbol_set: str, period: str, num_days: int | None,
    end: str | None, resume: bool, sleep_seconds: float, rate_max_calls: int,
    rate_window: float, max_calls_per_day: int, output_dir: str | None = None,
    progress_json: str | None = None, failed_json: str | None = None,
    collect_fn=None,
) -> dict:
    """robust 데이터셋 한 단계(5m 또는 1m subset)를 수집한다.

    collect_fn: 테스트 주입용 (기본은 실제 KIS read-only 엔진). 시그니처 (ns) -> report dict.
    """
    collect_fn = collect_fn or _default_collect_fn
    bar_size = "1m" if stage == "1m_subset" else "5m"
    if num_days is None:
        num_days = _PERIOD_DAYS.get(period, 240)
        if stage == "1m_subset":
            num_days = min(num_days, _PERIOD_DAYS["60d"])

    symbols = _resolve_symbol_set(symbol_set)
    out_dir = output_dir or (ROBUST_1M_SUBSET_DIR if stage == "1m_subset" else ROBUST_5M_DIR)
    prog = progress_json or str(
        Path(REPORT_DIR) / ("robust_dataset_1m_subset_progress.json" if stage == "1m_subset"
                            else "robust_dataset_5m_progress.json"))
    failed = failed_json or str(Path(REPORT_DIR) / "robust_dataset_failed_symbols.json")
    # resume 완료 판정 — 1m subset 은 짧으므로 절대 거래일 기준을 낮춘다.
    min_complete = 0 if stage != "1m_subset" else 30

    ns = _build_collector_ns(
        symbols=symbols, end=end, num_days=num_days, bar_size=bar_size,
        output_dir=out_dir, resume=resume, progress_json=prog, failed_json=failed,
        sleep_seconds=sleep_seconds, rate_max_calls=rate_max_calls,
        rate_window=rate_window, max_calls_per_day=max_calls_per_day,
        min_complete_days=min_complete)

    _eprint(f"[robust] stage={stage} symbols={len(symbols)} bar={bar_size} "
            f"num_days={num_days} out={out_dir}")
    report = collect_fn(ns)
    report = dict(report or {})
    report["stage"] = stage
    report["symbol_set"] = symbol_set
    report["output_dir"] = out_dir
    # 수집 단계 안전 불변값 재확인.
    report.setdefault("is_live_authorization", False)
    report.setdefault("kis_order_api_called", False)
    report.setdefault("broker_order_sent", False)
    return report


def collect_explicit_symbols(
    symbols: list[str], *, num_days: int, end: str | None = None, resume: bool = True,
    sleep_seconds: float = 0.25, rate_max_calls: int = 2, rate_window: float = 1.1,
    max_calls_per_day: int = 6, output_dir: str = ROBUST_5M_DIR,
    progress_json: str | None = None, failed_json: str | None = None, collect_fn=None,
) -> dict:
    """명시한 종목 리스트만 5분봉으로 (재)수집 — 실패 종목 재시도용. (read-only, 주문 0건)"""
    if not symbols:
        return {"status": "OK", "requested": 0, "succeeded": 0, "failed": 0,
                "total_bars": 0, "per_symbol": [], "stage": "5m", "output_dir": output_dir,
                "is_live_authorization": False, "kis_order_api_called": False,
                "broker_order_sent": False}
    collect_fn = collect_fn or _default_collect_fn
    prog = progress_json or str(Path(REPORT_DIR) / "robust_dataset_5m_retry_progress.json")
    failed = failed_json or str(Path(REPORT_DIR) / "robust_dataset_failed_symbols.json")
    ns = _build_collector_ns(
        symbols=symbols, end=end, num_days=num_days, bar_size="5m", output_dir=output_dir,
        resume=resume, progress_json=prog, failed_json=failed, sleep_seconds=sleep_seconds,
        rate_max_calls=rate_max_calls, rate_window=rate_window,
        max_calls_per_day=max_calls_per_day, min_complete_days=0)
    rep = dict(collect_fn(ns) or {})
    rep["stage"] = "5m"
    rep["output_dir"] = output_dir
    rep.setdefault("is_live_authorization", False)
    rep.setdefault("kis_order_api_called", False)
    rep.setdefault("broker_order_sent", False)
    return rep


def failed_symbols_of(report: dict) -> list[str]:
    """수집 report 에서 실패/무데이터 종목코드만 추출 (재시도 대상)."""
    return [p.get("symbol") for p in (report.get("per_symbol") or [])
            if p.get("status") in ("FAILED", "NO_DATA") and p.get("symbol")]


def build_one_minute_availability(report: dict, out_dir: str) -> dict:
    """1분봉 subset 수집 결과 → availability 분류 (UNAVAILABLE 은 FAIL 이 아님)."""
    succeeded = int(report.get("succeeded", 0) or 0)
    requested = int(report.get("requested", 0) or 0)
    if succeeded <= 0:
        availability = "UNAVAILABLE"
        note = ("1분봉을 수집하지 못했습니다(상태=UNAVAILABLE). KIS 분봉이 제공되지 않거나 "
                "네트워크/자격 문제일 수 있습니다 — 5분봉 수집은 별도로 계속 진행합니다. (FAIL 아님)")
    elif succeeded < requested:
        availability = "PARTIAL"
        note = f"1분봉 일부 종목만 수집({succeeded}/{requested})."
    else:
        availability = "AVAILABLE"
        note = f"1분봉 대표 종목 {succeeded}종목 수집 완료."
    return {
        "availability": availability,
        "succeeded": succeeded, "requested": requested,
        "output_dir": out_dir,
        "session_window_hint": "09:00-10:30 (장초반 ORB/GAP/체결순서 정밀 검증용 — 전체 장중 수집)",
        "per_symbol": report.get("per_symbol", []),
        "note": note,
        "is_live_authorization": False, "kis_order_api_called": False,
        "broker_order_sent": False, "contains_secret": False,
    }


def main(argv: list[str] | None = None) -> int:
    for _s in (sys.stdout, sys.stderr):
        try:
            _s.reconfigure(encoding="utf-8", errors="replace")
        except (AttributeError, ValueError):
            pass
    try:
        p = argparse.ArgumentParser(
            description="robust 분봉 데이터셋 수집 (read-only · 주문/백테스트/EXE 빌드 0건).")
        p.add_argument("--stage", choices=["5m", "1m_subset", "both"], default="5m",
                       help="수집 단계 (5m / 1m_subset / both)")
        p.add_argument("--symbol-set", choices=["robust30", "robust50", "all", "representative10"],
                       default="robust50", help="5m 종목 집합 (1m_subset 은 항상 representative10)")
        p.add_argument("--period", choices=list(_PERIOD_DAYS), default="2y",
                       help="요청 기간 (2y 우선 → 가능한 만큼; KIS 분봉은 ~1년 제공)")
        p.add_argument("--num-days", type=int, default=None, help="거래일 수 직접 지정(period 무시)")
        p.add_argument("--end", default=None, help="조회 종료일 YYYYMMDD (기본: 오늘 KST)")
        p.add_argument("--resume", action="store_true", help="이미 충분히 수집된 종목 skip")
        p.add_argument("--sleep-seconds", type=float, default=0.25)
        p.add_argument("--rate-max-calls", type=int, default=2)
        p.add_argument("--rate-window", type=float, default=1.1)
        p.add_argument("--max-calls-per-day", type=int, default=6)
        p.add_argument("--out-dir", default=None, help="저장 경로 직접 지정")
        args = p.parse_args(argv)

        out = Path(REPORT_DIR)
        out.mkdir(parents=True, exist_ok=True)

        # 종목군 manifest 는 수집과 무관하게 항상 먼저 기록(메타데이터).
        from app.market_data.robust_dataset import build_robust_symbol_groups, symbol_group_to_dict
        groups = build_robust_symbol_groups()
        _write_json(out / "robust_symbol_group_manifest.json", symbol_group_to_dict(groups))
        print(f"[OK] symbol group manifest: {out / 'robust_symbol_group_manifest.json'} "
              f"({groups.total_symbols} symbols)")

        reports: dict[str, dict] = {}
        stages = ["5m", "1m_subset"] if args.stage == "both" else [args.stage]
        for stage in stages:
            sset = "representative10" if stage == "1m_subset" else args.symbol_set
            rep = run_robust_collection(
                stage=stage, symbol_set=sset, period=args.period, num_days=args.num_days,
                end=args.end, resume=args.resume, sleep_seconds=args.sleep_seconds,
                rate_max_calls=args.rate_max_calls, rate_window=args.rate_window,
                max_calls_per_day=args.max_calls_per_day, output_dir=args.out_dir)
            reports[stage] = rep
            print(f"[OK] stage={stage} status={rep.get('status')} "
                  f"succeeded={rep.get('succeeded')}/{rep.get('requested')} "
                  f"total_bars={rep.get('total_bars', 0)}")

        if "1m_subset" in reports:
            avail = build_one_minute_availability(
                reports["1m_subset"], reports["1m_subset"].get("output_dir", ROBUST_1M_SUBSET_DIR))
            _write_json(out / "robust_1m_subset_availability.json", avail)
            print(f"[OK] 1m availability: {avail['availability']}")

        # 진행 요약 (collector 가 progress_json 을 증분 저장하지만 최종본도 보존).
        summary = {
            "generated_at": datetime.now(timezone.utc).isoformat(),
            "stages": {k: {"status": v.get("status"), "succeeded": v.get("succeeded"),
                           "requested": v.get("requested"), "total_bars": v.get("total_bars", 0),
                           "trading_day_count": v.get("trading_day_count", 0),
                           "output_dir": v.get("output_dir")}
                       for k, v in reports.items()},
            "symbol_count": groups.total_symbols,
            "is_live_authorization": False, "kis_order_api_called": False,
            "broker_order_sent": False, "exe_build_executed": False, "contains_secret": False,
            "note": ("robust 데이터셋 수집(read-only). 백테스트/주문/EXE 빌드 0건. "
                     "품질검증·manifest 는 validate_robust_intraday_dataset.py 로 생성."),
        }
        _write_json(out / "robust_dataset_collection_progress.json", summary)

        any_ok = any(int(r.get("succeeded", 0) or 0) > 0 for r in reports.values())
        return 0 if any_ok else 1
    except Exception as e:  # noqa: BLE001
        import traceback
        traceback.print_exc()
        _eprint(f"[ERROR] {type(e).__name__}: {e}")
        return 2


if __name__ == "__main__":
    raise SystemExit(main())

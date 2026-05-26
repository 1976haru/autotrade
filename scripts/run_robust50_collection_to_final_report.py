#!/usr/bin/env python3
"""ROBUST50-FULL-COLLECTION-TO-FINAL-REPORT-01 — 수집→실패재시도→품질검증→manifest 자동 chain.

robust50 5분봉 수집을 끝까지 진행한 뒤 곧바로 품질검증 + manifest 생성까지 *한 번에*
이어서 처리하고, 최종 summary(json/md)와 핵심 수치를 출력한다. 중간 진행은 기존 collector
가 progress/log 파일에 남긴다(본 wrapper 는 별도 tail 출력을 하지 않음).

CLAUDE.md 절대 원칙 (본 wrapper):
- read-only 시세 수집만. broker.place_order / route_order / OrderExecutor 호출 0건.
- KIS 주문 API 호출 0건. **백테스트 실행 0건**(backtest 모듈 직접 import 0건).
- 전략 파라미터 / locked rule 변경 0건. EXE / tauri / cargo 빌드 0건.
- 안전 flag 변경 0건. secret / 계좌 원문 출력 0건. 기존 CSV 삭제 0건.
- resume 가능 — 세션이 끊겨도 동일 명령 재실행 시 이어서 완료한다.

exit code:
    0: 수집 + 품질검증 완료 (ready 여부와 무관 — 정직하게 status 기록)
    2: 실행 오류
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parents[1]
_BACKEND_DIR = _REPO_ROOT / "backend"
_SCRIPTS_DIR = _REPO_ROOT / "scripts"
for _p in (_BACKEND_DIR, _SCRIPTS_DIR):
    if str(_p) not in sys.path:
        sys.path.insert(0, str(_p))

import collect_robust_intraday_dataset as C  # noqa: E402  (read-only 수집 — 주문 0건)
import validate_robust_intraday_dataset as V  # noqa: E402  (품질/manifest — 백테스트 0건)

REPORT_DIR = "reports/strategy_validation"
_PERIOD_DAYS = C._PERIOD_DAYS


def _eprint(*a: object) -> None:
    print(*a, file=sys.stderr, flush=True)


def _write_json(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def _read_json(path: Path) -> dict:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def run_to_final(
    *, symbol_set: str = "robust50", period: str = "2y", num_days: int | None = 300,
    resume: bool = True, max_retry_failed: int = 2, write_latest: bool = True,
    report_dir: str = REPORT_DIR, sleep_seconds: float = 0.35,
    rate_max_calls: int = 1, rate_window: float = 1.0,
) -> dict:
    """수집 → 실패 재시도 → 품질검증(--write-latest) → 최종 summary 까지 한 번에 진행.

    pacing 기본값(rate_max_calls=1 / window=1.0 / sleep=0.35)은 KIS 모의 초당건수 제한
    *아래*로 맞춰 EGW00201 backoff 재시도 churn 을 줄이기 위함(첫 시도 성공률↑ → 순 처리량↑).
    """
    resolved_days = num_days if num_days is not None else _PERIOD_DAYS.get(period, 240)

    _eprint(f"[runner] collect pass1: symbol_set={symbol_set} num_days={resolved_days} resume={resume} "
            f"pacing(rate={rate_max_calls}/{rate_window}s sleep={sleep_seconds})")
    rep = C.run_robust_collection(
        stage="5m", symbol_set=symbol_set, period=period, num_days=num_days, end=None,
        resume=resume, sleep_seconds=sleep_seconds, rate_max_calls=rate_max_calls,
        rate_window=rate_window, max_calls_per_day=6)

    failed = C.failed_symbols_of(rep)
    retry_log: list[dict] = []
    for attempt in range(max(0, max_retry_failed)):
        if not failed:
            break
        _eprint(f"[runner] retry {attempt + 1}/{max_retry_failed}: {len(failed)} symbols -> {failed}")
        r2 = C.collect_explicit_symbols(
            failed, num_days=resolved_days, resume=True, sleep_seconds=sleep_seconds,
            rate_max_calls=rate_max_calls, rate_window=rate_window)
        before = len(failed)
        failed = C.failed_symbols_of(r2)
        retry_log.append({"attempt": attempt + 1, "before": before, "remaining": len(failed),
                          "remaining_symbols": list(failed)})

    # 품질검증 + manifest 생성 (validate CLI 재사용 — 백테스트 실행 0건).
    _eprint("[runner] validate + manifest (write_latest=%s)" % write_latest)
    V.main(["--write-latest"] if write_latest else [])

    out = Path(report_dir)
    manifest = _read_json(out / "robust_dataset_manifest.json")
    quality = _read_json(out / "robust_dataset_quality.json")
    one_min = _read_json(out / "robust_1m_subset_availability.json")

    per = quality.get("per_symbol", [])
    days_present = [p.get("day_count", 0) for p in per if p.get("present")]
    summary = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "task": "ROBUST50-FULL-COLLECTION-TO-FINAL-REPORT-01",
        "collection_status": rep.get("status"),
        "requested_symbols": rep.get("requested"),
        "succeeded": rep.get("succeeded"),
        "skipped": rep.get("skipped"),
        "failed": rep.get("failed"),
        "total_bars_5m": quality.get("total_bars", rep.get("total_bars", 0)),
        "trading_days": manifest.get("trading_days"),
        "actual_period": manifest.get("actual_period"),
        "start_date": manifest.get("start_date"),
        "end_date": manifest.get("end_date"),
        "symbols_present": quality.get("symbols_present"),
        "symbol_count": manifest.get("symbol_count"),
        "min_trading_days": min(days_present) if days_present else 0,
        "max_trading_days": max(days_present) if days_present else 0,
        "avg_trading_days": round(sum(days_present) / len(days_present), 1) if days_present else 0,
        "group_quality": quality.get("group_quality", {}),
        "remaining_failed_symbols": list(failed),
        "retry_log": retry_log,
        "data_quality_status": manifest.get("data_quality_status"),
        "time_split_status": manifest.get("time_split_status"),
        "regime_label_status": manifest.get("regime_label_status"),
        "one_minute_availability": manifest.get("one_minute_availability"),
        "one_minute_note": one_min.get("note"),
        "ready_for_robust_backtest": manifest.get("ready_for_robust_backtest"),
        "warnings": manifest.get("warnings", []),
        "next_recommended_task": manifest.get("next_recommended_task"),
        # 안전 불변값.
        "backtest_executed": False, "is_live_authorization": False, "real_order_allowed": False,
        "live_trading_recommendation": False, "is_order_signal": False,
        "kis_order_api_called": False, "broker_order_sent": False, "order_created": False,
        "exe_build_executed": False, "contains_secret": False,
        "do_not_auto_apply": True, "no_profit_guarantee": True,
        "note": ("robust50 수집 → 품질검증 → manifest chain (read-only). "
                 "백테스트/주문/실전 전환/EXE 빌드 0건, 수익 보장 아님."),
    }
    _write_json(out / "robust50_final_collection_summary.json", summary)
    _write_text_md(out / "robust50_final_collection_summary.md", summary)
    return summary


def _write_text_md(path: Path, s: dict) -> None:
    lines = [
        "# robust50 5분봉 수집 → 품질검증 최종 summary (read-only)", "",
        f"- collection_status: **{s['collection_status']}** · 성공 {s['succeeded']}/"
        f"{s['requested_symbols']} · skip {s.get('skipped')} · 실패 {s['failed']}",
        f"- 실제 기간: {s['actual_period']} (거래일 {s['trading_days']})",
        f"- total_bars(5m): {s['total_bars_5m']}",
        f"- 종목별 거래일 min/avg/max: {s['min_trading_days']} / {s['avg_trading_days']} / "
        f"{s['max_trading_days']}",
        f"- 데이터 품질: **{s['data_quality_status']}** · 시간분할: **{s['time_split_status']}** · "
        f"regime: **{s['regime_label_status']}** · 1분봉: **{s['one_minute_availability']}**",
        f"- ready_for_robust_backtest: **{s['ready_for_robust_backtest']}**",
        f"- 남은 실패 종목: {s['remaining_failed_symbols'] or '없음'}",
        "",
        f"> 안전: backtest_executed={s['backtest_executed']} · is_live_authorization="
        f"{s['is_live_authorization']} · real_order_allowed={s['real_order_allowed']} · "
        f"do_not_auto_apply={s['do_not_auto_apply']}. 수집/검증 자료 — 실전 전환 승인/수익 보장 아님.",
    ]
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def main(argv: list[str] | None = None) -> int:
    for _s in (sys.stdout, sys.stderr):
        try:
            _s.reconfigure(encoding="utf-8", errors="replace")
        except (AttributeError, ValueError):
            pass
    try:
        p = argparse.ArgumentParser(
            description="robust50 수집→품질검증→manifest 자동 chain (read-only · 백테스트/주문/EXE 빌드 0건).")
        p.add_argument("--symbol-set", default="robust50",
                       choices=["robust30", "robust50", "all", "representative10"])
        p.add_argument("--period", choices=list(_PERIOD_DAYS), default="2y")
        p.add_argument("--num-days", type=int, default=300)
        p.add_argument("--resume", action="store_true", default=True)
        p.add_argument("--no-resume", dest="resume", action="store_false")
        p.add_argument("--max-retry-failed", type=int, default=2)
        p.add_argument("--sleep-seconds", type=float, default=0.35)
        p.add_argument("--rate-max-calls", type=int, default=1)
        p.add_argument("--rate-window", type=float, default=1.0)
        p.add_argument("--write-latest", action="store_true", default=True)
        p.add_argument("--no-backtest", action="store_true", default=True,
                       help="명시적 no-backtest (기본 — 본 wrapper 는 백테스트를 실행하지 않음)")
        p.add_argument("--log-file", default=None, help="참고용(리다이렉트는 호출측에서)")
        args = p.parse_args(argv)

        summary = run_to_final(
            symbol_set=args.symbol_set, period=args.period, num_days=args.num_days,
            resume=args.resume, max_retry_failed=args.max_retry_failed,
            write_latest=args.write_latest, sleep_seconds=args.sleep_seconds,
            rate_max_calls=args.rate_max_calls, rate_window=args.rate_window)

        print(f"[OK] collection_status={summary['collection_status']} "
              f"succeeded={summary['succeeded']}/{summary['requested_symbols']} "
              f"failed={summary['failed']} bars={summary['total_bars_5m']}")
        print(f"[OK] period={summary['actual_period']} trading_days={summary['trading_days']} "
              f"days(min/avg/max)={summary['min_trading_days']}/{summary['avg_trading_days']}/"
              f"{summary['max_trading_days']}")
        print(f"[OK] quality={summary['data_quality_status']} split={summary['time_split_status']} "
              f"regime={summary['regime_label_status']} 1m={summary['one_minute_availability']} "
              f"ready={summary['ready_for_robust_backtest']}")
        print(f"[OK] remaining_failed={summary['remaining_failed_symbols'] or 'none'}")
        print("NOTE: 수집/검증 전용 · 백테스트/주문/실전 전환/EXE 빌드 0건 · 수익 보장 아님.")
        return 0
    except Exception as e:  # noqa: BLE001
        import traceback
        traceback.print_exc()
        _eprint(f"[ERROR] {type(e).__name__}: {e}")
        return 2


if __name__ == "__main__":
    raise SystemExit(main())

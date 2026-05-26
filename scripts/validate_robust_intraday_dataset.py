#!/usr/bin/env python3
"""KIS-INTRADAY-ROBUST-DATASET-COLLECTION-01 — robust 데이터셋 품질검증 + manifest CLI.

수집된 robust 5분봉(+1분봉 subset)을 read-only 로 적재해 품질검증 / 시간분할 /
장세 regime 라벨 / dataset manifest 를 생성한다. **백테스트를 실행하지 않는다.**

CLAUDE.md 절대 원칙 (본 스크립트):
- read-only · broker / OrderExecutor / route_order / KIS 주문 API 호출 0건.
- 백테스트 실행 0건 · 전략 파라미터/locked rule 변경 0건 · EXE 빌드 0건.
- 안전 flag 변경 0건 · secret / 계좌 원문 출력 0건.
- 기존 데이터 삭제 0건 — 새 데이터(별도 경로)만 검증.

exit code:
    0: 검증 완료 (ready 여부와 무관 — 정직하게 status 기록)
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

ROBUST_5M_DIR = "data/market/robust_intraday_5m"
ROBUST_1M_SUBSET_DIR = "data/market/robust_intraday_1m_subset"
REPORT_DIR = "reports/strategy_validation"


def _eprint(*a: object) -> None:
    print(*a, file=sys.stderr, flush=True)


def _write_json(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def _write_text(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def _default_loader(dir_5m: str, symbols: list[str]) -> dict[str, list]:
    """{symbol}_5m.csv 를 적재 → {symbol: bars}. 없는 종목은 빈 리스트."""
    from app.market_data.intraday_ohlcv import load_intraday_csv

    out: dict[str, list] = {}
    base = Path(dir_5m)
    for sym in symbols:
        fp = base / f"{sym}_5m.csv"
        if not fp.exists():
            fp = base / f"{sym}.csv"
        if not fp.exists():
            out[sym] = []
            continue
        try:
            bars, _meta = load_intraday_csv(str(fp))
            out[sym] = list(bars)
        except Exception as e:  # noqa: BLE001
            _eprint(f"  [load] {sym} ERROR {type(e).__name__}: {str(e)[:80]}")
            out[sym] = []
    return out


def _count_1m_subset_bars(dir_1m: str) -> tuple[int, int]:
    """1분봉 subset 디렉토리의 (총 bar 수, 파일 수) — read-only."""
    base = Path(dir_1m)
    if not base.is_dir():
        return 0, 0
    files = sorted(base.glob("*.csv"))
    total = 0
    for fp in files:
        try:
            with fp.open(encoding="utf-8") as f:
                total += max(0, sum(1 for _ in f) - 1)
        except OSError:
            pass
    return total, len(files)


def run_validation(
    *, dir_5m: str = ROBUST_5M_DIR, dir_1m: str = ROBUST_1M_SUBSET_DIR,
    requested_period: str = "2y (KIS 분봉 ~1년 제공 — 가능한 만큼)",
    loader=None,
) -> dict:
    """robust 데이터셋 전 메타데이터를 생성한다 (순수 모듈 호출 + read-only 적재)."""
    from app.market_data import robust_dataset as rd

    loader = loader or (lambda syms: _default_loader(dir_5m, syms))
    groups = rd.build_robust_symbol_groups()
    symbol_to_bars = loader(groups.all_symbols())

    quality = rd.evaluate_dataset_quality(groups, symbol_to_bars)

    # 거래일 집합 → 시간 분할.
    all_dates: set[str] = set()
    from datetime import timedelta, timezone
    _KST = timezone(timedelta(hours=9))
    for bars in symbol_to_bars.values():
        for b in bars:
            ts = getattr(b, "timestamp", None)
            try:
                all_dates.add(ts.astimezone(_KST).date().isoformat())
            except Exception:  # noqa: BLE001
                d = str(ts)[:10]
                if d:
                    all_dates.add(d)
    time_split = rd.build_time_split(sorted(all_dates))

    # 장세 regime: ETF proxy(069500) 우선, 없으면 대형+중형 equal-weight.
    etf = "069500"
    if symbol_to_bars.get(etf):
        proxy_daily = rd.daily_from_intraday_bars(symbol_to_bars[etf])
        proxy_kind = f"ETF_PROXY:{etf}"
    else:
        core = (groups.symbols_by_group.get(rd.LARGE_CAP, [])
                + groups.symbols_by_group.get(rd.MID_CAP, []))
        proxy_daily = rd.build_equal_weight_proxy(
            {s: rd.daily_from_intraday_bars(symbol_to_bars.get(s, [])) for s in core})
        proxy_kind = "EQUAL_WEIGHT_CORE"
    regime = rd.label_market_regimes(proxy_daily, proxy_kind=proxy_kind)

    total_bars_1m, files_1m = _count_1m_subset_bars(dir_1m)
    if total_bars_1m > 0 and files_1m >= 5:
        one_min = "AVAILABLE"
    elif total_bars_1m > 0:
        one_min = "PARTIAL"
    else:
        one_min = "UNAVAILABLE"

    manifest = rd.build_dataset_manifest(
        group_manifest=groups, quality=quality, time_split=time_split, regime=regime,
        requested_period=requested_period, one_minute_availability=one_min,
        total_bars_1m_subset=total_bars_1m)

    return {
        "symbol_group": rd.symbol_group_to_dict(groups),
        "quality": rd.quality_to_dict(quality),
        "time_split": rd.time_split_to_dict(time_split),
        "regime": rd.regime_to_dict(regime),
        "manifest": rd.manifest_to_dict(manifest),
        "one_minute_availability": one_min,
        "one_minute_files": files_1m,
        "one_minute_bars": total_bars_1m,
    }


def build_status(result: dict) -> dict:
    """endpoint(GET /api/system/robust-dataset/status) 가 그대로 서빙할 status snapshot."""
    m = result["manifest"]
    q = result["quality"]
    return {
        "available": True,
        "collection_status": q["status"],
        "data_quality_status": q["status"],
        "symbol_count": m["symbol_count"],
        "actual_period": m["actual_period"],
        "trading_days": m["trading_days"],
        "ready_for_robust_backtest": m["ready_for_robust_backtest"],
        "one_minute_available": result["one_minute_availability"] != "UNAVAILABLE",
        "one_minute_availability": result["one_minute_availability"],
        "time_split_status": m["time_split_status"],
        "regime_label_status": m["regime_label_status"],
        "symbols_by_group": m["symbols_by_group"],
        "warnings": m["warnings"],
        "next_recommended_task": m["next_recommended_task"],
        # 안전 불변값.
        "live_trading_recommendation": False, "real_order_allowed": False,
        "is_live_authorization": False, "is_order_signal": False,
        "kis_order_api_called": False, "broker_order_sent": False,
        "exe_build_executed": False, "contains_secret": False,
        "do_not_auto_apply": True, "no_profit_guarantee": True,
        "disclaimer": ("robust 데이터셋 상태(read-only). 데이터 수집/품질검증/메타데이터 전용 — "
                       "백테스트/주문/실전 전환/EXE 빌드 0건, 수익 보장 아님."),
    }


def _md_groups(g: dict) -> str:
    lines = ["# robust 종목군 manifest", "",
             f"- 총 {g['total_symbols']}종목 · {g['counts_by_group']}", "",
             "| symbol | group | name | reason |", "|---|---|---|---|"]
    for e in g["entries"]:
        lines.append(f"| {e['symbol']} | {e['group']} | {e['name']} | {e['reason']} |")
    lines += ["", f"> {g['etf_proxy_note']}", "",
              "> 종목군은 검증 후보 — 투자 추천/주문 신호 아님."]
    return "\n".join(lines) + "\n"


def _md_quality(q: dict) -> str:
    lines = ["# robust 데이터 품질 검증", "",
             f"- status: **{q['status']}**",
             f"- 종목 {q['symbols_present']}/{q['symbol_count']} · 거래일 {q['trading_days']} · "
             f"total_bars {q['total_bars']}",
             f"- 기간: {q['start_date']} ~ {q['end_date']} · split_feasible={q['split_feasible']} · "
             f"regime_feasible={q['regime_feasible']}", "",
             "## 그룹별", "", "| group | expected | present | median_days | min | max |",
             "|---|---|---|---|---|---|"]
    for gname, gq in q["group_quality"].items():
        lines.append(f"| {gname} | {gq['expected']} | {gq['present']} | {gq['median_days']} | "
                     f"{gq['min_days']} | {gq['max_days']} |")
    lines += ["", "## 종목별", "", "| symbol | group | present | status | days | bars | bars/day |",
              "|---|---|---|---|---|---|---|"]
    for p in q["per_symbol"]:
        lines.append(f"| {p['symbol']} | {p['group']} | {p['present']} | {p['status']} | "
                     f"{p['day_count']} | {p['bar_count']} | {p.get('bars_per_day', 0)} |")
    if q["reasons"]:
        lines += ["", "## 사유", ""] + [f"- {r}" for r in q["reasons"]]
    lines += ["", "> read-only 품질검증 · 주문/백테스트 0건."]
    return "\n".join(lines) + "\n"


def _md_split(s: dict) -> str:
    return "\n".join([
        "# robust 시간 분할 manifest", "",
        f"- method: **{s['split_method']}** · status: **{s['split_quality_status']}**",
        f"- total_days: {s['total_days']}",
        "",
        "| 구간 | start | end | days |", "|---|---|---|---|",
        f"| Train | {s['train_start']} | {s['train_end']} | {s['train_days']} |",
        f"| Validation | {s['validation_start']} | {s['validation_end']} | {s['validation_days']} |",
        f"| Test/OOS | {s['test_start']} | {s['test_end']} | {s['test_days']} |",
        "",
        "> **Test 구간은 selector / score / 파라미터 선택에 절대 사용하지 않는다** "
        f"(test_used_for_selection={s['test_used_for_selection']}). Validation 은 과최적화 탐지용, "
        "Train 은 규칙/파라미터 개발용.",
    ] + ([""] + [f"- {r}" for r in s["reasons"]] if s["reasons"] else [])) + "\n"


def _md_regime(r: dict) -> str:
    lines = ["# robust 장세 regime 라벨 manifest", "",
             f"- proxy: **{r['proxy_kind']}** · status: **{r['regime_label_status']}** · "
             f"day_count {r['day_count']} · no_look_ahead={r['no_look_ahead']}",
             "", f"- formula: {r['formula']}", "",
             "## regime 분포", "", "| regime | days |", "|---|---|"]
    for k, v in sorted(r["counts_by_regime"].items(), key=lambda x: -x[1]):
        lines.append(f"| {k} | {v} |")
    if r["reasons"]:
        lines += ["", "## 사유", ""] + [f"- {x}" for x in r["reasons"]]
    lines += ["", "> 미래 데이터를 사용해 과거를 라벨링하지 않음(look-ahead 금지). 주문 신호 아님."]
    return "\n".join(lines) + "\n"


def _md_manifest(m: dict) -> str:
    return "\n".join([
        "# robust dataset manifest", "",
        f"- dataset: **{m['dataset_name']}** · provider {m['provider']}",
        f"- created_at: {m['created_at']}",
        f"- 요청 기간: {m['requested_period']}",
        f"- 실제 기간: {m['actual_period']}",
        f"- bar intervals: {m['bar_intervals_available']}",
        f"- 종목 {m['symbol_count']} · 5m bars {m['total_bars_5m']} · 1m subset bars "
        f"{m['total_bars_1m_subset']}",
        f"- 데이터 품질: **{m['data_quality_status']}** · 시간분할: **{m['time_split_status']}** · "
        f"regime: **{m['regime_label_status']}** · 1분봉: **{m['one_minute_availability']}**",
        "",
        f"## ready_for_robust_backtest: **{m['ready_for_robust_backtest']}**",
        f"- 다음 권장 작업: {m['next_recommended_task']}",
        "",
        "## warnings",
    ] + [f"- {w}" for w in m["warnings"]] + [
        "",
        f"> 안전: live_trading_recommendation={m['live_trading_recommendation']} · "
        f"real_order_allowed={m['real_order_allowed']} · is_live_authorization="
        f"{m['is_live_authorization']} · do_not_auto_apply={m['do_not_auto_apply']}. "
        "본 manifest 는 수집/검증 자료이며 실전 전환 승인 / 수익 보장이 아니다.",
    ]) + "\n"


def main(argv: list[str] | None = None) -> int:
    for _s in (sys.stdout, sys.stderr):
        try:
            _s.reconfigure(encoding="utf-8", errors="replace")
        except (AttributeError, ValueError):
            pass
    try:
        p = argparse.ArgumentParser(
            description="robust 데이터셋 품질검증 + manifest (read-only · 백테스트/주문 0건).")
        p.add_argument("--dir-5m", default=ROBUST_5M_DIR)
        p.add_argument("--dir-1m", default=ROBUST_1M_SUBSET_DIR)
        p.add_argument("--requested-period", default="2y (KIS 분봉 ~1년 제공 — 가능한 만큼)")
        p.add_argument("--output-dir", default=REPORT_DIR)
        p.add_argument("--write-latest", action="store_true",
                       help="endpoint 용 robust_dataset_status.json 갱신")
        args = p.parse_args(argv)

        result = run_validation(dir_5m=args.dir_5m, dir_1m=args.dir_1m,
                                requested_period=args.requested_period)
        out = Path(args.output_dir)
        out.mkdir(parents=True, exist_ok=True)

        _write_json(out / "robust_symbol_group_manifest.json", result["symbol_group"])
        _write_text(out / "robust_symbol_group_manifest.md", _md_groups(result["symbol_group"]))
        _write_json(out / "robust_dataset_quality.json", result["quality"])
        _write_text(out / "robust_dataset_quality.md", _md_quality(result["quality"]))
        _write_json(out / "robust_time_split_manifest.json", result["time_split"])
        _write_text(out / "robust_time_split_manifest.md", _md_split(result["time_split"]))
        _write_json(out / "robust_market_regime_manifest.json", result["regime"])
        _write_text(out / "robust_market_regime_manifest.md", _md_regime(result["regime"]))
        _write_json(out / "robust_dataset_manifest.json", result["manifest"])
        _write_text(out / "robust_dataset_manifest.md", _md_manifest(result["manifest"]))

        status = build_status(result)
        if args.write_latest:
            _write_json(out / "robust_dataset_status.json", status)
            print(f"[OK] status: {out / 'robust_dataset_status.json'}")

        m = result["manifest"]
        print(f"[OK] manifest: {out / 'robust_dataset_manifest.json'}")
        print(f"quality={m['data_quality_status']} split={m['time_split_status']} "
              f"regime={m['regime_label_status']} 1m={m['one_minute_availability']}")
        print(f"symbols_present={result['quality']['symbols_present']}/{m['symbol_count']} "
              f"trading_days={m['trading_days']} ready_for_robust_backtest={m['ready_for_robust_backtest']}")
        print("NOTE: 수집/검증 전용 · 백테스트/주문/실전 전환/EXE 빌드 0건 · 수익 보장 아님.")
        return 0
    except Exception as e:  # noqa: BLE001
        import traceback
        traceback.print_exc()
        _eprint(f"[ERROR] {type(e).__name__}: {e}")
        return 2


if __name__ == "__main__":
    raise SystemExit(main())

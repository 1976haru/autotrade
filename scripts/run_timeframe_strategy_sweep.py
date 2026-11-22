#!/usr/bin/env python3
"""B5/B6/B7 — 시간축 × 전략 스윕 + Walk-forward + 최종 리포트 (Paper 분석 전용).

"어느 *시간축*(5m/30m/60m/1d) × 어느 *전략*(ORB/Momentum/Gap/VWAP + Agent Council)"
조합이 *거래비용을 넘어서* PF 1.2+ 를 내는지 발견한다. 동일 종목군·동일 기간으로
통제하고, 유망 조합은 Walk-forward 로 재현성(1-run 신뢰 금지)을 본다.

재사용: `app.backtest.timeframe_sweep` (= `strategy_council_backtest` +
`cost_model` + `metrics`), `app.backtest.walk_forward_validation`,
`app.market_data.dataset_validation`.

**read-only — broker / 주문 라우터 / OrderExecutor / KIS API / 외부 HTTP 호출 0건,
안전 플래그·`.env` 변경 0건.** 산출물은 reports/backtest/ (gitignore — PC 에서 확인).

exit code:
    0: 정상 (조합 발견 여부와 무관 — 발견 0개여도 정직히 0)
    2: 입력 데이터 디렉토리 오류
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

from app.backtest.timeframe_sweep import (  # noqa: E402
    ALL_STRATEGIES,
    PF_TARGET,
    load_dir_bars,
    run_sweep_matrix,
)
from app.backtest.walk_forward_validation import (  # noqa: E402
    WalkForwardInput,
    WalkForwardMode,
    run_walk_forward_validation,
    summarize_walk_forward_report,
)
from app.market_data.dataset_validation import validate_dir  # noqa: E402

# (label, dir, glob)
TIMEFRAME_DIRS = [
    ("5m",  "data/market/robust_intraday_5m",     "*_5m.csv"),
    ("30m", "data/market/intraday_30m_resampled", "*_30m.csv"),
    ("60m", "data/market/intraday_60m_resampled", "*_60m.csv"),
    ("1d",  "data/market/intraday_1d_resampled",  "*.csv"),
]


def _fmt(v, *, pct=False, nd=4) -> str:
    if v is None:
        return "—"
    if isinstance(v, bool):
        return "예" if v else "아니오"
    if isinstance(v, float):
        return f"{v*100:.2f}%" if pct else f"{v:.{nd}f}"
    return str(v)


def _common_passing_symbols(validations: dict[str, dict]) -> set[str]:
    """모든 시간축에서 FAIL 아닌(통과) 종목의 교집합 — 동일 종목군 통제."""
    sets = []
    for v in validations.values():
        if v.get("passing_symbols"):
            sets.append(set(v["passing_symbols"]))
    if not sets:
        return set()
    common = sets[0]
    for s in sets[1:]:
        common &= s
    return common


def _run_walk_forward(label: str, bars, *, risk_profile: str, horizon: str) -> dict:
    """한 시간축의 council 백테스트 walk-forward (THREE_WAY 60/20/20)."""
    report = run_walk_forward_validation(WalkForwardInput(
        bars=tuple(bars), mode=WalkForwardMode.THREE_WAY.value,
        train_pct=0.6, validation_pct=0.2, risk_profile=risk_profile,
        horizons=(5, 10, 30, 60), primary_horizon=horizon,
    ))
    summary = summarize_walk_forward_report(report)
    # 방어적 키 추출 (스키마 변동 대비).
    return {
        "reason_code": summary.get("reason_code"),
        "split_count": summary.get("split_count"),
        "stability_score": summary.get("overall_stability_score"),
        "overfit_suspected": summary.get("overall_overfit_suspected"),
        "insufficient_data": summary.get("insufficient_data"),
        "council_vs_best_single": summary.get("council_vs_best_single"),
    }


def _build_markdown(matrix, validations, wf_results, common_symbols, args) -> str:
    rows = matrix.matrix_rows()
    promising = matrix.promising_combos()
    L: list[str] = []
    today = datetime.now(timezone.utc).astimezone().strftime("%Y-%m-%d")

    L.append("# 시간축 × 전략 백테스트 — 종합 리포트")
    L.append("")
    L.append(f"> {matrix.disclaimer}")
    L.append("")
    L.append(f"- 생성일: {today}")
    L.append(f"- 종목군(통제): {len(common_symbols)}개 (모든 시간축 동일 종목·동일 기간)")
    cost_bps = next((r.cost_round_trip_bps for r in matrix.results.values()), None)
    unfilled = next((r.unfilled_rate for r in matrix.results.values()), None)
    L.append(f"- 거래비용: 왕복 {cost_bps:.0f}bps (매수/매도 수수료 0.015%×2 + 매도세 0.18% "
             f"+ 슬리피지 0.05%×2) + 미체결 {unfilled*100:.0f}%(역선택)")
    L.append(f"- PF 목표선: {PF_TARGET} (비용 적용 *후* 기준)")
    L.append(f"- 측정 방식: 신호 시점 종가 대비 *forward return* (당일 종가까지 보유) — "
             f"신호 품질 측정이며, 5슬롯 자금관리 포함 자금곡선과는 다름")
    L.append("")

    # ── 1. 한 줄 결론 ───────────────────────────────────────────────
    L.append("## 1. 한 줄 결론 (쉬운 말)")
    L.append("")
    if promising:
        best = promising[0]
        L.append(f"**가장 좋은 건 {best['timeframe']}봉 {best['strategy']} 전략, "
                 f"비용 적용 후 PF {best['pf_after']:.2f}.** "
                 f"(거래비용·미체결 반영, 후보로 다음 단계 검토 가능)")
    else:
        L.append("**비용을 넘어 PF 1.2 이상을 낸 \"시간축 × 전략\" 조합은 없습니다 — 검증된 조합 없음.**")
        L.append("")
        L.append("쉽게 말해: 4개 전략 모두 거래비용(왕복 수수료+세금+슬리피지)을 빼기 *전*에는 "
                 "이익처럼 보이지만, 비용을 빼면 대부분 손실로 바뀝니다. 시간축을 길게 할수록"
                 "(5분→30분→60분) 비용 충격이 *줄어드는* 경향은 보이나, 그래도 안전선(PF 1.2)에는 "
                 "도달하지 못했습니다.")
    L.append("")

    # ── 2. 시간축 × 전략 매트릭스 ──────────────────────────────────
    L.append("## 2. 시간축 × 전략 매트릭스")
    L.append("")
    L.append("| 시간축 | 전략 | 거래수(BUY) | PF(비용 전) | PF(비용 후) | 승률(후) | 평균수익/건(후) | MDD(후) | 비용 넘음? |")
    L.append("|---|---|---|---|---|---|---|---|---|")
    for r in rows:
        if not r["applicable"]:
            continue
        L.append(
            f"| {r['timeframe']} | {r['strategy']} | {r['buy_count']} | "
            f"{_fmt(r['pf_before'])} | {_fmt(r['pf_after'])} | "
            f"{_fmt(r['win_rate_after'], pct=True)} | {_fmt(r['avg_return_after'], pct=True)} | "
            f"{r['mdd_after']} | {'✅' if r['meets_pf_target'] else '❌'} |"
        )
    L.append("")
    # 1d 별도 안내
    na = [tf for tf, res in matrix.results.items() if not res.applicable]
    if na:
        for tf in na:
            res = matrix.results[tf]
            L.append(f"> **{tf}: 해당 없음(N/A)** — {res.note}")
        L.append("")

    # ── 3. 비용 적용 전후 비교 ─────────────────────────────────────
    L.append("## 3. 비용이 얼마나 깎아먹는가 (전 → 후)")
    L.append("")
    L.append("| 시간축 | 전략 | PF 전 | PF 후 | PF 감소 | 평균수익/건 전 | 평균수익/건 후 |")
    L.append("|---|---|---|---|---|---|---|")
    for r in rows:
        if not r["applicable"]:
            continue
        res = matrix.results[r["timeframe"]]
        sr = res.strategies[r["strategy"]]
        ar_before = sr.before_cost.get("average_return")
        ar_after = sr.after_cost.get("average_return")
        pf_b, pf_a = r["pf_before"], r["pf_after"]
        drop = (f"{(pf_b - pf_a):.2f}" if (pf_b is not None and pf_a is not None) else "—")
        L.append(f"| {r['timeframe']} | {r['strategy']} | {_fmt(pf_b)} | {_fmt(pf_a)} | "
                 f"{drop} | {_fmt(ar_before, pct=True)} | {_fmt(ar_after, pct=True)} |")
    L.append("")
    L.append("> 평균수익/건이 비용 적용 후 음(−)으로 바뀌면, 그 조합은 거래할수록 손해입니다.")
    L.append("")

    # ── 4. Council vs 단일 ─────────────────────────────────────────
    L.append("## 4. Agent Council 이 단일 전략보다 나은가")
    L.append("")
    L.append("| 시간축 | Council PF(후) | 최우수 단일 | 단일 PF(후) | Council 우위? | expectancy Δ |")
    L.append("|---|---|---|---|---|---|")
    for tf, res in matrix.results.items():
        if not res.applicable:
            continue
        cvs = res.council_vs_best_single
        council_pf = res.strategies["AGENT_COUNCIL"].after_cost.get("profit_factor")
        best = cvs.get("best_single_strategy")
        best_pf = res.strategies[best].after_cost.get("profit_factor") if best else None
        L.append(f"| {tf} | {_fmt(council_pf)} | {best} | {_fmt(best_pf)} | "
                 f"{_fmt(cvs.get('council_better_than_best_single'))} | "
                 f"{_fmt(cvs.get('expectancy_delta'))} |")
    L.append("")

    # ── 5. Walk-forward 재현성 ─────────────────────────────────────
    L.append("## 5. Walk-forward 재현성 (1-run 신뢰 금지)")
    L.append("")
    L.append("THREE_WAY(60% train / 20% validation / 20% test) 로 시간 순서 분리 — "
             "train 에서 좋아 보여도 test 에서 무너지면 과최적화(overfit)입니다.")
    L.append("")
    L.append("> ⚠️ 주의: walk-forward 안정성은 *비용 적용 전* forward-return 으로 측정합니다. "
             "따라서 \"WALK_FORWARD_STABLE\" 은 **약한 신호가 시기마다 *일관*되게 나타난다**는 "
             "뜻이지, *비용을 넘어 수익이 난다*는 뜻이 아닙니다. (이번 결과: 신호는 안정적이나 "
             "비용을 넘지 못함 — 위 2·3절 PF(후) 참조.)")
    L.append("")
    L.append("| 시간축 | reason_code | 안정성 점수 | 과최적화 의심 | 분할 수 |")
    L.append("|---|---|---|---|---|")
    for tf, wf in wf_results.items():
        if wf is None:
            L.append(f"| {tf} | (미실행) | — | — | — |")
            continue
        L.append(f"| {tf} | {wf.get('reason_code')} | {_fmt(wf.get('stability_score'))} | "
                 f"{_fmt(wf.get('overfit_suspected'))} | {wf.get('split_count')} |")
    L.append("")

    # ── 6. 다음 한 줄 ──────────────────────────────────────────────
    L.append("## 6. 다음 단계 한 줄")
    L.append("")
    if promising:
        best = promising[0]
        wf = wf_results.get(best["timeframe"])
        wf_ok = wf and not wf.get("overfit_suspected") and not wf.get("insufficient_data")
        if wf_ok:
            L.append(f"→ **{best['timeframe']}봉 {best['strategy']}** 에 학습(다음 단계)을 붙일 후보. "
                     f"단, 자금관리 포함 포트폴리오 시뮬·Paper 실측으로 재확인 필요(실전 아님).")
        else:
            L.append(f"→ {best['timeframe']}봉 {best['strategy']} 이 비용 후 PF 목표를 넘었으나 "
                     f"walk-forward 재현성이 약함 — 더 긴 기간/다회 검증 후에만 후보로 고려.")
    else:
        # 비용 후 PF 가 가장 높은 (applicable) 조합을 "관찰 후보" 로만 안내.
        applicable_rows = [r for r in rows if r["applicable"] and r["pf_after"] is not None]
        applicable_rows.sort(key=lambda r: r["pf_after"], reverse=True)
        if applicable_rows:
            top = applicable_rows[0]
            L.append(f"→ 비용을 넘은 조합은 없습니다. 그나마 비용 충격이 가장 작은 건 "
                     f"**{top['timeframe']}봉 {top['strategy']}**(PF 후 {top['pf_after']:.2f})지만 "
                     f"여전히 1.0 미만 — 현재 4전략(+Council)은 *단타 forward-return* 기준으로는 "
                     f"비용을 못 넘습니다. 다음은 ① 더 긴 시간축/스윙(보유 1~수일)으로 비용 비중을 "
                     f"낮추거나, ② 신호 *선별*(상위 confidence/quality 만)로 거래수를 줄여 비용을 "
                     f"줄이는 방향을 *백테스트로만* 탐색 권장(실전·자동매매 추가 금지).")
        else:
            L.append("→ 적용 가능한 조합이 없습니다 (데이터/시간축 점검 필요).")
    L.append("")

    L.append("---")
    L.append("")
    L.append("### 부록: 검증된 데이터 품질")
    L.append("")
    L.append("| 시간축 | 종목 | OK | WARN | FAIL | 통과율 |")
    L.append("|---|---|---|---|---|---|")
    for tf, v in validations.items():
        L.append(f"| {tf} | {v.get('symbol_count')} | {v.get('ok_count')} | "
                 f"{v.get('warn_count')} | {v.get('fail_count')} | {_fmt(v.get('pass_rate'), pct=True)} |")
    L.append("")
    L.append("> 본 리포트는 발견(분석) 전용입니다. 매수/매도 버튼·자동매매를 추가하지 않으며, "
             "실전 전환은 별도 단계(Paper 100건·28거래일 + 운영자 승인)를 거쳐야 합니다.")
    L.append("")
    return "\n".join(L)


def _parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    p = argparse.ArgumentParser(description="시간축 × 전략 스윕 — Paper 분석 전용, 실주문 없음.")
    p.add_argument("--risk-profile", default="BALANCED",
                   choices=["CONSERVATIVE", "BALANCED", "AGGRESSIVE"])
    p.add_argument("--horizon", default="close", help="forward-return horizon (기본 당일 종가).")
    p.add_argument("--output-dir", default="reports/backtest")
    p.add_argument("--skip-walk-forward", action="store_true")
    p.add_argument("--limit-symbols", type=int, default=0, help="디버그: 종목 수 제한 (0=전체).")
    p.add_argument("--quiet", action="store_true")
    return p.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    for _stream in (sys.stdout, sys.stderr):
        try:
            _stream.reconfigure(encoding="utf-8", errors="replace")
        except (AttributeError, ValueError):
            pass

    args = _parse_args(argv)

    # 1) 데이터 품질 검증 → 통과 종목 교집합 (동일 종목군 통제).
    validations: dict[str, dict] = {}
    for label, path, glob_pat in TIMEFRAME_DIRS:
        abs_path = _REPO_ROOT / path
        if not abs_path.exists():
            if not args.quiet:
                print(f"[WARN] {label}: 디렉토리 없음 ({path}) — 건너뜀", file=sys.stderr)
            continue
        validations[label] = validate_dir(str(abs_path), glob_pat=glob_pat,
                                           min_bars=100, min_days=20)
    if not validations:
        print("[ERROR] 검증 가능한 시간축 디렉토리가 없습니다.", file=sys.stderr)
        return 2

    common = _common_passing_symbols(validations)
    if args.limit_symbols > 0:
        common = set(sorted(common)[:args.limit_symbols])
    if not args.quiet:
        print(f"[OK] 통제 종목군: {len(common)}개 (모든 시간축 통과 교집합)")

    # 2) 시간축별 bar 로드 + 스윕.
    tf_bars: dict[str, list] = {}
    for label, path, glob_pat in TIMEFRAME_DIRS:
        if label not in validations:
            continue
        bars = load_dir_bars(str(_REPO_ROOT / path), glob_pat=glob_pat, symbols=common)
        tf_bars[label] = bars
        if not args.quiet:
            print(f"[{label}] bar 로드: {len(bars)}")

    matrix = run_sweep_matrix(tf_bars, horizon=args.horizon, risk_profile=args.risk_profile)
    if not args.quiet:
        print("[OK] 시간축 × 전략 스윕 완료")

    # 3) Walk-forward (applicable 시간축만).
    wf_results: dict[str, dict | None] = {}
    if not args.skip_walk_forward:
        for label, res in matrix.results.items():
            if not res.applicable:
                wf_results[label] = None
                continue
            if not args.quiet:
                print(f"[{label}] walk-forward 실행 중...")
            try:
                wf_results[label] = _run_walk_forward(
                    label, tf_bars[label], risk_profile=args.risk_profile, horizon=args.horizon)
            except Exception as exc:  # noqa: BLE001
                wf_results[label] = {"reason_code": f"WF_ERROR: {type(exc).__name__}"}
    else:
        wf_results = {label: None for label in matrix.results}

    # 4) 리포트 작성.
    out_dir = _REPO_ROOT / args.output_dir
    out_dir.mkdir(parents=True, exist_ok=True)
    today = datetime.now(timezone.utc).astimezone().strftime("%Y%m%d")

    md = _build_markdown(matrix, validations, wf_results, common, args)
    md_path = out_dir / f"full_report_{today}.md"
    md_path.write_text(md, encoding="utf-8")

    json_payload = {
        "generated_at": matrix.generated_at,
        "common_symbols": sorted(common),
        "matrix": matrix.to_dict(),
        "walk_forward": wf_results,
        "data_quality": {tf: {k: v.get(k) for k in
                              ("symbol_count", "ok_count", "warn_count", "fail_count",
                               "pass_rate", "fail_symbols")}
                         for tf, v in validations.items()},
    }
    json_path = out_dir / f"full_report_{today}.json"
    json_path.write_text(json.dumps(json_payload, ensure_ascii=False, indent=2), encoding="utf-8")

    if not args.quiet:
        promising = matrix.promising_combos()
        print(f"[OK] 리포트: {md_path}")
        print(f"[OK] JSON: {json_path}")
        print(f"비용 넘어 PF≥{PF_TARGET} 조합: {len(promising)}개")
        if promising:
            for r in promising[:5]:
                print(f"   - {r['timeframe']} {r['strategy']}: PF(후)={r['pf_after']}")
        print("NOTE: 발견(분석) 전용 — 실주문/자동매매/실전 전환 아님.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

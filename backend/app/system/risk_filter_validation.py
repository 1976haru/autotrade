"""RISK_FILTER_ONLY 후보 OOS 검증 (CHECKLIST-04 PR, 백테스트 전용, read-only).

RANKING_REDESIGN 결론(reorder 무가치, filter-only 가 earliest 개선)에서 가장 현실적인
RISK_FILTER_ONLY 규칙을 *고정* 한 채 전체/OOS/rolling/stress 로 검증한다.

고정 규칙(RISK_FILTER_ONLY):
  - 진입 후보 중 net_edge_bps ≤ 0 (비용 초과 기대수익 없음) 제거
  - 저유동성(volume_expansion < 0.8) 제거
  - 순위 *재배열 없음* — 남은 신호를 선착순(earliest)으로 슬롯 배정
  - regime 등 look-ahead feature 미사용

본 모듈은 broker 주문 메서드 / 단일 주문 라우터 / 주문 실행기 / KIS 주문 API import
0건, 실주문 0건, read-only. **런타임/전략 자동 적용 0건** —
auto_apply_allowed=False, applied_to_runtime=False, is_live_authorization=False.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Sequence

from app.system.intrabar_realdata_backtest import (
    FIVE_MIN_DIR_DEFAULT,
    ONE_MIN_DIR_DEFAULT,
    _trade_metrics,
)
from app.system.ranking_redesign import collect_aligned_trades

_SLOTS = 5

# 고정 룰 파라미터 (검증 중 변경/최적화 금지).
RISK_FILTER_RULE = {
    "name": "RISK_FILTER_ONLY",
    "remove_if_net_edge_le": 0.0,
    "remove_if_low_liquidity": True,        # volume_expansion < 0.8
    "reorder": "NONE (earliest-first)",
    "uses_lookahead_feature": False,
}


def _kept_by_filter(t: dict) -> bool:
    """RISK_FILTER 통과 여부 — net_edge>0 AND not low_liq."""
    return t["net_edge_bps"] > 0 and not t["low_liq"]


def _select(trades: list[dict], *, days: set[str] | None, apply_filter: bool,
            slip_bps: float = 5.0) -> dict:
    """일자별 슬롯 선택(earliest) 후 집계. apply_filter=True 면 RISK_FILTER 적용."""
    by_day: dict[str, list[dict]] = {}
    for t in trades:
        if days is not None and t["day"] not in days:
            continue
        if apply_filter and not _kept_by_filter(t):
            continue
        by_day.setdefault(t["day"], []).append(t)
    sel: list[dict] = []
    for d in by_day.values():
        sel += sorted(d, key=lambda x: str(x["ts"]))[:_SLOTS]
    pnls, ents = [], []
    for t in sel:
        adj = t["pnl1"] + t["entry"] * 2 * (5.0 / 1e4) - t["entry"] * 2 * (slip_bps / 1e4)
        pnls.append(adj)
        ents.append(t["entry"])
    m = _trade_metrics(pnls, ents)
    m["selected_count"] = len(sel)
    m["selected_symbols"] = sorted({t["symbol"] for t in sel})
    return m


def _filtered_out_analysis(trades: list[dict]) -> dict[str, Any]:
    """필터가 제거한 신호 분석 — 손실 집중/놓친 수익/피한 손실."""
    removed = [t for t in trades if not _kept_by_filter(t)]
    kept = [t for t in trades if _kept_by_filter(t)]
    missed_winners = [t for t in removed if t["pnl1"] > 0]
    avoided_losers = [t for t in removed if t["pnl1"] < 0]
    rem_pnl = sum(t["pnl1"] for t in removed)
    return {
        "total_trades": len(trades),
        "kept_count": len(kept),
        "removed_count": len(removed),
        "removed_net_pnl": round(rem_pnl, 4),
        "missed_winners_count": len(missed_winners),
        "missed_winners_pnl": round(sum(t["pnl1"] for t in missed_winners), 4),
        "avoided_losers_count": len(avoided_losers),
        "avoided_losers_pnl": round(sum(t["pnl1"] for t in avoided_losers), 4),
        "loss_concentrated_in_removed": rem_pnl < 0,
    }


def _rolling(trades: list[dict], days_sorted: list[str], n_windows: int = 3) -> list[dict]:
    """rolling window 별 filter vs baseline PF 비교 (일관성 확인)."""
    size = max(1, len(days_sorted) // n_windows)
    out = []
    for i in range(0, len(days_sorted), size):
        wdays = set(days_sorted[i:i + size])
        if not wdays:
            continue
        base = _select(trades, days=wdays, apply_filter=False)
        filt = _select(trades, days=wdays, apply_filter=True)
        out.append({
            "window": f"{min(wdays)}~{max(wdays)}",
            "baseline_pf": base["profit_factor"], "filter_pf": filt["profit_factor"],
            "baseline_mdd": base["mdd_pct"], "filter_mdd": filt["mdd_pct"],
            "filter_ge_baseline": ((filt["profit_factor"] or 0) >= (base["profit_factor"] or 0)),
        })
    return out[:n_windows]


def validate_risk_filter(*, one_min_dir: Path | None = None,
                         five_min_dir: Path | None = None,
                         symbols: Sequence[str] | None = None,
                         oos_test_fraction: float = 0.34) -> dict[str, Any]:
    """RISK_FILTER_ONLY 고정 규칙 검증. read-only, 자동 적용 0건."""
    one_dir = Path(one_min_dir) if one_min_dir else ONE_MIN_DIR_DEFAULT
    five_dir = Path(five_min_dir) if five_min_dir else FIVE_MIN_DIR_DEFAULT
    trades = collect_aligned_trades(one_dir, five_dir, symbols)

    if len(trades) < 10:
        return _empty(len(trades))

    days = sorted({t["day"] for t in trades})
    n_test = max(1, int(len(days) * oos_test_fraction))
    oos_days = set(days[-n_test:])

    full_base = _select(trades, days=None, apply_filter=False)
    full_filt = _select(trades, days=None, apply_filter=True)
    oos_base = _select(trades, days=oos_days, apply_filter=False)
    oos_filt = _select(trades, days=oos_days, apply_filter=True)
    rolling = _rolling(trades, days)
    stress = {f"{s}bps": {"baseline": _select(trades, days=oos_days, apply_filter=False, slip_bps=s)["profit_factor"],
                          "filter": _select(trades, days=oos_days, apply_filter=True, slip_bps=s)["profit_factor"]}
              for s in (5.0, 7.0, 10.0, 15.0)}
    fo = _filtered_out_analysis(trades)

    verdict, recommendation = _verdict(oos_base, oos_filt, rolling, stress, fo)

    return {
        "available": True,
        "mode": "risk_filter_only_validation",
        "fixed_rule": RISK_FILTER_RULE,
        "data": {"trade_count": len(trades), "trading_days": len(days),
                 "oos_days": len(oos_days),
                 "symbols": sorted({t["symbol"] for t in trades})},
        "full_period": {"baseline": full_base, "risk_filter": full_filt},
        "oos": {"baseline": oos_base, "risk_filter": oos_filt},
        "rolling": rolling,
        "slippage_stress_oos": stress,
        "filtered_out_analysis": fo,
        "vs_earliest_first": {
            "oos_pf_delta": _d(oos_filt["profit_factor"], oos_base["profit_factor"]),
            "oos_mdd_delta": _d(oos_filt["mdd_pct"], oos_base["mdd_pct"]),
            "oos_exp_delta": _d(oos_filt["expectancy"], oos_base["expectancy"]),
        },
        "vs_current_composite_note": "reorder(composite) 는 RANKING_REDESIGN 에서 이미 실패 — "
                                     "본 검증은 filter-only 만 대상",
        "verdict": verdict,
        "recommendation": recommendation,
        "auto_apply_allowed": False,
        "applied_to_runtime": False,
        "is_live_authorization": False,
        "is_order_signal": False,
        "no_profit_guarantee": True,
        "contains_secret": False,
        "disclaimer": "연구용 백테스트이며 실전매매 권고가 아닙니다. RISK_FILTER 는 런타임/전략에 "
                      "자동 적용되지 않으며, paper rehearsal 도 별도 승인 후에만 검토합니다.",
        "next_steps": [
            "더 긴 기간(60일+) 및 다른 전략에서 filter 일관성 재확인",
            "OOS/rolling/stress 통과해도 runtime 자동 적용 금지 — 별도 승인 + paper rehearsal",
            "ORB 전략 자체가 비용 후 엣지 약하면 filter 이전에 전략 개선 우선",
        ],
    }


def _d(a, b):
    return None if a is None or b is None else round(a - b, 4)


def _verdict(base: dict, filt: dict, rolling: list[dict], stress: dict,
             fo: dict) -> tuple[str, str]:
    bpf = base["profit_factor"] or 0
    pf_up = (filt["profit_factor"] or 0) > bpf
    mdd_down = (filt["mdd_pct"] is not None and base["mdd_pct"] is not None
                and filt["mdd_pct"] < base["mdd_pct"])
    exp_up = (filt["expectancy"] is not None and base["expectancy"] is not None
              and filt["expectancy"] > base["expectancy"])
    enough = (filt["trade_count"] or 0) >= 30

    def _lvl_ok(bps: str) -> bool:
        lv = stress.get(bps) or {}
        return (lv.get("filter") is not None and lv.get("baseline") is not None
                and lv["filter"] >= lv["baseline"])
    # "slippage stress 에서도 baseline 이상" = *동일 슬리피지 수준*에서 filter≥baseline.
    stress_ok = _lvl_ok("7.0bps") and _lvl_ok("10.0bps")
    symbol_ok = len(filt.get("selected_symbols", [])) >= 5
    rolling_ok = bool(rolling) and all(w["filter_ge_baseline"] for w in rolling)

    if not (pf_up or mdd_down):
        return ("RISK_FILTER_REJECTED",
                "OOS 에서 RISK_FILTER 가 earliest-first 대비 PF/MDD 개선 없음 — 적용 불가. 자동 적용 금지.")
    if pf_up and mdd_down and exp_up and enough and stress_ok and symbol_ok and rolling_ok:
        return ("RISK_FILTER_READY_FOR_PAPER_REHEARSAL_CANDIDATE",
                "OOS + rolling + slippage stress 모두 baseline 이상, 종목 분산 OK. "
                "단 runtime 자동 적용은 여전히 금지 — 다음 단계에서만 paper rehearsal 후보로 검토.")
    if pf_up and mdd_down and exp_up and enough and stress_ok and symbol_ok:
        return ("RISK_FILTER_OOS_VALIDATED",
                "OOS 에서 PF/MDD/expectancy 개선 + slippage/종목 robust. 단 rolling 일관성 "
                "미확인(또는 표본 짧음) — 자동 적용 금지, 추가 기간 검증 필요.")
    return ("RISK_FILTER_CANDIDATE",
            "OOS PF 또는 MDD 개선(제거된 신호에 손실 집중="
            f"{fo['loss_concentrated_in_removed']}) — 후보. 추가 검증 필요, 자동 적용 금지.")


def _empty(n: int) -> dict[str, Any]:
    return {
        "available": False, "verdict": "RISK_FILTER_REJECTED",
        "reason": "INSUFFICIENT_TRADES", "trade_count": n,
        "auto_apply_allowed": False, "applied_to_runtime": False,
        "is_live_authorization": False, "no_profit_guarantee": True,
        "contains_secret": False,
        "disclaimer": "연구용 백테스트 — 거래 표본 부족. 자동 적용 안 됨, 실전매매 권고 아님.",
    }

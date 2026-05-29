"""ACUS Stage 07 — Integration (5중 교집합).

5개 에이전트 결과를 종목별로 합쳐 ``SymbolEvaluation`` 으로 만들고, **모든 기준을
통과한** FINAL_ROBUST 집합을 추출한다. 전체 universe 결과를 보고(cherry-picking 금지).

FINAL_ROBUST = (
    BacktestAgent ∈ {ROBUST, CONSISTENT}
    AND RegimeAgent == REGIME_ROBUST
    AND LiquidityAgent == LIQUID
    AND NewsAgent ∈ {NEWS_STABLE, NEWS_UNKNOWN}   # UNKNOWN 은 제외 아님(spec E2)
    AND RiskAgent == RISK_OK
)
"""

from __future__ import annotations

from typing import Any

from app.acus import types as T


def _index_by_symbol(stage: dict[str, Any]) -> dict[str, dict[str, Any]]:
    return {p["symbol"]: p for p in (stage.get("per_symbol") or []) if p.get("symbol")}


def _composite_score(bt: dict[str, Any], rg: dict[str, Any], liq: dict[str, Any],
                     nw: dict[str, Any], rk: dict[str, Any]) -> float | None:
    """advisory 0~100 합성 점수 (수익 예측 아님 — 단순 통과 마진의 가시화)."""
    def _cap_pf(pf: float | None, cap: float, scale: float) -> float:
        if pf is None:
            return 0.0
        return min(cap, max(0.0, pf / 1.5 * scale))

    bt_pts = _cap_pf(bt.get("validate_profit_factor"), 40.0, 40.0)
    rg_pts = _cap_pf(rg.get("best_regime_pf"), 25.0, 25.0)
    score_news = nw.get("score")
    nw_pts = ((score_news + 2) / 4 * 15.0) if score_news is not None else 7.5  # UNKNOWN=중립
    nw_pts = max(0.0, min(15.0, nw_pts))
    liq_pts = 10.0 if liq.get("liquidity_class") == T.LIQUID else 0.0
    rk_pts = 10.0 if rk.get("risk_class") == T.RISK_OK else 0.0
    return round(bt_pts + rg_pts + nw_pts + liq_pts + rk_pts, 2)


def run_integration(
    stage_01: dict[str, Any],
    stage_02_backtest: dict[str, Any],
    stage_03_regime: dict[str, Any],
    stage_04_liquidity: dict[str, Any],
    stage_05_news: dict[str, Any],
    stage_06_risk: dict[str, Any],
) -> dict[str, Any]:
    bt_idx = _index_by_symbol(stage_02_backtest)
    rg_idx = _index_by_symbol(stage_03_regime)
    liq_idx = _index_by_symbol(stage_04_liquidity)
    nw_idx = _index_by_symbol(stage_05_news)
    rk_idx = _index_by_symbol(stage_06_risk)

    all_symbols = sorted({p["symbol"] for p in (stage_01.get("per_symbol") or [])
                          if p.get("symbol")})

    evaluations: list[T.SymbolEvaluation] = []
    for sym in all_symbols:
        bt = bt_idx.get(sym, {})
        rg = rg_idx.get(sym, {})
        liq = liq_idx.get(sym, {})
        nw = nw_idx.get(sym, {})
        rk = rk_idx.get(sym, {})

        detail = {
            "backtest": {k: bt.get(k) for k in (
                "train_profit_factor", "validate_profit_factor", "train_trades",
                "validate_trades", "cost_adjusted_train_return",
                "cost_adjusted_validate_return", "cost_fragile")},
            "regime": {k: rg.get(k) for k in ("best_regime_pf", "regimes_evaluated")},
            "liquidity": {k: liq.get(k) for k in (
                "avg_daily_turnover_krw", "spread_proxy", "volume_cv")},
            "news": {k: nw.get(k) for k in ("score", "category", "reason")},
            "risk": {k: rk.get(k) for k in (
                "daily_sigma", "limit_hits", "max_drawdown", "single_bar_spikes")},
        }
        ev = T.SymbolEvaluation(
            symbol=sym,
            backtest_class=bt.get("backtest_class", T.BT_INSUFFICIENT),
            regime_class=rg.get("regime_class", T.REGIME_UNKNOWN),
            liquidity_class=liq.get("liquidity_class", T.LIQUIDITY_UNKNOWN),
            news_class=nw.get("news_class", T.NEWS_UNKNOWN),
            risk_class=rk.get("risk_class", T.RISK_UNKNOWN),
            score=_composite_score(bt, rg, liq, nw, rk),
            detail=detail,
        )
        evaluations.append(ev)

    final_robust = [e for e in evaluations if e.passes_all()]
    # composite score 내림차순 정렬(동점 시 심볼 오름차순) — 전체 표시, cherry-picking 아님.
    final_robust.sort(key=lambda e: (-(e.score or 0.0), e.symbol))

    return {
        "stage": "stage_07_integration",
        "universe_size": len(all_symbols),
        "final_robust_count": len(final_robust),
        "final_robust_symbols": [e.symbol for e in final_robust],
        "evaluations": [e.to_dict() for e in evaluations],
        "final_robust": [e.to_dict() for e in final_robust],
        # 단계별 funnel (전체 universe 결과)
        "funnel": {
            "stage_01_total": len(all_symbols),
            "stage_01_ready": stage_01.get("ready_count", 0),
            "stage_02_robust": len(stage_02_backtest.get("robust", [])),
            "stage_02_consistent": len(stage_02_backtest.get("consistent", [])),
            "stage_02_decayed": len(stage_02_backtest.get("decayed", [])),
            "stage_02_rejected": len(stage_02_backtest.get("rejected", [])),
            "stage_03_regime_robust": len(stage_03_regime.get("regime_robust", [])),
            "stage_04_liquid": len(stage_04_liquidity.get("liquid", [])),
            "stage_05_news_stable_or_unknown":
                len(stage_05_news.get("news_stable", []))
                + len(stage_05_news.get("news_unknown", [])),
            "stage_06_risk_ok": len(stage_06_risk.get("risk_ok", [])),
            "final_robust": len(final_robust),
        },
        "is_order_signal": False,
        "auto_apply_allowed": False,
        "applied_to_runtime": False,
        "is_live_authorization": False,
        "contains_secret": False,
        "no_profit_guarantee": True,
    }

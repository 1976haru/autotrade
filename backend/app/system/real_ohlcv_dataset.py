"""REAL-DATA-INPUT-01 — 다종목 실제/준실제 OHLCV Backtest + Walk-forward 집계 (read-only).

input-dir 의 {symbol}.csv 들을 *종목별로* 품질검증 → PASS 종목만 backtest + walk-forward →
종목별 결과 + 전체 aggregate(중앙값 PF/expectancy/MDD/WF, total_trades, Agent 효과 요약) →
caps 적용 최종 verdict. 기존 모듈(`real_ohlcv_loader`, `ohlcv_quality`,
`strategy_council_backtest`, `walk_forward_validation`, `agent_stress_test`,
`real_data_strategy`, `strategy_potential`)을 그대로 재사용한다.

본 모듈은 broker / OrderExecutor / route_order / KIS 주문 API 를 import·호출하지 않는다.
**결과가 좋아도 자동 적용 / 실전 전환 / 주문 0건.** 데이터 한계에 따라 verdict 보수 cap.
"""

from __future__ import annotations

import statistics
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from app.market_data.ohlcv_quality import check_ohlcv_quality
from app.market_data.real_ohlcv_loader import load_from_csv, load_from_dir
from app.system import strategy_potential as sp

_ORDER = [sp.STRONG, sp.CAUTIOUS, sp.RESEARCH_ONLY, sp.NOT_READY, sp.BLOCKED]


def _cap(verdict: str, ceiling: str) -> str:
    vi = _ORDER.index(verdict) if verdict in _ORDER else len(_ORDER) - 1
    ci = _ORDER.index(ceiling) if ceiling in _ORDER else len(_ORDER) - 1
    return _ORDER[max(vi, ci)]


def _median(xs: list[float]) -> float | None:
    vals = [x for x in xs if x is not None]
    return round(statistics.median(vals), 4) if vals else None


@dataclass(frozen=True)
class PerSymbolResult:
    symbol: str
    quality_status: str
    bar_count: int
    day_count: int
    trades: int
    profit_factor: float | None
    expectancy: float | None
    max_drawdown: float | None
    backtest_score: float | None
    walk_forward_score: float | None
    agent_value_verdict: str
    verdict: str
    included: bool
    reason: str = ""


@dataclass(frozen=True)
class RealOhlcvDatasetReport:
    generated_at: str
    data_source: str
    real_data_used: bool
    sample_fixture_only: bool
    symbols_count: int
    bars_count: int
    days_count: int
    trades_count: int
    quality: dict[str, Any]
    overall_verdict: str
    overall_score: float | None
    backtest_score: float | None
    walk_forward_score: float | None
    stress_score: float | None
    agent_value_score: float | None
    data_sufficiency_score: float | None
    agent_value_verdict: str
    paper_sample_class: str
    # dataset-specific
    pass_symbols: tuple[str, ...] = ()
    warn_symbols: tuple[str, ...] = ()
    blocked_symbols: tuple[str, ...] = ()
    per_symbol: tuple[dict[str, Any], ...] = ()
    total_trades: int = 0
    median_profit_factor: float | None = None
    median_expectancy: float | None = None
    median_mdd: float | None = None
    median_walk_forward_score: float | None = None
    agent_value_summary: str = "AGENT_VALUE_INSUFFICIENT_SAMPLE"
    agent_helped_symbols: tuple[str, ...] = ()
    agent_hurt_symbols: tuple[str, ...] = ()
    agent_no_trade_symbols: tuple[str, ...] = ()
    favorable_conditions: tuple[str, ...] = ()
    dangerous_conditions: tuple[str, ...] = ()
    strategy_strengths: tuple[str, ...] = ()
    strategy_weaknesses: tuple[str, ...] = ()
    agent_helped_where: tuple[str, ...] = ()
    agent_hurt_where: tuple[str, ...] = ()
    tuning_candidates: tuple[str, ...] = ()
    next_steps: tuple[str, ...] = ()
    kis_historical_available: bool = False
    do_not_auto_apply: bool = True
    auto_apply_allowed: bool = False
    is_live_authorization: bool = False
    is_order_signal: bool = False
    contains_secret: bool = False
    disclaimer: str = (
        "다종목 실제/준실제 데이터 Backtest+Walk-forward 집계이며 자동 적용 / 실전 전환 / "
        "주문 신호가 아니다. 수익을 보장하지 않는다. Paper 100건 + 28거래일 + 운영자 승인 "
        "전까지 실전 검토 불가. KIS historical 시세 API 미구현 — CSV / yfinance 사용."
    )
    _extra: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not self.do_not_auto_apply:
            raise ValueError("do_not_auto_apply must be True")
        if self.auto_apply_allowed or self.is_live_authorization or self.is_order_signal:
            raise ValueError("unsafe invariant True")
        if self.contains_secret or self.kis_historical_available:
            raise ValueError("contains_secret / kis_historical_available must be False")
        if self.overall_verdict not in _ORDER:
            raise ValueError(f"invalid verdict: {self.overall_verdict}")


def _council_metrics(bt: dict[str, Any] | None) -> dict[str, Any]:
    c = (bt or {}).get("council") or {}
    perf = c.get("performance") or {}
    counts = c.get("final_action_counts") or {}
    sig = perf.get("signal_counts") or {}
    return {
        "profit_factor": perf.get("profit_factor"),
        "expectancy": perf.get("expectancy"),
        "max_drawdown": perf.get("max_drawdown"),
        "trades": int(counts.get("BUY", sig.get("BUY", 0)) or 0),
    }


def _run_symbol(path: Path, *, min_trades: int, min_days: int) -> PerSymbolResult:
    from app.backtest.strategy_council_backtest import (
        BacktestInput,
        run_strategy_council_backtest,
        summarize_backtest_report,
    )
    from app.backtest.walk_forward_validation import (
        WalkForwardInput,
        run_walk_forward_validation,
        summarize_walk_forward_report,
    )

    lo = load_from_csv(path)
    bars = list(lo.bars)
    symbol = (lo.symbols[0] if lo.symbols else path.stem)
    q = check_ohlcv_quality(bars, min_bars=min_trades, min_days=min_days)
    status = "PASS" if q.status == "OK" else q.status
    if not bars or not q.sufficient_for_backtest or q.status == "FAIL":
        return PerSymbolResult(
            symbol, status, q.bar_count, q.day_count, 0, None, None, None, None, None,
            "AGENT_VALUE_INSUFFICIENT_SAMPLE", sp.BLOCKED, included=False,
            reason="; ".join(q.reasons[:2]) or "품질 미달")

    bt = summarize_backtest_report(run_strategy_council_backtest(BacktestInput(bars=tuple(bars))))
    try:
        wf = summarize_walk_forward_report(
            run_walk_forward_validation(WalkForwardInput(bars=tuple(bars))))
    except Exception:  # noqa: BLE001
        wf = {"insufficient_data": True}
    pot = sp.evaluate_strategy_potential(sp.StrategyPotentialInputs(
        backtest=bt, walk_forward=wf, has_real_data=lo.real_data_used))
    m = _council_metrics(bt)
    return PerSymbolResult(
        symbol=symbol, quality_status=status, bar_count=q.bar_count, day_count=q.day_count,
        trades=m["trades"], profit_factor=m["profit_factor"], expectancy=m["expectancy"],
        max_drawdown=m["max_drawdown"], backtest_score=pot.backtest_score,
        walk_forward_score=pot.walk_forward_score, agent_value_verdict=pot.agent_value_verdict,
        verdict=pot.overall_verdict, included=True)


def evaluate_real_ohlcv_dataset(
    input_dir: str | Path,
    *,
    symbols: list[str] | None = None,
    min_trades: int = 100,
    min_days: int = 28,
    strict: bool = False,
    generated_at: str | None = None,
) -> RealOhlcvDatasetReport:
    gen = generated_at or datetime.now(timezone.utc).isoformat()
    d = Path(input_dir)
    files = sorted(d.glob("*.csv")) if d.is_dir() else []
    if symbols:
        wanted = {s.lower() for s in symbols}
        files = [f for f in files if f.stem.lower() in wanted]

    per: list[PerSymbolResult] = []
    for f in files:
        per.append(_run_symbol(f, min_trades=min_trades, min_days=min_days))

    pass_syms = tuple(p.symbol for p in per if p.included and p.quality_status == "PASS")
    warn_syms = tuple(p.symbol for p in per if p.included and p.quality_status == "WARN")
    blocked_syms = tuple(p.symbol for p in per if not p.included)
    included = [p for p in per if p.included]

    # 전체 결합 백테스트 (base scores + 종합 potential).
    combined = load_from_dir(d, symbols=symbols)
    from app.stress_test.agent_stress_test import (
        run_agent_stress_test,
        summarize_stress_report,
    )
    from app.system.real_data_strategy import evaluate_real_data_strategy
    # 결합 평가는 PASS/WARN 종목 전체 bar 로 (real_data_strategy 재사용).
    combined_report = evaluate_real_data_strategy(
        combined, run_backtest=True, run_wf=True, run_stress=True,
        min_trades=min_trades, min_days=min_days, strict=strict, generated_at=gen)
    _ = run_agent_stress_test  # (stress 는 combined_report 내부에서 실행)
    _ = summarize_stress_report

    total_trades = sum(p.trades for p in included)
    median_pf = _median([p.profit_factor for p in included])
    median_exp = _median([p.expectancy for p in included])
    median_mdd = _median([p.max_drawdown for p in included])
    median_wf = _median([p.walk_forward_score for p in included])

    helped = tuple(p.symbol for p in included
                   if p.agent_value_verdict in ("AGENT_ADDS_VALUE", "AGENT_RISK_REDUCTION_VALUE"))
    hurt = tuple(p.symbol for p in included if p.agent_value_verdict == "AGENT_UNDERPERFORMS")
    no_trade = tuple(p.symbol for p in included if p.trades == 0)

    n_inc = max(1, len(included))
    # 거래가 거의 없으면 Agent 효과 판단 불가(INSUFFICIENT)가 우선.
    if not included or len(no_trade) >= n_inc * 0.5:
        agent_summary = "AGENT_VALUE_INSUFFICIENT_SAMPLE"
    elif helped and len(helped) > len(hurt):
        agent_summary = "AGENT_ADDS_VALUE"
    elif hurt and len(hurt) >= n_inc * 0.5:
        agent_summary = "AGENT_UNDERPERFORMS"
    else:
        agent_summary = "AGENT_MIXED"

    # ---- 종합 verdict + caps ----
    verdict = combined_report.overall_verdict
    if len(pass_syms) + len(warn_syms) == 0:
        verdict = sp.BLOCKED  # 사용 가능한 PASS 데이터 없음
    if len(blocked_syms) > len(per) / 2 and per:
        verdict = _cap(verdict, sp.NOT_READY)  # 품질 FAIL 과반
    if combined_report.sample_fixture_only:
        verdict = _cap(verdict, sp.RESEARCH_ONLY)
    if total_trades < min_trades:
        verdict = _cap(verdict, sp.RESEARCH_ONLY)
    if (median_wf or 0) < 40:
        verdict = _cap(verdict, sp.RESEARCH_ONLY)
    if hurt and len(hurt) >= n_inc * 0.5:
        verdict = _cap(verdict, sp.RESEARCH_ONLY)

    next_steps = list(combined_report.next_steps)
    if blocked_syms:
        next_steps.insert(0, f"품질 FAIL 종목 제외: {', '.join(blocked_syms)} (무결한 CSV 재확보)")
    next_steps.append("Paper 모의 100건 + 28거래일 표본 확보 전까지 실전 검토 불가")

    quality_agg = {
        "status": "FAIL" if blocked_syms and not pass_syms else (
            "WARN" if (warn_syms or blocked_syms) else "OK"),
        "pass": len(pass_syms), "warn": len(warn_syms), "blocked": len(blocked_syms),
    }

    return RealOhlcvDatasetReport(
        generated_at=gen,
        data_source=combined_report.data_source,
        real_data_used=combined_report.real_data_used,
        sample_fixture_only=combined_report.sample_fixture_only,
        symbols_count=len(per),
        bars_count=combined_report.bars_count,
        days_count=combined_report.days_count,
        trades_count=total_trades,
        quality=quality_agg,
        overall_verdict=verdict,
        overall_score=combined_report.overall_score,
        backtest_score=combined_report.backtest_score,
        walk_forward_score=combined_report.walk_forward_score,
        stress_score=combined_report.stress_score,
        agent_value_score=combined_report.agent_value_score,
        data_sufficiency_score=combined_report.data_sufficiency_score,
        agent_value_verdict=combined_report.agent_value_verdict,
        paper_sample_class=combined_report.paper_sample_class,
        pass_symbols=pass_syms,
        warn_symbols=warn_syms,
        blocked_symbols=blocked_syms,
        per_symbol=tuple(_psr_to_dict(p) for p in per),
        total_trades=total_trades,
        median_profit_factor=median_pf,
        median_expectancy=median_exp,
        median_mdd=median_mdd,
        median_walk_forward_score=median_wf,
        agent_value_summary=agent_summary,
        agent_helped_symbols=helped,
        agent_hurt_symbols=hurt,
        agent_no_trade_symbols=no_trade,
        favorable_conditions=combined_report.favorable_conditions,
        dangerous_conditions=combined_report.dangerous_conditions,
        strategy_strengths=combined_report.strategy_strengths,
        strategy_weaknesses=combined_report.strategy_weaknesses,
        agent_helped_where=combined_report.agent_helped_where,
        agent_hurt_where=combined_report.agent_hurt_where,
        tuning_candidates=combined_report.tuning_candidates,
        next_steps=tuple(next_steps),
    )


def _psr_to_dict(p: PerSymbolResult) -> dict[str, Any]:
    return {
        "symbol": p.symbol, "quality_status": p.quality_status, "bar_count": p.bar_count,
        "day_count": p.day_count, "trades": p.trades, "profit_factor": p.profit_factor,
        "expectancy": p.expectancy, "max_drawdown": p.max_drawdown,
        "backtest_score": p.backtest_score, "walk_forward_score": p.walk_forward_score,
        "agent_value_verdict": p.agent_value_verdict, "verdict": p.verdict,
        "included": p.included, "reason": p.reason,
    }


def to_dict(r: RealOhlcvDatasetReport) -> dict[str, Any]:
    return {
        "generated_at": r.generated_at, "data_source": r.data_source,
        "real_data_used": r.real_data_used, "sample_fixture_only": r.sample_fixture_only,
        "symbols_count": r.symbols_count, "bars_count": r.bars_count,
        "days_count": r.days_count, "trades_count": r.trades_count, "quality": r.quality,
        "overall_verdict": r.overall_verdict, "overall_score": r.overall_score,
        "backtest_score": r.backtest_score, "walk_forward_score": r.walk_forward_score,
        "stress_score": r.stress_score, "agent_value_score": r.agent_value_score,
        "data_sufficiency_score": r.data_sufficiency_score,
        "agent_value_verdict": r.agent_value_verdict, "paper_sample_class": r.paper_sample_class,
        "pass_symbols": list(r.pass_symbols), "warn_symbols": list(r.warn_symbols),
        "blocked_symbols": list(r.blocked_symbols), "per_symbol": list(r.per_symbol),
        "total_trades": r.total_trades, "median_profit_factor": r.median_profit_factor,
        "median_expectancy": r.median_expectancy, "median_mdd": r.median_mdd,
        "median_walk_forward_score": r.median_walk_forward_score,
        "agent_value_summary": r.agent_value_summary,
        "agent_helped_symbols": list(r.agent_helped_symbols),
        "agent_hurt_symbols": list(r.agent_hurt_symbols),
        "agent_no_trade_symbols": list(r.agent_no_trade_symbols),
        "favorable_conditions": list(r.favorable_conditions),
        "dangerous_conditions": list(r.dangerous_conditions),
        "strategy_strengths": list(r.strategy_strengths),
        "strategy_weaknesses": list(r.strategy_weaknesses),
        "agent_helped_where": list(r.agent_helped_where),
        "agent_hurt_where": list(r.agent_hurt_where),
        "tuning_candidates": list(r.tuning_candidates), "next_steps": list(r.next_steps),
        "kis_historical_available": r.kis_historical_available,
        "do_not_auto_apply": r.do_not_auto_apply, "auto_apply_allowed": r.auto_apply_allowed,
        "is_live_authorization": r.is_live_authorization, "is_order_signal": r.is_order_signal,
        "contains_secret": r.contains_secret, "disclaimer": r.disclaimer,
    }


def render_markdown(r: RealOhlcvDatasetReport) -> str:
    def _f(x):
        return "평가불가" if x is None else f"{x:.2f}"
    lines = [
        "# REAL-DATA-INPUT-01 — 다종목 실제/준실제 데이터 Backtest+Walk-forward 집계",
        "",
        "> 자동 적용 아님 · 실전 승인 아님 · 수익 보장 아님. KIS historical 미구현(CSV/yfinance).",
        "",
        f"- data_source={r.data_source} · real_data_used={r.real_data_used} · "
        f"sample_fixture_only={r.sample_fixture_only}",
        f"- 종목 {r.symbols_count} · PASS {len(r.pass_symbols)} · WARN {len(r.warn_symbols)} · "
        f"BLOCKED {len(r.blocked_symbols)}",
        f"- overall_verdict: **{r.overall_verdict}** · overall_score {_f(r.overall_score)}",
        f"- total_trades {r.total_trades} · median PF {_f(r.median_profit_factor)} · "
        f"median expectancy {_f(r.median_expectancy)} · median MDD {_f(r.median_mdd)} · "
        f"median WF {_f(r.median_walk_forward_score)}",
        f"- Agent 효과: **{r.agent_value_summary}** "
        f"(도움 {list(r.agent_helped_symbols)} / 방해 {list(r.agent_hurt_symbols)} / "
        f"무진입 {list(r.agent_no_trade_symbols)})",
        f"- paper_sample: {r.paper_sample_class}",
        "",
        "## 종목별",
        "| symbol | quality | trades | PF | expectancy | WF | agent | verdict | included |",
        "|---|---|---|---|---|---|---|---|---|",
    ]
    for p in r.per_symbol:
        lines.append(
            f"| {p['symbol']} | {p['quality_status']} | {p['trades']} | "
            f"{_f(p['profit_factor'])} | {_f(p['expectancy'])} | {_f(p['walk_forward_score'])} | "
            f"{p['agent_value_verdict']} | {p['verdict']} | {'예' if p['included'] else '제외'} |")
    lines += ["", "## 추천 다음 단계",
              *([f"- {s}" for s in r.next_steps] or ["- (없음)"]),
              "", f"> {r.disclaimer}"]
    return "\n".join(lines) + "\n"

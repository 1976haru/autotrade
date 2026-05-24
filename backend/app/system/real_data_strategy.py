"""REAL-DATA-STRATEGY-01 — 실제/준실제 OHLCV 기반 전략 가능성 검증 (advisory, read-only).

sample fixture 가 *아니라* 실제/준실제 OHLCV 로 매매기법 + Agent Council 전략을 검증한다.
기존 인프라를 그대로 재사용한다:
- 로딩/품질: `app.market_data.real_ohlcv_loader` + `app.market_data.ohlcv_quality`
- 백테스트: `run_strategy_council_backtest` (#46)
- Walk-forward: `run_walk_forward_validation` (#47)
- Stress: `run_agent_stress_test` (#48)
- 종합 판정: `evaluate_strategy_potential` (STRATEGY-VALIDATION-01)

본 모듈은 broker / OrderExecutor / route_order / KIS 주문 API 를 import·호출하지 않는다.
**결과가 좋아도 자동 적용 / 실전 전환 / 주문 0건.** 데이터 한계에 따라 verdict 를 보수적으로
cap 한다 (sample fixture → 최대 RESEARCH_ONLY, 거래 수 부족 → 최대 CAUTIOUS, 품질 FAIL → BLOCKED).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

from app.market_data.ohlcv_quality import check_ohlcv_quality
from app.market_data.ohlcv_quality import to_dict as quality_to_dict
from app.market_data.real_ohlcv_loader import LoadedOhlcv
from app.system import strategy_potential as sp

# verdict 우선순위 (best → worst).
_ORDER = [sp.STRONG, sp.CAUTIOUS, sp.RESEARCH_ONLY, sp.NOT_READY, sp.BLOCKED]


def _cap(verdict: str, ceiling: str) -> str:
    """verdict 와 ceiling 중 *더 나쁜* 것을 반환 (ceiling 보다 좋을 수 없게)."""
    vi = _ORDER.index(verdict) if verdict in _ORDER else len(_ORDER) - 1
    ci = _ORDER.index(ceiling) if ceiling in _ORDER else len(_ORDER) - 1
    return _ORDER[max(vi, ci)]


@dataclass(frozen=True)
class RealDataStrategyReport:
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
    favorable_conditions: tuple[str, ...] = ()
    dangerous_conditions: tuple[str, ...] = ()
    strategy_strengths: tuple[str, ...] = ()
    strategy_weaknesses: tuple[str, ...] = ()
    agent_helped_where: tuple[str, ...] = ()
    agent_hurt_where: tuple[str, ...] = ()
    tuning_candidates: tuple[str, ...] = ()
    next_steps: tuple[str, ...] = ()
    kis_historical_available: bool = False
    potential: dict[str, Any] = field(default_factory=dict)
    # advisory invariants.
    do_not_auto_apply: bool = True
    auto_apply_allowed: bool = False
    is_live_authorization: bool = False
    is_order_signal: bool = False
    contains_secret: bool = False
    disclaimer: str = (
        "실제/준실제 데이터 기준 전략 가능성 평가이며 자동 적용 / 실전 전환 승인 / 주문 "
        "신호가 아니다. 수익을 보장하지 않는다. 실전 검토는 Paper 100건 + 28거래일 이상 + "
        "운영자 승인이 필요하다. KIS historical 시세 API 는 미구현 — CSV / yfinance 사용."
    )

    def __post_init__(self) -> None:
        if not self.do_not_auto_apply:
            raise ValueError("do_not_auto_apply must be True")
        if self.auto_apply_allowed:
            raise ValueError("auto_apply_allowed must be False")
        if self.is_live_authorization:
            raise ValueError("is_live_authorization must be False")
        if self.is_order_signal:
            raise ValueError("is_order_signal must be False")
        if self.contains_secret:
            raise ValueError("contains_secret must be False")
        if self.kis_historical_available:
            raise ValueError("KIS historical candle API is not implemented")
        if self.overall_verdict not in _ORDER:
            raise ValueError(f"invalid verdict: {self.overall_verdict}")


def _council_perf(bt: dict[str, Any] | None) -> dict[str, Any] | None:
    if not bt:
        return None
    c = bt.get("council")
    if c and c.get("performance"):
        return c["performance"]
    return None


def _trades_count(bt: dict[str, Any] | None) -> int:
    """council 진입(BUY) 신호 수 = 평가된 거래 수 근사."""
    c = (bt or {}).get("council") or {}
    counts = c.get("final_action_counts") or {}
    perf = c.get("performance") or {}
    sig = perf.get("signal_counts") or {}
    return int(counts.get("BUY", sig.get("BUY", 0)) or 0)


def _regime_conditions(bt: dict[str, Any] | None) -> tuple[list[str], list[str]]:
    favorable: list[str] = []
    dangerous: list[str] = []
    perf = _council_perf(bt)
    if not perf:
        return favorable, dangerous
    for regime, stats in (perf.get("by_market_regime") or {}).items():
        wr = stats.get("win_rate")
        if wr is None:
            continue
        if wr >= 0.5:
            favorable.append(f"{regime}(win_rate={wr:.2f})")
        elif wr < 0.4:
            dangerous.append(f"{regime}(win_rate={wr:.2f})")
    for phase, stats in (perf.get("by_time_phase") or {}).items():
        wr = stats.get("win_rate")
        if wr is not None and wr >= 0.5:
            favorable.append(f"time_phase={phase}(win_rate={wr:.2f})")
        elif wr is not None and wr < 0.4:
            dangerous.append(f"time_phase={phase}(win_rate={wr:.2f})")
    return favorable, dangerous


def evaluate_real_data_strategy(
    loaded: LoadedOhlcv,
    *,
    run_backtest: bool = True,
    run_wf: bool = True,
    run_stress: bool = True,
    min_trades: int = 100,
    min_days: int = 28,
    strict: bool = False,
    generated_at: str | None = None,
) -> RealDataStrategyReport:
    gen = generated_at or datetime.now(timezone.utc).isoformat()
    bars = list(loaded.bars)
    quality = check_ohlcv_quality(bars, min_bars=min_trades, min_days=min_days)

    bt = wf = st = None
    if bars and quality.sufficient_for_backtest:
        if run_backtest:
            from app.backtest.strategy_council_backtest import (
                BacktestInput,
                run_strategy_council_backtest,
                summarize_backtest_report,
            )
            bt = summarize_backtest_report(
                run_strategy_council_backtest(BacktestInput(bars=tuple(bars))))
        if run_wf:
            from app.backtest.walk_forward_validation import (
                WalkForwardInput,
                run_walk_forward_validation,
                summarize_walk_forward_report,
            )
            try:
                wf = summarize_walk_forward_report(
                    run_walk_forward_validation(WalkForwardInput(bars=tuple(bars))))
            except Exception:  # noqa: BLE001
                wf = {"insufficient_data": True,
                      "reason_code": "WALK_FORWARD_INSUFFICIENT_DATA"}
    if run_stress:
        from app.stress_test.agent_stress_test import (
            run_agent_stress_test,
            summarize_stress_report,
        )
        st = summarize_stress_report(run_agent_stress_test())

    potential = sp.evaluate_strategy_potential(sp.StrategyPotentialInputs(
        backtest=bt, walk_forward=wf, stress=st,
        has_real_data=loaded.real_data_used), generated_at=gen)
    pot = sp.to_dict(potential)

    trades = _trades_count(bt)
    favorable, dangerous = _regime_conditions(bt)

    # verdict cap (real-data 특화 보수적 제한).
    verdict = potential.overall_verdict
    if quality.status == "FAIL":
        verdict = sp.BLOCKED
    if loaded.sample_fixture_only:
        verdict = _cap(verdict, sp.RESEARCH_ONLY)
    if loaded.real_data_used and trades < min_trades:
        verdict = _cap(verdict, sp.CAUTIOUS)
    if strict and quality.status == "WARN":
        verdict = _cap(verdict, sp.RESEARCH_ONLY)

    # agent helped / hurt.
    agent_helped: list[str] = []
    agent_hurt: list[str] = []
    if potential.agent_value_verdict == "AGENT_ADDS_VALUE":
        agent_helped.append("Agent Council 이 단일 전략 대비 수익/리스크 개선")
    elif potential.agent_value_verdict == "AGENT_RISK_REDUCTION_VALUE":
        agent_helped.append("Agent Council 이 리스크(MDD/연속손실) 축소")
    elif potential.agent_value_verdict == "AGENT_UNDERPERFORMS":
        agent_hurt.append("Agent Council 이 best single 전략보다 성과 저조")

    next_steps: list[str] = []
    if quality.status == "FAIL":
        next_steps.append("OHLCV 데이터 품질 문제 해결(무결한 실제 CSV 제공) 후 재실행")
    if loaded.sample_fixture_only:
        next_steps.append("sample fixture 가 아닌 실제/준실제 OHLCV 로 재실행")
    if trades < min_trades:
        next_steps.append(f"거래 표본 부족(trades={trades} < {min_trades}) — 더 긴 기간/다종목 데이터")
    next_steps.append("Paper 모의 운영으로 100건 + 28거래일 표본 축적 (실전 검토 전제)")
    next_steps.append("튜닝 후보는 운영자 검토 + 별도 PR + 재백테스트 (자동 적용 금지)")

    mf = potential.method_fit or {}
    return RealDataStrategyReport(
        generated_at=gen,
        data_source=loaded.data_source,
        real_data_used=loaded.real_data_used,
        sample_fixture_only=loaded.sample_fixture_only,
        symbols_count=len(loaded.symbols),
        bars_count=quality.bar_count,
        days_count=quality.day_count,
        trades_count=trades,
        quality=quality_to_dict(quality),
        overall_verdict=verdict,
        overall_score=potential.overall_strategy_potential_score,
        backtest_score=potential.backtest_score,
        walk_forward_score=potential.walk_forward_score,
        stress_score=potential.stress_resilience_score,
        agent_value_score=potential.agent_value_score,
        data_sufficiency_score=potential.data_sufficiency_score,
        agent_value_verdict=potential.agent_value_verdict,
        paper_sample_class=potential.paper_sample_class,
        favorable_conditions=tuple(favorable),
        dangerous_conditions=tuple(dangerous),
        strategy_strengths=tuple(potential.strengths),
        strategy_weaknesses=tuple(potential.weaknesses) + tuple(mf.get("method_weaknesses", [])),
        agent_helped_where=tuple(agent_helped),
        agent_hurt_where=tuple(agent_hurt),
        tuning_candidates=tuple(mf.get("recommended_tuning_candidates", [])),
        next_steps=tuple(next_steps),
        potential=pot,
    )


def to_dict(r: RealDataStrategyReport) -> dict[str, Any]:
    return {
        "generated_at": r.generated_at,
        "data_source": r.data_source,
        "real_data_used": r.real_data_used,
        "sample_fixture_only": r.sample_fixture_only,
        "symbols_count": r.symbols_count,
        "bars_count": r.bars_count,
        "days_count": r.days_count,
        "trades_count": r.trades_count,
        "quality": r.quality,
        "overall_verdict": r.overall_verdict,
        "overall_score": r.overall_score,
        "backtest_score": r.backtest_score,
        "walk_forward_score": r.walk_forward_score,
        "stress_score": r.stress_score,
        "agent_value_score": r.agent_value_score,
        "data_sufficiency_score": r.data_sufficiency_score,
        "agent_value_verdict": r.agent_value_verdict,
        "paper_sample_class": r.paper_sample_class,
        "favorable_conditions": list(r.favorable_conditions),
        "dangerous_conditions": list(r.dangerous_conditions),
        "strategy_strengths": list(r.strategy_strengths),
        "strategy_weaknesses": list(r.strategy_weaknesses),
        "agent_helped_where": list(r.agent_helped_where),
        "agent_hurt_where": list(r.agent_hurt_where),
        "tuning_candidates": list(r.tuning_candidates),
        "next_steps": list(r.next_steps),
        "kis_historical_available": r.kis_historical_available,
        "do_not_auto_apply": r.do_not_auto_apply,
        "auto_apply_allowed": r.auto_apply_allowed,
        "is_live_authorization": r.is_live_authorization,
        "is_order_signal": r.is_order_signal,
        "contains_secret": r.contains_secret,
        "disclaimer": r.disclaimer,
        "potential": r.potential,
    }


def render_markdown(r: RealDataStrategyReport) -> str:
    def _f(x: float | None) -> str:
        return "평가불가" if x is None else f"{x:.1f}"
    lines = [
        "# REAL-DATA-STRATEGY-01 — 실제/준실제 데이터 전략 가능성 검증",
        "",
        "> 자동 적용 아님 · 실전 승인 아님 · 수익 보장 아님. KIS historical 미구현(CSV/yfinance).",
        "",
        f"- data_source: **{r.data_source}** · real_data_used={r.real_data_used} · "
        f"sample_fixture_only={r.sample_fixture_only}",
        f"- 종목 {r.symbols_count} · bar {r.bars_count} · 거래일 {r.days_count} · "
        f"거래수(council BUY) {r.trades_count}",
        f"- 데이터 품질: {r.quality.get('status')} ({', '.join(r.quality.get('reasons', [])[:3])})",
        f"- overall_verdict: **{r.overall_verdict}** · overall_score: {_f(r.overall_score)}",
        f"- backtest={_f(r.backtest_score)} · walk_forward={_f(r.walk_forward_score)} · "
        f"stress={_f(r.stress_score)} · agent_value={_f(r.agent_value_score)} "
        f"({r.agent_value_verdict}) · data_sufficiency={_f(r.data_sufficiency_score)}",
        f"- paper_sample: {r.paper_sample_class}",
        "",
        "## 강한 시장국면/시간대",
        *([f"- {c}" for c in r.favorable_conditions] or ["- (데이터 부족)"]),
        "",
        "## 위험 시장국면/시간대",
        *([f"- {c}" for c in r.dangerous_conditions] or ["- (데이터 부족)"]),
        "",
        "## Agent 도움/방해",
        *([f"- 도움: {c}" for c in r.agent_helped_where] or []),
        *([f"- 방해: {c}" for c in r.agent_hurt_where] or []),
        "",
        "## 추천 다음 단계",
        *([f"- {c}" for c in r.next_steps] or ["- (없음)"]),
        "",
        f"> {r.disclaimer}",
    ]
    return "\n".join(lines) + "\n"

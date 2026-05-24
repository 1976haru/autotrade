"""INTRADAY-DATA-01 — 분봉 데이터 기반 단타 전략 검증 (advisory, read-only).

분봉 OHLCV 디렉토리({symbol}.csv)를 *분봉 품질검증* → PASS 종목만 backtest +
walk-forward + stress + Agent vs 단일전략 → 종목별/전체 집계 + 단타 전용 caps.

기존 인프라 재사용: `intraday_ohlcv`(품질/로더) + `strategy_council_backtest`(#46) +
`walk_forward_validation`(#47) + `agent_stress_test`(#48) + `strategy_potential`.

본 모듈은 broker / OrderExecutor / route_order / KIS 주문 API 를 import·호출하지 않는다.
**결과가 좋아도 자동 적용 / 실전 전환 / 주문 0건.** 단타 표본 한계에 따라 verdict 보수 cap.
"""

from __future__ import annotations

import statistics
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from app.market_data.intraday_ohlcv import check_intraday_quality, load_intraday_csv
from app.system import strategy_potential as sp

_ORDER = [sp.STRONG, sp.CAUTIOUS, sp.RESEARCH_ONLY, sp.NOT_READY, sp.BLOCKED]

# 단타(분봉) 표본 기준 — 일봉보다 짧은 기간 허용 (INTRADAY-DATA-01 §5).
MIN_BARS = 100
MIN_DAYS = 5
TRADES_OBSERVE = 30
TRADES_EVALUABLE = 100


def _cap(verdict: str, ceiling: str) -> str:
    vi = _ORDER.index(verdict) if verdict in _ORDER else len(_ORDER) - 1
    ci = _ORDER.index(ceiling) if ceiling in _ORDER else len(_ORDER) - 1
    return _ORDER[max(vi, ci)]


def _median(xs: list[float]) -> float | None:
    vals = [x for x in xs if x is not None]
    return round(statistics.median(vals), 4) if vals else None


@dataclass(frozen=True)
class IntradayStrategyReport:
    generated_at: str
    intraday_data_used: bool
    bar_size_minutes: float | None
    symbols_count: int
    total_bars: int
    total_days: int
    total_trades: int
    pass_symbols: tuple[str, ...]
    warn_symbols: tuple[str, ...]
    blocked_symbols: tuple[str, ...]
    per_symbol: tuple[dict[str, Any], ...]
    overall_verdict: str
    overall_score: float | None
    win_rate: float | None
    profit_factor: float | None
    expectancy: float | None
    max_drawdown: float | None
    walk_forward_score: float | None
    stress_score: float | None
    agent_value_score: float | None
    agent_value_summary: str
    agent_no_trade_symbols: tuple[str, ...] = ()
    agent_helped_symbols: tuple[str, ...] = ()
    agent_hurt_symbols: tuple[str, ...] = ()
    paper_sample_class: str = "PAPER_NO_TRADES_YET"
    next_steps: tuple[str, ...] = ()
    kis_intraday_available: bool = False
    do_not_auto_apply: bool = True
    auto_apply_allowed: bool = False
    is_live_authorization: bool = False
    is_order_signal: bool = False
    contains_secret: bool = False
    disclaimer: str = (
        "분봉(intraday) 데이터 기준 단타 전략 가능성 평가이며 자동 적용 / 실전 전환 승인 / "
        "주문 신호가 아니다. 수익을 보장하지 않는다. 실전 검토는 Paper 100건 + 28거래일 + "
        "운영자 승인이 필요하다. KIS 분봉 시세 API 는 미구현 — 분봉 CSV 입력 사용."
    )
    _extra: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not self.do_not_auto_apply:
            raise ValueError("do_not_auto_apply must be True")
        if self.auto_apply_allowed or self.is_live_authorization or self.is_order_signal:
            raise ValueError("unsafe invariant True")
        if self.contains_secret or self.kis_intraday_available:
            raise ValueError("contains_secret / kis_intraday_available must be False")
        if self.overall_verdict not in _ORDER:
            raise ValueError(f"invalid verdict: {self.overall_verdict}")


def kis_intraday_supported() -> bool:
    """KIS 분봉 시세 수집 지원 여부 — **현재 미구현** (현재가/잔고/당일체결만)."""
    return False


def _council_metrics(bt: dict[str, Any] | None) -> dict[str, Any]:
    c = (bt or {}).get("council") or {}
    perf = c.get("performance") or {}
    counts = c.get("final_action_counts") or {}
    sig = perf.get("signal_counts") or {}
    return {
        "win_rate": perf.get("win_rate"),
        "profit_factor": perf.get("profit_factor"),
        "expectancy": perf.get("expectancy"),
        "max_drawdown": perf.get("max_drawdown"),
        "trades": int(counts.get("BUY", sig.get("BUY", 0)) or 0),
    }


def _run_symbol(path: Path, *, min_bars: int = MIN_BARS, min_days: int = MIN_DAYS) -> dict[str, Any]:
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

    bars, meta = load_intraday_csv(str(path))
    symbol = (meta["symbols"][0] if meta.get("symbols") else path.stem)
    q = check_intraday_quality(bars, min_bars=min_bars, min_days=min_days)
    status = "PASS" if q.status == "OK" else q.status
    base = {
        "symbol": symbol, "quality_status": status, "bar_count": q.bar_count,
        "day_count": q.day_count, "bars_per_day": q.bars_per_day,
        "bar_size_minutes": q.bar_size_minutes, "intraday_detected": q.intraday_detected,
        "trades": 0, "win_rate": None, "profit_factor": None, "expectancy": None,
        "max_drawdown": None, "walk_forward_score": None,
        "agent_value_verdict": "AGENT_VALUE_INSUFFICIENT_SAMPLE",
        "verdict": sp.BLOCKED, "included": False,
        "reason": "; ".join(q.reasons[:2]),
        "real_data_used": meta.get("real_data_used", False),
        "sample_fixture_only": meta.get("sample_fixture_only", True),
    }
    if not bars or not q.sufficient_for_backtest or q.status == "FAIL":
        return base

    bt = summarize_backtest_report(run_strategy_council_backtest(BacktestInput(bars=tuple(bars))))
    try:
        wf = summarize_walk_forward_report(
            run_walk_forward_validation(WalkForwardInput(bars=tuple(bars))))
    except Exception:  # noqa: BLE001
        wf = {"insufficient_data": True}
    pot = sp.evaluate_strategy_potential(sp.StrategyPotentialInputs(
        backtest=bt, walk_forward=wf, has_real_data=meta.get("real_data_used", False)))
    m = _council_metrics(bt)
    base.update({
        "trades": m["trades"], "win_rate": m["win_rate"], "profit_factor": m["profit_factor"],
        "expectancy": m["expectancy"], "max_drawdown": m["max_drawdown"],
        "walk_forward_score": pot.walk_forward_score,
        "agent_value_verdict": pot.agent_value_verdict, "verdict": pot.overall_verdict,
        "included": True, "reason": base["reason"],
    })
    return base


def evaluate_intraday_strategy(
    input_dir: str | Path,
    *,
    symbols: list[str] | None = None,
    strict: bool = False,
    min_bars: int = MIN_BARS,
    min_days: int = MIN_DAYS,
    generated_at: str | None = None,
) -> IntradayStrategyReport:
    gen = generated_at or datetime.now(timezone.utc).isoformat()
    d = Path(input_dir)
    files = sorted(d.glob("*.csv")) if d.is_dir() else []
    if symbols:
        wanted = {s.lower() for s in symbols}
        files = [f for f in files if f.stem.lower() in wanted]

    per = [_run_symbol(f, min_bars=min_bars, min_days=min_days) for f in files]
    included = [p for p in per if p["included"]]
    pass_syms = tuple(p["symbol"] for p in per if p["included"] and p["quality_status"] == "PASS")
    warn_syms = tuple(p["symbol"] for p in per if p["included"] and p["quality_status"] == "WARN")
    blocked_syms = tuple(p["symbol"] for p in per if not p["included"])

    total_trades = sum(p["trades"] for p in included)
    total_bars = sum(p["bar_count"] for p in per)
    total_days = max((p["day_count"] for p in per), default=0)
    bar_size = next((p["bar_size_minutes"] for p in per if p["bar_size_minutes"]), None)
    intraday_used = any(p["intraday_detected"] for p in per)

    win_rate = _median([p["win_rate"] for p in included])
    pf = _median([p["profit_factor"] for p in included])
    exp = _median([p["expectancy"] for p in included])
    mdd = _median([p["max_drawdown"] for p in included])
    wf_score = _median([p["walk_forward_score"] for p in included])

    helped = tuple(p["symbol"] for p in included
                   if p["agent_value_verdict"] in ("AGENT_ADDS_VALUE", "AGENT_RISK_REDUCTION_VALUE"))
    hurt = tuple(p["symbol"] for p in included if p["agent_value_verdict"] == "AGENT_UNDERPERFORMS")
    no_trade = tuple(p["symbol"] for p in included if p["trades"] == 0)
    n_inc = max(1, len(included))

    if not included or len(no_trade) >= n_inc * 0.5:
        agent_summary = "AGENT_TOO_CONSERVATIVE" if not included or len(no_trade) == n_inc \
            else "AGENT_VALUE_INSUFFICIENT_SAMPLE"
    elif helped and len(helped) > len(hurt):
        agent_summary = "AGENT_ADDS_VALUE"
    elif hurt and len(hurt) >= n_inc * 0.5:
        agent_summary = "AGENT_UNDERPERFORMS"
    else:
        agent_summary = "AGENT_MIXED"

    # ---- 단타 전용 caps (§5) ----
    if not pass_syms and not warn_syms:
        verdict = sp.BLOCKED
    elif len(blocked_syms) > len(per) / 2 and per:
        verdict = sp.NOT_READY
    else:
        verdict = sp.CAUTIOUS  # 기본 — 아래 cap 으로만 내려감 (STRONG 은 Paper 필요)
    if total_trades == 0 or total_trades < TRADES_OBSERVE:
        verdict = _cap(verdict, sp.RESEARCH_ONLY)
    if total_trades < TRADES_EVALUABLE:
        verdict = _cap(verdict, sp.CAUTIOUS)
    if (wf_score or 0) < 40:
        verdict = _cap(verdict, sp.RESEARCH_ONLY)
    if len(no_trade) >= n_inc * 0.5:
        verdict = _cap(verdict, sp.RESEARCH_ONLY)
    if hurt and len(hurt) >= n_inc * 0.5:
        verdict = _cap(verdict, sp.RESEARCH_ONLY)
    # Paper 0건 → 실전 검토 불가 (STRONG 도달 불가): 본 파이프라인은 Paper 미투입.
    verdict = _cap(verdict, sp.CAUTIOUS)
    if strict and warn_syms:
        verdict = _cap(verdict, sp.RESEARCH_ONLY)

    # agent_value_score (집계): helped 비율 기반 간단 점수.
    if included:
        agent_value_score: float | None = round(
            50 + 50 * (len(helped) - len(hurt)) / n_inc, 1)
    else:
        agent_value_score = None

    next_steps: list[str] = []
    if blocked_syms:
        next_steps.append(f"품질/시간프레임 FAIL 종목 제외: {', '.join(blocked_syms)}")
    if total_trades < TRADES_EVALUABLE:
        next_steps.append(f"분봉 표본 부족(trades={total_trades} < {TRADES_EVALUABLE}) — 더 많은 종목/거래일")
    next_steps.append("Paper 모의 100건 + 28거래일 표본 확보 전까지 실전 검토 불가")
    next_steps.append("튜닝 후보는 운영자 검토 + 별도 PR + 재백테스트 (자동 적용 금지)")

    return IntradayStrategyReport(
        generated_at=gen, intraday_data_used=intraday_used, bar_size_minutes=bar_size,
        symbols_count=len(per), total_bars=total_bars, total_days=total_days,
        total_trades=total_trades, pass_symbols=pass_syms, warn_symbols=warn_syms,
        blocked_symbols=blocked_syms, per_symbol=tuple(per), overall_verdict=verdict,
        overall_score=wf_score, win_rate=win_rate, profit_factor=pf, expectancy=exp,
        max_drawdown=mdd, walk_forward_score=wf_score, stress_score=None,
        agent_value_score=agent_value_score, agent_value_summary=agent_summary,
        agent_no_trade_symbols=no_trade, agent_helped_symbols=helped, agent_hurt_symbols=hurt,
        next_steps=tuple(next_steps))


def to_dict(r: IntradayStrategyReport) -> dict[str, Any]:
    return {
        "generated_at": r.generated_at, "intraday_data_used": r.intraday_data_used,
        "bar_size_minutes": r.bar_size_minutes, "symbols_count": r.symbols_count,
        "total_bars": r.total_bars, "total_days": r.total_days, "total_trades": r.total_trades,
        "pass_symbols": list(r.pass_symbols), "warn_symbols": list(r.warn_symbols),
        "blocked_symbols": list(r.blocked_symbols), "per_symbol": list(r.per_symbol),
        "overall_verdict": r.overall_verdict, "overall_score": r.overall_score,
        "win_rate": r.win_rate, "profit_factor": r.profit_factor, "expectancy": r.expectancy,
        "max_drawdown": r.max_drawdown, "walk_forward_score": r.walk_forward_score,
        "stress_score": r.stress_score, "agent_value_score": r.agent_value_score,
        "agent_value_summary": r.agent_value_summary,
        "agent_no_trade_symbols": list(r.agent_no_trade_symbols),
        "agent_helped_symbols": list(r.agent_helped_symbols),
        "agent_hurt_symbols": list(r.agent_hurt_symbols),
        "paper_sample_class": r.paper_sample_class, "next_steps": list(r.next_steps),
        "kis_intraday_available": r.kis_intraday_available,
        "do_not_auto_apply": r.do_not_auto_apply, "auto_apply_allowed": r.auto_apply_allowed,
        "is_live_authorization": r.is_live_authorization, "is_order_signal": r.is_order_signal,
        "contains_secret": r.contains_secret, "disclaimer": r.disclaimer,
    }


def render_markdown(r: IntradayStrategyReport) -> str:
    def _f(x):
        return "평가불가" if x is None else f"{x:.2f}"
    lines = [
        "# INTRADAY-DATA-01 — 분봉 데이터 단타 전략 검증",
        "",
        "> 분봉 데이터 기준 · 자동 적용 아님 · 실전 승인 아님 · 수익 보장 아님.",
        "",
        f"- intraday_data_used={r.intraday_data_used} · bar_size={r.bar_size_minutes}분 · "
        f"종목 {r.symbols_count}(PASS {len(r.pass_symbols)} / WARN {len(r.warn_symbols)} / "
        f"BLOCKED {len(r.blocked_symbols)})",
        f"- overall_verdict: **{r.overall_verdict}** · total_trades {r.total_trades}",
        f"- win_rate {_f(r.win_rate)} · PF {_f(r.profit_factor)} · expectancy {_f(r.expectancy)} · "
        f"MDD {_f(r.max_drawdown)} · WF {_f(r.walk_forward_score)}",
        f"- Agent: **{r.agent_value_summary}** (도움 {list(r.agent_helped_symbols)} / "
        f"방해 {list(r.agent_hurt_symbols)} / 무진입 {list(r.agent_no_trade_symbols)})",
        f"- paper_sample: {r.paper_sample_class}",
        "",
        "## 종목별",
        "| symbol | quality | bars/day | trades | PF | expectancy | WF | agent | verdict | inc |",
        "|---|---|---|---|---|---|---|---|---|---|",
    ]
    for p in r.per_symbol:
        lines.append(
            f"| {p['symbol']} | {p['quality_status']} | {p['bars_per_day']} | {p['trades']} | "
            f"{_f(p['profit_factor'])} | {_f(p['expectancy'])} | {_f(p['walk_forward_score'])} | "
            f"{p['agent_value_verdict']} | {p['verdict']} | {'예' if p['included'] else '제외'} |")
    lines += ["", "## 추천 다음 단계",
              *([f"- {s}" for s in r.next_steps] or ["- (없음)"]),
              "", f"> {r.disclaimer}"]
    return "\n".join(lines) + "\n"

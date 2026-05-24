"""REAL-INTRADAY-TEST-01 — 실제 분봉 데이터 기반 전략 가능성 *최종 판정* (advisory, read-only).

intraday 검증 결과(IntradayStrategyReport)에 **실제 데이터 여부 / 표본 충분성** 게이팅을
적용해 *개발자용 verdict* 와 *사용자용 판단(user_final_judgement)* 을 함께 산출한다.

**합성 fixture 결과를 실제 전략 가능성으로 보고하지 않는다.** 실제 분봉 데이터가 아니면
최대 TOO_EARLY_TO_JUDGE, 데이터 자체가 없으면 BLOCKED_BY_DATA.

본 모듈은 broker / OrderExecutor / route_order / KIS 주문 API 를 import·호출하지 않는다.
결과가 좋아도 자동 적용 / 실전 전환 / 주문 0건.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

# 사용자용 판단.
PROMISING_FOR_PAPER_TEST = "PROMISING_FOR_PAPER_TEST"
WORTH_MORE_RESEARCH = "WORTH_MORE_RESEARCH"
TOO_EARLY_TO_JUDGE = "TOO_EARLY_TO_JUDGE"
STRATEGY_NEEDS_TUNING = "STRATEGY_NEEDS_TUNING"
NOT_PROMISING_ON_CURRENT_DATA = "NOT_PROMISING_ON_CURRENT_DATA"
BLOCKED_BY_DATA = "BLOCKED_BY_DATA"

# 사용자용 한 줄 결론 (빈칸 문구).
_ONE_LINER = {
    PROMISING_FOR_PAPER_TEST: "Paper 모의 리허설을 진행해볼 만한 가능성이 있습니다.",
    WORTH_MORE_RESEARCH: "연구를 계속할 가치는 있지만 튜닝이 필요합니다.",
    TOO_EARLY_TO_JUDGE: "아직 판단하기 이릅니다.",
    STRATEGY_NEEDS_TUNING: "현재 데이터에서는 전략 튜닝이 필요합니다.",
    NOT_PROMISING_ON_CURRENT_DATA: "현재 데이터에서는 유망하지 않습니다.",
    BLOCKED_BY_DATA: "데이터 문제로 판단할 수 없습니다.",
}

# 성과 임계 (사용자용 PROMISING 기준).
MIN_PF = 1.2
MIN_WF = 40.0
MIN_TRADES_OBSERVE = 30
MIN_TRADES_EVALUABLE = 100
MIN_SYMBOLS = 3


@dataclass(frozen=True)
class RealIntradayFinalResult:
    generated_at: str
    actual_data_used: bool
    data_source: str
    bar_size_minutes: float | None
    symbols_count: int
    pass_symbols: tuple[str, ...]
    blocked_symbols: tuple[str, ...]
    total_bars: int
    total_trades: int
    median_win_rate: float | None
    median_profit_factor: float | None
    median_expectancy: float | None
    median_mdd: float | None
    median_walk_forward_score: float | None
    agent_value_summary: str
    agent_value_score: float | None
    agent_helped_symbols: tuple[str, ...]
    agent_hurt_symbols: tuple[str, ...]
    agent_no_trade_symbols: tuple[str, ...]
    developer_verdict: str           # STRONG/CAUTIOUS/RESEARCH_ONLY/NOT_READY/BLOCKED
    user_final_judgement: str        # 6-level
    one_line_conclusion: str
    paper_rehearsal_recommended: bool
    strengths: tuple[str, ...] = ()
    weaknesses: tuple[str, ...] = ()
    next_steps: tuple[str, ...] = ()
    reasons: tuple[str, ...] = ()
    # invariants.
    do_not_auto_apply: bool = True
    is_live_authorization: bool = False
    is_order_signal: bool = False
    contains_secret: bool = False
    no_profit_guarantee: bool = True
    disclaimer: str = (
        "실제 분봉 데이터 기준 전략 가능성 평가이며 자동 적용 / 실전 전환 승인 / 주문 신호가 "
        "아니다. 수익을 보장하지 않는다. PROMISING 이어도 실전이 아니라 Paper 모의 리허설 "
        "단계이며, 실전 검토는 Paper 100건 + 28거래일 + 운영자 승인이 필요하다."
    )
    _extra: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not self.do_not_auto_apply or not self.no_profit_guarantee:
            raise ValueError("do_not_auto_apply / no_profit_guarantee must be True")
        if self.is_live_authorization or self.is_order_signal or self.contains_secret:
            raise ValueError("unsafe invariant True")
        if self.user_final_judgement not in _ONE_LINER:
            raise ValueError(f"invalid user judgement: {self.user_final_judgement}")


def _judge(
    *, actual_data_used: bool, symbols_count: int, pass_count: int, total_trades: int,
    median_pf: float | None, median_exp: float | None, median_wf: float | None,
    agent_summary: str,
) -> tuple[str, list[str]]:
    """사용자용 최종 판단 + 사유. 보수적 게이팅."""
    reasons: list[str] = []

    # 1) 데이터 자체 문제.
    if pass_count == 0:
        reasons.append("품질 PASS 종목 0 — 실제 분봉 데이터 없음/부족.")
        return BLOCKED_BY_DATA, reasons
    if not actual_data_used:
        reasons.append("합성 fixture 결과는 실제 전략 가능성 판단에 사용하지 않음.")
        return TOO_EARLY_TO_JUDGE, reasons

    # 2) total_trades 게이팅.
    if total_trades == 0:
        reasons.append("Agent 진입 0건 — 전략이 진입하지 못함(튜닝 필요).")
        return STRATEGY_NEEDS_TUNING, reasons
    ceiling = None  # 상한
    if symbols_count < MIN_SYMBOLS:
        reasons.append(f"종목 {symbols_count} < {MIN_SYMBOLS} — 표본 부족.")
        ceiling = TOO_EARLY_TO_JUDGE
    if total_trades < MIN_TRADES_OBSERVE:
        reasons.append(f"total_trades {total_trades} < {MIN_TRADES_OBSERVE} — 표본 부족.")
        return TOO_EARLY_TO_JUDGE, reasons

    # 3) 명백히 유망하지 않은 경우 (충분한 거래 + 음의 성과).
    if total_trades >= MIN_TRADES_EVALUABLE:
        if (median_pf is not None and median_pf < 1.0) and \
           (median_exp is not None and median_exp <= 0):
            reasons.append(f"충분한 거래에서 median PF {median_pf:.2f}<1 + expectancy≤0 — 유망하지 않음.")
            return NOT_PROMISING_ON_CURRENT_DATA, reasons

    # 4) PROMISING 조건 (모두 충족 시).
    promising = (
        total_trades >= MIN_TRADES_EVALUABLE
        and median_pf is not None and median_pf >= MIN_PF
        and median_exp is not None and median_exp > 0
        and median_wf is not None and median_wf >= MIN_WF
        and agent_summary in ("AGENT_ADDS_VALUE", "AGENT_RISK_REDUCTION_VALUE")
    )
    if promising and ceiling is None:
        reasons.append("실제 분봉 + 거래 100+ + PF≥1.2 + expectancy>0 + WF≥40 + Agent 도움 — "
                       "Paper 리허설 검토 가능(실전 아님).")
        return PROMISING_FOR_PAPER_TEST, reasons

    # PROMISING 못 미침 사유.
    if total_trades < MIN_TRADES_EVALUABLE:
        reasons.append(f"total_trades {total_trades} < {MIN_TRADES_EVALUABLE} — 1차 평가 표본 미달.")
    if median_pf is not None and median_pf < MIN_PF:
        reasons.append(f"median PF {median_pf:.2f} < {MIN_PF}.")
    if median_wf is not None and median_wf < MIN_WF:
        reasons.append(f"median walk_forward_score {median_wf:.1f} < {MIN_WF} — OOS 안정성 약함(과최적화 의심).")
    if median_exp is not None and median_exp <= 0:
        reasons.append("median expectancy ≤ 0.")

    # 5) WORTH_MORE_RESEARCH (실데이터 + 거래 충분 + 일부 양호).
    judgement = WORTH_MORE_RESEARCH
    if ceiling == TOO_EARLY_TO_JUDGE:
        judgement = TOO_EARLY_TO_JUDGE
    return judgement, reasons


def build_final_result(
    intraday: dict[str, Any],
    *,
    actual_data_used: bool | None = None,
    data_source: str = "unknown",
    generated_at: str | None = None,
) -> RealIntradayFinalResult:
    """IntradayStrategyReport.to_dict() → 최종 사용자 판정."""
    gen = generated_at or datetime.now(timezone.utc).isoformat()
    per = intraday.get("per_symbol", []) or []
    if actual_data_used is None:
        actual_data_used = any(p.get("real_data_used") for p in per) and not all(
            p.get("sample_fixture_only", True) for p in per)

    pass_syms = tuple(intraday.get("pass_symbols", []) or [])
    blocked_syms = tuple(intraday.get("blocked_symbols", []) or [])
    total_trades = int(intraday.get("total_trades", 0) or 0)
    median_pf = intraday.get("profit_factor")
    median_exp = intraday.get("expectancy")
    median_wf = intraday.get("walk_forward_score")
    median_wr = intraday.get("win_rate")
    median_mdd = intraday.get("max_drawdown")
    agent_summary = intraday.get("agent_value_summary", "AGENT_VALUE_INSUFFICIENT_SAMPLE")
    symbols_count = int(intraday.get("symbols_count", 0) or 0)

    judgement, reasons = _judge(
        actual_data_used=bool(actual_data_used), symbols_count=symbols_count,
        pass_count=len(pass_syms), total_trades=total_trades,
        median_pf=median_pf, median_exp=median_exp, median_wf=median_wf,
        agent_summary=agent_summary)

    strengths: list[str] = []
    weaknesses: list[str] = []
    if median_exp is not None and median_exp > 0:
        strengths.append(f"median expectancy 양(+{median_exp:.0f})")
    if agent_summary in ("AGENT_ADDS_VALUE", "AGENT_RISK_REDUCTION_VALUE"):
        strengths.append(f"Agent Council 효과: {agent_summary}")
    if total_trades >= MIN_TRADES_EVALUABLE:
        strengths.append(f"실제 분봉에서 진입 다수 발생(total_trades={total_trades})")
    if median_wf is not None and median_wf < MIN_WF:
        weaknesses.append(f"walk-forward 안정성 약함(median {median_wf:.1f}) — 과최적화 의심")
    if median_pf is not None and median_pf < MIN_PF:
        weaknesses.append(f"median PF {median_pf:.2f} < {MIN_PF}")
    if intraday.get("agent_hurt_symbols"):
        weaknesses.append(f"일부 종목 Agent 성과 저조: {intraday.get('agent_hurt_symbols')}")

    next_steps = [
        "성과 음수/저조 종목 제외 후 파라미터 튜닝(별도 PR + 재백테스트, 자동 적용 금지).",
        "더 긴 기간/더 많은 종목의 분봉으로 walk-forward 안정성 재확인.",
        "Paper 모의 운영 100건 + 28거래일 표본 축적(실전 검토 전제).",
    ]
    paper_ok = judgement == PROMISING_FOR_PAPER_TEST

    return RealIntradayFinalResult(
        generated_at=gen, actual_data_used=bool(actual_data_used), data_source=data_source,
        bar_size_minutes=intraday.get("bar_size_minutes"), symbols_count=symbols_count,
        pass_symbols=pass_syms, blocked_symbols=blocked_syms,
        total_bars=int(intraday.get("total_bars", 0) or 0), total_trades=total_trades,
        median_win_rate=median_wr, median_profit_factor=median_pf,
        median_expectancy=median_exp, median_mdd=median_mdd,
        median_walk_forward_score=median_wf, agent_value_summary=agent_summary,
        agent_value_score=intraday.get("agent_value_score"),
        agent_helped_symbols=tuple(intraday.get("agent_helped_symbols", []) or []),
        agent_hurt_symbols=tuple(intraday.get("agent_hurt_symbols", []) or []),
        agent_no_trade_symbols=tuple(intraday.get("agent_no_trade_symbols", []) or []),
        developer_verdict=intraday.get("overall_verdict", "RESEARCH_ONLY"),
        user_final_judgement=judgement, one_line_conclusion=_ONE_LINER[judgement],
        paper_rehearsal_recommended=paper_ok,
        strengths=tuple(strengths), weaknesses=tuple(weaknesses),
        next_steps=tuple(next_steps), reasons=tuple(reasons))


def to_dict(r: RealIntradayFinalResult) -> dict[str, Any]:
    return {
        "generated_at": r.generated_at, "actual_data_used": r.actual_data_used,
        "data_source": r.data_source, "bar_size_minutes": r.bar_size_minutes,
        "symbols_count": r.symbols_count, "pass_symbols": list(r.pass_symbols),
        "blocked_symbols": list(r.blocked_symbols), "total_bars": r.total_bars,
        "total_trades": r.total_trades, "median_win_rate": r.median_win_rate,
        "median_profit_factor": r.median_profit_factor, "median_expectancy": r.median_expectancy,
        "median_mdd": r.median_mdd, "median_walk_forward_score": r.median_walk_forward_score,
        "agent_value_summary": r.agent_value_summary, "agent_value_score": r.agent_value_score,
        "agent_helped_symbols": list(r.agent_helped_symbols),
        "agent_hurt_symbols": list(r.agent_hurt_symbols),
        "agent_no_trade_symbols": list(r.agent_no_trade_symbols),
        "developer_verdict": r.developer_verdict,
        "user_final_judgement": r.user_final_judgement,
        "one_line_conclusion": r.one_line_conclusion,
        "paper_rehearsal_recommended": r.paper_rehearsal_recommended,
        "strengths": list(r.strengths), "weaknesses": list(r.weaknesses),
        "next_steps": list(r.next_steps), "reasons": list(r.reasons),
        "do_not_auto_apply": r.do_not_auto_apply, "is_live_authorization": r.is_live_authorization,
        "is_order_signal": r.is_order_signal, "contains_secret": r.contains_secret,
        "no_profit_guarantee": r.no_profit_guarantee, "disclaimer": r.disclaimer,
    }


def render_markdown(r: RealIntradayFinalResult, per_symbol: list[dict] | None = None) -> str:
    def _f(x):
        return "평가불가" if x is None else f"{x:.2f}"
    lines = [
        "# REAL-INTRADAY-TEST-01 — 실제 분봉 데이터 기반 전략 가능성 최종 결과",
        "",
        f"> **한 줄 결론: 현재 실제 분봉 데이터 기준으로, 사용자님의 매매기법 + Agent 전략은 "
        f"{r.one_line_conclusion}**",
        "",
        "> 자동 적용 아님 · 실전 승인 아님 · 수익 보장 아님.",
        "",
        f"- 데이터 출처: **{r.data_source}** · 실제 데이터 여부: **{r.actual_data_used}** · "
        f"bar_size {r.bar_size_minutes}분",
        f"- 종목 {r.symbols_count} (PASS {len(r.pass_symbols)} / BLOCKED {len(r.blocked_symbols)}) · "
        f"total_bars {r.total_bars} · total_trades {r.total_trades}",
        f"- median win_rate {_f(r.median_win_rate)} · PF {_f(r.median_profit_factor)} · "
        f"expectancy {_f(r.median_expectancy)} · MDD {_f(r.median_mdd)} · "
        f"walk_forward {_f(r.median_walk_forward_score)}",
        f"- Agent vs 단일전략: **{r.agent_value_summary}** "
        f"(도움 {list(r.agent_helped_symbols)} / 방해 {list(r.agent_hurt_symbols)} / "
        f"무진입 {list(r.agent_no_trade_symbols)})",
        f"- 개발자 verdict: **{r.developer_verdict}** · 사용자 판단: **{r.user_final_judgement}** · "
        f"Paper 리허설 권고: **{r.paper_rehearsal_recommended}**",
        "",
    ]
    if per_symbol:
        lines += ["## 종목별",
                  "| symbol | quality | trades | PF | expectancy | WF | agent | verdict |",
                  "|---|---|---|---|---|---|---|---|"]
        for p in per_symbol:
            lines.append(
                f"| {p['symbol']} | {p['quality_status']} | {p['trades']} | "
                f"{_f(p.get('profit_factor'))} | {_f(p.get('expectancy'))} | "
                f"{_f(p.get('walk_forward_score'))} | {p.get('agent_value_verdict')} | "
                f"{p.get('verdict')} |")
        lines.append("")
    lines += [
        "## 핵심 강점", *([f"- {s}" for s in r.strengths] or ["- (없음)"]), "",
        "## 핵심 약점", *([f"- {w}" for w in r.weaknesses] or ["- (없음)"]), "",
        "## 판정 사유", *([f"- {x}" for x in r.reasons] or ["- (없음)"]), "",
        "## 지금 바로 할 일", *([f"- {n}" for n in r.next_steps] or ["- (없음)"]), "",
        f"> {r.disclaimer}",
    ]
    return "\n".join(lines) + "\n"

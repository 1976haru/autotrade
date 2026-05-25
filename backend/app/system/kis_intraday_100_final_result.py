"""KIS-INTRADAY-100-VALIDATION-01 — 실제 KIS 분봉 100종목 전략 가능성 최종 판정.

KIS 주식일별분봉조회(read-only) 로 수집한 100종목 내외의 실제 분봉으로 현재 전략
(ORB / Momentum / Gap / VWAP / Agent Council / RiskOfficer / exit_plan / quality_score
gate 결합)의 가능성을 *종합* 판정한다.

기존 인프라 재사용:
- `intraday_strategy_validation.evaluate_intraday_strategy` (품질 PASS-only backtest +
  walk-forward + Agent vs 단일전략)
- `real_intraday_final_result.build_final_result` (개발자 verdict + 사용자 판단 6단계)
- `agent_stress_test.run_agent_stress_test` (악조건 가드 — PROMISING 게이트)

KIS 대규모 전용 caps: 품질 PASS < 70 또는 total_trades < 500 이면 PROMISING 불가
(최대 WORTH_MORE_RESEARCH). 수집 실패 / PASS 과소 / 기간 과소 이면 DATA_NOT_RELIABLE.

본 모듈은 broker / OrderExecutor / route_order / KIS 주문 API 를 import·호출하지 않는다.
**결과가 좋아도 자동 적용 / 실전 전환 / 주문 0건.**
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from app.system import real_intraday_final_result as rifr
from app.system.intraday_strategy_validation import evaluate_intraday_strategy, to_dict as _intraday_to_dict

DATA_SOURCE = "KIS_INTRADAY_DAILYCHART"  # 주식일별분봉조회 [국내주식-213] FHKST03010230

# KIS 대규모 검증 임계 (task §3, §10).
MIN_PASS_FULL = 70
MIN_TRADES_FULL = 500
MIN_PASS_RELIABLE = 30  # 미만이면 DATA_NOT_RELIABLE

# 사용자용 판단 (real_intraday_final_result 재사용) + KIS 전용 추가 라벨.
DATA_NOT_RELIABLE = "DATA_NOT_RELIABLE"


@dataclass(frozen=True)
class KisIntraday100Result:
    generated_at: str
    data_source: str
    # 수집 요약.
    requested_symbols: int
    collected_symbols: int
    collection_failed: int
    trading_day_count: int
    bar_size_minutes: float | None
    # 품질.
    pass_count: int
    warn_count: int
    blocked_count: int
    pass_symbols: tuple[str, ...]
    blocked_symbols: tuple[str, ...]
    # 성과.
    total_bars: int
    total_trades: int
    median_win_rate: float | None
    median_profit_factor: float | None
    median_expectancy: float | None
    median_mdd: float | None
    median_walk_forward_score: float | None
    # Agent.
    agent_value_summary: str
    agent_helped_symbols: tuple[str, ...]
    agent_hurt_symbols: tuple[str, ...]
    agent_no_trade_symbols: tuple[str, ...]
    # 스트레스.
    stress_fail_count: int | None
    stress_overall: str | None
    # 판정.
    developer_verdict: str
    user_final_judgement: str
    one_line_conclusion: str
    paper_rehearsal_recommended: bool
    top_10_promising_symbols: tuple[dict[str, Any], ...]
    excluded_symbols: tuple[dict[str, Any], ...]
    strengths: tuple[str, ...] = ()
    weaknesses: tuple[str, ...] = ()
    next_actions: tuple[str, ...] = ()
    reasons: tuple[str, ...] = ()
    # 안전 불변값.
    do_not_auto_apply: bool = True
    is_live_authorization: bool = False
    broker_order_sent: bool = False
    order_created: bool = False
    is_order_signal: bool = False
    kis_order_api_called: bool = False
    contains_secret: bool = False
    no_profit_guarantee: bool = True
    disclaimer: str = (
        "실제 KIS 분봉 데이터 기준 전략 가능성 평가이며 자동 적용 / 실전 전환 승인 / 주문 "
        "신호가 아니다. 수익을 보장하지 않는다. PROMISING 이어도 실전이 아니라 Paper 모의 "
        "리허설 단계이며, 실전 검토는 Paper 100건 + 28거래일 + 운영자 명시 승인이 필요하다. "
        "수집·검증 과정에서 KIS 주문 API 는 호출하지 않았다(read-only 시세 조회만)."
    )
    _extra: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not self.do_not_auto_apply or not self.no_profit_guarantee:
            raise ValueError("do_not_auto_apply / no_profit_guarantee must be True")
        if (self.is_live_authorization or self.broker_order_sent or self.order_created
                or self.is_order_signal or self.kis_order_api_called or self.contains_secret):
            raise ValueError("unsafe invariant True on KisIntraday100Result")
        valid = set(rifr._ONE_LINER) | {DATA_NOT_RELIABLE}
        if self.user_final_judgement not in valid:
            raise ValueError(f"invalid user judgement: {self.user_final_judgement}")


def _top_promising(per_symbol: list[dict], n: int = 10) -> list[dict]:
    """included 종목을 expectancy → PF 내림차순 정렬해 상위 n."""
    inc = [p for p in per_symbol if p.get("included")]

    def _key(p):
        return (p.get("expectancy") or float("-inf"), p.get("profit_factor") or 0.0)

    return [
        {"symbol": p["symbol"], "trades": p.get("trades"),
         "profit_factor": p.get("profit_factor"), "expectancy": p.get("expectancy"),
         "walk_forward_score": p.get("walk_forward_score"),
         "agent_value_verdict": p.get("agent_value_verdict"), "verdict": p.get("verdict")}
        for p in sorted(inc, key=_key, reverse=True)[:n]
    ]


def _excluded(per_symbol: list[dict]) -> list[dict]:
    return [
        {"symbol": p["symbol"], "quality_status": p.get("quality_status"),
         "reason": p.get("reason", "")}
        for p in per_symbol if not p.get("included")
    ]


def _apply_kis_caps(
    base_judgement: str, *, pass_count: int, total_trades: int,
    stress_fail: int | None, reasons: list[str],
) -> str:
    """KIS 대규모 전용 보수 caps."""
    j = base_judgement
    # 1) 데이터 신뢰 불가.
    if pass_count < MIN_PASS_RELIABLE:
        reasons.append(f"품질 PASS {pass_count} < {MIN_PASS_RELIABLE} — 데이터 신뢰 불가.")
        return DATA_NOT_RELIABLE
    # 2) PROMISING 추가 요건: PASS≥70 + trades≥500 + stress FAIL 0.
    if j == rifr.PROMISING_FOR_PAPER_TEST:
        if pass_count < MIN_PASS_FULL:
            reasons.append(f"PASS {pass_count} < {MIN_PASS_FULL} — PROMISING 불가, 연구 단계로 하향.")
            j = rifr.WORTH_MORE_RESEARCH
        elif total_trades < MIN_TRADES_FULL:
            reasons.append(f"total_trades {total_trades} < {MIN_TRADES_FULL} — PROMISING 불가, 연구 단계로 하향.")
            j = rifr.WORTH_MORE_RESEARCH
        elif stress_fail is not None and stress_fail > 0:
            reasons.append(f"스트레스 FAIL {stress_fail}건 — PROMISING 불가, 연구 단계로 하향.")
            j = rifr.WORTH_MORE_RESEARCH
    return j


def evaluate_kis_intraday_100(
    input_dir: str | Path,
    *,
    collect_summary: dict[str, Any] | None = None,
    symbols: list[str] | None = None,
    run_stress: bool = True,
    generated_at: str | None = None,
) -> KisIntraday100Result:
    gen = generated_at or datetime.now(timezone.utc).isoformat()

    report = evaluate_intraday_strategy(input_dir, symbols=symbols, min_bars=100, min_days=5)
    d = _intraday_to_dict(report)
    per = list(d.get("per_symbol", []))

    pass_count = len(d.get("pass_symbols", []))
    warn_count = len(d.get("warn_symbols", []))
    blocked_count = len(d.get("blocked_symbols", []))

    # 스트레스 (악조건 가드) — PROMISING 게이트.
    stress_fail: int | None = None
    stress_overall: str | None = None
    if run_stress:
        try:
            from app.stress_test.agent_stress_test import run_agent_stress_test
            sr = run_agent_stress_test()
            stress_fail = int(sr.counts.get("FAIL", 0))
            stress_overall = sr.overall_verdict
        except Exception:  # noqa: BLE001
            stress_fail = None

    base = rifr.build_final_result(d, actual_data_used=True, data_source=DATA_SOURCE, generated_at=gen)

    reasons = list(base.reasons)
    judgement = _apply_kis_caps(
        base.user_final_judgement, pass_count=pass_count, total_trades=base.total_trades,
        stress_fail=stress_fail, reasons=reasons)
    one_liner = (rifr._ONE_LINER.get(judgement)
                 if judgement in rifr._ONE_LINER
                 else "데이터 신뢰성 부족으로 판단할 수 없습니다.")
    paper_ok = judgement == rifr.PROMISING_FOR_PAPER_TEST

    cs = collect_summary or {}
    requested = int(cs.get("requested", report.symbols_count) or report.symbols_count)
    collected = int(cs.get("succeeded", report.symbols_count) or report.symbols_count)
    failed = int(cs.get("failed", 0) or 0)
    trading_days = int(cs.get("trading_day_count", report.total_days) or report.total_days)

    next_actions = list(base.next_steps)
    if judgement == DATA_NOT_RELIABLE:
        next_actions = [
            "더 많은 종목/거래일의 실제 분봉을 재수집 (품질 PASS 70종목 목표).",
            "수집 실패 종목의 사유(상장폐지/거래정지/거래량 부족) 확인 후 universe 보강.",
        ]

    return KisIntraday100Result(
        generated_at=gen, data_source=DATA_SOURCE,
        requested_symbols=requested, collected_symbols=collected, collection_failed=failed,
        trading_day_count=trading_days, bar_size_minutes=report.bar_size_minutes,
        pass_count=pass_count, warn_count=warn_count, blocked_count=blocked_count,
        pass_symbols=tuple(d.get("pass_symbols", [])), blocked_symbols=tuple(d.get("blocked_symbols", [])),
        total_bars=report.total_bars, total_trades=base.total_trades,
        median_win_rate=base.median_win_rate, median_profit_factor=base.median_profit_factor,
        median_expectancy=base.median_expectancy, median_mdd=base.median_mdd,
        median_walk_forward_score=base.median_walk_forward_score,
        agent_value_summary=base.agent_value_summary,
        agent_helped_symbols=base.agent_helped_symbols, agent_hurt_symbols=base.agent_hurt_symbols,
        agent_no_trade_symbols=base.agent_no_trade_symbols,
        stress_fail_count=stress_fail, stress_overall=stress_overall,
        developer_verdict=base.developer_verdict, user_final_judgement=judgement,
        one_line_conclusion=one_liner, paper_rehearsal_recommended=paper_ok,
        top_10_promising_symbols=tuple(_top_promising(per)), excluded_symbols=tuple(_excluded(per)),
        strengths=base.strengths, weaknesses=base.weaknesses,
        next_actions=tuple(next_actions), reasons=tuple(reasons))


def to_dict(r: KisIntraday100Result) -> dict[str, Any]:
    return {
        "generated_at": r.generated_at, "data_source": r.data_source,
        "requested_symbols": r.requested_symbols, "collected_symbols": r.collected_symbols,
        "collection_failed": r.collection_failed, "trading_day_count": r.trading_day_count,
        "bar_size_minutes": r.bar_size_minutes,
        "pass_count": r.pass_count, "warn_count": r.warn_count, "blocked_count": r.blocked_count,
        "pass_symbols": list(r.pass_symbols), "blocked_symbols": list(r.blocked_symbols),
        "total_bars": r.total_bars, "total_trades": r.total_trades,
        "median_win_rate": r.median_win_rate, "median_profit_factor": r.median_profit_factor,
        "median_expectancy": r.median_expectancy, "median_mdd": r.median_mdd,
        "median_walk_forward_score": r.median_walk_forward_score,
        "agent_value_summary": r.agent_value_summary,
        "agent_helped_symbols": list(r.agent_helped_symbols),
        "agent_hurt_symbols": list(r.agent_hurt_symbols),
        "agent_no_trade_symbols": list(r.agent_no_trade_symbols),
        "stress_fail_count": r.stress_fail_count, "stress_overall": r.stress_overall,
        "developer_verdict": r.developer_verdict, "user_final_judgement": r.user_final_judgement,
        "one_line_conclusion": r.one_line_conclusion,
        "paper_rehearsal_recommended": r.paper_rehearsal_recommended,
        "top_10_promising_symbols": list(r.top_10_promising_symbols),
        "excluded_symbols": list(r.excluded_symbols),
        "strengths": list(r.strengths), "weaknesses": list(r.weaknesses),
        "next_actions": list(r.next_actions), "reasons": list(r.reasons),
        "do_not_auto_apply": r.do_not_auto_apply,
        "is_live_authorization": r.is_live_authorization,
        "broker_order_sent": r.broker_order_sent, "order_created": r.order_created,
        "is_order_signal": r.is_order_signal, "kis_order_api_called": r.kis_order_api_called,
        "contains_secret": r.contains_secret, "no_profit_guarantee": r.no_profit_guarantee,
        "disclaimer": r.disclaimer,
    }


def render_markdown(r: KisIntraday100Result) -> str:
    def _f(x):
        return "평가불가" if x is None else f"{x:.2f}"
    lines = [
        "# KIS-INTRADAY-100-VALIDATION-01 — 실제 KIS 분봉 100종목 전략 가능성 최종 결과",
        "",
        f"> **한 줄 결론: 현재 KIS 실제 분봉 {r.collected_symbols}종목 기준으로, 사용자님의 "
        f"매매기법 + Agent 전략은 {r.one_line_conclusion}**",
        "",
        "> 자동 적용 아님 · 실전 승인 아님 · 수익 보장 아님 · KIS 주문 API 호출 0건(read-only).",
        "",
        "## 1. 데이터 출처",
        "- 주식일별분봉조회 [국내주식-213] · TR FHKST03010230 · read-only 시세 조회",
        f"- data_source={r.data_source} · bar_size={r.bar_size_minutes}분",
        "",
        "## 2~4. 수집 범위",
        f"- 요청 {r.requested_symbols} / 수집성공 {r.collected_symbols} / 실패 {r.collection_failed}",
        f"- 거래일 {r.trading_day_count}일 · total_bars {r.total_bars}",
        "",
        "## 5. 품질검증",
        f"- PASS {r.pass_count} / WARN {r.warn_count} / BLOCKED {r.blocked_count}",
        "",
        "## 6. 전체 전략 성과",
        f"- total_trades {r.total_trades}",
        f"- median win_rate {_f(r.median_win_rate)} · PF {_f(r.median_profit_factor)} · "
        f"expectancy {_f(r.median_expectancy)} · MDD {_f(r.median_mdd)} · "
        f"walk_forward {_f(r.median_walk_forward_score)}",
        "",
        "## 7. Agent 효과",
        f"- **{r.agent_value_summary}** (도움 {len(r.agent_helped_symbols)} / "
        f"방해 {len(r.agent_hurt_symbols)} / 무진입 {len(r.agent_no_trade_symbols)})",
        "",
        "## 8. Walk-forward 안정성",
        f"- median walk_forward_score {_f(r.median_walk_forward_score)} "
        f"(40 미만이면 과최적화 의심)",
        "",
        "## 9. 상위 10종목",
        "| symbol | trades | PF | expectancy | WF | agent | verdict |",
        "|---|---|---|---|---|---|---|",
    ]
    for p in r.top_10_promising_symbols:
        lines.append(
            f"| {p['symbol']} | {p.get('trades')} | {_f(p.get('profit_factor'))} | "
            f"{_f(p.get('expectancy'))} | {_f(p.get('walk_forward_score'))} | "
            f"{p.get('agent_value_verdict')} | {p.get('verdict')} |")
    lines += [
        "",
        "## 10. 제외/위험 종목",
        f"- {len(r.excluded_symbols)}종목 제외: "
        f"{', '.join(p['symbol'] for p in r.excluded_symbols[:20]) or '(없음)'}",
        "",
        "## 11. 전략 가능성 판정",
        f"- 개발자 verdict: **{r.developer_verdict}**",
        f"- 사용자 최종 판단: **{r.user_final_judgement}**",
        f"- stress: FAIL {r.stress_fail_count} ({r.stress_overall})",
        "",
        "### 핵심 강점", *([f"- {s}" for s in r.strengths] or ["- (없음)"]),
        "### 핵심 약점", *([f"- {w}" for w in r.weaknesses] or ["- (없음)"]),
        "### 판정 사유", *([f"- {x}" for x in r.reasons] or ["- (없음)"]),
        "",
        "## 12. 지금 바로 할 일 / 다음 튜닝 방향",
        *([f"- {n}" for n in r.next_actions] or ["- (없음)"]),
        "",
        f"## 14. Paper 리허설 가능 여부: **{r.paper_rehearsal_recommended}**",
        "## 15. 실전 승인: 아님 (is_live_authorization=false)",
        "## 16. 수익 보장: 아님",
        "",
        f"> {r.disclaimer}",
    ]
    return "\n".join(lines) + "\n"

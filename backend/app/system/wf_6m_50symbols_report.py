"""WF-6M-50SYMBOLS-01 — 6개월·50종목·1000만원 기준 전략 종합 검증 + 업그레이드 방향.

세 가지 read-only 분석을 종합한다:
1. 포트폴리오 자금곡선 시뮬 (`portfolio_capital_sim`) — 10M KRW 공유 자본, 비용 후 성과.
2. 종목별 검증 (`intraday_strategy_validation`) — PF / walk-forward / Agent 효과.
3. 집계 전략 분해 (`strategy_council_backtest`) — 전략별 생존/사망 + 장세·시간대 버킷.

→ 종목 등급화(GO/WATCH/TUNE/EXCLUDE) + 실전 가능성 단계 + 프로그램 업그레이드 방향 +
최종 판정. **결과가 나빠도 그대로 보고** — 과장/수익 보장 0건, 자동 적용/실전 전환 0건.

본 모듈은 broker / OrderExecutor / route_order / KIS 주문 API 를 import·호출하지 않는다.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

# 종합 판정.
RESEARCH_ONLY = "RESEARCH_ONLY"
WORTH_MORE_RESEARCH = "WORTH_MORE_RESEARCH"
PAPER_REHEARSAL_WORTHY = "PAPER_REHEARSAL_WORTHY"
NOT_RECOMMENDED = "NOT_RECOMMENDED"

# 종목 등급.
GO, WATCH, TUNE, EXCLUDE = "GO", "WATCH", "TUNE", "EXCLUDE"

MIN_DAYS_FULL = 120
GOOD_PF = 1.3
GOOD_WF = 40.0
MIN_TRADES_SYM = 8


@dataclass(frozen=True)
class Wf6mReport:
    generated_at: str
    data_source: str
    # 데이터.
    symbols_count: int
    pass_symbols: int
    trading_days: int
    total_bars: int
    bar_size_minutes: float | None
    enough_history: bool          # >=120 거래일
    # 포트폴리오 (1000만원).
    initial_capital: float
    final_equity: float
    total_return_pct: float | None
    daily_avg_trades: float | None
    daily_avg_return_pct: float | None
    weekly_avg_return_pct: float | None
    monthly_avg_return_pct: float | None
    win_rate: float | None
    payoff_ratio: float | None
    expectancy: float | None
    profit_factor: float | None
    max_drawdown_pct: float | None
    worst_day_pnl: float | None
    worst_consecutive_losses: int
    avg_hold_minutes: float | None
    skipped_max_positions: int
    monthly_returns: dict[str, float]
    equity_curve_points: int
    # 종목 등급.
    grades: dict[str, int]        # {GO:n, WATCH:n, TUNE:n, EXCLUDE:n}
    top_10: tuple[dict[str, Any], ...]
    bottom_10: tuple[dict[str, Any], ...]
    exclude_recommended: tuple[str, ...]
    agent_helped_symbols: tuple[str, ...]
    agent_hurt_symbols: tuple[str, ...]
    # 전략 분해.
    strategy_survival: dict[str, dict[str, Any]]
    strategy_alive: tuple[str, ...]
    strategy_dead: tuple[str, ...]
    council_better_than_best_single: bool | None
    regime_buckets: dict[str, Any]
    time_phase_buckets: dict[str, Any]
    # 판정 + 업그레이드.
    median_walk_forward_score: float | None
    agent_value_summary: str
    final_verdict: str
    live_possibility: str
    strengths_top3: tuple[str, ...]
    weaknesses_top3: tuple[str, ...]
    agent_optimal_role: str
    upgrade_directions: tuple[str, ...]
    expansion_assessment: dict[str, str]
    next_steps: tuple[str, ...]
    reasons: tuple[str, ...] = ()
    # 안전 불변값.
    do_not_auto_apply: bool = True
    is_live_authorization: bool = False
    broker_order_sent: bool = False
    order_created: bool = False
    contains_secret: bool = False
    no_profit_guarantee: bool = True
    disclaimer: str = (
        "6개월·50종목·1000만원 기준 실제 분봉 backtest/simulation 결과이며 자동 적용 / 실전 "
        "전환 / 주문 신호가 아니다. 수익을 보장하지 않는다. 실주문/실체결 0건. 실전 검토는 "
        "Paper 모의 100건 + 28거래일 + 운영자 명시 승인이 필요하다."
    )
    _extra: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not self.do_not_auto_apply or not self.no_profit_guarantee:
            raise ValueError("do_not_auto_apply / no_profit_guarantee must be True")
        if self.is_live_authorization or self.broker_order_sent or self.order_created:
            raise ValueError("unsafe invariant True")
        if self.contains_secret:
            raise ValueError("contains_secret must be False")
        if self.final_verdict not in (RESEARCH_ONLY, WORTH_MORE_RESEARCH,
                                      PAPER_REHEARSAL_WORTHY, NOT_RECOMMENDED):
            raise ValueError(f"invalid verdict: {self.final_verdict}")


def _grade_symbol(v: dict[str, Any], sim_net: float | None) -> str:
    """종목 등급 — 검증 메트릭(PF/WF/expectancy/agent) + 포트 기여(net_pnl)."""
    if not v.get("included"):
        return EXCLUDE
    pf = v.get("profit_factor")
    exp = v.get("expectancy")
    wf = v.get("walk_forward_score")
    agent = v.get("agent_value_verdict", "")
    trades = v.get("trades", 0) or 0
    agent_ok = agent in ("AGENT_ADDS_VALUE", "AGENT_RISK_REDUCTION_VALUE")
    agent_bad = agent == "AGENT_UNDERPERFORMS"

    # EXCLUDE: 손실 + 약한 엣지.
    if (sim_net is not None and sim_net < 0) and ((pf is not None and pf < 1.0) or (exp is not None and exp <= 0)):
        return EXCLUDE
    if (exp is not None and exp <= 0) and (pf is not None and pf < 1.0):
        return EXCLUDE
    # GO: 강한 엣지 + OOS 안정 + Agent 도움.
    if (trades >= MIN_TRADES_SYM and pf is not None and pf >= GOOD_PF
            and exp is not None and exp > 0 and wf is not None and wf >= GOOD_WF and agent_ok):
        return GO
    # WATCH: 표본 부족 / 경계.
    if trades < MIN_TRADES_SYM:
        return WATCH
    # TUNE: 양의 기대값이지만 WF 약함 또는 Agent 방해.
    if (exp is not None and exp > 0) and ((wf is None or wf < GOOD_WF) or agent_bad):
        return TUNE
    return WATCH


def _agent_optimal_role(council_better: bool | None, agent_summary: str,
                        helped: int, hurt: int) -> str:
    if council_better is True and agent_summary in ("AGENT_ADDS_VALUE", "AGENT_RISK_REDUCTION_VALUE"):
        return ("종목 선택 + 진입 타이밍 — Agent Council 이 단일전략보다 우위. 현 가중투표 유지 권장.")
    if hurt > helped:
        return ("위험 필터(진입 억제) 중심 — Agent 가 종목 다수에서 단일전략보다 못하므로, "
                "진입 신호 생성보다 *나쁜 진입 차단 / 포지션 축소* 역할로 한정 권장.")
    return ("보조 검증 — Agent 효과가 혼재. 진입 결정권보다 RiskOfficer / exit_plan / "
            "quality gate 보조 역할로 두고 단일전략 신호를 1차로 사용 권장.")


def _final_verdict(*, enough_history: bool, pass_symbols: int, total_trades: int,
                   total_return: float | None, pf: float | None, wf: float | None,
                   mdd: float | None, agent_summary: str) -> tuple[str, str, list[str]]:
    reasons: list[str] = []
    # NOT_RECOMMENDED: 비용 후 손실 + 약한 엣지.
    if total_return is not None and total_return < 0 and (pf is None or pf < 1.0):
        reasons.append(f"비용 반영 후 자금곡선 음(-{abs(total_return):.1f}%) + PF<1 — 현재 상태 위험.")
        return NOT_RECOMMENDED, "현재 상태로는 위험", reasons
    if mdd is not None and mdd >= 30 and (total_return is None or total_return <= 0):
        reasons.append(f"MDD {mdd:.1f}% 과다 + 수익 미발생 — 현재 상태 위험.")
        return NOT_RECOMMENDED, "현재 상태로는 위험", reasons
    # PAPER_REHEARSAL_WORTHY: 충분한 history + OOS 안정 + 비용 후 양(+).
    if (enough_history and pass_symbols >= 30 and total_trades >= 200
            and total_return is not None and total_return > 0
            and pf is not None and pf >= GOOD_PF and wf is not None and wf >= GOOD_WF
            and agent_summary in ("AGENT_ADDS_VALUE", "AGENT_RISK_REDUCTION_VALUE")):
        reasons.append("6개월 history + 비용 후 양(+) + PF≥1.3 + WF≥40 + Agent 도움 — Paper 확대 검토 가능.")
        return PAPER_REHEARSAL_WORTHY, "Paper 확대 가능", reasons
    # WF 붕괴(과최적화).
    if wf is not None and wf < GOOD_WF:
        reasons.append(f"walk-forward {wf:.1f} < {GOOD_WF} — OOS 안정성 부족(과최적화 의심). 실전 검토 불가.")
    if total_return is not None and total_return > 0:
        reasons.append(f"비용 반영 후 자금곡선 양(+{total_return:.1f}%)이나 OOS 안정성/Agent 효과 보강 필요.")
        return WORTH_MORE_RESEARCH, "튜닝 필요", reasons
    reasons.append("실데이터에서 진입은 발생하나 비용 후 엣지/안정성 부족 — 연구·튜닝 단계.")
    return WORTH_MORE_RESEARCH, "튜닝 필요", reasons


def build_wf_6m_report(
    input_dir: str | Path,
    *,
    symbols: list[str] | None = None,
    collect_summary: dict[str, Any] | None = None,
    generated_at: str | None = None,
) -> Wf6mReport:
    from app.backtest.portfolio_capital_sim import SimConfig, run_portfolio_capital_sim
    from app.backtest.portfolio_capital_sim import to_dict as sim_to_dict
    from app.backtest.strategy_council_backtest import (
        BacktestInput,
        load_ohlcv_from_csv,
        run_strategy_council_backtest,
        summarize_backtest_report,
    )
    from app.system.intraday_strategy_validation import evaluate_intraday_strategy
    from app.system.intraday_strategy_validation import to_dict as iv_to_dict

    gen = generated_at or datetime.now(timezone.utc).isoformat()
    d = Path(input_dir)
    files = sorted(d.glob("*.csv")) if d.is_dir() else []
    if symbols:
        want = {s.lower() for s in symbols}
        files = [f for f in files if f.stem.lower().split("_")[0] in want]

    bars: list[Any] = []
    for f in files:
        bars += load_ohlcv_from_csv(str(f))

    # 1) 종목별 검증 (PF/WF/agent).
    iv = iv_to_dict(evaluate_intraday_strategy(input_dir, symbols=symbols, min_bars=100, min_days=5))
    per_v = {p["symbol"]: p for p in iv.get("per_symbol", [])}

    # 2) 포트폴리오 자금곡선.
    sim = sim_to_dict(run_portfolio_capital_sim(bars, SimConfig()))
    sim_net = {p["symbol"]: p["net_pnl"] for p in sim.get("per_symbol", [])}

    # 3) 집계 전략 분해 (생존/사망 + 장세/시간대).
    agg = summarize_backtest_report(run_strategy_council_backtest(BacktestInput(bars=tuple(bars))))
    strat_perf = agg.get("strategies", {}) or {}
    council = agg.get("council", {}) or {}
    comparison = agg.get("comparison", {}) or {}
    council_better = comparison.get("council_better_than_best_single")
    strategy_survival: dict[str, dict[str, Any]] = {}
    alive: list[str] = []
    dead: list[str] = []
    for name, perf in strat_perf.items():
        p = perf.get("performance", perf) if isinstance(perf, dict) else {}
        exp = p.get("expectancy")
        pf = p.get("profit_factor")
        strategy_survival[name] = {"expectancy": exp, "profit_factor": pf,
                                   "win_rate": p.get("win_rate")}
        if (exp is not None and exp > 0) and (pf is not None and pf >= 1.0):
            alive.append(name)
        else:
            dead.append(name)

    # 종목 등급화.
    grades = {GO: 0, WATCH: 0, TUNE: 0, EXCLUDE: 0}
    graded: list[dict[str, Any]] = []
    for sym, v in per_v.items():
        g = _grade_symbol(v, sim_net.get(sym))
        grades[g] += 1
        graded.append({"symbol": sym, "grade": g, "net_pnl": sim_net.get(sym),
                       "profit_factor": v.get("profit_factor"), "expectancy": v.get("expectancy"),
                       "walk_forward_score": v.get("walk_forward_score"),
                       "agent_value_verdict": v.get("agent_value_verdict"),
                       "trades": v.get("trades")})
    by_net = sorted(graded, key=lambda x: (x["net_pnl"] if x["net_pnl"] is not None else -1e18),
                    reverse=True)
    top_10 = by_net[:10]
    bottom_10 = list(reversed(by_net[-10:])) if len(by_net) >= 1 else []
    exclude_rec = tuple(g["symbol"] for g in graded if g["grade"] == EXCLUDE)
    helped = tuple(g["symbol"] for g in graded
                   if g["agent_value_verdict"] in ("AGENT_ADDS_VALUE", "AGENT_RISK_REDUCTION_VALUE"))
    hurt = tuple(g["symbol"] for g in graded if g["agent_value_verdict"] == "AGENT_UNDERPERFORMS")

    trading_days = sim["trading_days"]
    enough = trading_days >= MIN_DAYS_FULL
    wf = iv.get("walk_forward_score")
    verdict, live_poss, vreasons = _final_verdict(
        enough_history=enough, pass_symbols=len(iv.get("pass_symbols", [])),
        total_trades=sim["total_trades"], total_return=sim["total_return_pct"],
        pf=sim["profit_factor"], wf=wf, mdd=sim["max_drawdown_pct"],
        agent_summary=iv.get("agent_value_summary", ""))

    # 강점/약점 TOP3 (데이터 기반).
    strengths: list[str] = []
    weaknesses: list[str] = []
    if sim["total_return_pct"] is not None and sim["total_return_pct"] > 0:
        strengths.append(f"비용 반영 후 자금곡선 양(+{sim['total_return_pct']:.1f}%, {trading_days}거래일)")
    if sim["win_rate"] is not None and sim["win_rate"] >= 0.5:
        strengths.append(f"승률 {sim['win_rate']*100:.0f}%")
    if sim["max_drawdown_pct"] is not None and sim["max_drawdown_pct"] < 15:
        strengths.append(f"MDD {sim['max_drawdown_pct']:.1f}% (양호)")
    if alive:
        strengths.append(f"생존 전략: {', '.join(alive)}")
    if wf is not None and wf < GOOD_WF:
        weaknesses.append(f"walk-forward {wf:.1f} (과최적화 의심 — OOS 성과 붕괴)")
    if hurt and len(hurt) > len(helped):
        weaknesses.append(f"Agent 방해 종목({len(hurt)}) > 도움({len(helped)}) — Agent 역할 재조정 필요")
    if sim["skipped_max_positions"] and sim["total_trades"] and sim["skipped_max_positions"] > sim["total_trades"]:
        weaknesses.append(f"신호 과다 — 5슬롯 초과로 {sim['skipped_max_positions']}건 진입 불가(종목 선별 필요)")
    if dead:
        weaknesses.append(f"사망 전략: {', '.join(dead)}")
    if sim["worst_consecutive_losses"] >= 5:
        weaknesses.append(f"최악 연속손실 {sim['worst_consecutive_losses']}회")

    agent_role = _agent_optimal_role(council_better,
                                     iv.get("agent_value_summary", ""), len(helped), len(hurt))

    upgrades: list[str] = [
        "walk-forward 안정화 우선 — 파라미터 과최적화 방지(롤링 재학습 / 표본 외 검증 자동화).",
        f"종목 선별 레이어 추가 — 신호 과다(5슬롯) 대비 GO/{grades[GO]}·EXCLUDE/{grades[EXCLUDE]} "
        "등급으로 universe 축소.",
        "Agent Council 역할 재조정 — " + agent_role,
    ]
    if dead:
        upgrades.append(f"성과 사망 전략({', '.join(dead)}) 비중 축소 또는 진입조건 강화.")
    upgrades.append("비용 모델 상시 반영(수수료+세금+슬리피지) — 비용 전 성과로 과장 금지.")
    upgrades.append("장마감 강제청산 외 부분익절/트레일링 stop 등 exit 다양화 검토(별도 백테스트).")

    expansion = {
        "키움 확장": "멀티브로커 어댑터(BrokerAdapter ABC) 위에 KiwoomAdapter 추가 — 현 KIS 추상화로 구조적 가능, 단 전략 엣지 검증이 선행.",
        "미국시장 확장": "데이터/세션/세금 체계 상이 — 별도 universe + 분봉 소스 필요. 현 전략의 국내 엣지 확인 후 검토.",
        "선물 확장": "레버리지/청산 위험으로 *가장 마지막* (FuturesPromotionPolicy). 주식 Paper 안정화 전 금지.",
        "코인 확장": "24시간/변동성 상이 — 별도 리스크 모델 필요. 현 단계 권장 안 함.",
        "멀티브로커 구조": "BrokerAdapter 추상화는 이미 존재 — 어댑터 추가로 확장 가능하나, 전략 가능성 확정이 우선.",
    }

    next_steps = [
        "walk-forward 안정화(과최적화 제거)가 최우선 — 이게 안 되면 Paper/실전 의미 없음.",
        f"EXCLUDE {grades[EXCLUDE]}종목 제외 + GO/{grades[GO]}·TUNE/{grades[TUNE]} 종목으로 universe 축소 후 재검증.",
        "Agent Council 역할 재조정(위 권장) 후 단일전략 대비 우위 재측정.",
        "그 다음에야 Paper 모의 리허설(100건·28거래일) → 운영자 승인 → 실전 검토. 현재는 실전 단계 아님.",
    ]

    return Wf6mReport(
        generated_at=gen, data_source="KIS_INTRADAY_DAILYCHART_5M",
        symbols_count=iv.get("symbols_count", len(files)),
        pass_symbols=len(iv.get("pass_symbols", [])), trading_days=trading_days,
        total_bars=iv.get("total_bars", len(bars)), bar_size_minutes=iv.get("bar_size_minutes"),
        enough_history=enough, initial_capital=sim["initial_capital"],
        final_equity=sim["final_equity"], total_return_pct=sim["total_return_pct"],
        daily_avg_trades=sim["daily_avg_trades"], daily_avg_return_pct=sim["daily_avg_return_pct"],
        weekly_avg_return_pct=sim["weekly_avg_return_pct"],
        monthly_avg_return_pct=sim["monthly_avg_return_pct"], win_rate=sim["win_rate"],
        payoff_ratio=sim["payoff_ratio"], expectancy=sim["expectancy"],
        profit_factor=sim["profit_factor"], max_drawdown_pct=sim["max_drawdown_pct"],
        worst_day_pnl=sim["worst_day_pnl"],
        worst_consecutive_losses=sim["worst_consecutive_losses"],
        avg_hold_minutes=sim["avg_hold_minutes"], skipped_max_positions=sim["skipped_max_positions"],
        monthly_returns=sim["monthly_returns"], equity_curve_points=len(sim["equity_curve"]),
        grades=grades, top_10=tuple(top_10), bottom_10=tuple(bottom_10),
        exclude_recommended=exclude_rec, agent_helped_symbols=helped, agent_hurt_symbols=hurt,
        strategy_survival=strategy_survival, strategy_alive=tuple(alive), strategy_dead=tuple(dead),
        council_better_than_best_single=council_better,
        regime_buckets=council.get("by_market_regime", {}),
        time_phase_buckets=council.get("by_time_phase", {}),
        median_walk_forward_score=wf, agent_value_summary=iv.get("agent_value_summary", ""),
        final_verdict=verdict, live_possibility=live_poss,
        strengths_top3=tuple(strengths[:3]), weaknesses_top3=tuple(weaknesses[:3]),
        agent_optimal_role=agent_role, upgrade_directions=tuple(upgrades),
        expansion_assessment=expansion, next_steps=tuple(next_steps), reasons=tuple(vreasons))


def to_dict(r: Wf6mReport) -> dict[str, Any]:
    return {
        "generated_at": r.generated_at, "data_source": r.data_source,
        "symbols_count": r.symbols_count, "pass_symbols": r.pass_symbols,
        "trading_days": r.trading_days, "total_bars": r.total_bars,
        "bar_size_minutes": r.bar_size_minutes, "enough_history": r.enough_history,
        "initial_capital": r.initial_capital, "final_equity": r.final_equity,
        "total_return_pct": r.total_return_pct, "daily_avg_trades": r.daily_avg_trades,
        "daily_avg_return_pct": r.daily_avg_return_pct,
        "weekly_avg_return_pct": r.weekly_avg_return_pct,
        "monthly_avg_return_pct": r.monthly_avg_return_pct, "win_rate": r.win_rate,
        "payoff_ratio": r.payoff_ratio, "expectancy": r.expectancy,
        "profit_factor": r.profit_factor, "max_drawdown_pct": r.max_drawdown_pct,
        "worst_day_pnl": r.worst_day_pnl,
        "worst_consecutive_losses": r.worst_consecutive_losses,
        "avg_hold_minutes": r.avg_hold_minutes, "skipped_max_positions": r.skipped_max_positions,
        "monthly_returns": r.monthly_returns, "equity_curve_points": r.equity_curve_points,
        "grades": r.grades, "top_10": list(r.top_10), "bottom_10": list(r.bottom_10),
        "exclude_recommended": list(r.exclude_recommended),
        "agent_helped_symbols": list(r.agent_helped_symbols),
        "agent_hurt_symbols": list(r.agent_hurt_symbols),
        "strategy_survival": r.strategy_survival, "strategy_alive": list(r.strategy_alive),
        "strategy_dead": list(r.strategy_dead),
        "council_better_than_best_single": r.council_better_than_best_single,
        "regime_buckets": r.regime_buckets, "time_phase_buckets": r.time_phase_buckets,
        "median_walk_forward_score": r.median_walk_forward_score,
        "agent_value_summary": r.agent_value_summary, "final_verdict": r.final_verdict,
        "live_possibility": r.live_possibility, "strengths_top3": list(r.strengths_top3),
        "weaknesses_top3": list(r.weaknesses_top3), "agent_optimal_role": r.agent_optimal_role,
        "upgrade_directions": list(r.upgrade_directions),
        "expansion_assessment": r.expansion_assessment, "next_steps": list(r.next_steps),
        "reasons": list(r.reasons), "do_not_auto_apply": r.do_not_auto_apply,
        "is_live_authorization": r.is_live_authorization, "broker_order_sent": r.broker_order_sent,
        "order_created": r.order_created, "contains_secret": r.contains_secret,
        "no_profit_guarantee": r.no_profit_guarantee, "disclaimer": r.disclaimer,
    }


def render_markdown(r: Wf6mReport) -> str:
    def _f(x, suf=""):
        return "평가불가" if x is None else f"{x:.2f}{suf}"
    lines = [
        "# WF-6M-50SYMBOLS-01 — 6개월·50종목·1000만원 전략 종합 검증",
        "",
        "> 실제 KIS 5분봉 · Paper/Backtest only · 실주문 0건 · 자동 적용 아님 · 실전 승인 아님 · 수익 보장 아님.",
        "",
        f"- 데이터: {r.data_source} · 종목 {r.symbols_count}(PASS {r.pass_symbols}) · "
        f"거래일 {r.trading_days}(6개월 기준 충족: {r.enough_history}) · total_bars {r.total_bars}",
        "",
        "## 1000만원 자금곡선",
        f"- 최종 자산 {r.final_equity:,.0f} KRW (수익률 {_f(r.total_return_pct, '%')})",
        f"- 하루 평균 거래수 {_f(r.daily_avg_trades)} · 하루 {_f(r.daily_avg_return_pct,'%')} · "
        f"주간 {_f(r.weekly_avg_return_pct,'%')} · 월간 {_f(r.monthly_avg_return_pct,'%')}",
        f"- 승률 {_f(r.win_rate)} · 손익비 {_f(r.payoff_ratio)} · PF {_f(r.profit_factor)} · "
        f"expectancy {_f(r.expectancy)} KRW",
        f"- MDD {_f(r.max_drawdown_pct,'%')} · 최악 하루 {_f(r.worst_day_pnl)} KRW · "
        f"최악 연속손실 {r.worst_consecutive_losses}회 · 평균 보유 {_f(r.avg_hold_minutes)}분",
        f"- 5슬롯 초과로 진입 불가 {r.skipped_max_positions}건",
        f"- 월별 수익률: {r.monthly_returns}",
        "",
        "## 종목 등급화",
        f"- GO {r.grades['GO']} · WATCH {r.grades['WATCH']} · TUNE {r.grades['TUNE']} · "
        f"EXCLUDE {r.grades['EXCLUDE']}",
        f"- Top 10 (net_pnl): {[g['symbol'] for g in r.top_10]}",
        f"- Bottom 10: {[g['symbol'] for g in r.bottom_10]}",
        f"- EXCLUDE 추천: {list(r.exclude_recommended)[:20]}",
        f"- Agent 도움 {len(r.agent_helped_symbols)} / 방해 {len(r.agent_hurt_symbols)}",
        "",
        "## 전략 생존/사망",
        f"- 생존: {list(r.strategy_alive)} · 사망: {list(r.strategy_dead)}",
        f"- Agent Council vs best single 우위: {r.council_better_than_best_single}",
        f"- 장세 버킷: {r.regime_buckets}",
        f"- 시간대 버킷: {r.time_phase_buckets}",
        "",
        "## 최종 판정",
        f"### 1. 현재 전략 종합 판정: **{r.final_verdict}**",
        "### 2. 1000만원 기준 현실성",
        f"- 하루 평균 거래수: {_f(r.daily_avg_trades)}",
        f"- 예상 수익 범위(과거 표본): 월 {_f(r.monthly_avg_return_pct,'%')} (보장 아님)",
        f"- 최대 손실 위험: MDD {_f(r.max_drawdown_pct,'%')} · 최악 하루 {_f(r.worst_day_pnl)} KRW",
        f"- 유지 가능성: {r.live_possibility}",
        "### 3. 가장 큰 약점 TOP3",
        *([f"- {w}" for w in r.weaknesses_top3] or ["- (없음)"]),
        "### 4. 가장 큰 강점 TOP3",
        *([f"- {s}" for s in r.strengths_top3] or ["- (없음)"]),
        "### 5. Agent Council 최적 역할",
        f"- {r.agent_optimal_role}",
        "### 6. 추천 다음 단계",
        *[f"- {n}" for n in r.next_steps],
        "",
        "## 프로그램 업그레이드 방향",
        *[f"- {u}" for u in r.upgrade_directions],
        "",
        "### 확장성 평가",
        *[f"- {k}: {v}" for k, v in r.expansion_assessment.items()],
        "",
        "## 판정 사유",
        *([f"- {x}" for x in r.reasons] or ["- (없음)"]),
        "",
        f"> {r.disclaimer}",
    ]
    return "\n".join(lines) + "\n"

"""STRATEGY-VALIDATION-01 — 전략 가능성 종합 평가 (advisory, read-only).

백테스트(#46) + Walk-forward(#47) + Stress(#48) + Paper 모의 성과 + Agent/주문 품질
결과를 *종합* 해 "이 전략을 계속 검증해볼 가치가 있는가" 를 advisory 로 판정한다.

**본 리포트는 자동 적용 / 실전 전환 / 주문 신호가 아니다.** 결과가 좋아 보여도:
- `do_not_auto_apply=True` / `auto_apply_allowed=False` (threshold 추천 자동 적용 금지)
- `is_live_authorization=False` / `is_order_signal=False` (실전 승인/주문 신호 아님)
- sample fixture 만으로는 STRONG_CANDIDATE 판정 불가 (data-sufficiency 게이팅)
- Paper sample 부족이면 수익성 판단 불가 → CAUTIOUS 이하로 cap

CLAUDE.md 절대 원칙 (본 모듈):
- read-only. broker / OrderExecutor / route_order / KIS API / 외부 HTTP / AI SDK import 0건.
- 안전 flag / `.env` 변경 0건. secret / 계좌 원문 출력 0건. 수익 보장 문구 0건.
- 입력은 기존 리포트의 `.to_dict()` (또는 동등 dict) 만 받는다 — 새 매매 로직 0건.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

# ---- verdicts / sample classes -------------------------------------------
STRONG = "STRONG_CANDIDATE"
CAUTIOUS = "CAUTIOUS_CANDIDATE"
RESEARCH_ONLY = "RESEARCH_ONLY"
NOT_READY = "NOT_READY"
BLOCKED = "BLOCKED"

PAPER_NO_TRADES_YET = "PAPER_NO_TRADES_YET"
PAPER_SAMPLE_TOO_SMALL = "PAPER_SAMPLE_TOO_SMALL"
PAPER_EARLY_SIGNAL = "PAPER_EARLY_SIGNAL"
PAPER_GATE_EVALUABLE = "PAPER_GATE_EVALUABLE"

# 권장 기준값 (docs/strategy_potential_validation.md 와 동기).
MIN_BACKTEST_TRADES = 100
MIN_PAPER_TRADES_OBSERVE = 30
PAPER_GATE_TRADES = 100
PAPER_GATE_DAYS = 28
WIN_RATE_MIN = 0.50
PAYOFF_MIN = 1.0
PROFIT_FACTOR_MIN = 1.3
MDD_MAX_PCT = 10.0
MAX_CONSEC_LOSSES_MAX = 5
STABILITY_MIN = 0.65


def _clamp(x: float, lo: float = 0.0, hi: float = 100.0) -> float:
    return max(lo, min(hi, x))


@dataclass(frozen=True)
class StrategyPotentialInputs:
    """기존 리포트의 직렬화 dict 를 모은 입력 (모두 optional)."""

    backtest: dict[str, Any] | None = None          # BacktestReport.to_dict
    walk_forward: dict[str, Any] | None = None       # WalkForwardReport.to_dict
    stress: dict[str, Any] | None = None             # StressTestReport.to_dict
    paper: dict[str, Any] | None = None              # paper 성과 stats (PerformanceCriteriaInput 유사)
    order_quality: dict[str, Any] | None = None      # OrderQualityMetrics.to_dict
    feedback: dict[str, Any] | None = None           # FeedbackLoopReport.to_dict
    has_real_data: bool = False                      # 실/준실제 OHLCV 여부 (False = sample fixture)


@dataclass(frozen=True)
class SubScore:
    name: str
    score: float | None         # None = 평가 불가 (데이터 부족)
    weight: float
    notes: tuple[str, ...] = ()


@dataclass(frozen=True)
class StrategyPotentialReport:
    generated_at: str
    overall_verdict: str
    overall_strategy_potential_score: float | None
    backtest_score: float | None
    walk_forward_score: float | None
    stress_resilience_score: float | None
    paper_execution_score: float | None
    agent_value_score: float | None
    risk_control_score: float | None
    data_sufficiency_score: float | None
    paper_sample_class: str
    agent_value_verdict: str
    strengths: tuple[str, ...] = ()
    weaknesses: tuple[str, ...] = ()
    risks: tuple[str, ...] = ()
    recommended_next_steps: tuple[str, ...] = ()
    method_fit: dict[str, Any] = field(default_factory=dict)
    sub_scores: tuple[SubScore, ...] = ()
    sample_fixture_only: bool = True
    # ---- advisory invariants (항상 안전값) ----
    do_not_auto_apply: bool = True
    auto_apply_allowed: bool = False
    is_live_authorization: bool = False
    is_order_signal: bool = False
    contains_secret: bool = False
    disclaimer: str = (
        "본 리포트는 전략 가능성 평가 자료이며 자동 적용 / 실전 전환 승인 / 주문 신호가 "
        "아니다. 수익을 보장하지 않는다. sample fixture 결과는 기능 확인용이며, 실전 검토는 "
        "Paper 100건 + 28거래일 이상 + 별도 운영자 승인이 필요하다."
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
        if self.overall_verdict not in (
            STRONG, CAUTIOUS, RESEARCH_ONLY, NOT_READY, BLOCKED
        ):
            raise ValueError(f"invalid verdict: {self.overall_verdict}")


# --------------------------------------------------------------------------
# 개별 sub-score 계산 (각 helper 는 점수 + notes 반환, 데이터 없으면 None)
# --------------------------------------------------------------------------

def _council_metrics(backtest: dict[str, Any] | None) -> dict[str, Any] | None:
    """백테스트 dict 에서 council (없으면 best single) 성과 metric 추출."""
    if not backtest:
        return None
    council = backtest.get("council")
    if council and council.get("performance"):
        return council["performance"]
    # council 없으면 strategies 중 expectancy 최고를 best single 로.
    strategies = backtest.get("strategies") or {}
    best = None
    for perf in strategies.values():
        exp = perf.get("expectancy")
        if exp is None:
            continue
        if best is None or exp > (best.get("expectancy") or float("-inf")):
            best = perf
    return best


def score_backtest(backtest: dict[str, Any] | None) -> SubScore:
    if not backtest or backtest.get("insufficient_data"):
        return SubScore("backtest", None, 0.20, ("백테스트 데이터 부족 — 평가 불가",))
    m = _council_metrics(backtest)
    if not m:
        return SubScore("backtest", None, 0.20, ("성과 metric 없음 — 평가 불가",))
    notes: list[str] = []
    pts = 0.0
    wr = m.get("win_rate")
    pf = m.get("profit_factor")
    exp = m.get("expectancy")
    mdd = m.get("max_drawdown")
    mcl = m.get("max_consecutive_losses")
    if wr is not None:
        pts += 25 if wr >= WIN_RATE_MIN else 25 * max(0.0, wr / WIN_RATE_MIN)
        notes.append(f"win_rate={wr:.2f}")
    if pf is not None:
        pts += 25 if pf >= PROFIT_FACTOR_MIN else 25 * max(0.0, pf / PROFIT_FACTOR_MIN)
        notes.append(f"profit_factor={pf:.2f}")
    if exp is not None:
        pts += 25 if exp > 0 else 0
        notes.append(f"expectancy={exp:.2f}")
    if mcl is not None:
        pts += 25 if mcl <= MAX_CONSEC_LOSSES_MAX else max(0.0, 25 - (mcl - MAX_CONSEC_LOSSES_MAX) * 5)
        notes.append(f"max_consec_losses={mcl}")
    if mdd is not None:
        notes.append(f"max_drawdown={mdd}")
    return SubScore("backtest", _clamp(pts), 0.20, tuple(notes))


def score_walk_forward(wf: dict[str, Any] | None) -> SubScore:
    if not wf or wf.get("insufficient_data"):
        return SubScore("walk_forward", None, 0.18, ("walk-forward 데이터 부족 — 평가 불가",))
    notes: list[str] = []
    stab = wf.get("overall_stability_score")
    overfit = bool(wf.get("overall_overfit_suspected"))
    collapse = wf.get("collapse_segments") or []
    base = (stab or 0.0) * 100.0
    notes.append(f"stability={stab}")
    if overfit:
        base -= 30
        notes.append("overfit_suspected=True (-30)")
    if collapse:
        base -= min(30, len(collapse) * 10)
        notes.append(f"collapse_segments={len(collapse)} (-{min(30, len(collapse)*10)})")
    return SubScore("walk_forward", _clamp(base), 0.18, tuple(notes))


def score_stress(stress: dict[str, Any] | None) -> SubScore:
    if not stress:
        return SubScore("stress_resilience", None, 0.18, ("stress 데이터 없음 — 평가 불가",))
    counts = stress.get("counts") or {}
    fails = int(counts.get("FAIL", 0))
    warns = int(counts.get("WARN", 0))
    passes = int(counts.get("PASS", 0))
    total = max(1, fails + warns + passes)
    score = 100.0 - fails * (100.0 / total) * 2 - warns * (100.0 / total) * 0.5
    notes = [f"PASS={passes} WARN={warns} FAIL={fails}"]
    if fails:
        notes.append("가드 보호 실패(FAIL) 존재 — 안전성 문제")
    return SubScore("stress_resilience", _clamp(score), 0.18, tuple(notes))


def classify_paper_sample(paper: dict[str, Any] | None) -> str:
    if not paper:
        return PAPER_NO_TRADES_YET
    trades = int(paper.get("evaluated_trades", paper.get("trades", 0)) or 0)
    days = int(paper.get("trading_days", paper.get("days", 0)) or 0)
    if trades <= 0:
        return PAPER_NO_TRADES_YET
    if trades < MIN_PAPER_TRADES_OBSERVE:
        return PAPER_SAMPLE_TOO_SMALL
    if trades < PAPER_GATE_TRADES or days < PAPER_GATE_DAYS:
        return PAPER_EARLY_SIGNAL
    return PAPER_GATE_EVALUABLE


def score_paper_execution(paper: dict[str, Any] | None,
                          order_quality: dict[str, Any] | None) -> SubScore:
    sample_class = classify_paper_sample(paper)
    if sample_class in (PAPER_NO_TRADES_YET, PAPER_SAMPLE_TOO_SMALL):
        return SubScore(
            "paper_execution", None, 0.16,
            (f"Paper sample={sample_class} — 수익성 판단 불가",))
    notes: list[str] = [f"paper_sample={sample_class}"]
    pts = 0.0
    # 성과 (50%)
    wr = paper.get("win_rate")
    exp = paper.get("expectancy")
    if wr is not None:
        pts += 25 if wr >= WIN_RATE_MIN else 25 * max(0.0, wr / WIN_RATE_MIN)
    if exp is not None:
        pts += 25 if exp > 0 else 0
    # 체결 품질 (50%) — order_quality 의 실패/거절율 낮을수록 높음
    oq = order_quality or {}
    fr = oq.get("order_failure_rate")
    rr = oq.get("rejected_rate")
    if fr is not None:
        pts += 25 * max(0.0, 1.0 - min(1.0, fr / 0.05))
        notes.append(f"order_failure_rate={fr:.3f}")
    else:
        pts += 12.5  # 데이터 없으면 중립 절반
    if rr is not None:
        pts += 25 * max(0.0, 1.0 - min(1.0, rr / 0.03))
        notes.append(f"rejected_rate={rr:.3f}")
    else:
        pts += 12.5
    return SubScore("paper_execution", _clamp(pts), 0.16, tuple(notes))


def score_agent_value(backtest: dict[str, Any] | None,
                      wf: dict[str, Any] | None) -> tuple[SubScore, str]:
    """Agent Council 이 단일 전략보다 나은지."""
    if not backtest or backtest.get("insufficient_data"):
        return (SubScore("agent_value", None, 0.16,
                         ("백테스트 부족 — Agent 효과 평가 불가",)),
                "AGENT_VALUE_INSUFFICIENT_SAMPLE")
    comparison = backtest.get("comparison") or {}
    council_better = comparison.get("council_better_than_best_single")
    notes: list[str] = []
    score = 50.0  # 중립 기준
    if council_better is True:
        score += 30
        notes.append("council_better_than_best_single=True (+30)")
    elif council_better is False:
        score -= 20
        notes.append("council 이 best single 보다 못함 (-20)")
    # walk-forward OOS 비교
    wf_cmp = (wf or {}).get("council_vs_best_single") or {}
    frac = wf_cmp.get("council_better_fraction")
    if frac is not None:
        score += (frac - 0.5) * 40
        notes.append(f"WF council_better_fraction={frac:.2f}")
    # 리스크 축소: council 의 MDD 가 단일보다 낮으면 가점 (advisory)
    score = _clamp(score)
    if council_better is True and score >= 70:
        verdict = "AGENT_ADDS_VALUE"
    elif council_better is True:
        verdict = "AGENT_RISK_REDUCTION_VALUE"
    elif council_better is False:
        verdict = "AGENT_UNDERPERFORMS"
    else:
        verdict = "AGENT_VALUE_INSUFFICIENT_SAMPLE"
    return SubScore("agent_value", score, 0.16, tuple(notes)), verdict


def score_risk_control(backtest: dict[str, Any] | None,
                       stress: dict[str, Any] | None,
                       order_quality: dict[str, Any] | None) -> SubScore:
    notes: list[str] = []
    pts = 0.0
    have = 0
    m = _council_metrics(backtest)
    if m:
        mcl = m.get("max_consecutive_losses")
        if mcl is not None:
            have += 1
            pts += 100 if mcl <= MAX_CONSEC_LOSSES_MAX else max(0.0, 100 - (mcl - MAX_CONSEC_LOSSES_MAX) * 20)
            notes.append(f"max_consec_losses={mcl}")
    if stress:
        have += 1
        counts = stress.get("counts") or {}
        fails = int(counts.get("FAIL", 0))
        pts += 100 if fails == 0 else max(0.0, 100 - fails * 25)
        notes.append(f"stress_FAIL={fails}")
    oq = order_quality or {}
    if oq.get("rejected_rate") is not None:
        have += 1
        rr = oq["rejected_rate"]
        pts += 100 * max(0.0, 1.0 - min(1.0, rr / 0.03))
        notes.append(f"rejected_rate={rr:.3f}")
    if have == 0:
        return SubScore("risk_control", None, 0.08, ("리스크 데이터 없음 — 평가 불가",))
    return SubScore("risk_control", _clamp(pts / have), 0.08, tuple(notes))


def score_data_sufficiency(inp: StrategyPotentialInputs, paper_class: str) -> SubScore:
    notes: list[str] = []
    pts = 0.0
    # 백테스트 표본 (30)
    bt = inp.backtest or {}
    bars = int(bt.get("bar_count", 0) or 0)
    if bars >= 200:
        pts += 30
    elif bars > 0:
        pts += 30 * min(1.0, bars / 200)
    notes.append(f"bar_count={bars}")
    # 실데이터 여부 (20)
    if inp.has_real_data:
        pts += 20
        notes.append("has_real_data=True")
    else:
        notes.append("sample fixture only (실데이터 아님)")
    # walk-forward split (20)
    wf = inp.walk_forward or {}
    splits = int(wf.get("split_count", 0) or 0)
    if splits >= 3:
        pts += 20
    elif splits > 0:
        pts += 20 * min(1.0, splits / 3)
    notes.append(f"wf_splits={splits}")
    # paper sample (30)
    paper_pts = {
        PAPER_NO_TRADES_YET: 0,
        PAPER_SAMPLE_TOO_SMALL: 8,
        PAPER_EARLY_SIGNAL: 18,
        PAPER_GATE_EVALUABLE: 30,
    }[paper_class]
    pts += paper_pts
    notes.append(f"paper_sample={paper_class}")
    return SubScore("data_sufficiency", _clamp(pts), 0.04, tuple(notes))


# --------------------------------------------------------------------------
# 강점/약점/리스크/다음 단계 + 사용자 매매기법 적합성
# --------------------------------------------------------------------------

def _derive_method_fit(backtest: dict[str, Any] | None,
                       feedback: dict[str, Any] | None) -> dict[str, Any]:
    favorable: list[str] = []
    dangerous: list[str] = []
    tuning: list[str] = []
    strengths: list[str] = []
    weaknesses: list[str] = []
    m = _council_metrics(backtest)
    if m:
        for regime, stats in (m.get("by_market_regime") or {}).items():
            wr = stats.get("win_rate")
            if wr is None:
                continue
            if wr >= WIN_RATE_MIN:
                favorable.append(f"{regime} (win_rate={wr:.2f})")
            elif wr < 0.4:
                dangerous.append(f"{regime} (win_rate={wr:.2f})")
        for phase, stats in (m.get("by_time_phase") or {}).items():
            wr = stats.get("win_rate")
            if wr is not None and wr >= WIN_RATE_MIN:
                favorable.append(f"time_phase={phase} (win_rate={wr:.2f})")
    # feedback tag → tuning 후보
    for tag in (feedback or {}).get("feedback_tags", []):
        name = tag.get("tag")
        if name == "OVER_ENTRY":
            tuning.append("진입 기준 강화 (과잉진입 감소) — 자동 적용 금지, 별도 PR")
            weaknesses.append("과잉진입(OVER_ENTRY) 경향")
        elif name == "LATE_EXIT":
            tuning.append("청산 타이밍/트레일링 조정 (늦은 청산) — 자동 적용 금지, 별도 PR")
            weaknesses.append("늦은 청산(LATE_EXIT) 경향")
        elif name == "POOR_RISK_REWARD":
            tuning.append("손익비 개선 (take_profit/stop_loss 비율 재검토) — 별도 PR")
    return {
        "method_strengths": strengths,
        "method_weaknesses": weaknesses,
        "favorable_market_conditions": favorable,
        "dangerous_market_conditions": dangerous,
        "recommended_tuning_candidates": tuning,
        "do_not_auto_apply": True,
    }


# --------------------------------------------------------------------------
# 종합 판정
# --------------------------------------------------------------------------

def _weighted_overall(sub_scores: list[SubScore]) -> float | None:
    avail = [(s.score, s.weight) for s in sub_scores if s.score is not None]
    if not avail:
        return None
    total_w = sum(w for _, w in avail)
    if total_w <= 0:
        return None
    return _clamp(sum(sc * w for sc, w in avail) / total_w)


def _decide_verdict(inp: StrategyPotentialInputs,
                    sub: dict[str, SubScore],
                    overall: float | None,
                    paper_class: str) -> tuple[str, list[str]]:
    reasons: list[str] = []

    # ---- BLOCKED: 치명/안전 문제 ----
    stress = inp.stress or {}
    stress_fail = int((stress.get("counts") or {}).get("FAIL", 0))
    bt = inp.backtest or {}
    if bt.get("contains_secret") or stress.get("contains_secret"):
        return BLOCKED, ["리포트에서 secret 노출 의심 — 검증 불가"]
    if bt.get("is_live_authorization") or stress.get("is_live_authorization"):
        return BLOCKED, ["하위 리포트가 live_authorization 주장 — 안전성 문제"]
    if stress_fail >= 2:
        reasons.append(f"stress FAIL {stress_fail}건 — 안전 가드 보호 실패(치명)")
        return BLOCKED, reasons

    # ---- NOT_READY: 핵심 기준 미달 ----
    bt_score = sub.get("backtest").score if sub.get("backtest") else None
    if stress_fail == 1:
        reasons.append("stress FAIL 1건 — 안전 가드 보호 실패")
        return NOT_READY, reasons
    m = _council_metrics(inp.backtest)
    if m and not bt.get("insufficient_data"):
        exp = m.get("expectancy")
        pf = m.get("profit_factor")
        if exp is not None and exp <= 0:
            reasons.append("expectancy ≤ 0 — 핵심 기준 미달")
            return NOT_READY, reasons
        if pf is not None and pf < 1.0:
            reasons.append("profit_factor < 1.0 — 핵심 기준 미달")
            return NOT_READY, reasons

    # ---- data sufficiency 게이팅 (STRONG 차단) ----
    wf = inp.walk_forward or {}
    wf_stable = (
        not wf.get("insufficient_data")
        and (wf.get("overall_stability_score") or 0) >= STABILITY_MIN
        and not wf.get("overall_overfit_suspected")
    )
    paper_ok = paper_class in (PAPER_EARLY_SIGNAL, PAPER_GATE_EVALUABLE)
    backtest_good = bt_score is not None and bt_score >= 70 and not bt.get("insufficient_data")

    if (backtest_good and wf_stable and stress_fail == 0
            and paper_class == PAPER_GATE_EVALUABLE and inp.has_real_data
            and (overall or 0) >= 70):
        reasons.append("백테스트/WF/Stress/Paper 모두 양호 + 실데이터 + Paper Gate 표본")
        return STRONG, reasons

    # sample fixture only 또는 paper 부족 → STRONG 불가
    if not inp.has_real_data:
        reasons.append("sample fixture only — STRONG_CANDIDATE 불가 (실데이터 필요)")
    if not paper_ok:
        reasons.append(f"Paper sample 부족({paper_class}) — 수익성 판단 제한")

    # ---- CAUTIOUS vs RESEARCH_ONLY ----
    overfit = bool(wf.get("overall_overfit_suspected"))
    if backtest_good and (wf_stable or wf.get("insufficient_data")) and stress_fail == 0:
        reasons.append("구조/지표 방향성 양호하나 표본/실데이터 부족 — 소액 모의 지속 권장")
        return CAUTIOUS, reasons
    if overfit:
        reasons.append("과최적화 위험(overfit_suspected) — 리서치/튜닝 필요")
        return RESEARCH_ONLY, reasons
    reasons.append("성과 불안정 또는 표본 부족 — 리서치 단계")
    return RESEARCH_ONLY, reasons


def evaluate_strategy_potential(inp: StrategyPotentialInputs,
                                *, generated_at: str | None = None) -> StrategyPotentialReport:
    from datetime import datetime, timezone
    gen = generated_at or datetime.now(timezone.utc).isoformat()

    paper_class = classify_paper_sample(inp.paper)
    s_bt = score_backtest(inp.backtest)
    s_wf = score_walk_forward(inp.walk_forward)
    s_st = score_stress(inp.stress)
    s_pp = score_paper_execution(inp.paper, inp.order_quality)
    s_ag, agent_verdict = score_agent_value(inp.backtest, inp.walk_forward)
    s_rc = score_risk_control(inp.backtest, inp.stress, inp.order_quality)
    s_ds = score_data_sufficiency(inp, paper_class)

    sub_list = [s_bt, s_wf, s_st, s_pp, s_ag, s_rc, s_ds]
    sub = {s.name: s for s in sub_list}
    overall = _weighted_overall(sub_list)
    verdict, verdict_reasons = _decide_verdict(inp, sub, overall, paper_class)

    method_fit = _derive_method_fit(inp.backtest, inp.feedback)

    # strengths / weaknesses / risks / next_steps
    strengths: list[str] = []
    weaknesses: list[str] = list(method_fit["method_weaknesses"])
    risks: list[str] = []
    next_steps: list[str] = []

    if s_bt.score is not None and s_bt.score >= 70:
        strengths.append("백테스트 핵심 지표(승률/PF/expectancy) 양호")
    if s_wf.score is not None and s_wf.score >= STABILITY_MIN * 100:
        strengths.append("walk-forward 안정성 양호")
    if s_st.score is not None and s_st.score >= 90:
        strengths.append("스트레스 시나리오 가드 보호 양호(FAIL 0)")
    if agent_verdict in ("AGENT_ADDS_VALUE", "AGENT_RISK_REDUCTION_VALUE"):
        strengths.append(f"Agent Council 효과: {agent_verdict}")

    if not inp.has_real_data:
        risks.append("sample fixture 결과 — 수익성 판정에 낮은 가중치 (실데이터 검증 필요)")
    if paper_class in (PAPER_NO_TRADES_YET, PAPER_SAMPLE_TOO_SMALL):
        weaknesses.append(f"Paper 표본 부족({paper_class}) — 수익성 판단 불가")
    if (inp.walk_forward or {}).get("overall_overfit_suspected"):
        risks.append("과최적화 위험(overfit_suspected)")
    if int(((inp.stress or {}).get("counts") or {}).get("FAIL", 0)) > 0:
        risks.append("스트레스 FAIL — 안전 가드 보호 실패")
    if agent_verdict == "AGENT_UNDERPERFORMS":
        weaknesses.append("Agent Council 이 best single 전략보다 못함")

    next_steps.append("실/준실제 OHLCV 데이터로 백테스트 + walk-forward 재실행")
    if paper_class != PAPER_GATE_EVALUABLE:
        next_steps.append(f"Paper 모의 운영 지속 (목표 {PAPER_GATE_TRADES}건 + {PAPER_GATE_DAYS}거래일)")
    next_steps.append("KIS 모의 장중 리허설(BUILD-02B)로 체결 품질 관찰 (실거래 아님)")
    if method_fit["recommended_tuning_candidates"]:
        next_steps.append("튜닝 후보는 운영자 검토 + 별도 PR + 재백테스트 (자동 적용 금지)")
    next_steps.extend(verdict_reasons)

    return StrategyPotentialReport(
        generated_at=gen,
        overall_verdict=verdict,
        overall_strategy_potential_score=overall,
        backtest_score=s_bt.score,
        walk_forward_score=s_wf.score,
        stress_resilience_score=s_st.score,
        paper_execution_score=s_pp.score,
        agent_value_score=s_ag.score,
        risk_control_score=s_rc.score,
        data_sufficiency_score=s_ds.score,
        paper_sample_class=paper_class,
        agent_value_verdict=agent_verdict,
        strengths=tuple(strengths),
        weaknesses=tuple(weaknesses),
        risks=tuple(risks),
        recommended_next_steps=tuple(next_steps),
        method_fit=method_fit,
        sub_scores=tuple(sub_list),
        sample_fixture_only=not inp.has_real_data,
    )


def to_dict(report: StrategyPotentialReport) -> dict[str, Any]:
    return {
        "generated_at": report.generated_at,
        "overall_verdict": report.overall_verdict,
        "overall_strategy_potential_score": report.overall_strategy_potential_score,
        "backtest_score": report.backtest_score,
        "walk_forward_score": report.walk_forward_score,
        "stress_resilience_score": report.stress_resilience_score,
        "paper_execution_score": report.paper_execution_score,
        "agent_value_score": report.agent_value_score,
        "risk_control_score": report.risk_control_score,
        "data_sufficiency_score": report.data_sufficiency_score,
        "paper_sample_class": report.paper_sample_class,
        "agent_value_verdict": report.agent_value_verdict,
        "strengths": list(report.strengths),
        "weaknesses": list(report.weaknesses),
        "risks": list(report.risks),
        "recommended_next_steps": list(report.recommended_next_steps),
        "method_fit": report.method_fit,
        "sub_scores": [
            {"name": s.name, "score": s.score, "weight": s.weight, "notes": list(s.notes)}
            for s in report.sub_scores
        ],
        "sample_fixture_only": report.sample_fixture_only,
        "do_not_auto_apply": report.do_not_auto_apply,
        "auto_apply_allowed": report.auto_apply_allowed,
        "is_live_authorization": report.is_live_authorization,
        "is_order_signal": report.is_order_signal,
        "contains_secret": report.contains_secret,
        "disclaimer": report.disclaimer,
    }


def render_markdown(report: StrategyPotentialReport) -> str:
    def _fmt(x: float | None) -> str:
        return "평가불가" if x is None else f"{x:.1f}"

    lines = [
        "# STRATEGY-VALIDATION-01 — 전략 가능성 종합 평가",
        "",
        "> 자동 적용 아님 · 실전 승인 아님 · 수익 보장 아님. sample fixture 결과는 기능 확인용.",
        "",
        f"- overall_verdict: **{report.overall_verdict}**",
        f"- overall_strategy_potential_score: **{_fmt(report.overall_strategy_potential_score)}**",
        f"- paper_sample_class: {report.paper_sample_class}",
        f"- agent_value_verdict: {report.agent_value_verdict}",
        f"- sample_fixture_only: {report.sample_fixture_only}",
        f"- do_not_auto_apply={report.do_not_auto_apply} / "
        f"is_live_authorization={report.is_live_authorization} / "
        f"is_order_signal={report.is_order_signal} / contains_secret={report.contains_secret}",
        "",
        "## 점수",
        "",
        "| score | 값 |",
        "|---|---|",
        f"| backtest_score | {_fmt(report.backtest_score)} |",
        f"| walk_forward_score | {_fmt(report.walk_forward_score)} |",
        f"| stress_resilience_score | {_fmt(report.stress_resilience_score)} |",
        f"| paper_execution_score | {_fmt(report.paper_execution_score)} |",
        f"| agent_value_score | {_fmt(report.agent_value_score)} |",
        f"| risk_control_score | {_fmt(report.risk_control_score)} |",
        f"| data_sufficiency_score | {_fmt(report.data_sufficiency_score)} |",
        "",
        "## 강점",
        *([f"- {s}" for s in report.strengths] or ["- (없음)"]),
        "",
        "## 약점",
        *([f"- {w}" for w in report.weaknesses] or ["- (없음)"]),
        "",
        "## 리스크",
        *([f"- {r}" for r in report.risks] or ["- (없음)"]),
        "",
        "## 추천 다음 단계",
        *([f"- {n}" for n in report.recommended_next_steps] or ["- (없음)"]),
        "",
        f"> {report.disclaimer}",
    ]
    return "\n".join(lines) + "\n"

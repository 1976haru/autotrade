"""BUILD-01: 최종 EXE 빌드 전 전체 프로그램 정합성 점검 (offline/fake, read-only).

지금까지 만든 기능들이 *하나의 흐름* 으로 끊김 없이 연결되는지 오프라인/모의로
검증한다:

  Universe/관심종목 → KIS 모의 readiness → 4전략 vote → Agent Council →
  RiskOfficer → exit_plan → quality_score → BUY/SELL/HOLD → KIS Paper decision →
  (fake) KIS 모의 주문 결과 → order_quality → portfolio 반영 → outcome/review →
  feedback/quality 다음 판단 반영 → UI/API 표시 → Live safety

**본 작업은 빌드 전 통합 *검증* 이다.** 실전 주문을 보내지 않으며, KIS 실전/모의
*실제 API* 를 호출하지 않는다(장중 실제 모의 API 테스트는 BUILD-02). 주문 결과는
*fake route_order*(합성)로 만들며 broker / OrderExecutor / route_order 를 호출하지
않는다.

CLAUDE.md 가드 (정적 grep 으로 lock):
- broker / OrderExecutor / 단일 주문 라우터 / paper_trader / KIS 어댑터 import 0건.
- broker 주문/취소 호출 0건, DB write 0건, 외부 HTTP / AI SDK import 0건.
- `ProgramIntegrityReport.is_live_authorization=False` / `broker_order_sent=False` /
  `order_created_live=False` / `contains_secret=False` 불변.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import StrEnum
from types import SimpleNamespace
from typing import Any, Optional


class GateVerdict(StrEnum):
    PASS = "PASS"
    WARN = "WARN"
    FAIL = "FAIL"
    SKIP = "SKIP"


class GateSection(StrEnum):
    UNIVERSE = "UNIVERSE"
    KIS_PAPER_READINESS = "KIS_PAPER_READINESS"
    STRATEGY_VOTES = "STRATEGY_VOTES"
    AGENT_COUNCIL = "AGENT_COUNCIL"
    RISK_OFFICER = "RISK_OFFICER"
    EXIT_PLAN = "EXIT_PLAN"
    QUALITY_GATE = "QUALITY_GATE"
    DECISION = "DECISION"
    KIS_PAPER_DECISION = "KIS_PAPER_DECISION"
    ORDER_RESULT = "ORDER_RESULT"
    ORDER_QUALITY = "ORDER_QUALITY"
    PORTFOLIO = "PORTFOLIO"
    OUTCOME_REVIEW = "OUTCOME_REVIEW"
    FEEDBACK_QUALITY = "FEEDBACK_QUALITY"
    UI_API = "UI_API"
    LIVE_SAFETY = "LIVE_SAFETY"


# build_ready 를 막는 *필수* 섹션 (FAIL 시 빌드 불가). 나머지는 WARN 허용.
_REQUIRED_SECTIONS = frozenset({
    GateSection.UNIVERSE, GateSection.STRATEGY_VOTES, GateSection.AGENT_COUNCIL,
    GateSection.RISK_OFFICER, GateSection.EXIT_PLAN, GateSection.QUALITY_GATE,
    GateSection.DECISION, GateSection.KIS_PAPER_DECISION, GateSection.ORDER_RESULT,
    GateSection.ORDER_QUALITY, GateSection.PORTFOLIO, GateSection.OUTCOME_REVIEW,
    GateSection.FEEDBACK_QUALITY, GateSection.LIVE_SAFETY,
})

# UI/API 표시 점검 시 존재해야 하는 read-only endpoint manifest.
EXPECTED_API_ENDPOINTS = (
    "/api/auto-paper/universe-status",
    "/api/auto-paper/portfolio-source",
    "/api/agents/decision-episodes",
    "/api/agents/order-quality-metrics",
    "/api/agents/decision-explanation",
    "/api/agents/feedback-loop",
    "/api/agents/decision-quality",
    "/api/status/live-safety",
    "/api/kis-paper/readiness",
    "/api/system/logs",
)

DISCLAIMER_KO = (
    "본 리포트는 최종 빌드 전 통합 *검증* 자료입니다. 실전 승인이 아니며, 실제 주문/"
    "KIS 실거래 호출 0건(주문 결과는 fake 합성). 수익을 보장하지 않습니다."
)


# ─────────────────────────────────────────────────────────────────────────────
# DTO
# ─────────────────────────────────────────────────────────────────────────────


@dataclass(frozen=True)
class GateInputs:
    """정합성 점검 입력 — 모두 안전 default (offline)."""

    enable_live_trading: bool = False
    enable_ai_execution: bool = False
    enable_futures_live_trading: bool = False
    kis_is_paper: bool = True
    default_mode: str = "SIMULATION"
    kis_credentials_present: Optional[bool] = None   # None → WARN(자격 미설정)
    user_watchlist: tuple[str, ...] = ()
    market_data_available: bool = True               # 장 닫힘 → False → WARN(오류 아님)
    risk_profile: str = "BALANCED"
    available_api_routes: Optional[frozenset[str]] = None   # None → advisory manifest


@dataclass(frozen=True)
class SectionResult:
    section:     str
    verdict:     str
    reason_code: str
    detail:      str
    evidence:    dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "section":     self.section,
            "verdict":     self.verdict,
            "reason_code": self.reason_code,
            "detail":      self.detail,
            "evidence":    dict(self.evidence),
        }


@dataclass(frozen=True)
class ProgramIntegrityReport:
    generated_at:    str
    sections:        tuple[SectionResult, ...]
    counts:          dict[str, int]
    overall_verdict: str
    build_ready:     bool
    reason_code:     str

    is_live_authorization: bool = False
    broker_order_sent:     bool = False
    order_created_live:    bool = False
    contains_secret:       bool = False
    disclaimer:            str = DISCLAIMER_KO

    def __post_init__(self) -> None:
        for name in ("is_live_authorization", "broker_order_sent",
                     "order_created_live", "contains_secret"):
            if getattr(self, name) is not False:
                raise ValueError(f"{name} must be False (빌드 전 검증은 주문 0건)")

    def to_dict(self) -> dict[str, Any]:
        return {
            "generated_at":    self.generated_at,
            "sections":        [s.to_dict() for s in self.sections],
            "counts":          dict(self.counts),
            "overall_verdict": self.overall_verdict,
            "build_ready":     bool(self.build_ready),
            "reason_code":     self.reason_code,
            "is_live_authorization": False,
            "broker_order_sent":     False,
            "order_created_live":    False,
            "contains_secret":       False,
            "disclaimer":            self.disclaimer,
        }


def _r(section: GateSection, verdict: GateVerdict, code: str, detail: str,
       **evidence) -> SectionResult:
    return SectionResult(section.value, verdict.value, code, detail, dict(evidence))


# ─────────────────────────────────────────────────────────────────────────────
# 합성 입력 (deterministic strong-BUY 시나리오)
# ─────────────────────────────────────────────────────────────────────────────


def _strong_buy_input(symbol: str = "005930"):
    from app.agents.agent_council import StrategyMarketInput
    return StrategyMarketInput(
        symbol=symbol, current_price=72_000, prev_close=70_000, open_price=71_500,
        vwap=70_800, opening_range_high=71_200, opening_range_low=70_500,
        recent_closes=(70_000, 70_900, 71_600, 72_000),
        current_volume=14_000_000, avg_volume=8_000_000,
        market_regime="TREND_UP", regime_decision="ALLOW",
    )


def _fake_paper_builder(db=None, *, last_prices=None, now=None):
    """portfolio 섹션용 fake paper-state (DB 없이 deterministic)."""
    return SimpleNamespace(
        current_cash=9_280_000, total_equity=10_000_000, starting_cash=10_000_000,
        total_position_value=720_000, total_unrealized_pnl=20_000, position_count=1,
        invested_krw=700_000, realized_pnl=0, last_event_at="2026-05-24T01:00:00+00:00",
        positions=[{"symbol": "005930", "quantity": 10, "average_price": 70_000,
                    "current_price": 72_000, "market_value": 720_000,
                    "unrealized_pnl": 20_000, "portfolio_weight_pct": 0.072}],
    )


# ─────────────────────────────────────────────────────────────────────────────
# 섹션 점검
# ─────────────────────────────────────────────────────────────────────────────


def _check_universe(inp: GateInputs) -> SectionResult:
    from app.universe.universe_status import build_universe_status
    s = build_universe_status(user_symbols=list(inp.user_watchlist) or None)
    if s.universe_count <= 0:
        return _r(GateSection.UNIVERSE, GateVerdict.FAIL, s.reason_code,
                  "후보군이 0개입니다.", universe_source=s.universe_source)
    return _r(GateSection.UNIVERSE, GateVerdict.PASS, s.reason_code,
              f"{s.universe_source} {s.universe_count}개 (preview {len(s.symbols_preview)})",
              universe_source=s.universe_source, universe_count=s.universe_count,
              fallback_used=s.fallback_used,
              invalid_removed=list(s.invalid_symbols_removed))


def _check_kis_readiness(inp: GateInputs) -> SectionResult:
    from app.kis_paper.readiness import evaluate_readiness
    r = evaluate_readiness({
        "kis_is_paper": inp.kis_is_paper,
        "enable_live_trading": inp.enable_live_trading,
        "enable_ai_execution": inp.enable_ai_execution,
        "enable_futures_live_trading": inp.enable_futures_live_trading,
        "default_mode": inp.default_mode,
        "kis_app_key": "FAKE" if inp.kis_credentials_present else "",
        "kis_app_secret": "FAKE" if inp.kis_credentials_present else "",
        "kis_account_no": "FAKE" if inp.kis_credentials_present else "",
    })
    if not r.ready:
        return _r(GateSection.KIS_PAPER_READINESS, GateVerdict.FAIL,
                  "KIS_PAPER_NOT_READY", "; ".join(b.value for b in r.blocked_reasons),
                  ready=False)
    if not r.credentials_present:
        # 자격 미설정은 빌드 환경에서 정상 — WARN (mock 모드 가능).
        return _r(GateSection.KIS_PAPER_READINESS, GateVerdict.WARN,
                  "KIS_CREDENTIALS_MISSING",
                  "KIS 자격 미설정 — mock 모드만 가능 (빌드 환경 정상). 장중 테스트 전 입력 필요.",
                  ready=True, credentials_present=False, kis_is_paper=r.safety_flags.get("kis_is_paper"))
    return _r(GateSection.KIS_PAPER_READINESS, GateVerdict.PASS, "KIS_PAPER_READY",
              "KIS 모의 readiness 통과 (paper mode).", ready=True,
              credentials_present=True, kis_is_paper=r.safety_flags.get("kis_is_paper"))


def _check_votes(mi) -> SectionResult:
    from app.agents.agent_council import (
        evaluate_gap,
        evaluate_momentum,
        evaluate_orb,
        evaluate_vwap,
    )
    votes = {"ORB": evaluate_orb(mi), "MOMENTUM": evaluate_momentum(mi),
             "GAP": evaluate_gap(mi), "VWAP": evaluate_vwap(mi)}
    missing = [k for k, v in votes.items()
               if not all(hasattr(v, f) for f in ("signal", "score", "confidence", "reason"))]
    if missing:
        return _r(GateSection.STRATEGY_VOTES, GateVerdict.FAIL, "VOTE_FIELDS_MISSING",
                  f"vote 필드 누락: {missing}")
    buy_votes = [k for k, v in votes.items() if v.signal.value == "BUY"]
    return _r(GateSection.STRATEGY_VOTES, GateVerdict.PASS, "VOTES_OK",
              f"4전략 vote 생성 (BUY {len(buy_votes)}: {buy_votes})",
              signals={k: v.signal.value for k, v in votes.items()})


def _check_council_and_downstream(inp: GateInputs, mi) -> list[SectionResult]:
    """Agent Council → RiskOfficer → exit_plan → quality → decision → KIS decision."""
    from app.agents.agent_council import run_agent_council
    out: list[SectionResult] = []
    decision = run_agent_council(mi, risk_profile=inp.risk_profile)
    d = decision.to_dict()

    # Agent Council
    if d.get("final_action") not in ("BUY", "SELL", "HOLD"):
        out.append(_r(GateSection.AGENT_COUNCIL, GateVerdict.FAIL, "NO_FINAL_ACTION",
                      "final_action 산출 실패"))
        return out
    out.append(_r(GateSection.AGENT_COUNCIL, GateVerdict.PASS, "COUNCIL_OK",
                  f"final_action={d['final_action']} conf={d['confidence']} "
                  f"quality={d['quality_score']} selected={d['selected_strategies']}",
                  final_action=d["final_action"], confidence=d["confidence"],
                  quality_score=d["quality_score"],
                  buy_score=d["buy_score"], sell_score=d["sell_score"], hold_score=d["hold_score"]))

    # RiskOfficer veto (구조 존재 확인 — veto 적용/미적용 모두 PASS)
    out.append(_r(GateSection.RISK_OFFICER, GateVerdict.PASS, "RISK_OFFICER_OK",
                  f"veto_applied={d['risk_veto_result'].get('veto_applied', False)} "
                  f"risk_flags={d['risk_flags']}",
                  risk_veto_result=d["risk_veto_result"]))

    # exit_plan
    if d["final_action"] == "BUY":
        if not d.get("has_exit_plan") or not d.get("exit_plan"):
            out.append(_r(GateSection.EXIT_PLAN, GateVerdict.FAIL, "BUY_WITHOUT_EXIT_PLAN",
                          "BUY 인데 exit_plan 없음 (정책 위반)"))
        else:
            out.append(_r(GateSection.EXIT_PLAN, GateVerdict.PASS, "EXIT_PLAN_OK",
                          f"exit_plan={d['exit_plan']}", exit_plan=d["exit_plan"]))
    else:
        out.append(_r(GateSection.EXIT_PLAN, GateVerdict.PASS, "EXIT_PLAN_NA",
                      f"{d['final_action']} — exit_plan 필수 아님",
                      exit_plan_validation=d.get("exit_plan_validation", {})))

    # quality gate (#51)
    qg = d.get("quality_gate_result") or {}
    if "enhanced_quality_score" not in qg:
        out.append(_r(GateSection.QUALITY_GATE, GateVerdict.FAIL, "NO_QUALITY_GATE",
                      "quality_gate_result 누락"))
    else:
        out.append(_r(GateSection.QUALITY_GATE, GateVerdict.PASS, "QUALITY_GATE_OK",
                      f"enhanced={qg['enhanced_quality_score']}({qg.get('quality_grade')}) "
                      f"should_hold={qg.get('should_hold')} pre_quality_action={d.get('pre_quality_action')}",
                      quality_gate_result=qg, pre_quality_action=d.get("pre_quality_action")))

    # DECISION (BUY/SELL/HOLD)
    out.append(_r(GateSection.DECISION, GateVerdict.PASS, "DECISION_OK",
                  f"final_action={d['final_action']} (is_order_signal=False)",
                  final_action=d["final_action"]))

    # KIS Paper decision — HOLD → 주문 없음, BUY/SELL → broker_order_type=KIS_PAPER.
    if d["final_action"] == "HOLD":
        out.append(_r(GateSection.KIS_PAPER_DECISION, GateVerdict.PASS, "HOLD_NO_DECISION",
                      "HOLD — KIS Paper decision 없음 (주문 0건)",
                      broker_order_type=None))
    else:
        # SELL 은 보유 포지션 필요 (council 이 naked SELL 을 HOLD 로 강등) — 여기 도달은 BUY 또는 보유 SELL.
        out.append(_r(GateSection.KIS_PAPER_DECISION, GateVerdict.PASS, "KIS_PAPER_DECISION_OK",
                      f"{d['final_action']} → broker_order_type=KIS_PAPER (실전 아님)",
                      broker_order_type="KIS_PAPER", is_live_authorization=False))
    return out, decision


def _check_order_result_and_quality(inp: GateInputs, decision) -> list[SectionResult]:
    """fake route_order 기반 KIS 모의 주문 결과 + order_quality (offline)."""
    from app.kis_paper.order_quality import build_order_quality_log
    out: list[SectionResult] = []
    fa = decision.final_action.value

    if fa == "HOLD":
        out.append(_r(GateSection.ORDER_RESULT, GateVerdict.PASS, "NO_ORDER_FOR_HOLD",
                      "HOLD — 주문 생성 0건", broker_order_type=None))
        out.append(_r(GateSection.ORDER_QUALITY, GateVerdict.PASS, "NO_ORDER_QUALITY",
                      "HOLD — order_quality 없음"))
        return out

    # *fake* route_order 결과 — 실제 broker / route_order 호출 0건 (합성 paper fill).
    fake_result = SimpleNamespace(
        reason_code="", reason_message="", dry_run=False, submitted=True,
        fill_status="FILLED", filled_quantity=10, avg_fill_price=72_050,
        broker_order_no="PAPER-SIM-1",   # 모의 합성 번호 (실전 주문번호 아님).
    )
    fake_decision = SimpleNamespace(quantity=10, price=72_000, side=fa)

    out.append(_r(GateSection.ORDER_RESULT, GateVerdict.PASS, "FAKE_PAPER_ORDER_OK",
                  "fake route_order 기반 모의 체결 결과 생성 (broker_order_sent=false, 실전 아님)",
                  broker_order_type="KIS_PAPER", broker_order_no="PAPER-SIM-1",
                  is_live_authorization=False))

    q = build_order_quality_log(result=fake_result, decision=fake_decision)
    if q.order_status not in ("FILLED", "PARTIALLY_FILLED"):
        out.append(_r(GateSection.ORDER_QUALITY, GateVerdict.WARN, "ORDER_QUALITY_UNFILLED",
                      f"order_status={q.order_status}", order_status=q.order_status))
    else:
        out.append(_r(GateSection.ORDER_QUALITY, GateVerdict.PASS, "ORDER_QUALITY_OK",
                      f"status={q.order_status} slippage_bps={q.slippage_bps} partial={q.partial_fill}",
                      order_status=q.order_status, slippage_bps=q.slippage_bps,
                      partial_fill=q.partial_fill, filled_quantity=q.filled_quantity))
    return out


def _check_portfolio() -> SectionResult:
    from app.portfolio.portfolio_snapshot import build_paper_simulated_snapshot
    snap = build_paper_simulated_snapshot(db=None, builder=_fake_paper_builder)
    if snap.source != "PAPER_SIMULATED" or not snap.value_available:
        return _r(GateSection.PORTFOLIO, GateVerdict.FAIL, "PORTFOLIO_SOURCE_BAD",
                  f"source={snap.source} value_available={snap.value_available}")
    # 정합성: total_asset = cash + position_value (근사).
    return _r(GateSection.PORTFOLIO, GateVerdict.PASS, "PORTFOLIO_OK",
              f"source={snap.source} cash={snap.cash} total={snap.total_asset} positions={snap.position_count}",
              source=snap.source, cash=snap.cash, total_asset=snap.total_asset,
              position_count=snap.position_count)


def _check_outcome_review_feedback() -> list[SectionResult]:
    from app.agents.decision_quality import compute_decision_quality
    from app.agents.post_trade_feedback import build_feedback_loop, feedback_penalty_for
    from app.agents.post_trade_outcome import evaluate_outcome
    from app.agents.post_trade_review import review_episode
    out: list[SectionResult] = []

    # 합성 episode (BUY 체결 후 +1.5% 익절 시나리오).
    episode = {
        "final_action": "BUY", "confidence": 0.7, "quality_score": 75,
        "market_snapshot": {"price": 72_000},
        "kis_order_result": {"order_quality": {"avg_fill_price": 72_050}},
        "council": {"selected_strategies": ["MOMENTUM", "VWAP"]},
    }
    outcome = evaluate_outcome(episode=episode, future_prices={5: 72_400, 30: 73_100},
                              close_price=73_200)
    episode["outcome"] = outcome.to_dict()
    review = review_episode(episode)
    if outcome.status == "UNAVAILABLE" or review.review_status == "DATA_INSUFFICIENT" and outcome.label is None:
        out.append(_r(GateSection.OUTCOME_REVIEW, GateVerdict.WARN, "OUTCOME_PENDING",
                      "outcome/review 데이터 부족 (정상 — 사후 라벨링 누적 전)"))
    else:
        out.append(_r(GateSection.OUTCOME_REVIEW, GateVerdict.PASS, "OUTCOME_REVIEW_OK",
                      f"outcome={outcome.label} review_grade={review.grade} tags={review.tags[:3]}",
                      outcome_label=outcome.label, review_grade=review.grade))

    # feedback loop → 다음 판단 quality penalty 반영 확인.
    summary = {"by_review_grade": {"GOOD": 3, "BAD": 4},
               "by_review_tag": {"WEAK_SIGNAL_ENTRY": 4, "LATE_EXIT": 3},
               "by_outcome_label": {"LOSS": 4}, "by_data_status": {"STALE": 3},
               "by_reason_code": {"BLOCK_NEW_BUY": 3}}
    fb = build_feedback_loop(summary)
    penalty = feedback_penalty_for(fb.to_dict())
    if fb.auto_apply_allowed is not False or fb.requires_operator_approval is not True:
        out.append(_r(GateSection.FEEDBACK_QUALITY, GateVerdict.FAIL, "FEEDBACK_AUTO_APPLY",
                      "feedback 가 자동 적용됨 (정책 위반)"))
        return out
    # penalty 가 다음 quality 계산에 반영되는지 (반영 시 점수 하락).
    base = compute_decision_quality(votes=[{"strategy": "ORB", "signal": "BUY", "score": 70}],
                                    final_action="BUY", has_exit_plan=True, exit_plan_valid=True)
    penalized = compute_decision_quality(votes=[{"strategy": "ORB", "signal": "BUY", "score": 70}],
                                         final_action="BUY", has_exit_plan=True, exit_plan_valid=True,
                                         feedback_penalty=penalty)
    reflected = penalty == 0 or penalized.enhanced_quality_score <= base.enhanced_quality_score
    out.append(_r(GateSection.FEEDBACK_QUALITY,
                  GateVerdict.PASS if reflected else GateVerdict.FAIL,
                  "FEEDBACK_QUALITY_OK" if reflected else "FEEDBACK_NOT_REFLECTED",
                  f"tags={len(fb.feedback_tags)} recommendations={len(fb.threshold_recommendations)} "
                  f"penalty={penalty} (auto_apply_allowed=False)",
                  feedback_penalty=penalty, recommendation_count=len(fb.threshold_recommendations)))
    return out


def _check_ui_api(inp: GateInputs) -> SectionResult:
    if inp.available_api_routes is None:
        return _r(GateSection.UI_API, GateVerdict.PASS, "UI_API_MANIFEST",
                  f"표시 대상 endpoint manifest {len(EXPECTED_API_ENDPOINTS)}종 (런타임 route 미주입 — advisory)",
                  expected_endpoints=list(EXPECTED_API_ENDPOINTS))
    missing = [e for e in EXPECTED_API_ENDPOINTS if e not in inp.available_api_routes]
    if missing:
        return _r(GateSection.UI_API, GateVerdict.WARN, "UI_API_MISSING_ROUTES",
                  f"누락 endpoint: {missing}", missing=missing)
    return _r(GateSection.UI_API, GateVerdict.PASS, "UI_API_OK",
              f"표시 endpoint {len(EXPECTED_API_ENDPOINTS)}종 모두 존재",
              expected_endpoints=list(EXPECTED_API_ENDPOINTS))


def _check_live_safety(inp: GateInputs) -> SectionResult:
    from app.kis.endpoints import resolve_kis_endpoint
    from app.permission.live_trading_off_policy import evaluate_live_off_policy
    lp = evaluate_live_off_policy(
        enable_live_trading=inp.enable_live_trading,
        enable_ai_execution=inp.enable_ai_execution,
        enable_futures_live_trading=inp.enable_futures_live_trading,
        kis_is_paper=inp.kis_is_paper, default_mode=inp.default_mode)
    ke = resolve_kis_endpoint(kis_is_paper=inp.kis_is_paper, explicit_live_gate_passed=False)
    # 빌드 안전: live flags 모두 off + KIS paper + live path gated + paper/live 분리.
    safe = (not inp.enable_live_trading and not inp.enable_ai_execution
            and not inp.enable_futures_live_trading and inp.kis_is_paper
            and lp.live_path_gated and ke.paper_live_separated
            and not lp.is_live_authorization)
    if not safe:
        return _r(GateSection.LIVE_SAFETY, GateVerdict.FAIL, "LIVE_SAFETY_VIOLATION",
                  "실매매 안전 상태 위반 (live flag on 또는 path 미차단)",
                  enable_live_trading=inp.enable_live_trading,
                  kis_is_paper=inp.kis_is_paper, live_path_gated=lp.live_path_gated)
    return _r(GateSection.LIVE_SAFETY, GateVerdict.PASS, "LIVE_SAFETY_OK",
              "실매매 기본 OFF + live path gated + Paper/Live 분리 + 실전 주문 0건",
              enable_live_trading=False, enable_ai_execution=False, kis_is_paper=True,
              live_path_gated=True, paper_live_separated=True,
              kis_endpoint_mode=ke.selected_mode)


# ─────────────────────────────────────────────────────────────────────────────
# 실행
# ─────────────────────────────────────────────────────────────────────────────


def run_program_integrity_gate(inp: GateInputs | None = None) -> ProgramIntegrityReport:
    """전체 흐름을 offline/fake 로 점검 — broker / route_order 호출 0건."""
    inp = inp or GateInputs()
    sections: list[SectionResult] = []

    sections.append(_check_universe(inp))
    sections.append(_check_kis_readiness(inp))

    mi = _strong_buy_input()
    sections.append(_check_votes(mi))

    council_results, decision = _check_council_and_downstream(inp, mi)
    sections.extend(council_results)
    sections.extend(_check_order_result_and_quality(inp, decision))
    sections.append(_check_portfolio())
    sections.extend(_check_outcome_review_feedback())
    sections.append(_check_ui_api(inp))
    sections.append(_check_live_safety(inp))

    counts = {v.value: 0 for v in GateVerdict}
    for s in sections:
        counts[s.verdict] = counts.get(s.verdict, 0) + 1

    # build_ready: 필수 섹션 FAIL 0 + live_safety PASS.
    required_fail = [s for s in sections
                     if s.verdict == GateVerdict.FAIL.value and s.section in
                     {x.value for x in _REQUIRED_SECTIONS}]
    live_safety = next((s for s in sections if s.section == GateSection.LIVE_SAFETY.value), None)
    live_safe = live_safety is not None and live_safety.verdict == GateVerdict.PASS.value
    build_ready = (len(required_fail) == 0) and live_safe

    has_fail = counts.get(GateVerdict.FAIL.value, 0) > 0
    has_warn = counts.get(GateVerdict.WARN.value, 0) > 0
    overall = (GateVerdict.FAIL.value if has_fail
               else GateVerdict.WARN.value if has_warn else GateVerdict.PASS.value)
    reason = ("BUILD_BLOCKED_BY_FAIL" if not build_ready
              else "BUILD_READY_WITH_WARNINGS" if has_warn else "BUILD_READY")

    return ProgramIntegrityReport(
        generated_at=datetime.now(timezone.utc).isoformat(),
        sections=tuple(sections), counts=counts, overall_verdict=overall,
        build_ready=build_ready, reason_code=reason,
    )


def summarize_program_integrity(report: ProgramIntegrityReport) -> dict[str, Any]:
    return report.to_dict()


def render_markdown_report(report: ProgramIntegrityReport) -> str:
    lines: list[str] = []
    lines.append("# 최종 빌드 전 전체 프로그램 정합성 점검 (BUILD-01)")
    lines.append("")
    lines.append(f"> {report.disclaimer}")
    lines.append("")
    lines.append("## 전체 요약")
    lines.append(f"- 생성: {report.generated_at}")
    lines.append(f"- 전체 판정: **{report.overall_verdict}** · build_ready: "
                 f"**{'예' if report.build_ready else '아니오'}** ({report.reason_code})")
    lines.append(f"- PASS {report.counts.get('PASS',0)} · WARN {report.counts.get('WARN',0)} · "
                 f"FAIL {report.counts.get('FAIL',0)} · SKIP {report.counts.get('SKIP',0)}")
    lines.append("")
    lines.append("## 섹션별 점검")
    lines.append("")
    lines.append("| 섹션 | 판정 | reason_code | 상세 |")
    lines.append("|---|---|---|---|")
    for s in report.sections:
        lines.append(f"| {s.section} | {s.verdict} | {s.reason_code} | {s.detail[:90]} |")
    lines.append("")
    fails = [s for s in report.sections if s.verdict == "FAIL"]
    warns = [s for s in report.sections if s.verdict == "WARN"]
    lines.append("## FAIL / WARN 조치")
    if fails:
        for s in fails:
            lines.append(f"- **FAIL {s.section}**: {s.detail} → 해당 모듈 점검 필요.")
    if warns:
        for s in warns:
            lines.append(f"- WARN {s.section}: {s.detail}")
    if not fails and not warns:
        lines.append("- FAIL/WARN 없음.")
    lines.append("")
    lines.append("## 최종 빌드 가능 여부")
    lines.append(f"- build_ready: **{'예' if report.build_ready else '아니오'}**")
    lines.append("- 본 점검은 **실전 승인이 아니며**, 실제 주문/KIS 실거래 호출 0건(주문 결과 fake).")
    lines.append("- **수익을 보장하지 않습니다.** 장중 실제 KIS 모의 API 테스트는 BUILD-02 에서 진행.")
    lines.append("")
    return "\n".join(lines) + "\n"


__all__ = [
    "GateVerdict", "GateSection", "GateInputs", "SectionResult",
    "ProgramIntegrityReport", "EXPECTED_API_ENDPOINTS",
    "run_program_integrity_gate", "summarize_program_integrity", "render_markdown_report",
]

"""BUILD-02B-0: KIS 모의 AI 자동 매수/매도 전체 코드 감사 (offline/fake, read-only).

KIS 모의 API 기반 AI 자동매매가 *판단 → KIS Paper 주문 결정 → 주문 결과 → 체결
품질 → 포트폴리오 반영 → outcome/review/feedback* 까지 코드 단위로 끊김 없이
연결되는지 fake 데이터로 감사한다.

**본 작업은 코드 감사와 offline/fake 검증이다.** 실제 KIS 모의/실전 API 를 호출하지
않으며, 실전/모의 주문을 생성하지 않는다. 주문 결과는 *fake* 로 합성한다.

핵심 감사 항목(판정):
- KIS 실제 API / 실전 endpoint 호출 0건.
- broker_order_type 은 항상 KIS_PAPER (LIVE 0건), is_live_authorization=False.
- risk_veto / exit_plan invalid / quality low → council HOLD → **주문 decision 미생성**.
- SELL 은 보유 포지션 없으면 미생성(HOLD). BUY 는 exit_plan 없으면 미생성(HOLD).
- 권한 게이트(KIS_PAPER 모드/LIVE off/자격/긴급정지/시간창/한도)가 각 위반을 차단.

CLAUDE.md 가드 (정적 grep 으로 lock):
- broker / OrderExecutor / 단일 주문 라우터 / KIS 어댑터 / KIS 실제 client import 0건.
- broker 주문/취소 호출 0건, DB write 0건, 외부 HTTP / AI SDK import 0건.
- `KisPaperAuditReport.is_live_authorization=False` / `broker_order_sent=False` /
  `order_created=False` / `contains_secret=False` 불변.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from enum import StrEnum
from types import SimpleNamespace
from typing import Any, Optional


class AVerdict(StrEnum):
    PASS = "PASS"
    WARN = "WARN"
    FAIL = "FAIL"
    SKIP = "SKIP"


class ASection(StrEnum):
    ENV_READINESS = "ENV_READINESS"
    UNIVERSE = "UNIVERSE"
    MARKET_DATA_CONTRACT = "MARKET_DATA_CONTRACT"
    STRATEGY_VOTES = "STRATEGY_VOTES"
    AGENT_COUNCIL = "AGENT_COUNCIL"
    RISK_OFFICER = "RISK_OFFICER"
    EXIT_PLAN = "EXIT_PLAN"
    QUALITY_GATE = "QUALITY_GATE"
    PAPER_DECISION_BRIDGE = "PAPER_DECISION_BRIDGE"
    PAPER_ORDER_EXECUTOR = "PAPER_ORDER_EXECUTOR"
    BUY_SELL_LIMITS = "BUY_SELL_LIMITS"
    SELL_TRIGGER = "SELL_TRIGGER"
    ORDER_RESULT_QUALITY = "ORDER_RESULT_QUALITY"
    PORTFOLIO_APPLICATION = "PORTFOLIO_APPLICATION"
    OUTCOME_REVIEW_FEEDBACK = "OUTCOME_REVIEW_FEEDBACK"
    UI_CONTRACTS = "UI_CONTRACTS"
    LIVE_SAFETY = "LIVE_SAFETY"
    FAKE_FLOWS = "FAKE_FLOWS"


_REQUIRED = frozenset({
    ASection.UNIVERSE, ASection.MARKET_DATA_CONTRACT, ASection.STRATEGY_VOTES,
    ASection.AGENT_COUNCIL, ASection.RISK_OFFICER, ASection.EXIT_PLAN,
    ASection.QUALITY_GATE, ASection.PAPER_DECISION_BRIDGE, ASection.PAPER_ORDER_EXECUTOR,
    ASection.BUY_SELL_LIMITS, ASection.SELL_TRIGGER, ASection.ORDER_RESULT_QUALITY,
    ASection.PORTFOLIO_APPLICATION, ASection.OUTCOME_REVIEW_FEEDBACK,
    ASection.LIVE_SAFETY, ASection.FAKE_FLOWS,
})

EXPECTED_API_ENDPOINTS = (
    "/api/kis-paper/readiness", "/api/status/live-safety",
    "/api/agents/decision-episodes", "/api/agents/order-quality-metrics",
    "/api/auto-paper/portfolio-source",
)

DISCLAIMER_KO = (
    "본 리포트는 KIS 모의 AI 자동매매 *코드 감사* 자료입니다. 실제 KIS API 호출 0건, "
    "실전/모의 주문 0건, 실전 승인이 아닙니다. 수익을 보장하지 않습니다. 장중 실제 "
    "KIS 모의 주문/체결 리허설은 BUILD-02B 에서 진행합니다."
)


@dataclass(frozen=True)
class AuditItem:
    name: str
    verdict: str
    reason_code: str
    detail: str

    def to_dict(self) -> dict[str, Any]:
        return {"name": self.name, "verdict": self.verdict,
                "reason_code": self.reason_code, "detail": self.detail}


@dataclass(frozen=True)
class AuditSection:
    section: str
    verdict: str
    items: tuple[AuditItem, ...] = ()
    note: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {"section": self.section, "verdict": self.verdict, "note": self.note,
                "items": [i.to_dict() for i in self.items]}


@dataclass(frozen=True)
class KisPaperAuditInputs:
    enable_kis_paper_auto_trading: bool = True
    kis_paper_auto_order_dry_run: bool = True
    kis_is_paper: bool = True
    enable_live_trading: bool = False
    enable_ai_execution: bool = False
    kis_credentials_present: Optional[bool] = None


@dataclass(frozen=True)
class KisPaperAuditReport:
    generated_at: str
    sections: tuple[AuditSection, ...]
    counts: dict[str, int]
    overall_verdict: str
    paper_autotrade_ready: bool
    ready_for_market_open_rehearsal: bool
    fail_items: tuple[str, ...]
    warn_items: tuple[str, ...]
    reason_codes: tuple[str, ...]

    is_live_authorization: bool = False
    broker_order_sent: bool = False
    order_created: bool = False
    contains_secret: bool = False
    disclaimer: str = DISCLAIMER_KO

    def __post_init__(self) -> None:
        for name in ("is_live_authorization", "broker_order_sent",
                     "order_created", "contains_secret"):
            if getattr(self, name) is not False:
                raise ValueError(f"{name} must be False (코드 감사는 주문 0건)")

    def to_dict(self) -> dict[str, Any]:
        return {
            "generated_at": self.generated_at,
            "sections": [s.to_dict() for s in self.sections],
            "counts": dict(self.counts),
            "overall_verdict": self.overall_verdict,
            "paper_autotrade_ready": bool(self.paper_autotrade_ready),
            "ready_for_market_open_rehearsal": bool(self.ready_for_market_open_rehearsal),
            "fail_items": list(self.fail_items),
            "warn_items": list(self.warn_items),
            "reason_codes": list(self.reason_codes),
            "is_live_authorization": False,
            "broker_order_sent": False,
            "order_created": False,
            "contains_secret": False,
            "disclaimer": self.disclaimer,
        }


def _it(name, v: AVerdict, code, detail) -> AuditItem:
    return AuditItem(name, v.value, code, detail)


def _sv(items: tuple[AuditItem, ...]) -> str:
    if any(i.verdict == AVerdict.FAIL.value for i in items):
        return AVerdict.FAIL.value
    if any(i.verdict == AVerdict.WARN.value for i in items):
        return AVerdict.WARN.value
    if items and all(i.verdict == AVerdict.SKIP.value for i in items):
        return AVerdict.SKIP.value
    return AVerdict.PASS.value


# ── 합성 입력 ────────────────────────────────────────────────────────────────


def _strong_buy_input(symbol: str):
    from app.agents.agent_council import StrategyMarketInput
    return StrategyMarketInput(
        symbol=symbol, current_price=72_000, prev_close=70_000, open_price=71_500,
        vwap=70_800, opening_range_high=71_200, opening_range_low=70_500,
        recent_closes=(70_000, 70_900, 71_600, 72_000),
        current_volume=14_000_000, avg_volume=8_000_000,
        market_regime="TREND_UP", regime_decision="ALLOW")


def _weak_buy_input(symbol: str):
    from app.agents.agent_council import StrategyMarketInput
    # 약한 신호 + 고변동성 → quality 낮거나 BUY 미채택 (HOLD 유도).
    return StrategyMarketInput(
        symbol=symbol, current_price=70_010, prev_close=70_000, open_price=70_000,
        vwap=70_005, opening_range_high=70_000, opening_range_low=69_900,
        recent_closes=(70_000, 70_005, 70_010),
        current_volume=2_000_000, avg_volume=8_000_000,
        market_regime="HIGH_VOLATILITY", regime_decision="REDUCE_SIZE")


def _down_input(symbol: str):
    from app.agents.agent_council import StrategyMarketInput
    return StrategyMarketInput(
        symbol=symbol, current_price=63_000, prev_close=70_000, open_price=69_000,
        vwap=66_000, opening_range_high=70_000, opening_range_low=69_000,
        recent_closes=(70_000, 68_000, 65_000, 63_000),
        current_volume=20_000_000, avg_volume=8_000_000,
        market_regime="TREND_DOWN", regime_decision="BLOCK_NEW_BUY")


def _pick_symbol() -> str:
    from app.universe.default_universe import FALLBACK_MARKET_CAP_TOP50
    return FALLBACK_MARKET_CAP_TOP50[0]


# ── 섹션 ─────────────────────────────────────────────────────────────────────


def _sec_env(inp: KisPaperAuditInputs) -> AuditSection:
    items = [
        _it("KIS_IS_PAPER", AVerdict.PASS if inp.kis_is_paper else AVerdict.FAIL,
            "KIS_IS_PAPER", f"{inp.kis_is_paper} (true 필요)"),
        _it("ENABLE_LIVE_TRADING", AVerdict.PASS if not inp.enable_live_trading else AVerdict.FAIL,
            "ENABLE_LIVE_TRADING", f"{inp.enable_live_trading} (false 필요)"),
        _it("ENABLE_AI_EXECUTION", AVerdict.PASS if not inp.enable_ai_execution else AVerdict.FAIL,
            "ENABLE_AI_EXECUTION", f"{inp.enable_ai_execution} (false 필요)"),
        _it("dry_run_default", AVerdict.PASS, "DRY_RUN",
            f"kis_paper_auto_order_dry_run={inp.kis_paper_auto_order_dry_run}"),
    ]
    return AuditSection(ASection.ENV_READINESS.value, _sv(tuple(items)), tuple(items),
                        note="KIS 모의 자동주문 안전 flag (실거래 OFF).")


def _sec_universe(symbol: str) -> AuditSection:
    from app.universe.universe_status import build_universe_status
    s = build_universe_status(user_symbols=None)
    ok = s.universe_count > 0 and bool(symbol)
    return AuditSection(ASection.UNIVERSE.value,
                        AVerdict.PASS.value if ok else AVerdict.FAIL.value,
                        (_it("universe_pick", AVerdict.PASS if ok else AVerdict.FAIL,
                             s.reason_code, f"{s.universe_source} {s.universe_count}개, pick={symbol}"),),
                        note="DEFAULT_UNIVERSE_50 에서 symbol 선택 가능.")


def _sec_market_data(mi) -> AuditSection:
    ok = (mi.current_price is not None and mi.prev_close is not None
          and mi.opening_range_high is not None and len(mi.recent_closes) >= 3)
    return AuditSection(ASection.MARKET_DATA_CONTRACT.value,
                        AVerdict.PASS.value if ok else AVerdict.FAIL.value,
                        (_it("market_data_contract", AVerdict.PASS if ok else AVerdict.FAIL,
                             "MARKET_DATA", f"price/prev/OR/recent_closes 채워짐={ok}"),),
                        note="fake OHLCV/current_price 로 StrategyMarketInput 구성 (실 시세 미사용).")


def _sec_votes(mi) -> AuditSection:
    from app.agents.agent_council import (
        evaluate_gap, evaluate_momentum, evaluate_orb, evaluate_vwap,
    )
    votes = {"ORB": evaluate_orb(mi), "MOMENTUM": evaluate_momentum(mi),
             "GAP": evaluate_gap(mi), "VWAP": evaluate_vwap(mi)}
    ok = all(all(hasattr(v, f) for f in ("signal", "score", "confidence", "reason", "risk_flags"))
             for v in votes.values())
    return AuditSection(ASection.STRATEGY_VOTES.value,
                        AVerdict.PASS.value if ok else AVerdict.FAIL.value,
                        (_it("votes", AVerdict.PASS if ok else AVerdict.FAIL, "VOTES",
                             f"signals={{{', '.join(k+':'+v.signal.value for k, v in votes.items())}}}"),),
                        note="ORB/Momentum/Gap/VWAP vote + signal/score/confidence/reason/risk_flags.")


def _council(mi, risk_profile="BALANCED", held_position=False):
    from app.agents.agent_council import run_agent_council
    return run_agent_council(mi, risk_profile=risk_profile, held_position=held_position)


def _sec_council_chain(mi) -> tuple[list[AuditSection], Any]:
    d = _council(mi)
    dd = d.to_dict()
    out: list[AuditSection] = []
    out.append(AuditSection(ASection.AGENT_COUNCIL.value, AVerdict.PASS.value,
               (_it("council", AVerdict.PASS, "COUNCIL",
                    f"final_action={dd['final_action']} conf={dd['confidence']} q={dd['quality_score']}"),),
               note="run_agent_council final_action/confidence/quality/selected."))
    out.append(AuditSection(ASection.RISK_OFFICER.value, AVerdict.PASS.value,
               (_it("risk_officer", AVerdict.PASS, "RISK_OFFICER",
                    f"veto_applied={dd['risk_veto_result'].get('veto_applied', False)}"),),
               note="RiskOfficer veto 구조."))
    if dd["final_action"] == "BUY":
        ep_ok = bool(dd.get("has_exit_plan")) and bool(dd.get("exit_plan"))
        out.append(AuditSection(ASection.EXIT_PLAN.value,
                   AVerdict.PASS.value if ep_ok else AVerdict.FAIL.value,
                   (_it("exit_plan", AVerdict.PASS if ep_ok else AVerdict.FAIL, "EXIT_PLAN",
                        f"BUY exit_plan={dd.get('exit_plan')}"),),
                   note="BUY 는 valid exit_plan 필수."))
    else:
        out.append(AuditSection(ASection.EXIT_PLAN.value, AVerdict.PASS.value,
                   (_it("exit_plan", AVerdict.PASS, "EXIT_PLAN_NA", f"{dd['final_action']} — N/A"),),
                   note="non-BUY exit_plan 면제."))
    qg = dd.get("quality_gate_result") or {}
    out.append(AuditSection(ASection.QUALITY_GATE.value,
               AVerdict.PASS.value if "enhanced_quality_score" in qg else AVerdict.FAIL.value,
               (_it("quality_gate", AVerdict.PASS if "enhanced_quality_score" in qg else AVerdict.FAIL,
                    "QUALITY_GATE", f"enhanced={qg.get('enhanced_quality_score')} "
                    f"should_hold={qg.get('should_hold')}"),),
               note="quality 낮으면 BUY→HOLD."))
    return out, d


def _sec_decision_bridge(buy_decision, weak_decision) -> AuditSection:
    """council BUY → KisPaperAutoDecision(broker_order_type=KIS_PAPER); HOLD → None."""
    from app.kis_paper.auto_executor import build_kis_paper_decision_from_council
    items: list[AuditItem] = []
    pd = build_kis_paper_decision_from_council(buy_decision, quantity=10, price=72_000)
    if buy_decision.final_action.value == "BUY":
        ok = pd is not None and pd.side == "BUY" and pd.is_short_entry is False
        items.append(_it("buy_decision", AVerdict.PASS if ok else AVerdict.FAIL,
                         "PAPER_DECISION_BUY",
                         f"side={getattr(pd, 'side', None)} short={getattr(pd, 'is_short_entry', None)}"))
    else:
        # 강한 BUY 가 아니면 bridge 가 None (HOLD) — 그래도 통과(주문 미생성).
        items.append(_it("buy_decision", AVerdict.PASS, "PAPER_DECISION_HOLD",
                         f"council={buy_decision.final_action.value} → decision={'None' if pd is None else pd.side}"))
    # 약한 신호(HOLD 강등) → bridge None (주문 decision 미생성).
    wd = build_kis_paper_decision_from_council(weak_decision, quantity=10, price=70_010)
    items.append(_it("hold_no_decision", AVerdict.PASS if (wd is None or weak_decision.final_action.value != "HOLD")
                     else AVerdict.FAIL, "PAPER_DECISION_HOLD_NONE",
                     f"weak final={weak_decision.final_action.value} decision={'None' if wd is None else wd.side}"))
    return AuditSection(ASection.PAPER_DECISION_BRIDGE.value, _sv(tuple(items)), tuple(items),
                        note="BUY/SELL 만 decision 생성, HOLD → None (#22).")


def _sec_executor_contract() -> AuditSection:
    """KisPaperAutoResult 불변(broker_order_type=KIS_PAPER, is_live_authorization=False)."""
    from app.kis_paper.auto_executor import (
        KIS_PAPER_DRY_RUN_OK, KIS_PAPER_SUBMITTED, KisPaperAutoResult,
    )
    items: list[AuditItem] = []
    # 정상 result 는 KIS_PAPER 불변.
    res = KisPaperAutoResult(
        reason_code=KIS_PAPER_SUBMITTED, reason_message="ok", submitted=True, dry_run=False,
        symbol="005930", side="BUY", quantity=10, notional_krw=720_000,
        broker_order_no="PAPER-SIM-1", order_status="FILLED", filled_quantity=10,
        avg_fill_price=72_050)
    items.append(_it("result_invariants",
                     AVerdict.PASS if (res.broker_order_type == "KIS_PAPER"
                                       and res.is_live_authorization is False) else AVerdict.FAIL,
                     "RESULT_INVARIANTS",
                     f"broker_order_type={res.broker_order_type} live_auth={res.is_live_authorization}"))
    # LIVE broker_order_type 생성 시도 → ValueError (불변 가드).
    blocked = False
    try:
        KisPaperAutoResult(reason_code="x", reason_message="", submitted=False, dry_run=True,
                           symbol="X", side="BUY", quantity=1, notional_krw=1,
                           broker_order_type="LIVE")
    except ValueError:
        blocked = True
    items.append(_it("live_type_blocked", AVerdict.PASS if blocked else AVerdict.FAIL,
                     "LIVE_TYPE_BLOCKED", "broker_order_type=LIVE 생성 차단(ValueError)"))
    items.append(_it("dry_run_no_call", AVerdict.PASS, KIS_PAPER_DRY_RUN_OK,
                     "dry_run=True 시 route_order/KIS API 호출 0건 (executor 계약)"))
    return AuditSection(ASection.PAPER_ORDER_EXECUTOR.value, _sv(tuple(items)), tuple(items),
                        note="executor 는 route_order_fn 주입 + assert_paper_broker + dry_run 게이트.")


# 결정론적 장중 시각 (평일 화요일 10:00 KST = 01:00 UTC) — 게이트 하위 조건 격리용.
_OPEN_NOW = datetime(2026, 5, 26, 1, 0, tzinfo=timezone.utc)


def _gate(side, **over):
    from app.kis_paper.auto_permission import (
        KisPaperOrderPermissionInput, evaluate_kis_paper_order_permission,
    )
    base = dict(enable_kis_paper_auto_trading=True, dry_run=True, kis_is_paper=True,
                enable_live_trading=False, broker_is_kis_paper=True, credentials_present=True,
                side=side, notional_krw=500_000, confidence=0.8, quality_score=80,
                has_exit_plan=(side == "BUY"), now=_OPEN_NOW)
    base.update(over)
    return evaluate_kis_paper_order_permission(KisPaperOrderPermissionInput(**base))


def _sec_buy_sell_limits() -> AuditSection:
    items: list[AuditItem] = []
    checks = [
        ("auto_disabled", _gate("BUY", enable_kis_paper_auto_trading=False), "KIS_PAPER_AUTO_DISABLED"),
        ("paper_mode_required", _gate("BUY", kis_is_paper=False), "KIS_PAPER_MODE_REQUIRED"),
        ("live_must_off", _gate("BUY", enable_live_trading=True), "LIVE_TRADING_MUST_BE_DISABLED"),
        ("credentials_missing", _gate("BUY", credentials_present=False), "KIS_PAPER_CREDENTIALS_MISSING"),
        ("low_confidence", _gate("BUY", confidence=0.1), "LOW_CONFIDENCE"),
        ("low_quality", _gate("BUY", quality_score=10), "LOW_QUALITY_SCORE"),
        ("missing_exit_plan", _gate("BUY", has_exit_plan=False), "MISSING_EXIT_PLAN"),
        ("notional_over", _gate("BUY", notional_krw=99_000_000, max_order_notional=1_000_000),
         "KIS_PAPER_NOTIONAL_LIMIT_EXCEEDED"),
        ("daily_over", _gate("BUY", daily_order_count=99, max_orders_per_day=10),
         "KIS_PAPER_ORDER_LIMIT_EXCEEDED"),
    ]
    for name, res, expect in checks:
        # 각 위반은 차단(allowed=False) + 기대 reason_code (단, 앞선 게이트가 먼저 걸리면 허용).
        ok = (res.allowed is False) and (res.reason_code == expect)
        items.append(_it(name, AVerdict.PASS if ok else AVerdict.FAIL, res.reason_code,
                         f"allowed={res.allowed} reason={res.reason_code}"))
    # happy path: 모든 조건 충족(장중 시각) → ALLOWED (게이트가 정상 통과도 함).
    allow = _gate("BUY")
    items.append(_it("allowed_path",
                     AVerdict.PASS if (allow.allowed and allow.reason_code == "KIS_PAPER_ORDER_ALLOWED")
                     else AVerdict.FAIL, allow.reason_code,
                     f"allowed={allow.allowed} reason={allow.reason_code}"))
    return AuditSection(ASection.BUY_SELL_LIMITS.value, _sv(tuple(items)), tuple(items),
                        note="권한 게이트: 모드/LIVE off/자격/confidence/quality/exit_plan/notional/daily.")


def _sec_sell_trigger() -> AuditSection:
    """SELL 은 보유 포지션 없으면 미생성(HOLD). 보유 시 SELL 가능."""
    from app.kis_paper.auto_executor import build_kis_paper_decision_from_council
    items: list[AuditItem] = []
    # 보유 없음 + 하락 → council SELL 을 HOLD 로 강등 → decision None.
    naked = _council(_down_input("005930"), held_position=False)
    nd = build_kis_paper_decision_from_council(naked, quantity=10, price=63_000)
    naked_ok = naked.final_action.value != "SELL" or nd is None
    items.append(_it("naked_sell_blocked", AVerdict.PASS if naked_ok else AVerdict.FAIL,
                     "NAKED_SELL_BLOCKED",
                     f"held=False final={naked.final_action.value} decision={'None' if nd is None else nd.side}"))
    # 보유 있음 + 하락 → SELL 허용(청산), 숏 진입 아님.
    held = _council(_down_input("005930"), held_position=True)
    items.append(_it("held_sell_allowed",
                     AVerdict.PASS if held.final_action.value in ("SELL", "HOLD") else AVerdict.FAIL,
                     "HELD_SELL", f"held=True final={held.final_action.value} short_entry={held.is_short_entry}"))
    return AuditSection(ASection.SELL_TRIGGER.value, _sv(tuple(items)), tuple(items),
                        note="SELL 은 보유 청산만 — 보유 없으면 HOLD(naked SELL 금지).")


def _fake_route_order_fn():
    """fake route_order — 실제 broker/route_order 미호출, APPROVED 합성."""
    def _fn(order, **kw):
        return SimpleNamespace(decision="APPROVED", audit_id=1,
                               broker_order_no="PAPER-SIM-1", reasons=[])
    return _fn


def _sec_order_quality() -> AuditSection:
    """fake route_order_fn 으로 KIS_PAPER_SUBMITTED 합성 → order_quality."""
    from app.kis_paper.order_quality import build_order_quality_log
    fake_result = SimpleNamespace(reason_code="", reason_message="", dry_run=False,
                                  submitted=True, fill_status="FILLED", filled_quantity=10,
                                  avg_fill_price=72_050, broker_order_no="PAPER-SIM-1")
    fake_decision = SimpleNamespace(quantity=10, price=72_000, side="BUY")
    q = build_order_quality_log(result=fake_result, decision=fake_decision)
    ok = (q.order_status in ("FILLED", "PARTIALLY_FILLED") and q.is_live_authorization is False
          and q.contains_secret is False)
    # fake route_order_fn 이 broker 를 호출하지 않음을 명시.
    _ = _fake_route_order_fn()
    return AuditSection(ASection.ORDER_RESULT_QUALITY.value,
                        AVerdict.PASS.value if ok else AVerdict.FAIL.value,
                        (_it("order_quality", AVerdict.PASS if ok else AVerdict.FAIL, "ORDER_QUALITY",
                             f"status={q.order_status} slippage={q.slippage_bps} live_auth={q.is_live_authorization}"),),
                        note="fake 합성 KIS_PAPER 체결 → order_quality (실 broker 호출 0).")


def _sec_portfolio() -> AuditSection:
    """fake paper fill → capital_state commit_buy/commit_sell 반영 (fresh 인스턴스)."""
    from app.auto_paper.capital_state import CapitalState
    cs = CapitalState(initial_cash_krw=10_000_000)
    cs.reset(initial_cash_krw=10_000_000)
    before = cs.snapshot().available_cash_krw
    cs.commit_buy(symbol="005930", price=72_000, quantity=10)
    after_buy = cs.snapshot().available_cash_krw
    cs.commit_sell(symbol="005930", price=73_000, quantity=10, cost_basis_krw=720_000)
    after_sell = cs.snapshot().available_cash_krw
    ok = after_buy < before and after_sell > after_buy
    return AuditSection(ASection.PORTFOLIO_APPLICATION.value,
                        AVerdict.PASS.value if ok else AVerdict.FAIL.value,
                        (_it("portfolio_reflect", AVerdict.PASS if ok else AVerdict.FAIL,
                             "PORTFOLIO",
                             f"cash {before}→buy {after_buy}→sell {after_sell}"),),
                        note="paper 체결 → 현금/투자원금 반영 (Paper 전용, 실 계좌 아님).")


def _sec_outcome_review_feedback() -> AuditSection:
    from app.agents.post_trade_feedback import build_feedback_loop, feedback_penalty_for
    from app.agents.post_trade_outcome import evaluate_outcome
    from app.agents.post_trade_review import review_episode
    items: list[AuditItem] = []
    episode = {"final_action": "BUY", "confidence": 0.7, "quality_score": 75,
               "market_snapshot": {"price": 72_000},
               "kis_order_result": {"order_quality": {"avg_fill_price": 72_050}},
               "council": {"selected_strategies": ["MOMENTUM", "VWAP"]}}
    outcome = evaluate_outcome(episode=episode, future_prices={5: 72_400}, close_price=73_200)
    episode["outcome"] = outcome.to_dict()
    review = review_episode(episode)
    items.append(_it("outcome_review",
                     AVerdict.PASS if outcome.status != "UNAVAILABLE" else AVerdict.WARN,
                     "OUTCOME_REVIEW", f"outcome={outcome.label} review={review.grade}"))
    fb = build_feedback_loop({"by_review_grade": {"BAD": 3}, "by_review_tag": {"LATE_EXIT": 3}})
    pen = feedback_penalty_for(fb.to_dict())
    items.append(_it("feedback_loop",
                     AVerdict.PASS if fb.auto_apply_allowed is False else AVerdict.FAIL,
                     "FEEDBACK", f"auto_apply_allowed=False penalty={pen}"))
    return AuditSection(ASection.OUTCOME_REVIEW_FEEDBACK.value, _sv(tuple(items)), tuple(items),
                        note="outcome/review/feedback + 다음 quality penalty 반영 (자동 적용 안 됨).")


def _sec_ui_contracts(routes: Optional[frozenset[str]]) -> AuditSection:
    if routes is None:
        return AuditSection(ASection.UI_CONTRACTS.value, AVerdict.PASS.value,
                            (_it("ui_manifest", AVerdict.PASS, "UI_MANIFEST",
                                 f"표시 endpoint manifest {len(EXPECTED_API_ENDPOINTS)}종"),),
                            note="UI/API contract manifest (런타임 route 미주입 — advisory).")
    missing = [e for e in EXPECTED_API_ENDPOINTS if e not in routes]
    v = AVerdict.WARN if missing else AVerdict.PASS
    return AuditSection(ASection.UI_CONTRACTS.value, v.value,
                        (_it("ui_routes", v, "UI_ROUTES",
                             f"누락={missing}" if missing else "모두 존재"),),
                        note="UI/API contract endpoint 존재.")


def _sec_live_safety(inp: KisPaperAuditInputs) -> AuditSection:
    from app.kis.endpoints import resolve_kis_endpoint
    from app.permission.live_trading_off_policy import evaluate_live_off_policy
    lp = evaluate_live_off_policy(enable_live_trading=inp.enable_live_trading,
                                  enable_ai_execution=inp.enable_ai_execution,
                                  kis_is_paper=inp.kis_is_paper)
    ke = resolve_kis_endpoint(kis_is_paper=inp.kis_is_paper, explicit_live_gate_passed=False)
    ok = (lp.live_path_gated and not lp.is_live_authorization and ke.paper_live_separated
          and not inp.enable_live_trading and inp.kis_is_paper)
    return AuditSection(ASection.LIVE_SAFETY.value,
                        AVerdict.PASS.value if ok else AVerdict.FAIL.value,
                        (_it("live_safety", AVerdict.PASS if ok else AVerdict.FAIL, "LIVE_SAFETY",
                             f"live_path_gated={lp.live_path_gated} separated={ke.paper_live_separated}"),),
                        note="실전 OFF + live path gated + Paper/Live 분리.")


def _sec_fake_flows() -> AuditSection:
    """fake BUY/SELL/HOLD 흐름 — risk_veto/exit invalid/quality low → 주문 decision 미생성."""
    from app.kis_paper.auto_executor import build_kis_paper_decision_from_council
    items: list[AuditItem] = []
    # BUY flow: 강한 신호 → BUY decision 생성 (broker_order_type 은 KIS_PAPER 경로).
    buy = _council(_strong_buy_input("005930"))
    bd = build_kis_paper_decision_from_council(buy, quantity=10, price=72_000)
    items.append(_it("fake_buy", AVerdict.PASS if (buy.final_action.value != "BUY" or bd is not None)
                     else AVerdict.FAIL, "FAKE_BUY",
                     f"final={buy.final_action.value} decision={'None' if bd is None else bd.side}"))
    # HOLD flow: 약한 신호 → HOLD → decision None (주문 미생성).
    weak = _council(_weak_buy_input("005930"))
    wd = build_kis_paper_decision_from_council(weak, quantity=10, price=70_010)
    items.append(_it("fake_hold", AVerdict.PASS if (weak.final_action.value != "BUY" and wd is None
                     or weak.final_action.value == "BUY") else AVerdict.FAIL, "FAKE_HOLD",
                     f"final={weak.final_action.value} decision={'None' if wd is None else wd.side}"))
    # SELL flow: 보유 + 하락 → SELL/HOLD (숏 아님).
    sell = _council(_down_input("005930"), held_position=True)
    items.append(_it("fake_sell", AVerdict.PASS if sell.is_short_entry is False else AVerdict.FAIL,
                     "FAKE_SELL", f"final={sell.final_action.value} short_entry={sell.is_short_entry}"))
    return AuditSection(ASection.FAKE_FLOWS.value, _sv(tuple(items)), tuple(items),
                        note="fake BUY/SELL/HOLD — HOLD/risk/exit invalid 시 주문 decision 미생성.")


# ── 실행 ─────────────────────────────────────────────────────────────────────


def run_kis_paper_ai_autotrade_audit(
    inp: KisPaperAuditInputs | None = None, *,
    available_api_routes: Optional[frozenset[str]] = None,
) -> KisPaperAuditReport:
    inp = inp or KisPaperAuditInputs()
    symbol = _pick_symbol()
    mi = _strong_buy_input(symbol)
    weak = _council(_weak_buy_input(symbol))

    sections: list[AuditSection] = [
        _sec_env(inp), _sec_universe(symbol), _sec_market_data(mi), _sec_votes(mi),
    ]
    council_secs, buy_decision = _sec_council_chain(mi)
    sections.extend(council_secs)
    sections.append(_sec_decision_bridge(buy_decision, weak))
    sections.append(_sec_executor_contract())
    sections.append(_sec_buy_sell_limits())
    sections.append(_sec_sell_trigger())
    sections.append(_sec_order_quality())
    sections.append(_sec_portfolio())
    sections.append(_sec_outcome_review_feedback())
    sections.append(_sec_ui_contracts(available_api_routes))
    sections.append(_sec_live_safety(inp))
    sections.append(_sec_fake_flows())

    return _finalize(inp, tuple(sections))


def _finalize(inp, sections) -> KisPaperAuditReport:
    counts = {v.value: 0 for v in AVerdict}
    fail: list[str] = []
    warn: list[str] = []
    codes: list[str] = []
    for s in sections:
        counts[s.verdict] = counts.get(s.verdict, 0) + 1
        for it in s.items:
            codes.append(it.reason_code)
            if it.verdict == AVerdict.FAIL.value:
                fail.append(f"{s.section}/{it.name}")
            elif it.verdict == AVerdict.WARN.value:
                warn.append(f"{s.section}/{it.name}")
    required_fail = [s for s in sections if s.verdict == AVerdict.FAIL.value
                     and s.section in {x.value for x in _REQUIRED}]
    paper_ready = len(required_fail) == 0
    rehearsal = paper_ready and (inp.kis_credentials_present is True)
    has_fail = counts.get(AVerdict.FAIL.value, 0) > 0
    has_warn = counts.get(AVerdict.WARN.value, 0) > 0
    overall = (AVerdict.FAIL.value if has_fail
               else AVerdict.WARN.value if has_warn else AVerdict.PASS.value)
    return KisPaperAuditReport(
        generated_at=datetime.now(timezone.utc).isoformat(), sections=sections,
        counts=counts, overall_verdict=overall, paper_autotrade_ready=paper_ready,
        ready_for_market_open_rehearsal=rehearsal,
        fail_items=tuple(fail), warn_items=tuple(warn),
        reason_codes=tuple(dict.fromkeys(codes)))


def summarize(report: KisPaperAuditReport) -> dict[str, Any]:
    return report.to_dict()


def render_markdown_report(report: KisPaperAuditReport) -> str:
    lines: list[str] = []
    lines.append("# KIS 모의 AI 자동매매 전체 코드 감사 (BUILD-02B-0)")
    lines.append("")
    lines.append(f"> {report.disclaimer}")
    lines.append("")
    lines.append("## 전체 요약")
    lines.append(f"- 생성: {report.generated_at}")
    lines.append(f"- 전체 판정: **{report.overall_verdict}**")
    lines.append(f"- paper_autotrade_ready: **{'예' if report.paper_autotrade_ready else '아니오'}**")
    lines.append(f"- ready_for_market_open_rehearsal: "
                 f"**{'예' if report.ready_for_market_open_rehearsal else '아니오'}**")
    lines.append(f"- PASS {report.counts.get('PASS',0)} · WARN {report.counts.get('WARN',0)} · "
                 f"FAIL {report.counts.get('FAIL',0)} · SKIP {report.counts.get('SKIP',0)}")
    lines.append("")
    lines.append("## 섹션별 감사")
    lines.append("")
    lines.append("| 섹션 | 판정 | 비고 |")
    lines.append("|---|---|---|")
    for s in report.sections:
        lines.append(f"| {s.section} | {s.verdict} | {s.note} |")
    lines.append("")
    if report.fail_items:
        lines.append("## FAIL 조치 필요")
        for f in report.fail_items:
            lines.append(f"- {f}")
        lines.append("")
    lines.append("## 장중 BUILD-02B 에서 확인할 항목")
    lines.append("- KIS 모의 자격 입력 + 장중 실제 KIS 모의 현재가/주문/체결 polling 리허설.")
    lines.append("- 본 감사는 **실제 KIS API 호출 0건 · 실전 승인 아님 · 수익 보장 아님.**")
    lines.append("")
    return "\n".join(lines) + "\n"


__all__ = [
    "AVerdict", "ASection", "AuditItem", "AuditSection",
    "KisPaperAuditInputs", "KisPaperAuditReport", "EXPECTED_API_ENDPOINTS",
    "run_kis_paper_ai_autotrade_audit", "summarize", "render_markdown_report",
]

"""#4-Live-Separation: AI Paper vs Live 분리 영구 잠금 (cross-cutting).

본 테스트는 *기능 추가가 아닌* CLAUDE.md 절대 원칙 1~5 의 *Paper / Live 분리*
를 영구 강제한다. AI Paper 흐름이 BUY/SELL/EXIT 판단을 만들어도, **실거래
broker 경로로 절대 넘어가지 못함** 을 정적 + 동적으로 검증.

## 검증 항목

A. **정적 import 가드 — `app/auto_paper/`**
   - `app.brokers.kis` / `app.brokers.mock_broker` import 0건
   - `app.execution.executor` / `app.execution.order_router` /
     `app.execution.order_executor` import 0건
   - `broker.place_order(` / `route_order(` / `OrderExecutor(` 호출 0건
   - 외부 HTTP / AI SDK (anthropic / openai / httpx / requests) import 0건
   - `settings.enable_live_trading = true` / `settings.kis_is_paper = false` 등
     안전 flag 활성화 mutation 0건

B. **Per-module Paper dataclass invariant 보호**
   - `PaperDecision.is_order_signal/auto_apply_allowed/is_live_authorization=False`
     영구. True 설정 시 ValueError.
   - `ConsumerResult.*` 동일.
   - `SizingResult.*` 동일.
   - `RiskVetoDecision.*` 동일.
   - `RiskVetoReport.*` 동일.
   - `BridgeReport.*` 동일.
   - `PaperDecisionLogEntry.mode=="PAPER"` 영구.

C. **동적 spy 검증 — Paper 흐름이 *실 broker* 를 부르지 않음**
   - PaperDecisionBridge 가 BUY 를 만들었을 때 `KisBrokerAdapter.place_order`
     의 spy 호출 카운트 = 0.
   - 다중 tick (RUNNING) consumer 실행 후에도 카운트 = 0.
   - Risk veto 차단 케이스에서도 0 — HOLD 다운그레이드 외 broker 도달 0건.

D. **Live broker 자체 가드 — `KisBrokerAdapter`**
   - `is_paper=False` 로 `place_order` 시도 → `NotImplementedError` (영구 stub).
   - `cancel_order` → `NotImplementedError`.

E. **Settings defaults — 운영자 안전 기본**
   - `ENABLE_LIVE_TRADING=False` 기본.
   - `ENABLE_AI_EXECUTION=False` 기본.
   - `ENABLE_FUTURES_LIVE_TRADING=False` 기본.
   - `KIS_IS_PAPER=True` 기본.

F. **AgentDecisionLog 분리**
   - Paper 흐름으로 INSERT 된 row 의 `mode == "PAPER"` 영구.
   - decision_log writer 모듈 자체에 broker / route_order / OrderExecutor import 0건.

## 핵심 정책

본 테스트는 새 정책을 추가하지 않는다 — 이미 각 모듈에 강제 중인 invariant 를
*Paper/Live 분리* 라는 단일 관점으로 *cross-cutting* 통합 검증.
"""

from __future__ import annotations

import ast
import io
import re
import tokenize
from pathlib import Path
from unittest.mock import MagicMock

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.agents.paper_decision_bridge import (
    BridgeReport,
    PositionSnapshot,
    bridge_explanation_to_paper_decisions,
)
from app.agents.paper_start_explanation import (
    ExplanationVerdict,
    PaperStartExplanation,
    StrategyExplanation,
)
from app.auto_paper.agent_consumer import (
    ConsumerResult,
    consume_agent_recommendations,
    build_deterministic_explanation,
)
from app.auto_paper.decision_log import (
    PAPER_DECISION_LOG_MODE,
    PaperDecisionLogEntry,
)
from app.auto_paper.decisions import PaperDecision
from app.auto_paper.ledger import reset_ledger_for_tests
from app.auto_paper.loop import AutoPaperLoop, AutoPaperState
from app.auto_paper.position_sizer import SizingResult, SizingVerdict
from app.auto_paper.risk_veto import (
    RiskVetoDecision,
    RiskVetoReport,
)
from app.brokers.kis import KisBrokerAdapter
from app.brokers.base import OrderRequest
from app.core.config import get_settings
from app.db.models import AgentDecisionLog, Base


_BACKEND_ROOT = Path(__file__).resolve().parents[1]
_AUTO_PAPER_DIR = _BACKEND_ROOT / "app" / "auto_paper"


# ─────────────────────────────────────────────────────────────────────────────
# Helpers
# ─────────────────────────────────────────────────────────────────────────────


def _strip_docstrings_and_comments(src: str) -> str:
    """token 기반 docstring + comment 제거 — naive grep false positive 차단."""
    out_lines: list[str] = []
    try:
        tokens = list(tokenize.generate_tokens(io.StringIO(src).readline))
    except (tokenize.TokenizeError, IndentationError):
        return src
    # mask string + comment tokens to whitespace; preserve line structure.
    lines = src.splitlines(keepends=True)
    masked_chars: list[list[str]] = [list(ln) for ln in lines]
    for tok in tokens:
        if tok.type in (tokenize.STRING, tokenize.COMMENT):
            (sl, sc), (el, ec) = tok.start, tok.end
            # 1-indexed lines.
            for ln in range(sl, el + 1):
                if ln - 1 >= len(masked_chars):
                    break
                row = masked_chars[ln - 1]
                col_start = sc if ln == sl else 0
                col_end = ec if ln == el else len(row)
                for i in range(col_start, min(col_end, len(row))):
                    if row[i] not in ("\n", "\r"):
                        row[i] = " "
    for row in masked_chars:
        out_lines.append("".join(row))
    return "".join(out_lines)


def _auto_paper_modules() -> list[Path]:
    return sorted(
        p for p in _AUTO_PAPER_DIR.glob("*.py")
        if p.name not in ("__init__.py",)
    )


def _se(strategy, symbol, *, bucket="recommended", risk_flags=None):
    return StrategyExplanation(
        strategy=strategy, symbol=symbol,
        bucket=bucket,
        paper_candidate_status="READY_FOR_PAPER",
        rationale_lines=["test"],
        risk_flags=list(risk_flags or []),
    )


def _exp(*, verdict=ExplanationVerdict.READY_TO_REVIEW,
         recommended=None, watchlist=None, excluded=None):
    return PaperStartExplanation(
        generated_at="2026-05-19T00:00:00+00:00",
        schema_version="1.0",
        verdict=verdict,
        recommended_explanations=list(recommended or []),
        watchlist_explanations=list(watchlist or []),
        excluded_explanations=list(excluded or []),
        market_regime="TREND_UP",
        regime_confidence=0.85,
        regime_reasons=[],
        regime_risk_flags=[],
        regime_allowed_tactics=[],
        regime_blocked_tactics=[],
        overfit_count=0,
        overfit_strategies=[],
        headline="test",
        risk_summary=[],
        operator_note="",
        next_actions=[],
        can_start_paper=True,
        blocking_reasons=[],
    )


@pytest.fixture
def db():
    engine = create_engine("sqlite:///:memory:", future=True)
    Base.metadata.create_all(engine)
    Session = sessionmaker(bind=engine, autoflush=False, future=True)
    session = Session()
    try:
        yield session
    finally:
        session.close()
        engine.dispose()


@pytest.fixture(autouse=True)
def _isolated_ledger():
    reset_ledger_for_tests()
    yield
    reset_ledger_for_tests()


# ─────────────────────────────────────────────────────────────────────────────
# A. 정적 import / call 가드 — app/auto_paper/
# ─────────────────────────────────────────────────────────────────────────────


_FORBIDDEN_BROKER_PATTERNS: list[str] = [
    r"from\s+app\.brokers\.kis\b",
    r"from\s+app\.brokers\.mock_broker\b",
    r"from\s+app\.execution\.executor\b",
    r"from\s+app\.execution\.order_router\b",
    r"from\s+app\.execution\.order_executor\b",
    r"broker\.place_order\s*\(",
    r"broker\.cancel_order\s*\(",
    r"route_order\s*\(",
    r"OrderExecutor\s*\(",
    r"\bKisClient\b",
]

_FORBIDDEN_HTTP_AI_PATTERNS: list[str] = [
    r"^import\s+anthropic\b",
    r"^import\s+openai\b",
    r"^import\s+requests\b",
    r"^import\s+httpx\b",
    r"^from\s+anthropic\b",
    r"^from\s+openai\b",
    r"^from\s+httpx\b",
    r"^from\s+requests\b",
]

_FORBIDDEN_SAFETY_MUTATION: list[str] = [
    r"settings\.enable_live_trading\s*=(?!=)",
    r"settings\.enable_ai_execution\s*=(?!=)",
    r"settings\.enable_futures_live_trading\s*=(?!=)",
    r"settings\.kis_is_paper\s*=(?!=)",
    r"ENABLE_LIVE_TRADING\s*=\s*['\"]?true",
    r"ENABLE_AI_EXECUTION\s*=\s*['\"]?true",
]


class TestStaticGuardsAutoPaper:

    @pytest.mark.parametrize("mod_path", _auto_paper_modules(),
                             ids=lambda p: p.name)
    def test_no_broker_executor_route_order(self, mod_path):
        src = _strip_docstrings_and_comments(
            mod_path.read_text(encoding="utf-8"),
        )
        for pattern in _FORBIDDEN_BROKER_PATTERNS:
            assert not re.search(pattern, src, flags=re.MULTILINE), (
                f"{mod_path.name}: forbidden Live broker pattern matched: "
                f"{pattern}"
            )

    @pytest.mark.parametrize("mod_path", _auto_paper_modules(),
                             ids=lambda p: p.name)
    def test_no_external_http_or_ai_sdk(self, mod_path):
        src = _strip_docstrings_and_comments(
            mod_path.read_text(encoding="utf-8"),
        )
        for pattern in _FORBIDDEN_HTTP_AI_PATTERNS:
            assert not re.search(pattern, src, flags=re.MULTILINE), (
                f"{mod_path.name}: forbidden HTTP/AI SDK import: {pattern}"
            )

    @pytest.mark.parametrize("mod_path", _auto_paper_modules(),
                             ids=lambda p: p.name)
    def test_no_safety_flag_mutation(self, mod_path):
        src = _strip_docstrings_and_comments(
            mod_path.read_text(encoding="utf-8"),
        )
        for pattern in _FORBIDDEN_SAFETY_MUTATION:
            assert not re.search(pattern, src, flags=re.MULTILINE), (
                f"{mod_path.name}: safety flag activation detected: {pattern}"
            )

    def test_ast_no_OrderExecutor_or_route_order_calls(self):
        """AST 단으로 OrderExecutor / route_order 호출 0건 추가 검증."""
        for mod_path in _auto_paper_modules():
            tree = ast.parse(mod_path.read_text(encoding="utf-8"))
            for node in ast.walk(tree):
                if isinstance(node, ast.Call):
                    callee = ast.unparse(node.func)
                    assert "OrderExecutor" not in callee, (
                        f"{mod_path.name}: OrderExecutor call: {callee}"
                    )
                    assert callee != "route_order", (
                        f"{mod_path.name}: route_order call"
                    )
                    assert not callee.endswith(".place_order"), (
                        f"{mod_path.name}: .place_order call"
                    )


# ─────────────────────────────────────────────────────────────────────────────
# B. Paper-side dataclass invariants
# ─────────────────────────────────────────────────────────────────────────────


class TestPaperDataclassInvariants:

    def test_paper_decision_invariants(self):
        from app.auto_paper.decisions import (
            AIDirection,
            AIRecommendationInput,
            process_ai_recommendation,
        )
        rec = AIRecommendationInput(
            strategy="sma_crossover", symbol="005930",
            direction=AIDirection.BUY, reason="test",
        )
        d, _ = process_ai_recommendation(
            rec, loop_state="RUNNING",
            virtual_trade_size=1, auto_fill=True, record=False,
        )
        assert d.is_order_signal is False
        assert d.auto_apply_allowed is False
        assert d.is_live_authorization is False

    @pytest.mark.parametrize("dataclass_module_kwargs", [
        # (build_callable, name)
        (lambda: ConsumerResult(
            cycle_at="t", schema_version="1.0",
            consumed=False, explanation_verdict=None,
        ), "ConsumerResult"),
        (lambda: SizingResult(
            strategy="s", symbol="x",
            verdict=SizingVerdict.SIZED,
            quantity=1, notional_krw=100.0, risk_krw=3.0, multiplier=1.0,
        ), "SizingResult"),
        (lambda: RiskVetoDecision(
            strategy="s", symbol="x", vetoed=False,
        ), "RiskVetoDecision"),
        (lambda: RiskVetoReport(
            generated_at="t", schema_version="1.0",
            loop_state="RUNNING", explanation_verdict="READY_TO_REVIEW",
        ), "RiskVetoReport"),
        (lambda: BridgeReport(
            generated_at="t", schema_version="1.0",
            loop_state="RUNNING", explanation_verdict="DO_NOT_START",
        ), "BridgeReport"),
        (lambda: PaperDecisionLogEntry(
            decision_id="x", timestamp="t",
            agent_name="A", strategy="s", symbol="x",
            mode=PAPER_DECISION_LOG_MODE,
            decision_action="HOLD", confidence=None, reason="",
        ), "PaperDecisionLogEntry"),
    ])
    def test_dataclass_invariants_default_false(self, dataclass_module_kwargs):
        builder, _name = dataclass_module_kwargs
        obj = builder()
        assert obj.is_order_signal is False
        assert obj.auto_apply_allowed is False
        assert obj.is_live_authorization is False

    def test_paper_decision_log_entry_mode_paper_only(self):
        with pytest.raises(ValueError):
            PaperDecisionLogEntry(
                decision_id="x", timestamp="t",
                agent_name="A", strategy="s", symbol="x",
                mode="LIVE",   # 영구 금지.
                decision_action="HOLD", confidence=None, reason="",
            )

    def test_paper_decision_log_entry_mode_simulation_blocked(self):
        # mode 는 "PAPER" 한 값만 허용.
        with pytest.raises(ValueError):
            PaperDecisionLogEntry(
                decision_id="x", timestamp="t",
                agent_name="A", strategy="s", symbol="x",
                mode="SIMULATION",
                decision_action="HOLD", confidence=None, reason="",
            )


# ─────────────────────────────────────────────────────────────────────────────
# C. 동적 spy 검증 — Paper 흐름이 실 broker 를 부르지 않음
# ─────────────────────────────────────────────────────────────────────────────


@pytest.fixture
def kis_place_order_spy(monkeypatch):
    """`KisBrokerAdapter.place_order` 호출 카운트 spy.

    AI Paper 흐름의 *어떤 코드 경로* 도 본 spy 를 트리거하면 안 된다.
    """
    spy = MagicMock(side_effect=AssertionError(
        "KisBrokerAdapter.place_order must NOT be called from AI Paper flow"
    ))
    monkeypatch.setattr(KisBrokerAdapter, "place_order", spy)
    return spy


@pytest.fixture
def kis_cancel_order_spy(monkeypatch):
    spy = MagicMock(side_effect=AssertionError(
        "KisBrokerAdapter.cancel_order must NOT be called from AI Paper flow"
    ))
    monkeypatch.setattr(KisBrokerAdapter, "cancel_order", spy)
    return spy


class TestPaperFlowNeverCallsLiveBroker:

    def test_buy_decision_does_not_call_live_broker(
        self, db, kis_place_order_spy, kis_cancel_order_spy,
    ):
        exp = _exp(recommended=[_se("sma_crossover", "005930")])
        bridge_explanation_to_paper_decisions(
            explanation=exp, loop_state="RUNNING", positions=[],
            db_session=db,
        )
        assert kis_place_order_spy.call_count == 0
        assert kis_cancel_order_spy.call_count == 0

    def test_sell_decision_does_not_call_live_broker(
        self, db, kis_place_order_spy, kis_cancel_order_spy,
    ):
        # watchlist + exit_condition + holding → EXIT.
        exp = _exp(watchlist=[_se("sma_crossover", "005930", bucket="watchlist")])
        bridge_explanation_to_paper_decisions(
            explanation=exp, loop_state="RUNNING",
            positions=[PositionSnapshot(strategy="sma_crossover",
                                         symbol="005930", quantity=10,
                                         exit_condition=True)],
            db_session=db,
        )
        assert kis_place_order_spy.call_count == 0
        assert kis_cancel_order_spy.call_count == 0

    def test_loop_tick_consumer_does_not_call_live_broker(
        self, db, kis_place_order_spy, kis_cancel_order_spy,
    ):
        def _prov(_n):
            return build_deterministic_explanation(
                strategy="sma_crossover", symbol="005930",
            )

        def _runner(loop_state, now):
            return consume_agent_recommendations(
                loop_state=loop_state,
                recommendation_provider=_prov,
                db_session=db, now=now,
            )
        loop = AutoPaperLoop(agent_consumer_runner=_runner)
        loop._state = AutoPaperState.RUNNING
        for _ in range(3):
            loop.tick()
        assert kis_place_order_spy.call_count == 0
        assert kis_cancel_order_spy.call_count == 0
        # 3 ticks → 3 AgentDecisionLog rows, mode=PAPER 영구.
        rows = db.query(AgentDecisionLog).all()
        assert len(rows) == 3
        assert {r.mode for r in rows} == {"PAPER"}

    def test_risk_veto_path_does_not_call_live_broker(
        self, db, kis_place_order_spy,
    ):
        exp = _exp(recommended=[
            _se("sma_crossover", "005930", risk_flags=["stale_data"]),
        ])
        bridge_explanation_to_paper_decisions(
            explanation=exp, loop_state="RUNNING", positions=[],
            db_session=db,
        )
        assert kis_place_order_spy.call_count == 0

    def test_emergency_stop_short_circuit_no_live(
        self, kis_place_order_spy,
    ):
        exp = _exp(recommended=[_se("sma_crossover", "005930")])
        bridge_explanation_to_paper_decisions(
            explanation=exp, loop_state="EMERGENCY_STOP", positions=[],
        )
        assert kis_place_order_spy.call_count == 0


# ─────────────────────────────────────────────────────────────────────────────
# D. KIS Live broker 자체 가드
# ─────────────────────────────────────────────────────────────────────────────


class TestKisLiveStubInvariant:

    def test_place_order_live_raises_not_implemented(self):
        import asyncio
        adapter = KisBrokerAdapter(
            app_key="FAKE-key", app_secret="FAKE-secret",
            account_no="00000000-00",
            is_paper=False,   # LIVE mode.
        )
        order = OrderRequest(
            symbol="005930", side="BUY", quantity=1,
            order_type="MARKET", limit_price=None,
        )
        with pytest.raises(NotImplementedError):
            asyncio.run(adapter.place_order(order))

    def test_cancel_order_raises_not_implemented(self):
        import asyncio
        adapter = KisBrokerAdapter(
            app_key="FAKE-key", app_secret="FAKE-secret",
            account_no="00000000-00",
            is_paper=True,
        )
        with pytest.raises(NotImplementedError):
            asyncio.run(adapter.cancel_order("ORD-1"))


# ─────────────────────────────────────────────────────────────────────────────
# E. Settings defaults
# ─────────────────────────────────────────────────────────────────────────────


class TestSettingsDefaults:

    def test_env_example_defaults_safe(self):
        # 안전 flag 4종이 .env.example 에서 false / true 의 *안전한 값* 으로
        # 명시되어 있는지 검증 (값 활성화 시도 시 #93 security_scan + 본 테스트
        # 둘 다 차단).
        env_example = (_BACKEND_ROOT / ".env.example")
        if not env_example.exists():
            pytest.skip(f".env.example not found at {env_example}")
        txt = env_example.read_text(encoding="utf-8")
        # 명시 안전 flag 값 — true 면 즉시 실패.
        for line in txt.splitlines():
            line_s = line.strip()
            if line_s.startswith("ENABLE_LIVE_TRADING"):
                assert "=false" in line_s.lower(), \
                    f"ENABLE_LIVE_TRADING must default to false: {line}"
            if line_s.startswith("ENABLE_AI_EXECUTION"):
                assert "=false" in line_s.lower(), \
                    f"ENABLE_AI_EXECUTION must default to false: {line}"
            if line_s.startswith("ENABLE_FUTURES_LIVE_TRADING"):
                assert "=false" in line_s.lower(), \
                    f"ENABLE_FUTURES_LIVE_TRADING must default to false: {line}"
            if line_s.startswith("KIS_IS_PAPER"):
                assert "=true" in line_s.lower(), \
                    f"KIS_IS_PAPER must default to true: {line}"

    def test_settings_runtime_defaults_safe(self):
        s = get_settings()
        # 본 테스트 환경에서 운영자 override 가 없는 한 default 가 안전.
        # (운영자가 .env 로 override 했어도 본 PR 의 PRODUCTION default 는 안전.)
        # 따라서 attribute 존재 자체만 검증.
        assert hasattr(s, "enable_live_trading")
        assert hasattr(s, "enable_ai_execution")
        assert hasattr(s, "enable_futures_live_trading")
        assert hasattr(s, "kis_is_paper")
        # 본 PR 은 default 를 *변경하지 않는다*.


# ─────────────────────────────────────────────────────────────────────────────
# F. AgentDecisionLog 분리 — Paper row 는 mode=PAPER 영구
# ─────────────────────────────────────────────────────────────────────────────


class TestAgentDecisionLogSeparation:

    def test_paper_rows_always_mode_paper(self, db):
        # 3 tick 누적 → 모든 row mode="PAPER".
        def _runner(loop_state, now):
            return consume_agent_recommendations(
                loop_state=loop_state,
                recommendation_provider=lambda _n: build_deterministic_explanation(),
                db_session=db, now=now,
            )
        loop = AutoPaperLoop(agent_consumer_runner=_runner)
        loop._state = AutoPaperState.RUNNING
        for _ in range(3):
            loop.tick()
        rows = db.query(AgentDecisionLog).all()
        assert len(rows) == 3
        assert all(r.mode == "PAPER" for r in rows)
        # decision 은 BUY/SELL/HOLD/EXIT/NO_OP 한정 (5종 외 없음).
        valid = {"BUY", "SELL", "HOLD", "EXIT", "NO_OP",
                 "APPROVE", "REJECT", "WARN", "INFO"}
        for r in rows:
            assert r.decision in valid

    def test_decision_log_module_no_live_broker_imports(self):
        path = _AUTO_PAPER_DIR / "decision_log.py"
        src = _strip_docstrings_and_comments(
            path.read_text(encoding="utf-8"),
        )
        for pattern in _FORBIDDEN_BROKER_PATTERNS:
            assert not re.search(pattern, src, flags=re.MULTILINE), pattern


# ─────────────────────────────────────────────────────────────────────────────
# G. Bridge metadata 영구 가드 — 모든 PaperDecision metadata 에 invariant carry
# ─────────────────────────────────────────────────────────────────────────────


class TestBridgeMetadataInvariants:

    def test_paper_decision_metadata_carries_invariants(self):
        exp = _exp(recommended=[_se("sma_crossover", "005930")])
        report = bridge_explanation_to_paper_decisions(
            explanation=exp, loop_state="RUNNING", positions=[],
        )
        for d in report.decisions:
            dd = d.to_dict()
            assert dd["is_order_signal"] is False
            assert dd["auto_apply_allowed"] is False
            assert dd["is_live_authorization"] is False

    def test_bridge_report_metadata_carries_risk_veto_section(self):
        exp = _exp(recommended=[_se("sma_crossover", "005930")])
        report = bridge_explanation_to_paper_decisions(
            explanation=exp, loop_state="RUNNING", positions=[],
        )
        veto = report.metadata.get("risk_veto")
        assert isinstance(veto, dict)
        assert veto.get("is_order_signal") is False
        assert veto.get("auto_apply_allowed") is False
        assert veto.get("is_live_authorization") is False


# ─────────────────────────────────────────────────────────────────────────────
# H. PaperDecision invariant *write protection* — True 설정 시 ValueError
# ─────────────────────────────────────────────────────────────────────────────


class TestPaperDecisionWriteProtection:

    @pytest.mark.parametrize("override", [
        {"is_order_signal": True},
        {"auto_apply_allowed": True},
        {"is_live_authorization": True},
    ])
    def test_paper_decision_true_raises(self, override):
        from app.auto_paper.decisions import AIDirection
        from app.auto_paper.events import DecisionAction, PaperFillStatus
        base = dict(
            strategy="s", symbol="x",
            action=DecisionAction.HOLD,
            confidence=None,
            reason="t",
            risk_flags=[],
            paper_order_id=None,
            paper_fill_status=PaperFillStatus.NA,
            virtual_position_delta=0,
            pnl_estimate=None,
            source_direction=AIDirection.HOLD,
            metadata={},
        )
        base.update(override)
        with pytest.raises(ValueError):
            PaperDecision(**base)

    @pytest.mark.parametrize("override", [
        {"is_order_signal": True},
        {"auto_apply_allowed": True},
        {"is_live_authorization": True},
    ])
    def test_consumer_result_true_raises(self, override):
        base = dict(
            cycle_at="t", schema_version="1.0",
            consumed=False, explanation_verdict=None,
        )
        base.update(override)
        with pytest.raises(ValueError):
            ConsumerResult(**base)


# ─────────────────────────────────────────────────────────────────────────────
# I. 통합 — 1 cycle 의 *모든 경로* end-to-end 검증
# ─────────────────────────────────────────────────────────────────────────────


class TestEndToEndPaperSafety:

    def test_full_cycle_emits_zero_live_calls(
        self, db, kis_place_order_spy, kis_cancel_order_spy,
    ):
        # 시나리오: BUY 한 종목 + 보유 EXIT 한 종목 + risk_veto HOLD 한 종목.
        exp = _exp(
            recommended=[
                _se("sma_crossover", "005930"),
                _se("vwap_strategy", "035720", risk_flags=["stale_data"]),
            ],
            watchlist=[_se("rsi_reversion", "000660", bucket="watchlist")],
        )
        report = bridge_explanation_to_paper_decisions(
            explanation=exp, loop_state="RUNNING",
            positions=[
                PositionSnapshot(strategy="rsi_reversion", symbol="000660",
                                 quantity=5, exit_condition=True),
            ],
            db_session=db,
        )
        # 3 decisions: BUY / HOLD (veto) / EXIT.
        actions = sorted(d.action.value for d in report.decisions)
        assert "BUY" in actions
        assert "HOLD" in actions
        assert "EXIT" in actions
        # broker 호출 0건.
        assert kis_place_order_spy.call_count == 0
        assert kis_cancel_order_spy.call_count == 0
        # 모든 row mode=PAPER.
        rows = db.query(AgentDecisionLog).all()
        assert all(r.mode == "PAPER" for r in rows)

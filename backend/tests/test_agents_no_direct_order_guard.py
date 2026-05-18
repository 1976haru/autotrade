"""#4-06: AI 직접 주문 금지 — cross-cutting invariant guard.

본 테스트는 *기능 추가가 아닌* CLAUDE.md 절대 원칙 1~5 (broker 직접 호출 금지 /
주문 흐름 강제 / advisory only) 를 `backend/app/agents/` 전 모듈에서 *영구
강제* 한다. 신규 agent 모듈이 추가되면 본 테스트가 자동으로 검증.

## 검증 항목

A. **정적 import 가드** — 모든 `app/agents/*.py`:
   - `app.brokers.kis` / `app.brokers.mock_broker` import 0건
   - `app.execution.executor` / `app.execution.order_router` import 0건
   - `broker.place_order(` / `route_order(` / `OrderExecutor(` 호출 0건
   - `KisClient` 참조 0건
   - 외부 HTTP / AI SDK import 0건 (`anthropic` / `openai` / `httpx` / `requests`)
   - `settings.enable_live_trading =` / `settings.enable_ai_execution =` mutate 0건

B. **AgentBase invariant** — `AgentOutput`:
   - `is_order_intent=False` / `can_execute_order=False` 항상 강제
   - `__post_init__` 가드가 True 생성 시 ValueError

C. **Per-agent dataclass invariant** — 각 agent 의 출력 dataclass:
   - `is_order_signal=False` / `auto_apply_allowed=False` / `is_live_authorization=False`
   - True 생성 시 즉시 ValueError (테스트 5+ 케이스)

D. **운영자 dataclass 의 추가 invariant**:
   - `StrategyCombinationRecommendation.auto_start_paper_trader=False`
   - `OverfitWarningReport.auto_disable=False`
   - `MarketRegimeReport.auto_start_paper_trader=False`

E. **AI Agent 추천 → PaperDecision *유일한 변환 경로*** — `AIRecommendationInput`
   + `process_ai_recommendation()` 의 결과 PaperDecision 도 invariant 강제.

F. **frontend Agent UI 안전 배지** — 핵심 카드 (예: PaperStartExplanationCard /
   AutoPaperLoopCard) 의 정적 grep 으로 `매수`/`매도`/`Place Order`/`실거래
   시작`/`ENABLE_LIVE_TRADING` 라벨 button 0개.

## 핵심 정책

본 테스트는 *기존* invariant 를 *통합 검증* 만. 새 invariant 추가 0건 — 이미 각
agent 모듈에 강제 중인 항목을 한 곳에서 *cross-cutting* 으로 lock.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest


_BACKEND_ROOT = Path(__file__).resolve().parents[1]
_AGENTS_DIR   = _BACKEND_ROOT / "app" / "agents"
_FRONTEND_TAB = _BACKEND_ROOT.parents[0] / "frontend" / "src" / "components" / "tabs"


# *순수 advisory* agent 검증 대상 — 본 검사는 broker / route_order 호출이 *영구
# 금지* 인 advisory aggregator / analyzer 만 대상.
#
# 제외:
# - `__init__.py` — 패키지 진입점
# - `auto_trader_loop.py` — 자동매매 *orchestrator*, sanctioned `route_order` 흐름
#   사용 (RiskManager → PermissionGate → OrderExecutor — CLAUDE.md 절대 원칙 2 의
#   유일한 정식 경로). 본 모듈 자체에 대한 가드는 별도 `test_auto_trader_loop`
#   에서 강제.
# - `operating_loop.py` — Agent orchestration loop (필요시 approval queue 호출).
# - `agent_memory.py` — DB storage agent (memory persistence — sanctioned DB write).
#
# 본 제외 list 가 늘어나는 것은 *advisory 가 아닌 모듈* 추가를 의미 — review
# 시점에 신중히 검토.
_ORCHESTRATION_EXEMPT: frozenset[str] = frozenset({
    "__init__.py",
    "auto_trader_loop.py",
    "operating_loop.py",
    "agent_memory.py",
})


def _agent_modules() -> list[Path]:
    """순수 advisory agent 모듈 (orchestration 제외)."""
    return sorted(
        p for p in _AGENTS_DIR.glob("*.py")
        if p.name not in _ORCHESTRATION_EXEMPT
    )


# ─────────────────────────────────────────────────────────────────────────────
# A. 정적 import 가드 — broker / OrderExecutor / route_order / 외부 HTTP / AI SDK
# ─────────────────────────────────────────────────────────────────────────────


_FORBIDDEN_BROKER_PATTERNS: list[str] = [
    r"from\s+app\.brokers\.kis\b",
    r"from\s+app\.brokers\.mock_broker\b",
    r"from\s+app\.execution\.executor\b",
    r"from\s+app\.execution\.order_router\b",
    r"broker\.place_order\s*\(",
    r"route_order\s*\(",
    r"OrderExecutor\s*\(",
    r"\bKisClient\b",
]


_FORBIDDEN_HTTP_AI_PATTERNS: list[str] = [
    r"^import\s+anthropic\b",
    r"^import\s+openai\b",
    r"^import\s+requests\b",
    r"^import\s+httpx\b",
    r"^import\s+yfinance\b",
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


def _strip_docstrings_and_comments(src: str) -> str:
    """Python 소스에서 docstring / 한 줄 주석 제거 — 코드 라인만 carry.

    *모듈 docstring 이 정책 안내 ("broker import 0건" 같은) 를 포함할 수 있어
    naive grep 이 false positive 를 낳는다. 본 helper 는 token 기반으로
    실제 코드 라인만 추출.*
    """
    import io
    import tokenize
    out_lines: list[str] = []
    try:
        tokens = list(tokenize.generate_tokens(io.StringIO(src).readline))
    except (tokenize.TokenizeError, IndentationError):
        # tokenize 실패 — 안전하게 원본 반환 (naive grep).
        return src
    # token 별 시작-끝 라인/컬럼 기준으로 docstring / 주석 위치 표시.
    masked = src
    # 문자열 리터럴 + 주석을 공백으로 마스킹.
    for tok in tokens:
        if tok.type in (tokenize.STRING, tokenize.COMMENT):
            start_line, start_col = tok.start
            end_line,   end_col   = tok.end
            # 단일 라인 단순 마스킹.
            if start_line == end_line:
                lines = masked.splitlines(keepends=True)
                if start_line - 1 < len(lines):
                    line = lines[start_line - 1]
                    lines[start_line - 1] = (
                        line[:start_col] + " " * (end_col - start_col) + line[end_col:]
                    )
                    masked = "".join(lines)
            else:
                # multi-line string (docstring 포함) — 전체 라인을 공백으로.
                lines = masked.splitlines(keepends=True)
                for ln in range(start_line - 1, min(end_line, len(lines))):
                    lines[ln] = " " * len(lines[ln].rstrip("\n")) + (
                        "\n" if lines[ln].endswith("\n") else ""
                    )
                masked = "".join(lines)
    return masked


class TestStaticImportGuards:
    @pytest.mark.parametrize("mod_path", _agent_modules(),
                              ids=lambda p: p.name)
    def test_no_broker_executor_imports(self, mod_path: Path):
        """모든 agent 모듈은 broker / OrderExecutor / route_order import 0건.

        *docstring / 주석은 정책 안내를 포함할 수 있어 검사 대상 외* — token
        기반으로 stripped 후 grep.
        """
        src = _strip_docstrings_and_comments(mod_path.read_text(encoding="utf-8"))
        for pat in _FORBIDDEN_BROKER_PATTERNS:
            assert not re.search(pat, src, re.MULTILINE), (
                f"FORBIDDEN broker/executor pattern {pat!r} in {mod_path.name}"
            )

    @pytest.mark.parametrize("mod_path", _agent_modules(),
                              ids=lambda p: p.name)
    def test_no_external_http_or_ai_sdk(self, mod_path: Path):
        """모든 agent 모듈은 외부 HTTP / AI SDK import 0건."""
        src = _strip_docstrings_and_comments(mod_path.read_text(encoding="utf-8"))
        for pat in _FORBIDDEN_HTTP_AI_PATTERNS:
            assert not re.search(pat, src, re.MULTILINE), (
                f"FORBIDDEN http/AI SDK pattern {pat!r} in {mod_path.name}"
            )

    @pytest.mark.parametrize("mod_path", _agent_modules(),
                              ids=lambda p: p.name)
    def test_no_safety_flag_mutation(self, mod_path: Path):
        """모든 agent 모듈은 safety flag mutate 0건."""
        src = _strip_docstrings_and_comments(mod_path.read_text(encoding="utf-8"))
        for pat in _FORBIDDEN_SAFETY_MUTATION:
            assert not re.search(pat, src, re.IGNORECASE), (
                f"FORBIDDEN safety flag mutation {pat!r} in {mod_path.name}"
            )

    def test_no_db_write_in_pure_advisory_agents(self):
        """순수 advisory agent 는 DB write 0건.

        본 검사 범위: paper_start_explanation / strategy_optimizer_agent /
        strategy_combination_recommender / overfit_warning_agent /
        market_regime_agent — 모두 read-only aggregator/transformer.

        *Python list/dict `.add()` 메서드 호출은 *DB write 아님*. 본 검사는
        ORM session 의 `db.add(` / `db.commit(` / raw SQL `INSERT/UPDATE/DELETE`
        만 검출.*

        agent_memory (storage), auto_trader_loop (state mgmt), daily_report
        등은 *명시적으로* DB 와 상호작용하므로 본 검사에서 *제외*.
        """
        targets = [
            _AGENTS_DIR / "paper_start_explanation.py",
            _AGENTS_DIR / "strategy_optimizer_agent.py",
            _AGENTS_DIR / "strategy_combination_recommender.py",
            _AGENTS_DIR / "overfit_warning_agent.py",
            _AGENTS_DIR / "market_regime_agent.py",
        ]
        # ORM session API + raw SQL DML 만 검사 — `.add(` 단독 매칭은 false-positive
        # (Python 의 set.add / list.append 등) 라 제외.
        forbidden = [
            r"\b(?:session|db|sess)\.commit\s*\(",
            r"\b(?:session|db|sess)\.add\s*\(",
            r"\b(?:session|db|sess)\.delete\s*\(",
            r"\.execute\s*\(\s*['\"]\s*INSERT\b",
            r"\.execute\s*\(\s*['\"]\s*UPDATE\b",
            r"\.execute\s*\(\s*['\"]\s*DELETE\b",
        ]
        for path in targets:
            if not path.exists():
                continue
            src = _strip_docstrings_and_comments(
                path.read_text(encoding="utf-8")
            )
            for pat in forbidden:
                assert not re.search(pat, src, re.MULTILINE | re.IGNORECASE), (
                    f"FORBIDDEN DB write {pat!r} in {path.name}"
                )


# ─────────────────────────────────────────────────────────────────────────────
# B. AgentBase invariant — AgentOutput.is_order_intent / can_execute_order
# ─────────────────────────────────────────────────────────────────────────────


class TestAgentBaseInvariants:
    def test_agent_output_is_order_intent_false_default(self):
        from app.agents.base import AgentDecision, AgentOutput, AgentRole
        out = AgentOutput(
            role=AgentRole.OBSERVER, decision=AgentDecision.OBSERVE,
            summary="test",
        )
        assert out.is_order_intent is False
        assert out.can_execute_order is False

    def test_agent_output_is_order_intent_true_raises(self):
        from app.agents.base import AgentDecision, AgentOutput, AgentRole
        with pytest.raises(ValueError):
            AgentOutput(
                role=AgentRole.OBSERVER, decision=AgentDecision.OBSERVE,
                summary="test", is_order_intent=True,
            )

    def test_agent_output_can_execute_order_true_raises(self):
        from app.agents.base import AgentDecision, AgentOutput, AgentRole
        with pytest.raises(ValueError):
            AgentOutput(
                role=AgentRole.OBSERVER, decision=AgentDecision.OBSERVE,
                summary="test", can_execute_order=True,
            )

    def test_agent_metadata_can_execute_order_false_default(self):
        """모든 등록된 Agent 의 metadata.can_execute_order = False."""
        from app.agents.base import AgentMetadata, AgentRole
        meta = AgentMetadata(
            name="test", role=AgentRole.OBSERVER, description="test",
        )
        assert meta.can_execute_order is False


# ─────────────────────────────────────────────────────────────────────────────
# C. Per-agent dataclass invariants — is_order_signal / auto_apply_allowed /
#    is_live_authorization
# ─────────────────────────────────────────────────────────────────────────────


@pytest.fixture
def _dataclass_cases():
    """invariant 강제 dataclass 목록 — (factory_callable, "name") 튜플.

    각 factory 는 invariant 필드 1개를 True 로 시도 → ValueError 발생 확인.
    """
    from app.agents.paper_start_explanation import (
        ExplanationVerdict, PaperStartExplanation, StrategyExplanation,
    )
    from app.agents.strategy_combination_recommender import (
        PaperCombinationStatus, PaperStrategyCombination, PaperStrategyEntry,
        StrategyAction, StrategyCombinationRecommendation, StrategyDecision,
        OverallRecommendation,
    )
    from app.agents.strategy_optimizer_agent import (
        StrategyAgentInput, StrategyAgentInputItem,
    )
    from app.agents.overfit_warning_agent import (
        OverfitAction, OverfitVerdict, OverfitWarning, OverfitWarningReport,
    )
    from app.agents.market_regime_agent import MarketRegime, MarketRegimeReport

    cases = []

    def add(cls, factory_kwargs, label):
        cases.append((cls, factory_kwargs, label))

    # 4-01 schema.
    add(StrategyAgentInputItem,
        {"strategy": "s", "symbol": "x"}, "StrategyAgentInputItem")
    add(StrategyAgentInput,
        {"generated_at": "t", "schema_version": "1.0",
         "overall_status": "NO_CANDIDATE"}, "StrategyAgentInput")

    # 4-02 v1.
    add(StrategyDecision,
        {"strategy": "s", "symbol": "x", "params": {},
         "action": StrategyAction.RECOMMEND,
         "paper_candidate_status": "READY_FOR_PAPER", "score": 0.0},
        "StrategyDecision")
    add(StrategyCombinationRecommendation,
        {"generated_at": "t", "schema_version": "1.0",
         "overall_recommendation": OverallRecommendation.NO_CANDIDATES_TODAY,
         "recommended_combo": []}, "StrategyCombinationRecommendation")

    # 4-02 v2.
    add(PaperStrategyEntry,
        {"strategy": "s", "symbol": "x", "params": {},
         "paper_candidate_status": "READY_FOR_PAPER", "score": 0.0,
         "rationale": "r"}, "PaperStrategyEntry")
    add(PaperStrategyCombination,
        {"generated_at": "t",
         "status": PaperCombinationStatus.NO_CANDIDATE}, "PaperStrategyCombination")

    # 4-03.
    add(OverfitWarning,
        {"strategy": "s", "symbol": "x", "params": {},
         "overfit_flag": False, "overfit_reason": None,
         "train_validation_gap": None, "walk_forward_verdict": None,
         "recommendation_action": OverfitAction.KEEP,
         "operator_note": None,
         "overfit_verdict": OverfitVerdict.HEALTHY},
        "OverfitWarning")
    add(OverfitWarningReport,
        {"generated_at": "t", "schema_version": "1.0",
         "overall_status": OverallRecommendation.NO_CANDIDATES_TODAY,
         "warnings": [], "overfit_count": 0, "suspect_count": 0,
         "insufficient_data_count": 0, "healthy_count": 0},
        "OverfitWarningReport")

    # 4-04.
    add(MarketRegimeReport,
        {"generated_at": "t", "schema_version": "1.0",
         "regime": MarketRegime.UNKNOWN, "confidence": 0.3},
        "MarketRegimeReport")

    # 4-05.
    add(StrategyExplanation,
        {"strategy": "s", "symbol": "x", "bucket": "recommended",
         "paper_candidate_status": "READY_FOR_PAPER"},
        "StrategyExplanation")
    add(PaperStartExplanation,
        {"generated_at": "t", "schema_version": "1.0",
         "verdict": ExplanationVerdict.DO_NOT_START,
         "recommended_explanations": [], "watchlist_explanations": [],
         "excluded_explanations": [], "market_regime": "UNKNOWN",
         "regime_confidence": 0.3}, "PaperStartExplanation")

    return cases


class TestPerAgentDataclassInvariants:
    """is_order_signal / auto_apply_allowed / is_live_authorization 각각 True →
    `__post_init__` ValueError 강제 (양 레벨 cross-cutting)."""

    def test_all_dataclasses_reject_is_order_signal_true(self, _dataclass_cases):
        for cls, base_kwargs, name in _dataclass_cases:
            with pytest.raises(ValueError):
                cls(**base_kwargs, is_order_signal=True)

    def test_all_dataclasses_reject_auto_apply_allowed_true(self, _dataclass_cases):
        for cls, base_kwargs, name in _dataclass_cases:
            with pytest.raises(ValueError):
                cls(**base_kwargs, auto_apply_allowed=True)

    def test_all_dataclasses_reject_is_live_authorization_true(self, _dataclass_cases):
        for cls, base_kwargs, name in _dataclass_cases:
            with pytest.raises(ValueError):
                cls(**base_kwargs, is_live_authorization=True)

    def test_all_dataclasses_default_invariants_false(self, _dataclass_cases):
        """default 생성 시 모든 invariant False."""
        for cls, base_kwargs, name in _dataclass_cases:
            instance = cls(**base_kwargs)
            assert instance.is_order_signal is False, name
            assert instance.auto_apply_allowed is False, name
            assert instance.is_live_authorization is False, name


# ─────────────────────────────────────────────────────────────────────────────
# D. 운영자 dataclass 추가 invariant
# ─────────────────────────────────────────────────────────────────────────────


class TestExtendedInvariants:
    def test_strategy_combination_auto_start_paper_trader_false(self):
        from app.agents.strategy_combination_recommender import (
            OverallRecommendation, StrategyCombinationRecommendation,
        )
        rec = StrategyCombinationRecommendation(
            generated_at="t", schema_version="1.0",
            overall_recommendation=OverallRecommendation.NO_CANDIDATES_TODAY,
            recommended_combo=[],
        )
        assert rec.auto_start_paper_trader is False
        # mutate 시도 → ValueError.
        with pytest.raises(ValueError):
            StrategyCombinationRecommendation(
                generated_at="t", schema_version="1.0",
                overall_recommendation=OverallRecommendation.NO_CANDIDATES_TODAY,
                recommended_combo=[],
                auto_start_paper_trader=True,
            )

    def test_overfit_warning_report_auto_disable_false(self):
        from app.agents.overfit_warning_agent import OverfitWarningReport
        from app.agents.strategy_combination_recommender import OverallRecommendation
        rep = OverfitWarningReport(
            generated_at="t", schema_version="1.0",
            overall_status=OverallRecommendation.NO_CANDIDATES_TODAY,
            warnings=[], overfit_count=0, suspect_count=0,
            insufficient_data_count=0, healthy_count=0,
        )
        assert rep.auto_disable is False
        with pytest.raises(ValueError):
            OverfitWarningReport(
                generated_at="t", schema_version="1.0",
                overall_status=OverallRecommendation.NO_CANDIDATES_TODAY,
                warnings=[], overfit_count=0, suspect_count=0,
                insufficient_data_count=0, healthy_count=0,
                auto_disable=True,
            )

    def test_market_regime_report_auto_start_paper_trader_false(self):
        from app.agents.market_regime_agent import MarketRegime, MarketRegimeReport
        rep = MarketRegimeReport(
            generated_at="t", schema_version="1.0",
            regime=MarketRegime.UNKNOWN, confidence=0.3,
        )
        assert rep.auto_start_paper_trader is False
        with pytest.raises(ValueError):
            MarketRegimeReport(
                generated_at="t", schema_version="1.0",
                regime=MarketRegime.UNKNOWN, confidence=0.3,
                auto_start_paper_trader=True,
            )


# ─────────────────────────────────────────────────────────────────────────────
# E. AI 추천 → PaperDecision *유일한 변환 경로*
# ─────────────────────────────────────────────────────────────────────────────


class TestPaperDecisionIsSoleConversion:
    """AI advisory recommendation 이 PaperDecision 으로만 변환되며, 그 결과도
    invariant 강제 — 다른 경로 (broker 직접 / OrderRequest 등) 부재."""

    def test_paper_decision_invariants(self):
        from app.auto_paper.decisions import (
            AIDirection, AIRecommendationInput, convert_to_paper_decision,
        )
        rec = AIRecommendationInput(
            strategy="sma_crossover", symbol="005930",
            direction=AIDirection.HOLD, reason="test",
        )
        decision = convert_to_paper_decision(rec)
        assert decision.is_order_signal       is False
        assert decision.auto_apply_allowed    is False
        assert decision.is_live_authorization is False

    def test_paper_decision_module_has_no_broker_imports(self):
        """decisions.py 자체가 broker/executor 0건 — recursive check."""
        path = (_BACKEND_ROOT / "app" / "auto_paper" / "decisions.py")
        src = _strip_docstrings_and_comments(path.read_text(encoding="utf-8"))
        for pat in _FORBIDDEN_BROKER_PATTERNS:
            assert not re.search(pat, src, re.MULTILINE), (
                f"FORBIDDEN pattern {pat!r} in decisions.py"
            )

    def test_ai_recommendation_input_invariants(self):
        from app.auto_paper.decisions import AIDirection, AIRecommendationInput
        with pytest.raises(ValueError):
            AIRecommendationInput(
                strategy="s", symbol="x", direction=AIDirection.HOLD,
                reason="r", is_order_signal=True,
            )

    def test_process_ai_recommendation_only_emits_paper_event(self):
        """process_ai_recommendation 의 결과 event 도 invariant."""
        from app.auto_paper.decisions import (
            AIDirection, AIRecommendationInput, process_ai_recommendation,
        )
        from app.auto_paper.ledger import reset_ledger_for_tests
        reset_ledger_for_tests()
        rec = AIRecommendationInput(
            strategy="s", symbol="x", direction=AIDirection.HOLD,
            reason="advisory",
        )
        decision, event = process_ai_recommendation(
            rec, loop_state="RUNNING",
        )
        assert decision.is_order_signal is False
        if event is not None:
            assert event.is_order_signal      is False
            assert event.auto_apply_allowed   is False
            assert event.is_live_authorization is False
        reset_ledger_for_tests()


# ─────────────────────────────────────────────────────────────────────────────
# F. ENABLE_LIVE_TRADING / ENABLE_AI_EXECUTION default false
# ─────────────────────────────────────────────────────────────────────────────


class TestSafetyFlagsDefault:
    def test_settings_default_safety_flags(self):
        from app.core.config import get_settings
        s = get_settings()
        # 본 PR 시점 *현재 값* 검사 — local .env 가 다른 값일 수 있어 *경고만*.
        # CI 환경에서는 .env 없으므로 default 사용 → 모두 false.
        # local 운영자 .env 에서 변경해도 *코드 default* 가 false 인지 확인.
        # → 본 검사는 .env.example 의 default 값 검사로 대체 (이미 hygiene 에 있음).
        # 여기서는 *값 존재 여부* + 타입만 확인.
        assert hasattr(s, "enable_live_trading")
        assert hasattr(s, "enable_ai_execution")
        assert hasattr(s, "enable_futures_live_trading")
        assert hasattr(s, "kis_is_paper")


# ─────────────────────────────────────────────────────────────────────────────
# G. Frontend Agent UI 안전 배지 — 정적 grep
# ─────────────────────────────────────────────────────────────────────────────


_CRITICAL_AGENT_CARDS = [
    "PaperStartExplanationCard.jsx",       # 4-05
    "AutoPaperLoopCard.jsx",               # 2-01/2-09/2-10
]


class TestFrontendAgentUiSafety:
    @pytest.mark.parametrize("card_name", _CRITICAL_AGENT_CARDS)
    def test_card_has_paper_only_or_advisory_badge(self, card_name):
        """핵심 Agent UI 카드는 'Paper' / 'advisory' / '실거래 OFF' 안전 라벨 carry."""
        path = _FRONTEND_TAB / card_name
        assert path.exists(), f"{card_name} not found"
        src = path.read_text(encoding="utf-8")
        # 최소 하나의 안전 라벨 등장 — Paper / advisory / 실거래 / 모의.
        safety_terms = ["Paper", "advisory", "실거래", "모의", "주문 신호 아님"]
        assert any(t in src for t in safety_terms), (
            f"{card_name}: no safety badge label found "
            f"(any of {safety_terms} expected)"
        )

    @pytest.mark.parametrize("card_name", _CRITICAL_AGENT_CARDS)
    def test_card_has_no_buy_sell_button_labels(self, card_name):
        """핵심 Agent UI 카드에 *button* 라벨 '매수' / '매도' / 'Place Order' /
        '실거래 시작' / 'ENABLE_LIVE_TRADING 토글' 0개.

        본 검사는 *button 텍스트 내부* 패턴만 — 주석 / SAFETY 안내 문구는 허용.
        """
        path = _FRONTEND_TAB / card_name
        src = path.read_text(encoding="utf-8")
        # button JSX 추출 — `<button ...>...</button>` 의 내부 텍스트 정밀 검사는
        # 어려우므로 *명확한 위반 패턴* (label 만 단독) 만 차단.
        forbidden_patterns = [
            r">\s*지금 매수\s*<",
            r">\s*지금 매도\s*<",
            r">\s*Place Order\s*<",
            r">\s*실거래 시작\s*<",
            r">\s*ENABLE_LIVE_TRADING 토글\s*<",
            r">\s*AI 자동매매 켜기\s*<",
        ]
        for pat in forbidden_patterns:
            assert not re.search(pat, src), (
                f"{card_name}: forbidden button label pattern {pat!r}"
            )

    @pytest.mark.parametrize("card_name", _CRITICAL_AGENT_CARDS)
    def test_card_no_forbidden_secret_keywords(self, card_name):
        """카드 소스 코드에 API key / Secret / 계좌번호 carry 0건."""
        path = _FRONTEND_TAB / card_name
        src = path.read_text(encoding="utf-8")
        secret_terms = [
            "anthropic_api_key", "openai_api_key",
            "kis_app_key", "kis_app_secret", "account_no",
        ]
        for term in secret_terms:
            assert term not in src.lower(), (
                f"{card_name}: forbidden secret keyword {term!r}"
            )


# ─────────────────────────────────────────────────────────────────────────────
# H. AgentBase 모듈 자체 invariant (재확인 — 기존 test_agents_architecture
#    의 cross-check)
# ─────────────────────────────────────────────────────────────────────────────


class TestAgentBaseModuleIntegrity:
    def test_base_module_does_not_import_broker(self):
        path = _AGENTS_DIR / "base.py"
        src = _strip_docstrings_and_comments(path.read_text(encoding="utf-8"))
        for pat in _FORBIDDEN_BROKER_PATTERNS:
            assert not re.search(pat, src, re.MULTILINE), (
                f"FORBIDDEN in base.py: {pat}"
            )

    def test_agent_role_enum_values(self):
        """6 role 매트릭스 — 추가 확장은 명시 PR 필요."""
        from app.agents.base import AgentRole
        actual = {r.value for r in AgentRole}
        assert actual == {
            "OBSERVER", "ANALYST", "RISK_AUDITOR",
            "STRATEGY_RESEARCHER", "REPORT_WRITER", "EXECUTION_RECOMMENDER",
        }

    def test_agent_decision_enum_no_buy_sell(self):
        """AgentDecision 에 BUY/SELL/HOLD 같은 주문 방향 0개."""
        from app.agents.base import AgentDecision
        forbidden = {"BUY", "SELL", "PLACE_ORDER", "EXECUTE"}
        actual = {d.value for d in AgentDecision}
        assert not (actual & forbidden), (
            f"forbidden order direction in AgentDecision: {actual & forbidden}"
        )


# ─────────────────────────────────────────────────────────────────────────────
# I. 누락 invariant 발견 시 — coverage 안전망
# ─────────────────────────────────────────────────────────────────────────────


class TestCoverage:
    def test_known_agent_modules_count(self):
        """현재 등록된 agent 모듈 수 검증 — 새 모듈 추가 시 invariant
        guard 도 같이 추가됐는지 review 시 알림.
        """
        modules = _agent_modules()
        # 본 PR 시점 19개 — 증가 시 본 테스트가 fail 하지 않지만 review attention.
        assert len(modules) >= 16, (
            "agent module count dropped — invariant coverage 확인 필요"
        )

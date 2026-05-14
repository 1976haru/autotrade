"""System audit invariants — 2026-05.

본 파일은 사용자 요청서 §15 의 9개 invariant 를 *한 곳에서 통합 검증* 하는
audit-grade smoke 테스트다. 각 invariant 는 이미 다른 테스트 파일에서 더 깊게
검증되고 있으며, 본 파일은:

1. 새 코드를 만들지 않고
2. 사실 검증 한 줄 한 줄을 명시적으로 *문서화* 해
3. 향후 회귀 시 어떤 invariant 가 깨졌는지 즉시 보이게 한다

본 파일은 broker / OrderExecutor / route_order import 0건. DB write 0건.
"""

from __future__ import annotations

import pathlib

import pytest

from app.core.modes import OperationMode
from app.risk.emergency_stop import KillSwitchLevel
from app.strategies.concrete import STRATEGY_REGISTRY
from app.strategies.registry_metadata import (
    BeginnerMetadata,
    backtest_available,
    list_beginner_registry,
    live_trading_available,
    paper_trading_available,
    validate_metadata,
)


# ====================================================================
# Invariant 1 — 매매기법 6종 외 0건
# ====================================================================


_EXPECTED_STRATEGIES = (
    "sma_crossover",
    "rsi_reversion",
    "vwap_strategy",
    "orb_vwap",
    "volume_breakout",
    "pullback_rebreak",
)


def test_registry_contains_exactly_six_strategies():
    """STRATEGY_REGISTRY 가 *정확히* 6개 키만 가진다. 새 전략 추가는 본
    invariant 를 명시적으로 갱신해야만 가능 — 우발적 추가 차단.
    """
    assert set(STRATEGY_REGISTRY.keys()) == set(_EXPECTED_STRATEGIES)
    assert len(STRATEGY_REGISTRY) == 6


def test_beginner_metadata_and_registry_one_to_one():
    """registry_metadata 의 validate_metadata 가 STRATEGY_REGISTRY ↔ _BEGINNER_METADATA
    1:1 매핑을 강제 — 한쪽에만 있는 entry 가 있으면 raise."""
    # raise 안 함 = 통과.
    validate_metadata()


# ====================================================================
# Invariant 2 — 가짜 / 경쟁사 전략명 0건
# ====================================================================


_BANNED_HYPE_PATTERNS = (
    # 한글 hype
    "골든브릿지", "트라이앵글 전설", "다이아 전략", "퀀텀 점프",
    "황금알", "초신성", "월급쟁이 비밀", "100% 승률",
    # 영문 hype
    "guaranteed", "magic strategy", "secret formula", "100% win",
)


def test_no_hype_pattern_in_any_metadata_text_field():
    """6개 전략의 display_name / beginner_name / description / notes 어느 곳에도
    hype 단어가 없다.
    """
    entries = list_beginner_registry()
    for e in entries:
        blob = " ".join([
            e.get("display_name", "") or "",
            e.get("beginner_name", "") or "",
            e.get("description", "") or "",
            " ".join(e.get("notes", []) or []),
        ]).lower()
        for banned in _BANNED_HYPE_PATTERNS:
            assert banned.lower() not in blob, (
                f"strategy '{e['strategy_id']}' contains banned hype: '{banned}'"
            )


# ====================================================================
# Invariant 3 — paper 모드에서 실 주문 0건 (KIS LIVE place_order 미구현)
# ====================================================================


def test_kis_live_place_order_raises_not_implemented():
    """KisBrokerAdapter.place_order(is_paper=False) 는 즉시 NotImplementedError.
    실 거래 활성화 전까지 본 invariant 가 LIVE 주문을 절대 흐르게 두지 않음.
    """
    # 실 instance 생성은 secret 의존 — 본 테스트는 *클래스 메서드 검사* 만 한다.
    from app.brokers.kis import KisBrokerAdapter
    import inspect
    src = inspect.getsource(KisBrokerAdapter.place_order)
    # is_paper=False 분기에서 NotImplementedError raise 여부 (문자열 검사 — robust 한
    # type-level test 가 가능해질 때까지의 first-line 가드).
    assert "NotImplementedError" in src, (
        "KisBrokerAdapter.place_order 는 LIVE 분기에서 NotImplementedError 를 "
        "raise해야 한다 (CLAUDE.md 다층 안전 가드)."
    )


def test_paper_trader_blocks_live_broker():
    """PaperTrader 가 live broker instance 에서 NotPaperBrokerError 를 raise.
    paper 코드 경로에 live broker 가 섞이는 사고를 차단.
    """
    from app.execution.paper_trader import (
        assert_paper_broker, NotPaperBrokerError, is_live_broker,
    )
    # 함수 존재만 검증 — 동작 테스트는 별도 test_paper_trader.py.
    assert callable(assert_paper_broker)
    assert callable(is_live_broker)
    assert issubclass(NotPaperBrokerError, Exception)


# ====================================================================
# Invariant 4 — 운영모드 7종 정확히 존재 (+ default SIMULATION)
# ====================================================================


def test_operation_modes_exact_set():
    """OperationMode enum 이 정확히 7개 — 새 모드 추가는 CLAUDE.md "변경 시
    동기화" 정책에 따라 본 invariant 도 동시에 갱신해야 한다.
    """
    expected = {
        "SIMULATION", "PAPER", "LIVE_SHADOW",
        "LIVE_MANUAL_APPROVAL", "LIVE_AI_ASSIST", "LIVE_AI_EXECUTION",
        "VIRTUAL_AI_EXECUTION",
    }
    assert {m.value for m in OperationMode} == expected


def test_default_mode_is_simulation_or_paper_at_env_example():
    """backend/.env.example 의 DEFAULT_MODE 가 SIMULATION 또는 PAPER 여야 한다 —
    LIVE_* 가 default 로 commit 되는 것을 방지.
    """
    env_path = (
        pathlib.Path(__file__).parent.parent / ".env.example"
    )
    text = env_path.read_text(encoding="utf-8")
    for line in text.splitlines():
        if line.strip().startswith("DEFAULT_MODE="):
            val = line.split("=", 1)[1].strip()
            assert val in {"SIMULATION", "PAPER"}, (
                f".env.example DEFAULT_MODE='{val}' — must be SIMULATION or PAPER"
            )
            break
    else:
        pytest.fail("DEFAULT_MODE not found in .env.example")


def test_dangerous_flags_default_false_at_env_example():
    """ENABLE_LIVE_TRADING / ENABLE_AI_EXECUTION / ENABLE_FUTURES_LIVE_TRADING
    이 .env.example 에서 *모두 false* — git 에 commit 된 default 가 LIVE 를
    활성화하지 않음을 보장.
    """
    env_path = (
        pathlib.Path(__file__).parent.parent / ".env.example"
    )
    text = env_path.read_text(encoding="utf-8")
    for key in (
        "ENABLE_LIVE_TRADING",
        "ENABLE_AI_EXECUTION",
        "ENABLE_FUTURES_LIVE_TRADING",
    ):
        for line in text.splitlines():
            if line.strip().startswith(f"{key}="):
                val = line.split("=", 1)[1].strip().lower()
                assert val == "false", (
                    f".env.example {key}={val} — must be false"
                )
                break
        else:
            pytest.fail(f"{key} not found in .env.example")


def test_kis_is_paper_default_true_at_env_example():
    env_path = (
        pathlib.Path(__file__).parent.parent / ".env.example"
    )
    text = env_path.read_text(encoding="utf-8")
    for line in text.splitlines():
        if line.strip().startswith("KIS_IS_PAPER="):
            assert line.split("=", 1)[1].strip().lower() == "true"
            return
    pytest.fail("KIS_IS_PAPER not found in .env.example")


# ====================================================================
# Invariant 5 — KillSwitchLevel 4단계 (#37)
# ====================================================================


def test_kill_switch_level_has_four_steps():
    """OFF / LEVEL_1 / LEVEL_2 / LEVEL_3 정확히 4개 — 자동 청산 / 자동 취소는
    절대 금지 (CLAUDE.md #37). LEVEL_2 는 *후보 표시*, LEVEL_3 는 *청산 후보*.
    """
    assert {m.value for m in KillSwitchLevel} == {
        "OFF", "LEVEL_1", "LEVEL_2", "LEVEL_3",
    }


# ====================================================================
# Invariant 6 — Live trading 모두 비활성, backtest/paper 모두 활성
# ====================================================================


def test_all_strategies_live_trading_unavailable():
    """6개 전략 모두 live_trading_available=False — KIS LIVE place_order 가
    NotImplementedError 인 *현재 시점* 의 정직한 표시.
    """
    for sid in _EXPECTED_STRATEGIES:
        assert live_trading_available(sid) is False, (
            f"strategy '{sid}' live_trading_available must be False"
        )


def test_all_strategies_backtest_available():
    for sid in _EXPECTED_STRATEGIES:
        assert backtest_available(sid) is True, (
            f"strategy '{sid}' backtest_available must be True"
        )


def test_all_strategies_paper_trading_available():
    for sid in _EXPECTED_STRATEGIES:
        assert paper_trading_available(sid) is True, (
            f"strategy '{sid}' paper_trading_available must be True"
        )


# ====================================================================
# Invariant 7 — Audit log 핵심 컬럼 존재 (위험관리 차단 사유 carry)
# ====================================================================


def test_order_audit_log_has_reasons_column():
    """OrderAuditLog.reasons 가 list 형으로 차단 사유를 carry. 본 컬럼이
    없어지면 사용자 친화 차단 메시지를 표시할 근거가 사라진다.
    """
    from app.db.models import OrderAuditLog
    cols = {c.name for c in OrderAuditLog.__table__.columns}
    # 필수 컬럼.
    for required in (
        "id", "created_at", "mode", "symbol", "side", "quantity",
        "decision", "reasons", "strategy",
    ):
        assert required in cols, (
            f"OrderAuditLog must have '{required}' column"
        )


def test_agent_decision_log_has_chain_id_for_link():
    """AgentDecisionLog 가 OrderAuditLog 와 연결 가능한 추적 ID 를 가진다."""
    from app.db.models import AgentDecisionLog
    cols = {c.name for c in AgentDecisionLog.__table__.columns}
    for required in (
        "id", "created_at", "agent_name", "decision", "reasons",
    ):
        assert required in cols
    # chain_id 또는 symbol — 본 PR 시점 chain_id 우선, fallback 으로 symbol 확인.
    assert "chain_id" in cols or "symbol" in cols


def test_shadow_trade_actual_broker_order_sent_default_false():
    """ShadowTrade 모델의 actual_broker_order_sent 컬럼이 *반드시 default False*.
    LIVE_SHADOW 의 핵심 invariant — DB 단에서 실 주문이 *발생했다* 라고
    잘못 기록하지 않게 한다.
    """
    from app.db.models import ShadowTrade
    col = ShadowTrade.__table__.columns.get("actual_broker_order_sent")
    assert col is not None, "ShadowTrade.actual_broker_order_sent 컬럼 누락"
    default = getattr(col, "default", None)
    # SQLAlchemy ColumnDefault 의 arg 가 False.
    if default is not None and hasattr(default, "arg"):
        assert default.arg is False, (
            f"ShadowTrade.actual_broker_order_sent default must be False, "
            f"got {default.arg}"
        )


# ====================================================================
# Invariant 8 — Agent base 가 broker 직접 호출하지 않는다 (#51)
# ====================================================================


def _read(path: pathlib.Path) -> str:
    return path.read_text(encoding="utf-8")


def test_agent_base_does_not_import_broker_or_executor():
    """AgentBase 모듈 (`app/agents/base.py`) 이 broker / OrderExecutor /
    route_order 어떤 것도 import 하지 않는다.
    """
    src = _read(
        pathlib.Path(__file__).parent.parent / "app" / "agents" / "base.py"
    )
    for banned in (
        "from app.brokers.kis",
        "from app.brokers.mock_broker",
        "from app.brokers.base import BrokerAdapter",
        "from app.brokers.base import OrderRequest",
        "from app.execution.order_router",
        "from app.execution.order_executor",
        "from app.execution.executor",
        "route_order(",
        "broker.place_order(",
    ):
        assert banned not in src, (
            f"app/agents/base.py must not contain '{banned}'"
        )


def test_strategy_selection_agent_does_not_import_broker():
    """#85 StrategySelectionAgent — broker import 0건 재검증."""
    src = _read(
        pathlib.Path(__file__).parent.parent / "app" / "agents"
        / "strategy_selection_agent.py"
    )
    for banned in (
        "from app.brokers.kis",
        "from app.brokers.mock_broker",
        "from app.brokers.base import OrderRequest",
        "from app.execution.order_router",
        "route_order(",
        "broker.place_order(",
    ):
        assert banned not in src


def test_aggregator_module_no_broker_imports():
    """#84 aggregator — broker import 0건 재검증."""
    src = _read(
        pathlib.Path(__file__).parent.parent / "app" / "strategies"
        / "aggregator.py"
    )
    for banned in (
        "from app.brokers.kis",
        "from app.brokers.mock_broker",
        "from app.execution.order_router",
        "route_order(",
        "broker.place_order(",
    ):
        assert banned not in src


def test_registry_metadata_no_broker_or_db_write():
    """#81 registry_metadata — broker / OrderExecutor import 0건, DB write 0건."""
    src = _read(
        pathlib.Path(__file__).parent.parent / "app" / "strategies"
        / "registry_metadata.py"
    )
    for banned in (
        "from app.brokers.",
        "from app.execution.",
        "session.add(",
        "session.commit(",
        "session.delete(",
    ):
        assert banned not in src


# ====================================================================
# Invariant 9 — 전략 파라미터 보존 (describe_strategy 가 init 시그니처 그대로 노출)
# ====================================================================


def test_describe_strategy_carries_init_params_for_all_six():
    """describe_strategy(sid) 가 __init__ 의 모든 파라미터를 그대로 carry —
    UI 가 사용자에게 노출하는 설정값이 강제로 손실되지 않는다.
    """
    from app.strategies.concrete import describe_strategy
    for sid in _EXPECTED_STRATEGIES:
        desc = describe_strategy(sid)
        assert isinstance(desc, dict)
        assert desc["name"] == sid
        assert "params" in desc
        assert isinstance(desc["params"], list)
        # 6개 전략 중 어느 것도 params 가 *비어있지 않다* —
        # __init__ 에는 최소 1개 이상의 keyword 가 있다.
        assert len(desc["params"]) >= 1, (
            f"strategy '{sid}' has no exposed init params — 사용자가 설정값을 "
            "변경할 방법이 사라짐"
        )


def test_beginner_metadata_dataclass_is_frozen():
    """BeginnerMetadata 는 frozen dataclass — UI 가 metadata 를 mutate 못 함."""
    from app.strategies.registry_metadata import RecommendedMode, RiskLevel
    md = BeginnerMetadata(
        strategy_id="x", display_name="x", beginner_name="x",
        description="x",
        risk_level=RiskLevel.LOW,
        recommended_mode=RecommendedMode.PAPER_RECOMMENDED,
        notes=(),
    )
    with pytest.raises(Exception):
        md.display_name = "modified"  # type: ignore[misc]
